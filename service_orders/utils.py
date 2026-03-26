"""
Helper functions for service order processing
"""

import logging
from django.db.models import F
from django.utils import timezone
from consultants.models import ClientServiceRequest
from consultants.services import assign_consultant_to_request

logger = logging.getLogger(__name__)


def _resolve_contact(user):
    """
    Return the best (email, phone, name) for notification purposes.
    Sub-accounts may not have their own email/phone — fall back to the parent account.
    """
    email = user.email or ''
    phone = user.phone_number or ''
    name = user.first_name or user.username

    # If this is a sub-account and contact fields are missing, use parent's
    if user.parent_account_id:
        parent = user.parent_account
        if not email:
            email = parent.email or ''
        if not phone:
            phone = parent.phone_number or ''

    return email, phone, name


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

    # Resolve contact details once for all items in this order
    client_email, client_phone, client_name = _resolve_contact(order.user)
    
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
        
        service_title_for_email = getattr(item.service, 'title', getattr(item, 'service_title', 'Custom Service'))

        # ── Email notification ──────────────────────────────────────────────
        # Uses fallback email so sub-accounts whose email is blank get parent's email
        if client_email:
            try:
                from notifications.tasks import send_service_assignment_email_task
                send_service_assignment_email_task.delay(
                    client_email=client_email,
                    client_name=client_name,
                    service_title=service_title_for_email,
                    amount_paid=str(item.price)
                )
            except Exception as e:
                logger.error(f"Failed to queue assignment email task for order {order.id}: {e}")
        else:
            logger.warning(f"Order {order.id}: no email found for user {order.user.id} or parent — skipping email.")

        # ── WhatsApp notification ───────────────────────────────────────────
        # Uses fallback phone so sub-accounts without their own number get parent's
        if client_phone:
            try:
                from notifications.tasks import send_whatsapp_template_task
                send_whatsapp_template_task.delay(
                    phone_number=client_phone,
                    template_name="service_order_confirmation",
                    variables=[
                        client_name,
                        service_title_for_email,
                        str(item.price),
                    ]
                )
            except Exception as e:
                logger.error(f"Failed to queue WhatsApp task for order {order.id}: {e}")
        else:
            logger.warning(f"Order {order.id}: no phone found for user {order.user.id} or parent — skipping WhatsApp.")
        
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
            } if consultant else None
        })
    
    return created_requests
