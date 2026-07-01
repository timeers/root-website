from collections import defaultdict

from django.core.management.base import BaseCommand
from django.db import transaction

from the_warroom.models import StageParticipant, MatchSeat


class Command(BaseCommand):
    help = (
        "Find and merge duplicate StageParticipants (same stage + tournament_player). "
        "These can be left behind by an earlier faulty profile merge, causing a player "
        "to appear twice in a single stage. MatchSeat children are folded onto the kept "
        "row before duplicates are deleted. Dry-run by default; pass --apply to write."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--apply',
            action='store_true',
            help='Actually delete duplicates. Without this flag the command only reports.',
        )

    def handle(self, *args, **options):
        apply = options['apply']

        # Group participants by (stage, tournament_player); any group with >1 row
        # is a duplicate set that should collapse to a single participant.
        groups = defaultdict(list)
        for sp in StageParticipant.objects.all().order_by('pk'):
            groups[(sp.stage_id, sp.tournament_player_id)].append(sp)

        dup_groups = {k: v for k, v in groups.items() if len(v) > 1}

        if not dup_groups:
            self.stdout.write(self.style.SUCCESS('No duplicate StageParticipants found.'))
            return

        self.stdout.write(
            f'Found {len(dup_groups)} duplicated (stage, tournament_player) group(s).'
        )

        total_removed = 0
        total_seats_moved = 0
        total_seats_dropped = 0
        total_wins_moved = 0

        for (stage_id, tp_id), rows in dup_groups.items():
            # Keep the lowest-pk row; fold the rest into it.
            keeper = rows[0]
            losers = rows[1:]
            sample = rows[0]
            self.stdout.write(
                f'  Stage {stage_id} / TournamentPlayer {tp_id} '
                f'({sample.tournament_player.profile.display_name}): '
                f'{len(rows)} rows -> keep pk={keeper.pk}, '
                f'remove {[l.pk for l in losers]}'
            )

            for loser in losers:
                for seat in MatchSeat.objects.filter(stage_participant=loser):
                    if MatchSeat.objects.filter(
                        series_id=seat.series_id, stage_participant=keeper
                    ).exists():
                        total_seats_dropped += 1
                        if apply:
                            seat.delete()
                    else:
                        total_seats_moved += 1
                        if apply:
                            seat.stage_participant = keeper
                            seat.save(update_fields=['stage_participant'])
                # Transfer any "series winner" records off the loser before it is
                # deleted, otherwise the through-row CASCADEs away and the win is lost.
                for series in loser.won_series.all():
                    total_wins_moved += 1
                    if apply:
                        series.winners.remove(loser)
                        if not series.winners.filter(pk=keeper.pk).exists():
                            series.winners.add(keeper)
                total_removed += 1
                if apply:
                    loser.delete()

        summary = (
            f'{total_removed} duplicate participant(s), '
            f'{total_seats_moved} seat(s) re-pointed, '
            f'{total_seats_dropped} redundant seat(s) dropped, '
            f'{total_wins_moved} series-win record(s) transferred'
        )
        if apply:
            self.stdout.write(self.style.SUCCESS(f'Applied: removed {summary}.'))
        else:
            self.stdout.write(self.style.WARNING(
                f'Dry run: would remove {summary}. Re-run with --apply to commit.'
            ))

    # Wrap the write path in a transaction only when applying.
    def execute(self, *args, **options):
        if options.get('apply'):
            with transaction.atomic():
                return super().execute(*args, **options)
        return super().execute(*args, **options)
