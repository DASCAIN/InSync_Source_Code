import logging
from pathlib import Path

import inngest
from app.services.inngest_client import inngest_client
from app.services.document_processor import process_file, chunk_text
from app.services.vector_store import add_documents

logger = logging.getLogger(__name__)

def update_job_status(job_id, status_str, progress):
    if not job_id:
        return
    try:
        from config import get_settings
        import json
        settings = get_settings()
        job_file = settings.UPLOADS_DIR / "jobs" / f"{job_id}.json"
        if job_file.exists():
            with open(job_file, "r") as f:
                data = json.load(f)
            data["status"] = status_str
            data["progress"] = progress
            with open(job_file, "w") as f:
                json.dump(data, f)
    except Exception as e:
        logger.error(f"Failed to update job status: {e}")

@inngest_client.create_function(
    fn_id="RAG: Ingest PDF",
    name="RAG: Ingest PDF",
    trigger=inngest.TriggerEvent(event="app/document.uploaded"),
)
async def rag_ingest_pdf(*args, **kwargs) -> dict:
    ctx = None
    step = None
    if len(args) == 2:
        ctx, step = args
    elif len(args) == 1:
        arg = args[0]
        if hasattr(arg, 'run'):
            step = arg
            ctx = getattr(arg, 'ctx', getattr(arg, 'context', arg))
        elif hasattr(arg, 'step'):
            ctx = arg
            step = arg.step
        else:
            ctx = arg
            step = kwargs.get('step')
    else:
        ctx = kwargs.get('ctx')
        step = kwargs.get('step')
    """
    Background job triggered when a teacher uploads a document.
    Extracts text, chunks it, and indexes it into Qdrant.
    """
    # Simplify payload for direct testing: only pdf_path (or file_path) is strictly required
    file_path = ctx.event.data.get("pdf_path") or ctx.event.data.get("file_path")
    if not file_path:
        return {"status": "failed", "message": "Missing 'pdf_path' in event data."}

    filename = ctx.event.data.get("filename", Path(file_path).name)
    subject = ctx.event.data.get("subject", "Test Subject")
    topic = ctx.event.data.get("topic", "Test Topic")

    logger.info(f"Inngest started processing document: {file_path}")

    # Step 1: Extract Text
    def _extract_text():
        logger.info("Step 1: Extracting text")
        return process_file(file_path)
    
    extracted_pages = await step.run("extract_text", _extract_text)

    if not extracted_pages:
        logger.error("Extraction failed or returned empty text.")
        return {"status": "failed", "message": "Extraction failed or returned empty text."}

    # Step 2: Chunk Text
    def _chunk_text():
        logger.info("Step 2: Chunking text")
        # chunk_text returns a list of LangChain Document objects.
        # Inngest step results must be JSON serializable, so we serialize them manually.
        docs = chunk_text(extracted_pages, subject, topic, filename)
        return [
            {
                "page_content": doc.page_content,
                "metadata": doc.metadata
            }
            for doc in docs
        ]

    chunks_data = await step.run("chunk_text", _chunk_text)

    if not chunks_data:
        logger.error("Chunking failed or returned empty chunks.")
        return {"status": "failed", "message": "Chunking failed or returned empty chunks."}

    # Step 3: Embed & Store in Vector DB
    def _store_vectors():
        logger.info("Step 3: Storing vectors in Qdrant")
        from langchain_core.documents import Document
        # Reconstruct LangChain Document objects
        docs = [
            Document(page_content=c["page_content"], metadata=c["metadata"])
            for c in chunks_data
        ]
        add_documents(subject, topic, docs)
        return True

    await step.run("store_vectors", _store_vectors)

    logger.info(f"Successfully processed and indexed {len(chunks_data)} chunks for {file_path}")
    return {"status": "success", "chunks_processed": len(chunks_data)}


