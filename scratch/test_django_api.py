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

    print("=== TESTING DJANGO ENDPOINTS ===")
    
    # 1. Healthcheck
    print("\n1. Testing GET / (healthcheck):")
    res = client.get('/')
    print("Status:", res.status_code)
    print("Data:", res.json())
    assert res.status_code == 200, "Healthcheck failed!"

    # 2. List Topics
    print("\n2. Testing GET /api/student/topics:")
    res = client.get('/api/student/topics')
    print("Status:", res.status_code)
    print("Data:", res.json())
    assert res.status_code == 200, "List topics failed!"

    # 3. Check Cache (Invalid request)
    print("\n3. Testing GET /api/student/check-cache (missing params):")
    res = client.get('/api/student/check-cache')
    print("Status:", res.status_code)
    print("Data:", res.json())
    assert res.status_code == 400, "Check cache missing params validation failed!"

    # 4. Check Cache (Valid request)
    print("\n4. Testing GET /api/student/check-cache?subject=SDA&topic=ANOVA:")
    res = client.get('/api/student/check-cache?subject=SDA&topic=ANOVA')
    print("Status:", res.status_code)
    print("Data:", res.json())
    assert res.status_code == 200, "Check cache valid request failed!"

    # 5. List Sources
    print("\n5. Testing GET /api/student/sources?subject=SDA&topic=ANOVA:")
    res = client.get('/api/student/sources?subject=SDA&topic=ANOVA')
    print("Status:", res.status_code)
    print("Data:", res.json())
    assert res.status_code == 200, "List sources failed!"

    # 6. Generate Content (Off-topic warning check)
    print("\n6. Testing POST /api/student/generate (Off-topic warning):")
    payload = {
        "subject": "SDA",
        "topic": "ANOVA",
        "type": "notes",
        "notes_custom": "who won the 2022 FIFA World Cup?",
        "force_regenerate": True
    }
    res = client.post('/api/student/generate', json.dumps(payload), content_type='application/json')
    print("Status:", res.status_code)
    print("Data:", res.json())
    assert res.status_code == 200, "Off-topic generation check failed!"
    assert "Off-Topic Guidelines Warning" in res.json().get("content", ""), "Failed to intercept off-topic prompt!"

    print("\nAll Django endpoint tests completed successfully!")

if __name__ == '__main__':
    run_tests()
