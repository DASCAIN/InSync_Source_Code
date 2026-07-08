import logging
from pathlib import Path
import inngest

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
import aiofiles

from app.services.document_processor import (
    SUPPORTED_EXTENSIONS,
    chunk_text,
    process_file,
)
from app.services.vector_store import add_documents, register_topic
from config import get_settings

logger = logging.getLogger(__name__)

from app.services.inngest_client import inngest_client

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
        filename = upload_file.name or "unknown_file"
        file_path = upload_dir / filename
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

            content = upload_file.read()

            # Save file to disk
            async with aiofiles.open(file_path, "wb") as f:
                await f.write(content)

            # Trigger Inngest background task
            await inngest_client.send(
                inngest.Event(
                    name="app/document.uploaded",
                    data={
                        "subject": subject,
                        "topic": topic,
                        "file_path": str(file_path),
                        "filename": filename
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
    Upload a single PDF file specifically to generate an assignment from it.
    Triggers 'app/assignment.generate' event.
    """
    if request.method != "POST":
        return JsonResponse({"detail": "Method not allowed"}, status=405)

    subject = request.POST.get("subject", "Test Subject")
    topic = request.POST.get("topic", "Test Topic")
    batch_id = request.POST.get("batch_id", "C101")
    upload_file = request.FILES.get("file")

    if not upload_file:
        return JsonResponse({"detail": "Missing 'file'"}, status=400)

    settings = get_settings()
    max_size_bytes = settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024

    filename = upload_file.name or "unknown_assignment.pdf"
    ext = Path(filename).suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        return JsonResponse({"detail": f"Unsupported file type: '{ext}'"}, status=400)

    if upload_file.size > max_size_bytes:
        return JsonResponse({"detail": f"File '{filename}' exceeds {settings.MAX_UPLOAD_SIZE_MB}MB."}, status=400)

    upload_dir = settings.UPLOADS_DIR / "assignments" / subject / topic
    upload_dir.mkdir(parents=True, exist_ok=True)
    file_path = upload_dir / filename
    
    import uuid
    import json
    job_id = uuid.uuid4().hex
    jobs_dir = settings.UPLOADS_DIR / "jobs"
    jobs_dir.mkdir(parents=True, exist_ok=True)
    job_file = jobs_dir / f"{job_id}.json"
    
    with open(job_file, "w") as jf:
        json.dump({"status": "Uploading to server...", "progress": 5, "filename": filename}, jf)

    try:
        content = upload_file.read()
        async with aiofiles.open(file_path, "wb") as f:
            await f.write(content)

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
                    "job_id": job_id
                }
            )
        )

        return JsonResponse({
            "success": True,
            "job_id": job_id,
            "message": f"Successfully queued '{filename}' for assignment generation.",
        }, status=202)

    except Exception as e:
        logger.error("Error saving assignment file '%s': %s", filename, str(e))
        with open(job_file, "w") as jf:
            json.dump({"status": "Failed to upload.", "progress": 100, "filename": filename, "error": str(e)}, jf)
        return JsonResponse({"detail": f"Error saving file: {str(e)}"}, status=500)

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

