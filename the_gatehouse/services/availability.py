"""
Converting weekly availability between a player's local time and UTC.

Availability is stored as "hour-of-week" integers 0-167, where Monday 00:00 UTC is
0 and Sunday 23:00 UTC is 167 (hour_of_week = weekday * 24 + hour). That encoding is
shared with the survey answer path, so any two players' availability can be compared
with plain set algebra.

Kept free of Django-request concerns so it can be unit-tested directly.

WHY ZoneInfo AND NOT A STORED OFFSET
------------------------------------
The obvious implementation -- capture the browser's UTC offset as a number and do
arithmetic -- is what the survey path does, and it is wrong in three ways that only
show up later:

  * A fixed offset cannot survive a DST change. A schedule saved in July reads an
    hour off in December, silently.
  * Half-hour zones (Kolkata, St John's, Adelaide) truncate under int() arithmetic.
  * The offset describes one instant, but a weekly schedule spans a whole week, and
    a week can contain a DST transition.

Converting each hour through ZoneInfo against a reference week fixes all three: the
zone knows its own transition rules, so each hour is resolved with the offset that
actually applies to it.

SUB-HOUR ZONES ARE LOSSY BY DESIGN
----------------------------------
A player in a :30 or :45 zone has local hours that do not line up with UTC hours. The
0-167 model has no way to express half an hour, so such an hour is attributed to the
UTC hour that CONTAINS its start (i.e. rounded down). This is a deliberate, documented
limitation of the encoding rather than an arithmetic slip -- surface it in the UI for
affected zones rather than letting it skew quietly.
"""

from datetime import datetime, timedelta, timezone as dt_timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

HOURS_PER_WEEK = 168
HOURS_PER_DAY = 24

# Monday-first, matching the hour-of-week encoding (day_index = hour // 24) and the
# week the grid draws. Not calendar.day_name, which is locale-dependent and would
# desynchronize the labels from the integers.
DAY_LABELS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday',
              'Saturday', 'Sunday']

# An arbitrary reference Monday used to give each hour-of-week a real date, so
# ZoneInfo can apply the correct offset for that moment. 2024-01-01 is a Monday.
# It sits in January deliberately: northern-hemisphere zones are on standard time,
# which makes the reference week's behaviour easy to reason about in tests.
#
# A reference week can only carry ONE of a zone's DST states, so a schedule saved in
# summer and re-read in winter shifts by an hour relative to wall-clock. That is
# inherent to storing a repeating weekly pattern as fixed UTC hours; anchoring to a
# stable reference at least makes the behaviour deterministic and testable rather
# than dependent on the day the user happened to hit save.
_REFERENCE_MONDAY = datetime(2024, 1, 1)


def _zone_or_utc(tz_name):
    """ZoneInfo for `tz_name`, or UTC when it is missing or unrecognized.

    Falling back rather than raising keeps a bad or absent profile timezone from
    500-ing a page; UTC is the same assumption the rest of the site makes.
    """
    if not tz_name:
        return dt_timezone.utc
    try:
        return ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, ValueError, KeyError, TypeError):
        return dt_timezone.utc


def _normalize(hours):
    """Clean an iterable of hour-of-week values into a sorted, deduped, in-range list."""
    seen = set()
    for hour in hours or []:
        try:
            value = int(hour)
        except (TypeError, ValueError):
            continue
        if 0 <= value < HOURS_PER_WEEK:
            seen.add(value)
    return sorted(seen)


def local_to_utc_hours(local_hours, tz_name):
    """Map local hour-of-week ints to UTC hour-of-week ints for `tz_name`.

    Each local hour is placed on the reference week, converted through ZoneInfo, and
    re-derived as an hour-of-week -- so DST rules and sub-hour offsets are applied by
    the zone rather than by arithmetic here.

    Returns a sorted list. Out-of-range and non-integer values are dropped.
    """
    tzinfo = _zone_or_utc(tz_name)
    utc_hours = set()
    for hour in _normalize(local_hours):
        # Build the wall-clock time the user meant, then ask the zone what instant
        # that was. fold=0 resolves the ambiguous repeated hour of a DST fall-back
        # to the first occurrence; either choice is defensible for a weekly pattern.
        naive = _REFERENCE_MONDAY + timedelta(hours=hour)
        local_dt = naive.replace(tzinfo=tzinfo, fold=0)
        utc_dt = local_dt.astimezone(dt_timezone.utc)
        utc_hours.add(_hour_of_week(utc_dt))
    return sorted(utc_hours)


