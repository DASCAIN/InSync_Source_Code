import json
import logging
from pathlib import Path
import re


from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from app.services.generator import (
    generate_assignment,
    generate_notes,
    generate_video_explanation,
)
from app.services.vector_store import get_all_topics, _sanitize_name
from config import get_settings

logger = logging.getLogger(__name__)


def parse_and_run_infographic(content: str, output_path: Path) -> str:
    """
    Extracts [INFOGRAPHIC_SCRIPT] block, executes it headlessly,
    saves the image, and removes the script block.
    """
    pattern = re.compile(r"\[INFOGRAPHIC_SCRIPT\]\s*```python\s*(.*?)\s*```", re.DOTALL | re.IGNORECASE)
    match = pattern.search(content)
    
    if match:
        script_content = match.group(1).strip()
        logger.info("Found infographic script. Running headlessly...")
        
        # Ensure target directory exists
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        try:
            import matplotlib
            matplotlib.use('Agg')
            import matplotlib.pyplot as plt
            import numpy as np
            
            # Clear previous plots
            plt.close('all')
            
            # Execute python script block in a controlled local scope
            local_vars = {
                "OUTPUT_PATH": str(output_path),
            }
            global_vars = {
                "plt": plt,
                "np": np,
                "__builtins__": __builtins__,
            }
            exec(script_content, global_vars, local_vars)
            logger.info("Successfully executed infographic script and saved to %s", output_path)
        except ImportError:
            logger.error("Matplotlib is not available. Skipping infographic generation.")
        except Exception as e:
            logger.error("Failed to run infographic script: %s", str(e), exc_info=True)
            # Ensure cleanup
            try:
                import matplotlib.pyplot as plt
                plt.close('all')
            except Exception:
                pass
        
        # Remove the infographic script from the content
        content = content[:match.start()] + content[match.end():]
        content = content.strip()
        
    return content


def _get_versions(cache_dir: Path, content_type: str) -> list:
    type_dir = cache_dir / content_type
    versions = []
    if type_dir.exists() and type_dir.is_dir():
        for f in type_dir.glob("v*.json"):
            try:
                with open(f, "r", encoding="utf-8") as file_obj:
                    data = json.load(file_obj)
                    params = data.get("parameters", {})
                    if content_type == "notes":
                        focus_val = params.get("notes_focus")
                        custom_val = params.get("notes_custom")
                    elif content_type == "assignment":
                        focus_val = params.get("assignment_focus")
                        custom_val = params.get("assignment_custom")
                    else:
                        focus_val = params.get("video_focus")
                        custom_val = params.get("video_custom")
                    versions.append({
                        "version": data.get("version", 1),
                        "timestamp": data.get("timestamp", ""),
                        "focus": focus_val,
                        "custom": custom_val,
                    })
            except Exception as e:
                logger.error("Failed to read version file %s: %s", f.name, str(e))
    versions.sort(key=lambda x: x["version"])
    return versions


async def list_topics(request) -> JsonResponse:
    """List all available subjects and their topics."""
    if request.method != "GET":
        return JsonResponse({"detail": "Method not allowed"}, status=405)
    subjects = get_all_topics()
    return JsonResponse({"subjects": subjects})


async def check_cache(request) -> JsonResponse:
    """Check check-cache history for subject and topic."""
    if request.method != "GET":
        return JsonResponse({"detail": "Method not allowed"}, status=405)
    subject = request.GET.get("subject")
    topic = request.GET.get("topic")
    if not subject or not topic:
        return JsonResponse({"detail": "Missing subject or topic parameters"}, status=400)

    settings = get_settings()
    subject_sanit = _sanitize_name(subject)
    topic_sanit = _sanitize_name(topic)
    cache_dir = settings.GENERATED_DIR / subject_sanit / topic_sanit

    notes_versions = _get_versions(cache_dir, "notes")
    assignment_versions = _get_versions(cache_dir, "assignment")
    video_versions = _get_versions(cache_dir, "video")

    return JsonResponse({
        "notes_cached": len(notes_versions) > 0,
        "notes_versions": notes_versions,
        "assignment_cached": len(assignment_versions) > 0,
        "assignment_versions": assignment_versions,
        "video_cached": len(video_versions) > 0,
        "video_versions": video_versions,
    })


