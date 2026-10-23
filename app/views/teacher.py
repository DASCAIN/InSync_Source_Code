import logging
import hashlib
import uuid
from pathlib import Path
import inngest

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
import aiofiles

from app.services.document_processor import (
    SUPPORTED_EXTENSIONS,
)
from app.services.vector_store import register_topic
from config import get_settings

logger = logging.getLogger(__name__)

from app.services.inngest_client import inngest_client


def _tenant_id(request) -> str:
    """Return the caller's ingestion scope without a sync auth lookup."""
    return str(
        request.POST.get("tenant_id")
        or request.headers.get("X-Tenant-ID")
        or "default"
    )


async def _stream_upload(upload_file, file_path: Path) -> str:
    """Persist and hash an upload without loading the complete file in memory."""
    digest = hashlib.sha256()
    async with aiofiles.open(file_path, "wb") as destination:
        for chunk in upload_file.chunks():
            digest.update(chunk)
            await destination.write(chunk)
    return digest.hexdigest()

@csrf_exempt
async def upload_files(request) -> JsonResponse:
    """
    Upload one or more study material files for a given subject/topic.
    Files are saved and then an Inngest background task is triggered.
    """
    if request.method != "POST":
        return JsonResponse({"detail": "Method not allowed"}, status=405)

    subject = request.POST.get("subject")
    topic = request.POST.get("topic")
    files = request.FILES.getlist("files")

    if not subject or not topic or not files:
        return JsonResponse({"detail": "Missing subject, topic or files"}, status=400)

    settings = get_settings()
    max_size_bytes = settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024

    # Validate all files first
    for upload_file in files:
        filename = upload_file.name or ""
        ext = Path(filename).suffix.lower()
        if ext not in SUPPORTED_EXTENSIONS:
            return JsonResponse({
                "detail": (
                    f"Unsupported file type: '{ext}' for file '{filename}'. "
                    f"Supported types: {', '.join(sorted(SUPPORTED_EXTENSIONS))}"
                )
            }, status=400)

    # Create upload directory
    upload_dir = settings.UPLOADS_DIR / subject / topic
    upload_dir.mkdir(parents=True, exist_ok=True)

    files_processed = 0

    for upload_file in files:
        filename = Path(upload_file.name or "unknown_file").name
        immutable_dir = upload_dir / uuid.uuid4().hex
        immutable_dir.mkdir(parents=True, exist_ok=True)
        file_path = immutable_dir / filename
        logger.info("Saving uploaded file: %s", filename)

        try:
            # Check size before reading
            if upload_file.size > max_size_bytes:
                return JsonResponse({
                    "detail": (
                        f"File '{filename}' exceeds maximum size of "
                        f"{settings.MAX_UPLOAD_SIZE_MB}MB."
                    )
                }, status=400)

            file_sha256 = await _stream_upload(upload_file, file_path)

            # Trigger Inngest background task
            await inngest_client.send(
                inngest.Event(
                    name="app/document.uploaded",
                    data={
                        "subject": subject,
                        "topic": topic,
                        "file_path": str(file_path),
                        "filename": filename,
                        "tenant_id": _tenant_id(request),
                        "file_sha256": file_sha256,
                    }
                )
            )

            files_processed += 1

        except Exception as e:
            logger.error("Error saving file '%s': %s", filename, str(e))
            return JsonResponse({
                "detail": f"Error saving file '{filename}': {str(e)}"
            }, status=500)

    # Register the topic in metadata so frontend knows it exists immediately
    # (even though vectors are generating in background)
    register_topic(subject=subject, topic=topic)

    message = f"Successfully queued {files_processed} file(s) for background processing."
    logger.info(message)

    return JsonResponse({
        "success": True,
        "files_queued": files_processed,
        "message": message,
    }, status=202)


