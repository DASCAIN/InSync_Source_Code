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

BOSS_SYSTEM_PROMPT = """You are Professor Algor, Guardian of Extrema — an authoritative, dramatic, but fair calculus professor and boss examiner in an educational game called InSync.

You are evaluating a university student in an 'Oral Defense' on 'Understanding Maxima and Minima in Profit Maximization'.

Current Challenge: {ROUND_TITLE}
Topic: {ROUND_TOPIC}

Expected Conceptual Criteria:
{EVALUATION_CRITERIA}

Student's Spoken Defense Transcript:
\"{STUDENT_TRANSCRIPT}\"

Your Job:
1. Evaluate the student's verbal defense against the criteria.
2. Determine if the explanation is conceptually sound (solid/good understanding) or flawed/insufficient.
3. Determine damage dealt to the Boss (out of 50 HP max for this round):
   - Solid explanation: 40 to 50 damage.
   - Partial/incomplete explanation: 20 to 35 damage.
   - Incorrect/vague/evasive: 0 to 15 damage.
4. Compose your Boss spoken dialogue (2 to 4 punchy sentences):
   - Stay in character as Professor Algor: dramatic, scholarly, formidable, yet intellectually generous.
   - Speak directly to the student in natural spoken English (no markdown, no bullets, no math symbols like 'π' or 'f\'(x)' spelled as raw LaTeX; say 'pi' or 'f prime of x' or 'first derivative' so TTS pronounces it cleanly).
   - If they did well, react with dramatic respect (e.g., 'Argh! Your grasp of concavity pierces my defenses!').
   - If flawed, point out what they missed (e.g., 'Hah! You mentioned zero slope, but ignored the concavity test entirely!').
   - If this was Round 1 and they dealt >= 35 damage, transition to the Round 2 question.
   - If this was Round 2 and total Boss HP reaches 0, give an epic defeat concession speech acknowledging them as the true Extrema Master.

You must respond ONLY with a valid JSON object in this exact schema:
{{
  "is_sound": true/false,
  "damage_dealt": <integer between 0 and 50>,
  "boss_dialogue": "<spoken response text for text-to-speech>",
  "feedback": "<concise 1-2 sentence constructive pedagogical feedback for the UI>",
  "next_round": <1 or 2 or null>
}}
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
The student is currently in an intense Oral Defense against Professor Algor on Maxima and Minima in Profit Maximization.

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


class OralDefenseService:
    def __init__(self) -> None:
        settings = get_settings()
        self.api_key = settings.OPENAI_API_KEY
        self.model = settings.LLM_MODEL or "gpt-4o-mini"
        if not self.api_key:
            raise ValueError("OPENAI_API_KEY not configured in environment")
        self.voice = "onyx"  # Deep, authoritative, professorial voice

    async def get_intro_audio(self, round_num: int = 1) -> Dict[str, Any]:
        """Returns the introductory question and OpenAI audio for the given round."""
        config = ROUNDS_CONFIG.get(round_num, ROUNDS_CONFIG[1])
        question_text = config["intro_question"]
        audio_base64 = await self.synthesize_speech(question_text)
        return {
            "round": round_num,
            "title": config["title"],
            "topic": config["topic"],
            "boss_dialogue": question_text,
            "audio_base64": audio_base64,
        }

    async def evaluate_turn(
        self,
        round_num: int,
        student_transcript: str,
        current_boss_hp: int = 100,
    ) -> Dict[str, Any]:
        """Evaluates student spoken defense and returns evaluation, damage, and synthesized OpenAI voice."""
        config = ROUNDS_CONFIG.get(round_num, ROUNDS_CONFIG[1])

        prompt = BOSS_SYSTEM_PROMPT.format(
            ROUND_TITLE=config["title"],
            ROUND_TOPIC=config["topic"],
            EVALUATION_CRITERIA=config["evaluation_criteria"],
            STUDENT_TRANSCRIPT=student_transcript.strip() or "[No words spoken / silence]",
        )

        async with AsyncOpenAI(api_key=self.api_key) as client:
            completion = await client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "You evaluate calculus students in an educational boss battle. Respond only in JSON."},
                    {"role": "user", "content": prompt},
                ],
                response_format={"type": "json_object"},
                temperature=0.7,
            )
            raw_response = completion.choices[0].message.content or "{}"
            try:
                data = json.loads(raw_response)
            except json.JSONDecodeError:
                logger.error(f"Failed to parse boss response JSON: {raw_response}")
                data = {
                    "is_sound": True,
                    "damage_dealt": 45,
                    "boss_dialogue": "Impressive reasoning, scholar! You have defended your calculations with honor.",
                    "feedback": "Great explanation of critical points and derivatives.",
                    "next_round": 2 if round_num == 1 else None,
                }

        damage_dealt = int(data.get("damage_dealt", 40))
        new_boss_hp = max(0, current_boss_hp - damage_dealt)
        is_defeated = (new_boss_hp <= 0) or (round_num == 2 and data.get("is_sound", False))

        if is_defeated:
            new_boss_hp = 0

        boss_dialogue = data.get("boss_dialogue", "")

        # If Round 1 is passed and next round is available, append the Round 2 question seamlessly to the voice
        if round_num == 1 and not is_defeated and data.get("is_sound", False):
            next_q = ROUNDS_CONFIG[2]["intro_question"]
            boss_dialogue = f"{boss_dialogue} {next_q}"
            data["next_round"] = 2

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

    async def get_sage_perspective(self, round_num: int, angle: str) -> Dict[str, Any]:
        """Returns Sage's perspective text and warm audio whisper for the requested lens."""
        round_perspectives = SAGE_PERSPECTIVES.get(round_num, SAGE_PERSPECTIVES[1])
        text = round_perspectives.get(angle, round_perspectives.get("calculus", ""))
        audio_base64 = await self.synthesize_speech(text, voice="nova")
        return {
            "round": round_num,
            "angle": angle,
            "text": text,
            "audio_base64": audio_base64,
        }

    async def ask_sage_coach(self, round_num: int, student_question: str) -> Dict[str, Any]:
        """Provides real-time Socratic coaching from Sage AI based on student's question."""
        config = ROUNDS_CONFIG.get(round_num, ROUNDS_CONFIG[1])
        prompt = SAGE_COACH_SYSTEM_PROMPT.format(
            ROUND_TITLE=config["title"],
            ROUND_TOPIC=config["topic"],
            EVALUATION_CRITERIA=config["evaluation_criteria"],
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
