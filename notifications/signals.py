from django.db.models.signals import post_save
from django.dispatch import receiver
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync
from document_vault.models import Document
from consultants.models import ClientServiceRequest
from exotel_calls.models import CallLog
from .models import Notification
from .serializers import NotificationSerializer

def create_and_push_notification(recipient, category, title, message, link):
    # 1. Create Notification in DB
    notification = Notification.objects.create(
        recipient=recipient,
        category=category,
        title=title,
        message=message,
        link=link
    )

    # 2. Push to WebSocket
    channel_layer = get_channel_layer()
    group_name = f"user_{recipient.id}"
    
    async_to_sync(channel_layer.group_send)(
        group_name,
        {
            "type": "notification_message",
            "data": NotificationSerializer(notification).data
        }
    )

# --- Document Signals ---

@receiver(post_save, sender=Document)
def notify_document_activity(sender, instance, created, **kwargs):
    # 1. Document Uploaded -> Notify Consultant
    if created and instance.file and instance.client:
        if instance.client.client_profile.assigned_consultant:
            consultant = instance.client.client_profile.assigned_consultant
            create_and_push_notification(
                recipient=consultant,
                category='document',
                title=f"New Document from {instance.client.first_name}",
                message=f"Uploaded: {instance.title}",
                link="/consultant/vault" # Adjust link as needed
            )

    # 2. Document Verified/Rejected -> Notify Client
    elif not created:
         # Check if status changed (simplified logic, ideally check pre_save)
         if instance.status == 'VERIFIED' and instance.client:
             create_and_push_notification(
                recipient=instance.client,
                category='document',
                title="Document Verified",
                message=f"Your document '{instance.title}' was approved.",
                link="/client/vault"
             )
         elif instance.status == 'REJECTED' and instance.client:
             create_and_push_notification(
                recipient=instance.client,
                category='document',
                title="Document Rejected",
                message=f"Action required for '{instance.title}'.",
                link="/client/vault"
             )

# --- Service Request Signals ---

@receiver(post_save, sender=ClientServiceRequest)
def notify_service_activity(sender, instance, created, **kwargs):
    # 1. New Service Request -> Notify Consultant (if assigned)
    if created and instance.assigned_consultant:
        create_and_push_notification(
            recipient=instance.assigned_consultant.user,
            category='service',
            title="New Service Request",
            message=f"{instance.client.first_name} requested {instance.service.title}",
            link="/consultant/services"
        )
    
    # 2. Status Changed -> Notify Client
    elif not created:
        # Ideally check if status actually changed
        create_and_push_notification(
            recipient=instance.client,
            category='service',
            title=f"Service Update: {instance.status}",
            message=f"Update on {instance.service.title}",
            link="/client/services"
        )

# --- Call Logs ---
# Skipping for now to avoid noise, or add similarly
