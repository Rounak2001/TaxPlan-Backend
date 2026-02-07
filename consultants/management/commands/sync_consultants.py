from django.core.management.base import BaseCommand
from consultants.models import ClientServiceRequest
from core_auth.models import ClientProfile


class Command(BaseCommand):
    help = 'Sync existing ClientServiceRequest assignments to ClientProfile'

    def handle(self, *args, **options):
        """
        Backfill existing service requests with assigned consultants
        to update ClientProfile.assigned_consultant
        """
        self.stdout.write("Starting consultant assignment sync...")
        
        # Get all service requests with assigned consultants
        requests = ClientServiceRequest.objects.filter(
            assigned_consultant__isnull=False
        ).select_related('client', 'assigned_consultant').order_by('assigned_at')
        
        synced_count = 0
        created_count = 0
        
        for request in requests:
            client_profile, created = ClientProfile.objects.get_or_create(
                user=request.client,
                defaults={'assigned_consultant': request.assigned_consultant.user}
            )
            
            if created:
                created_count += 1
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Created profile for {request.client.email} -> {request.assigned_consultant.full_name}"
                    )
                )
            else:
                # Update to most recent assignment
                if client_profile.assigned_consultant != request.assigned_consultant.user:
                    client_profile.assigned_consultant = request.assigned_consultant.user
                    client_profile.save()
                    synced_count += 1
                    self.stdout.write(
                        self.style.SUCCESS(
                            f"Updated {request.client.email} -> {request.assigned_consultant.full_name}"
                        )
                    )
        
        self.stdout.write(
            self.style.SUCCESS(
                f"\nSync complete! Created: {created_count}, Updated: {synced_count}"
            )
        )
