import base64
import json
import os
from typing import Any, Dict, Optional

from openai import AsyncOpenAI

from config import get_settings
from utils.logger import get_logger

logger = get_logger(__name__)

# Rubric and context specifically tailored for "Understanding Maxima and Minima in Profit Maximization" (A_C2F6D9)
ROUNDS_CONFIG = {
    1: {
        "title": "Round 1: The Calculus Trial",
        "topic": "First & Second Derivative Tests for Local Extrema",
        "intro_question": (
            "Halt, scholar! You computed the numbers on paper, but numbers alone do not impress me. "
            "Tell me: Why must the first derivative equal zero at maximum profit, and how does the second derivative "
            "prove that we haven't blundered into a minimum profit instead?"
        ),
        "evaluation_criteria": (
            "The student must explain:\n"
            "1. First derivative f'(x) = 0 because the slope of the tangent line is zero (rate of change is flat/critical point).\n"
            "2. Second derivative f''(x) < 0 indicates concave down (the curve is bending downward like an upside-down bowl/peak). "
            "If f''(x) > 0, it would be concave up (a minimum or valley), and if f''(x) = 0, the test is inconclusive.\n"
            "Reward clear conceptual intuition even if phrasing is conversational."
        ),
    },
    2: {
        "title": "Round 2: The Profit Maximization Trial",
        "topic": "Marginal Revenue, Marginal Cost & Profit Function",
        "intro_question": (
            "A formidable defense, but don't celebrate yet! In profit maximization, our profit function is "
            "pi(Q) = Revenue(Q) - Cost(Q). Explain why maximum profit occurs precisely when Marginal Revenue equals "
            "Marginal Cost, and what happens if a company continues producing when Marginal Cost exceeds Marginal Revenue?"
        ),
        "evaluation_criteria": (
            "The student must explain:\n"
            "1. Since profit is Revenue minus Cost, the derivative of profit is MR - MC. Setting this derivative to zero yields MR = MC.\n"
            "2. If Marginal Cost exceeds Marginal Revenue (MC > MR), each additional unit costs more to produce than it brings in revenue, "
            "which diminishes total profit.\n"
            "3. If MR > MC, the firm should keep expanding production because each additional unit adds to total profit."
        ),
    },
}

BOSS_SYSTEM_PROMPT = """You are Professor Algor — an authoritative, dramatic, but fair university examiner in an educational Oral Defense game called InSync.
Judge the student's spoken defense only against the professor-authored evaluation criteria supplied for the current level. Do not require facts that are absent from those criteria.

Strict Evaluation & Damage Rules:
1. Empty, Ignorant, or Evasive Answers:
   - If the student admits ignorance (e.g. "I do not know", "I don't know", "idk", "no idea", "no clue", "i do not know anything", "nothing"), speaks gibberish, avoids the question, or provides factually wrong content:
     - damage_dealt MUST BE 0 (ABSOLUTELY ZERO DAMAGE. Do NOT give even 1 HP).
     - is_sound MUST BE false.
     - boss_dialogue: Respond sternly but constructively in character.
     - feedback: Explain which professor-specified concepts they need to study.

2. Partially Correct Answers (Damage 10-25):
   - The student addresses only part of the required knowledge or reasoning:
     - damage_dealt: 10 to 25.
     - is_sound: false.
     - boss_dialogue: Acknowledge the partial truth, but demand the missing piece.
     - feedback: Guide the student on what specific missing component they need to add.

3. Solid to Masterful Answers (Damage 30-50):
   - The student correctly covers the essential professor-authored criteria with sound reasoning:
     - damage_dealt: 30 to 50.
     - is_sound: true.
     - boss_dialogue: Express dramatic shock or respect at their defense.
     - feedback: Praise their clear conceptual understanding.

You must respond ONLY with a valid JSON object matching this exact schema:
{
  "is_sound": boolean,
  "damage_dealt": integer (between 0 and 50),
  "boss_dialogue": string (2-3 spoken sentences, natural spoken English, no markdown or raw LaTeX symbols),
  "feedback": string (1-2 sentences constructive guidance),
  "next_round": integer or null
}
"""

SAGE_PERSPECTIVES = {
    1: {
        "calculus": (
            "To satisfy the first derivative test, set the first derivative f prime of x to zero to find the critical point "
            "where the tangent slope is zero. Then apply the second derivative test: it must be strictly negative to prove "
            "concave down, ensuring you have found a maximum rather than a minimum."
        ),
        "graph": (
            "Picture a mountain summit or roller coaster peak. At the very highest point, the tangent line is momentarily "
            "completely flat (slope equals zero). Because the track curves downward on both sides, the second derivative is "
            "negative, confirming a true peak rather than a valley."
        ),
        "business": (
            "Think of profit as a curve that rises and then crests. At the absolute peak of profitability, the rate of profit "
            "growth stops momentarily, which is why the derivative equals zero. Curving downward ensures this is the peak of "
            "earnings and not the bottom of a slump."
        ),
    },
    2: {
        "calculus": (
            "Profit is Revenue minus Cost. The derivative of profit is Marginal Revenue minus Marginal Cost. Setting this "
            "derivative to zero directly proves that maximum profit occurs when Marginal Revenue equals Marginal Cost."
        ),
        "graph": (
            "On a graph of revenue and cost, total profit is the vertical distance between them. That gap is widest exactly "
            "where the two curves have the exact same slope, meaning Marginal Revenue equals Marginal Cost."
        ),
        "business": (
            "If making one more phone brings in 50 dollars but costs 40 dollars, make it! But if making one more costs 60 dollars "
            "and only sells for 50 dollars, Marginal Cost exceeds Marginal Revenue. Every additional unit sold actively eats "
            "away at your total profit."
        ),
    },
}

