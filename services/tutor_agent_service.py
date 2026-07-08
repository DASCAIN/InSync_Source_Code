import os
from typing import Optional

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
 - Explain step-by-step and start with simple, intuitive explanations.
 - Encourage the student to attempt answers; do not jump directly to the final answer.
 - Ask clarifying or follow-up questions when needed.
 - Detect confusion and provide alternative explanations or examples.
 - Support coding only when it is directly related to DSA or ML.
 - Maintain a warm, mentor-like tone.
 
Response formatting rules:
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

    async def chat(self, message: str, user_id: str) -> str:
        if not message.strip():
            raise ValueError("Message is empty")
        if not user_id.strip():
            raise ValueError("User id is empty")

        mem0_service = get_mem0_service()
        memories = await mem0_service.retrieve_memories(message, user_id)
        
        # Retrieve context from uploaded PDFs globally
        from app.services.vector_store import retrieve_global_documents
        import asyncio
        docs = await asyncio.to_thread(retrieve_global_documents, message)
        pdf_context = "\n\n".join([doc.page_content for doc in docs]) if docs else "No specific context found in uploaded materials."

        prompt = [
            {"role": "system", "content": SYSTEM_PROMPT.format(MEMORIES=memories, PDF_CONTEXT=pdf_context)},
            {"role": "user", "content": message},
        ]

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


_tutor_agent_service: Optional[TutorAgentService] = None


def get_tutor_agent_service() -> TutorAgentService:
    global _tutor_agent_service
    if _tutor_agent_service is None:
        _tutor_agent_service = TutorAgentService()
    return _tutor_agent_service
