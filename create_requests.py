import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')
django.setup()

from consultants.models import ClientServiceRequest, Service, ConsultantServiceProfile
from consultants.services import assign_consultant_to_request
from service_orders.models import ServiceOrder

# Get the latest paid order
order = ServiceOrder.objects.filter(status='paid').order_by('-created_at').first()

if not order:
    print("No paid orders found!")
    exit()

print(f"Processing Order ID: {order.id}")
print(f"Client: {order.user.email}")
print(f"Amount: Rs {order.total_amount}")

# Get order items
for item in order.items.all():
    print(f"\nItem: {item.service_title}")
    
    # Try to find matching service
    service = Service.objects.filter(title__icontains=item.service_title.split('(')[0].strip()).first()
    
    if not service:
        print(f"  ERROR: Could not find service matching '{item.service_title}'")
        continue
    
    print(f"  Matched to service ID: {service.id} - {service.title}")
    
    # Create service request
    request = ClientServiceRequest.objects.create(
        client=order.user,
        service=service,
        status='pending',
        notes=f'Manual creation for paid order #{order.id}',
        priority=5
    )
    print(f"  Created request ID: {request.id}")
    
    # Try to assign consultant
    consultant = assign_consultant_to_request(request.id)
    
    if consultant:
        request.refresh_from_db()
        print(f"  Assigned to: {consultant.full_name}")
        print(f"  Email: {consultant.email}")
        print(f"  Phone: {consultant.phone}")
    else:
        print(f"  No consultant available")

print("\n=== DONE ===")
print("Check your dashboard now!")