@csrf_exempt
async def generate_content(request) -> JsonResponse:
    """Generate educational content and save to versioned cache."""
    if request.method != "POST":
        return JsonResponse({"detail": "Method not allowed"}, status=405)
    
    try:
        data = json.loads(request.body)
        subject = data.get("subject")
        topic = data.get("topic")
        content_type = data.get("type")
        notes_focus = data.get("notes_focus")
        notes_custom = data.get("notes_custom")
        assignment_focus = data.get("assignment_focus")
        assignment_custom = data.get("assignment_custom")
        video_focus = data.get("video_focus")
        video_custom = data.get("video_custom")
        force_regenerate = data.get("force_regenerate", False)
        version = data.get("version")
    except Exception:
        return JsonResponse({"detail": "Invalid JSON body"}, status=400)

    if not subject or not topic or not content_type:
        return JsonResponse({"detail": "Missing subject, topic or type"}, status=400)

    settings = get_settings()
    subject_sanit = _sanitize_name(subject)
    topic_sanit = _sanitize_name(topic)
    cache_dir = settings.GENERATED_DIR / subject_sanit / topic_sanit
    type_dir = cache_dir / content_type

    # 1. If force_regenerate is False, load cache
    if not force_regenerate:
        target_version = version
        if target_version is None:
            # Load the latest version
            versions = _get_versions(cache_dir, content_type)
            if versions:
                target_version = versions[-1]["version"]
        
        if target_version is not None:
            cache_file = type_dir / f"v{target_version}.json"
            if cache_file.exists():
                try:
                    with open(cache_file, "r", encoding="utf-8") as f:
                        cached_data = json.load(f)
                        logger.info("Returning cached version %s of %s for %s/%s", target_version, content_type, subject, topic)
                        return JsonResponse({
                            "content": cached_data.get("content", ""),
                            "type": content_type,
                            "subject": subject,
                            "topic": topic,
                            "video_url": cached_data.get("video_url"),
                            "version": cached_data.get("version", target_version)
                        })
                except Exception as e:
                    logger.warning("Version file exists but failed to read: %s. Re-generating...", str(e))

    # 2. Re-generate content using LLM generator
    generators = {
        "notes": generate_notes,
        "assignment": generate_assignment,
        "video": generate_video_explanation,
    }

    generator_fn = generators.get(content_type)
    if generator_fn is None:
        return JsonResponse({
            "detail": f"Invalid content type: '{content_type}'. Valid types: {', '.join(generators.keys())}"
        }, status=400)

    try:
        logger.info(
            "Generating %s for %s/%s (force=%s)",
            content_type,
            subject,
            topic,
            force_regenerate,
        )
        
        # Prepare parameters depending on type
        kwargs = {
            "subject": subject,
            "topic": topic,
        }
        if content_type == "notes":
            kwargs["focus"] = notes_focus
            kwargs["custom_instructions"] = notes_custom
        elif content_type == "assignment":
            kwargs["focus"] = assignment_focus
            kwargs["custom_instructions"] = assignment_custom
        elif content_type == "video":
            kwargs["focus"] = video_focus
            kwargs["custom_instructions"] = video_custom

        content = await generator_fn(**kwargs)
    except Exception as e:
        logger.error(
            "Generation failed for %s/%s (%s): %s",
            subject,
            topic,
            content_type,
            str(e),
        )
        err_msg = str(e)
        if "429" in err_msg or "RESOURCE_EXHAUSTED" in err_msg or "Quota" in err_msg or "quota" in err_msg.lower():
            return JsonResponse({
                "detail": "Google Gemini API Quota Exceeded. Please try again later or provide a key with available quota."
            }, status=429)
        return JsonResponse({
            "detail": f"Content generation failed: {str(e)}"
        }, status=500)

    video_url = None
    if content_type == "video":
        video_url, content = content

    # 3. Save as new version
    import datetime
    try:
        type_dir.mkdir(parents=True, exist_ok=True)
        # Determine version number
        existing_versions = _get_versions(cache_dir, content_type)
        new_version = (existing_versions[-1]["version"] + 1) if existing_versions else 1
        
        # Run infographic script and replace placeholder if notes type
        if content_type == "notes":
            infographic_filename = f"v{new_version}_infographic.png"
            infographic_path = type_dir / infographic_filename
            content = parse_and_run_infographic(content, infographic_path)
            static_url = f"http://localhost:8001/static/generated_content/{subject_sanit}/{topic_sanit}/notes/{infographic_filename}"
            content = content.replace("infographic.png", static_url)

        cache_file = type_dir / f"v{new_version}.json"
        cache_data = {
            "version": new_version,
            "timestamp": datetime.datetime.utcnow().isoformat(),
            "content": content,
            "video_url": video_url,
            "parameters": {
                "notes_focus": notes_focus,
                "notes_custom": notes_custom,
                "assignment_focus": assignment_focus,
                "assignment_custom": assignment_custom,
                "video_focus": video_focus,
                "video_custom": video_custom,
            }
        }
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump(cache_data, f, ensure_ascii=False, indent=2)
        logger.info("Saved version %s of %s generated content to cache: %s", new_version, content_type, cache_file)
        
        return JsonResponse({
            "content": content,
            "type": content_type,
            "subject": subject,
            "topic": topic,
            "video_url": video_url,
            "version": new_version
        })
    except Exception as e:
        logger.error("Failed to write generated content to cache: %s", str(e))
        return JsonResponse({
            "detail": f"Failed to write content to cache: {str(e)}"
        }, status=500)


