"""
Parsing user-typed times from Discord slash commands into aware UTC datetimes.

Kept free of Django-request and Discord-payload concerns so it can be unit-tested
directly. The rule here: never silently guess in a way the user can't see. Anything
ambiguous either returns an error for the caller to surface, or is resolved in a way
the caller then shows back to the user for confirmation (see /schedule's confirm
step) — Discord renders `<t:...>` in each viewer's own timezone, so a misparse is
visible before anything is written.

Dateless inputs lean on that confirm step. A bare time ("4pm"), a day word
("tomorrow 4pm") and a weekday ("friday 4pm") all resolve to the NEXT occurrence of
what was typed, rolling forward by a day or a week when the time has already gone.
The resolved date is always shown back before anything is written.
"""

import re
from datetime import datetime, timedelta, timezone as dt_timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

from dateutil import parser as date_parser

# Sentinel error: the input needs a timezone to be meaningful and we don't have one.
# The caller turns this into a "tell me your timezone once" prompt rather than a
# generic parse failure.
NEED_TIMEZONE = "NEED_TIMEZONE"

# `<t:1773612000:F>` — Discord's own timestamp markup, optionally with a style
# suffix. Pasted from generators like hammertime.cc. Unambiguous: it's a unix
# epoch, so it needs no timezone.
_DISCORD_TS_RE = re.compile(r"^<t:(\d{1,11})(?::[tTdDfFR])?>$")
# A bare epoch. Bounded to 9-11 digits so a year ("2026") or a date typed without
# separators ("20260315") can't be misread as a timestamp.
_BARE_EPOCH_RE = re.compile(r"^\d{9,11}$")

# Weekday names, matched LEXICALLY rather than through _supplied_fields. dateutil
# resolves a weekday by shifting the default's day, which moves identically in both
# probes below — so "friday 4pm" and a bare "4pm" both report just {"hour"} and are
# otherwise indistinguishable. Without this, "friday 4pm" would lose the word
# "friday" and get rolled to tomorrow.
_WEEKDAY_RE = re.compile(
    r"\b(mon|tue|tues|wed|thu|thur|thurs|fri|sat|sun"
    r"|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", re.I)

# A leading "today"/"tomorrow", stripped before dateutil sees the text — dateutil
# cannot parse either word and raises on them.
_DAY_KEYWORD_RE = re.compile(r"^\s*(today|tomorrow|tmrw|tmr)\b[\s,]*", re.I)

# Guardrails on the resulting datetime. A match scheduled years out is a typo far
# more often than it's real; one scheduled well in the past can't be played.
_MAX_FUTURE = timedelta(days=730)
_MAX_PAST = timedelta(days=1)

# Two sentinel dates that differ in every field. dateutil fills anything the input
# omitted from `default`, so parsing twice and diffing tells us exactly which
# fields the user actually supplied. This is the only reliable way to distinguish
# "8pm" (no date) from "Mar 15" (no time) — dateutil reports neither.
_PROBE_A = datetime(2001, 2, 3, 4, 5, 6)
_PROBE_B = datetime(2002, 3, 4, 5, 6, 7)


def valid_timezone(name):
    """True if `name` is a known IANA zone. Used to validate the command option
    before it's persisted to a Profile."""
    if not name:
        return False
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, KeyError):
        return False
    return True


