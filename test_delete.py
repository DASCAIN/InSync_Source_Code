import os, django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'insync.settings')
django.setup()

from app.services.vector_store import delete_documents_by_source
print("Ready")