def utc_to_local_hours(utc_hours, tz_name):
    """Inverse of local_to_utc_hours, for rendering a stored schedule in local time.

    Returns a sorted list of local hour-of-week ints.
    """
    tzinfo = _zone_or_utc(tz_name)
    local_hours = set()
    for hour in _normalize(utc_hours):
        utc_dt = (_REFERENCE_MONDAY + timedelta(hours=hour)).replace(
            tzinfo=dt_timezone.utc
        )
        local_hours.add(_hour_of_week(utc_dt.astimezone(tzinfo)))
    return sorted(local_hours)


def week_start_for(date):
    """The Monday (ISO week start) of the calendar week containing `date`."""
    return date - timedelta(days=date.isoweekday() - 1)


def week_grid_cells(week_start, tz_name):
    """{(local_date, local_hour): [utc_hour_of_week, ...]} for all 168 real
    hours in the UTC week starting `week_start` (a date), keyed by where each
    one is drawn. Almost always exactly one UTC hour per (date, hour) key --
    the values ARE what's stored in PlayerSchedule.available_hours, so no
    conversion is needed to paint or save a week-specific grid, only to know
    WHICH (column, row) position each stored hour belongs in.

    NOT a strict bijection: a value-list, not a single value, because ONE
    local (date, hour) slot can legitimately hold two different real UTC
    hours during the week a DST zone "falls back" -- 1:00-1:59am happens
    twice that night, and both are real, independently storable hours. A
    (date, hour) with an empty list is the OTHER DST edge case ("spring
    forward": that local hour never occurs at all that week). Either way,
    every one of the 168 UTC hours is accounted for exactly once across the
    returned cells -- nothing is ever silently dropped.
    """
    tzinfo = _zone_or_utc(tz_name)
    cells = {}
    for utc_how in range(HOURS_PER_WEEK):
        utc_dt = (datetime.combine(week_start, datetime.min.time())
                  + timedelta(hours=utc_how)).replace(tzinfo=dt_timezone.utc)
        local_dt = utc_dt.astimezone(tzinfo)
        cells.setdefault((local_dt.date(), local_dt.hour), []).append(utc_how)
    return cells


def week_grid_columns(week_start, tz_name):
    """The local calendar dates one real UTC week spans, in order -- derived
    from week_grid_cells's keys. 8 dates whenever the viewer's current offset
    is non-zero (the two ends are partial); exactly 7 (no partial columns)
    only when the offset is exactly zero that week -- which is not a fixed
    per-timezone fact: a DST zone at its zero-offset time of year (e.g.
    Europe/London in GMT) gets a clean 7-column week too.
    """
    dates = {d for d, _hour in week_grid_cells(week_start, tz_name)}
    return sorted(dates)


def utc_instant_token(source_week_start, utc_how):
    """A real UTC hour, unambiguously identified: `(source_week_start,
    utc_how)` alone is not enough to compare across different weeks, since
    `utc_how` (0-167) is only meaningful relative to whichever week's
    `available_hours` it was read from -- the SAME bare int from two
    different weeks names two different real moments three weeks apart
    (confirmed by a genuine collision found during implementation: on a DST
    week, `week_start`'s own hour 4 and the FOLLOWING week's own hour 4
    landed in the same local slot's data, silently conflated, when the cell
    shape kept only the bare int). This token is the actual UTC instant as
    an ISO string, which both is unique by construction and needs no side
    table to interpret -- used as the grid's `data-how` and as the merge key
    between `local_week_cell_shape`'s per-slot UTC hours and a profile's own
    per-week hour lists.
    """
    utc_dt = (datetime.combine(source_week_start, datetime.min.time())
              + timedelta(hours=utc_how)).replace(tzinfo=dt_timezone.utc)
    return utc_dt.isoformat()


