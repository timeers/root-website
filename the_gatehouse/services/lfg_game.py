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


def group_roster(group, series_id=None):
    """Every Profile in a player group, deduped, in a stable order.

    THE roster resolver for a tournament group -- used by the consensus flow
    (_match_roster), /seating and /pick, so all three agree about who is in a
    group. They used to each read `tournament_players` directly and therefore all
    shared the same blind spot.

    PREFERS PlayerGroup.tournament_players (an M2M to TournamentPlayer, not to
    Profile, hence the hop through .profile): that's the group the round was
    actually formed with.

    FALLS BACK to MatchSeat when it yields nobody. Seats are the per-series
    seating chart -- what can_schedule, build_upcoming_embed and the series page
    all read -- so a group whose M2M was never populated still shows players on
    the site. Without this fallback the bot disagrees with the site about whether
    a group has any players at all.

    `series_id` enables the fallback; with none (a group not tied to a series)
    only the M2M is consulted."""
    from the_warroom.models import MatchSeat

    if not group:
        return []

    seen, roster = set(), []
    for tp in group.tournament_players.select_related("profile"):
        profile = tp.profile
        if profile and profile.pk and profile.pk not in seen:
            seen.add(profile.pk)
            roster.append(profile)
    if roster or not series_id:
        return roster

    # seat_number is NULLABLE, and databases disagree on where NULLs sort
    # (Postgres first, SQLite last), so order in Python rather than with order_by
    # -- otherwise the roster's order would differ between dev and production.
    seats = MatchSeat.objects.filter(series_id=series_id).select_related(
        "stage_participant__tournament_player__profile")
    for seat in sorted(seats, key=lambda s: (s.seat_number is None,
                                             s.seat_number, s.pk)):
        participant = seat.stage_participant
        tp = participant.tournament_player if participant else None
        profile = tp.profile if tp else None
        if profile and profile.pk and profile.pk not in seen:
            seen.add(profile.pk)
            roster.append(profile)
    return roster


def group_series_id(group):
    """The group's MatchSeries id, or None.

    getattr, NOT `group.series`: MatchSeries.player_group is a OneToOne, so the
    reverse accessor RAISES RelatedObjectDoesNotExist when the group has no
    series (a third of them don't) -- the same trap _pick_thread_for_channel
    documents."""
    series = getattr(group, "series", None) if group else None
    return series.pk if series else None


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


# ── Schedule proposal rendering ──────────────────────────────────────────────
# Lives here rather than in discord_interactions so the Celery task can reach it
# too: strip_schedule_proposal_messages_task renders the same closed embed for a
# proposal retired out of band (superseded, website-set, swept), and tasks.py
# cannot import discord_interactions -- that module imports tasks, which is why
# tasks.py duplicates EPHEMERAL locally rather than importing it.

def roster_name(profile, nudge=True):
    """A roster player as shown in the proposal lists: a mention once we know their
    snowflake (so the people who owe a confirmation get pinged), otherwise their
    display name.

    `nudge` appends "(not linked — log in with Discord once)" to an unlinked
    player. That belongs on a LIVE proposal, where it explains why the proposal is
    stuck on someone and what they must do. It is wrong on a CLOSED one: the
    proposal no longer exists, so telling them to go link an account for it is an
    instruction with nothing behind it. Closed renderers pass nudge=False."""
    if profile.discord_id:
        return f"<@{profile.discord_id}>"
    name = profile.display_name or profile.discord or profile.slug or "—"
    return f"{name} (not linked — log in with Discord once)" if nudge else name


# Discord caps an embed field value at 1024 chars. /lfg guards this too (an
# over-long field makes Discord reject the whole edit, which would silently discard
# a change we've already committed to the DB).
FIELD_VALUE_MAX = 1024


def name_list_value(profiles, empty="—", nudge=True):
    """Newline-joined roster names, truncated to Discord's field cap with a
    '…and N more' tail rather than overflowing it."""
    names = [roster_name(p, nudge=nudge) for p in profiles]
    if not names:
        return empty
    out, used = [], 0
    for i, name in enumerate(names):
        remaining = len(names) - i
        tail = f"\n…and {remaining} more"
        # Keep room for the tail we'd need if this were the last one we could fit.
        if used + len(name) + 1 + len(tail) > FIELD_VALUE_MAX and out:
            return "\n".join(out) + f"\n…and {remaining} more"
        out.append(name)
        used += len(name) + 1
    return "\n".join(out)[:FIELD_VALUE_MAX]