SAGE_COACH_SYSTEM_PROMPT = """You are Sage AI, the student's supportive learning companion and corner coach in InSync.
The student is currently in an Oral Defense against Professor Algor.

Current Challenge: {ROUND_TITLE}
Topic: {ROUND_TOPIC}
Expected Concepts:
{EVALUATION_CRITERIA}

The student called a timeout and asked you this question:
\"{STUDENT_QUESTION}\"

Your Job as their Coach:
1. Provide a warm, uplifting, intuitive 2 to 3 sentence explanation.
2. Use an easy-to-grasp analogy or conceptual spark.
3. Do NOT just give them a full script to parrot back to the Boss. Give them the understanding so they can speak for themselves.
4. Speak in clear, natural conversational English (no markdown, no bullet points, no LaTeX symbols).

Respond with only your spoken coaching advice.
"""


def legacy_oral_defense_config() -> Dict[str, Any]:
    """Return the original calculus battle as a reusable configuration."""
    return {
        "enabled": True,
        "template": "oral_defense",
        "title": "Apex Extrema Oral Defense",
        "boss_name": "Professor Algor",
        "boss_title": "Guardian of Extrema",
        "levels": [
            {
                "level": number,
                "title": config["title"],
                "topic": config["topic"],
                "question": config["intro_question"],
                "knowledge_base": config["evaluation_criteria"],
            }
            for number, config in sorted(ROUNDS_CONFIG.items())
        ],
    }


