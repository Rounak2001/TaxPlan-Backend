from core_auth.models import User
from consultants.models import ConsultantServiceProfile

print("\n" + "="*50)
print("DEBUG: Consultant Profile Details")
print("="*50)

consultants = User.objects.filter(role='CONSULTANT')

print(f"Found {consultants.count()} consultants:")

for user in consultants:
    print(f"\nUser ID: {user.id}")
    print(f"Username: {user.username}")
    print(f"Email: {user.email}")
    print(f"Full Name: {user.get_full_name()}")
    print(f"Phone: {user.phone_number}")
    print(f"Is Verified: {user.is_phone_verified}")
    
    # Try to access ConsultantServiceProfile
    try:
        if hasattr(user, 'consultant_service_profile'):
            profile = user.consultant_service_profile
            print("\n  [Consultant Service Profile]")
            print(f"  Qualification: {profile.qualification}")
            print(f"  Experience: {profile.experience_years} years")
            print(f"  Certifications: {profile.certifications}")
            print(f"  Fee: {profile.consultation_fee}")
            print(f"  Max Clients: {profile.max_concurrent_clients}")
            print(f"  Current Clients: {profile.current_client_count}")
        else:
            print("\n  [!] NO Consultant Service Profile linked.")
    except Exception as e:
        print(f"\n  [!] Error accessing profile: {e}")

print("\n" + "="*50 + "\n")
