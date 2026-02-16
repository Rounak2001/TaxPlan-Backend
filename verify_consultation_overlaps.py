import os
import django
import sys

# Setup Django environment
sys.path.append('/home/rounak-patel/Desktop/web_coding/saas/backend')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')
django.setup()

from consultations.serializers import WeeklyAvailabilitySerializer, DateOverrideSerializer
from consultations.models import WeeklyAvailability, DateOverride
from django.contrib.auth import get_user_model
from rest_framework.exceptions import ValidationError
from datetime import time, date

User = get_user_model()

def test_overlaps():
    # Get or create a test consultant
    consultant, _ = User.objects.get_or_create(username='test_consultant', role='CONSULTANT')
    
    print(f"Testing for consultant: {consultant.username}")

    # Mock request object for serializer context
    class MockRequest:
        def __init__(self, user):
            self.user = user

    context = {'request': MockRequest(consultant)}

    # 1. Test WeeklyAvailability Overlap
    print("\n--- Testing WeeklyAvailability Overlaps ---")
    data1 = {
        'day_of_week': 1, # Monday
        'start_time': '09:00:00',
        'end_time': '12:00:00'
    }
    
    # Create first slot
    serializer1 = WeeklyAvailabilitySerializer(data=data1, context=context)
    if serializer1.is_valid():
        serializer1.save()
        print("Initial slot created: 09:00 - 12:00")
    else:
        print(f"Failed to create initial slot: {serializer1.errors}")

    # Test perfect overlap
    data2 = {
        'day_of_week': 1,
        'start_time': '09:00:00',
        'end_time': '12:00:00'
    }
    serializer2 = WeeklyAvailabilitySerializer(data=data2, context=context)
    try:
        serializer2.is_valid(raise_exception=True)
        print("FAILED: Perfect overlap allowed")
    except ValidationError as e:
        print(f"SUCCESS: Perfect overlap caught: {e}")

    # Test partial overlap (11:00 - 13:00)
    data3 = {
        'day_of_week': 1,
        'start_time': '11:00:00',
        'end_time': '13:00:00'
    }
    serializer3 = WeeklyAvailabilitySerializer(data=data3, context=context)
    try:
        serializer3.is_valid(raise_exception=True)
        print("FAILED: Partial overlap allowed")
    except ValidationError as e:
        print(f"SUCCESS: Partial overlap caught: {e}")

    # Test non-overlapping slot (12:00 - 15:00)
    data4 = {
        'day_of_week': 1,
        'start_time': '12:00:00',
        'end_time': '15:00:00'
    }
    serializer4 = WeeklyAvailabilitySerializer(data=data4, context=context)
    if serializer4.is_valid():
        print("SUCCESS: Non-overlapping slot (12:00-15:00) allowed")
    else:
        print(f"FAILED: Non-overlapping slot rejected: {serializer4.errors}")

    # 2. Test DateOverride Overlap
    print("\n--- Testing DateOverride Overlaps ---")
    test_date = date.today().strftime('%Y-%m-%d')
    data5 = {
        'date': test_date,
        'is_unavailable': False,
        'start_time': '10:00:00',
        'end_time': '11:00:00'
    }
    serializer5 = DateOverrideSerializer(data=data5, context=context)
    if serializer5.is_valid():
        serializer5.save()
        print(f"Initial override created for {test_date}: 10:00 - 11:00")
    
    data6 = {
        'date': test_date,
        'is_unavailable': False,
        'start_time': '10:30:00',
        'end_time': '11:30:00'
    }
    serializer6 = DateOverrideSerializer(data=data6, context=context)
    try:
        serializer6.is_valid(raise_exception=True)
        print("FAILED: DateOverride overlap allowed")
    except ValidationError as e:
        print(f"SUCCESS: DateOverride overlap caught: {e}")

    # 3. Test PATCH (Partial Update)
    print("\n--- Testing PATCH (Partial Update) ---")
    data_patch_base = {
        'day_of_week': 3,
        'start_time': '16:00:00',
        'end_time': '17:00:00'
    }
    serializer_base = WeeklyAvailabilitySerializer(data=data_patch_base, context=context)
    if serializer_base.is_valid():
        temp_slot = serializer_base.save()
        print("Fresh slot created for PATCH test: 16:00 - 17:00")
        
        # Try to update ONLY the day_of_week
        patch_data = {'day_of_week': 4}
        serializer_patch = WeeklyAvailabilitySerializer(instance=temp_slot, data=patch_data, context=context, partial=True)
        try:
            if serializer_patch.is_valid(raise_exception=True):
                serializer_patch.save()
                print("SUCCESS: PATCH update (changing day only) succeeded without TypeError")
        except Exception as e:
            print(f"FAILED: PATCH update failed: {e}")
    else:
        # If it already exists, fetch it
        temp_slot = WeeklyAvailability.objects.filter(consultant=consultant, day_of_week=3, start_time='16:00:00').first()
        if temp_slot:
            print("Using existing slot for PATCH test")
            patch_data = {'day_of_week': 4}
            serializer_patch = WeeklyAvailabilitySerializer(instance=temp_slot, data=patch_data, context=context, partial=True)
            if serializer_patch.is_valid():
                serializer_patch.save()
                print("SUCCESS: PATCH update (changing day only) succeeded without TypeError")
        else:
            print(f"FAILED: Could not create or find slot for PATCH test: {serializer_base.errors}")

    # Cleanup (Optional)
    # WeeklyAvailability.objects.filter(consultant=consultant).delete()
    # DateOverride.objects.filter(consultant=consultant).delete()

if __name__ == "__main__":
    test_overlaps()
