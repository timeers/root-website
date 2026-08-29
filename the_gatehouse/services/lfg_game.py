"""Turning an LFGThread's captured Discord activity into game-form inputs.

An LFG thread accumulates curated state in three related tables: LFGSeat (who
sits where, written by /draft's seat button and /seating; its faction/vagabond
written by /pick), LFGRoll (an append-only log of every component surfaced by
/random, /map, /deck, the other lookups, /draft and /pick), and
LFGDraft/LFGDraftPick (the current draft). This module resolves them into the
shapes the record-game form needs, so the view stays thin.

Note the form narrows its options from the ROLL LOG, not the draft. /pick is why
it also writes rolls: a picked faction that isn't in the log would be dropped by
the narrowing below and never reach the form.

Kept in the_gatehouse (the app that owns LFGThread) but imports the_warroom
models lazily inside functions — the_warroom.models imports from
the_gatehouse.models at module level, so a top-level import here is circular.
"""

import re

from django.db.models import Q


# Collapse whitespace and strip leading decoration when comparing a thread title to
# a group name; thread names routinely pick up an emoji or separator prefix.
#
# Defined HERE rather than in discord_interactions so the two thread resolvers --
# _match_for_thread (which finds a Match) and player_group_for_channel (which finds
# a PlayerGroup) -- normalize titles identically. They used to be able to disagree
# about which threads matched, which is exactly the /schedule-finds-it-but-/seating-
# doesn't asymmetry this module now closes.
_TITLE_NOISE_RE = re.compile(r"^[^\w(]+|[^\w)]+$")
_WS_RE = re.compile(r"\s+")


def normalize_title(text):
    return _WS_RE.sub(" ", _TITLE_NOISE_RE.sub("", (text or "").strip())).strip().lower()


# Every `kind` an LFGThread roll can carry, mapped to the asset bucket it
# validates against. Source of truth: _LFG_LOOKUP_KIND in discord_interactions
# plus the /random and /draft capture paths.
#
# Two kinds don't map one-to-one onto Tournament's M2Ms:
#   Clockwork -> a Faction row with component="Clockwork"
#   Captain   -> a Vagabond row with captain=True
ROLL_KIND_TO_BUCKET = {
    "Faction": "factions",
    "Clockwork": "factions",
    "Map": "maps",
    "Deck": "decks",
    "Vagabond": "vagabonds",
    "Captain": "captains",
    "Landmark": "landmarks",
    "Hireling": "hirelings",
    "Tweak": "tweaks",
}


def link_group_thread(group, guild_id, channel_id):
    """Persist this thread as the group's `discord_thread`, once. Returns True when
    this call is the one that wrote it.

    Called ONLY after a group was resolved by TITLE: an id-resolved group is
    already linked by definition, and an ambiguous title resolves to None, so
    there's no group in hand to mislink.

    A conditional UPDATE, not save(): it's a compare-and-swap on
    discord_thread="" (so a link another request just wrote is never clobbered)
    and it skips PlayerGroup.save()'s derivation entirely.

    The URL shape mirrors LFGThread.thread_url() and must satisfy
    the_warroom.views._DISCORD_THREAD_URL_RE, which parses this field back out to
    decide where to announce a recorded game -- hence no trailing slash and no
    message-id suffix.

    NOTE this makes the group unreachable by the title fallback from now on (both
    resolvers only consider groups with discord_thread=""). That's the point --
    later lookups are by id -- but it does mean a WRONG title match cements
    itself, which is why both resolvers refuse to guess when a title is
    ambiguous."""
    from the_warroom.models import PlayerGroup

    if not group or not guild_id or not channel_id:
        return False
    if not str(guild_id).isdigit() or not str(channel_id).isdigit():
        return False

    url = f"https://discord.com/channels/{guild_id}/{channel_id}"
    linked = PlayerGroup.objects.filter(
        pk=group.pk, discord_thread="").update(discord_thread=url)
    if linked:
        # Keep the in-memory instance consistent with the row: callers go on to
        # read group.discord_thread (and _match_thread_id parses it) in the same
        # request that triggered the link.
        group.discord_thread = url
    return bool(linked)


def _groups_matching_title(guild_id, title):
    """Unlinked PlayerGroups in this guild whose name normalizes to `title`.

    Mirrors _match_for_thread's title fallback, and deliberately keeps its two
    guards: only groups with NO thread saved (a linked group is authoritative by
    id, so a same-named thread can't hijack it), and scoped to the guild through
    both round->stage->tournament and round->tournament, since a tournament may
    skip stages.

    Compared in Python so normalization applies to BOTH sides -- __iexact would
    only fold case, missing the emoji prefixes real thread titles carry."""
    from the_warroom.models import PlayerGroup

    candidates = PlayerGroup.objects.filter(
        Q(round__stage__tournament__guild__guild_id=str(guild_id))
        | Q(round__tournament__guild__guild_id=str(guild_id)),
        discord_thread="",
    )
    return [g for g in candidates if normalize_title(g.name) == title]


