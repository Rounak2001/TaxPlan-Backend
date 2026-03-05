import logging
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync
from django.core.cache import cache

from document_vault.models import Document, SharedReport, LegalNotice
from consultants.models import ClientServiceRequest
from consultations.models import ConsultationBooking
from service_orders.models import ServiceOrder
from chat.models import Message

from .models import Notification
from .serializers import NotificationSerializer
from .whatsapp_service import send_whatsapp_template

logger = logging.getLogger(__name__)


# =====================================================================
# Helper: Create + Push (reused by every signal below)
# =====================================================================

def create_and_push_notification(recipient, category, title, message, link=''):
    """Create a Notification row AND push it over the WebSocket."""
    try:
        print(f"[NOTIFICATION] Creating for user={recipient.username} (id={recipient.id}), title={title}")
        notification = Notification.objects.create(
            recipient=recipient,
            category=category,
            title=title,
            message=message,
            link=link,
        )
        print(f"[NOTIFICATION] Created in DB: id={notification.id}")

        channel_layer = get_channel_layer()
        group_name = f"user_{recipient.id}"

        async_to_sync(channel_layer.group_send)(
            group_name,
            {
                "type": "notification_message",
                "data": NotificationSerializer(notification).data,
            },
        )
        print(f"[NOTIFICATION] Pushed to WebSocket group: {group_name}")
        logger.info(f"Notification sent to {recipient.username}: {title}")
    except Exception as exc:
        print(f"[NOTIFICATION] ERROR: {exc}")
        logger.exception(f"Failed to create/push notification for {recipient}: {exc}")


def _get_consultant_for_client(client_user):
    """
    Find the consultant responsible for a client.
    Checks: 1) Primary assignment  2) Active service request assignment.
    Returns User object or None.
    """
    # 1. Try primary assignment from ClientProfile
    try:
        if hasattr(client_user, 'client_profile') and client_user.client_profile.assigned_consultant:
            return client_user.client_profile.assigned_consultant
    except Exception:
        pass

    # 2. Fallback: find consultant via the latest active service request
    try:
        latest_sr = ClientServiceRequest.objects.filter(
            client=client_user,
            assigned_consultant__isnull=False,
        ).exclude(status__in=['completed', 'cancelled']).order_by('-created_at').first()
        if latest_sr and latest_sr.assigned_consultant:
            return latest_sr.assigned_consultant.user
    except Exception:
        pass

    return None


# =====================================================================
# 1. DOCUMENT EVENTS
# =====================================================================

@receiver(post_save, sender=Document)
def notify_document_activity(sender, instance, created, **kwargs):
    """
    • Document uploaded by client (created=True with file, OR status changed to UPLOADED)
      → notify the assigned consultant
    • Document verified/rejected → notify client
    """
    try:
        print(f"[SIGNAL] Document signal fired: created={created}, status={instance.status}, "
              f"file={bool(instance.file)}, client={getattr(instance.client, 'username', None)}")

        # --- Client uploaded a document → notify consultant ---
        if instance.status == 'UPLOADED' and instance.file and instance.client:
            consultant_user = _get_consultant_for_client(instance.client)
            if consultant_user:
                # Avoid duplicate: don't send if this was already sent recently
                client_name = instance.client.get_full_name() or instance.client.username
                create_and_push_notification(
                    recipient=consultant_user,
                    category='document',
                    title=f"New Document from {client_name}",
                    message=f"Uploaded: {instance.title}",
                    link="/vault?tab=documents",
                )
            else:
                print(f"[SIGNAL] No consultant found for client {instance.client.username}")

        # --- Document verified → notify client ---
        elif instance.status == 'VERIFIED' and instance.client:
            create_and_push_notification(
                recipient=instance.client,
                category='document',
                title="Document Verified ✅",
                message=f"Your document '{instance.title}' was approved.",
                link="/client/vault?tab=documents",
            )

        # --- Document rejected → notify client ---
        elif instance.status == 'REJECTED' and instance.client:
            create_and_push_notification(
                recipient=instance.client,
                category='document',
                title="Document Rejected ❌",
                message=f"Action required for '{instance.title}'.",
                link="/client/vault?tab=documents",
            )
            # Send WhatsApp Template
            if getattr(instance.client, 'phone_number', None):
                send_whatsapp_template(
                    phone_number=instance.client.phone_number,
                    template_name="doc_rejected_action_needed",
                    variables=[
                        instance.client.first_name or instance.client.username,
                        instance.title,
                        "Please review the document requirements."
                    ]
                )

    except Exception as exc:
        print(f"[SIGNAL] Document ERROR: {exc}")
        logger.exception(f"Error in notify_document_activity: {exc}")