# Why a proposal closed, for the ones no person decided. Keyed by the same reason
# strings strip_schedule_proposal_messages_task already takes.
PROPOSAL_RETIRED_TEXT = {
    "superseded": "A different time was confirmed for this match. This proposal is "
                  "no longer active.",
    "cancelled": "This proposed time is no longer active — the match's scheduled "
                 "time was changed or cleared.",
    "expired": "This proposed time has passed without everyone confirming.\n"
               "Run `/schedule` to propose another time.",
    # Distinct from "cancelled" so players mid-confirmation learn WHERE the time
    # came from instead of just that theirs stopped mattering. Names no one: the
    # bracket editor is open to organizers and admins, not only moderators.
    "website": "The scheduled time for this match was set on the website. This "
               "proposal is no longer active.",
    "unschedulable": "This match can no longer be scheduled — it may have been "
                     "played or removed.",
}


def proposal_reason_line(reason, actor=None):
    """The one line saying WHY a proposal closed.

    `actor` is named only when a person actually decided it. An EXPIRED proposal
    was closed by nobody -- the time simply arrived -- so it must never read as
    someone's doing. That is why the reason is passed in rather than inferred from
    rejected_by: the row can carry a rejecter AND later expire, and guessing from
    the field would invent an actor for a clock."""
    if reason == "rejected":
        # No actor means the Profile was deleted (rejected_by is SET_NULL), so
        # fall back to a whole sentence rather than "Rejected by A player."
        if actor is None:
            return "A player rejected this time."
        return f"Rejected by {roster_name(actor, nudge=False)}."
    if actor is not None and reason == "unschedulable":
        return (f"Closed by {roster_name(actor, nudge=False)} — "
                "this match can no longer be scheduled.")
    return PROPOSAL_RETIRED_TEXT.get(reason, PROPOSAL_RETIRED_TEXT["cancelled"])


def match_label(match):
    """How a match is named back to the user: the player group's name (what the UI
    shows everywhere), falling back to the derived match name.

    Here rather than in discord_interactions so the Celery strip task can label a
    closed proposal the same way an interaction does -- pure model reads, nothing
    Discord-specific."""
    group = match.series.player_group if match.series_id else None
    return (group.name if group and group.name else None) or match.name or "this match"


def schedule_closed_embed(proposal, title, reason, actor=None, label=None):
    """The embed for a proposal that has closed, keeping what it knew.

    Rendered from the row rather than hardcoded, so the thread keeps a record of
    who suggested the time, what time it was, and who had agreed before it fell
    through. All of that was already on the row and used to be discarded the
    moment the proposal closed.

    Every part is optional on the model -- proposed_by and rejected_by are
    SET_NULL and confirmed_by can be empty -- so each is included only when
    present, and a proposal whose proposer was deleted still renders.

    `label` defaults to the proposal's own match. It is NOT named match_label:
    that would shadow the module-level function of that name inside this body,
    turning any later call to it into a TypeError on a str.

    Names render as mentions, which is safe: Discord only notifies from message
    `content`, never from inside an embed, so a closed message cannot ping anyone
    no matter who it lists."""
    from .time_parsing import format_discord_timestamp

    if label is None:
        label = match_label(proposal.match)

    lines = []
    if label:
        lines.append(f"**{label}**")

    when = format_discord_timestamp(proposal.proposed_time)
    if proposal.proposed_by_id:
        lines.append(f"Proposed by {roster_name(proposal.proposed_by, nudge=False)} "
                     f"for {when}")
    else:
        lines.append(f"Proposed for {when}")

    lines.append(proposal_reason_line(reason, actor))

    embed = {"title": title, "description": "\n".join(lines)}

    # Past tense: these confirmations no longer stand. "✅ Confirmed" would read as
    # though the time were still live.
    agreed = list(proposal.confirmed_by.all())
    if agreed:
        embed["fields"] = [{
            "name": "✅ Had agreed",
            "value": name_list_value(agreed, nudge=False),
            "inline": False,
        }]
    return embed
