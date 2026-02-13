
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')
django.setup()

from consultants.models import ClientServiceRequest
from document_vault.models import Document

# Get the most recent service request that is stuck in doc phases
req = ClientServiceRequest.objects.exclude(status__in=['completed', 'cancelled', 'wip', 'final_review', 'filed', 'revision_pending']).order_by('-created_at').first()

if not req:
    print("No active service request found.")
else:
    print(f"Checking Request: {req.service.title} for {req.client.email}")
    print(f"Current Status: {req.status}")
    
    all_docs = Document.objects.filter(client=req.client)
    print(f"Total documents for client: {all_docs.count()}")
    for d in all_docs:
        print(f" - [{d.id}] {d.title}: status={d.status}, desc='{d.description}'")
    
    docs = Document.objects.filter(client=req.client, description__icontains=req.service.title)
    print(f"Found {docs.count()} documents linked to this service title.")
    
    for doc in docs:
        print(f" - {doc.title}: status={doc.status}, desc='{doc.description}'")
        
    all_verified = docs.count() > 0 and not docs.exclude(status='VERIFIED').exists()
    print(f"All verified logically: {all_verified}")
    
    if all_verified and req.status != 'wip':
        print("FIXING: Status is not WIP but all docs are verified. Moving to WIP now.")
        req.status = 'wip'
        req.save()
        print("Success.")
