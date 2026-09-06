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
