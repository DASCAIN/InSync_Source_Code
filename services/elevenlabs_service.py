import os
import json
import asyncio
import aiohttp
from typing import Optional, Dict, Any
from datetime import datetime
from enum import Enum
import logging
from urllib.parse import urlparse, parse_qs

logger = logging.getLogger(__name__)


class ConversationStatus(str, Enum):
    IDLE = "idle"
    CONNECTING = "connecting"
    ACTIVE = "active"
    LISTENING = "listening"
    SPEAKING = "speaking"
    ENDED = "ended"
    ERROR = "error"


class ElevenLabsService:
    def __init__(self) -> None:
        self.api_key = os.getenv("ELEVENLABS_API_KEY")
        self.agent_id = os.getenv("ELEVENLABS_AGENT_ID")
        self.api_url = os.getenv("ELEVENLABS_API_URL", "https://api.elevenlabs.io/v1")
        if not self.api_key:
            raise ValueError("ELEVENLABS_API_KEY not found in environment")
        if not self.agent_id:
            raise ValueError("ELEVENLABS_AGENT_ID not found in environment")
        self.headers = {
            "xi-api-key": self.api_key,
            "Content-Type": "application/json",
        }
        self.conversations: Dict[str, Dict[str, Any]] = {}

    async def start_conversation(
        self,
        user_id: str,
        lead_id: Optional[str] = None,
        user_name: Optional[str] = None,
        user_email: Optional[str] = None,
        memories: Optional[str] = None,
    ) -> Dict[str, Any]:
        params = {
            "agent_id": self.agent_id,
            "include_conversation_id": "true",
        }
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{self.api_url}/convai/conversation/get-signed-url",
                params=params,
                headers=self.headers,
            ) as response:
                response_text = await response.text()
                if response.status != 200:
                    logger.error(
                        f"Failed to start conversation ({response.status}): {response_text}"
                    )
                    raise Exception(
                        f"Failed to start conversation: {response.status} - {response_text}"
                    )
                data = json.loads(response_text) if response_text else {}
        signed_url = data.get("signed_url")
        conversation_id = None
        if signed_url:
            parsed_url = urlparse(signed_url)
            conv_ids = parse_qs(parsed_url.query).get("conversation_id")
            if conv_ids:
                conversation_id = conv_ids[0]
        if not conversation_id:
            logger.error("Failed to start conversation: missing conversation_id in response")
            raise Exception("Failed to start conversation: missing conversation_id")
        self.conversations[conversation_id] = {
            "user_id": user_id,
            "lead_id": lead_id,
            "status": ConversationStatus.CONNECTING,
            "started_at": datetime.utcnow(),
            "transcript": [],
            "metadata": {
                "user_id": user_id,
                "lead_id": lead_id,
                "user_name": user_name,
                "user_email": user_email,
                "started_at": datetime.utcnow().isoformat(),
                "memories": memories,
            },
            "agent_data": data,
        }
        return {
            "conversation_id": conversation_id,
            "agent_id": self.agent_id,
            "status": ConversationStatus.CONNECTING,
            "call_url": signed_url,
            "user_id": user_id,
            "memories": memories,
        }

    async def get_conversation_status(self, conversation_id: str) -> Dict[str, Any]:
        if conversation_id not in self.conversations:
            return {"error": "Conversation not found"}
        conv = self.conversations[conversation_id]
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{self.api_url}/convai/conversations/{conversation_id}",
                    headers=self.headers,
                ) as response:
                    if response.status == 200:
                        data = await response.json()
                        status = data.get("status", ConversationStatus.ACTIVE)
                        conv["status"] = status
                        if "transcript" in data:
                            conv["transcript"] = data["transcript"]
                        return {
                            "conversation_id": conversation_id,
                            "status": status,
                            "transcript": conv.get("transcript", []),
                            "duration": (datetime.utcnow() - conv["started_at"]).total_seconds(),
                        }
                    error_text = await response.text()
                    logger.warning(
                        f"Failed to get conversation status ({response.status}): {error_text}"
                    )
                    return {"error": f"Failed to get status: {response.status}"}
        except Exception as e:
            logger.error(f"Error getting conversation status: {e}")
            return {"error": str(e)}

    async def end_conversation(self, conversation_id: str) -> Dict[str, Any]:
        if conversation_id not in self.conversations:
            return {"error": "Conversation not found"}
        conv = self.conversations[conversation_id]
        try:
            async with aiohttp.ClientSession() as session:
                async with session.delete(
                    f"{self.api_url}/convai/conversations/{conversation_id}",
                    headers=self.headers,
                ) as response:
                    if response.status in [200, 204]:
                        conv["status"] = ConversationStatus.ENDED
                        conv["ended_at"] = datetime.utcnow()
                        return {
                            "conversation_id": conversation_id,
                            "status": ConversationStatus.ENDED,
                            "duration": (conv["ended_at"] - conv["started_at"]).total_seconds(),
                            "transcript": conv.get("transcript", []),
                        }
                    error_text = await response.text()
                    logger.error(
                        f"Failed to end conversation ({response.status}): {error_text}"
                    )
                    return {"error": f"Failed to end conversation: {response.status}"}
        except Exception as e:
            logger.error(f"Error ending conversation: {e}")
            return {"error": str(e)}

    def get_conversation(self, conversation_id: str) -> Optional[Dict[str, Any]]:
        return self.conversations.get(conversation_id)

    def list_conversations(self, user_id: Optional[str] = None) -> list:
        if user_id:
            return [
                {"conversation_id": conv_id, **conv}
                for conv_id, conv in self.conversations.items()
                if conv.get("user_id") == user_id
            ]
        return [
            {"conversation_id": conv_id, **conv}
            for conv_id, conv in self.conversations.items()
        ]


_elevenlabs_service: Optional[ElevenLabsService] = None


def get_elevenlabs_service() -> ElevenLabsService:
    global _elevenlabs_service
    if _elevenlabs_service is None:
        _elevenlabs_service = ElevenLabsService()
    return _elevenlabs_service
