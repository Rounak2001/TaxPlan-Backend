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
    # Send confirmation email for new bookings
    # We check confirmation_sent to avoid duplicate sends (one on create, one on link update)
    if created and not instance.confirmation_sent:
        # If it was just created, check if we already have a link (unlikely but possible)
        # or just send it. Actually, we wait for the link in perform_create then save.
        # So the SECOND save (created=False) is where the link exists.
        pass

    if not created and not instance.confirmation_sent and instance.meeting_link:
        logger.info(f"Booking {instance.id} updated with meeting link. Sending confirmation emails...")
        send_booking_confirmation(instance)

    # Send cancellation email when status changes to cancelled
    if instance.status == 'cancelled' and not created:
        # Check if status actually changed to cancelled
        try:
            old_instance = ConsultationBooking.objects.get(pk=instance.pk)
            if old_instance.status != 'cancelled':
                logger.info(f"Booking {instance.id} cancelled. Sending cancellation emails...")
                send_booking_cancellation(instance)
        except ConsultationBooking.DoesNotExist:
            pass


@receiver(post_save, sender=ConsultationBooking)
def handle_booking_scheduled_call(sender, instance, created, **kwargs):
    """
    Create or update a ScheduledCall for Exotel exactly 1 hour before the consultation.
    """
    from exotel_calls.models import ScheduledCall
    from django.utils import timezone
    from datetime import timedelta
    
    # If the booking is cancelled, keep a record by marking pending calls as canceled
    if instance.status == 'cancelled':
        ScheduledCall.objects.filter(booking=instance, status='pending').update(status='canceled')
        return

    # If it's pending/confirmed and has a valid date and time, schedule/reschedule
    if instance.status in ['pending', 'confirmed'] and instance.booking_date and instance.start_time:
        booking_datetime = timezone.make_aware(
            timezone.datetime.combine(instance.booking_date, instance.start_time)
        )
        
        reminder_times = [
            booking_datetime - timedelta(hours=1),
            booking_datetime - timedelta(minutes=15),
            booking_datetime
        ]
        
        # Clear existing pending calls before creating new ones for the current schedule
        ScheduledCall.objects.filter(booking=instance, status='pending').delete()
        
        for run_time in reminder_times:
            if run_time > timezone.now():
                ScheduledCall.objects.create(
                    booking=instance,
                    status='pending',
                    run_at=run_time
                )