async def list_sources(request) -> JsonResponse:
    """List uploaded source documents."""
    if request.method != "GET":
        return JsonResponse({"detail": "Method not allowed"}, status=405)
    subject = request.GET.get("subject")
    topic = request.GET.get("topic")
    if not subject or not topic:
        return JsonResponse({"detail": "Missing subject or topic parameters"}, status=400)

    settings = get_settings()
    upload_dir = settings.UPLOADS_DIR / subject / topic
    
    sources = []
    if upload_dir.exists():
        for item in upload_dir.iterdir():
            if item.is_file() and not item.name.startswith("."):
                try:
                    stat = item.stat()
                    sources.append({
                        "name": item.name,
                        "size": stat.st_size,
                        "ext": item.suffix.lower(),
                    })
                except Exception as e:
                    logger.error("Error reading file stat for %s: %s", item.name, str(e))
                    
    return JsonResponse({"sources": sources})


@csrf_exempt
async def save_cache(request) -> JsonResponse:
    """Save edited content to cache file."""
    if request.method != "POST":
        return JsonResponse({"detail": "Method not allowed"}, status=405)
    
    try:
        data = json.loads(request.body)
        subject = data.get("subject")
        topic = data.get("topic")
        content_type = data.get("type")
        version = data.get("version")
        content = data.get("content")
    except Exception:
        return JsonResponse({"detail": "Invalid JSON body"}, status=400)

    if not subject or not topic or not content_type or version is None or content is None:
        return JsonResponse({"detail": "Missing required fields"}, status=400)

    settings = get_settings()
    subject_sanit = _sanitize_name(subject)
    topic_sanit = _sanitize_name(topic)
    cache_dir = settings.GENERATED_DIR / subject_sanit / topic_sanit
    type_dir = cache_dir / content_type
    cache_file = type_dir / f"v{version}.json"

    parameters = {}
    video_url = None
    timestamp = None

    if cache_file.exists():
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                existing_data = json.load(f)
                parameters = existing_data.get("parameters", {})
                video_url = existing_data.get("video_url")
                timestamp = existing_data.get("timestamp")
        except Exception as e:
            logger.warning("Failed to load existing version file for merging: %s", str(e))

    content = content
    if content_type == "notes":
        infographic_filename = f"v{version}_infographic.png"
        infographic_path = type_dir / infographic_filename
        content = parse_and_run_infographic(content, infographic_path)
        static_url = f"http://localhost:8001/static/generated_content/{subject_sanit}/{topic_sanit}/notes/{infographic_filename}"
        content = content.replace("infographic.png", static_url)

    try:
        import datetime
        type_dir.mkdir(parents=True, exist_ok=True)
        cache_data = {
            "version": version,
            "timestamp": timestamp or datetime.datetime.utcnow().isoformat(),
            "content": content,
            "video_url": video_url,
            "parameters": parameters,
        }
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump(cache_data, f, ensure_ascii=False, indent=2)
        logger.info("Saved edited content for version %s in cache: %s", version, cache_file)
        return JsonResponse({"success": True, "message": f"{content_type.capitalize()} version {version} content saved successfully."})
    except Exception as e:
        logger.error("Failed to write edited content to cache: %s", str(e))
        return JsonResponse({"detail": f"Failed to save content to cache: {str(e)}"}, status=500)
