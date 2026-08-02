from django.core.management.base import BaseCommand
from django.db.models import Count
from the_warroom.models import Game


class Command(BaseCommand):
    help = "Recalculate and cache Game.cached_player_count from each game's efforts."

    def add_arguments(self, parser):
        parser.add_argument(
            '--batch-size',
            type=int,
            default=500,
            help='Rows per bulk_update batch (default: 500)',
        )

    def handle(self, *args, **options):
        batch_size = options['batch_size']
        count = Game.objects.count()
        self.stdout.write(f'Recalculating player counts for {count} games...')

        # Annotate the true count so we only touch rows that are stale.
        qs = Game.objects.annotate(_n=Count('efforts')).iterator()
        batch = []
        processed = 0
        for game in qs:
            if game.cached_player_count != game._n:
                game.cached_player_count = game._n
                batch.append(game)
            processed += 1
            if len(batch) >= batch_size:
                Game.objects.bulk_update(batch, ['cached_player_count'], batch_size=batch_size)
                batch = []
                self.stdout.write(f'  {processed}/{count}')
        if batch:
            Game.objects.bulk_update(batch, ['cached_player_count'], batch_size=batch_size)

        self.stdout.write(self.style.SUCCESS(f'Done: {count} games checked.'))