def local_week_cell_shape(week_start, tz_name):
    """{local_slot (0-167): [(source_week_start, utc_how), ...]} describing
    the SHAPE of the local Mon-Sun week that best corresponds to the real
    UTC week `week_start`, in `tz_name` -- which local (day, hour)
    positions are ordinary (one real UTC hour), split (two, a DST fall-back
    night's repeated local hour), or disabled (zero, a DST spring-forward
    gap). Independent of any profile's own availability -- this is the
    compare grid's per-cell layout, the read-only-multi-player equivalent of
    week_grid_cells for the single-user grid, except reassembled onto a
    FIXED 7-day local week instead of that real week's own (possibly 8)
    local calendar dates.

    Each entry is a `(source_week_start, utc_how)` PAIR, not a bare int --
    see utc_instant_token's docstring for why a bare 0-167 int is ambiguous
    once hours from more than one week are mixed together, which they
    always are here (the edge-fill-in below).

    A UTC hour near either end of `week_start` may belong to the ADJACENT
    real week's row once converted to local time: a positive-offset zone
    spills week_start's own LAST few UTC hours onto local NEXT Monday
    morning (leaving a gap at week_start's own start, filled by the
    PREVIOUS week's own late UTC hours); a negative-offset zone spills its
    FIRST few onto local PREVIOUS Sunday night (mirror case, filled by the
    NEXT week's own early UTC hours). This walks all three weeks' own 168
    UTC hours to account for every local slot 0-167 exactly once -- verified
    directly during planning that the in-range/spillover split from each of
    the three weeks never overlaps and always sums to 168 DISTINCT real
    instants (the earlier bare-int version summed to 168 but with one
    duplicate real instant double-counted and, necessarily, one local slot
    silently short a distinct hour -- fixed by keying on the real instant).
    """
    tzinfo = _zone_or_utc(tz_name)

    def _local_slots(base_date):
        slots = []
        for utc_how in range(HOURS_PER_WEEK):
            utc_dt = (datetime.combine(base_date, datetime.min.time())
                      + timedelta(hours=utc_how)).replace(tzinfo=dt_timezone.utc)
            local_dt = utc_dt.astimezone(tzinfo)
            slot = (local_dt.date() - base_date).days * HOURS_PER_DAY + local_dt.hour
            slots.append((utc_how, slot))
        return slots

    prev_week = week_start - timedelta(weeks=1)
    next_week = week_start + timedelta(weeks=1)

    shape = {slot: [] for slot in range(HOURS_PER_WEEK)}
    for utc_how, slot in _local_slots(week_start):
        if 0 <= slot < HOURS_PER_WEEK:
            shape[slot].append((week_start, utc_how))
    # See local_week_hours_for's old comment (same math, kept for the shift
    # direction reasoning): prev_week's own hours land in week_start's frame
    # shifted by -168; next_week's by +168.
    for utc_how, slot in _local_slots(prev_week):
        shifted = slot - HOURS_PER_WEEK
        if 0 <= shifted < HOURS_PER_WEEK:
            shape[shifted].append((prev_week, utc_how))
    for utc_how, slot in _local_slots(next_week):
        shifted = slot + HOURS_PER_WEEK
        if 0 <= shifted < HOURS_PER_WEEK:
            shape[shifted].append((next_week, utc_how))
    return shape


def general_pattern_local_slots(general_utc_hours, tz_name):
    """The general/standing row's hours as a set of (local_weekday, local_hour)
    pairs (weekday 0=Monday), via the existing utc_to_local_hours (reference-
    Monday) conversion then divmod. This is what "Copy from general" checks
    each live week-specific cell's (column.date.weekday(), row hour) against,
    to find which of a real week's UTC hours the general pattern implies.
    """
    local_hours = utc_to_local_hours(general_utc_hours, tz_name)
    return {divmod(h, HOURS_PER_DAY) for h in local_hours}


def copy_from_general_utc_hours(general_utc_hours, week_start, tz_name):
    """Which of THIS real week's UTC hours the general/standing pattern implies
    -- the seed for "Copy from general" under the UTC-keyed grid model.
    week_grid_cells' values are lists (see its docstring re: DST), so a
    matching slot contributes every UTC hour at that position -- both of a
    fall-back night's "1am" cells light up together if the general pattern
    says that weekday/hour is free, since the pattern doesn't know about this
    specific week's DST quirk and applies to both real hours that share the
    label.
    """
    slots = general_pattern_local_slots(general_utc_hours, tz_name)
    cells = week_grid_cells(week_start, tz_name)
    return sorted(
        utc_how
        for (local_date, local_hour), utc_hows in cells.items()
        if (local_date.weekday(), local_hour) in slots
        for utc_how in utc_hows
    )


def _hour_of_week(moment):
    """hour-of-week (0-167) for an aware datetime, Monday 00:00 = 0.

    weekday() is already Monday-based. The modulo matters: converting near either end
    of the reference week can land on the previous or next week (a Sunday 23:00 local
    hour in a positive-offset zone becomes Monday UTC), and that must wrap to the
    other end of the same week rather than running past 167 or below 0.
    """
    return (moment.weekday() * HOURS_PER_DAY + moment.hour) % HOURS_PER_WEEK


