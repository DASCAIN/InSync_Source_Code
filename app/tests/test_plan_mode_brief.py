import json
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.contrib.sessions.middleware import SessionMiddleware
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory, SimpleTestCase, override_settings

from services.openai_voice_service import OpenAIVoiceService
from services.plan_mode_service import (
    generate_course_from_brief,
    generate_questions_with_ai,
    normalize_oral_defense_config,
    publish_plan_to_database,
)
from voice_tutor.views import api_plan_transcribe, api_plan_upload


@override_settings(SESSION_ENGINE="django.contrib.sessions.backends.signed_cookies")
class PlanModeBriefViewTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def _request(self, data):
        request = self.factory.post("/api/professor/plan/upload", data=data)
        SessionMiddleware(lambda req: None).process_request(request)
        request.session.save()
        return request

    @patch("app.services.inngest_functions.update_job_status")
    @patch("services.plan_mode_service.generate_questions_with_ai")
    @patch("services.plan_mode_service.generate_course_from_brief")
    def test_creates_plan_from_topic_brief(self, generate_course, generate_questions, _update_status):
        generate_course.return_value = {
            "notes": [
                {
                    "t": "1. Core Ideas",
                    "p": 1,
                    "k": "ul",
                    "items": ["A clear concept explanation."],
                }
            ],
            "focus_areas": ["Core Ideas"],
        }
        generate_questions.return_value = [{"id": 1, "q": "A question"}]
        description = "Introduce neural networks, activation functions, and a simple worked example."
        request = self._request({
            "job_id": "brief-test",
            "course_name": "Neural Networks Foundations",
            "topic": "Neural Networks",
            "description": description,
            "subject": "Machine Learning",
            "batch": "Year 2",
        })

        response = api_plan_upload(request)
        data = json.loads(response.content)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(data["success"])
        self.assertEqual(data["source_type"], "brief")
        self.assertEqual(data["course_name"], "Neural Networks Foundations")
        self.assertEqual(data["page_count"], 0)
        session = request.session["plan_session"]
        self.assertEqual(session["source_type"], "brief")
        self.assertEqual(session["course_name"], "Neural Networks Foundations")
        self.assertEqual(session["brief_description"], description)
        self.assertEqual(session["file_path"], "")
        self.assertIn("A clear concept explanation.", session["context_text"])
        self.assertEqual(session["source_pages"][0]["page_number"], 1)
        generate_course.assert_called_once_with("Machine Learning", "Neural Networks", description)
        question_call = generate_questions.call_args.kwargs
        self.assertFalse(question_call["source_has_pages"])
        self.assertEqual(question_call["professor_brief"], description)

    @patch("app.services.inngest_functions.update_job_status")
    def test_rejects_an_underspecified_brief(self, _update_status):
        request = self._request({
            "course_name": "Database Foundations",
            "topic": "Databases",
            "description": "Too short",
        })

        response = api_plan_upload(request)
        data = json.loads(response.content)

        self.assertEqual(response.status_code, 400)
        self.assertIn("at least 20 characters", data["error"])

    @patch("app.services.inngest_functions.update_job_status")
    def test_requires_a_course_name(self, update_status):
        request = self._request({
            "topic": "Databases",
            "description": "Cover relational models, keys, joins, and normalization.",
        })

        response = api_plan_upload(request)
        data = json.loads(response.content)

        self.assertEqual(response.status_code, 400)
        self.assertIn("course name", data["error"])
        update_status.assert_not_called()


class GenerateCourseFromBriefTests(SimpleTestCase):
    @patch("services.plan_mode_service.get_settings")
    @patch("services.plan_mode_service.get_openai_client")
    def test_normalizes_generated_course_sections(self, get_client, get_settings):
        get_settings.return_value = SimpleNamespace(LLM_MODEL="gpt-4o-mini")
        completion = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content=json.dumps({
                            "notes": [
                                {
                                    "t": "1. Foundations [Source: hidden]",
                                    "k": "ul",
                                    "items": ["Explain the basic model clearly."],
                                }
                            ],
                            "focus_areas": ["Foundations"],
                        })
                    )
                )
            ]
        )
        client = Mock()
        client.chat.completions.create.return_value = completion
        get_client.return_value = client

        result = generate_course_from_brief(
            "Computer Science",
            "Operating Systems",
            "Cover processes, memory management, scheduling, and practical examples.",
        )

        self.assertEqual(result["notes"][0]["t"], "1. Foundations")
        self.assertEqual(result["notes"][0]["p"], 1)
        self.assertEqual(result["focus_areas"], ["Foundations"])
        client.chat.completions.create.assert_called_once()


