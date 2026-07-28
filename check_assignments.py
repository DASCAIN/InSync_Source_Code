import os, django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'insync.settings')
django.setup()

from voice_tutor.models import Assignment
for a in Assignment.objects.all():
    print(f"Code: {a.code}, Title: {a.title}, PDF: {a.source_pdf_path}")
