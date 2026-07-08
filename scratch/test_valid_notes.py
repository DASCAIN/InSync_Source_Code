import os
import sys
import json
from pathlib import Path

# Add backend directory to python path
backend_dir = Path("/Users/tatparramawat/Downloads/RAG FOR INSYNC/backend")
sys.path.insert(0, str(backend_dir))

# Force load .env
from dotenv import load_dotenv
load_dotenv(dotenv_path=backend_dir / ".env")

import django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'insync_backend.settings')
django.setup()

from django.test import Client

def run_tests():
    client = Client()

    print("=== TESTING VALID ON-TOPIC GENERATION ===")
    payload = {
        "subject": "SDA",
        "topic": "ANOVA",
        "type": "notes",
        "notes_custom": "focus on F-distribution assumptions",
        "force_regenerate": True
    }
    res = client.post('/api/student/generate', json.dumps(payload), content_type='application/json')
    print("Status:", res.status_code)
    try:
        data = res.json()
        print("Data preview:", data.get("content", "")[:500])
        print("Infographic URL:", data.get("content", "").split("infographic.png"))
    except Exception as e:
        print("Failed to parse JSON response:", e)
        print("Raw Content:", res.content[:500])

if __name__ == '__main__':
    run_tests()
