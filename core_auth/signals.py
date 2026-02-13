from django.db.models.signals import post_delete
from django.dispatch import receiver
from .models import ClientProfile, User

@receiver(post_delete, sender=ClientProfile)
def delete_user_on_client_profile_delete(sender, instance, **kwargs):
    """
    Ensure the User record is deleted when the ClientProfile is deleted.
    This ensures that 'deleting a client' from the profiles list correctly
    triggers the CASCADE to documents and services.
    """
    if instance.user:
        try:
            instance.user.delete()
            print(f"Deleted User {instance.user.username} following ClientProfile deletion.")
        except User.DoesNotExist:
            pass
