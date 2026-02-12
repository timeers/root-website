"""
Service layer for player grouping operations.
Handles availability-based, manual, and random grouping of players.
"""
import random

from django.db import transaction
from django.db.models import Max, F

from the_warroom.models import (
    GroupingSession,
    PlayerGroup,
    SessionPlayer,
)
from the_gatehouse.utils import generate_name, NameConvention


def calculate_best_consecutive(hours_set):
    """Find longest consecutive run in hour-of-week set."""
    if not hours_set:
        return 0
    sorted_hours = sorted(hours_set)
    max_consecutive = 1
    current = 1
    for i in range(1, len(sorted_hours)):
        if sorted_hours[i] == sorted_hours[i - 1] + 1:
            current += 1
            max_consecutive = max(max_consecutive, current)
        else:
            current = 1
    return max_consecutive


def calculate_days_with_overlap(hours_set, min_consecutive=1):
    """
    Count how many different days have at least min_consecutive hours of overlap.

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

    # Count days that have at least min_consecutive consecutive hours
    qualifying_days = 0
    for day, hours in days_with_hours.items():
        if len(hours) < min_consecutive:
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


class GroupingService:
    """Service for creating and managing player groups."""

    @classmethod
    @transaction.atomic
    def create_from_tournament(cls, tournament, created_by, grouping_type='manual', **config):
        """
        Create a new grouping session for a tournament.

        Args:
            tournament: Tournament instance
            created_by: Profile who is creating the session
            grouping_type: 'manual', 'random', or 'availability'
            **config: Optional config (name, etc.)
        """
        session = GroupingSession.objects.create(
            tournament=tournament,
            name=config.get('name', ''),
            grouping_type=grouping_type,
            created_by=created_by,
        )
        return session

    @classmethod
    @transaction.atomic
    def generate_availability_groups(cls, session, round):
        """
        Run the availability-based grouping algorithm for a specific round.
        Reads players from round.roster (active players not yet in a group for this round).
        Creates PlayerGroup objects owned by the round.

        Args:
            session: GroupingSession instance (provides survey/config)
            round: Round instance (provides player roster and group size constraints)
        """
        if not session.survey:
            raise ValueError("Session must have a survey for availability-based grouping")

        min_size = round.get_min_players()
        max_size = round.get_max_players()

        # Clear existing groups for this round
        round.player_groups.all().delete()

        # Get active rostered players (not yet in a group for this round)
        active_players = round.roster.filter(
            status=SessionPlayer.StatusChoices.ACTIVE
        ).select_related('profile', 'survey_response')

        if not active_players.exists():
            return

        # Build availability map
        availability_map = {}
        sp_map = {}  # profile_id -> SessionPlayer
        waitlist_ids = set()

        for sp in active_players:
            hours = set(sp.availability_hours) if sp.availability_hours else set()
            availability_map[sp.profile_id] = hours
            sp_map[sp.profile_id] = sp

        # Handle random algorithm
        if getattr(session, 'algorithm', 'greedy') == 'random':
            cls.generate_random_groups(session, round)
            return

        # For availability-based algorithms - use cascading hours: 5 → 4 → 3
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

        # Create PlayerGroup objects and assign session_players via M2M
        for i, group_data in enumerate(groups_data, 1):
            group = PlayerGroup.objects.create(
                round=round,
                session=session,
                group_number=i,
                name=generate_name(i, NameConvention(session.naming_convention)),
                created_via=GroupingSession.AlgorithmChoices.GREEDY,
                all_hours=[],
                overlap_hours=sorted(list(group_data['overlap_hours'])),
                total_overlap_hours=len(group_data['overlap_hours']),
            )

            for profile_id in group_data['members']:
                sp = sp_map.get(profile_id)
                if sp:
                    group.session_players.add(sp)

            group.recalculate_overlap()

        # Calculate best fit for ungrouped players
        cls.calculate_best_fit_groups(session, round)

        session.recalculate_statistics()

    @classmethod
    @transaction.atomic
    def generate_random_groups(cls, session, round):
        """
        Randomly assign active rostered players to groups for a round.

        Args:
            session: GroupingSession instance (provides config/naming)
            round: Round instance (provides player roster and group size constraints)
        """
        min_size = round.get_min_players()
        max_size = round.get_max_players()

        active_players = list(
            round.roster.filter(status=SessionPlayer.StatusChoices.ACTIVE)
        )
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
                session=session,
                group_number=group_number,
                name=generate_name(group_number, NameConvention(session.naming_convention)),
                created_via=GroupingSession.AlgorithmChoices.RANDOM,
                all_hours=[],
            )
            group_number += 1

            for _ in range(group_size):
                if player_index >= len(active_players):
                    break
                group.session_players.add(active_players[player_index])
                player_index += 1

        session.recalculate_statistics()

    @classmethod
    @transaction.atomic
    def assign_player_to_group(cls, session_player, to_group, round, moved_by=None):
        """
        Assign a player to a group within a round.
        Handles all transitions: ungrouped→group, group→group, group→ungrouped.

        Args:
            session_player: SessionPlayer instance to move
            to_group: PlayerGroup to assign to (None removes from all groups for this round)
            round: Round instance (scopes group membership)
            moved_by: Profile making the change (optional)
        """
        # Find the current group for this player in this round (if any)
        current_groups = session_player.player_groups.filter(round=round)
        for current_group in current_groups:
            current_group.session_players.remove(session_player)
            current_group.recalculate_overlap()

        if to_group:
            to_group.session_players.add(session_player)
            to_group.recalculate_overlap()

        session_player.session.recalculate_statistics()

    @classmethod
    @transaction.atomic
    def remove_from_group(cls, session_player, round):
        """
        Remove a player from all groups in a round, returning them to ungrouped status.

        Args:
            session_player: SessionPlayer instance
            round: Round instance (scopes group membership)
        """
        current_groups = list(session_player.player_groups.filter(round=round))
        for group in current_groups:
            group.session_players.remove(session_player)
            group.recalculate_overlap()

        cls.calculate_best_fit_groups(session_player.session, round)
        session_player.session.recalculate_statistics()

    @classmethod
    @transaction.atomic
    def move_player(cls, from_group, to_group, session_player):
        """
        Move a player from one group to another within the same round.

        Args:
            from_group: PlayerGroup to remove from
            to_group: PlayerGroup to add to (must be in the same round)
            session_player: SessionPlayer instance to move
        """
        from_group.session_players.remove(session_player)
        to_group.session_players.add(session_player)

        from_group.recalculate_overlap()
        to_group.recalculate_overlap()

        if from_group.session:
            from_group.session.recalculate_statistics()

    @classmethod
    def calculate_best_fit_groups(cls, session, round):
        """
        For each active ungrouped player in a round, find the best-fit PlayerGroup.
        Populates PlayerGroup.best_fit_players M2M with players not in the group
        who have the best availability overlap with the group's current members.
        """
        groups = list(round.player_groups.all())
        if not groups:
            return

        # Get ungrouped active players in this round
        ungrouped = round.get_active_players_queryset()

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

            for sp in ungrouped:
                if not sp.availability_hours:
                    continue
                player_hours = set(sp.availability_hours)
                overlap = player_hours & group_hours
                overlap_count = len(overlap)
                if overlap_count > 0:
                    scored_players.append((sp, overlap_count))

            # Sort by overlap count descending, keep top candidates
            scored_players.sort(key=lambda x: -x[1])
            best_fits = [sp for sp, _ in scored_players[:5]]  # Store up to 5 best fits per group

            if best_fits:
                group.best_fit_players.set(best_fits)

    @classmethod
    @transaction.atomic
    def finalize_session(cls, session, round):
        """
        Finalize the grouping for a specific round.
        Sets round.grouping_status to FINALIZED — does not affect other rounds.

        Args:
            session: GroupingSession instance
            round: Round instance to finalize grouping for
        """
        from the_warroom.models import Round as RoundModel
        round.grouping_status = RoundModel.GroupingStatusChoices.FINALIZED
        round.save(update_fields=['grouping_status'])

    @classmethod
    @transaction.atomic
    def move_to_waitlist(cls, session_player):
        """
        Move a player to the session waitlist.
        Updates their SurveyResponse.response_position if applicable.

        Args:
            session_player: SessionPlayer instance
        """
        session = session_player.session

        # Remove from any groups in all rounds this player is rostered in
        for round in session_player.rounds.all():
            for group in session_player.player_groups.filter(round=round):
                group.session_players.remove(session_player)
                group.recalculate_overlap()

        # Update status to waitlist
        session_player.status = SessionPlayer.StatusChoices.WAITLIST
        session_player.save(update_fields=['status'])

        # Update survey response position if applicable
        if session_player.survey_response and session.survey:
            survey = session.survey
            if survey.waitlist_threshold:
                max_position = survey.responses.aggregate(
                    max_pos=Max('response_position')
                )['max_pos'] or 0
                session_player.survey_response.response_position = max_position + 1
                session_player.survey_response.save(update_fields=['response_position'])

        session.recalculate_statistics()

    @classmethod
    @transaction.atomic
    def move_from_waitlist(cls, session_player, to_group=None):
        """
        Move a player from the session waitlist to active status (and optionally a group).
        Updates their SurveyResponse.response_position if applicable.

        Args:
            session_player: SessionPlayer instance (status='waitlist')
            to_group: PlayerGroup to add to (optional)
        """
        session = session_player.session

        session_player.status = SessionPlayer.StatusChoices.ACTIVE
        session_player.save(update_fields=['status'])

        if to_group:
            to_group.session_players.add(session_player)
            to_group.recalculate_overlap()

        # Update survey response position if applicable
        if session_player.survey_response and session.survey:
            survey = session.survey
            if survey.waitlist_threshold:
                threshold = survey.waitlist_threshold
                survey.responses.filter(
                    response_position__gte=threshold
                ).update(response_position=F('response_position') + 1)
                session_player.survey_response.response_position = threshold
                session_player.survey_response.save(update_fields=['response_position'])

        session.recalculate_statistics()

    @classmethod
    @transaction.atomic
    def sync_survey_responses_to_session(cls, session, survey):
        """
        Sync survey respondents into a tournament's GroupingSession as SessionPlayers.
        Creates or updates SessionPlayer records with availability data from survey responses.
        Does not overwrite waitlist/eliminated status.
        Removes SessionPlayer records for profiles no longer in the survey.

        Args:
            session: GroupingSession instance (tournament-level)
            survey: Survey instance to sync from
        """
        from the_tavern.models import SurveyResponse

        accepted_responses = survey.responses.filter(
            profile__isnull=False
        ).select_related('profile').order_by('response_position')

        threshold = survey.waitlist_threshold if survey.has_waitlist else None
        accepted_profile_ids = set()

        for response in accepted_responses:
            profile = response.profile
            accepted_profile_ids.add(profile.id)

            is_waitlist = threshold and response.response_position > threshold
            availability = sorted(list(response.get_combined_availability_hours()))

            sp, created = SessionPlayer.objects.get_or_create(
                session=session,
                profile=profile,
                defaults={
                    'survey_response': response,
                    'status': SessionPlayer.StatusChoices.WAITLIST if is_waitlist else SessionPlayer.StatusChoices.ACTIVE,
                    'availability_hours': availability,
                }
            )
            if not created:
                # Update availability hours and survey response reference
                # But don't overwrite a manually-set waitlist or eliminated status
                update_fields = ['availability_hours', 'survey_response']
                sp.availability_hours = availability
                sp.survey_response = response
                sp.save(update_fields=update_fields)

        # Remove SessionPlayer records for profiles no longer in the survey
        session.session_players.exclude(
            profile_id__in=accepted_profile_ids
        ).delete()

        session.recalculate_statistics()

    @classmethod
    @transaction.atomic
    def create_groups_from_ungrouped(cls, session, round, min_hours=None):
        """
        Create new groups from ungrouped active players in a round.

        Args:
            session: GroupingSession instance (provides config)
            round: Round instance (provides player roster)
            min_hours: Starting minimum consecutive hours (default: 4, will cascade to 3)
        """
        ungrouped = list(round.get_active_players_queryset())
        if not ungrouped:
            return

        # Build availability map from ungrouped players
        availability_map = {}
        sp_map = {}

        for sp in ungrouped:
            if sp.availability_hours:
                availability_map[sp.profile_id] = set(sp.availability_hours)
                sp_map[sp.profile_id] = sp

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
                session=session,
                group_number=i,
                name=generate_name(i, NameConvention(session.naming_convention)),
                created_via=GroupingSession.AlgorithmChoices.GREEDY,
                all_hours=[],
                overlap_hours=sorted(list(group_data['overlap_hours'])),
                total_overlap_hours=len(group_data['overlap_hours']),
            )

            for profile_id in group_data['members']:
                sp = sp_map.get(profile_id)
                if sp:
                    group.session_players.add(sp)

            group.recalculate_overlap()

        cls.calculate_best_fit_groups(session, round)
        session.recalculate_statistics()

    @classmethod
    @transaction.atomic
    def add_waitlist_to_round(cls, session, round, count=None):
        """
        Move waitlisted players into a round's roster.

        Args:
            session: GroupingSession instance
            round: Round instance
            count: Number of waitlist players to add (None = all)
        """
        waitlist_players = session.session_players.filter(
            status=SessionPlayer.StatusChoices.WAITLIST
        ).exclude(rounds=round)

        if count is not None:
            waitlist_players = waitlist_players[:count]

        for sp in waitlist_players:
            sp.status = SessionPlayer.StatusChoices.ACTIVE
            sp.save(update_fields=['status'])
            round.roster.add(sp)

        cls.calculate_best_fit_groups(session, round)
        session.recalculate_statistics()
