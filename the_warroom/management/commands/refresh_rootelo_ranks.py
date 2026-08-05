from django.core.management.base import BaseCommand

from the_warroom.tasks import refresh_rootelo_ranks_impl


class Command(BaseCommand):
    help = (
        "Refresh EloParticipant rows for every ROOTELO EloSystem that has an "
        "api_url, by fetching its live_elo feed. Worker-free entry point for the "
        "refresh_rootelo_ranks Celery task."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Fetch and match without writing any EloParticipant rows.',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        results = refresh_rootelo_ranks_impl(dry_run=dry_run)

        prefix = '[dry-run] ' if dry_run else ''
        if not results:
            self.stdout.write(self.style.WARNING(
                'No ROOTELO EloSystem with an api_url found; nothing to refresh.'
            ))
            return

        for r in results:
            if r.get('error'):
                self.stdout.write(self.style.ERROR(
                    f"{prefix}{r['system']}: fetch failed (no changes written)."
                ))
            else:
                self.stdout.write(self.style.SUCCESS(
                    f"{prefix}{r['system']}: matched {r['matched']}, "
                    f"created {r['created']}, updated {r['updated']}."
                ))