@receiver(post_save, sender=SharedReport)
def notify_shared_report_activity(sender, instance, created, **kwargs):
    """
    • Consultant shares a report → notify client
    """
    try:
        if created and instance.client:
            consultant_name = instance.consultant.get_full_name() or instance.consultant.username
            create_and_push_notification(
                recipient=instance.client,
                category='document',
                title=f"New Shared Report from {consultant_name}",
                message=f"Received: {instance.title}",
                link="/client/vault?tab=shared",
            )
            # Send WhatsApp Template
            if getattr(instance.client, 'phone_number', None):
                send_whatsapp_template(
                    phone_number=instance.client.phone_number,
                    template_name="new_document_shared",
                    variables=[
                        instance.client.first_name or instance.client.username,
                        instance.title,
                        consultant_name
                    ]
                )
    except Exception as exc:
        print(f"[SIGNAL] SharedReport ERROR: {exc}")
        logger.exception(f"Error in notify_shared_report_activity: {exc}")


@receiver(post_save, sender=LegalNotice)
def notify_legal_notice_activity(sender, instance, created, **kwargs):
    """
    • Consultant uploads a legal notice → notify client
    """
    try:
        if created and instance.client:
            consultant_name = instance.consultant.get_full_name() or instance.consultant.username
            create_and_push_notification(
                recipient=instance.client,
                category='document',
                title=f"New Legal Notice ({instance.get_priority_display()})",
                message=f"Added: {instance.title}",
                link="/client/vault?tab=notices",
            )
            # Send WhatsApp Template
            if getattr(instance.client, 'phone_number', None):
                send_whatsapp_template(
                    phone_number=instance.client.phone_number,
                    template_name="new_document_shared",
                    variables=[
                        instance.client.first_name or instance.client.username,
                        instance.title,
                        consultant_name
                    ]
                )
    except Exception as exc:
        print(f"[SIGNAL] LegalNotice ERROR: {exc}")
        logger.exception(f"Error in notify_legal_notice_activity: {exc}")


# =====================================================================
# 2. SERVICE REQUEST EVENTS
# =====================================================================

@receiver(post_save, sender=ClientServiceRequest)
def notify_service_activity(sender, instance, created, **kwargs):
    """
    • New request  → notify consultant
    • Status change → notify client
    """
    try:
        print(f"[SIGNAL] ServiceRequest signal fired: created={created}, status={instance.status}, "
              f"consultant={getattr(instance.assigned_consultant, 'user', None)}")

        if created:
            if instance.assigned_consultant:
                create_and_push_notification(
                    recipient=instance.assigned_consultant.user,
                    category='service',
                    title="New Service Request",
                    message=f"{instance.client.get_full_name() or instance.client.username} requested {instance.service.title}",
                    link="/consultant/services",
                )
        else:
            # Status update → notify client
            status_display = dict(ClientServiceRequest.STATUS_CHOICES).get(instance.status, instance.status)
            create_and_push_notification(
                recipient=instance.client,
                category='service',
                title=f"Service Update: {status_display}",
                message=f"Your request for '{instance.service.title}' is now {status_display}.",
                link="/client/services",
            )
            # Send WhatsApp Template
            if getattr(instance.client, 'phone_number', None):
                send_whatsapp_template(
                    phone_number=instance.client.phone_number,
                    template_name="service_status_update",
                    variables=[
                        instance.client.first_name or instance.client.username,
                        instance.service.title,
                        status_display
                    ]
                )
    except Exception as exc:
        print(f"[SIGNAL] ServiceRequest ERROR: {exc}")
        logger.exception(f"Error in notify_service_activity: {exc}")


# =====================================================================
# 3. CONSULTATION BOOKING EVENTS
# =====================================================================

@receiver(pre_save, sender=ConsultationBooking)
def cache_old_booking_status(sender, instance, **kwargs):
    """Cache the old status before saving to detect actual changes in post_save."""
    if instance.pk:
        try:
            old_instance = ConsultationBooking.objects.get(pk=instance.pk)
            instance._old_status = old_instance.status
        except ConsultationBooking.DoesNotExist:
            instance._old_status = None
    else:
        instance._old_status = None

