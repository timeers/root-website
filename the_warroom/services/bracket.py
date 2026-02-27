"""
Service layer for tournament bracket generation.
Handles building match + advancement structures for different bracket formats.
"""
import math

from django.db import transaction
from django.utils import timezone

from the_warroom.models import (
    CompetitionStatus,
    FormatChoices,
    Match,
    MatchAdvancement,
    MatchSeat,
    MatchSeries,
    Round,
    Stage,
    StageParticipant,
    TournamentPlayer,
)
from the_warroom.services.grouping import GroupingService


class BracketService:

    @classmethod
    @transaction.atomic
    def generate_bracket(cls, tournament, stage_rounds):
        """
        Generate matches + advancements for all stages of a bracket.

        For each stage, dispatches to the appropriate per-format builder based on
        that stage's get_format(). Bracket generation is idempotent — existing
        matches for all affected stages are deleted before recreating.

        Args:
            tournament: Tournament instance
            stage_rounds: ordered list of Round instances (Stage 1 first)

        Returns:
            list[str]: warning messages about player count constraint violations
        """
        if not stage_rounds:
            raise ValueError("At least one stage round is required.")

        # Seed Stage 1: ensure it has PlayerGroups
        first_round = stage_rounds[0]
        if not first_round.player_groups.exists() and first_round.stage_id:
            GroupingService.generate_random_groups(first_round.stage, first_round)

        # Clear existing matches for all stages (idempotent re-generation)
        Match.objects.filter(round__in=stage_rounds).delete()
        MatchSeries.objects.filter(round__in=stage_rounds).delete()

        prev_match_count = None
        warnings = []

        for i, bracket_round in enumerate(stage_rounds):
            next_round = stage_rounds[i + 1] if i + 1 < len(stage_rounds) else None
            fmt = bracket_round.get_format()

            if fmt == FormatChoices.SINGLE_ELIM:
                prev_match_count, stage_warnings = cls._build_single_elim_stage(
                    bracket_round, next_round,
                    stage_number=i + 1,
                    is_first=(i == 0),
                    prev_match_count=prev_match_count,
                )
                warnings.extend(stage_warnings)
            else:
                raise ValueError(
                    f"No bracket builder implemented for format '{fmt}' "
                    f"(stage {i + 1}: {bracket_round}). "
                    f"Supported formats: {FormatChoices.SINGLE_ELIM}"
                )

        return warnings

    @classmethod
    def _build_single_elim_stage(cls, bracket_round, next_round, stage_number=1, is_first=False, prev_match_count=None):
        """
        Build matches + winner advancements for one single-elimination stage.

        Stage 1 (is_first=True): one MatchSeries+Match per existing PlayerGroup.
        Later stages: empty MatchSeries+Match shells; count determined by advancing
        player count and the round's min/max player constraints.
        Advancements: each series winner → to_stage (if not terminal).

        Returns:
            tuple[int, list[str]]: (number of matches created, warning messages)
        """
        min_per_match = bracket_round.get_min_players()
        max_per_match = bracket_round.get_max_players()
        warnings = []

        if is_first:
            groups = list(bracket_round.player_groups.order_by('group_number'))
            for group in groups:
                series = MatchSeries.objects.create(round=bracket_round, player_group=group)
                Match.objects.create(round=bracket_round, series=series)
                player_count = group.tournament_players.count()
                if max_per_match and player_count > max_per_match:
                    warnings.append(
                        f"Stage {stage_number}: {group.name} has {player_count} player(s), "
                        f"above maximum of {max_per_match}."
                    )
                elif min_per_match and player_count < min_per_match:
                    warnings.append(
                        f"Stage {stage_number}: {group.name} has {player_count} player(s), "
                        f"below minimum of {min_per_match}."
                    )
            match_count = len(groups)
        else:
            advancing = prev_match_count  # 1 winner per match
            match_count = cls._optimal_match_count(advancing, min_per_match, max_per_match)

            for _ in range(match_count):
                series = MatchSeries.objects.create(round=bracket_round)
                Match.objects.create(round=bracket_round, series=series)

            # Check if any matches would violate constraints
            base_size = advancing // match_count
            remainder = advancing % match_count
            smallest_match = base_size
            largest_match = base_size + (1 if remainder > 0 else 0)

            if max_per_match and largest_match > max_per_match:
                warnings.append(
                    f"Stage {stage_number}: Some matches will have {largest_match} player(s), "
                    f"above maximum of {max_per_match}."
                )
            if min_per_match and smallest_match < min_per_match:
                warnings.append(
                    f"Stage {stage_number}: Some matches will have {smallest_match} player(s), "
                    f"below minimum of {min_per_match}."
                )

        if next_round and next_round.stage_id:
            for ms in MatchSeries.objects.filter(round=bracket_round).order_by('id'):
                MatchAdvancement.objects.create(
                    from_series=ms,
                    position=MatchAdvancement.PositionChoices.WINNER,
                    to_stage=next_round.stage,
                )

        return match_count, warnings

    @staticmethod
    def _optimal_match_count(advancing, min_per_match, max_per_match):
        """
        Find the number of matches that best distributes advancing players
        within [min_per_match, max_per_match] per match.

        Tries all valid match counts and picks the one with zero or fewest
        leftover players. Falls back to ceil(advancing / max) if no perfect
        fit is found.
        """
        if not max_per_match or max_per_match <= 0:
            return advancing  # no constraint, 1 player per match

        lower = math.ceil(advancing / max_per_match)
        upper = math.floor(advancing / min_per_match) if min_per_match and min_per_match > 0 else advancing

        best = None
        best_leftover = advancing + 1

        for num_matches in range(max(1, lower), upper + 1):
            base_size = advancing // num_matches
            extra = advancing % num_matches

            if min_per_match and base_size < min_per_match:
                continue
            if max_per_match and base_size > max_per_match:
                continue
            if max_per_match and extra > 0 and base_size + 1 > max_per_match:
                continue

            leftover = 0
            if leftover < best_leftover:
                best_leftover = leftover
                best = num_matches

        if best is not None:
            return best

        # Fallback: use max_per_match even though some matches may be undersized
        return max(1, math.ceil(advancing / max_per_match))

    # ── Round-level bracket generation ──────────────────────────────

    @classmethod
    @transaction.atomic
    def generate_round_bracket(cls, round, best_of=1, losers_stage=None, winners_stage=None, create_byes=False):
        """
        Generate MatchSeries, Matches, MatchSeats, and MatchAdvancements
        for a single round based on its finalized PlayerGroups.

        Args:
            round: Round instance (must have grouping_status == FINALIZED)
            best_of: Number of games per match series (1, 3, 5, 7)
            losers_stage: Stage instance for double elimination loser advancement.
            winners_stage: Stage instance for explicit winner advancement.
                           If None, winners stay in the current stage.
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
        MatchAdvancement.objects.filter(from_series__round=round).delete()
        Match.objects.filter(round=round).delete()
        MatchSeries.objects.filter(round=round).delete()

        min_per_match = round.get_min_players()
        max_per_match = round.get_max_players()
        warnings = []

        # Seed losers stage with a first round if it doesn't have one
        if losers_stage and not losers_stage.rounds.exists():
            Round.objects.create(
                tournament=round.get_tournament(),
                stage=losers_stage,
                name=f"{losers_stage.name} Round 1",
                round_number=1,
                start_date=timezone.now(),
                min_players=round.get_min_players(),
                max_players=round.get_max_players(),
            )

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

            first_match = None
            for game_num in range(best_of):
                match = Match.objects.create(round=round, series=series)
                if game_num == 0:
                    first_match = match

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

            # Wire WINNER advancement (to explicit stage, if set)
            if winners_stage:
                MatchAdvancement.objects.create(
                    from_series=series,
                    position=MatchAdvancement.PositionChoices.WINNER,
                    to_stage=winners_stage,
                )

            # Wire LOSER advancement (double elimination)
            if losers_stage:
                MatchAdvancement.objects.create(
                    from_series=series,
                    position=MatchAdvancement.PositionChoices.LOSER,
                    to_stage=losers_stage,
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

                bye_match = Match.objects.create(
                    round=round,
                    series=bye_series,
                    status=CompetitionStatus.COMPLETED,
                )
                MatchSeat.objects.create(
                    series=bye_series,
                    stage_participant=sp,
                    seat_number=1,
                )

                if winners_stage:
                    MatchAdvancement.objects.create(
                        from_series=bye_series,
                        position=MatchAdvancement.PositionChoices.WINNER,
                        to_stage=winners_stage,
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
        match.status = CompetitionStatus.COMPLETED
        match.save(update_fields=['status'])

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
            completed_matches = series.matches.filter(
                status=CompetitionStatus.COMPLETED,
                game__isnull=False,
            )
            # Count wins per player across all completed games in the series
            from collections import Counter
            win_counts = Counter()
            for m in completed_matches:
                for profile in m.winners:
                    win_counts[profile.id] += 1

            from the_gatehouse.models import Profile
            threshold_winners = [
                Profile.objects.get(id=pid)
                for pid, count in win_counts.items()
                if count >= wins_needed
            ]
            if threshold_winners:
                cls._set_series_winners(series, threshold_winners)

        # Check if this round is now complete
        if series.is_complete():
            cls._process_advancement(series)
            cls._check_round_complete(match.round)
        else:
            # Else mark as in progress / active
            series.status = CompetitionStatus.ACTIVE
            series.save(update_fields=['status'])

        # Activate any pending parent objects
        round_obj = match.round
        stage = round_obj.stage
        tournament = stage.tournament if stage else None
        for obj in (round_obj, stage, tournament):
            if obj and obj.status == CompetitionStatus.PENDING:
                obj.status = CompetitionStatus.ACTIVE
                obj.save(update_fields=['status'])


    @classmethod
    def _set_series_winners(cls, series, winner_profiles):
        """Set the series winners from Profile queryset/list to StageParticipants."""
        stage = series.round.stage
        if not stage:
            return

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
    def _process_advancement(cls, series):
        """Process winner and loser advancements for a completed series."""
        if not series.winners.exists():
            return

        winner_tp_ids = set(
            series.winners.values_list('tournament_player_id', flat=True)
        )

        for advancement in series.advancements.all():
            if not advancement.to_stage:
                continue

            if advancement.position == MatchAdvancement.PositionChoices.WINNER:
                # Create StageParticipants for winners in the target stage
                for sp in series.winners.all():
                    StageParticipant.objects.get_or_create(
                        stage=advancement.to_stage,
                        tournament_player=sp.tournament_player,
                        defaults={'status': StageParticipant.ParticipantStatus.ACTIVE},
                    )
            elif advancement.position == MatchAdvancement.PositionChoices.LOSER:
                # Create StageParticipants for losers (non-winners) in the losers stage
                if series.player_group:
                    for tp in series.player_group.tournament_players.exclude(id__in=winner_tp_ids):
                        StageParticipant.objects.get_or_create(
                            stage=advancement.to_stage,
                            tournament_player=tp,
                            defaults={'status': StageParticipant.ParticipantStatus.ACTIVE},
                        )

    @classmethod
    @transaction.atomic
    def _check_round_complete(cls, round):
        """
        Check if all match series in the round are complete.
        If so, mark the round and potentially the stage as completed.
        """
        all_series = round.series.all()
        if not all_series.exists():
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

                    # Populate next stage with winners from this stage
                    cls._advance_to_next_stage(round.stage)

    @classmethod
    def _advance_to_next_stage(cls, completed_stage):
        """
        Populate StageParticipant records in the next stage
        from all series winners in the completed stage.
        Skips winners already advanced via explicit MatchAdvancement records.
        """
        # Find series whose winners were NOT explicitly advanced
        all_series_ids = set(
            MatchSeries.objects.filter(
                round__stage=completed_stage
            ).values_list('id', flat=True)
        )
        explicitly_advanced_ids = set(
            MatchAdvancement.objects.filter(
                from_series_id__in=all_series_ids,
                position=MatchAdvancement.PositionChoices.WINNER,
                to_stage__isnull=False,
            ).values_list('from_series_id', flat=True)
        )
        unhandled_series_ids = all_series_ids - explicitly_advanced_ids

        if not unhandled_series_ids:
            return

        next_stage = (
            Stage.objects.filter(
                tournament=completed_stage.tournament,
                order__gt=completed_stage.order,
            )
            .order_by('order')
            .first()
        )
        if not next_stage:
            return

        # Collect winners only from series without explicit advancement
        winner_tp_ids = StageParticipant.objects.filter(
            won_series__id__in=unhandled_series_ids,
        ).values_list('tournament_player_id', flat=True).distinct()

        for tp_id in winner_tp_ids:
            tp = TournamentPlayer.objects.get(id=tp_id)
            StageParticipant.objects.get_or_create(
                stage=next_stage,
                tournament_player=tp,
                defaults={'status': StageParticipant.ParticipantStatus.ACTIVE},
            )