def player_group_for_channel(channel_id, channel_name=None, guild_id=None):
    """The PlayerGroup whose Discord thread is this channel, or None.

    `discord_thread` is a URL ending in the thread id
    (https://discord.com/channels/<guild>/<thread>), so anchor on the leading
    slash -- the same trick _match_for_thread uses -- so a thread id can't match
    part of the guild id. Deliberately not routed through _schedulable_matches:
    a group is worth resolving whatever state its matches are in.

    With `channel_name` AND `guild_id`, falls back to matching the thread's TITLE
    against the group's name, the same fallback _match_for_thread has always had.
    Without them the lookup is id-only, exactly as before -- so callers that have
    no title (a button click carries no channel name) are unaffected.

    On a title match the thread is LINKED to the group, so every later lookup --
    from any command, and from the Celery tasks -- resolves by id instead of
    re-running the guess.

    Lives here rather than in discord_interactions so the Celery task can reach
    it too. PlayerGroup is imported lazily for this module's usual circular
    import reason."""
    from the_warroom.models import PlayerGroup

    if not channel_id or not str(channel_id).isdigit():
        return None
    group = PlayerGroup.objects.filter(
        discord_thread__contains=f"/{channel_id}").first()
    if group:
        return group

    if not guild_id or not str(guild_id).isdigit():
        return None
    title = normalize_title(channel_name)
    if not title:
        return None

    matches = _groups_matching_title(guild_id, title)
    # Group names are unique per ROUND, not per tournament, so a title can
    # legitimately match several groups. Don't guess -- and don't link.
    if len(matches) != 1:
        return None

    group = matches[0]
    link_group_thread(group, guild_id, channel_id)
    return group


def rolled_components(thread):
    """`kind` -> [slug, ...] for everything surfaced in this thread.

    Deduped, first-seen order preserved. Rows with no resolvable slug are
    skipped.

    Emits the LIVE slug (post.slug) and falls back to the stored snapshot only
    when the Post is gone. Order matters: slugs are derived from the title, so a
    renamed Post would strand the snapshot and silently drop that component from
    the form's choices -- the exact dangling-reference bug the FK exists to fix.
    """
    out = {}
    for roll in thread.roll_log.select_related("post"):
        slug = roll.post.slug if roll.post_id else roll.slug
        if not roll.kind or not slug:
            continue
        slugs = out.setdefault(roll.kind, [])
        if slug not in slugs:
            slugs.append(slug)
    return out


def seated_profiles(thread):
    """Ordered [(seat_number, Profile|None, faction_slug|None, vagabond_slug|None), ...],
    seat 1 first.

    The Profile is None when the seat's Profile was deleted; that row still KEEPS
    ITS POSITION, because the record form places effort rows by list position and
    sizes the formset from this list's length -- dropping a seat would silently
    shrink the form by a player instead of leaving a blank row.

    The last two elements are SLUG STRINGS, not model instances: the form does
    `.filter(slug=...)`, and an instance there would coerce via str() and
    silently match nothing. Both are written by /pick; the vagabond is set only
    when the seat took the Vagabond faction.

    With no seating recorded, falls back to the thread's players in default
    order (seat numbers still assigned 1..N). That branch must emit the same
    4-tuple shape -- callers unpack a fixed width."""
    seats = list(thread.seats.select_related("profile", "faction", "vagabond"))
    if not seats:
        return [(i, p, None, None)
                for i, p in enumerate(thread.players.all(), 1)]

    return [(s.seat_number, s.profile,
             s.faction.slug if s.faction_id else None,
             s.vagabond.slug if s.vagabond_id else None)
            for s in seats]


def captains_by_seat(thread):
    """{seat_number: {"captains": [slug, ...], "discarded": slug|None}} for seats
    that took captains.

    A SIBLING of seated_profiles rather than more elements in its tuple: that
    tuple is unpacked at a fixed width by both the record view and its tests, so
    widening it would break every caller. Seats with no captains are omitted --
    callers .get() with a default.

    Slug STRINGS, not instances, for the same reason seated_profiles returns
    them: the view filters `.filter(slug__in=...)`, and instances there coerce
    via str() and silently match nothing.

    `discarded` is the 4th captain offered but not taken. It can be None on a
    seat that HAS captains: a short roll (fewer than 4 qualified) leaves nothing
    to discard.
    """
    seats = thread.seats.select_related("discarded_captain").prefetch_related("captains")
    out = {}
    for s in seats:
        slugs = [c.slug for c in s.captains.all()]
        if not slugs:
            continue
        out[s.seat_number] = {
            "captains": slugs,
            "discarded": s.discarded_captain.slug if s.discarded_captain_id else None,
        }
    return out


# A full Knaves complement. Mirrors DRAFT_CAPTAIN_COUNT in discord_interactions
# (the roller) and the "exactly 4 or none" rule in GameCreateForm.clean() (the
# validator). Duplicated rather than imported: discord_interactions is the bot
# entrypoint and pulls in the whole Discord stack, which the record view must not
# depend on.
FULL_CAPTAIN_COMPLEMENT = 4