# ── Timezone regions ─────────────────────────────────────────────────────────
# The /schedule follow-up asks for a timezone with two string selects: a region,
# then a city. A Discord select caps at 25 options and can't autocomplete, so each
# region's list is CURATED rather than derived from available_timezones() (~600
# entries, mostly redundant aliases). The `timezone` command option keeps its
# autocomplete over every zone, and is the escape hatch for anywhere not listed
# here — don't remove it without replacing that coverage.
#
# Region keys are two characters because they ride in component custom_ids.
# Labels are the city as a player would say it, with the offset appended at render
# time (a fixed offset can't live here — it changes with DST).
TIMEZONE_REGIONS = [
    {"key": "AM", "label": "Americas", "emoji": "🌎", "zones": [
        ("Pacific/Honolulu", "Honolulu (Hawaii)"),
        ("America/Anchorage", "Anchorage (Alaska)"),
        ("America/Los_Angeles", "Los Angeles (US Pacific)"),
        ("America/Vancouver", "Vancouver (Pacific)"),
        ("America/Phoenix", "Phoenix (Arizona, no DST)"),
        ("America/Denver", "Denver (US Mountain)"),
        ("America/Edmonton", "Edmonton (Mountain)"),
        ("America/Chicago", "Chicago (US Central)"),
        ("America/Winnipeg", "Winnipeg (Central)"),
        ("America/Mexico_City", "Mexico City"),
        ("America/Guatemala", "Guatemala City"),
        ("America/New_York", "New York (US Eastern)"),
        ("America/Toronto", "Toronto (Eastern)"),
        ("America/Bogota", "Bogotá"),
        ("America/Lima", "Lima"),
        ("America/Panama", "Panama City"),
        ("America/Halifax", "Halifax (Atlantic)"),
        ("America/Puerto_Rico", "San Juan (Atlantic)"),
        ("America/Caracas", "Caracas"),
        ("America/La_Paz", "La Paz"),
        ("America/Santiago", "Santiago"),
        ("America/Sao_Paulo", "São Paulo"),
        ("America/Argentina/Buenos_Aires", "Buenos Aires"),
        ("America/Montevideo", "Montevideo"),
        ("America/St_Johns", "St. John's (Newfoundland)"),
    ]},
    {"key": "EU", "label": "Europe & Africa", "emoji": "🌍", "zones": [
        ("Europe/London", "London (UK)"),
        ("Europe/Dublin", "Dublin"),
        ("Europe/Lisbon", "Lisbon"),
        ("Africa/Accra", "Accra"),
        ("Africa/Casablanca", "Casablanca"),
        ("Europe/Paris", "Paris"),
        ("Europe/Berlin", "Berlin"),
        ("Europe/Madrid", "Madrid"),
        ("Europe/Rome", "Rome"),
        ("Europe/Amsterdam", "Amsterdam"),
        ("Europe/Brussels", "Brussels"),
        ("Europe/Zurich", "Zurich"),
        ("Europe/Vienna", "Vienna"),
        ("Europe/Prague", "Prague"),
        ("Europe/Warsaw", "Warsaw"),
        ("Europe/Stockholm", "Stockholm"),
        ("Europe/Oslo", "Oslo"),
        ("Europe/Copenhagen", "Copenhagen"),
        ("Africa/Lagos", "Lagos"),
        ("Europe/Helsinki", "Helsinki"),
        ("Europe/Athens", "Athens"),
        ("Europe/Bucharest", "Bucharest"),
        ("Europe/Kyiv", "Kyiv"),
        ("Africa/Cairo", "Cairo"),
        ("Africa/Johannesburg", "Johannesburg"),
    ]},
    {"key": "AP", "label": "Asia & Oceania", "emoji": "🌏", "zones": [
        ("Asia/Jerusalem", "Jerusalem"),
        ("Asia/Riyadh", "Riyadh"),
        ("Asia/Tehran", "Tehran"),
        ("Asia/Dubai", "Dubai"),
        ("Asia/Karachi", "Karachi"),
        ("Asia/Almaty", "Almaty"),
        ("Asia/Kolkata", "India (Kolkata / Mumbai)"),
        ("Asia/Kathmandu", "Kathmandu"),
        ("Asia/Dhaka", "Dhaka"),
        ("Asia/Bangkok", "Bangkok"),
        ("Asia/Jakarta", "Jakarta"),
        ("Asia/Ho_Chi_Minh", "Ho Chi Minh City"),
        ("Asia/Singapore", "Singapore"),
        ("Asia/Manila", "Manila"),
        ("Asia/Hong_Kong", "Hong Kong"),
        ("Asia/Shanghai", "China (Shanghai / Beijing)"),
        ("Asia/Taipei", "Taipei"),
        ("Asia/Seoul", "Seoul"),
        ("Asia/Tokyo", "Tokyo"),
        ("Australia/Perth", "Perth"),
        ("Australia/Adelaide", "Adelaide"),
        ("Australia/Brisbane", "Brisbane"),
        ("Australia/Sydney", "Sydney / Melbourne"),
        ("Pacific/Auckland", "Auckland"),
        ("Pacific/Fiji", "Fiji"),
    ]},
    {"key": "UT", "label": "UTC / other", "emoji": "🕐", "zones": [
        ("UTC", "UTC (Coordinated Universal Time)"),
    ]},
]

