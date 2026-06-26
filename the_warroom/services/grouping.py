"""
Service layer for player grouping operations.
Handles availability-based, manual, and random grouping of players.
"""
import random
from collections import defaultdict, Counter

from django.db import transaction
from django.db.models import Max, F

from the_warroom.models import (
    PlayerGroup,
    TournamentPlayer,
    Stage,
    StageParticipant,
)
from the_gatehouse.utils import generate_name, NameConvention


def calculate_best_consecutive(hours_set):
    """Find longest consecutive run in hour-of-week set, handling week wraparound."""
    if not hours_set:
        return 0
    # Double the hours to handle week wraparound (168-hour cycle)
    doubled = sorted(hours_set | {h + 168 for h in hours_set})
    max_consecutive = 1
    current = 1
    for i in range(1, len(doubled)):
        if doubled[i] == doubled[i - 1] + 1:
            current += 1
            max_consecutive = max(max_consecutive, current)
        else:
            current = 1
    # Cap at actual set size (can't have more consecutive than total hours)
    return min(max_consecutive, len(hours_set))


def calculate_days_with_overlap(hours_set, min_consecutive=1):
    """
    Count how many different days have at least min_consecutive hours of overlap.
    Handles week wraparound: a streak crossing Sunday→Monday counts as one
    qualifying day (credited to Sunday) to avoid inflating the count.

    Args:
        hours_set: set of hour-of-week integers (0-167)
        min_consecutive: minimum consecutive hours needed on a day to count it

    Returns:
        int: number of days (0-7) that have qualifying overlap
    """
    if not hours_set:
        return 0

    # Group hours by day (0-6 for Mon-Sun)
    days_with_hours = {}
    for hour in hours_set:
        day = hour // 24
        if day not in days_with_hours:
            days_with_hours[day] = []
        days_with_hours[day].append(hour % 24)

    # Detect wraparound: Sunday ending at hour 23 connecting to Monday starting at hour 0
    wrap_bonus = 0
    if 6 in days_with_hours and 0 in days_with_hours:
        sun_hours = sorted(days_with_hours[6])
        mon_hours = sorted(days_with_hours[0])
        # Count consecutive hours at end of Sunday (up to hour 23)
        sun_tail = 0
        for h in reversed(sun_hours):
            if h == 23 - sun_tail:
                sun_tail += 1
            else:
                break
        # Count consecutive hours at start of Monday (from hour 0)
        mon_head = 0
        for h in mon_hours:
            if h == mon_head:
                mon_head += 1
            else:
                break
        if sun_tail > 0 and mon_head > 0:
            wrap_bonus = sun_tail + mon_head

    # Count days that have at least min_consecutive consecutive hours
    qualifying_days = 0
    for day, hours in days_with_hours.items():
        if len(hours) < min_consecutive:
            # Could still qualify via wraparound, but only for Sunday
            if wrap_bonus >= min_consecutive and day == 6:
                qualifying_days += 1
            continue

        # Check for consecutive hours on this day
        sorted_hours = sorted(hours)
        max_consecutive = 1
        current = 1
        for i in range(1, len(sorted_hours)):
            if sorted_hours[i] == sorted_hours[i - 1] + 1:
                current += 1
                max_consecutive = max(max_consecutive, current)
            else:
                current = 1

        # Apply wraparound bonus only to Sunday
        if day == 6 and wrap_bonus > 0:
            max_consecutive = max(max_consecutive, wrap_bonus)

        if max_consecutive >= min_consecutive:
            qualifying_days += 1

    return qualifying_days


