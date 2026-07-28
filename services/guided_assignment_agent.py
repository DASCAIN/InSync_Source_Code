import json
from typing import Optional, Tuple
from openai import AsyncOpenAI
from config import get_settings
from utils.logger import get_logger

logger = get_logger(__name__)

SYSTEM_PROMPT = """You are an advanced, witty curriculum assistant helping a professor build a custom assignment.
You are currently chatting with the professor.

Your tone should be a blend of playful and formal—respectful of their expertise but engaging and collaborative.
When the professor asks for ideas (e.g., "What types of questions can I generate from this PDF?"), you should enthusiastically suggest 2-3 specific question types or focus areas based on the provided text, adding a touch of academic flair. 
For example: "Ah, excellent choice Professor! Based on the text, we could grill the students on the core mathematical formulas, or perhaps craft a few scenario-based questions to test their conceptual grasp. Shall we focus on the math, or mix it up?"

Course Material Context for '{SUBJECT}' - '{TOPIC}':
{PDF_CONTEXT}

Scope rules:
 - You MUST strongly rely on the provided 'Course Material Context' (from uploaded PDFs) when answering questions.
 - Discuss potential questions, focus areas, and difficulty levels with the professor.
 - You have access to a tool named `create_assignment`. 
 - WHEN THE PROFESSOR ASKS TO GENERATE OR FINALIZE THE ASSIGNMENT (e.g., "create an assignment based on the mathematical part", "looks good, generate it", "make 5 questions on X"):
   You MUST call the `create_assignment` tool, passing their custom instructions to it.
 - Do NOT try to output the assignment JSON in your chat response. Let the tool do the work.
"""

class GuidedAssignmentAgentService:
    def __init__(self) -> None:
        settings = get_settings()
        self.api_key = settings.OPENAI_API_KEY
        self.model = settings.LLM_MODEL
        if not self.api_key or not self.model:
            raise ValueError("OPENAI_API_KEY or LLM_MODEL not found")
        
        # We will keep a very basic in-memory conversation history for this session.
        # In production, this should use mem0 or a DB table.
        self.sessions = {}

    async def chat(self, message: str, session_id: str, subject: str, topic: str, batch_id: str) -> Tuple[str, bool]:
        if session_id not in self.sessions:
            self.sessions[session_id] = []
            
        history = self.sessions[session_id]
        
        # Retrieve context from uploaded PDFs
        from app.services.vector_store import retrieve_global_documents
        import asyncio
        docs = await asyncio.to_thread(retrieve_global_documents, f"{subject} {topic} {message}")
        pdf_context = "\n\n".join([doc.page_content for doc in docs]) if docs else "No specific context found."

        system_msg = SYSTEM_PROMPT.format(SUBJECT=subject, TOPIC=topic, PDF_CONTEXT=pdf_context)
        
        messages = [{"role": "system", "content": system_msg}] + history + [{"role": "user", "content": message}]

        tools = [
            {
                "type": "function",
                "function": {
                    "name": "create_assignment",
                    "description": "Trigger the generation and saving of the assignment based on the professor's custom instructions.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "custom_instructions": {
                                "type": "string",
                                "description": "The exact instructions for how the assignment should be generated (e.g., 'Make 5 MCQ questions focused on probability theory, advanced difficulty')."
                            }
                        },
                        "required": ["custom_instructions"],
                    },
                }
            }
        ]

        async with AsyncOpenAI(api_key=self.api_key) as client:
            response = await client.chat.completions.create(
                model=self.model,
                messages=messages,
                tools=tools,
                temperature=0.3,
            )
            
            response_message = response.choices[0].message
            
            if response_message.tool_calls:
                # Agent wants to call the tool
                tool_call = response_message.tool_calls[0]
                if tool_call.function.name == "create_assignment":
                    args = json.loads(tool_call.function.arguments)
                    custom_instructions = args.get("custom_instructions", "")
                    
                    # 1. Generate JSON
                    from app.services.generator import generate_structured_assignment, save_assignment_to_db
                    assignment_schema = await generate_structured_assignment(
                        subject=subject,
                        topic=topic,
                        context=pdf_context,
                        custom_instructions=custom_instructions
                    )
                    
                    # 2. Save to DB
                    from asgiref.sync import sync_to_async
                    assign_code = await sync_to_async(save_assignment_to_db, thread_sensitive=False)(
                        assignment_json=assignment_schema.model_dump(),
                        subject=subject,
                        topic=topic,
                        batch_id=batch_id,
                        file_path=""
                    )
                    
                    final_msg = f"I have successfully generated and saved the assignment based on your instructions! The assignment code is {assign_code}."
                    self.sessions[session_id].append({"role": "user", "content": message})
                    self.sessions[session_id].append({"role": "assistant", "content": final_msg})
                    return final_msg, True

            # Standard chat response
            content = response_message.content
            if not content:
                content = "I'm sorry, I couldn't process that."
                
            self.sessions[session_id].append({"role": "user", "content": message})
            self.sessions[session_id].append({"role": "assistant", "content": content})
            return content, False

_guided_assignment_agent_service: Optional[GuidedAssignmentAgentService] = None

def get_guided_assignment_agent_service() -> GuidedAssignmentAgentService:
    global _guided_assignment_agent_service
    if _guided_assignment_agent_service is None:
        _guided_assignment_agent_service = GuidedAssignmentAgentService()
    return _guided_assignment_agent_service