@inngest_client.create_function(
    fn_id="RAG: Query PDF",
    name="RAG: Query PDF",
    trigger=inngest.TriggerEvent(event="app/document.queried"),
)
async def rag_query_pdf(*args, **kwargs) -> dict:
    ctx = None
    step = None
    if len(args) == 2:
        ctx, step = args
    elif len(args) == 1:
        arg = args[0]
        if hasattr(arg, 'run'):
            step = arg
            ctx = getattr(arg, 'ctx', getattr(arg, 'context', arg))
        elif hasattr(arg, 'step'):
            ctx = arg
            step = arg.step
        else:
            ctx = arg
            step = kwargs.get('step')
    else:
        ctx = kwargs.get('ctx')
        step = kwargs.get('step')
    """
    Background job to query the RAG pipeline directly from Inngest.
    Expects 'subject', 'topic', and 'query' in the event payload.
    """
    subject = ctx.event.data.get("subject", "Science")
    topic = ctx.event.data.get("topic", "Physics")
    query = ctx.event.data.get("query", "Summarize this topic.")

    logger.info(f"Inngest RAG Query started for: {query}")

    # Step 1: Retrieve Context
    async def _retrieve_context():
        from app.services.retrieval_pipeline import retrieve_hybrid
        docs = await retrieve_hybrid(subject, topic, query, top_n_child=8)
        
        # Serialize docs
        return [
            {
                "page_content": doc.page_content,
                "metadata": doc.metadata
            }
            for doc in docs
        ]

    retrieved_chunks = await step.run("retrieve_context", _retrieve_context)

    if not retrieved_chunks:
        return {"status": "failed", "answer": f"No study material found for {topic} in {subject}."}

    # Step 2: Generate Answer
    async def _generate_answer():
        from app.services.generator import get_llm
        from langchain_core.prompts import ChatPromptTemplate
        
        context_parts = []
        for idx, doc in enumerate(retrieved_chunks, start=1):
            source = doc["metadata"].get("source_file", "Unknown Source")
            page = doc["metadata"].get("page", "Unknown Page")
            section = doc["metadata"].get("section", "Unknown Section")
            content = doc["page_content"].strip()
            context_parts.append(f"--- Chunk {idx} ---\nSource: {source}\nPage: {page}\nSection: {section}\nContent:\n{content}")
            
        context_str = "\n\n".join(context_parts)
        
        system_prompt = (
            "You are an expert AI educational assistant. "
            "Use ONLY the provided context to answer the user's question. "
            "Include inline source citations formatted exactly as `[Source: <file>, Page: <page>, Section: <section>]` "
            "at the end of sentences that reference facts from the context. "
            "If the answer is not in the context, say you do not know."
        )
        
        prompt = ChatPromptTemplate.from_messages([
            ("system", system_prompt),
            ("human", "Context:\n\n{context}\n\nQuestion: {question}"),
        ])
        
        llm = get_llm(temperature=0.3)
        chain = prompt | llm
        
        response = await chain.ainvoke({"context": context_str, "question": query})
        return response.content

    answer = await step.run("generate_answer", _generate_answer)

    return {
        "status": "success",
        "question": query,
        "answer": answer,
        "sources_used": len(retrieved_chunks)
    }

@inngest_client.create_function(
    fn_id="RAG: Generate Assignment",
    name="RAG: Generate Assignment",
    trigger=inngest.TriggerEvent(event="app/assignment.generate"),
)
async def rag_generate_assignment(*args, **kwargs) -> dict:
    ctx = None
    step = None
    if len(args) == 2:
        ctx, step = args
    elif len(args) == 1:
        arg = args[0]
        if hasattr(arg, 'run'):
            step = arg
            ctx = getattr(arg, 'ctx', getattr(arg, 'context', arg))
        elif hasattr(arg, 'step'):
            ctx = arg
            step = arg.step
        else:
            ctx = arg
            step = kwargs.get('step')
    else:
        ctx = kwargs.get('ctx')
        step = kwargs.get('step')
    file_path = ctx.event.data.get("file_path")
    job_id = ctx.event.data.get("job_id")
    if not file_path:
        update_job_status(job_id, "Failed: Missing file path", 100)
        return {"status": "failed", "message": "Missing 'file_path'"}

    subject = ctx.event.data.get("subject", "Test Subject")
    topic = ctx.event.data.get("topic", "Test Topic")
    batch_id = ctx.event.data.get("batch_id", "C101")
    filename = ctx.event.data.get("filename", Path(file_path).name)

    logger.info(f"Generating assignment from {file_path}")

    # Step 1: Extract & Chunk Text
    def _extract_and_chunk():
        extracted = process_file(file_path)
        docs = chunk_text(extracted, subject, topic, filename)
        return [{"page_content": doc.page_content, "metadata": doc.metadata} for doc in docs]

    chunks_data = await step.run("extract_and_chunk", _extract_and_chunk)

    if not chunks_data:
        update_job_status(job_id, "Extraction/Chunking failed.", 100)
        return {"status": "failed", "message": "Extraction/Chunking failed."}
        
    update_job_status(job_id, "Extracting text and chunking...", 25)

    # Step 2: Ingest into Qdrant for the AI Chat Tutor
    def _store_vectors():
        from langchain_core.documents import Document
        docs = [Document(page_content=c["page_content"], metadata=c["metadata"]) for c in chunks_data]
        add_documents(subject, topic, docs)
        return True

    await step.run("store_vectors", _store_vectors)
    
    update_job_status(job_id, "Generating assignment...", 50)

    # Step 3: LLM Generation
    async def _generate_json():
        from app.services.generator import generate_structured_assignment
        # Combine all chunk text to provide the full assignment context
        context_str = "\n\n".join([c["page_content"] for c in chunks_data])
        # Calling the new structured output function
        assignment_data = await generate_structured_assignment(subject, topic, context_str)
        # Pydantic models need to be dumped to dict for Inngest step output
        return assignment_data.model_dump()

    assignment_json = await step.run("generate_json", _generate_json)
    
    update_job_status(job_id, "Generating study notes...", 70)

    # Step 3.5: Generate and Save Notes
    async def _generate_and_save_notes():
        from app.services.generator import generate_notes, save_notes_to_db
        from asgiref.sync import sync_to_async
        
        notes_md = await generate_notes(subject, topic, focus="Summary & Bullets")
        await sync_to_async(save_notes_to_db, thread_sensitive=False)(notes_md, subject, topic)
        return True
        
    await step.run("generate_and_save_notes", _generate_and_save_notes)
    
    update_job_status(job_id, "Saving to database...", 85)

    # Step 4: Persist to Django DB
    from app.services.generator import save_assignment_to_db
    from asgiref.sync import sync_to_async

    async def _save_to_db_wrapper():
        return await sync_to_async(save_assignment_to_db, thread_sensitive=False)(assignment_json, subject, topic, batch_id, file_path)

    assign_code = await step.run("save_to_db", _save_to_db_wrapper)
    
    update_job_status(job_id, "Completed!", 100)

    return {
        "status": "success",
        "assignment_code": assign_code,
        "questions_generated": len(assignment_json.get("questions", []))
    }

