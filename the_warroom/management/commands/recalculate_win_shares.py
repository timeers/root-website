from django.core.management.base import BaseCommand

from the_warroom.models import Game
from the_warroom.services.win_share_service import refresh_game_win_shares


class Command(BaseCommand):
    help = "Recalculate Effort.win_share (each effort's share of its game's win)."

    def add_arguments(self, parser):
        parser.add_argument(
            '--chunk-size',
            type=int,
            default=500,
            help='Rows per iterator chunk (default: 500)',
        )

    def handle(self, *args, **options):
        chunk_size = options['chunk_size']

        qs = Game.objects.only('pk')
        count = qs.count()
        self.stdout.write(f'Recalculating win shares for {count} games...')

        processed = 0
        updated = 0
        for game in qs.iterator(chunk_size=chunk_size):
            # refresh_game_win_shares wraps its own writes in transaction.atomic(),
            # so each game commits independently rather than one giant transaction.
            updated += refresh_game_win_shares(game)
            processed += 1
            if processed % chunk_size == 0:
                self.stdout.write(f'  {processed}/{count}')

        self.stdout.write(self.style.SUCCESS(
            f'Done: {processed} games checked, {updated} efforts updated.'
        ))
