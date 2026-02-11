"""
Service layer for player grouping operations.
Handles availability-based, manual, and random grouping of players.
"""
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

import random

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
    import random

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
        Create a new grouping session from tournament players.

        Args:
            tournament: Tournament instance
            created_by: Profile who is creating the session
            grouping_type: 'manual' or 'random'
            **config: Optional overrides for group sizes
        """
        session = GroupingSession.objects.create(
            tournament=tournament,
            round=config.get('round'),
            name=config.get('name', ''),
            grouping_type=grouping_type,
            min_group_size=config.get('min_group_size', 3),
            max_group_size=config.get('max_group_size', 5),
            created_by=created_by,
        )

        cls.populate_ungrouped_from_players(session)

        if grouping_type == GroupingSession.GroupingTypeChoices.RANDOM:
            cls.generate_random_groups(session)

        return session

    @classmethod
    @transaction.atomic
    def create_from_round(cls, round_obj, created_by, grouping_type='manual', **config):
        """
        Create a new grouping session from round players.

        Args:
            round_obj: Round instance
            created_by: Profile who is creating the session
            grouping_type: 'manual' or 'random'
            **config: Optional overrides for group sizes
        """
        session = GroupingSession.objects.create(
            tournament=round_obj.tournament,
            round=round_obj,
            name=config.get('name', ''),
            grouping_type=grouping_type,
            min_group_size=config.get('min_group_size', 3),
            max_group_size=config.get('max_group_size', 5),
            created_by=created_by,
        )

        cls.populate_ungrouped_from_players(session)

        if grouping_type == GroupingSession.GroupingTypeChoices.RANDOM:
            cls.generate_random_groups(session)

        return session

    @classmethod
    @transaction.atomic
    def generate_availability_groups(cls, session):
        """
        Run the availability-based grouping algorithm.

        Args:
            session: GroupingSession instance
            include_waitlist: Whether to include waitlisted survey responses
        """
        from the_tavern.models import SurveyResponse

        if not session.survey:
            raise ValueError("Session must have a survey for availability-based grouping")

        # Clear existing groups and session players
        session.groups.all().delete()
        session.session_players.all().delete()

        # Get responses ordered by position (accepted first, then waitlist in order)
        responses_qs = session.survey.responses.filter(
            profile__isnull=False
        ).select_related('profile').order_by('response_position')

        include_waitlist = session.include_waitlist

        # Filter by waitlist if needed
        if session.survey.has_waitlist and session.survey.waitlist_threshold and not include_waitlist:
            responses_qs = responses_qs.filter(
                response_position__lte=session.survey.waitlist_threshold
            )

        # Limit to total_players if set (respects waitlist count selection)
        if session.total_players > 0:
            responses_qs = responses_qs[:session.total_players]

        responses = list(responses_qs)

        # Build availability map
        availability_map = {}
        response_map = {}  # profile_id -> response
        waitlist_ids = set()

        for response in responses:
            hours = response.get_combined_availability_hours()
            availability_map[response.profile_id] = hours
            response_map[response.profile_id] = response

            # Track waitlist status
            if (session.survey.has_waitlist and
                session.survey.waitlist_threshold and
                response.response_position > session.survey.waitlist_threshold):
                waitlist_ids.add(response.profile_id)

        # Select algorithm based on session configuration
        algorithm = getattr(session, 'algorithm', 'greedy')

        # Handle random algorithm - convert to random grouping type
        if algorithm == 'random':
            # Create ungrouped players first
            cls.populate_ungrouped_from_survey(session)
            # Use random grouping
            cls.generate_random_groups(session)
            session.recalculate_statistics()
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
                min_size=session.min_group_size,
                max_size=session.max_group_size,
                min_consecutive=min_hours,
                min_days=1,  # Require at least 1 day, but prioritize more days in scoring
            )
            groups_data.extend(new_groups)
            ungrouped_ids = still_ungrouped

        # Second pass: try to fit remaining ungrouped players into groups with room
        for player_id in ungrouped_ids[:]:  # Iterate over a copy
            player_hours = availability_map[player_id]
            best_group = None
            best_overlap_quality = -1

            for group in groups_data:
                if len(group['members']) >= session.max_group_size:
                    continue  # Group is full

                # Check if player is compatible (use 3-hour floor)
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

        # Third pass: try swapping - if ungrouped player fits a full group well,
        # see if someone from that group can move to a non-full group
        if session.min_group_size != session.max_group_size and ungrouped_ids:
            # Find groups that have room
            groups_with_room = [g for g in groups_data if len(g['members']) < session.max_group_size]

            if groups_with_room:
                for player_id in ungrouped_ids[:]:  # Iterate over a copy
                    player_hours = availability_map[player_id]

                    # Find full groups where this player would fit well
                    for full_group in groups_data:
                        if len(full_group['members']) < session.max_group_size:
                            continue  # Not full, already handled in second pass

                        # Check if ungrouped player fits this group
                        new_overlap = full_group['overlap_hours'] & player_hours
                        if calculate_best_consecutive(new_overlap) < 3:
                            continue  # Doesn't fit well enough

                        # Try to find a member who can move to another group
                        for member_id in full_group['members']:
                            member_hours = availability_map[member_id]

                            # Find a non-full group this member could join
                            for target_group in groups_with_room:
                                if target_group == full_group:
                                    continue

                                # Check if member fits target group
                                member_overlap = target_group['overlap_hours'] & member_hours
                                if calculate_best_consecutive(member_overlap) >= 3:
                                    # Do the swap: move member to target, add ungrouped to full group
                                    full_group['members'].remove(member_id)
                                    full_group['members'].append(player_id)
                                    # Recalculate full group's overlap
                                    full_group['overlap_hours'] = set.intersection(
                                        *[availability_map[m] for m in full_group['members']]
                                    )

                                    target_group['members'].append(member_id)
                                    target_group['overlap_hours'] &= member_hours

                                    ungrouped_ids.remove(player_id)

                                    # Update groups_with_room if target is now full
                                    if len(target_group['members']) >= session.max_group_size:
                                        groups_with_room.remove(target_group)

                                    break  # Found a swap, move to next ungrouped player
                            else:
                                continue  # No target found for this member, try next member
                            break  # Swap done, move to next ungrouped player
                        else:
                            continue  # No swappable member found, try next full group
                        break  # Swap done

                    if not groups_with_room:
                        break  # No more room anywhere

        # Fourth pass: multi-player swaps - try swapping 2 players out of a full group
        # to make room for 2 ungrouped players
        if session.min_group_size != session.max_group_size and len(ungrouped_ids) >= 2:
            groups_with_room = [g for g in groups_data if len(g['members']) < session.max_group_size]

            if groups_with_room:
                # Try pairs of ungrouped players
                for i, player1_id in enumerate(ungrouped_ids[:]):
                    if player1_id not in ungrouped_ids:
                        continue
                    player1_hours = availability_map[player1_id]

                    for player2_id in ungrouped_ids[i+1:]:
                        if player2_id not in ungrouped_ids:
                            continue
                        player2_hours = availability_map[player2_id]

                        # Find a full group where both ungrouped players would fit
                        for full_group in groups_data:
                            if len(full_group['members']) < session.max_group_size:
                                continue

                            # Check if both ungrouped players fit this group
                            overlap_with_p1 = full_group['overlap_hours'] & player1_hours
                            overlap_with_both = overlap_with_p1 & player2_hours
                            if calculate_best_consecutive(overlap_with_both) < 3:
                                continue

                            # Try to find 2 members who can move to other groups
                            for m1_idx, member1_id in enumerate(full_group['members']):
                                member1_hours = availability_map[member1_id]

                                # Find a group for member1
                                target1 = None
                                for tg in groups_with_room:
                                    if tg == full_group:
                                        continue
                                    if calculate_best_consecutive(tg['overlap_hours'] & member1_hours) >= 3:
                                        target1 = tg
                                        break

                                if not target1:
                                    continue

                                for member2_id in full_group['members'][m1_idx+1:]:
                                    member2_hours = availability_map[member2_id]

                                    # Find a group for member2 (can be same as target1 if room)
                                    target2 = None
                                    for tg in groups_with_room:
                                        if tg == full_group:
                                            continue
                                        # Check if target1 has room for both
                                        if tg == target1:
                                            if len(tg['members']) + 2 <= session.max_group_size:
                                                if calculate_best_consecutive(tg['overlap_hours'] & member2_hours) >= 3:
                                                    target2 = tg
                                                    break
                                        else:
                                            if len(tg['members']) < session.max_group_size:
                                                if calculate_best_consecutive(tg['overlap_hours'] & member2_hours) >= 3:
                                                    target2 = tg
                                                    break

                                    if not target2:
                                        continue

                                    # Do the double swap
                                    full_group['members'].remove(member1_id)
                                    full_group['members'].remove(member2_id)
                                    full_group['members'].append(player1_id)
                                    full_group['members'].append(player2_id)
                                    full_group['overlap_hours'] = set.intersection(
                                        *[availability_map[m] for m in full_group['members']]
                                    )

                                    target1['members'].append(member1_id)
                                    target1['overlap_hours'] &= member1_hours

                                    target2['members'].append(member2_id)
                                    target2['overlap_hours'] &= member2_hours

                                    ungrouped_ids.remove(player1_id)
                                    ungrouped_ids.remove(player2_id)

                                    # Update groups_with_room
                                    groups_with_room = [g for g in groups_data if len(g['members']) < session.max_group_size]

                                    break  # Done with this pair
                                else:
                                    continue
                                break
                            else:
                                continue
                            break
                        else:
                            continue
                        break

        # Fifth pass: try to form new groups from ungrouped players with looser requirements
        if len(ungrouped_ids) >= session.min_group_size:
            # Try to form groups with 2-hour overlap minimum
            remaining_ungrouped = {pid: availability_map[pid] for pid in ungrouped_ids}

            # Simple greedy: pick first player, find compatible others
            while len(remaining_ungrouped) >= session.min_group_size:
                # Start with the player who has fewest remaining compatible players
                best_starter = None
                min_compatible = float('inf')

                for pid, hours in remaining_ungrouped.items():
                    compatible_count = sum(
                        1 for other_id, other_hours in remaining_ungrouped.items()
                        if other_id != pid and calculate_best_consecutive(hours & other_hours) >= 2
                    )
                    if compatible_count < min_compatible and compatible_count >= session.min_group_size - 1:
                        min_compatible = compatible_count
                        best_starter = pid

                if not best_starter:
                    break

                # Build group starting with this player
                new_group_members = [best_starter]
                new_group_hours = remaining_ungrouped[best_starter].copy()

                # Find compatible players
                candidates = []
                for other_id, other_hours in remaining_ungrouped.items():
                    if other_id == best_starter:
                        continue
                    overlap = new_group_hours & other_hours
                    consecutive = calculate_best_consecutive(overlap)
                    if consecutive >= 2:
                        candidates.append((other_id, consecutive, overlap))

                # Sort by overlap quality
                candidates.sort(key=lambda x: -x[1])

                for candidate_id, _, _ in candidates:
                    if len(new_group_members) >= session.min_group_size:
                        break
                    candidate_hours = remaining_ungrouped[candidate_id]
                    new_overlap = new_group_hours & candidate_hours
                    if calculate_best_consecutive(new_overlap) >= 2:
                        new_group_members.append(candidate_id)
                        new_group_hours = new_overlap

                if len(new_group_members) >= session.min_group_size:
                    groups_data.append({
                        'members': new_group_members,
                        'overlap_hours': new_group_hours
                    })
                    for pid in new_group_members:
                        ungrouped_ids.remove(pid)
                        del remaining_ungrouped[pid]
                else:
                    # Couldn't form a group, stop trying
                    break

        # Create PlayerGroup objects and SessionPlayer records for grouped players
        for i, group_data in enumerate(groups_data, 1):
            group = PlayerGroup.objects.create(
                session=session,
                group_number=i,
                name=generate_name(i, NameConvention(session.naming_convention)),
                all_hours=[],
                overlap_hours=sorted(list(group_data['overlap_hours'])),
                total_overlap_hours=len(group_data['overlap_hours']),
            )

            for profile_id in group_data['members']:
                response = response_map.get(profile_id)
                availability = list(availability_map.get(profile_id, set()))
                SessionPlayer.objects.create(
                    session=session,
                    profile_id=profile_id,
                    survey_response=response,
                    group=group,
                    status=SessionPlayer.StatusChoices.GROUPED,
                    added_via=SessionPlayer.AddedViaChoices.ALGORITHM,
                    availability_hours=sorted(availability)
                )

            group.recalculate_overlap()

        # Record ungrouped players as SessionPlayer records
        for profile_id in ungrouped_ids:
            response = response_map.get(profile_id)
            availability = list(availability_map.get(profile_id, set()))

            # Determine reason
            if profile_id in waitlist_ids:
                reason = SessionPlayer.ReasonChoices.WAITLIST
            elif not availability:
                reason = SessionPlayer.ReasonChoices.LOW_AVAILABILITY
            else:
                reason = SessionPlayer.ReasonChoices.NO_COMPATIBLE

            SessionPlayer.objects.create(
                session=session,
                profile_id=profile_id,
                survey_response=response,
                group=None,
                status=SessionPlayer.StatusChoices.UNGROUPED,
                reason=reason,
                availability_hours=sorted(availability)
            )

        # Calculate best fit for ungrouped players
        cls.calculate_best_fit_groups(session)

        session.recalculate_statistics()

    @classmethod
    def populate_ungrouped_from_players(cls, session):
        """
        Load tournament/round players as ungrouped (for manual/random grouping).
        """
        # Clear existing session players
        session.session_players.all().delete()

        # Get source master session (read from SessionPlayer)
        source_session = None
        if session.round:
            # Try round's master session first
            source_session = session.round.master_session
            # If round has no session, it inherits from tournament
            if not source_session and session.tournament:
                source_session = session.tournament.master_session
        elif session.tournament:
            # Use tournament's master session
            source_session = session.tournament.master_session

        if not source_session:
            return

        # Copy ungrouped players from source master session
        for sp in source_session.session_players.filter(status='ungrouped'):
            SessionPlayer.objects.create(
                session=session,
                profile=sp.profile,
                status=SessionPlayer.StatusChoices.UNGROUPED,
                reason=SessionPlayer.ReasonChoices.PENDING
            )

        session.recalculate_statistics()

    @classmethod
    def populate_ungrouped_from_survey(cls, session):
        """
        Load survey respondents as ungrouped (for manual grouping from survey).
        Respects session.total_players to limit how many waitlist players are included.
        """
        from the_tavern.models import SurveyResponse

        if not session.survey:
            raise ValueError("Session must have a survey")

        # Clear existing session players
        session.session_players.all().delete()

        # Get responses ordered by position (accepted first, then waitlist in order)
        responses_qs = session.survey.responses.filter(
            profile__isnull=False
        ).select_related('profile').order_by('response_position')

        include_waitlist = session.include_waitlist
        threshold = session.survey.waitlist_threshold

        # Filter by waitlist if needed
        if session.survey.has_waitlist and threshold and not include_waitlist:
            responses_qs = responses_qs.filter(response_position__lte=threshold)

        # Limit to total_players if set (respects waitlist count selection)
        if session.total_players > 0:
            responses_qs = responses_qs[:session.total_players]

        for response in responses_qs:
            is_from_waitlist = (
                session.survey.has_waitlist and threshold and
                response.response_position > threshold
            )

            # Get availability if any
            availability = response.get_combined_availability_hours()

            SessionPlayer.objects.create(
                session=session,
                profile=response.profile,
                survey_response=response,
                status=SessionPlayer.StatusChoices.UNGROUPED,
                reason=SessionPlayer.ReasonChoices.WAITLIST if is_from_waitlist else SessionPlayer.ReasonChoices.PENDING,
                availability_hours=sorted(list(availability)) if availability else []
            )

        session.recalculate_statistics()

    @classmethod
    def populate_ungrouped_from_round_session(cls, session, round):
        """
        Populate a grouping session with players from the round's master session.
        Only includes SessionPlayers with status='ungrouped' from the master session.
        SessionPlayers with status='waitlist' are NOT included automatically.
        IMPORTANT: Preserves availability_hours from master session if present.
        """
        if not round:
            raise ValueError("Round must be provided")

        # Clear existing session players
        session.session_players.all().delete()

        # Get round's master session
        master_session = round.master_session
        if not master_session:
            # No players to populate
            session.recalculate_statistics()
            return

        # Get ungrouped players from master session
        master_ungrouped = master_session.session_players.filter(
            status=SessionPlayer.StatusChoices.UNGROUPED
        ).select_related('profile')

        # Create SessionPlayer in grouping session for each
        for master_sp in master_ungrouped:
            SessionPlayer.objects.create(
                session=session,
                profile=master_sp.profile,
                status=SessionPlayer.StatusChoices.UNGROUPED,
                added_via=SessionPlayer.AddedViaChoices.MANUAL,
                availability_hours=master_sp.availability_hours,  # Preserve availability data
            )

        # Recalculate statistics
        session.recalculate_statistics()

    @classmethod
    @transaction.atomic
    def generate_random_groups(cls, session):
        """
        Randomly assign ungrouped players to groups, minimizing ungrouped players.
        """
        import random

        ungrouped = list(session.session_players.filter(status=SessionPlayer.StatusChoices.UNGROUPED))
        total_ungrouped = len(ungrouped)
        print(f'Total ungrouped: {total_ungrouped}')
        
        if total_ungrouped == 0:
            print('No ungrouped players')
            return
        
        # Calculate optimal group distribution
        min_size = session.min_group_size
        max_size = session.max_group_size
        print(f'Min size: {min_size}, Max size: {max_size}')
        
        # Determine how many complete groups we can make
        best_config = None
        min_leftover = total_ungrouped
        
        for num_groups in range(1, (total_ungrouped // min_size) + 2):
            base_size = total_ungrouped // num_groups
            extra = total_ungrouped % num_groups
            
            print(f'Trying {num_groups} groups: base_size={base_size}, extra={extra}')
            
            # NEW: When min_size == max_size, we can only make exact-size groups
            if min_size == max_size:
                # Can we make at least 'num_groups' full groups of exact size?
                if num_groups * min_size <= total_ungrouped:
                    leftover = total_ungrouped - (num_groups * min_size)
                    print(f'  Valid config found! ({num_groups} groups of {min_size}, {leftover} leftover)')
                else:
                    print(f'  Skipped: not enough players for {num_groups} groups of {min_size}')
                    continue
            else:
                # Check if distribution is valid
                if base_size < min_size:
                    print(f'  Skipped: base_size {base_size} < min_size {min_size}')
                    continue
                if base_size > max_size:
                    print(f'  Skipped: base_size {base_size} > max_size {max_size}')
                    continue

                # Flexible sizing
                if base_size + 1 > max_size and extra > 0:
                    print(f'  Skipped: base_size+1 ({base_size+1}) > max_size {max_size}')
                    continue
                leftover = 0
                print(f'  Valid config found!')
            
            if leftover < min_leftover:
                min_leftover = leftover
                # FIX: Store min_size when min==max, not base_size
                actual_group_size = min_size if min_size == max_size else base_size
                best_config = (num_groups, actual_group_size, extra)
                print(f'  New best config: {best_config}')
        
        if best_config is None:
            print('No valid configuration found!')
            return
        
        num_groups, base_size, extra = best_config
        print(f'Final config: {num_groups} groups, base_size={base_size}, extra={extra}')
        
        
        # Shuffle for randomness
        random.shuffle(ungrouped)
        
        # Assign players to groups
        group_number = session.groups.count() + 1
        player_index = 0
        
        for i in range(num_groups):
            # Determine size of this group
            if min_size == max_size:
                group_size = base_size
            else:
                group_size = base_size + (1 if i < extra else 0)
            
            # Create group
            current_group = PlayerGroup.objects.create(
                session=session,
                group_number=group_number,
                name=generate_name(group_number, NameConvention(session.naming_convention)),
                all_hours=[],
            )
            print(session.naming_convention)
            print(NameConvention(session.naming_convention))
            group_number += 1
            
            # Add players to this group
            for _ in range(group_size):
                if player_index >= len(ungrouped):
                    break
                
                session_player = ungrouped[player_index]
                session_player.group = current_group
                session_player.status = SessionPlayer.StatusChoices.GROUPED
                session_player.added_via = SessionPlayer.AddedViaChoices.ALGORITHM
                session_player.save()
                player_index += 1
        
        # Any remaining players stay ungrouped (should be minimal or zero)
        # Update their reason if needed
        if player_index < len(ungrouped):
            for remaining in ungrouped[player_index:]:
                remaining.reason = SessionPlayer.ReasonChoices.GROUPS_FULL
                remaining.save()

        session.recalculate_statistics()

    @classmethod
    def calculate_best_fit_groups(cls, session):
        """
        For each ungrouped player, find the group they'd fit best in.
        """
        if session.grouping_type != GroupingSession.GroupingTypeChoices.AVAILABILITY:
            return

        groups = list(session.groups.all())
        if not groups:
            return

        for session_player in session.session_players.filter(status=SessionPlayer.StatusChoices.UNGROUPED):
            if not session_player.availability_hours:
                continue

            player_hours = set(session_player.availability_hours)
            best_group = None
            best_overlap = 0

            for group in groups:
                if not group.overlap_hours:
                    continue
                group_hours = set(group.overlap_hours)
                overlap = player_hours & group_hours
                overlap_count = len(overlap)

                if overlap_count > best_overlap:
                    best_overlap = overlap_count
                    best_group = group

            if best_group:
                session_player.best_fit_group = best_group
                session_player.best_fit_overlap_hours = best_overlap
                session_player.save(update_fields=['best_fit_group', 'best_fit_overlap_hours'])

    @classmethod
    @transaction.atomic
    def create_groups_from_ungrouped(cls, session, min_hours=None):
        """
        Create new groups from ungrouped players using cascading hour requirements.

        Args:
            session: GroupingSession instance
            min_hours: Starting minimum consecutive hours (default: 4, will cascade to 3)
        """
        ungrouped = list(session.session_players.filter(status=SessionPlayer.StatusChoices.UNGROUPED))
        if not ungrouped:
            return

        # Build availability map from ungrouped players
        availability_map = {}
        player_map = {}  # profile_id -> SessionPlayer

        for sp in ungrouped:
            if sp.availability_hours:
                availability_map[sp.profile_id] = set(sp.availability_hours)
                player_map[sp.profile_id] = sp

        if not availability_map:
            return

        grouping_func = greedy_group_assignment_with_restarts

        # Use cascading hours: start from min_hours or 4, go down to 3
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
                min_size=session.min_group_size,
                max_size=session.max_group_size,
                min_consecutive=min_h,
                min_days=1,
            )
            groups_data.extend(new_groups)
            remaining_ids = still_ungrouped


        # Get next group number
        max_group_num = session.groups.aggregate(
            max_num=Max('group_number')
        )['max_num'] or 0

        # Create new groups
        for i, group_data in enumerate(groups_data, max_group_num + 1):
            group = PlayerGroup.objects.create(
                session=session,
                group_number=i,
                name=generate_name(i, NameConvention(session.naming_convention)),
                all_hours=[],
                overlap_hours=sorted(list(group_data['overlap_hours'])),
                total_overlap_hours=len(group_data['overlap_hours']),
            )

            for profile_id in group_data['members']:
                # Update SessionPlayer to be in the group
                sp = player_map.get(profile_id)
                if sp:
                    sp.group = group
                    sp.status = SessionPlayer.StatusChoices.GROUPED
                    sp.added_via = SessionPlayer.AddedViaChoices.ALGORITHM
                    sp.save()

            group.recalculate_overlap()

        # Update best fit for remaining ungrouped
        cls.calculate_best_fit_groups(session)
        session.recalculate_statistics()

    @classmethod
    @transaction.atomic
    def add_waitlist_to_session(cls, session, count=None):
        """
        Add waitlisted survey responses as ungrouped players.

        Args:
            session: GroupingSession instance
            count: Number of waitlist players to add (None = all)
        """
        from the_tavern.models import SurveyResponse

        if not session.survey or not session.survey.has_waitlist:
            return

        threshold = session.survey.waitlist_threshold
        if not threshold:
            return

        # Get profile IDs already in session
        existing_profile_ids = set(
            session.session_players.values_list('profile_id', flat=True)
        )

        waitlist_responses = session.survey.responses.filter(
            profile__isnull=False,
            response_position__gt=threshold
        ).exclude(
            profile_id__in=existing_profile_ids
        ).order_by('response_position')

        if count is not None:
            waitlist_responses = waitlist_responses[:count]

        for response in waitlist_responses:
            availability = response.get_combined_availability_hours()
            SessionPlayer.objects.create(
                session=session,
                profile=response.profile,
                survey_response=response,
                status=SessionPlayer.StatusChoices.UNGROUPED,
                reason=SessionPlayer.ReasonChoices.WAITLIST,
                availability_hours=sorted(list(availability))
            )

        # Update best fit
        cls.calculate_best_fit_groups(session)
        session.recalculate_statistics()

    @classmethod
    @transaction.atomic
    def recalculate_with_waitlist(cls, session, waitlist_count):
        """
        Clear existing groups, add N waitlist players, regenerate all groups.
        Only works in draft mode.

        Args:
            session: GroupingSession instance (must be in draft status)
            waitlist_count: Number of waitlist players to include
        """
        if session.status != GroupingSession.StatusChoices.DRAFT:
            raise ValueError("Can only recalculate sessions in draft status")

        # Clear everything
        session.groups.all().delete()
        session.session_players.all().delete()

        # Regenerate with waitlist included up to count
        cls._generate_with_waitlist_limit(session, waitlist_count)

    @classmethod
    def _generate_with_waitlist_limit(cls, session, waitlist_count):
        """
        Generate groups including a specific number of waitlist players.
        """
        from the_tavern.models import SurveyResponse

        if not session.survey:
            raise ValueError("Session must have a survey")

        threshold = session.survey.waitlist_threshold if session.survey.has_waitlist else None

        # Get all responses
        responses_qs = session.survey.responses.filter(
            profile__isnull=False
        ).select_related('profile').order_by('response_position')

        responses = []
        waitlist_added = 0

        for response in responses_qs:
            is_waitlist = (
                threshold and
                response.response_position > threshold
            )

            if is_waitlist:
                if waitlist_added < waitlist_count:
                    responses.append((response, True))
                    waitlist_added += 1
            else:
                responses.append((response, False))

        # Build availability map
        availability_map = {}
        response_map = {}
        waitlist_ids = set()

        for response, is_waitlist in responses:
            hours = response.get_combined_availability_hours()
            availability_map[response.profile_id] = hours
            response_map[response.profile_id] = response
            if is_waitlist:
                waitlist_ids.add(response.profile_id)

        # Select algorithm based on session configuration
        algorithm = getattr(session, 'algorithm', 'greedy')
        grouping_func = greedy_group_assignment_with_restarts

        # Use cascading hours: 5 → 4 → 3
        groups_data = []
        ungrouped_ids = list(availability_map.keys())

        for min_hours in [5, 4, 3]:
            if not ungrouped_ids:
                break

            remaining_availability = {pid: availability_map[pid] for pid in ungrouped_ids}
            new_groups, still_ungrouped, _ = grouping_func(
                remaining_availability,
                min_size=session.min_group_size,
                max_size=session.max_group_size,
                min_consecutive=min_hours,
                min_days=1,
            )
            groups_data.extend(new_groups)
            ungrouped_ids = still_ungrouped

        # Create groups and SessionPlayer records for grouped players
        for i, group_data in enumerate(groups_data, 1):
            group = PlayerGroup.objects.create(
                session=session,
                group_number=i,
                name=generate_name(i, NameConvention(session.naming_convention)),
                all_hours=[],
                overlap_hours=sorted(list(group_data['overlap_hours'])),
                total_overlap_hours=len(group_data['overlap_hours']),
            )

            for profile_id in group_data['members']:
                response = response_map.get(profile_id)
                availability = list(availability_map.get(profile_id, set()))
                SessionPlayer.objects.create(
                    session=session,
                    profile_id=profile_id,
                    survey_response=response,
                    group=group,
                    status=SessionPlayer.StatusChoices.GROUPED,
                    added_via=SessionPlayer.AddedViaChoices.ALGORITHM,
                    availability_hours=sorted(availability)
                )

            group.recalculate_overlap()

        # Record ungrouped as SessionPlayer records
        for profile_id in ungrouped_ids:
            response = response_map.get(profile_id)
            availability = list(availability_map.get(profile_id, set()))

            if profile_id in waitlist_ids:
                reason = SessionPlayer.ReasonChoices.WAITLIST
            elif not availability:
                reason = SessionPlayer.ReasonChoices.LOW_AVAILABILITY
            else:
                reason = SessionPlayer.ReasonChoices.NO_COMPATIBLE

            SessionPlayer.objects.create(
                session=session,
                profile_id=profile_id,
                survey_response=response,
                group=None,
                status=SessionPlayer.StatusChoices.UNGROUPED,
                reason=reason,
                availability_hours=sorted(availability)
            )

        cls.calculate_best_fit_groups(session)
        session.recalculate_statistics()

    @classmethod
    @transaction.atomic
    def regenerate_groups(cls, session):
        """
        Clear and regenerate all groups for a session.
        Only works in draft or processing mode.
        """
        if session.status not in [GroupingSession.StatusChoices.DRAFT, GroupingSession.StatusChoices.PROCESSING]:
            raise ValueError("Can only regenerate sessions in draft or processing status")

        if session.grouping_type == GroupingSession.GroupingTypeChoices.AVAILABILITY:
            cls.generate_availability_groups(session)
        elif session.grouping_type == GroupingSession.GroupingTypeChoices.RANDOM:
            session.groups.all().delete()
            cls.populate_ungrouped_from_players(session)
            cls.generate_random_groups(session)

    @classmethod
    @transaction.atomic
    def move_player(cls, from_group, to_group, profile, moved_by=None):
        """
        Move a player from one group to another.

        Args:
            from_group: PlayerGroup to remove from
            to_group: PlayerGroup to add to
            profile: Profile to move
            moved_by: Profile who is making the move (optional)
        """
        # Get existing session player
        session_player = SessionPlayer.objects.filter(
            group=from_group,
            profile=profile,
            status=SessionPlayer.StatusChoices.GROUPED
        ).first()

        if not session_player:
            raise ValueError(f"Player {profile} is not in group {from_group}")

        # Update to new group
        session_player.group = to_group
        session_player.added_by = moved_by
        session_player.added_via = SessionPlayer.AddedViaChoices.REASSIGNED
        session_player.save(update_fields=['group', 'added_by', 'added_via'])

        # Recalculate overlaps for both groups
        from_group.recalculate_overlap()
        to_group.recalculate_overlap()

        from_group.session.recalculate_statistics()

    @classmethod
    @transaction.atomic
    def add_ungrouped_to_group(cls, session_player, group, added_by=None):
        """
        Add an ungrouped or waitlisted player to a group.

        Args:
            session_player: SessionPlayer instance (status='ungrouped' or 'waitlist')
            group: PlayerGroup to add to
            added_by: Profile who is making the addition (optional)
        """
        # Update session player to grouped status
        session_player.group = group
        session_player.status = SessionPlayer.StatusChoices.GROUPED
        session_player.added_by = added_by
        session_player.added_via = SessionPlayer.AddedViaChoices.MANUAL
        session_player.reason = None
        session_player.best_fit_group = None
        session_player.best_fit_overlap_hours = 0
        session_player.save(update_fields=[
            'group', 'status', 'added_by', 'added_via',
            'reason', 'best_fit_group', 'best_fit_overlap_hours'
        ])

        group.recalculate_overlap()
        session_player.session.recalculate_statistics()

    @classmethod
    @transaction.atomic
    def assign_player_to_group(cls, session_player, to_group, moved_by=None):
        """
        Universal method to assign a player to a group or ungrouped status.
        Handles all transitions: ungrouped→group, waitlist→group, group→group,
        group→ungrouped, group→waitlist.

        Args:
            session_player: SessionPlayer instance to move
            to_group: PlayerGroup to assign to (None for ungrouped status)
            moved_by: Profile making the change (optional)
        """
        # Track the source group if player is currently grouped
        from_group = session_player.group if session_player.status == SessionPlayer.StatusChoices.GROUPED else None

        # Update session player
        session_player.group = to_group

        # Set status based on target
        if to_group is None:
            session_player.status = SessionPlayer.StatusChoices.UNGROUPED
            session_player.added_via = SessionPlayer.AddedViaChoices.MANUAL
        else:
            session_player.status = SessionPlayer.StatusChoices.GROUPED
            # Determine added_via based on previous state
            if from_group:
                session_player.added_via = SessionPlayer.AddedViaChoices.REASSIGNED
            else:
                session_player.added_via = SessionPlayer.AddedViaChoices.MANUAL

        session_player.added_by = moved_by
        session_player.reason = None
        session_player.best_fit_group = None
        session_player.best_fit_overlap_hours = 0
        session_player.save(update_fields=[
            'group', 'status', 'added_by', 'added_via',
            'reason', 'best_fit_group', 'best_fit_overlap_hours'
        ])

        # Recalculate overlaps for affected groups
        if from_group:
            from_group.recalculate_overlap()
        if to_group:
            to_group.recalculate_overlap()

        # Recalculate session statistics
        session_player.session.recalculate_statistics()

    @classmethod
    @transaction.atomic
    def remove_from_group(cls, session_player, reason=None):
        """
        Remove a player from a group and set to ungrouped.

        Args:
            session_player: SessionPlayer instance (status='grouped')
            reason: SessionPlayer.ReasonChoices (default: MANUAL)
        """
        session = session_player.session
        group = session_player.group

        # Get availability if available
        availability = []
        if session_player.survey_response:
            hours = session_player.survey_response.get_combined_availability_hours()
            availability = sorted(list(hours))

        # Update to ungrouped status
        session_player.group = None
        session_player.status = SessionPlayer.StatusChoices.UNGROUPED
        session_player.reason = reason or SessionPlayer.ReasonChoices.MANUAL
        session_player.availability_hours = availability
        session_player.save(update_fields=['group', 'status', 'reason', 'availability_hours'])

        # Recalculate group overlap
        if group:
            group.recalculate_overlap()

        # Update best fit
        cls.calculate_best_fit_groups(session)
        session.recalculate_statistics()

    @classmethod
    @transaction.atomic
    def finalize_session(cls, session, target_round=None):
        """
        Finalize groups and optionally assign to a round.
        Deletes all other draft/processing sessions for the same survey.

        Args:
            session: GroupingSession instance
            target_round: Round to assign groups to (optional, creates if needed)
        """
        if target_round:
            session.round = target_round

        session.status = GroupingSession.StatusChoices.FINALIZED
        session.save()

        # Delete other draft/processing sessions for this survey
        if session.survey:
            GroupingSession.objects.filter(
                survey=session.survey
            ).exclude(
                id=session.id
            ).filter(
                status__in=[
                    GroupingSession.StatusChoices.DRAFT,
                    GroupingSession.StatusChoices.PROCESSING
                ]
            ).delete()

        # NOTE: Removed write-back to round.players M2M field
        # The round.players property now reads FROM this session's SessionPlayer records
        # No need to write back to M2M - the master session pattern makes this unnecessary

    @classmethod
    @transaction.atomic
    def move_to_waitlist(cls, session_player):
        """
        Move a player to the session waitlist.
        Updates their SurveyResponse.response_position if applicable.

        Args:
            session_player: SessionPlayer instance (status='grouped' or 'ungrouped')
        """
        session = session_player.session
        group = session_player.group

        # Update to waitlist status
        session_player.group = None
        session_player.status = SessionPlayer.StatusChoices.WAITLIST
        session_player.reason = SessionPlayer.ReasonChoices.WAITLIST
        session_player.best_fit_group = None
        session_player.best_fit_overlap_hours = 0
        session_player.save(update_fields=[
            'group', 'status', 'reason', 'best_fit_group', 'best_fit_overlap_hours'
        ])

        # Recalculate group overlap if was in a group
        if group:
            group.recalculate_overlap()

        # Update survey response position if applicable
        if session_player.survey_response and session.survey:
            survey = session.survey
            if survey.waitlist_threshold:
                # Move response to end of waitlist
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
        Move a player from the session waitlist to ungrouped or a group.
        Updates their SurveyResponse.response_position if applicable.

        Args:
            session_player: SessionPlayer instance (status='waitlist')
            to_group: PlayerGroup to add to (optional, defaults to ungrouped)
        """
        session = session_player.session

        if to_group:
            # Add directly to group
            session_player.group = to_group
            session_player.status = SessionPlayer.StatusChoices.GROUPED
            session_player.reason = None
        else:
            # Add to ungrouped pool
            session_player.group = None
            session_player.status = SessionPlayer.StatusChoices.UNGROUPED
            session_player.reason = SessionPlayer.ReasonChoices.PENDING

        session_player.added_via = SessionPlayer.AddedViaChoices.MANUAL
        session_player.save(update_fields=['group', 'status', 'reason', 'added_via'])

        # Recalculate group overlap if added to a group
        if to_group:
            to_group.recalculate_overlap()

        # Update survey response position if applicable
        if session_player.survey_response and session.survey:
            survey = session.survey
            if survey.waitlist_threshold:
                # Move response to accepted (before waitlist threshold)
                # Find the current waitlist threshold position
                threshold = survey.waitlist_threshold
                # Shift all responses at or after threshold up by 1
                survey.responses.filter(
                    response_position__gte=threshold
                ).update(response_position=F('response_position') + 1)
                # Place this response at the threshold position
                session_player.survey_response.response_position = threshold
                session_player.survey_response.save(update_fields=['response_position'])

        session.recalculate_statistics()
        cls.calculate_best_fit_groups(session)

    @classmethod
    @transaction.atomic
    def add_survey_response_to_session(cls, session, survey_response, to_group=None, added_by=None):
        """
        Add a survey response (from survey waitlist) to the grouping session.
        Creates a new SessionPlayer for the response.

        Args:
            session: GroupingSession instance
            survey_response: SurveyResponse to add
            to_group: PlayerGroup to add to (optional, defaults to ungrouped)
            added_by: Profile who is making the addition (optional)

        Returns:
            SessionPlayer: The created session player
        """
        # Get availability hours
        availability = []
        hours = survey_response.get_combined_availability_hours()
        if hours:
            availability = sorted(list(hours))

        if to_group:
            status = SessionPlayer.StatusChoices.GROUPED
            reason = None
        else:
            status = SessionPlayer.StatusChoices.UNGROUPED
            reason = SessionPlayer.ReasonChoices.PENDING

        session_player = SessionPlayer.objects.create(
            session=session,
            profile=survey_response.profile,
            survey_response=survey_response,
            group=to_group,
            status=status,
            added_via=SessionPlayer.AddedViaChoices.MANUAL,
            added_by=added_by,
            reason=reason,
            availability_hours=availability
        )

        # Recalculate group overlap if added to a group
        if to_group:
            to_group.recalculate_overlap()

        # Update survey response position (move from waitlist to accepted)
        survey = session.survey
        if survey and survey.waitlist_threshold:
            threshold = survey.waitlist_threshold
            # Shift all responses at or after threshold up by 1
            survey.responses.filter(
                response_position__gte=threshold
            ).update(response_position=F('response_position') + 1)
            # Place this response at the threshold position
            survey_response.response_position = threshold
            survey_response.save(update_fields=['response_position'])

        session.recalculate_statistics()
        cls.calculate_best_fit_groups(session)

        return session_player
