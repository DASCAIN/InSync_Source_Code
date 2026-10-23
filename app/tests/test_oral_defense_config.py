import json
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock, patch

from django.template.loader import render_to_string
from django.test import SimpleTestCase

from services.oral_defense_service import OralDefenseService, get_level_config


GAME_CONFIG = {
    "enabled": True,
    "title": "Physics Oral Defense",
    "levels": [
        {
            "level": 1,
            "title": "Level 1: Forces",
            "topic": "Newton's laws",
            "question": "Explain how net force changes motion.",
            "knowledge_base": "A net force causes acceleration according to F = ma.",
        },
        {
            "level": 2,
            "title": "Level 2: Energy",
            "topic": "Conservation of energy",
            "question": "Defend the conservation of mechanical energy.",
            "knowledge_base": "In an isolated system, kinetic and potential energy transform while total energy remains constant.",
        },
    ],
}


class OralDefenseRuntimeTests(IsolatedAsyncioTestCase):
    def _service(self):
        service = OralDefenseService.__new__(OralDefenseService)
        service.api_key = "test-key"
        service.model = "test-model"
        service.voice = "onyx"
        service.synthesize_speech = AsyncMock(return_value=None)
        return service

    def _openai_context(self, payload):
        client = MagicMock()
        client.chat.completions.create = AsyncMock(return_value=SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))]
        ))
        context = MagicMock()
        context.__aenter__ = AsyncMock(return_value=client)
        context.__aexit__ = AsyncMock(return_value=None)
        return context

    def test_resolves_the_requested_professor_level(self):
        self.assertEqual(get_level_config(GAME_CONFIG, 2)["topic"], "Conservation of energy")

    async def test_passed_level_advances_to_next_professor_question(self):
        service = self._service()
        response = {
            "is_sound": True,
            "damage_dealt": 35,
            "boss_dialogue": "A sound defense.",
            "feedback": "You covered the required relationship.",
            "next_round": None,
        }
        with patch(
            "services.oral_defense_service.AsyncOpenAI",
            return_value=self._openai_context(response),
        ):
            result = await service.evaluate_turn(
                round_num=1,
                student_transcript="A net force causes acceleration, described by force equals mass times acceleration.",
                current_boss_hp=100,
                game_config=GAME_CONFIG,
            )

        self.assertEqual(result["next_round"], 2)
        self.assertFalse(result["is_defeated"])
        self.assertIn(GAME_CONFIG["levels"][1]["question"], result["boss_dialogue"])

    async def test_passing_final_level_completes_the_game(self):
        service = self._service()
        response = {
            "is_sound": True,
            "damage_dealt": 40,
            "boss_dialogue": "You have mastered the final challenge.",
            "feedback": "Complete and accurate.",
            "next_round": None,
        }
        with patch(
            "services.oral_defense_service.AsyncOpenAI",
            return_value=self._openai_context(response),
        ):
            result = await service.evaluate_turn(
                round_num=2,
                student_transcript="Energy changes form, but the total remains constant in an isolated system.",
                current_boss_hp=65,
                game_config=GAME_CONFIG,
            )

        self.assertTrue(result["is_defeated"])
        self.assertEqual(result["boss_hp"], 0)


class ProfessorGamePlanPreviewTests(SimpleTestCase):
    def test_preview_shows_the_authored_plan_instead_of_game_iframe(self):
        html = render_to_string("professor/topic_preview.html", {
            "topic": SimpleNamespace(id=1, name="Forces"),
            "subject": SimpleNamespace(name="Physics", code="PHY"),
            "note": None,
            "assignment": SimpleNamespace(code="A_GAME"),
            "questions_json": "[]",
            "question_count": 0,
            "batch_name": "Year 2",
            "game_enabled": True,
            "game_title": GAME_CONFIG["title"],
            "game_levels": GAME_CONFIG["levels"],
        })

        self.assertIn("The complete Oral Defense plan", html)
        self.assertIn("Explain how net force changes motion.", html)
        self.assertIn("Professor evaluation criteria", html)
        self.assertNotIn("game-preview-frame", html)
        self.assertNotIn("Sage AI Voice Tutor Grounding", html)
