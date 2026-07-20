from django.core.management.base import BaseCommand
from the_keep.models import Faction, Vagabond
from the_gatehouse.models import Profile
from the_warroom.services.winrate_service import CACHED_FIELDS, _apply_cached_stats


class Command(BaseCommand):
    help = 'Recalculate and cache winrates for Factions, Vagabonds, and Profiles'

    def add_arguments(self, parser):
        parser.add_argument(
            '--model',
            choices=['faction', 'vagabond', 'profile', 'all'],
            default='all',
            help='Which model to recalculate (default: all)',
        )
        parser.add_argument(
            '--batch-size',
            type=int,
            default=500,
            help='Rows per bulk_update batch (default: 500)',
        )

    def _recalc(self, model, label, batch_size):
        """Compute cached fields for every row of model and persist in batches.

        Uses bulk_update instead of per-row save() to stay light on the
        database during a full backfill. These cached fields don't drive
        signals, so skipping save()/signals is safe.
        """
        qs = model.objects.all()
        count = qs.count()
        self.stdout.write(f'Recalculating winrates for {count} {label}...')

        batch = []
        processed = 0
        for obj in qs.iterator():
            _apply_cached_stats(obj)
            batch.append(obj)
            processed += 1
            if len(batch) >= batch_size:
                model.objects.bulk_update(batch, CACHED_FIELDS, batch_size=batch_size)
                batch = []
                self.stdout.write(f'  {processed}/{count}')
        if batch:
            model.objects.bulk_update(batch, CACHED_FIELDS, batch_size=batch_size)

        self.stdout.write(self.style.SUCCESS(f'Done: {count} {label} updated.'))

    def handle(self, *args, **options):
        model = options['model']
        batch_size = options['batch_size']

        if model in ('faction', 'all'):
            self._recalc(Faction, 'factions', batch_size)

        if model in ('vagabond', 'all'):
            self._recalc(Vagabond, 'vagabonds', batch_size)

        if model in ('profile', 'all'):
            self._recalc(Profile, 'profiles', batch_size)
