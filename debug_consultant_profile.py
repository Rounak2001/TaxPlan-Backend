import os
import django
from django.conf import settings

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')
django.setup()

from core_auth.models import User
from consultants.models import ConsultantServiceProfile

def print_consultant_details():
    consultants = User.objects.filter(role=User.CONSULTANT)
    
    print(f"Found {consultants.count()} consultants:")
    print("-" * 50)
    
    for user in consultants:
        print(f"User ID: {user.id}")
        print(f"Username: {user.username}")
        print(f"Email: {user.email}")
        print(f"Full Name: {user.get_full_name()}")
        print(f"Phone: {user.phone_number}")
        
        try:
            profile = user.consultant_service_profile
            print("\nConsultant Service Profile:")
            print(f"  Qualification: {profile.qualification}")
            print(f"  Experience: {profile.experience_years} years")
            print(f"  Certifications: {profile.certifications}")
            print(f"  Consultation Fee: {profile.consultation_fee}")
            print(f"  Max Concurrent Clients: {profile.max_concurrent_clients}")
            print(f"  Current Client Count: {profile.current_client_count}")
        except ConsultantServiceProfile.DoesNotExist:
            print("\nNO Consultant Service Profile found!")
            
        print("-" * 50)

if __name__ == '__main__':
    print_consultant_details()
