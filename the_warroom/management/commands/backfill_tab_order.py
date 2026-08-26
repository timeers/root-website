from django.core.management.base import BaseCommand

from the_warroom.models import Tournament


class Command(BaseCommand):
    help = (
        "Populate Tournament.tab_order from the legacy hidden_tabs field. Overview "
        "was never hideable, so it lands first and navs are unchanged. Safe to "
        "re-run; only fills rows with an empty tab_order. Dry-run by default; "
        "pass --apply to write."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--apply',
            action='store_true',
            help='Actually write. Without this the command only reports.',
        )

    def handle(self, *args, **options):
        apply = options['apply']

        # Filtering in Python rather than with a JSONField lookup: __contains and
        # friends are Postgres-only and break on the SQLite dev DB. The Tournament
        # table is small enough that materializing it costs nothing.
        pending = [
            t for t in Tournament.objects.only('id', 'name', 'tab_order', 'hidden_tabs')
            if not t.tab_order
        ]

        if not pending:
            self.stdout.write(self.style.SUCCESS('All tournaments already have tab_order set.'))
            return

        self.stdout.write(f'{len(pending)} tournament(s) need tab_order populated.')

        for t in pending:
            hidden = t.hidden_tabs or []
            order = [k for k in Tournament.NAV_TABS if k not in hidden]
            self.stdout.write(f'  {t.pk} ({t.name}): {order}')
            if apply:
                t.tab_order = order
                t.save(update_fields=['tab_order'])

        if apply:
            self.stdout.write(self.style.SUCCESS(f'Updated {len(pending)} tournament(s).'))
        else:
            self.stdout.write(self.style.WARNING('Dry run — pass --apply to write.'))
