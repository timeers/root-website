from datetime import datetime, timezone as dt_timezone

from django.core.management.base import BaseCommand
from dateutil import parser as date_parser

from the_warroom.models import EloSystem
from the_warroom.services.elo_service import recompute_system_from


class Command(BaseCommand):
    help = "Recompute local Elo ratings from scratch (or from a given date) for LOCAL systems."

    def add_arguments(self, parser):
        parser.add_argument(
            '--system',
            help='Restrict to one EloSystem by slug or id (default: all LOCAL systems).',
        )
        parser.add_argument(
            '--from',
            dest='from_iso',
            help='Replay from this ISO datetime (default: from the beginning).',
        )

    def _resolve_systems(self, system_arg):
        qs = EloSystem.objects.filter(calculation_type=EloSystem.CalculationType.LOCAL)
        if not system_arg:
            return qs
        if system_arg.isdigit():
            return qs.filter(pk=int(system_arg))
        return qs.filter(slug=system_arg)

    def handle(self, *args, **options):
        systems = list(self._resolve_systems(options['system']))
        if not systems:
            self.stdout.write(self.style.WARNING('No matching LOCAL EloSystems.'))
            return

        if options['from_iso']:
            cutoff = date_parser.parse(options['from_iso'])
        else:
            # Epoch — earlier than any game, so the whole history is rebuilt.
            cutoff = datetime(1970, 1, 1, tzinfo=dt_timezone.utc)

        total = len(systems)
        self.stdout.write(f'Recomputing elo for {total} LOCAL system(s) from {cutoff.isoformat()}...')
        for i, system in enumerate(systems, 1):
            recompute_system_from(system, cutoff)
            # Clear the dirty marker — this rebuild covers everything from `cutoff`.
            EloSystem.objects.filter(pk=system.pk).update(recompute_from=None)
            self.stdout.write(f'  {i}/{total}  {system.name}')

        self.stdout.write(self.style.SUCCESS(f'Done: {total} system(s) recomputed.'))