# {iana: friendly label} across every region, for label lookups.
_ZONE_LABELS = {z: label for r in TIMEZONE_REGIONS for z, label in r["zones"]}

# Continent prefix -> region key, so a zone that ISN'T curated (set via the
# `timezone` option) still resolves to a sensible region to pre-select.
_REGION_BY_PREFIX = {
    "America": "AM", "Europe": "EU", "Africa": "EU", "Atlantic": "EU",
    "Asia": "AP", "Australia": "AP", "Pacific": "AP", "Indian": "AP",
}


def timezone_regions():
    """The curated region list, in display order."""
    return TIMEZONE_REGIONS


def zones_for_region(key):
    """[(iana, label)] for a region key, or [] when the key is unknown."""
    for region in TIMEZONE_REGIONS:
        if region["key"] == key:
            return region["zones"]
    return []


def timezone_label(name):
    """The friendly label for a zone, falling back to the IANA name so a zone set
    via the `timezone` option still displays sensibly."""
    return _ZONE_LABELS.get(name) or name or ""


def region_for_timezone(name):
    """The region key a zone belongs to, or None. Curated membership first, then
    the continent prefix so uncurated zones still land somewhere. Used only to
    pre-select a dropdown, so a wrong guess is cosmetic."""
    if not name:
        return None
    for region in TIMEZONE_REGIONS:
        if any(z == name for z, _label in region["zones"]):
            return region["key"]
    return _REGION_BY_PREFIX.get(name.split("/")[0], "UT")