def greedy_group_assignment(availability_map, min_size=3, max_size=5, min_consecutive=4, min_days=1, randomize=False):
    """
    Greedy algorithm for grouping players by availability compatibility.

    Args:
        availability_map: dict of profile_id -> set of hour-of-week integers (0-167)
        min_size: minimum players per group
        max_size: maximum players per group
        min_consecutive: minimum consecutive overlapping hours required
        min_days: minimum number of days with qualifying overlap required

    Returns:
        tuple of (groups_data, ungrouped_ids, used_fallback)
        - groups_data: list of dicts with 'members' (list of profile_ids) and 'overlap_hours' (set)
        - ungrouped_ids: list of profile_ids that couldn't be grouped
        - used_fallback: Always False for greedy
    """
    player_ids = list(availability_map.keys())

    if not player_ids:
        return [], [], False

    def meets_requirements(hours_set):
        """Check if hours meet both consecutive and days requirements."""
        if calculate_best_consecutive(hours_set) < min_consecutive:
            return False
        if calculate_days_with_overlap(hours_set, min_consecutive) < min_days:
            return False
        return True

    # Calculate pairwise compatibility
    # Use sorted tuple keys (smaller_id, larger_id) for consistent lookup
    compatibility = {}
    for i, p1 in enumerate(player_ids):
        for p2 in player_ids[i + 1:]:
            overlap = availability_map[p1] & availability_map[p2]
            best_consecutive = calculate_best_consecutive(overlap)
            days_with_overlap = calculate_days_with_overlap(overlap, min_consecutive)
            # Always use (smaller, larger) as key for consistent lookup
            key = (min(p1, p2), max(p1, p2))
            compatibility[key] = {
                'overlap_count': len(overlap),
                'best_consecutive': best_consecutive,
                'days_with_overlap': days_with_overlap,
                'hours': overlap
            }

    # Sort by flexibility with random component
    sorted_players = sorted(
        player_ids,
        key=lambda p: (
            len(availability_map[p]),
            random.random() if randomize else 0  # Add randomness for tie-breaking
        )
    )

    groups = []
    assigned = set()

    for player in sorted_players:
        if player in assigned:
            continue

        # Find most compatible unassigned players
        candidates = []
        for other in player_ids:
            if other == player or other in assigned:
                continue
            key = (min(player, other), max(player, other))
            compat = compatibility.get(key, {'overlap_count': 0, 'best_consecutive': 0, 'days_with_overlap': 0, 'hours': set()})
            # Check both consecutive hours AND days requirements
            if compat['best_consecutive'] >= min_consecutive and compat['days_with_overlap'] >= min_days:
                candidates.append((other, compat['overlap_count'], compat['hours']))

        # Sort by longest consecutive blocks, then by total overlap
        candidates.sort(key=lambda x: (-calculate_best_consecutive(x[2]), -x[1]))

        # Try to form a group
        group = [player]
        group_hours = availability_map[player].copy()

        for candidate, _, _ in candidates:
            if len(group) >= min_size:
                break  # Stop at min_size to create more groups
            # Check if adding this candidate maintains both requirements
            new_overlap = group_hours & availability_map[candidate]
            if meets_requirements(new_overlap):
                group.append(candidate)
                group_hours = new_overlap

        if len(group) >= min_size:
            groups.append({
                'members': group,
                'overlap_hours': group_hours
            })
            assigned.update(group)

    ungrouped = [p for p in player_ids if p not in assigned]

    # Post-process: optimize groups with swap iterations
    if len(groups) >= 2:
        groups = _optimize_groups_with_swaps(
            groups,
            availability_map,
            iterations=100,
            min_consecutive=min_consecutive,
            min_days=min_days,
            min_size=min_size,
            max_size=max_size
        )

    return groups, ungrouped, False


def greedy_group_assignment_with_restarts(
    availability_map,
    min_size=3,
    max_size=5,
    min_consecutive=4,
    min_days=1,
    restarts=10
):
    """
    Run greedy algorithm multiple times and return best result.

    Args:
        availability_map: dict of profile_id -> set of hours
        min_size: minimum players per group
        max_size: maximum players per group
        min_consecutive: minimum consecutive hours required
        min_days: minimum number of days with qualifying overlap required
        restarts: number of times to run the algorithm

    Returns:
        Best result from all restarts
    """
    best_groups = []
    best_ungrouped = []
    best_score = float('inf')  # Lower is better (fewer ungrouped)

    for attempt in range(restarts):
        groups, ungrouped, _ = greedy_group_assignment(
            availability_map,
            min_size=min_size,
            max_size=max_size,
            min_consecutive=min_consecutive,
            min_days=min_days,
            randomize=True
        )

        # Score: prioritize fewer ungrouped, then more days with overlap, then better consecutive hours
        # Lower score is better
        score = len(ungrouped) * 10000 + sum(
            -calculate_days_with_overlap(g['overlap_hours'], min_consecutive) * 100
            - calculate_best_consecutive(g['overlap_hours'])
            for g in groups
        )

        if score < best_score:
            best_score = score
            best_groups = groups
            best_ungrouped = ungrouped

    return best_groups, best_ungrouped, False


