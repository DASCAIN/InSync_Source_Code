import logging
import json
import os
import time
from pathlib import Path

import inngest
from app.services.inngest_client import inngest_client
from app.services.document_processor import process_file, chunk_text
from app.services.ingestion_artifacts import (
    cleanup_artifacts,
    read_documents_artifact,
    read_pages_artifact,
    sha256_file,
    write_documents_artifact,
    write_pages_artifact,
)
from app.services.vector_store import add_documents, get_document_index_status

logger = logging.getLogger(__name__)

def update_job_status(job_id, status_str, progress, step=None, **details):
    if not job_id:
        return
    try:
        from config import get_settings
        settings = get_settings()
        jobs_dir = settings.UPLOADS_DIR / "jobs"
        jobs_dir.mkdir(parents=True, exist_ok=True)
        job_file = jobs_dir / f"{job_id}.json"
        data = {}
        if job_file.exists():
            try:
                with open(job_file, "r") as f:
                    data = json.load(f)
            except Exception:
                data = {}
        data["status"] = status_str
        data["progress"] = progress
        data.update(details)
        if step is not None:
            cur_step = data.get("step", 1)
            data["step"] = max(cur_step, step)
        temporary = job_file.with_suffix(".json.tmp")
        with open(temporary, "w") as f:
            json.dump(data, f)
        os.replace(temporary, job_file)
    except Exception as e:
        logger.error(f"Failed to update job status: {e}")


def _artifact_id(job_id: str | None, file_path: str) -> str:
    if job_id:
        return job_id
    path = Path(file_path)
    try:
        version = path.stat().st_mtime_ns
    except OSError:
        version = 0
    return f"ingest-{path.name}-{version}"


def _extract_to_artifact(
    file_path: str,
    artifact_id: str,
    *,
    existing_artifact_path: str = "",
    known_file_hash: str = "",
) -> dict:
    started_at = time.perf_counter()
    if existing_artifact_path and Path(existing_artifact_path).exists():
        artifact_hash, pages = read_pages_artifact(existing_artifact_path)
        file_hash = known_file_hash or artifact_hash or sha256_file(file_path)
        artifact_path = existing_artifact_path
        reused = True
    else:
        file_hash = known_file_hash or sha256_file(file_path)
        pages = process_file(file_path)
        artifact_path = write_pages_artifact(
            artifact_id,
            pages,
            file_sha256=file_hash,
        )
        reused = False

    return {
        "pages_artifact_path": artifact_path,
        "file_sha256": file_hash,
        "page_count": len(pages),
        "ocr_page_count": sum(page.get("extraction_method") == "ocr" for page in pages),
        "artifact_reused": reused,
        "duration_seconds": round(time.perf_counter() - started_at, 3),
    }


