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
            
            # Always update to the most recent consultant assignment
            if client_profile.assigned_consultant != instance.assigned_consultant.user:
                client_profile.assigned_consultant = instance.assigned_consultant.user
                client_profile.save()
                print(f"✅ Synced consultant: {instance.assigned_consultant.full_name} → Client: {instance.client.email}")
                
        except Exception as e:
            print(f"❌ Error syncing consultant assignment: {e}")
