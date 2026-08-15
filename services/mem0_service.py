import os
from typing import Any, Iterable, List, Optional

from mem0 import AsyncMemoryClient


class Mem0Service:
    def __init__(self) -> None:
        api_key = os.getenv("MEM0_API_KEY")
        if not api_key:
            raise ValueError("MEM0_API_KEY not found in environment")
        self.api_key = api_key

    def _client(self) -> AsyncMemoryClient:
        return AsyncMemoryClient(api_key=self.api_key)

    async def add_memory(self, message: str, user_id: str) -> None:
        if not message.strip():
            raise ValueError("Memory message is empty")
        await self._client().add(
            [
                {
                    "role": "user",
                    "content": message,
                }
            ],
            user_id=user_id,
        )

    async def retrieve_memories(self, message: str, user_id: str) -> str:
        if not message.strip():
            raise ValueError("Search message is empty")
        
        # Append a broad context string to ensure vector search matches general learning preferences and weak topics
        search_query = f"{message} user learning preferences, weak topics, and academic subjects"
        
        results = await self._client().search(search_query, filters={"user_id": user_id})
        memories = self._extract_memories(results)
        return "\n".join(memories) if memories else "No memories found"

    def _extract_memories(self, results: Any) -> List[str]:
        candidates: Optional[Iterable[Any]] = None
        if isinstance(results, dict):
            candidates = (
                results.get("results")
                or results.get("memories")
                or results.get("data")
                or results.get("items")
            )
        elif isinstance(results, list):
            candidates = results

        if not candidates:
            return []

        memories: List[str] = []
        for item in candidates:
            if isinstance(item, str):
                memories.append(item)
                continue
            if isinstance(item, dict):
                text = (
                    item.get("memory")
                    or item.get("text")
                    or item.get("content")
                    or item.get("data")
                )
                if isinstance(text, str) and text.strip():
                    memories.append(text.strip())
        return memories


_mem0_service: Optional[Mem0Service] = None


def get_mem0_service() -> Mem0Service:
    global _mem0_service
    if _mem0_service is None:
        _mem0_service = Mem0Service()
    return _mem0_service
