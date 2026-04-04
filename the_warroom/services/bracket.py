"""
Service layer for tournament bracket generation.
Handles building match + advancement structures for different bracket formats.
"""
import math

from django.db import transaction
from django.utils import timezone

from the_warroom.models import (
    CompetitionStatus,
    Match,
    MatchSeat,
    MatchSeries,
    Round,
    Stage,
    StageParticipant,
    TournamentPlayer,
)


class BracketService:

    # ── Round-level bracket generation ──────────────────────────────

    @classmethod
    @transaction.atomic
    def generate_round_bracket(cls, round, best_of=1, create_byes=False):
        """
        Generate MatchSeries, Matches, and MatchSeats
        for a single round based on its finalized PlayerGroups.

        Args:
            round: Round instance (must have grouping_status == FINALIZED)
            best_of: Number of games per match series (1, 3, 5, 7)
            create_byes: If True, create bye series for ungrouped players.

        Returns:
            list[str]: warning messages about constraint violations
        """
        if round.grouping_status != Round.GroupingStatusChoices.FINALIZED:
            raise ValueError("Round grouping must be finalized before generating a bracket.")

        if round.bracket_status == Round.BracketStatusChoices.FINALIZED:
            raise ValueError("Bracket has been finalized and cannot be regenerated.")

        groups = list(round.player_groups.order_by('group_number'))
        if not groups:
            raise ValueError("Round has no player groups. Generate groups first.")

        # Clear existing bracket data for this round (idempotent)
        Match.objects.filter(round=round).delete()
        MatchSeries.objects.filter(round=round).delete()

        min_per_match = round.get_min_players()
        max_per_match = round.get_max_players()
        warnings = []

        for group in groups:
            player_count = group.tournament_players.count()

            # Validate player counts
            if max_per_match and player_count > max_per_match:
                warnings.append(
                    f"{group.name or f'Group {group.group_number}'} has {player_count} player(s), "
                    f"above maximum of {max_per_match}."
                )
            elif min_per_match and player_count < min_per_match:
                warnings.append(
                    f"{group.name or f'Group {group.group_number}'} has {player_count} player(s), "
                    f"below minimum of {min_per_match}."
                )

            # Create the series and matches
            series = MatchSeries.objects.create(
                round=round,
                player_group=group,
                number_of_games=best_of,
            )

            for game_num in range(best_of):
                Match.objects.create(round=round, series=series)

            # Create MatchSeat records for the series
            if round.stage_id:
                for i, tp in enumerate(group.tournament_players.all()):
                    sp = StageParticipant.objects.filter(
                        tournament_player=tp,
                        stage=round.stage,
                    ).first()
                    if sp:
                        MatchSeat.objects.create(
                            series=series,
                            stage_participant=sp,
                            seat_number=i + 1,
                        )

        # ── Bye series for ungrouped players ────────────────────────
        if create_byes and round.stage_id:
            grouped_tp_ids = set(
                TournamentPlayer.objects.filter(
                    player_groups__round=round
                ).values_list('id', flat=True)
            )
            ungrouped_tps = TournamentPlayer.objects.filter(
                tournament=round.get_tournament(),
                stage_participations__stage=round.stage,
                stage_participations__status=StageParticipant.ParticipantStatus.ACTIVE,
            ).exclude(id__in=grouped_tp_ids)

            for tp in ungrouped_tps:
                sp = StageParticipant.objects.filter(
                    tournament_player=tp, stage=round.stage,
                ).first()
                if not sp:
                    continue

                bye_series = MatchSeries.objects.create(
                    round=round,
                    name=f"{tp.profile.display_name}",
                    is_bye=True,
                    number_of_games=0,
                    status=CompetitionStatus.COMPLETED,
                )
                bye_series.winners.add(sp)

                Match.objects.create(
                    round=round,
                    series=bye_series,
                    status=CompetitionStatus.COMPLETED,
                )
                MatchSeat.objects.create(
                    series=bye_series,
                    stage_participant=sp,
                    seat_number=1,
                )

        return warnings

    # ── Advancement logic ───────────────────────────────────────────

    @classmethod
    @transaction.atomic
    def on_game_complete(cls, match):
        """
        Called when a Game linked to a Match is finalized.
        Updates match/series status, determines the series winner,
        and triggers advancement.
        """
        import time
        import logging
        logger = logging.getLogger(__name__)
        t0 = time.time()

        match.status = CompetitionStatus.COMPLETED
        match.save(update_fields=['status'])
        logger.warning(f"[on_game_complete] match.save: {time.time()-t0:.3f}s")

        series = match.series
        best_of = series.number_of_games

        if best_of <= 1:
            # Single game — winners are whoever won this game
            winner_profiles = match.winners
            if winner_profiles.exists():
                cls._set_series_winners(series, winner_profiles)
        else:
            # Best-of-X — check if any player has reached the win threshold
            wins_needed = math.ceil(best_of / 2)
            # Single query: count wins per player across all completed games in the series
            from django.db.models import Count
            from the_gatehouse.models import Profile
            win_counts = (
                Profile.objects.filter(
                    efforts__game__match__series=series,
                    efforts__game__match__status=CompetitionStatus.COMPLETED,
                    efforts__win=True,
                )
                .annotate(win_count=Count('efforts'))
                .filter(win_count__gte=wins_needed)
            )
            if win_counts.exists():
                cls._set_series_winners(series, win_counts)
        logger.warning(f"[on_game_complete] after set_series_winners: {time.time()-t0:.3f}s")

        # Check if this round is now complete
        if series.is_complete():
            cls._check_round_complete(match.round)
            logger.warning(f"[on_game_complete] after _check_round_complete: {time.time()-t0:.3f}s")
        else:
            # Else mark as in progress / active
            series.status = CompetitionStatus.ACTIVE
            series.save(update_fields=['status'])

        # Activate any pending parent objects (only if they're active)
        round_obj = match.round
        stage = round_obj.stage
        tournament = stage.tournament if stage else None
        for obj in (round_obj, stage, tournament):
            if obj and obj.status == CompetitionStatus.PENDING and obj.is_active:
                obj.status = CompetitionStatus.ACTIVE
                obj.save(update_fields=['status'])
        logger.warning(f"[on_game_complete] total: {time.time()-t0:.3f}s")


    @classmethod
    def _set_series_winners(cls, series, winner_profiles):
        """Set the series winners from Profile queryset/list to StageParticipants."""
        stage = series.round.stage
        if not stage:
            return

        series.winners.clear()

        for winner_profile in winner_profiles:
            sp = StageParticipant.objects.filter(
                stage=stage,
                tournament_player__profile=winner_profile,
            ).first()
            if sp:
                series.winners.add(sp)

        if series.winners.exists():
            series.status = CompetitionStatus.COMPLETED
            series.save(update_fields=['status'])

    @classmethod
    @transaction.atomic
    def _check_round_complete(cls, round):
        """
        Check if all match series in the round are complete.
        If so, mark the round and potentially the stage as completed.
        """
        all_series = list(round.series.prefetch_related('winners', 'matches').all())
        if not all_series:
            return

        if all(s.is_complete() for s in all_series):
            round.status = CompetitionStatus.COMPLETED
            round.save(update_fields=['status'])

            # Check if this was the last round in the stage
            if round.stage_id:
                remaining = round.stage.rounds.exclude(
                    status=CompetitionStatus.COMPLETED,
                )
                if not remaining.exists():
                    round.stage.status = CompetitionStatus.COMPLETED
                    round.stage.save(update_fields=['status'])
