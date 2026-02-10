from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver
from django.contrib.contenttypes.models import ContentType
from document_vault.models import Document
from consultants.models import ClientServiceRequest
from exotel_calls.models import CallLog
from .models import Activity


# Track previous status for detecting changes
_document_status_cache = {}
_service_status_cache = {}


@receiver(pre_save, sender=Document)
def cache_document_status(sender, instance, **kwargs):
    """Cache the previous status before saving"""
    if instance.pk:
        try:
            old_doc = Document.objects.get(pk=instance.pk)
            _document_status_cache[instance.pk] = old_doc.status
        except Document.DoesNotExist:
            pass


@receiver(post_save, sender=Document)
def log_document_activity(sender, instance, created, **kwargs):
    """Create activity when document is uploaded, verified, or rejected"""
    
    # Document upload (client uploads a file)
    if created and instance.file:
        Activity.objects.create(
            actor=instance.client,
            target_user=instance.client,
            activity_type='document_upload',
            title=f'Uploaded {instance.title}',
            description=f'Client uploaded document: {instance.title}',
            content_object=instance,
            metadata={'folder': instance.folder.name if instance.folder else None}
        )
    
    # Document verification (consultant verifies)
    elif not created and instance.status == 'VERIFIED':
        old_status = _document_status_cache.get(instance.pk)
        if old_status != 'VERIFIED' and instance.consultant:
            Activity.objects.create(
                actor=instance.consultant,
                target_user=instance.client,
                activity_type='document_verify',
                title=f'Verified {instance.title}',
                description=f'Document verified by consultant',
                content_object=instance
            )
    
    # Document rejection (consultant rejects)
    elif not created and instance.status == 'REJECTED':
        old_status = _document_status_cache.get(instance.pk)
        if old_status != 'REJECTED' and instance.consultant:
            Activity.objects.create(
                actor=instance.consultant,
                target_user=instance.client,
                activity_type='document_reject',
                title=f'Rejected {instance.title}',
                description=instance.description or 'Document rejected - please re-upload',
                content_object=instance
            )
    
    # Clean up cache
    if instance.pk in _document_status_cache:
        del _document_status_cache[instance.pk]


@receiver(pre_save, sender=ClientServiceRequest)
def cache_service_status(sender, instance, **kwargs):
    """Cache the previous status before saving"""
    if instance.pk:
        try:
            old_service = ClientServiceRequest.objects.get(pk=instance.pk)
            _service_status_cache[instance.pk] = old_service.status
        except ClientServiceRequest.DoesNotExist:
            pass


@receiver(post_save, sender=ClientServiceRequest)
def log_service_activity(sender, instance, created, **kwargs):
    """Create activity for service request events"""
    
    # New service request
    if created:
        Activity.objects.create(
            actor=instance.client,
            target_user=instance.client,
            activity_type='service_new',
            title=f'New service request: {instance.service.title}',
            description=f'Client requested {instance.service.title}',
            content_object=instance,
            metadata={
                'service_id': instance.service.id,
                'service_title': instance.service.title,
                'category': instance.service.category.name
            }
        )
    
    # Service status change
    elif not created:
        old_status = _service_status_cache.get(instance.pk)
        if old_status and old_status != instance.status:
            # Service completed
            if instance.status == 'completed':
                Activity.objects.create(
                    actor=instance.assigned_consultant.user if instance.assigned_consultant else instance.client,
                    target_user=instance.client,
                    activity_type='service_complete',
                    title=f'Completed: {instance.service.title}',
                    description=f'Service request completed successfully',
                    content_object=instance,
                    metadata={'previous_status': old_status, 'new_status': instance.status}
                )
            # Other status changes
            else:
                Activity.objects.create(
                    actor=instance.assigned_consultant.user if instance.assigned_consultant else instance.client,
                    target_user=instance.client,
                    activity_type='service_status',
                    title=f'Status updated: {instance.service.title}',
                    description=f'Status changed from {old_status} to {instance.status}',
                    content_object=instance,
                    metadata={'previous_status': old_status, 'new_status': instance.status}
                )
    
    # Clean up cache
    if instance.pk in _service_status_cache:
        del _service_status_cache[instance.pk]


@receiver(post_save, sender=CallLog)
def log_call_activity(sender, instance, created, **kwargs):
    """Create activity when a call is made"""
    
    if created:
        # Determine if consultant made or received the call
        is_outgoing = hasattr(instance.caller, 'consultant_service_profile')
        
        Activity.objects.create(
            actor=instance.caller,
            target_user=instance.callee,
            activity_type='call_made' if is_outgoing else 'call_received',
            title=f'Call with {instance.callee.get_full_name() or instance.callee.username}',
            description=f'Duration: {instance.duration_display}' if instance.duration else 'Call initiated',
            content_object=instance,
            metadata={
                'duration': instance.duration,
                'status': instance.status,
                'outcome': instance.outcome
            }
        )