def resolve_oral_defense_config(game_config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Use an assignment's professor-authored game or the original legacy template."""
    if isinstance(game_config, dict) and game_config.get("enabled"):
        levels = game_config.get("levels")
        if isinstance(levels, list) and levels:
            return game_config
    return legacy_oral_defense_config()


def get_level_config(game_config: Optional[Dict[str, Any]], round_num: int) -> Dict[str, Any]:
    config = resolve_oral_defense_config(game_config)
    levels = config["levels"]
    index = max(0, min(int(round_num or 1) - 1, len(levels) - 1))
    return levels[index]


class OralDefenseService:
    def __init__(self) -> None:
        settings = get_settings()
        self.api_key = settings.OPENAI_API_KEY
        self.model = settings.LLM_MODEL or "gpt-4o-mini"
        if not self.api_key:
            raise ValueError("OPENAI_API_KEY not configured in environment")
        self.voice = "onyx"  # Deep, authoritative, professorial voice

    async def get_intro_audio(
        self,
        round_num: int = 1,
        game_config: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Returns the introductory question and OpenAI audio for the given round."""
        config = resolve_oral_defense_config(game_config)
        level = get_level_config(config, round_num)
        question_text = level["question"]
        audio_base64 = await self.synthesize_speech(question_text)
        return {
            "round": round_num,
            "title": level["title"],
            "topic": level["topic"],
            "boss_dialogue": question_text,
            "audio_base64": audio_base64,
            "total_levels": len(config["levels"]),
        }

    async def evaluate_turn(
        self,
        round_num: int,
        student_transcript: str,
        current_boss_hp: int = 100,
        game_config: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Evaluates student spoken defense and returns evaluation, damage, and synthesized OpenAI voice."""
        config = resolve_oral_defense_config(game_config)
        level = get_level_config(config, round_num)
        total_levels = len(config["levels"])

        user_message_content = (
            f"Current Challenge: {level['title']}\n"
            f"Topic: {level['topic']}\n"
            f"Challenge Question Asked: \"{level['question']}\"\n\n"
            f"Expected Conceptual Criteria:\n{level['knowledge_base']}\n\n"
            f"Student Spoken Defense Transcript:\n\"{student_transcript.strip() or '[Silence / No words spoken]'}\""
        )

        async with AsyncOpenAI(api_key=self.api_key) as client:
            completion = await client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": BOSS_SYSTEM_PROMPT},
                    {"role": "user", "content": user_message_content},
                ],
                response_format={"type": "json_object"},
                temperature=0.2,
            )
            raw_response = completion.choices[0].message.content or "{}"
            try:
                data = json.loads(raw_response)
            except json.JSONDecodeError:
                logger.error(f"Failed to parse boss response JSON: {raw_response}")
                data = {
                    "is_sound": False,
                    "damage_dealt": 0,
                    "boss_dialogue": "I could not decipher your defense, scholar! Speak with precision and mathematical clarity.",
                    "feedback": "Your response could not be parsed. Please speak clearly using calculus terms.",
                    "next_round": round_num,
                }

        # Deterministic checks for admissions of ignorance
        transcript_clean = (student_transcript or "").strip().lower()
        idk_phrases = [
            "i don't know", "i dont know", "i do not know", "idk",
            "no idea", "no clue", "dunno", "nothing", "i have no idea",
            "not sure", "do not know anything", "dont know anything",
            "i have no clue"
        ]
        has_idk = any(phrase in transcript_clean for phrase in idk_phrases)
        is_pure_ignorance = (has_idk and len(transcript_clean.split()) < 14) or len(transcript_clean) < 3

        damage_dealt = int(data.get("damage_dealt", 0))
        damage_dealt = max(0, min(50, damage_dealt))

        if is_pure_ignorance:
            damage_dealt = 0
            data["is_sound"] = False
            data["next_round"] = round_num
        elif not data.get("is_sound", False):
            damage_dealt = min(damage_dealt, 25)
            data["next_round"] = round_num
        else:
            damage_dealt = max(damage_dealt, 30)
            data["next_round"] = round_num + 1 if round_num < total_levels else None

        new_boss_hp = max(0, current_boss_hp - damage_dealt)
        is_defeated = (
            round_num >= total_levels
            and data.get("is_sound", False)
            and damage_dealt >= 30
        )

        if is_defeated:
            new_boss_hp = 0
        elif new_boss_hp <= 0:
            new_boss_hp = 1

        boss_dialogue = data.get("boss_dialogue", "")

        # When a level is passed, introduce the next professor-authored challenge.
        if round_num < total_levels and not is_defeated and data.get("is_sound", False):
            next_q = get_level_config(config, round_num + 1)["question"]
            boss_dialogue = f"{boss_dialogue} {next_q}"
            data["next_round"] = round_num + 1

        # Generate OpenAI speech for the boss response
        audio_base64 = await self.synthesize_speech(boss_dialogue)

        return {
            "round": round_num,
            "is_sound": data.get("is_sound", False),
            "damage_dealt": damage_dealt,
            "boss_dialogue": boss_dialogue,
            "feedback": data.get("feedback", ""),
            "boss_hp": new_boss_hp,
            "is_defeated": is_defeated,
            "next_round": data.get("next_round"),
            "audio_base64": audio_base64,
            "total_levels": total_levels,
        }

    async def synthesize_speech(self, text: str, voice: Optional[str] = None) -> Optional[str]:
        """Synthesizes text into speech using OpenAI TTS and returns base64-encoded mp3."""
        if not text or not text.strip():
            return None
        selected_voice = voice or self.voice
        try:
            async with AsyncOpenAI(api_key=self.api_key) as client:
                response = await client.audio.speech.create(
                    model="tts-1",
                    voice=selected_voice,
                    input=text[:4000],
                )
                audio_bytes = response.content
                return f"data:audio/mp3;base64,{base64.b64encode(audio_bytes).decode('utf-8')}"
        except Exception as exc:
            logger.error(f"OpenAI TTS synthesis error ({selected_voice}): {exc}")
            return None

    async def get_sage_perspective(
        self,
        round_num: int,
        angle: str,
        game_config: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Returns Sage's perspective text and warm audio whisper for the requested lens."""
        if game_config:
            level = get_level_config(game_config, round_num)
            text = (
                f"Focus on {level['topic']}. Build your answer around these ideas: "
                f"{level['knowledge_base'][:700]}"
            )
        else:
            round_perspectives = SAGE_PERSPECTIVES.get(round_num, SAGE_PERSPECTIVES[1])
            text = round_perspectives.get(angle, round_perspectives.get("calculus", ""))
        audio_base64 = await self.synthesize_speech(text, voice="nova")
        return {
            "round": round_num,
            "angle": angle,
            "text": text,
            "audio_base64": audio_base64,
        }

    async def ask_sage_coach(
        self,
        round_num: int,
        student_question: str,
        game_config: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Provides real-time Socratic coaching from Sage AI based on student's question."""
        config = get_level_config(game_config, round_num)
        prompt = SAGE_COACH_SYSTEM_PROMPT.format(
            ROUND_TITLE=config["title"],
            ROUND_TOPIC=config["topic"],
            EVALUATION_CRITERIA=config["knowledge_base"],
            STUDENT_QUESTION=student_question.strip() or "How should I think about this problem?",
        )

        async with AsyncOpenAI(api_key=self.api_key) as client:
            completion = await client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "You are Sage AI, an encouraging and intuitive learning coach. Respond with warm, spoken coaching."},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.7,
                max_tokens=200,
            )
            coaching_text = completion.choices[0].message.content.strip()

        audio_base64 = await self.synthesize_speech(coaching_text, voice="nova")
        return {
            "round": round_num,
            "question": student_question,
            "text": coaching_text,
            "audio_base64": audio_base64,
        }


_oral_defense_service: Optional[OralDefenseService] = None


def get_oral_defense_service() -> OralDefenseService:
    global _oral_defense_service
    if _oral_defense_service is None:
        _oral_defense_service = OralDefenseService()
    return _oral_defense_service
