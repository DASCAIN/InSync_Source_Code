import os
from typing import Optional, AsyncGenerator

from openai import AsyncOpenAI

from config import get_settings
from services.mem0_service import get_mem0_service
from utils.logger import get_logger

logger = get_logger(__name__)

SYSTEM_PROMPT = """You are an advanced, supportive AI tutor. You personalize explanations, encourage problem-solving, and adapt difficulty based on student understanding.

Scope rules:
 - You MUST strongly rely on the provided 'Course Material Context' (from uploaded PDFs) when answering questions.
 - If the answer is found in the 'Course Material Context', formulate your response primarily using that information.
 - If the answer is not in the context, you may use your general knowledge, but state clearly that it is outside the provided course materials.
 - Do not invent facts, examples, formulas, code behavior, or citations. If you are unsure, say what is uncertain and ask a clarifying question.
 - Keep examples, practice questions, and interview prep strictly within ML or DSA.
 
Teaching rules:
 - When greeting the user, always suggest they grab a pen and paper to trace algorithms and write down their thoughts.
 - Explain step-by-step and start with simple, intuitive explanations.
 - Encourage the student to attempt answers; do not jump directly to the final answer.
 - Ask clarifying or follow-up questions when needed.
 - Detect confusion and provide alternative explanations or examples.
 - Support coding only when it is directly related to DSA or ML.
 - Maintain a warm, mentor-like tone.
 - Actively encourage the student to use pen and paper to write down their thoughts, trace algorithms, or solve math problems when interacting with you.
 
Response formatting rules:
 - ALWAYS respond in English, regardless of the language of the user's prompt or context.
 - Use plain text only (no Markdown, no bold markers like **).
 - Organize the response into short paragraphs.
 - Insert a blank line between subtopics or steps.
 
Course Material Context:
{PDF_CONTEXT}

Long-term memory for this student:
{MEMORIES}

{MEMORIES} and {PDF_CONTEXT} are replaced at runtime with the retrieval outputs.
"""


class TutorAgentService:
    def __init__(self) -> None:
        settings = get_settings()
        api_key = settings.OPENAI_API_KEY
        model = settings.LLM_MODEL
        if not api_key:
            raise ValueError("OPENAI_API_KEY not found in environment or .env")
        if not model:
            raise ValueError("LLM_MODEL not found in environment or .env")
        self.api_key = api_key
        self.model = model

    async def _build_prompt(self, message: str, user_id: str, question_context: str = "", history: list = None):
        """Build the prompt messages list (shared by chat and chat_stream)."""
        mem0_service = get_mem0_service()
        memories = await mem0_service.retrieve_memories(message, user_id)
        
        # Check cancellation before vector store retrieval
        if getattr(self, '_cancel_event', None) and self._cancel_event.is_set():
            return [], mem0_service

        from app.services.vector_store import retrieve_global_documents
        import asyncio
        docs = await asyncio.to_thread(retrieve_global_documents, message)
        
        # Check cancellation after vector store retrieval
        if getattr(self, '_cancel_event', None) and self._cancel_event.is_set():
            return [], mem0_service

        pdf_context = "\n\n".join([doc.page_content for doc in docs]) if docs else "No specific context found in uploaded materials."

        system_content = SYSTEM_PROMPT.format(MEMORIES=memories, PDF_CONTEXT=pdf_context)
        if question_context:
            system_content += f"\n\nCURRENT QUESTION CONTEXT (Keep this in mind for the user's questions):\n{question_context}"

        prompt = [
            {"role": "system", "content": system_content},
        ]

        if history:
            prompt.extend(history)

        prompt.append({"role": "user", "content": message})
        return prompt, mem0_service

    async def chat(self, message: str, user_id: str, question_context: str = "", history: list = None) -> str:
        if not message.strip():
            raise ValueError("Message is empty")
        if not user_id.strip():
            raise ValueError("User id is empty")

        prompt, mem0_service = await self._build_prompt(message, user_id, question_context, history)

        async with AsyncOpenAI(api_key=self.api_key) as client:
            response = await client.chat.completions.create(
                model=self.model,
                messages=prompt,
                temperature=0.3,
            )
        content = response.choices[0].message.content if response.choices else None
        if not content:
            raise ValueError("OpenAI response was empty")

        cleaned = content.replace("**", "").strip()
        summary = f"Q: {message}\nA: {cleaned}"
        await mem0_service.add_memory(summary, user_id)
        return cleaned

    async def chat_stream(
        self, message: str, user_id: str, question_context: str = "", history: list = None,
        cancel_event=None,
    ) -> AsyncGenerator[str, None]:
        """Stream tokens from OpenAI. Yields each delta content string as it arrives.

        If ``cancel_event`` (an asyncio.Event) is set, the generator stops
        consuming the OpenAI stream immediately.

        After the generator is fully consumed (or closed due to client disconnect),
        the caller is responsible for saving the accumulated reply via
        ``save_reply_and_memory``.
        """
        if not message.strip():
            raise ValueError("Message is empty")
        if not user_id.strip():
            raise ValueError("User id is empty")

        self._cancel_event = cancel_event
        try:
            prompt, _ = await self._build_prompt(message, user_id, question_context, history)
            if not prompt:  # Cancelled during prompt building
                return

            async with AsyncOpenAI(api_key=self.api_key) as client:
                stream = await client.chat.completions.create(
                    model=self.model,
                    messages=prompt,
                    temperature=0.3,
                    stream=True,
                )
                try:
                    async for chunk in stream:
                        # Check cancellation between every chunk
                        if cancel_event and cancel_event.is_set():
                            break
                        delta = chunk.choices[0].delta if chunk.choices else None
                        if delta and delta.content:
                            # Strip markdown bold markers in real time
                            token = delta.content.replace("**", "")
                            yield token
                finally:
                    # Ensure the OpenAI stream is closed even on cancellation
                    await stream.close()
        finally:
            self._cancel_event = None

    async def save_reply_and_memory(self, message: str, full_reply: str, user_id: str):
        """Persist the accumulated streamed reply into mem0 long-term memory."""
        if not full_reply.strip():
            return
        mem0_service = get_mem0_service()
        summary = f"Q: {message}\nA: {full_reply.strip()}"
        await mem0_service.add_memory(summary, user_id)


_tutor_agent_service: Optional[TutorAgentService] = None


def get_tutor_agent_service() -> TutorAgentService:
    global _tutor_agent_service
    if _tutor_agent_service is None:
        _tutor_agent_service = TutorAgentService()
    return _tutor_agent_service
