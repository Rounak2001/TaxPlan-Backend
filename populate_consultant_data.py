import os
import django
import random
from datetime import timedelta
from django.utils import timezone

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')
django.setup()

from django.contrib.auth import get_user_model
from core_auth.models import ClientProfile
from consultants.models import (
    ConsultantServiceProfile, ServiceCategory, Service, 
    ConsultantServiceExpertise, ClientServiceRequest
)
from activity_timeline.models import Activity
from exotel_calls.models import CallLog

User = get_user_model()

def populate_data():
    print("Populating data for Consultant Dashboard...")
    
    # 1. Get or Create Consultant
    # Try to find 'riteshdhumak95' or the first consultant
    consultant_user = User.objects.filter(username='riteshdhumak95', role=User.CONSULTANT).first()
    if not consultant_user:
        consultant_user = User.objects.filter(role=User.CONSULTANT).first()
        
    if not consultant_user:
        print("No consultant found! Creating one...")
        consultant_user = User.objects.create_user(
            username='consultant_demo',
            email='consultant@demo.com',
            password='password123',
            role=User.CONSULTANT,
            first_name='Demo',
            last_name='Consultant',
            is_phone_verified=True,
            phone_number='9876543210'
        )
    
    print(f"Targeting Consultant: {consultant_user.username} ({consultant_user.email})")
    
    # Ensure Profile
    profile, created = ConsultantServiceProfile.objects.get_or_create(
        user=consultant_user,
        defaults={
            'qualification': 'CA, CPA',
            'experience_years': 10,
            'certifications': 'Certified Public Accountant',
            'consultation_fee': 1500.00,
            'max_concurrent_clients': 20
        }
    )
    
    # 2. Ensure Services
    cat_tax, _ = ServiceCategory.objects.get_or_create(name="Income Tax")
    cat_gst, _ = ServiceCategory.objects.get_or_create(name="GST Services")
    
    s1, _ = Service.objects.get_or_create(
        category=cat_tax, title="ITR Filing - Salaried",
        defaults={'price': 999, 'tat': '2 Days', 'documents_required': 'Form 16, PAN'}
    )
    s2, _ = Service.objects.get_or_create(
        category=cat_gst, title="GST Registration",
        defaults={'price': 1499, 'tat': '5 Days', 'documents_required': 'PAN, Aadhaar, Rent Agreement'}
    )
    
    # Assign Expertise
    ConsultantServiceExpertise.objects.get_or_create(consultant=profile, service=s1)
    ConsultantServiceExpertise.objects.get_or_create(consultant=profile, service=s2)
    
    # 3. Create Clients
    clients = []
    for i in range(1, 4):
        username = f'client_demo_{i}'
        email = f'client{i}@demo.com'
        client, created = User.objects.get_or_create(
            username=username,
            defaults={
                'email': email,
                'role': User.CLIENT,
                'first_name': f'Client',
                'last_name': f'{i}',
                'phone_number': f'900000000{i}'
            }
        )
        if created:
            client.set_password('password123')
            client.save()
            ClientProfile.objects.create(user=client)
        clients.append(client)
        
    # 4. Create Service Requests
    statuses = ['pending', 'doc_pending', 'wip', 'completed']
    
    for client in clients:
        # Check existing requests
        if ClientServiceRequest.objects.filter(client=client, assigned_consultant=profile).exists():
            continue
            
        req = ClientServiceRequest.objects.create(
            client=client,
            service=random.choice([s1, s2]),
            status=random.choice(statuses),
            assigned_consultant=profile,
            assigned_at=timezone.now(),
            notes="Demo request created via script"
        )
        print(f"Created request for {client.username}: {req.service.title} ({req.status})")
        
        # Update load
        if req.status != 'completed':
            profile.current_client_count = ClientServiceRequest.objects.filter(
                assigned_consultant=profile
            ).exclude(status__in=['completed', 'cancelled']).count()
            profile.save()

    # 5. Artificial Activities
    Activity.objects.create(
        actor=clients[0],
        target_user=clients[0],
        activity_type='document_upload',
        title='Uploaded Form 16',
        description='Client uploaded Form 16 for FY23-24',
        content_object=clients[0] # Just linking to user for simplicity
    )
    
    Activity.objects.create(
        actor=consultant_user,
        target_user=clients[1],
        activity_type='call_made',
        title=f'Call with {clients[1].get_full_name()}',
        description='Duration: 5m 23s',
        metadata={'duration': 323, 'status': 'completed'}
    )

    # 6. Artificial Call Logs
    CallLog.objects.create(
        caller=consultant_user,
        callee=clients[1],
        status='completed',
        duration=323,
        created_at=timezone.now() - timedelta(hours=2),
        outcome='connected',
        notes='Discussed document requirements'
    )
    
    print("Data population complete!")
    print(f"Consultant {consultant_user.username} now has:")
    print(f"- {profile.current_client_count} active clients")
    print(f"- {ClientServiceRequest.objects.filter(assigned_consultant=profile).count()} total requests")

if __name__ == '__main__':
    populate_data()
