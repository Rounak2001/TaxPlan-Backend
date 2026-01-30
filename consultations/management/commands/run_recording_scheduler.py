import time
import logging
from datetime import datetime, timedelta
from django.core.management.base import BaseCommand
from django.utils import timezone
from consultations.models import ConsultationBooking
from consultations.utils import trigger_recording_bot

logger = logging.getLogger(__name__)

class Command(BaseCommand):
    help = 'Automatically monitors and triggers the recording bot for upcoming meetings.'

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS('Recording Scheduler Started. Monitoring meetings...'))
        
        while True:
            try:
                self.check_and_trigger_meetings()
            except Exception as e:
                self.stdout.write(self.style.ERROR(f'Error in scheduler loop: {str(e)}'))
            
            # Check every 30 seconds
            time.sleep(30)

    def check_and_trigger_meetings(self):
        # Current time in local timezone
        now = timezone.localtime()
        current_date = now.date()
        
        # Trigger meetings strictly when the start time has arrived
        trigger_window = now
        
        upcoming_bookings = ConsultationBooking.objects.filter(
            status='confirmed',
            meeting_link__isnull=False,
            bot_triggered=False,
            booking_date=current_date,
            start_time__lte=trigger_window.time()
        )

        for booking in upcoming_bookings:
            # Check if it hasn't ended already (safety)
            if booking.end_time < now.time():
                continue

            self.stdout.write(f'Auto-Triggering bot for meeting: {booking.id} ({booking.topic.name})')
            
            success = trigger_recording_bot(booking.meeting_link, booking_id=booking.id)
            if success:
                booking.bot_triggered = True
                booking.save(update_fields=['bot_triggered'])
                self.stdout.write(self.style.SUCCESS(f'Successfully triggered bot for booking {booking.id}'))
            else:
                self.stdout.write(self.style.ERROR(f'Failed to trigger bot for booking {booking.id}'))
