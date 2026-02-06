"""
Helper functions for service order processing
"""

from consultants.models import ClientServiceRequest
from consultants.services import assign_consultant_to_request


def create_service_requests_from_order(order):
    """
    Create ClientServiceRequest for each OrderItem and assign consultants
    
    Args:
        order: ServiceOrder instance
        
    Returns:
        List of dicts with request details and assigned consultant info
    """
    created_requests = []
    
    for item in order.items.all():
        if not item.service:
            # Skip if no service linked
            continue
        
        # Create service request
        request = ClientServiceRequest.objects.create(
            client=order.user,
            service=item.service,
            status='pending',
            notes=f'Payment completed for order #{order.id}',
            priority=5  # High priority for paid services
        )
        
        # Auto-assign consultant
        consultant = assign_consultant_to_request(request.id)
        
        # Refresh to get updated status
        request.refresh_from_db()
        
        created_requests.append({
            'request_id': request.id,
            'service': item.service.title,
            'status': request.status,
            'consultant': {
                'id': consultant.id if consultant else None,
                'name': consultant.full_name if consultant else None,
                'email': consultant.email if consultant else None,
                'phone': consultant.phone if consultant else None
            } if consultant else None
        })
    
    return created_requests
