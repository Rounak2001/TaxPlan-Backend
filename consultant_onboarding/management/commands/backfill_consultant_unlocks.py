from django.core.management.base import BaseCommand

from consultant_onboarding.models import ConsultantApplication
from consultant_onboarding.unlock_ops import (
    ensure_unlock_from_completed_sessions,
    force_unlock_all_main_categories,
)


class Command(BaseCommand):
    help = "Backfill consultant unlock categories for old accounts."

    def add_arguments(self, parser):
        parser.add_argument(
            "--all-applications",
            action="store_true",
            help="Include applications without generated credentials.",
        )
        parser.add_argument(
            "--force-all",
            action="store_true",
            help="Force unlock ITR/GSTR/Scrutiny for each target consultant.",
        )
        parser.add_argument(
            "--email",
            type=str,
            default="",
            help="Process only one consultant application by exact email.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would change without writing to DB.",
        )

    def handle(self, *args, **options):
        queryset = ConsultantApplication.objects.all().order_by("id")
        if not options["all_applications"]:
            queryset = queryset.filter(credentials__isnull=False)

        email = str(options.get("email") or "").strip().lower()
        if email:
            queryset = queryset.filter(email__iexact=email)

        dry_run = bool(options.get("dry_run"))
        force_all = bool(options.get("force_all"))

        total = queryset.count()
        changed = 0
        skipped = 0

        self.stdout.write(self.style.NOTICE(f"Processing {total} consultant application(s)..."))

        for app in queryset:
            if dry_run:
                self.stdout.write(f"DRY RUN: would process {app.id} | {app.email}")
                continue

            updated = (
                force_unlock_all_main_categories(app)
                if force_all
                else ensure_unlock_from_completed_sessions(app)
            )

            if updated:
                changed += 1
                self.stdout.write(self.style.SUCCESS(f"Updated {app.id} | {app.email}"))
            else:
                skipped += 1
                self.stdout.write(f"Skipped {app.id} | {app.email}")

        if dry_run:
            self.stdout.write(self.style.WARNING("Dry run complete. No changes were written."))
            return

        self.stdout.write(
            self.style.SUCCESS(
                f"Done. Updated={changed}, Skipped={skipped}, Total={total}"
            )
        )
