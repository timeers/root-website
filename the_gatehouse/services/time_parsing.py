"""
Parsing user-typed times from Discord slash commands into aware UTC datetimes.

Kept free of Django-request and Discord-payload concerns so it can be unit-tested
directly. The only hard rule here: never silently guess. Anything ambiguous either
returns an error for the caller to surface, or is resolved in a way the caller then
shows back to the user for confirmation (see /schedule's confirm step) — Discord
renders `<t:...>` in each viewer's own timezone, so a misparse is visible before
anything is written.
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
    # "america" that would otherwise surface obscure entries first).
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
    an absolute date+time interpreted in `tz_name`. Deliberately does NOT accept
    relative offsets ("in 2 hours") or a bare time with no date.
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

    supplied = _supplied_fields(text, tzinfo)
    if supplied is None:
        return None, (
            "I couldn't read that as a date and time. Try something like "
            '`2026-03-15 20:00`, `Mar 15 8pm`, or paste a `<t:...>` timestamp.'
        )

    # dateutil happily returns midnight for a date-only input, which would silently
    # schedule a match at 00:00. Require an explicit time of day.
    if not ({"hour", "minute"} & supplied):
        return None, "Please include a time of day, e.g. `Mar 15 8pm`."
    # A bare time with no date would need us to guess which day. Don't.
    if not ({"month", "day"} & supplied):
        return None, "Please include a date, e.g. `Mar 15 8pm`."

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

    # Only roll forward when the user omitted the year — "Mar 15" in December means
    # next March. If they explicitly typed a past year, leave it and let the range
    # check reject it, rather than silently rewriting what they asked for.
    if "year" not in supplied and parsed < local_now:
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