def _optimize_groups_with_swaps(groups, availability_map, iterations=100, min_consecutive=4, min_days=1, min_size=3, max_size=5):
    """
    Improve groups by trying player swaps between groups.
    Uses hill-climbing to find better grouping configurations.

    Args:
        groups: list of group dicts from greedy_group_assignment
        availability_map: dict of profile_id -> set of hours
        iterations: number of swap attempts
        min_consecutive: minimum consecutive hours required
        min_days: minimum number of days with qualifying overlap required
        min_size: minimum players per group
        max_size: maximum players per group

    Returns:
        Optimized groups list
    """
    if len(groups) < 2:
        return groups

    def meets_requirements(hours_set):
        """Check if hours meet both consecutive and days requirements."""
        if calculate_best_consecutive(hours_set) < min_consecutive:
            return False
        if calculate_days_with_overlap(hours_set, min_consecutive) < min_days:
            return False
        return True

    def score_group(group_members):
        """Score a group by its best consecutive overlap."""
        if not group_members:
            return 0
        overlap = set.intersection(*[availability_map[p] for p in group_members])
        return calculate_best_consecutive(overlap)

    def total_score(groups_list):
        """Sum of all group scores."""
        return sum(score_group(g['members']) for g in groups_list)

    # Work with a copy
    working_groups = [{'members': g['members'][:], 'overlap_hours': g['overlap_hours'].copy()} for g in groups]
    best_groups = [g.copy() for g in working_groups]
    best_score = total_score(best_groups)

    for _ in range(iterations):
        # Pick two random groups
        g1_idx, g2_idx = random.sample(range(len(working_groups)), 2)
        g1 = working_groups[g1_idx]['members']
        g2 = working_groups[g2_idx]['members']

        if not g1 or not g2:
            continue

        # Try swapping random players
        p1 = random.choice(g1)
        p2 = random.choice(g2)

        # Make swap
        g1_new = [p2 if p == p1 else p for p in g1]
        g2_new = [p1 if p == p2 else p for p in g2]

        # Ensure size constraints
        if not (min_size <= len(g1_new) <= max_size and min_size <= len(g2_new) <= max_size):
            continue

        # Check if both groups still meet requirements (consecutive hours AND days)
        g1_overlap = set.intersection(*[availability_map[p] for p in g1_new])
        g2_overlap = set.intersection(*[availability_map[p] for p in g2_new])

        if meets_requirements(g1_overlap) and meets_requirements(g2_overlap):
            # Calculate score improvement
            old_score = score_group(g1) + score_group(g2)
            new_score = score_group(g1_new) + score_group(g2_new)

            # Keep swap if it improves
            if new_score > old_score:
                working_groups[g1_idx] = {
                    'members': g1_new,
                    'overlap_hours': g1_overlap
                }
                working_groups[g2_idx] = {
                    'members': g2_new,
                    'overlap_hours': g2_overlap
                }

                current_total = total_score(working_groups)
                if current_total > best_score:
                    best_score = current_total
                    best_groups = [{'members': g['members'][:], 'overlap_hours': g['overlap_hours'].copy()}
                                   for g in working_groups]

    return best_groups


def build_opponent_history(stage, current_round):
    """
    Derive opponent history from finalized PlayerGroups in prior rounds of the same stage.

    Returns:
        defaultdict(Counter) mapping tp_id -> Counter({opponent_tp_id: times_played_together})
    """
    from the_warroom.models import Round as RoundModel

    finalized_rounds = RoundModel.objects.filter(
        stage=stage,
        grouping_status=RoundModel.GroupingStatusChoices.FINALIZED,
    ).exclude(id=current_round.id)

    history = defaultdict(Counter)

    groups = (
        PlayerGroup.objects
        .filter(round__in=finalized_rounds)
        .prefetch_related('tournament_players')
    )

    for group in groups:
        members = list(group.tournament_players.values_list('id', flat=True))
        for i, tp_id in enumerate(members):
            for j, other_id in enumerate(members):
                if i != j:
                    history[tp_id][other_id] += 1

    return history