@csrf_exempt
async def upload_assignment_pdf(request) -> JsonResponse:
    """
    Upload up to 5 PDF files specifically to generate assignments from them.
    Triggers 'app/assignment.generate' event for each.
    """
    if request.method != "POST":
        return JsonResponse({"detail": "Method not allowed"}, status=405)

    subject = request.POST.get("subject", "Test Subject")
    topic = request.POST.get("topic", "Test Topic")
    batch_id = request.POST.get("batch_id", "C101")
    upload_files = request.FILES.getlist("file")

    if not upload_files:
        return JsonResponse({"detail": "Missing 'file'"}, status=400)
        
    if len(upload_files) > 5:
        return JsonResponse({"detail": "Maximum of 5 files allowed."}, status=400)

    settings = get_settings()
    max_size_bytes = settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024
    jobs = []
    
    import json
    
    for upload_file in upload_files:
        filename = Path(upload_file.name or "unknown_assignment.pdf").name
        ext = Path(filename).suffix.lower()
        if ext not in SUPPORTED_EXTENSIONS:
            return JsonResponse({"detail": f"Unsupported file type: '{ext}'"}, status=400)
    
        if upload_file.size > max_size_bytes:
            return JsonResponse({"detail": f"File '{filename}' exceeds {settings.MAX_UPLOAD_SIZE_MB}MB."}, status=400)
    
        job_id = uuid.uuid4().hex
        upload_dir = settings.UPLOADS_DIR / "assignments" / subject / topic / job_id
        upload_dir.mkdir(parents=True, exist_ok=True)
        file_path = upload_dir / filename

        jobs_dir = settings.UPLOADS_DIR / "jobs"
        jobs_dir.mkdir(parents=True, exist_ok=True)
        job_file = jobs_dir / f"{job_id}.json"
        
        with open(job_file, "w") as jf:
            json.dump({"status": "Uploading to server...", "progress": 5, "filename": filename}, jf)
    
        try:
            file_sha256 = await _stream_upload(upload_file, file_path)
    
            # Trigger Assignment Generation Inngest task
            await inngest_client.send(
                inngest.Event(
                    name="app/assignment.generate",
                    data={
                        "subject": subject,
                        "topic": topic,
                        "batch_id": batch_id,
                        "file_path": str(file_path),
                        "filename": filename,
                        "job_id": job_id,
                        "tenant_id": _tenant_id(request),
                        "file_sha256": file_sha256,
                    }
                )
            )
            
            jobs.append({"job_id": job_id, "filename": filename})
    
        except Exception as e:
            logger.error("Error saving assignment file '%s': %s", filename, str(e))
            with open(job_file, "w") as jf:
                json.dump({"status": "Failed to upload.", "progress": 100, "filename": filename, "error": str(e)}, jf)
            return JsonResponse({"detail": f"Error saving file '{filename}': {str(e)}"}, status=500)

    return JsonResponse({
        "success": True,
        "jobs": jobs,
        "message": f"Successfully queued {len(jobs)} file(s) for assignment generation.",
    }, status=202)

@csrf_exempt
def check_job_status(request, job_id) -> JsonResponse:
    if request.method != "GET":
        return JsonResponse({"detail": "Method not allowed"}, status=405)
    
    settings = get_settings()
    import json
    job_file = settings.UPLOADS_DIR / "jobs" / f"{job_id}.json"
    
    if not job_file.exists():
        return JsonResponse({"detail": "Job not found"}, status=404)
        
    try:
        with open(job_file, "r") as jf:
            data = json.load(jf)
        return JsonResponse(data, status=200)
    except Exception as e:
        return JsonResponse({"detail": f"Error reading job: {str(e)}"}, status=500)


@csrf_exempt
async def upload_guided_assignment(request) -> JsonResponse:
    """
    Upload up to 5 PDF files specifically for guided assignment generation.
    Triggers 'app/document.uploaded' event for each to just index the vectors.
    """
    if request.method != "POST":
        return JsonResponse({"detail": "Method not allowed"}, status=405)

    subject = request.POST.get("subject", "Test Subject")
    topic = request.POST.get("topic", "Test Topic")
    batch_id = request.POST.get("batch_id", "C101")
    upload_files = request.FILES.getlist("file")

    if not upload_files:
        return JsonResponse({"detail": "Missing 'file'"}, status=400)
        
    if len(upload_files) > 5:
        return JsonResponse({"detail": "Maximum of 5 files allowed."}, status=400)

    settings = get_settings()
    max_size_bytes = settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024
    
    session_id = uuid.uuid4().hex
    
    for upload_index, upload_file in enumerate(upload_files):
        filename = Path(upload_file.name or "unknown_assignment.pdf").name
        ext = Path(filename).suffix.lower()
        if ext not in SUPPORTED_EXTENSIONS:
            return JsonResponse({"detail": f"Unsupported file type: '{ext}'"}, status=400)
    
        if upload_file.size > max_size_bytes:
            return JsonResponse({"detail": f"File '{filename}' exceeds {settings.MAX_UPLOAD_SIZE_MB}MB."}, status=400)
    
        upload_dir = (
            settings.UPLOADS_DIR
            / "assignments"
            / subject
            / topic
            / session_id
            / str(upload_index)
        )
        upload_dir.mkdir(parents=True, exist_ok=True)
        file_path = upload_dir / filename
        
        try:
            file_sha256 = await _stream_upload(upload_file, file_path)
    
            # Trigger ONLY Document Uploaded Inngest task (just vectorization)
            await inngest_client.send(
                inngest.Event(
                    name="app/document.uploaded",
                    data={
                        "subject": subject,
                        "topic": topic,
                        "file_path": str(file_path),
                        "filename": filename,
                        "tenant_id": _tenant_id(request),
                        "file_sha256": file_sha256,
                    }
                )
            )
        except Exception as e:
            logger.error("Error saving guided assignment file '%s': %s", filename, str(e))
            return JsonResponse({"detail": f"Error saving file '{filename}': {str(e)}"}, status=500)

    # Store the session data in Django session (or cache) so we know subject/topic/batch for the chat
    from asgiref.sync import sync_to_async
    
    @sync_to_async(thread_sensitive=False)
    def set_session_data():
        request.session[f'guided_session_{session_id}'] = {
            "subject": subject,
            "topic": topic,
            "batch_id": batch_id,
            "files": [f.name for f in upload_files]
        }
        
    await set_session_data()
    
    return JsonResponse({
        "success": True,
        "session_id": session_id,
        "message": f"Successfully queued {len(upload_files)} file(s) for guided session.",
    }, status=202)