@inngest_client.create_function(
    fn_id="RAG: Tutor Chat",
    name="RAG: Tutor Chat",
    trigger=inngest.TriggerEvent(event="app/tutor.chat"),
)
async def rag_tutor_chat(*args, **kwargs) -> dict:
    ctx = None
    step = None
    if len(args) == 2:
        ctx, step = args
    elif len(args) == 1:
        arg = args[0]
        if hasattr(arg, 'run'):
            step = arg
            ctx = getattr(arg, 'ctx', getattr(arg, 'context', arg))
        elif hasattr(arg, 'step'):
            ctx = arg
            step = arg.step
        else:
            ctx = arg
            step = kwargs.get('step')
    else:
        ctx = kwargs.get('ctx')
        step = kwargs.get('step')
        
    """
    Background job to simulate the tutor chat process and log each step in Inngest.
    Expects 'message' and 'user_id' in the event payload.
    """
    message = ctx.event.data.get("message", "Can you explain backpropagation?")
    user_id = ctx.event.data.get("user_id", "test_user_123")

    logger.info(f"Inngest RAG Tutor Chat started for message: {message}")

    # Step 1: Retrieve Mem0 Memory
    async def _retrieve_mem0():
        from services.mem0_service import get_mem0_service
        mem0_service = get_mem0_service()
        memories = await mem0_service.retrieve_memories(message, user_id)
        return memories

    memories = await step.run("retrieve_mem0", _retrieve_mem0)

    # Step 2: Retrieve Global Qdrant Context
    async def _retrieve_qdrant():
        from app.services.vector_store import retrieve_global_documents
        import asyncio
        docs = await asyncio.to_thread(retrieve_global_documents, message)
        return "\n\n".join([doc.page_content for doc in docs]) if docs else "No specific context found in uploaded materials."

    pdf_context = await step.run("retrieve_qdrant", _retrieve_qdrant)

    # Step 3: Generate Answer
    async def _generate_answer():
        from services.tutor_agent_service import SYSTEM_PROMPT
        from config import get_settings
        from openai import AsyncOpenAI
        
        settings = get_settings()
        prompt = [
            {"role": "system", "content": SYSTEM_PROMPT.format(MEMORIES=memories, PDF_CONTEXT=pdf_context)},
            {"role": "user", "content": message},
        ]
        
        async with AsyncOpenAI(api_key=settings.OPENAI_API_KEY) as client:
            response = await client.chat.completions.create(
                model=settings.LLM_MODEL,
                messages=prompt,
                temperature=0.3,
            )
        content = response.choices[0].message.content if response.choices else None
        return content.replace("**", "").strip() if content else ""

    answer = await step.run("generate_answer", _generate_answer)

    # Step 4: Save Memory
    async def _save_memory():
        from services.mem0_service import get_mem0_service
        mem0_service = get_mem0_service()
        summary = f"Q: {message}\nA: {answer}"
        await mem0_service.add_memory(summary, user_id)
        return True

    await step.run("save_memory", _save_memory)

    return {
        "status": "success",
        "message": message,
        "answer": answer
    }



