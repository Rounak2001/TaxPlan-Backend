from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta
from consultations.models import ConsultationBooking
from consultations.emails import send_booking_reminder
import logging

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Send reminder emails for bookings happening in 24 hours'

    def handle(self, *args, **options):
        # Calculate tomorrow's date
        tomorrow = (timezone.now() + timedelta(days=1)).date()
        
        # Find all confirmed/pending bookings happening tomorrow
        # that haven't had a reminder sent yet
        bookings = ConsultationBooking.objects.filter(
            booking_date=tomorrow,
            status__in=['confirmed', 'pending'],
            reminder_sent=False
        )
        
        count = bookings.count()
        self.stdout.write(f'Found {count} booking(s) requiring reminders for {tomorrow}')
        
        success_count = 0
        fail_count = 0
        
        for booking in bookings:
            try:
                if send_booking_reminder(booking):
                    success_count += 1
                    self.stdout.write(
                        self.style.SUCCESS(
                            f'✓ Sent reminder for booking {booking.id}: {booking.client.email} & {booking.consultant.email}'
                        )
                    )
                else:
                    fail_count += 1
                    self.stdout.write(
                        self.style.ERROR(
                            f'✗ Failed to send reminder for booking {booking.id}'
                        )
                    )
            except Exception as e:
                fail_count += 1
                self.stdout.write(
                    self.style.ERROR(
                        f'✗ Error sending reminder for booking {booking.id}: {str(e)}'
                    )
                )
                logger.error(f'Error sending reminder for booking {booking.id}: {str(e)}')
        
        # Summary
        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS(f'Summary: {success_count} sent, {fail_count} failed'))
        
        if success_count > 0:
            logger.info(f'Sent {success_count} reminder email(s) for {tomorrow}')