def hours_to_bitmask(hours):
    """Pack hour-of-week ints into a 168-bit int for fast overlap math."""
    mask = 0
    for hour in _normalize(hours):
        mask |= 1 << hour
    return mask


def overlap_count(mask_a, mask_b):
    """How many hours two packed schedules share."""
    return bin(mask_a & mask_b).count('1')


def describe_day_hour(hour_of_week):
    """('Monday', 14) for an hour-of-week int -- for labels and debugging."""
    from calendar import day_name
    return day_name[hour_of_week // HOURS_PER_DAY], hour_of_week % HOURS_PER_DAY


def format_hour_12(hour, compact=False):
    """A 0-23 hour as 12-hour text: '9:00 AM', or '9am' when compact.

    The compact form drops the ':00' because the grid repeats this label in all
    168 cells, where the full '12:00 PM' is too wide for a column that also has to
    stay tappable.
    """
    period = 'PM' if hour >= 12 else 'AM'
    display = 12 if hour % 12 == 0 else hour % 12
    if compact:
        return f"{display}{period.lower()}"
    return f"{display}:00 {period}"


def hour_labels():
    """[(hour, compact_label, full_label)] for the 24 rows of the weekly grid.

    Built here rather than in the template because Django templates can't do the
    12-hour arithmetic, and rather than in a filter because the view already
    assembles the grid's axes.
    """
    return [(hour, format_hour_12(hour, compact=True), format_hour_12(hour))
            for hour in range(HOURS_PER_DAY)]


# ── Comparing several players ────────────────────────────────────────────────
# The comparison page's data model. Everything below works on a plain
# {profile_id: hours} mapping so it serves any set of players -- one match's
# seats today, an arbitrary hand-picked set later.

# How many players can be missing before the exact count stops being useful.
# Past this the cell is just "far off", so it gets one flat bucket.
HEAT_BUCKETS = 4


def availability_matrix(hours_by_profile):
    """{profile_id: hours} -> {hour_of_week: [profile_id, ...]}.

    For each hour of the week, who is free then. The count is len() of the entry
    and full overlap is len() == number of players, so the heatmap, the summary
    and the tooltips all read off this one structure.

    Hours nobody has are absent rather than mapped to [] -- an empty cell and a
    cell where everyone is busy are the same thing to the renderer.
    """
    matrix = {}
    for profile_id, hours in (hours_by_profile or {}).items():
        for hour in hours or ():
            matrix.setdefault(hour, []).append(profile_id)
    return matrix


def heat_bucket(free_count, total_count):
    """Which colour band an hour falls in, keyed on how many players are MISSING.

    0 missing -> 'heat-0' (everyone free), then one band per missing player up to
    HEAT_BUCKETS-1, and 'heat-far' beyond that. Returns None when nobody is free,
    which the renderer draws as an empty cell rather than a colour.

    Keyed on missing rather than free so the colour answers "how close is this to
    working?" -- which is the question the page exists to answer.
    """
    if not free_count:
        return None
    missing = total_count - free_count
    if missing >= HEAT_BUCKETS:
        return 'heat-far'
    return f'heat-{missing}'


def reachable_buckets(total_count):
    """The bucket names a group of this size can actually produce.

    A group of 3 can never be missing 4, so a static legend would advertise bands
    that cannot occur. The legend is built from this instead.
    """
    if not total_count:
        return []
    names = []
    for missing in range(min(total_count, HEAT_BUCKETS)):
        names.append(f'heat-{missing}')
    if total_count > HEAT_BUCKETS:
        names.append('heat-far')
    return names


def overlap_summary(hours_by_profile):
    """Headline stats for a set of players: hours where ALL of them are free.

    Returns {'overlap_hours': sorted list, 'total': int, 'best_block': int,
    'days': int} -- the line above the grid.

    The two grouping helpers are imported lazily: the_warroom.models imports
    Profile from the_gatehouse at module level, so importing the_warroom up here
    would close the loop. They also require real SETS (they do `hours | {...}`
    and raise TypeError on a list), while schedules_for() hands back lists.
    """
    from the_warroom.services.grouping import (calculate_best_consecutive,
                                               calculate_days_with_overlap)

    sets = [set(hours) for hours in (hours_by_profile or {}).values() if hours]
    if not sets:
        return {'overlap_hours': [], 'total': 0, 'best_block': 0, 'days': 0}

    overlap = set.intersection(*sets)
    return {
        'overlap_hours': sorted(overlap),
        'total': len(overlap),
        'best_block': calculate_best_consecutive(overlap),
        'days': calculate_days_with_overlap(overlap),
    }
