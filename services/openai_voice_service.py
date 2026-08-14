import json
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional

import aiohttp

from services.mem0_service import get_mem0_service
from services.tutor_agent_service import SYSTEM_PROMPT
from utils.logger import get_logger

logger = get_logger(__name__)

ARTIFACTS_DIR = Path(__file__).resolve().parents[1] / "storage" / "openai_voice"
TRANSCRIPTS_DIR = ARTIFACTS_DIR / "transcripts"
RECORDINGS_DIR = ARTIFACTS_DIR / "recordings"

VOICE_STYLE_PROMPT = """Voice delivery rules:
 - At the very beginning of the session, warmly suggest that the student grabs a pen and paper to trace their thoughts and solve problems.
 - Sound deeply calm, uplifting, and motivating, like a patient mentor who believes the student can improve.
 - Use a warm, reassuring voice with a steady relaxed pace; never sound rushed, stern, flat, or robotic.
 - Add gentle energy when encouraging progress, but keep the overall delivery peaceful and grounded.
 - Use natural pauses after important ideas so the student has time to think.
 - When the student struggles, reassure them first, normalize the difficulty, then guide one clear next step.
 - Keep encouragement specific and earned, using phrases like "good attempt", "you are close", or "let's build from that".
 - Prefer clear, short spoken sentences that are easy to follow aloud.
"""