class GenerateQuestionsFromBriefTests(SimpleTestCase):
    @patch("services.plan_mode_service.get_openai_client", return_value=None)
    def test_does_not_substitute_unrelated_sample_questions(self, _get_client):
        with self.assertRaisesRegex(ValueError, "no unrelated sample questions"):
            generate_questions_with_ai(
                subject="Biology",
                topic="Cell Membranes",
                context_text="Explain phospholipids and selective permeability.",
                source_has_pages=False,
                professor_brief="Explain phospholipids and selective permeability.",
            )

    @patch("services.plan_mode_service.get_settings")
    @patch("services.plan_mode_service.get_openai_client")
    def test_sends_the_professor_brief_as_authoritative_scope(self, get_client, get_settings):
        get_settings.return_value = SimpleNamespace(LLM_MODEL="gpt-4o-mini")
        completion = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps({
                "questions": [{
                    "q": "What does selective permeability control?",
                    "o": ["Movement across the membrane", "DNA replication", "Bone growth", "Weather"],
                    "a": 0,
                    "d": "Easy",
                    "p": 1,
                    "f": "Selective permeability",
                    "e": "It controls which substances cross the cell membrane.",
                }]
            })))]
        )
        client = Mock()
        client.chat.completions.create.return_value = completion
        get_client.return_value = client
        brief = "Teach phospholipids and selective permeability, without covering cell division."

        questions = generate_questions_with_ai(
            subject="Biology",
            topic="Cell Membranes",
            context_text="Generated notes about membrane structure and transport.",
            count=1,
            focus_areas=["Selective permeability"],
            source_has_pages=False,
            professor_brief=brief,
        )

        self.assertEqual(questions[0]["q"], "What does selective permeability control?")
        call = client.chat.completions.create.call_args.kwargs
        self.assertIn("authoritative scope", call["messages"][0]["content"])
        self.assertIn(brief, call["messages"][1]["content"])


class PublishPlanLabelTests(SimpleTestCase):
    @patch("services.plan_mode_service.models.Batch.objects.filter")
    @patch("services.plan_mode_service.models.Assignment.objects.create")
    @patch("services.plan_mode_service.models.SummaryNotes.objects.create")
    @patch("services.plan_mode_service.models.TopicMaster.objects.filter")
    @patch("services.plan_mode_service.models.SubjectMaster.objects.filter")
    def test_uses_course_name_for_student_facing_labels(
        self,
        subject_filter,
        topic_filter,
        summary_create,
        assignment_create,
        batch_filter,
    ):
        subject_filter.return_value.first.return_value = Mock()
        topic_filter.return_value.first.return_value = Mock()
        assignment_create.return_value = SimpleNamespace(
            code="A_TEST",
            title="Data Science Bootcamp: Comprehensive Assignment",
        )
        batch_filter.return_value.first.return_value = None

        publish_plan_to_database(
            subject_name="Machine Learning",
            batch_name_or_code="Year 2",
            topic_name="K-Means Clustering",
            course_name="Data Science Bootcamp",
            notes_list=[],
            questions_list=[],
        )

        self.assertEqual(
            summary_create.call_args.kwargs["title"],
            "Data Science Bootcamp — Study Notes",
        )
        self.assertIn(
            "# Data Science Bootcamp — Comprehensive Study Notes",
            summary_create.call_args.kwargs["content"],
        )
        self.assertEqual(
            assignment_create.call_args.kwargs["title"],
            "Data Science Bootcamp: Comprehensive Assignment",
        )

    @patch("services.plan_mode_service.models.Batch.objects.filter")
    @patch("services.plan_mode_service.models.Assignment.objects.create")
    @patch("services.plan_mode_service.models.SummaryNotes.objects.create")
    @patch("services.plan_mode_service.models.TopicMaster.objects.filter")
    @patch("services.plan_mode_service.models.SubjectMaster.objects.filter")
    def test_publishes_professor_authored_oral_defense_config(
        self,
        subject_filter,
        topic_filter,
        _summary_create,
        assignment_create,
        batch_filter,
    ):
        subject_filter.return_value.first.return_value = Mock()
        topic_filter.return_value.first.return_value = Mock()
        assignment_create.return_value = SimpleNamespace(code="A_GAME", title="Physics")
        batch_filter.return_value.first.return_value = None
        game_config = {
            "enabled": True,
            "title": "Forces Oral Defense",
            "levels": [{
                "title": "Level 1: Newton's Second Law",
                "topic": "Force and acceleration",
                "question": "Explain how force, mass, and acceleration are related.",
                "knowledge_base": "Students must explain F = ma and how acceleration changes with force and mass.",
            }],
        }

        result = publish_plan_to_database(
            subject_name="Physics",
            batch_name_or_code="Year 2",
            topic_name="Forces",
            course_name="Mechanics",
            notes_list=[],
            questions_list=[],
            game_config=game_config,
        )

        created = assignment_create.call_args.kwargs
        self.assertTrue(created["oral_defense_enabled"])
        self.assertEqual(created["oral_defense_config"]["levels"][0]["level"], 1)
        self.assertEqual(result["game_levels"], 1)


