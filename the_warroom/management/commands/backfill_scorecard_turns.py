from collections import defaultdict

from django.core.management.base import BaseCommand

from the_warroom.models import ScoreCard, TurnScore


# Point fields copied verbatim from each TurnScore row into the JSON dict.
COPY_FIELDS = (
    'battle_points', 'crafting_points', 'faction_points',
    'other_points', 'generic_points', 'total_points', 'game_points_total',
)
DETAIL_FIELDS = ('battle_points', 'crafting_points', 'faction_points', 'other_points')


def build_turns(rows):
    """Turn a scorecard's ordered TurnScore rows into the turns_data dict list.

    Stored ``game_points_total`` is copied verbatim (no recomputation) so the
    backfill produces zero display change even where legacy cumulative values
    have drifted from cumsum(total_points).
    """
    turns = []
    for t in rows:
        turn = {f: getattr(t, f) for f in COPY_FIELDS}
        turn['turn_number'] = t.turn_number
        turn['dominance'] = bool(t.dominance)
        turns.append(turn)
    return turns


class Command(BaseCommand):
    help = 'Backfill ScoreCard.turns_data / is_detailed from the TurnScore rows.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Report what would change (and cumulative drift) without writing.',
        )
        parser.add_argument(
            '--batch-size',
            type=int,
            default=500,
            help='Rows per bulk_update batch (default: 500).',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        batch_size = options['batch_size']

        # Group every TurnScore by scorecard, ordered by turn_number.
        turns_by_card = defaultdict(list)
        for t in TurnScore.objects.filter(scorecard__isnull=False).order_by(
            'scorecard_id', 'turn_number'
        ).iterator():
            turns_by_card[t.scorecard_id].append(t)

        total = ScoreCard.objects.count()
        self.stdout.write(f'Backfilling turns_data for {total} scorecards...')

        drift = []          # (scorecard_id, turn_number, stored, recomputed)
        detail_flips = []   # scorecard_ids whose is_detailed would change
        batch = []
        processed = 0

        for card in ScoreCard.objects.all().iterator():
            rows = turns_by_card.get(card.id, [])
            turns = build_turns(rows)

            # Report cumulative drift: stored game_points_total vs cumsum(total_points).
            running = 0
            for turn in turns:
                running += turn['total_points']
                if turn['game_points_total'] != running:
                    drift.append((card.id, turn['turn_number'], turn['game_points_total'], running))

            is_detailed = any(any(turn[f] for f in DETAIL_FIELDS) for turn in turns)
            if is_detailed != card.is_detailed:
                detail_flips.append(card.id)

            card.turns_data = turns
            card.is_detailed = is_detailed
            batch.append(card)
            processed += 1

            if not dry_run and len(batch) >= batch_size:
                ScoreCard.objects.bulk_update(batch, ['turns_data', 'is_detailed'], batch_size=batch_size)
                batch = []
                self.stdout.write(f'  {processed}/{total}')

        if not dry_run and batch:
            ScoreCard.objects.bulk_update(batch, ['turns_data', 'is_detailed'], batch_size=batch_size)

        # Summary
        if drift:
            self.stdout.write(self.style.WARNING(
                f'{len(drift)} turn(s) have game_points_total drift '
                f'(stored != cumsum(total_points)). First 20:'
            ))
            for card_id, turn_no, stored, recomputed in drift[:20]:
                self.stdout.write(f'  scorecard {card_id} turn {turn_no}: stored={stored} cumsum={recomputed}')
        else:
            self.stdout.write(self.style.SUCCESS('No cumulative drift detected.'))

        if detail_flips:
            self.stdout.write(
                f'{len(detail_flips)} scorecard(s) would change is_detailed.'
            )

        if dry_run:
            self.stdout.write(self.style.SUCCESS(f'Dry run complete: {processed} scorecards inspected (no writes).'))
        else:
            self.stdout.write(self.style.SUCCESS(f'Done: {processed} scorecards backfilled.'))
