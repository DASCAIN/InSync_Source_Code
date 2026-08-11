"""
URL configuration for insync_backend project.
Merges: voice_tutor app routes + RAG API routes.
"""
import os
from django.contrib import admin
from django.urls import path, include, re_path
from django.views.static import serve
from django.http import JsonResponse
from django.conf import settings
from django.conf.urls.static import static
from config import get_settings

# Import RAG API views
from app.views.teacher import upload_files, upload_assignment_pdf, upload_guided_assignment, check_job_status
from app.views.student import (
    list_topics,
    check_cache,
    generate_content,
    list_sources,
    save_cache,
)

# Inngest Setup
from inngest.django import serve as inngest_serve
from app.services.inngest_client import inngest_client
from app.services.inngest_functions import rag_ingest_pdf, rag_query_pdf, rag_generate_assignment, rag_tutor_chat


async def healthcheck(request):
    return JsonResponse({"message": "InSync RAG API (Django) is running"})


rag_settings = get_settings()

urlpatterns = [
    # Django admin
    path('admin/', admin.site.urls),

    # Voice tutor app (main app routes — dashboards, auth, chat, voice)
    path('', include('voice_tutor.urls')),

    # Inngest Background Tasks API
    # inngest_serve(inngest_client, [rag_ingest_pdf, rag_query_pdf, rag_generate_assignment, rag_tutor_chat]),
    # --- NEW CODE ---
    inngest_serve(  
        client=inngest_client,
        functions=[rag_ingest_pdf, rag_query_pdf, rag_generate_assignment, rag_tutor_chat],
        serve_origin=None if os.getenv("INNGEST_DEV", "0") == "1" else os.getenv("INNGEST_SERVE_ORIGIN", "https://www.dascain.com"),
    ),

    # RAG API endpoints
    path('api/healthcheck', healthcheck, name='rag_healthcheck'),
    path('api/teacher/upload', upload_files, name='upload_files'),
    path('api/teacher/upload-assignment', upload_assignment_pdf, name='upload_assignment_pdf'),
    path('api/teacher/upload-guided-assignment', upload_guided_assignment, name='upload_guided_assignment'),
    path('api/teacher/upload-status/<str:job_id>', check_job_status, name='check_job_status'),
    path('api/student/topics', list_topics, name='list_topics'),
    path('api/student/check-cache', check_cache, name='check_cache'),
    path('api/student/generate', generate_content, name='generate_content'),
    path('api/student/sources', list_sources, name='list_sources'),
    path('api/student/save-cache', save_cache, name='save_cache'),

    # Serve RAG data files (videos, generated content)
    re_path(r'^videos/(?P<path>.*)$', serve, {'document_root': str(rag_settings.VIDEOS_DIR)}),
    re_path(r'^rag-static/(?P<path>.*)$', serve, {'document_root': str(rag_settings.DATA_DIR)}),
]

if settings.DEBUG:
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
