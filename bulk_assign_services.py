"""
Bulk assign services to consultants
Run with: python bulk_assign_services.py
"""

import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')
django.setup()

from consultants.models import ConsultantServiceProfile, Service, ServiceCategory, ConsultantServiceExpertise

def assign_services_by_category(consultant_id, category_names):
    """
    Assign all services from specified categories to a consultant
    
    Args:
        consultant_id: ID of the ConsultantServiceProfile
        category_names: List of category names (e.g., ['Registration', 'Compliance'])
    """
    try:
        consultant = ConsultantServiceProfile.objects.get(id=consultant_id)
        print(f"\nAssigning services to: {consultant.full_name}")
        
        total_assigned = 0
        
        for category_name in category_names:
            try:
                category = ServiceCategory.objects.get(name=category_name)
                services = Service.objects.filter(category=category, is_active=True)
                
                print(f"\n{category_name} Services:")
                for service in services:
                    # Create expertise entry (get_or_create prevents duplicates)
                    expertise, created = ConsultantServiceExpertise.objects.get_or_create(
                        consultant=consultant,
                        service=service
                    )
                    
                    if created:
                        print(f"  + Assigned: {service.title}")
                        total_assigned += 1
                    else:
                        print(f"  - Already assigned: {service.title}")
                        
            except ServiceCategory.DoesNotExist:
                print(f"  X Category '{category_name}' not found")
        
        print(f"\n{'='*50}")
        print(f"Total new services assigned: {total_assigned}")
        print(f"{'='*50}")
        
    except ConsultantServiceProfile.DoesNotExist:
        print(f"X Consultant with ID {consultant_id} not found")


# ============================================
# CONFIGURE YOUR ASSIGNMENTS HERE
# ============================================

# Available Consultants:
# ID: 1 - Consulatnt Ritesh (riteshdhumak95@gmail.com)
# ID: 3 - Ramzan Sir (ramzan@abc.com)
# ID: 4 - Consultant3 (con3@g.com)

# Assign Registration and Compliance services to consultant ID 1
assign_services_by_category(
    consultant_id=4,
    category_names=['Registration', 'Certification Services', 'Startup & Advisory', 'Capital Gains & Tax Planning']
)

# You can add more assignments here:
# assign_services_by_category(
#     consultant_id=3,
#     category_names=['Income Tax', 'GST']
# )
