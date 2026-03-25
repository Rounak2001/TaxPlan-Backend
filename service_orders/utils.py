"""
Helper functions for service order processing
"""

import logging
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
    
    for item in order.items.select_related('service', 'selected_consultant'):
        # Create service request using the actual DB service if available, else use custom title
        service_title_for_notes = item.service.title if item.service else getattr(item, 'service_title', 'Custom Service')
        
        request = ClientServiceRequest.objects.create(
            client=order.user,
            service=item.service, # Can be null for custom landing page items
            status='pending',
            notes=f'Payment completed for order #{order.id}: {service_title_for_notes}',
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
        
        # Fire async email to client about the purchase and assignment
        try:
            from notifications.tasks import send_service_assignment_email_task
            client_name = order.user.first_name or order.user.username
            
            # The service title fallback handles custom DB services or frontend string items
            service_title_for_email = getattr(item.service, 'title', getattr(item, 'service_title', 'Custom Service'))
            
            send_service_assignment_email_task.delay(
                client_email=order.user.email,
                client_name=client_name,
                service_title=service_title_for_email,
                amount_paid=str(item.price)
            )
        except Exception as e:
            logger = logging.getLogger(__name__)
            logger.error(f"Failed to queue assignment email task for order {order.id}: {e}")
        
        created_requests.append({
            'request_id': request.id,
            'service': getattr(item.service, 'title', getattr(item, 'service_title', 'Custom Service')),
            'status': request.status,
            'selection_mode': getattr(item, 'selection_mode', 'auto'),
            'consultant': {
                'id': consultant.id if consultant else None,
                'name': consultant.full_name if consultant else None,
                'email': consultant.email if consultant else None,
                'phone': consultant.phone if consultant else None
            } if getattr(locals(), 'consultant', None) else None
        })
    
    return created_requests

