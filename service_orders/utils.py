"""
Helper functions for service order processing
"""

from django.db.models import F
from django.utils import timezone
from consultants.models import ClientServiceRequest
from consultants.services import assign_consultant_to_request


def create_service_requests_from_order(order):
    """
    Create ClientServiceRequest for each OrderItem and assign consultants.
    
    If item has a pre-selected consultant (manual mode), assign directly.
    Otherwise, use auto-assignment (affinity + round-robin).
    
    Args:
        order: ServiceOrder instance
        
    Returns:
        List of dicts with request details and assigned consultant info
    """
    created_requests = []
    
    # Idempotency Check: See if requests already exist for this order
    existing_count = ClientServiceRequest.objects.filter(
        client=order.user,
        notes__contains=f'order #{order.id}'
    ).count()
    
    if existing_count > 0:
        return [] # Already processed
    
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
        
        consultant = None
        
        if item.selected_consultant:
            # Manual mode: Directly assign the chosen consultant
            consultant = item.selected_consultant
            request.assigned_consultant = consultant
            request.status = 'assigned'
            request.assigned_at = timezone.now()
            request.save()
            
            # Increment consultant's current client count atomically
            consultant.current_client_count = F('current_client_count') + 1
            consultant.last_assigned_at = timezone.now()
            consultant.save()
            consultant.refresh_from_db()
        else:
            # Auto mode: Use existing affinity + round-robin logic
            consultant = assign_consultant_to_request(request.id)
        
        # Refresh to get updated status
        request.refresh_from_db()
        
        created_requests.append({
            'request_id': request.id,
            'service': item.service.title,
            'status': request.status,
            'selection_mode': item.selection_mode,
            'consultant': {
                'id': consultant.id if consultant else None,
                'name': consultant.full_name if consultant else None,
                'email': consultant.email if consultant else None,
                'phone': consultant.phone if consultant else None
            } if consultant else None
        })
    
    return created_requests