class OralDefenseConfigTests(SimpleTestCase):
    def test_repairs_latex_control_characters_in_game_content(self):
        config = normalize_oral_defense_config({
            "enabled": True,
            "levels": [{
                "title": "Level 1",
                "topic": "K-means",
                "question": "Explain $d(x, c)$ and defend the update rule.",
                "knowledge_base": (
                    "Use $d(x,c)=\\text{sqrt}(x-c)^2$ and "
                    "$c_j=\\frac{1}{|C_j|}\times\\text{sum}(x_i)$ accurately."
                ),
            }],
        }, "K-means")

        knowledge = config["levels"][0]["knowledge_base"]
        self.assertIn(r"\times", knowledge)
        self.assertNotIn("\t", knowledge)

    def test_rejects_a_level_without_a_knowledge_base(self):
        with self.assertRaisesRegex(ValueError, "knowledge base"):
            normalize_oral_defense_config({
                "enabled": True,
                "levels": [{
                    "title": "Level 1",
                    "topic": "Vectors",
                    "question": "Explain vector addition.",
                    "knowledge_base": "Too short",
                }],
            }, "Vectors")

    def test_allows_plan_without_optional_game(self):
        self.assertIsNone(normalize_oral_defense_config(None, "Vectors"))


@override_settings(SESSION_ENGINE="django.contrib.sessions.backends.signed_cookies")
class PlanModeTranscriptionViewTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()

    @patch("voice_tutor.views.get_openai_voice_service")
    def test_transcribes_a_professor_recording(self, get_service):
        service = Mock()
        service.transcribe_audio.return_value = "Cover gradient descent with a worked example."
        get_service.return_value = service
        audio = SimpleUploadedFile("topic.webm", b"recorded-audio" * 100, content_type="audio/webm")
        request = self.factory.post("/api/professor/plan/transcribe", data={"audio": audio})
        SessionMiddleware(lambda req: None).process_request(request)
        request.session["user"] = {"id": "professor@example.com", "role": "professor"}

        response = api_plan_transcribe(request)
        data = json.loads(response.content)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(data["text"], "Cover gradient descent with a worked example.")
        service.transcribe_audio.assert_called_once()

    def test_rejects_an_unsupported_recording_format(self):
        audio = SimpleUploadedFile("topic.bin", b"not-audio", content_type="application/octet-stream")
        request = self.factory.post("/api/professor/plan/transcribe", data={"audio": audio})
        SessionMiddleware(lambda req: None).process_request(request)
        request.session["user"] = {"id": "professor@example.com", "role": "professor"}

        response = api_plan_transcribe(request)

        self.assertEqual(response.status_code, 400)


class OpenAIVoiceTranscriptionTests(SimpleTestCase):
    @patch("services.openai_voice_service.OpenAI")
    def test_uses_the_configured_openai_transcription_model(self, openai):
        client = Mock()
        client.audio.transcriptions.create.return_value = SimpleNamespace(text="Transcribed brief")
        openai.return_value = client
        service = OpenAIVoiceService.__new__(OpenAIVoiceService)
        service.api_key = "test-key"
        service.transcription_model = "gpt-4o-transcribe"
        service.transcription_language = "en"
        service.transcription_prompt = ""

        text = service.transcribe_audio(BytesIO(b"audio"), "topic.webm", "audio/webm")

        self.assertEqual(text, "Transcribed brief")
        client.audio.transcriptions.create.assert_called_once()