class OpenAIVoiceService:
    def __init__(self) -> None:
        self.api_key = os.getenv("OPENAI_API_KEY")
        if not self.api_key:
            raise ValueError("OPENAI_API_KEY not found in environment")
        self.model = os.getenv("OPENAI_REALTIME_MODEL", "gpt-realtime")
        self.voice = os.getenv("OPENAI_REALTIME_VOICE", "marin")
        self.transcription_model = os.getenv(
            "OPENAI_REALTIME_TRANSCRIPTION_MODEL",
            "gpt-4o-transcribe",
        )
        self.transcription_language = os.getenv("OPENAI_REALTIME_TRANSCRIPTION_LANGUAGE", "en")
        self.transcription_prompt = os.getenv("OPENAI_REALTIME_TRANSCRIPTION_PROMPT", "")
        self.input_noise_reduction = os.getenv(
            "OPENAI_REALTIME_INPUT_NOISE_REDUCTION",
            "near_field",
        )
        self.mem0_service = get_mem0_service()

    async def create_realtime_session(
        self,
        user_id: str,
        topic: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not user_id.strip():
            raise ValueError("User id is empty")

        query = "voice tutor learning profile"
        if topic and topic.strip():
            query = f"{query} {topic.strip()}"

        memories = "No memories found"
        try:
            memories = await self.mem0_service.retrieve_memories(query, user_id)
        except Exception as exc:
            logger.warning(f"Could not retrieve Mem0 memories for OpenAI voice: {exc}")

        pdf_context = "No specific context found in uploaded materials."
        try:
            from app.services.vector_store import retrieve_global_documents
            import asyncio
            docs = await asyncio.to_thread(retrieve_global_documents, query)
            if docs:
                pdf_context = "\n\n".join([doc.page_content for doc in docs])
        except Exception as exc:
            logger.warning(f"Could not retrieve PDF context for OpenAI voice: {exc}")

        session_id = f"openai-voice-{uuid.uuid4()}"
        payload = {
            "session": {
                "type": "realtime",
                "model": self.model,
                "instructions": (
                    f"{SYSTEM_PROMPT.format(MEMORIES=memories, PDF_CONTEXT=pdf_context)}\n\n"
                    f"{VOICE_STYLE_PROMPT}"
                ),
                "audio": {
                    "input": self._build_audio_input_config(),
                    "output": {
                        "voice": self.voice,
                    },
                },
            }
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://api.openai.com/v1/realtime/client_secrets",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            ) as response:
                data = await response.json(content_type=None)
                if response.status >= 400:
                    logger.error(f"OpenAI realtime session failed: {response.status} {data}")
                    detail = data.get("error", {}).get("message") if isinstance(data, dict) else None
                    raise RuntimeError(detail or "Failed to create OpenAI realtime session")

        client_secret = self._extract_client_secret(data)
        if not client_secret:
            raise RuntimeError("OpenAI realtime session did not return a client secret")

        return {
            "session_id": session_id,
            "client_secret": client_secret,
            "model": self.model,
            "voice": self.voice,
            "memories": memories,
        }

    async def store_learning_memory(
        self,
        user_id: str,
        session_id: str,
        transcript: List[Dict[str, Any]],
        metrics: Dict[str, Any],
        latest_user_message: Optional[str] = None,
        latest_assistant_message: Optional[str] = None,
        is_final: bool = False,
    ) -> None:
        if not user_id.strip():
            raise ValueError("User id is empty")
        if not session_id.strip():
            raise ValueError("Session id is empty")

        if is_final:
            lines = []
            for item in transcript[-40:]:
                role = item.get("role", "assistant")
                text = item.get("text", "")
                if isinstance(text, str) and text.strip():
                    label = "Student" if role == "user" else "Tutor"
                    lines.append(f"{label}: {text.strip()}")
            message = (
                f"OpenAI voice tutor session summary ({session_id}).\n"
                f"Learning metrics: {self._format_metrics(metrics)}\n"
                f"Transcript:\n{chr(10).join(lines)}"
            )
            try:
                self._store_transcript_artifact(
                    user_id=user_id,
                    session_id=session_id,
                    transcript=transcript,
                    metrics=metrics,
                )
            except Exception as exc:
                logger.warning(f"Could not store OpenAI voice transcript artifact: {exc}")
        else:
            if (
                not latest_user_message
                or not latest_user_message.strip()
                or not latest_assistant_message
                or not latest_assistant_message.strip()
            ):
                return
            cleaned_answer = latest_assistant_message.replace("**", "").strip()
            message = f"Q: {latest_user_message.strip()}\nA: {cleaned_answer}"

        await self.mem0_service.add_memory(message[:6000], user_id)

    async def store_recording(
        self,
        user_id: str,
        session_id: str,
        chunks: AsyncIterator[bytes],
        content_type: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not user_id.strip():
            raise ValueError("User id is empty")
        if not session_id.strip():
            raise ValueError("Session id is empty")

        RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
        safe_session_id = self._safe_file_part(session_id)
        extension = self._recording_extension(content_type)
        filename = f"{safe_session_id}-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}{extension}"
        path = RECORDINGS_DIR / filename

        size = 0
        with path.open("wb") as output:
            async for chunk in chunks:
                if not chunk:
                    continue
                size += len(chunk)
                output.write(chunk)

        if size <= 0:
            path.unlink(missing_ok=True)
            raise ValueError("Recording upload was empty")

        metadata = {
            "session_id": session_id,
            "user_id": user_id,
            "filename": filename,
            "content_type": content_type,
            "size_bytes": size,
            "stored_at": datetime.utcnow().isoformat(),
            "path": str(path),
        }
        metadata_path = path.with_suffix(f"{path.suffix}.json")
        metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
        return metadata

    def _extract_client_secret(self, data: Any) -> Optional[str]:
        if not isinstance(data, dict):
            return None
        direct = data.get("value") or data.get("client_secret")
        if isinstance(direct, str):
            return direct
        client_secret = data.get("client_secret")
        if isinstance(client_secret, dict) and isinstance(client_secret.get("value"), str):
            return client_secret["value"]
        return None

    def _build_audio_input_config(self) -> Dict[str, Any]:
        config: Dict[str, Any] = {
            "transcription": self._build_transcription_config(),
            "turn_detection": {
                "type": "server_vad",
                "silence_duration_ms": 700,
            },
        }
        noise_reduction = self._build_noise_reduction_config()
        if noise_reduction:
            config["noise_reduction"] = noise_reduction
        return config

    def _build_transcription_config(self) -> Dict[str, str]:
        config = {"model": self.transcription_model}
        language = self.transcription_language.strip()
        if language:
            config["language"] = language
        prompt = self.transcription_prompt.strip()
        if prompt and self.transcription_model != "gpt-realtime-whisper":
            config["prompt"] = prompt
        return config

    def _build_noise_reduction_config(self) -> Optional[Dict[str, str]]:
        value = self.input_noise_reduction.strip()
        if not value or value.lower() == "none":
            return None
        if value not in {"near_field", "far_field"}:
            logger.warning(
                "Invalid OPENAI_REALTIME_INPUT_NOISE_REDUCTION=%s; using near_field",
                value,
            )
            value = "near_field"
        return {"type": value}

    def _store_transcript_artifact(
        self,
        user_id: str,
        session_id: str,
        transcript: List[Dict[str, Any]],
        metrics: Dict[str, Any],
    ) -> None:
        TRANSCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
        safe_session_id = self._safe_file_part(session_id)
        path = TRANSCRIPTS_DIR / f"{safe_session_id}.json"
        payload = {
            "session_id": session_id,
            "user_id": user_id,
            "stored_at": datetime.utcnow().isoformat(),
            "metrics": metrics,
            "transcript": transcript,
        }
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def _safe_file_part(self, value: str) -> str:
        safe = "".join(char if char.isalnum() or char in {"-", "_"} else "-" for char in value)
        return safe.strip("-") or "openai-voice-session"

    def _recording_extension(self, content_type: Optional[str]) -> str:
        if content_type == "audio/mp4":
            return ".m4a"
        if content_type == "audio/ogg":
            return ".ogg"
        return ".webm"

    def _format_metrics(self, metrics: Dict[str, Any]) -> str:
        return (
            f"progress={metrics.get('progress', 0)}%, "
            f"understanding={metrics.get('understandingScore', 0)}%, "
            f"confidence={metrics.get('confidenceLevel', 0)}%, "
            f"stage={metrics.get('currentStage', 'Assessment')}, "
            f"turns={metrics.get('turns', 0)}"
        )


_openai_voice_service: Optional[OpenAIVoiceService] = None


def get_openai_voice_service() -> OpenAIVoiceService:
    global _openai_voice_service
    if _openai_voice_service is None:
        _openai_voice_service = OpenAIVoiceService()
    return _openai_voice_service
