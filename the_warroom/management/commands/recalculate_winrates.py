from django.core.management.base import BaseCommand
from the_keep.models import Faction, Vagabond
from the_gatehouse.models import Profile
from the_warroom.services.winrate_service import calculate_and_cache_winrate


class Command(BaseCommand):
    help = 'Recalculate and cache winrates for Factions, Vagabonds, and Profiles'

    def add_arguments(self, parser):
        parser.add_argument(
            '--model',
            choices=['faction', 'vagabond', 'profile', 'all'],
            default='all',
            help='Which model to recalculate (default: all)',
        )

    def handle(self, *args, **options):
        model = options['model']

        if model in ('faction', 'all'):
            factions = Faction.objects.all()
            count = factions.count()
            self.stdout.write(f'Recalculating winrates for {count} factions...')
            for i, faction in enumerate(factions, 1):
                calculate_and_cache_winrate(faction)
                if i % 50 == 0:
                    self.stdout.write(f'  {i}/{count}')
            self.stdout.write(self.style.SUCCESS(f'Done: {count} factions updated.'))

        if model in ('vagabond', 'all'):
            vagabonds = Vagabond.objects.all()
            count = vagabonds.count()
            self.stdout.write(f'Recalculating winrates for {count} vagabonds...')
            for i, vagabond in enumerate(vagabonds, 1):
                calculate_and_cache_winrate(vagabond)
                if i % 50 == 0:
                    self.stdout.write(f'  {i}/{count}')
            self.stdout.write(self.style.SUCCESS(f'Done: {count} vagabonds updated.'))

        if model in ('profile', 'all'):
            profiles = Profile.objects.all()
            count = profiles.count()
            self.stdout.write(f'Recalculating winrates for {count} profiles...')
            for i, profile in enumerate(profiles, 1):
                calculate_and_cache_winrate(profile)
                if i % 100 == 0:
                    self.stdout.write(f'  {i}/{count}')
            self.stdout.write(self.style.SUCCESS(f'Done: {count} profiles updated.'))
