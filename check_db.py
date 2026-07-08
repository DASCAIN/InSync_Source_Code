import os
import django
import sys

# Setup Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'insync_backend.settings')
django.setup()

from voice_tutor.models import UserProfile, Cohort, Assignment

print("--- Checking User Profile ---")
# Find user 'kedhar jadhave'
users = UserProfile.objects.filter(name__icontains="kedhar")
if not users:
    print("User 'kedhar' not found.")
else:
    for u in users:
        print(f"Found User: {u.name} (Role: {u.role.role_code})")
        # Find their cohorts
        cohorts = Cohort.objects.filter(students=u)
        print(f"  Cohorts: {[c.code for c in cohorts]}")

print("\n--- Checking Assignments ---")
assignments = Assignment.objects.all().order_by('-created_at')[:5]
for a in assignments:
    print(f"Assignment: {a.title} (Code: {a.code}, Topic: {a.topic.name})")
    # Which cohorts is this assignment linked to?
    linked_cohorts = a.cohorts.all()
    print(f"  Linked to cohorts: {[c.code for c in linked_cohorts]}")
    # How many questions?
    print(f"  Questions: {a.questions.count()}")
