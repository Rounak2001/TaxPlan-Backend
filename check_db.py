import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')
django.setup()

from core_auth.models import ClientProfile
from consultants.models import ClientServiceRequest

print(f'ClientProfiles with consultant: {ClientProfile.objects.filter(assigned_consultant__isnull=False).count()}')
print(f'ServiceRequests with consultant: {ClientServiceRequest.objects.filter(assigned_consultant__isnull=False).count()}')
print(f'ServiceRequests pending: {ClientServiceRequest.objects.filter(status="pending").count()}')
print(f'Total ServiceRequests: {ClientServiceRequest.objects.count()}')
