from django.db.models.signals import post_save
from django.dispatch import receiver
from .models import ConsultationBooking
from .emails import send_booking_confirmation, send_booking_cancellation
import logging

logger = logging.getLogger(__name__)


@receiver(post_save, sender=ConsultationBooking)
def handle_booking_emails(sender, instance, created, **kwargs):
    """
    Automatically send emails when bookings are created or cancelled.
    """
    # Send confirmation email when booking is created
    if created:
        logger.info(f"New booking created (ID: {instance.id}). Sending confirmation emails...")
        send_booking_confirmation(instance)
    
    # Send cancellation email when status changes to cancelled
    elif instance.status == 'cancelled' and not created:
        # Check if status actually changed to cancelled
        try:
            old_instance = ConsultationBooking.objects.get(pk=instance.pk)
            if old_instance.status != 'cancelled':
                logger.info(f"Booking {instance.id} cancelled. Sending cancellation emails...")
                send_booking_cancellation(instance)
        except ConsultationBooking.DoesNotExist:
            pass
