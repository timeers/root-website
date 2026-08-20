from django.core.management.base import BaseCommand
from django.db.models import Q

from the_warroom.models import Game, SeatDraftOption
from the_warroom.services.draft_service import refresh_game_draft_options


class Command(BaseCommand):
    help = "Recalculate and cache SeatDraftOption rows (each seat's faction draft pool)."

    def add_arguments(self, parser):
        parser.add_argument(
            '--chunk-size',
            type=int,
            default=500,
            help='Rows per iterator chunk (default: 500)',
        )
        parser.add_argument(
            '--drafted-only',
            action='store_true',
            help=(
                'Only process games with an undrafted option. WARNING: skips the clearing '
                'pass, so games edited to remove their undrafted option keep stale rows.'
            ),
        )

    def handle(self, *args, **options):
        chunk_size = options['chunk_size']
        drafted_only = options['drafted_only']

        qs = Game.objects.all()
        if drafted_only:
            qs = qs.filter(
                Q(undrafted_faction__isnull=False) | Q(undrafted_vagabond__isnull=False)
            )
        count = qs.count()
        label = 'drafted games' if drafted_only else 'games'
        self.stdout.write(f'Recalculating draft options for {count} {label}...')

        # iterator(chunk_size=...) is required for prefetch_related to apply under
        # iterator() and keeps ~18.7k games + ~74k efforts out of memory at once.
        qs = (qs.select_related('undrafted_faction', 'undrafted_vagabond')
                .prefetch_related('efforts__draft_options'))

        processed = 0
        updated = 0
        for game in qs.iterator(chunk_size=chunk_size):
            # refresh_game_draft_options wraps its own writes in transaction.atomic(),
            # so each game commits independently rather than one giant transaction.
            updated += 1 if refresh_game_draft_options(game) else 0
            processed += 1
            if processed % chunk_size == 0:
                self.stdout.write(f'  {processed}/{count}')

        total_rows = SeatDraftOption.objects.count()
        self.stdout.write(self.style.SUCCESS(
            f'Done: {processed} {label} checked, {updated} updated, '
            f'{total_rows} draft option rows cached.'
        ))
