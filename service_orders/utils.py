"""
Helper functions for service order processing
"""

import logging
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.db.models import F
from django.utils import timezone
from consultants.models import ClientServiceRequest
from consultants.services import assign_consultant_to_request
from activity_timeline.models import Activity
from notifications.models import Notification
from notifications.serializers import NotificationSerializer

logger = logging.getLogger(__name__)


def _push_realtime_notification(notification, *, extra_payload=None):
    channel_layer = get_channel_layer()
    if not channel_layer:
        return

    payload = NotificationSerializer(notification).data
    if isinstance(extra_payload, dict):
        payload.update(extra_payload)

    async_to_sync(channel_layer.group_send)(
        f'user_{notification.recipient_id}',
        {
            'type': 'notification_message',
            'data': payload,
        },
    )


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
    additional_line_summaries = []
    
    for item in order.items.select_related('service', 'selected_consultant'):
        # Create service request using the actual DB service if available, else use custom title
        service_title_for_notes = item.service.title if item.service else getattr(item, 'service_title', 'Custom Service')
        
        explainability_note = (getattr(item, 'variant_name', '') or '').strip()
        price_update_reason = (getattr(item, 'price_update_reason', '') or '').strip()
        base_price = getattr(item, 'base_price', None)
        note_parts = [f'Payment completed for order #{order.id}: {service_title_for_notes}']
        if explainability_note:
            note_parts.append(f'Why needed: {explainability_note}')
        if base_price is not None and str(base_price) != str(item.price) and price_update_reason:
            note_parts.append(
                f'Price updated from Rs {base_price} to Rs {item.price}. Reason: {price_update_reason}'
            )

        request = ClientServiceRequest.objects.create(
            client=order.user,
            service=item.service, # Can be null for custom landing page items
            status='pending',
            notes=' | '.join(note_parts),
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
                    template_name="payment_receipt_success",  # "service_order_confirmation" was removed from Meta BM
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

        if getattr(order, 'is_additional', False):
            service_title = getattr(item.service, 'title', getattr(item, 'service_title', 'Custom Service'))
            activity_actor = order.initiated_by or order.user
            additional_line_summaries.append(service_title)

            Activity.objects.create(
                actor=activity_actor,
                target_user=order.user,
                activity_type='additional_payment_paid',
                title=f"Payment of Rs {item.price} completed for {service_title}",
                content_object=order,
                metadata={
                    'booking_id': order.from_booking_id,
                    'service_title': service_title,
                    'amount': str(item.price),
                    'razorpay_payment_id': order.razorpay_payment_id,
                    'why_needed': explainability_note,
                    'price_update_reason': price_update_reason,
                },
            )

            Activity.objects.create(
                actor=activity_actor,
                target_user=order.user,
                activity_type='additional_service_added',
                title=f"Additional service '{service_title}' added to your account",
                content_object=request,
                metadata={
                    'booking_id': order.from_booking_id,
                    'assigned_consultant_id': consultant.id if consultant else None,
                    'service_request_id': request.id,
                },
            )

    if getattr(order, 'is_additional', False) and getattr(order, 'initiated_by_id', None):
        total_services = len(additional_line_summaries)
        summary_text = ', '.join(additional_line_summaries[:2])
        if total_services > 2:
            summary_text += f' +{total_services - 2} more'
        notification = Notification.objects.create(
            recipient=order.initiated_by,
            category='payment',
            title='Additional payment received',
            message=(
                f'{order.user.get_full_name() or order.user.username} completed payment for '
                f'{summary_text or "additional services"}.'
            ),
            link='/dashboard',
        )
        _push_realtime_notification(
            notification,
            extra_payload={
                'type': 'ADDITIONAL_PAYMENT_CONFIRMED',
                'order_id': order.id,
                'booking_id': order.from_booking_id,
            },
        )

    return created_requests
