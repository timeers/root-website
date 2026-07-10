from django.core.management.base import BaseCommand
from the_warroom.models import Tournament


CACHED_FIELDS = ['cached_game_count', 'cached_player_count']


class Command(BaseCommand):
    help = 'Recalculate and cache game/player counts for all Tournaments (series)'

    def add_arguments(self, parser):
        parser.add_argument(
            '--batch-size',
            type=int,
            default=500,
            help='Rows per bulk_update batch (default: 500)',
        )

    def handle(self, *args, **options):
        batch_size = options['batch_size']

        qs = Tournament.objects.all()
        count = qs.count()
        self.stdout.write(f'Recalculating counts for {count} tournaments...')

        # bulk_update instead of per-row save() to stay light on the database
        # during a full backfill. These cached fields don't drive signals, so
        # skipping save()/signals is safe.
        batch = []
        processed = 0
        for tournament in qs.iterator():
            tournament.cached_game_count = tournament.game_count()
            tournament.cached_player_count = tournament.all_player_count()
            batch.append(tournament)
            processed += 1
            if len(batch) >= batch_size:
                Tournament.objects.bulk_update(batch, CACHED_FIELDS, batch_size=batch_size)
                batch = []
                self.stdout.write(f'  {processed}/{count}')
        if batch:
            Tournament.objects.bulk_update(batch, CACHED_FIELDS, batch_size=batch_size)

        self.stdout.write(self.style.SUCCESS(f'Done: {count} tournaments updated.'))
