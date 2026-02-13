
import os
import django
from django.core.files.base import ContentFile

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')
django.setup()

from consultants.models import ClientServiceRequest
from document_vault.models import SharedReport
from django.contrib.auth import get_user_model

User = get_user_model()

def verify_flow():
    # Find a request in WIP status
    req = ClientServiceRequest.objects.filter(status='wip').first()
    
    if not req:
        # Fallback: create one or move one to WIP
        req = ClientServiceRequest.objects.exclude(status='completed').first()
        if not req:
            print("No service request found to test.")
            return
        req.status = 'wip'
        req.save()
        print(f"Using Request: {req.service.title} (Forced to WIP for test)")
    else:
        print(f"Found WIP Request: {req.service.title} for {req.client.email}")

    consultant = req.assigned_consultant.user if req.assigned_consultant else None
    if not consultant:
        # Ensure consultant is assigned for the test
        consultant = User.objects.filter(role='CONSULTANT').first()
        from consultants.models import ConsultantServiceProfile
        profile = ConsultantServiceProfile.objects.get(user=consultant)
        req.assigned_consultant = profile
        req.save()
        print(f"Assigned consultant {consultant.email} for test.")

    print(f"Current Status: {req.status}")
    
    # Simulate report upload
    print("Uploading report...")
    report = SharedReport.objects.create(
        consultant=consultant,
        client=req.client,
        title="Final Tax Computation Report",
        description="Automated test report",
        file=ContentFile(b"test content", name="test_report.pdf"),
        report_type='TAX'
    )
    
    # Re-fetch request
    req.refresh_from_db()
    print(f"New Status: {req.status}")
    
    if req.status == 'final_review':
        print("✅ SUCCESS: Service moved to Final Review automatically.")
    else:
        print(f"❌ FAILURE: Service status is {req.status}, expected final_review.")

if __name__ == "__main__":
    verify_flow()