def undrafted_pick(thread):
    """The one drafted faction no seat took, as an LFGDraftPick, or None.

    The ONLY draft-aware reader here. Everything else narrows from the roll log,
    but the undrafted faction is by definition the pick nobody took -- it has no
    roll, so the log cannot supply it.

    /draft deals players+1 factions and, with a draft, /pick may only choose from
    them, so a completed pick leaves exactly one over. That is a rule, not an
    invariant: the draft is stored independently of the seating, so a re-seat, a
    departed player, or a /draft re-run (which deletes and rebuilds its picks) can
    desync the two. Anything other than exactly one leftover returns None, which
    degrades to "no prefill" rather than guessing -- and correctly says nothing
    mid-pick, when several are still unseated.

    `getattr(thread, "draft", None)`: LFGDraft.thread is a OneToOne, so the
    reverse accessor RAISES when the thread has no draft.
    """
    draft = getattr(thread, "draft", None)
    if not draft:
        return None

    # faction_id on both sides: seats store the FK and so does the pick.
    taken = {s.faction_id for s in thread.seats.all() if s.faction_id}
    leftover = [p for p in draft.picks
                .select_related("faction", "vagabond")
                .prefetch_related("captains")
                if p.faction_id not in taken]
    return leftover[0] if len(leftover) == 1 else None


def picked_factions_by_profile(thread):
    """{profile_id: LFGSeat} for every seat in `thread` that has a profile.

    Keyed by PROFILE, not seat_number, because this is what match mode joins on.
    MatchSeat.seat_number is nullable with no uniqueness constraint, and /pick
    seats a tournament group thread by shuffling the PlayerGroup roster -- which
    bears no relation to MatchSeat ordering. A seat-number join could therefore
    attach a faction to the WRONG PLAYER; the profile is the only field both
    sides genuinely share.

    A stale seating needs no separate check as a result: a player who left the
    series isn't in the map, and one who joined isn't either, so neither gets a
    faction prefilled. MatchSeat stays authoritative for who plays.
    """
    return {s.profile_id: s
            for s in thread.seats
            .select_related("faction", "vagabond", "discarded_captain")
            .prefetch_related("captains")
            if s.profile_id}


def lfg_option_querysets(thread, tournament):
    """Per-field choices for the LFG game form: the thread's rolled components,
    intersected with what the tournament allows.

    Returns {bucket: queryset} for factions/maps/decks/vagabonds/captains/
    landmarks/tweaks/hirelings, plus a 'notices' list of user-facing strings
    explaining anything that had to be dropped.

    Rules:
      * a bucket WITH rolls narrows to those rolls (intersected with the
        tournament's allowed assets);
      * a bucket with NO rolls keeps the tournament's normal queryset;
      * an intersection that comes out EMPTY falls back to the tournament
        queryset rather than leaving an unusable field -- except clockwork-only
        factions, which are reported instead (falling back there would offer a
        list that still excludes everything the thread rolled).
    """
    from the_keep.models import Faction, Vagabond, Landmark, Hireling, Tweak, Map, Deck

    models_by_bucket = {
        "factions": Faction, "maps": Map, "decks": Deck,
        "vagabonds": Vagabond, "captains": Vagabond,
        "landmarks": Landmark, "tweaks": Tweak, "hirelings": Hireling,
    }

    if tournament is not None:
        assets = tournament.get_asset_querysets()
        # get_asset_querysets has no 'captains' bucket; captains are the
        # captain-capable subset of the allowed vagabonds.
        base = {b: assets[b] for b in
                ("factions", "maps", "decks", "vagabonds", "landmarks", "tweaks", "hirelings")}
        base["captains"] = assets["vagabonds"].filter(captain=True)
    else:
        # Unlinked LFG role: the thread's rolls are the only restriction.
        base = {b: m.objects.all() for b, m in models_by_bucket.items()}
        base["captains"] = Vagabond.objects.filter(captain=True)

    rolls = rolled_components(thread)
    slugs_by_bucket = {}
    for kind, slugs in rolls.items():
        bucket = ROLL_KIND_TO_BUCKET.get(kind)
        if bucket:
            slugs_by_bucket.setdefault(bucket, []).extend(slugs)

    out = dict(base)
    notices = []
    for bucket, slugs in slugs_by_bucket.items():
        narrowed = base[bucket].filter(slug__in=slugs)
        if narrowed.exists():
            out[bucket] = narrowed
            continue

        # Empty intersection. Clockwork is the case worth naming: the tournament
        # excludes it unless include_clockwork, so falling back would offer a
        # faction list that still contains none of the rolled factions.
        if bucket == "factions" and tournament is not None and not tournament.include_clockwork:
            clockwork_only = set(slugs) and not rolls.get("Faction")
            if clockwork_only:
                notices.append(
                    f"This thread drafted Clockwork factions, but {tournament} "
                    "does not allow Clockwork. Choose factions manually.")
                continue
        notices.append(
            f"None of the {bucket} rolled in this thread are playable in "
            f"{tournament}." if tournament else
            f"None of the {bucket} rolled in this thread could be found.")

    out["notices"] = notices
    return out