def format_utc_offset(name, at=None):
    """`UTC-4` / `UTC+5:45` for a zone at a given instant, or "" if unknown.

    Minutes are shown only when non-zero — five curated zones are on a half or
    quarter hour (St John's, Tehran, Kolkata, Kathmandu, Adelaide)."""
    try:
        tzinfo = ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, KeyError, TypeError):
        return ""
    at = at or datetime.now(dt_timezone.utc)
    offset = at.astimezone(tzinfo).utcoffset()
    if offset is None:
        return ""
    total = int(offset.total_seconds())
    if total == 0:
        return "UTC"
    sign = "-" if total < 0 else "+"
    hours, minutes = divmod(abs(total) // 60, 60)
    return f"UTC{sign}{hours}:{minutes:02d}" if minutes else f"UTC{sign}{hours}"


def describe_timezone(name, at=None):
    """`New York (US Eastern) — UTC-4`, or "" when the zone isn't valid.

    `at` matters: the offset shown should be the one in effect at the SCHEDULED
    time, not today, or a booking across a DST boundary reads wrong."""
    if not valid_timezone(name):
        return ""
    offset = format_utc_offset(name, at)
    label = timezone_label(name)
    # A zone sitting exactly on UTC renders as plain "UTC"; appending that to a
    # label that already says so reads as a stutter.
    if not offset or offset in label:
        return label
    return f"{label} — {offset}"


def search_timezones(query, limit=25):
    """IANA zone names matching `query`, for the /schedule timezone autocomplete.

    An empty query returns common zones first — the raw `available_timezones()` set
    is alphabetical, which would otherwise open on Africa/Abidjan and friends."""
    zones = available_timezones()
    q = (query or "").strip().lower().replace(" ", "_")
    if q:
        matches = sorted(z for z in zones if q in z.lower())
    else:
        matches = []
    # Float widely-used zones to the top (of an empty query, or of a broad one like
    # "america" that would otherwise surface obscure entries first). Drawn from the
    # curated regions so the autocomplete and the picker can't drift apart.
    common = [
        "America/New_York", "America/Chicago", "America/Denver",
        "America/Los_Angeles", "Europe/London", "Europe/Paris", "Europe/Berlin",
        "Australia/Sydney", "Asia/Tokyo", "UTC",
    ]
    preferred = [z for z in common if z in zones and (not q or q in z.lower())]
    ordered = preferred + [z for z in matches if z not in preferred]
    return ordered[:limit]


def format_discord_timestamp(dt):
    """`<t:unix:F> (<t:unix:R>)` — an absolute time plus a relative hint, each
    rendered by Discord in the viewer's own timezone. Matches the format already
    used by build_upcoming_embed."""
    ts = int(dt.timestamp())
    return f"<t:{ts}:F> (<t:{ts}:R>)"


def _supplied_fields(text, tzinfo):
    """Which date/time fields the input actually contained.

    Returns a set drawn from {"year","month","day","hour","minute"}, or None if the
    text isn't parseable. Works by parsing against two different defaults and seeing
    which fields stayed pinned to their default rather than being overridden."""
    try:
        a = date_parser.parse(text, default=_PROBE_A.replace(tzinfo=tzinfo), fuzzy=False)
        b = date_parser.parse(text, default=_PROBE_B.replace(tzinfo=tzinfo), fuzzy=False)
    except (ValueError, OverflowError, TypeError):
        return None
    supplied = set()
    for field in ("year", "month", "day", "hour", "minute"):
        if getattr(a, field) == getattr(b, field):
            supplied.add(field)
    return supplied


def parse_user_datetime(text, tz_name=None, now=None):
    """Parse a user-supplied time string into an aware UTC datetime.

    Returns (datetime, None) on success or (None, error) on failure, where `error`
    is either the NEED_TIMEZONE sentinel or a user-facing message.

    Accepts a Discord `<t:...>` paste or bare epoch (both timezone-independent), or
    a date+time interpreted in `tz_name`. The date may be left off: a bare time
    ("4pm"), a leading day word ("tomorrow 4pm") and a weekday ("friday 4pm") each
    resolve to the next occurrence of what was typed. Deliberately does NOT accept
    relative offsets ("in 2 hours") or a bare date with no time of day.
    """
    text = (text or "").strip()
    if not text:
        return None, "Please provide a time."

    now = now or datetime.now(dt_timezone.utc)

    # 1) Discord timestamp paste / bare epoch — exact, no timezone needed.
    discord_ts = _DISCORD_TS_RE.match(text)
    epoch = discord_ts.group(1) if discord_ts else (
        text if _BARE_EPOCH_RE.match(text) else None)
    if epoch is not None:
        try:
            parsed = datetime.fromtimestamp(int(epoch), tz=dt_timezone.utc)
        except (ValueError, OSError, OverflowError):
            return None, "That timestamp isn't a valid date."
        return _validate_range(parsed, now)

    # 2) Absolute date + time. Needs a zone to be meaningful.
    if not tz_name:
        return None, NEED_TIMEZONE
    try:
        tzinfo = ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, ValueError, KeyError):
        return None, NEED_TIMEZONE

    # Strip a leading "today"/"tomorrow" before anything else looks at the text:
    # dateutil can't parse either word and raises, so leaving it in place would send
    # "tomorrow 4pm" to the unparseable branch below. `day_offset` stays None when
    # absent — 0 ("today") is meaningful and must not read as "not supplied".
    day_offset = None
    keyword = _DAY_KEYWORD_RE.match(text)
    if keyword:
        day_offset = 0 if keyword.group(1).lower() == "today" else 1
        text = _DAY_KEYWORD_RE.sub("", text, count=1)

    supplied = _supplied_fields(text, tzinfo)
    if supplied is None:
        return None, (
            "I couldn't read that as a date and time. Try something like "
            '`4pm`, `tomorrow 4pm`, `Mar 15 8pm`, or paste a `<t:...>` timestamp.'
        )

    # dateutil happily returns midnight for a date-only input, which would silently
    # schedule a match at 00:00. Require an explicit time of day.
    if not ({"hour", "minute"} & supplied):
        return None, "Please include a time of day, e.g. `Mar 15 8pm`."
    # How the day was expressed, if at all. Both flags drive the roll below, so
    # compute them once. `has_weekday` has to be lexical — see _WEEKDAY_RE.
    has_date = bool({"month", "day"} & supplied)
    has_weekday = bool(_WEEKDAY_RE.search(text))

    # "tomorrow Mar 15 8pm" / "tomorrow friday 4pm" name the day twice, and the two
    # can disagree. Ask rather than pick one.
    if day_offset is not None and (has_date or has_weekday):
        return None, ("Please give either a day word or a date, not both — "
                      "e.g. `tomorrow 4pm` or `Mar 15 8pm`.")

    # Reparse against a real default so unsupplied fields fall back to today in the
    # user's zone (rather than to a probe sentinel).
    local_now = now.astimezone(tzinfo)
    default = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    try:
        parsed = date_parser.parse(text, default=default, fuzzy=False)
    except (ValueError, OverflowError, TypeError):
        return None, "I couldn't read that as a date and time."

    if parsed.tzinfo is None:
        # Ambiguous local times (the repeated hour when DST ends) resolve to the
        # first/DST occurrence via fold=0, which is Python's default. Nonexistent
        # times (the skipped hour when DST starts) get normalized by the UTC
        # conversion below. Either way the confirm step shows the real result.
        parsed = parsed.replace(tzinfo=tzinfo)

    # Roll a past time forward to its next occurrence. Which unit to roll by depends
    # on how the day was expressed, and the two branches are mutually exclusive: a
    # dateless input has no year to roll, and rolling its year instead of its day
    # would miss by twelve months rather than one day.
    if not has_date:
        # `parsed` already sits on today's date — the default above is midnight today.
        if day_offset is not None:
            if day_offset == 0 and parsed < local_now:
                # An explicit "today" pins the date, so there's nothing to roll to.
                # _MAX_PAST is a 24h grace, so without this the range check below
                # would happily accept a time that has already gone.
                return None, ("That time has already passed today — try "
                              "`tomorrow` instead.")
            parsed += timedelta(days=day_offset)
        elif parsed < local_now:
            # A named weekday that's already gone means the one next week; a bare
            # time that's gone means tomorrow. timedelta (not .replace(day=...))
            # so month and year rollover come for free.
            parsed += timedelta(days=7 if has_weekday else 1)
    # Only roll forward when the user omitted the year — "Mar 15" in December means
    # next March. If they explicitly typed a past year, leave it and let the range
    # check reject it, rather than silently rewriting what they asked for.
    elif "year" not in supplied and parsed < local_now:
        try:
            parsed = parsed.replace(year=parsed.year + 1)
        except ValueError:
            # Feb 29 -> the following year isn't a leap year.
            parsed = parsed.replace(month=3, day=1, year=parsed.year + 1)

    return _validate_range(parsed.astimezone(dt_timezone.utc), now)


def _validate_range(parsed, now):
    """Reject datetimes far enough out of range to be a typo."""
    if parsed < now - _MAX_PAST:
        return None, "That time is in the past."
    if parsed > now + _MAX_FUTURE:
        return None, "That time is more than two years away — check the year."
    return parsed, None