@receiver(post_save, sender=ConsultationBooking)
def notify_consultation_activity(sender, instance, created, **kwargs):
    """
    • New booking       → notify consultant
    • Status updated    → notify client (confirmed / cancelled)
    """
    try:
        print(f"[SIGNAL] ConsultationBooking signal fired: created={created}, status={instance.status}, "
              f"consultant={instance.consultant.username}, client={instance.client.username}")

        if created:
            # New booking → Consultant should know
            create_and_push_notification(
                recipient=instance.consultant,
                category='consultation',
                title="New Consultation Booking 📅",
                message=f"{instance.client.get_full_name() or instance.client.username} booked on {instance.booking_date.strftime('%d %b %Y')} ({instance.start_time.strftime('%I:%M %p')} – {instance.end_time.strftime('%I:%M %p')})",
                link="/consultant/consultations",
            )
            # Also let the client know
            create_and_push_notification(
                recipient=instance.client,
                category='consultation',
                title="Booking Submitted",
                message=f"Your consultation on {instance.booking_date.strftime('%d %b %Y')} is pending confirmation.",
                link="/client/consultations",
            )
            
            # For created instance, we don't need to check old status. Wait for confirmation.
            return

        # We need to detect if the status *just* changed
        old_status = getattr(instance, '_old_status', None)
        
        if old_status != instance.status:
            # Status update
            if instance.status == 'confirmed':
                create_and_push_notification(
                    recipient=instance.client,
                    category='consultation',
                    title="Booking Confirmed ✅",
                    message=f"Your consultation on {instance.booking_date.strftime('%d %b %Y')} at {instance.start_time.strftime('%I:%M %p')} is confirmed.",
                    link="/client/consultations",
                )
                # Send WhatsApp Template
                if getattr(instance.client, 'phone_number', None):
                    send_whatsapp_template(
                        phone_number=instance.client.phone_number,
                        template_name="consultation_status_update",
                        variables=[
                            instance.client.first_name or instance.client.username,
                            instance.consultant.get_full_name() or instance.consultant.username,
                            instance.booking_date.strftime('%d %b %Y'),
                            instance.start_time.strftime('%I:%M %p'),
                            "Confirmed"
                        ]
                    )
            elif instance.status == 'cancelled':
                # Notify both parties
                create_and_push_notification(
                    recipient=instance.client,
                    category='consultation',
                    title="Booking Cancelled",
                    message=f"Consultation on {instance.booking_date.strftime('%d %b %Y')} has been cancelled.",
                    link="/client/consultations",
                )
                # Send WhatsApp Template
                if getattr(instance.client, 'phone_number', None):
                    send_whatsapp_template(
                        phone_number=instance.client.phone_number,
                        template_name="consultation_status_update",
                        variables=[
                            instance.client.first_name or instance.client.username,
                            instance.consultant.get_full_name() or instance.consultant.username,
                            instance.booking_date.strftime('%d %b %Y'),
                            instance.start_time.strftime('%I:%M %p'),
                            "Cancelled"
                        ]
                    )
                create_and_push_notification(
                    recipient=instance.consultant,
                    category='consultation',
                    title="Booking Cancelled",
                    message=f"Consultation with {instance.client.get_full_name() or instance.client.username} on {instance.booking_date.strftime('%d %b %Y')} was cancelled.",
                    link="/consultant/consultations",
                )
    except Exception as exc:
        print(f"[SIGNAL] ConsultationBooking ERROR: {exc}")
        logger.exception(f"Error in notify_consultation_activity: {exc}")


# =====================================================================
# 4. SERVICE ORDER / PAYMENT EVENTS
# =====================================================================

@receiver(post_save, sender=ServiceOrder)
def notify_payment_activity(sender, instance, created, **kwargs):
    """
    • Order paid → notify client (confirmation)
    """
    try:
        print(f"[SIGNAL] ServiceOrder signal fired: created={created}, status={instance.status}")
        if not created and instance.status == 'paid':
            # Build item summary
            items = instance.items.all()
            item_names = ', '.join([item.service_title for item in items[:3]])
            if items.count() > 3:
                item_names += f" +{items.count() - 3} more"

            create_and_push_notification(
                recipient=instance.user,
                category='payment',
                title="Payment Successful 💳",
                message=f"₹{instance.total_amount} paid for: {item_names}",
                link="/client/services",
            )
            # Send WhatsApp Template
            if getattr(instance.user, 'phone_number', None):
                send_whatsapp_template(
                    phone_number=instance.user.phone_number,
                    template_name="payment_receipt_success",
                    variables=[
                        instance.user.first_name or instance.user.username,
                        str(instance.total_amount),
                        item_names
                    ]
                )
    except Exception as exc:
        print(f"[SIGNAL] ServiceOrder ERROR: {exc}")
        logger.exception(f"Error in notify_payment_activity: {exc}")


# =====================================================================
# 5. CHAT MESSAGE EVENTS
# =====================================================================

@receiver(post_save, sender=Message)
def notify_chat_message(sender, instance, created, **kwargs):
    """
    New chat message → notify the OTHER participant (not the sender).
    """
    try:
        print(f"[SIGNAL] Chat Message signal fired: created={created}, sender={instance.sender.username}")
        if not created:
            return

        conversation = instance.conversation
        sender_user = instance.sender

        # Determine recipient: the other person in the conversation
        if sender_user.id == conversation.consultant_id:
            recipient = conversation.client
        else:
            recipient = conversation.consultant

        sender_name = sender_user.get_full_name() or sender_user.username
        # Truncate message preview
        preview = instance.content[:80] + ('…' if len(instance.content) > 80 else '')

        create_and_push_notification(
            recipient=recipient,
            category='chat',
            title=f"New message from {sender_name}",
            message=preview,
            link="/messages" if recipient.role == 'CONSULTANT' else "/client/messages",
        )
        
        # Send WhatsApp Template for clients ONLY (consultants usually live in the dashboard)
        if recipient.role == 'CLIENT' and getattr(recipient, 'phone_number', None):
            send_whatsapp_template(
                phone_number=recipient.phone_number,
                template_name="unread_secure_message",
                variables=[
                    recipient.first_name or recipient.username,
                    sender_name,
                    preview
                ]
            )
    except Exception as exc:
        import traceback
        import sys
        print(f"[SIGNAL] Chat Message CRITICAL ERROR: {exc}", flush=True)
        print(traceback.format_exc(), flush=True)
        logger.exception(f"Error in notify_chat_message: {exc}")
