from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta
from notifications.models import Notification

class Command(BaseCommand):
    help = 'Deletes read notifications older than 30 days'

    def handle(self, *args, **options):
        # Calculate the date 30 days ago
        cutoff_date = timezone.now() - timedelta(days=30)
        
        # Count items before deletion for logging
        old_notifications = Notification.objects.filter(
            is_read=True, 
            created_at__lt=cutoff_date
        )
        count = old_notifications.count()
        
        # Perform deletion
        old_notifications.delete()
        
        self.stdout.write(
            self.style.SUCCESS(f'Successfully deleted {count} read notifications older than {cutoff_date}')
        )