def swiss_group_assignment(player_ids, conflict_map, min_size=4, max_size=4, restarts=20):
    """
    Greedy pairing algorithm that minimises repeat opponents.

    Uses random restarts, scoring each attempt by total conflict weight
    (times already-played pairs appear in the same group) with ungrouped
    count as a secondary penalty.

    Args:
        player_ids: list of TournamentPlayer.id values
        conflict_map: defaultdict(Counter) from build_opponent_history
        min_size: minimum group size
        max_size: maximum group size
        restarts: number of random restart attempts

    Returns:
        tuple (groups_data, ungrouped_ids)
        - groups_data: list of {'members': [tp_id, ...]}
        - ungrouped_ids: list of tp_ids that could not be placed
    """
    total = len(player_ids)
    if total == 0:
        return [], []

    # Determine optimal number of full groups (same logic as generate_random_groups)
    best_config = None
    min_leftover = total + 1

    for num_groups in range(1, (total // min_size) + 2):
        base_size = total // num_groups
        extra = total % num_groups

        if min_size == max_size:
            if num_groups * min_size <= total:
                leftover = total - (num_groups * min_size)
            else:
                continue
        else:
            if base_size < min_size:
                continue
            if base_size > max_size:
                continue
            if base_size + 1 > max_size and extra > 0:
                continue
            leftover = 0

        if leftover < min_leftover:
            min_leftover = leftover
            actual_base = min_size if min_size == max_size else base_size
            best_config = (num_groups, actual_base, extra)

    if best_config is None:
        return [], list(player_ids)

    num_groups, base_size, extra = best_config

    def conflict_score(members):
        score = 0
        for i, a in enumerate(members):
            for b in members[i + 1:]:
                score += conflict_map.get(a, Counter()).get(b, 0)
        return score

    def attempt():
        pool = list(player_ids)
        random.shuffle(pool)
        groups = []
        for g in range(num_groups):
            if min_size == max_size:
                size = base_size
            else:
                size = base_size + (1 if g < extra else 0)
            if len(pool) < size:
                break
            group = [pool.pop(0)]
            for _ in range(size - 1):
                if not pool:
                    break
                best = min(
                    pool,
                    key=lambda p: sum(conflict_map.get(p, Counter()).get(m, 0) for m in group)
                )
                pool.remove(best)
                group.append(best)
            groups.append({'members': group})
        return groups, pool

    best_groups = None
    best_score = float('inf')
    best_ungrouped = list(player_ids)

    for _ in range(restarts):
        groups, ungrouped = attempt()
        score = sum(conflict_score(g['members']) for g in groups) * 10000 + len(ungrouped)
        if score < best_score:
            best_score = score
            best_groups = groups
            best_ungrouped = ungrouped

    return best_groups or [], best_ungrouped


class GroupingService:
    """Service for creating and managing player groups."""

    @classmethod
    def _get_active_tournament_players(cls, stage):
        """
        Return TournamentPlayers for players who are Active StageParticipants in the given stage.
        """
        from the_warroom.models import StageParticipant
        active_tp_ids = StageParticipant.objects.filter(
            stage=stage,
            status=StageParticipant.ParticipantStatus.ACTIVE
        ).values_list('tournament_player_id', flat=True)
        return TournamentPlayer.objects.filter(
            id__in=active_tp_ids
        ).select_related('profile', 'survey_response')

    @classmethod
    def _recalculate_stage_stats(cls, stage, round):
        """Update grouped/ungrouped counts on a stage."""
        grouped_ids = set(
            TournamentPlayer.objects.filter(
                player_groups__round=round
            ).values_list('id', flat=True)
        )
        active_players = cls._get_active_tournament_players(stage)
        stage.grouped_count = active_players.filter(id__in=grouped_ids).count()
        stage.ungrouped_count = active_players.exclude(id__in=grouped_ids).count()
        stage.save(update_fields=['grouped_count', 'ungrouped_count'])

    @classmethod
    @transaction.atomic
    def generate_availability_groups(cls, stage, round):
        """
        Run the availability-based grouping algorithm for a specific round.
        Reads players from the stage's tournament (active TournamentPlayers not yet in a group for this round).
        Creates PlayerGroup objects owned by the round.

        Args:
            stage: Stage instance (provides grouping config and player source via tournament)
            round: Round instance (provides group size constraints)
        """
        min_size = round.get_min_players()
        max_size = round.get_max_players()

        # Clear existing groups for this round
        round.player_groups.all().delete()

        # Get active tournament players
        active_players = cls._get_active_tournament_players(stage)

        if not active_players.exists():
            return

        # Build availability map
        availability_map = {}
        tp_map = {}  # profile_id -> TournamentPlayer

        for tp in active_players:
            hours = set(tp.availability_hours) if tp.availability_hours else set()
            availability_map[tp.profile_id] = hours
            tp_map[tp.profile_id] = tp

        # For availability-based grouping - use cascading hours: 5 → 4 → 3
        grouping_func = greedy_group_assignment_with_restarts
        groups_data = []
        ungrouped_ids = list(availability_map.keys())

        # Try progressively lower hour requirements
        for min_hours in [5, 4, 3]:
            if not ungrouped_ids:
                break

            remaining_availability = {pid: availability_map[pid] for pid in ungrouped_ids}
            new_groups, still_ungrouped, _ = grouping_func(
                remaining_availability,
                min_size=min_size,
                max_size=max_size,
                min_consecutive=min_hours,
                min_days=1,
            )
            groups_data.extend(new_groups)
            ungrouped_ids = still_ungrouped

        # Second pass: try to fit remaining ungrouped players into groups with room
        for player_id in ungrouped_ids[:]:
            player_hours = availability_map[player_id]
            best_group = None
            best_overlap_quality = -1

            for group in groups_data:
                if len(group['members']) >= max_size:
                    continue

                new_overlap = group['overlap_hours'] & player_hours
                consecutive = calculate_best_consecutive(new_overlap)
                if consecutive >= 3:
                    if consecutive > best_overlap_quality:
                        best_overlap_quality = consecutive
                        best_group = group

            if best_group:
                best_group['members'].append(player_id)
                best_group['overlap_hours'] &= player_hours
                ungrouped_ids.remove(player_id)

        # Third pass: swap-based fitting
        if min_size != max_size and ungrouped_ids:
            groups_with_room = [g for g in groups_data if len(g['members']) < max_size]

            if groups_with_room:
                for player_id in ungrouped_ids[:]:
                    player_hours = availability_map[player_id]

                    for full_group in groups_data:
                        if len(full_group['members']) < max_size:
                            continue

                        new_overlap = full_group['overlap_hours'] & player_hours
                        if calculate_best_consecutive(new_overlap) < 3:
                            continue

                        for member_id in full_group['members']:
                            member_hours = availability_map[member_id]

                            for target_group in groups_with_room:
                                if target_group == full_group:
                                    continue

                                member_overlap = target_group['overlap_hours'] & member_hours
                                if calculate_best_consecutive(member_overlap) >= 3:
                                    full_group['members'].remove(member_id)
                                    full_group['members'].append(player_id)
                                    full_group['overlap_hours'] = set.intersection(
                                        *[availability_map[m] for m in full_group['members']]
                                    )

                                    target_group['members'].append(member_id)
                                    target_group['overlap_hours'] &= member_hours

                                    ungrouped_ids.remove(player_id)

                                    if len(target_group['members']) >= max_size:
                                        groups_with_room.remove(target_group)

                                    break
                            else:
                                continue
                            break
                        else:
                            continue
                        break

                    if not groups_with_room:
                        break

        # Create PlayerGroup objects and assign tournament_players via M2M
        for i, group_data in enumerate(groups_data, 1):
            group = PlayerGroup.objects.create(
                round=round,
                group_number=i,
                name=generate_name(i, NameConvention(stage.naming_convention)),
                created_via=Stage.GroupingTypeChoices.AVAILABILITY,
                all_hours=[],
                overlap_hours=sorted(list(group_data['overlap_hours'])),
                total_overlap_hours=len(group_data['overlap_hours']),
            )

            for profile_id in group_data['members']:
                tp = tp_map.get(profile_id)
                if tp:
                    group.tournament_players.add(tp)

            group.recalculate_overlap()

        # Calculate best fit for ungrouped players
        cls.calculate_best_fit_groups(stage, round)
        cls._recalculate_stage_stats(stage, round)

    @classmethod
    @transaction.atomic
    def generate_random_groups(cls, stage, round):
        """
        Randomly assign active tournament players to groups for a round.

        Args:
            stage: Stage instance (provides config/naming)
            round: Round instance (provides group size constraints)
        """
        min_size = round.get_min_players()
        max_size = round.get_max_players()

        active_players = list(cls._get_active_tournament_players(stage))
        total_players = len(active_players)

        if total_players == 0:
            return

        # Determine optimal group distribution
        best_config = None
        min_leftover = total_players

        for num_groups in range(1, (total_players // min_size) + 2):
            base_size = total_players // num_groups
            extra = total_players % num_groups

            if min_size == max_size:
                if num_groups * min_size <= total_players:
                    leftover = total_players - (num_groups * min_size)
                else:
                    continue
            else:
                if base_size < min_size:
                    continue
                if base_size > max_size:
                    continue
                if base_size + 1 > max_size and extra > 0:
                    continue
                leftover = 0

            if leftover < min_leftover:
                min_leftover = leftover
                actual_group_size = min_size if min_size == max_size else base_size
                best_config = (num_groups, actual_group_size, extra)

        if best_config is None:
            return

        num_groups, base_size, extra = best_config

        # Shuffle for randomness
        random.shuffle(active_players)

        # Get starting group number
        group_number = round.player_groups.count() + 1
        player_index = 0

        for i in range(num_groups):
            group_size = base_size if min_size == max_size else base_size + (1 if i < extra else 0)

            group = PlayerGroup.objects.create(
                round=round,
                group_number=group_number,
                name=generate_name(group_number, NameConvention(stage.naming_convention)),
                created_via=Stage.GroupingTypeChoices.RANDOM,
                all_hours=[],
            )
            group_number += 1

            for _ in range(group_size):
                if player_index >= len(active_players):
                    break
                group.tournament_players.add(active_players[player_index])
                player_index += 1

        cls._recalculate_stage_stats(stage, round)

    @classmethod
    @transaction.atomic
    def generate_swiss_groups(cls, stage, round):
        """
        Generate groups that minimise repeat opponent matchups across rounds.

        Derives opponent history from finalized PlayerGroups in prior rounds of
        the same stage and uses a greedy algorithm with random restarts to
        minimise the number of players facing the same opponents again.

        Args:
            stage: Stage instance (provides config/naming)
            round: Round instance (provides group size constraints)
        """
        min_size = round.get_min_players()
        max_size = round.get_max_players()

        active_players = list(cls._get_active_tournament_players(stage))
        if not active_players:
            return

        tp_map = {tp.id: tp for tp in active_players}
        player_ids = list(tp_map.keys())

        conflict_map = build_opponent_history(stage, round)

        groups_data, ungrouped_ids = swiss_group_assignment(
            player_ids, conflict_map, min_size=min_size, max_size=max_size
        )

        group_number = round.player_groups.count() + 1
        conflict_groups = 0

        for group_data in groups_data:
            group = PlayerGroup.objects.create(
                round=round,
                group_number=group_number,
                name=generate_name(group_number, NameConvention(stage.naming_convention)),
                created_via=Stage.GroupingTypeChoices.SWISS,
                all_hours=[],
            )
            group_number += 1

            members = group_data['members']
            for tp_id in members:
                tp = tp_map.get(tp_id)
                if tp:
                    group.tournament_players.add(tp)

            # Check for conflicts within this group
            for i, a in enumerate(members):
                for b in members[i + 1:]:
                    if conflict_map.get(a, Counter()).get(b, 0) > 0:
                        conflict_groups += 1
                        break

        if conflict_groups > 0:
            round.grouping_notes = f"Warning: {conflict_groups} group(s) contain repeat matchups."
            round.save(update_fields=['grouping_notes'])

        cls._recalculate_stage_stats(stage, round)

    @classmethod
    @transaction.atomic
    def generate_manual_groups(cls, stage, round):
        """
        Pre-create empty groups for manual assignment.
        Uses the same group count logic as random grouping but leaves all
        players ungrouped for the organiser to drag into groups.

        Args:
            stage: Stage instance (provides config/naming)
            round: Round instance (provides group size constraints)
        """
        min_size = round.get_min_players()
        max_size = round.get_max_players()

        total_players = cls._get_active_tournament_players(stage).count()

        if total_players == 0:
            return

        # Determine optimal number of groups (same logic as generate_random_groups)
        best_config = None
        min_leftover = total_players

        for num_groups in range(1, (total_players // min_size) + 2):
            base_size = total_players // num_groups
            extra = total_players % num_groups

            if min_size == max_size:
                if num_groups * min_size <= total_players:
                    leftover = total_players - (num_groups * min_size)
                else:
                    continue
            else:
                if base_size < min_size:
                    continue
                if base_size > max_size:
                    continue
                if base_size + 1 > max_size and extra > 0:
                    continue
                leftover = 0

            if leftover < min_leftover:
                min_leftover = leftover
                best_config = num_groups

        if best_config is None:
            return

        num_groups = best_config

        for i in range(1, num_groups + 1):
            PlayerGroup.objects.create(
                round=round,
                group_number=i,
                name=generate_name(i, NameConvention(stage.naming_convention)),
                created_via=Stage.GroupingTypeChoices.MANUAL,
                all_hours=[],
            )

        cls._recalculate_stage_stats(stage, round)

    @classmethod
    @transaction.atomic
    def assign_player_to_group(cls, tournament_player, to_group, round, moved_by=None):
        """
        Assign a player to a group within a round.
        Handles all transitions: ungrouped→group, group→group, group→ungrouped.

        Args:
            tournament_player: TournamentPlayer instance to move
            to_group: PlayerGroup to assign to (None removes from all groups for this round)
            round: Round instance (scopes group membership)
            moved_by: Profile making the change (optional)
        """
        # Find the current group for this player in this round (if any)
        current_groups = tournament_player.player_groups.filter(round=round)
        for current_group in current_groups:
            current_group.tournament_players.remove(tournament_player)
            current_group.recalculate_overlap()

        if to_group:
            to_group.tournament_players.add(tournament_player)
            to_group.recalculate_overlap()

        if round.stage_id:
            cls._recalculate_stage_stats(round.stage, round)

    @classmethod
    @transaction.atomic
    def remove_from_group(cls, tournament_player, round):
        """
        Remove a player from all groups in a round, returning them to ungrouped status.

        Args:
            tournament_player: TournamentPlayer instance
            round: Round instance (scopes group membership)
        """
        current_groups = list(tournament_player.player_groups.filter(round=round))
        for group in current_groups:
            group.tournament_players.remove(tournament_player)
            group.recalculate_overlap()

        if round.stage_id:
            cls.calculate_best_fit_groups(round.stage, round)
            cls._recalculate_stage_stats(round.stage, round)

    @classmethod
    @transaction.atomic
    def move_player(cls, from_group, to_group, tournament_player):
        """
        Move a player from one group to another within the same round.

        Args:
            from_group: PlayerGroup to remove from
            to_group: PlayerGroup to add to (must be in the same round)
            tournament_player: TournamentPlayer instance to move
        """
        from_group.tournament_players.remove(tournament_player)
        to_group.tournament_players.add(tournament_player)

        from_group.recalculate_overlap()
        to_group.recalculate_overlap()

    @classmethod
    def calculate_best_fit_groups(cls, stage, round):
        """
        For each active ungrouped player in a round, find the best-fit PlayerGroup.
        Populates PlayerGroup.best_fit_players M2M with players not in the group
        who have the best availability overlap with the group's current members.
        """
        groups = list(round.player_groups.all())
        if not groups:
            return

        # Get ungrouped active tournament players (active but not in any group for this round)
        grouped_ids = set(
            TournamentPlayer.objects.filter(
                player_groups__round=round
            ).values_list('id', flat=True)
        )
        ungrouped = cls._get_active_tournament_players(stage).exclude(id__in=grouped_ids)

        # Clear existing best_fit_players for all groups in this round
        for group in groups:
            group.best_fit_players.clear()

        if not ungrouped.exists():
            return

        # For each group, find the ungrouped players with best overlap
        for group in groups:
            if not group.overlap_hours:
                continue

            group_hours = set(group.overlap_hours)
            scored_players = []

            for tp in ungrouped:
                if not tp.availability_hours:
                    continue
                player_hours = set(tp.availability_hours)
                overlap = player_hours & group_hours
                overlap_count = len(overlap)
                if overlap_count > 0:
                    scored_players.append((tp, overlap_count))

            # Sort by overlap count descending, keep top candidates
            scored_players.sort(key=lambda x: -x[1])
            best_fits = [tp for tp, _ in scored_players[:5]]  # Store up to 5 best fits per group

            if best_fits:
                group.best_fit_players.set(best_fits)

    @classmethod
    @transaction.atomic
    def finalize_round_grouping(cls, round):
        """
        Finalize the grouping for a specific round.
        Sets round.grouping_status to FINALIZED.

        Args:
            round: Round instance to finalize grouping for
        """
        from the_warroom.models import Round as RoundModel
        round.grouping_status = RoundModel.GroupingStatusChoices.FINALIZED
        round.save(update_fields=['grouping_status'])

    @classmethod
    @transaction.atomic
    def move_to_waitlist(cls, tournament_player):
        """
        Move a player to the tournament waitlist.
        Removes them from any groups they are in.

        Args:
            tournament_player: TournamentPlayer instance
        """
        # Remove from any groups
        for group in tournament_player.player_groups.all():
            group.tournament_players.remove(tournament_player)
            group.recalculate_overlap()

        # Update status to waitlist
        tournament_player.status = TournamentPlayer.StatusChoices.WAITLIST
        tournament_player.save(update_fields=['status'])

    @classmethod
    @transaction.atomic
    def sync_survey_responses_to_tournament(cls, tournament, survey):
        """
        Sync survey respondents into a tournament as TournamentPlayers.
        Creates or updates TournamentPlayer records with availability data from survey responses.
        Does not overwrite waitlist/eliminated status.

        ADDITIVE ONLY: Does not remove any existing TournamentPlayers.

        Args:
            tournament: Tournament instance
            survey: Survey instance to sync from

        Returns:
            dict: {'created': int, 'updated': int}
        """
        accepted_responses = survey.responses.filter(
            profile__isnull=False
        ).select_related('profile').order_by('response_position')

        threshold = survey.waitlist_threshold if survey.has_waitlist else None

        # Base waitlist position offset: new waitlist players are appended after existing ones
        existing_max_waitlist = (
            tournament.tournament_players
            .filter(status=TournamentPlayer.StatusChoices.WAITLIST)
            .aggregate(Max('waitlist_position'))['waitlist_position__max']
        ) or 0

        created_count = 0
        updated_count = 0
        synced_profile_ids = set()

        for response in accepted_responses:
            profile = response.profile

            is_waitlist = threshold and response.response_position > threshold
            availability = sorted(list(response.get_combined_availability_hours()))

            # Waitlist position = existing max + relative position within this survey's waitlist
            waitlist_pos = (existing_max_waitlist + (response.response_position - threshold)) if is_waitlist else None

            tp, created = TournamentPlayer.objects.get_or_create(
                tournament=tournament,
                profile=profile,
                defaults={
                    'survey_response': response,
                    'status': TournamentPlayer.StatusChoices.WAITLIST if is_waitlist else TournamentPlayer.StatusChoices.REGISTERED,
                    'availability_hours': availability,
                    'waitlist_position': waitlist_pos,
                }
            )

            synced_profile_ids.add(profile.id)

            if created:
                created_count += 1
            else:
                # Update availability hours and survey response reference
                # But don't overwrite a manually-set waitlist or eliminated status
                tp.availability_hours = availability
                tp.survey_response = response
                tp.save(update_fields=['availability_hours', 'survey_response'])
                updated_count += 1

        # If the survey is tied to a specific stage, add REGISTERED respondents to it.
        # Additive + idempotent; never demotes/removes. Open-stage fan-out (no linked
        # stage) is handled by the caller, not here, so the manual "none" path is unaffected.
        if survey.stage_id and synced_profile_ids:
            registered_players = TournamentPlayer.objects.filter(
                tournament=tournament,
                profile_id__in=synced_profile_ids,
                status=TournamentPlayer.StatusChoices.REGISTERED,
            )
            for tp in registered_players:
                StageParticipant.objects.get_or_create(
                    tournament_player=tp,
                    stage_id=survey.stage_id,
                    defaults={'status': StageParticipant.ParticipantStatus.ACTIVE},
                )

        return {
            'created': created_count,
            'updated': updated_count,
        }

    @classmethod
    @transaction.atomic
    def create_groups_from_ungrouped(cls, stage, round, min_hours=None):
        """
        Create new groups from ungrouped active players in a round.

        Args:
            stage: Stage instance (provides config)
            round: Round instance
            min_hours: Starting minimum consecutive hours (default: 4, will cascade to 3)
        """
        # Get ungrouped active tournament players
        grouped_ids = set(
            TournamentPlayer.objects.filter(
                player_groups__round=round
            ).values_list('id', flat=True)
        )
        ungrouped = list(
            TournamentPlayer.objects.filter(
                tournament=stage.tournament,
                status=TournamentPlayer.StatusChoices.REGISTERED
            ).exclude(id__in=grouped_ids)
        )
        if not ungrouped:
            return

        # Build availability map from ungrouped players
        availability_map = {}
        tp_map = {}

        for tp in ungrouped:
            if tp.availability_hours:
                availability_map[tp.profile_id] = set(tp.availability_hours)
                tp_map[tp.profile_id] = tp

        if not availability_map:
            return

        min_size = round.get_min_players()
        max_size = round.get_max_players()

        grouping_func = greedy_group_assignment_with_restarts

        start_hours = min_hours if min_hours else 4
        hours_to_try = [h for h in [5, 4, 3] if h <= start_hours]

        groups_data = []
        remaining_ids = list(availability_map.keys())

        for min_h in hours_to_try:
            if not remaining_ids:
                break

            remaining_availability = {pid: availability_map[pid] for pid in remaining_ids}
            new_groups, still_ungrouped, _ = grouping_func(
                remaining_availability,
                min_size=min_size,
                max_size=max_size,
                min_consecutive=min_h,
                min_days=1,
            )
            groups_data.extend(new_groups)
            remaining_ids = still_ungrouped

        # Get next group number
        max_group_num = round.player_groups.aggregate(
            max_num=Max('group_number')
        )['max_num'] or 0

        for i, group_data in enumerate(groups_data, max_group_num + 1):
            group = PlayerGroup.objects.create(
                round=round,
                group_number=i,
                name=generate_name(i, NameConvention(stage.naming_convention)),
                created_via=Stage.GroupingTypeChoices.AVAILABILITY,
                all_hours=[],
                overlap_hours=sorted(list(group_data['overlap_hours'])),
                total_overlap_hours=len(group_data['overlap_hours']),
            )

            for profile_id in group_data['members']:
                tp = tp_map.get(profile_id)
                if tp:
                    group.tournament_players.add(tp)

            group.recalculate_overlap()

        cls.calculate_best_fit_groups(stage, round)
        cls._recalculate_stage_stats(stage, round)
