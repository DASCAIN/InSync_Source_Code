import json
from unittest.mock import patch

from django.contrib.sessions.middleware import SessionMiddleware
from django.test import RequestFactory, SimpleTestCase, override_settings

from voice_tutor.views import api_plan_note_refine


@override_settings(SESSION_ENGINE="django.contrib.sessions.backends.signed_cookies")
class PlanModeRefinementViewTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def _request(self, payload):
        request = self.factory.post(
            "/api/professor/plan/notes/refine",
            data=json.dumps(payload),
            content_type="application/json",
        )
        SessionMiddleware(lambda req: None).process_request(request)
        request.session["user"] = {
            "id": "professor@example.com",
            "role": "professor",
        }
        request.session["plan_session"] = {
            "subject": "Machine Learning",
            "topic": "K-Means",
            "source_pages": [{"page_number": 1, "text": "Source material"}],
            "notes": [
                {"t": "Theory", "p": 1, "k": "ul", "items": ["Original theory"]},
                {"t": "Algorithm", "p": 2, "k": "ol", "items": ["Original step"]},
            ],
        }
        return request

    @patch("services.plan_mode_service.refine_note_section_with_ai")
    def test_refines_only_the_selected_section_and_saves_conversation(self, refine):
        refine.return_value = {
            "message": "I added a worked example.",
            "section": {
                "t": "Algorithm",
                "p": 2,
                "k": "ol",
                "items": ["Revised step with an example"],
            },
        }
        request = self._request({
            "section_index": 1,
            "instruction": "Add a worked example",
            "section": {"t": "Algorithm", "p": 2, "k": "ol", "items": ["Edited step"]},
        })

        response = api_plan_note_refine(request)
        data = json.loads(response.content)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(data["success"])
        plan_session = request.session["plan_session"]
        self.assertEqual(plan_session["notes"][0]["items"], ["Original theory"])
        self.assertEqual(plan_session["notes"][1]["items"], ["Revised step with an example"])
        self.assertEqual(
            [message["role"] for message in plan_session["note_chats"]["1"]],
            ["user", "assistant"],
        )

    def test_rejects_empty_instruction(self):
        request = self._request({"section_index": 0, "instruction": "  "})

        response = api_plan_note_refine(request)

        self.assertEqual(response.status_code, 400)
