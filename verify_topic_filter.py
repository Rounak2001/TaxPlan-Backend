import os
import django
from datetime import date

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings")
django.setup()

from django.contrib.auth import get_user_model
from consultations.models import Topic

User = get_user_model()

def verify_filtering():
    # 1. Create a test consultant
    consultant, created = User.objects.get_or_create(
        username="test_consultant_filter",
        email="test_filter@example.com",
        role='CONSULTANT'
    )
    if created:
        consultant.set_password("password")
        consultant.save()
        print("Created test consultant.")

    # 2. Create a test topic
    topic, created = Topic.objects.get_or_create(name="Test Filtering Topic")
    print(f"Topic '{topic.name}' (ID: {topic.id}) created/retrieved.")

    # 3. Assign consultant to topic
    topic.consultants.add(consultant)
    print(f"Assigned consultant '{consultant.username}' to topic.")

    # 4. Verify filtering
    # Case A: Filter by this topic - Consultant SHOULD be present
    filtered_consultants = User.objects.filter(role='CONSULTANT', topics__id=topic.id)
    if consultant in filtered_consultants:
        print("[PASS] Consultant found when filtering by correct topic.")
    else:
        print("[FAIL] Consultant NOT found when filtering by correct topic.")

    # Case B: Filter by a different topic - Consultant SHOULD NOT be present
    other_topic, _ = Topic.objects.get_or_create(name="Other Topic")
    filtered_consultants_wrong = User.objects.filter(role='CONSULTANT', topics__id=other_topic.id)
    
    if consultant not in filtered_consultants_wrong:
        print("[PASS] Consultant NOT found when filtering by other topic.")
    else:
        print("[FAIL] Consultant FOUND when filtering by other topic (should be excluded).")

if __name__ == "__main__":
    verify_filtering()
