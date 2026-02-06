import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')
django.setup()

from consultants.models import ClientServiceRequest
from service_orders.models import ServiceOrder

print("=== SERVICE REQUESTS ===")
requests = ClientServiceRequest.objects.all()
print(f"Total: {requests.count()}\n")

if requests.count() == 0:
    print("NO SERVICE REQUESTS FOUND!")
    print("\nThis means:")
    print("1. No payments have triggered consultant assignment yet")
    print("2. OR the frontend didn't send service_id in the order")
else:
    for r in requests:
        print(f"Request ID: {r.id}")
        print(f"  Client: {r.client.email}")
        print(f"  Service: {r.service.title}")
        print(f"  Status: {r.status}")
        print(f"  Consultant: {r.assigned_consultant.full_name if r.assigned_consultant else 'Not assigned'}")
        print()

print("\n=== RECENT PAID ORDERS ===")
orders = ServiceOrder.objects.filter(status='paid').order_by('-created_at')[:3]
for order in orders:
    print(f"\nOrder ID: {order.id}")
    print(f"  User: {order.user.email}")
    print(f"  Amount: ₹{order.total_amount}")
    print(f"  Status: {order.status}")
    print(f"  Items:")
    for item in order.items.all():
        print(f"    - {item.service_title}")
        print(f"      service_id: {item.service_id}")
