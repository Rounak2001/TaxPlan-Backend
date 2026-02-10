from django.db.models.signals import post_save
from django.dispatch import receiver
from .models import ClientServiceRequest
from core_auth.models import ClientProfile


@receiver(post_save, sender=ClientServiceRequest)
def sync_consultant_to_client_profile(sender, instance, created, **kwargs):
    """
    When a consultant is assigned to a service request,
    automatically update the client's profile with this consultant.
    
    Logic: Most recent assignment wins - the client's primary consultant
    is always updated to the most recently assigned consultant.
    """
    if instance.assigned_consultant:
        try:
            # Get or create the client profile
            client_profile, _ = ClientProfile.objects.get_or_create(
                user=instance.client,
                defaults={'assigned_consultant': instance.assigned_consultant.user}
            )
            
            # Only set primary consultant if not already set
            # Multi-TC support: additional TCs are handled via service requests,
            # not by overwriting the primary ConsultantProfile link.
            if not client_profile.assigned_consultant:
                client_profile.assigned_consultant = instance.assigned_consultant.user
                client_profile.save()
                print(f"✅ Set primary consultant: {instance.assigned_consultant.full_name} → Client: {instance.client.email}")
            else:
                print(f"ℹ️ Primary consultant already set for {instance.client.email}, skipping profile sync.")
                
                
        except Exception as e:
            print(f"Error syncing consultant assignment: {e}")


@receiver(post_save, sender=ClientServiceRequest)
def create_pending_document_requests(sender, instance, created, **kwargs):
    """
    Automatically create pending document requests in the client's vault
    when a service is assigned or created.
    """
    from document_vault.models import Document
    import re
    
    if created and instance.service.documents_required:
        client = instance.client
        service = instance.service
        
        # Split by newline, comma, semicolon, or bullet points
        required_list = re.split(r'[\n,;•]|\r\n', service.documents_required)
        
        # Clean the items (remove leading bullets/numbers and trim)
        cleaned_list = []
        for doc in required_list:
            # Remove leading symbols like •, -, *, 1., etc.
            title = re.sub(r'^[ \-\•\*\d\.]+', '', doc).strip()
            if title and len(title) > 1:
                cleaned_list.append(title)
        
        # Create PENDING records for each document
        created_count = 0
        for doc_title in cleaned_list:
            # Check if this document (by title) already exists for this client to avoid duplicates
            if not Document.objects.filter(client=client, title__iexact=doc_title).exists():
                Document.objects.create(
                    client=client,
                    consultant=instance.assigned_consultant.user if instance.assigned_consultant else None,
                    title=doc_title,
                    description=f"Required for {service.title}",
                    status='PENDING'
                )
                created_count += 1
        
        if created_count > 0:
            print(f"Created {created_count} pending document requests for {client.email}")
