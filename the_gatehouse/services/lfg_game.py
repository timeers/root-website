"""Turning an LFGThread's captured Discord activity into game-form inputs.

An LFG thread accumulates curated state in three related tables: LFGSeat (who
sits where, written by /draft's seat button), LFGRoll (an append-only log of
every component surfaced by /random, /map, /deck, the other lookups, and
/draft), and LFGDraft/LFGDraftPick (the current draft). This module resolves
them into the shapes the record-game form needs, so the view stays thin.

Note the form narrows its options from the ROLL LOG, not the draft -- the draft
is recorded for a future "pick a faction from the draft" command.

Kept in the_gatehouse (the app that owns LFGThread) but imports the_warroom
models lazily inside functions — the_warroom.models imports from
the_gatehouse.models at module level, so a top-level import here is circular.
"""


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
    """Ordered [(seat_number, Profile|None, faction_slug|None), ...], seat 1 first.

    The middle element is None when the seat's Profile was deleted; that row
    still KEEPS ITS POSITION, because the record form places effort rows by list
    position and sizes the formset from this list's length -- dropping a seat
    would silently shrink the form by a player instead of leaving a blank row.

    The third element is a SLUG STRING, not a Faction: the form does
    `.filter(slug=faction_slug)`, and a model instance there would coerce via
    str() and silently match nothing. No production path writes LFGSeat.faction
    yet; a future "pick a faction from the draft" command will.

    With no seating recorded, falls back to the thread's players in default
    order (seat numbers still assigned 1..N)."""
    seats = list(thread.seats.select_related("profile", "faction"))
    if not seats:
        return [(i, p, None)
                for i, p in enumerate(thread.players.all(), 1)]

    return [(s.seat_number, s.profile,
             s.faction.slug if s.faction_id else None)
            for s in seats]


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