def _chunk_to_artifact(
    extraction: dict,
    artifact_id: str,
    subject: str,
    topic: str,
    filename: str,
) -> dict:
    started_at = time.perf_counter()
    _, pages = read_pages_artifact(extraction["pages_artifact_path"])
    documents = chunk_text(pages, subject, topic, filename)
    chunks_path = write_documents_artifact(
        artifact_id,
        documents,
        file_sha256=extraction["file_sha256"],
    )
    return {
        "chunks_artifact_path": chunks_path,
        "file_sha256": extraction["file_sha256"],
        "chunk_count": len(documents),
        "duration_seconds": round(time.perf_counter() - started_at, 3),
    }

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
    job_id = ctx.event.data.get("job_id")
    if not file_path:
        update_job_status(job_id, "Missing file path", 100)
        return {"status": "failed", "message": "Missing 'pdf_path' in event data."}

    filename = ctx.event.data.get("filename", Path(file_path).name)
    subject = ctx.event.data.get("subject", "Test Subject")
    topic = ctx.event.data.get("topic", "Test Topic")
    tenant_id = str(ctx.event.data.get("tenant_id") or "default")
    artifact_id = _artifact_id(job_id, file_path)
    pages_artifact_path = str(ctx.event.data.get("pages_artifact_path") or "")
    known_file_hash = str(ctx.event.data.get("file_sha256") or "")

    logger.info(f"Inngest started processing document: {file_path}")
    update_job_status(job_id, "Fingerprinting document", 10, step=1)

    def _fingerprint_file():
        return known_file_hash or sha256_file(file_path)

    file_hash = await step.run("fingerprint_file", _fingerprint_file)

    def _check_existing_index():
        return get_document_index_status(
            subject,
            topic,
            filename,
            file_hash,
            tenant_id=tenant_id,
        )

    existing_index = await step.run("check_existing_index", _check_existing_index)
    if existing_index.get("complete"):
        update_job_status(
            job_id,
            "Ready (already indexed)",
            100,
            step=3,
            document_id=existing_index["document_id"],
            reused_chunks=existing_index["indexed_count"],
        )
        cleanup_artifacts(pages_artifact_path)
        return {
            "status": "success",
            "chunks_processed": existing_index["indexed_count"],
            "artifact_reused": False,
            "qdrant": {
                "status": "already_indexed",
                "document_id": existing_index["document_id"],
                "indexed_count": 0,
                "reused_count": existing_index["indexed_count"],
            },
        }

    update_job_status(job_id, "Reading pages", 20, step=1)

    # Step 1: Extract once and persist a small artifact reference. Plan Mode can
    # provide an artifact it already created, avoiding a second PDF/OCR pass.
    def _extract_text():
        logger.info("Step 1: Extracting text")
        return _extract_to_artifact(
            file_path,
            artifact_id,
            existing_artifact_path=pages_artifact_path,
            known_file_hash=file_hash,
        )
    
    extraction = await step.run("extract_text", _extract_text)

    if not extraction or not extraction.get("page_count"):
        logger.error("Extraction failed or returned empty text.")
        update_job_status(job_id, "Extraction failed", 100)
        return {"status": "failed", "message": "Extraction failed or returned empty text."}

    update_job_status(
        job_id,
        "Finding key concepts",
        55,
        step=2,
        page_count=extraction["page_count"],
        ocr_page_count=extraction["ocr_page_count"],
        extraction_seconds=extraction["duration_seconds"],
    )

    # Step 2: Chunk Text
    def _chunk_text():
        logger.info("Step 2: Chunking text")
        return _chunk_to_artifact(
            extraction,
            artifact_id,
            subject,
            topic,
            filename,
        )

    chunking = await step.run("chunk_text", _chunk_text)

    if not chunking or not chunking.get("chunk_count"):
        logger.error("Chunking failed or returned empty chunks.")
        update_job_status(job_id, "Chunking failed", 100)
        return {"status": "failed", "message": "Chunking failed or returned empty chunks."}

    update_job_status(
        job_id,
        "Embedding and indexing",
        85,
        step=3,
        chunk_count=chunking["chunk_count"],
        chunking_seconds=chunking["duration_seconds"],
    )

    # Step 3: Embed & Store in Vector DB
    def _store_vectors():
        logger.info("Step 3: Storing vectors in Qdrant")
        file_hash, documents = read_documents_artifact(chunking["chunks_artifact_path"])
        return add_documents(
            subject,
            topic,
            documents,
            file_sha256=file_hash,
            tenant_id=tenant_id,
        )

    indexing = await step.run("store_vectors", _store_vectors)

    update_job_status(
        job_id,
        "Ready",
        100,
        step=3,
        document_id=indexing.get("document_id"),
        indexed_chunks=indexing.get("indexed_count", 0),
        reused_chunks=indexing.get("reused_count", 0),
    )
    cleanup_artifacts(
        extraction.get("pages_artifact_path", ""),
        chunking.get("chunks_artifact_path", ""),
    )
    logger.info(
        "Successfully processed %d chunks for %s (Qdrant status: %s)",
        chunking["chunk_count"],
        file_path,
        indexing.get("status"),
    )
    return {
        "status": "success",
        "chunks_processed": chunking["chunk_count"],
        "pages_processed": extraction["page_count"],
        "ocr_pages": extraction["ocr_page_count"],
        "artifact_reused": extraction["artifact_reused"],
        "qdrant": indexing,
    }


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
    tenant_id = str(ctx.event.data.get("tenant_id") or "default")
    artifact_id = _artifact_id(job_id, file_path)
    known_file_hash = str(ctx.event.data.get("file_sha256") or "")

    logger.info(f"Generating assignment from {file_path}")

    # Step 1: Persist parsed pages and chunks outside the Inngest step payload.
    def _extract_and_chunk():
        extraction = _extract_to_artifact(
            file_path,
            artifact_id,
            known_file_hash=known_file_hash,
        )
        chunking = _chunk_to_artifact(
            extraction,
            artifact_id,
            subject,
            topic,
            filename,
        )
        return {"extraction": extraction, "chunking": chunking}

    prepared = await step.run("extract_and_chunk", _extract_and_chunk)

    if not prepared or not prepared.get("chunking", {}).get("chunk_count"):
        update_job_status(job_id, "Extraction/Chunking failed.", 100)
        return {"status": "failed", "message": "Extraction/Chunking failed."}

    extraction = prepared["extraction"]
    chunking = prepared["chunking"]
        
    update_job_status(
        job_id,
        "Extracted and chunked",
        25,
        page_count=extraction["page_count"],
        ocr_page_count=extraction["ocr_page_count"],
        chunk_count=chunking["chunk_count"],
    )

    # Step 2: Ingest into Qdrant for the AI Chat Tutor
    def _store_vectors():
        file_hash, documents = read_documents_artifact(chunking["chunks_artifact_path"])
        return add_documents(
            subject,
            topic,
            documents,
            file_sha256=file_hash,
            tenant_id=tenant_id,
        )

    await step.run("store_vectors", _store_vectors)
    
    update_job_status(job_id, "Generating assignment...", 50)

    # Step 3: LLM Generation
    async def _generate_json():
        from app.services.generator import generate_structured_assignment
        # Combine all chunk text to provide the full assignment context
        _, documents = read_documents_artifact(chunking["chunks_artifact_path"])
        context_str = "\n\n".join(document.page_content for document in documents)
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

    cleanup_artifacts(
        extraction.get("pages_artifact_path", ""),
        chunking.get("chunks_artifact_path", ""),
    )

    return {
        "status": "success",
        "assignment_code": assign_code,
        "questions_generated": len(assignment_json.get("questions", [])),
        "chunks_processed": chunking["chunk_count"],
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
    Background job for Inngest pipeline observability.
    Displays detailed step metrics (Mem0 latency, Qdrant vector retrieval latency,
    OpenAI TTFT / streaming latency, and chat summary) in the Inngest dashboard.
    """
    message = ctx.event.data.get("message", "")
    user_id = ctx.event.data.get("user_id", "")
    answer = ctx.event.data.get("answer", "")
    metrics = ctx.event.data.get("metrics", {})

    logger.info(f"Inngest RAG Tutor Chat pipeline telemetry for user: {user_id}")

    # Step 1: Mem0 Memory Retrieval Metrics
    async def _step_mem0():
        mem0_data = metrics.get("mem0", {})
        return {
            "duration_ms": mem0_data.get("duration_ms", 0),
            "memories_found": mem0_data.get("memories_found", 0),
            "preview": mem0_data.get("preview", "None")
        }
    await step.run("1_mem0_retrieval", _step_mem0)

    # Step 2: Qdrant Vector Retrieval Metrics
    async def _step_qdrant():
        qdrant_data = metrics.get("qdrant", {})
        return {
            "duration_ms": qdrant_data.get("duration_ms", 0),
            "chunks_retrieved": qdrant_data.get("chunks_retrieved", 0),
            "sources": qdrant_data.get("sources", [])
        }
    await step.run("2_qdrant_rag_search", _step_qdrant)

    # Step 3: LLM Generation & TTFT Metrics
    async def _step_llm():
        llm_data = metrics.get("llm", {})
        return {
            "model": llm_data.get("model", "gpt-4o-mini"),
            "ttft_ms": llm_data.get("ttft_ms", 0),
            "total_stream_ms": llm_data.get("total_stream_ms", 0),
            "tokens_estimated": llm_data.get("tokens_estimated", 0),
            "chars_streamed": llm_data.get("chars", len(answer))
        }
    await step.run("3_llm_generation", _step_llm)

    # Step 4: Chat Interaction Summary
    async def _step_summary():
        mem0_ms = metrics.get("mem0", {}).get("duration_ms", 0)
        qdrant_ms = metrics.get("qdrant", {}).get("duration_ms", 0)
        ttft_ms = metrics.get("llm", {}).get("ttft_ms", 0)
        total_pre_llm_ms = round(mem0_ms + qdrant_ms, 1)

        return {
            "user_id": user_id,
            "message": message,
            "answer_preview": answer[:250] + ("..." if len(answer) > 250 else ""),
            "total_pre_llm_latency_ms": total_pre_llm_ms,
            "ttft_ms": ttft_ms
        }
    await step.run("4_chat_summary", _step_summary)

    return {
        "status": "success",
        "user_id": user_id,
        "metrics": metrics
    }
