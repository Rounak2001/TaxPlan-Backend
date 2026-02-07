"""
Fix broken consultant references after accidental deletion
Run this with: python manage.py shell < fix_broken_consultants.py
"""

from core_auth.models import ClientProfile
from consultants.models import ClientServiceRequest

print("Fixing broken consultant references...")

# Fix ClientProfile with deleted consultants
broken_profiles = ClientProfile.objects.filter(assigned_consultant__isnull=False)
fixed_profiles = 0

for profile in broken_profiles:
    try:
        # Try to access the consultant - if deleted, this will fail
        _ = profile.assigned_consultant.email
    except:
        # Consultant was deleted, set to None
        profile.assigned_consultant = None
        profile.save()
        fixed_profiles += 1
        print(f"Fixed ClientProfile for user: {profile.user.email}")

# Fix ClientServiceRequest with deleted consultants
broken_requests = ClientServiceRequest.objects.filter(assigned_consultant__isnull=False)
fixed_requests = 0

for request in broken_requests:
    try:
        # Try to access the consultant - if deleted, this will fail
        _ = request.assigned_consultant.full_name
    except:
        # Consultant was deleted, set to None and status to pending
        request.assigned_consultant = None
        request.status = 'pending'
        request.assigned_at = None
        request.save()
        fixed_requests += 1
        print(f"Fixed ClientServiceRequest #{request.id} for client: {request.client.email}")

print(f"\nDone! Fixed {fixed_profiles} ClientProfiles and {fixed_requests} ServiceRequests")
