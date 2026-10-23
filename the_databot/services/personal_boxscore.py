"""Personal (site-only) box score threads: the helpers the site needs around one.

A personal thread is an LFGThread with `personal=True`, minted with a token from
the Box Score Token page (BoxScoreUploadToken.issue_personal). It never touches
Discord. Its upload is staged exactly like a Discord one, but any seat the file
couldn't match is left BLANK for the owner to fill in on the record form,
which stands in for the Discord gates.

Formset row i is seat i + 1 is payload["seats"][i] throughout: the record form
builds its rows from the thread's seats in order, and the payload's seats are
sorted by turn_order before they are stored.
"""
from django.db.models import Q

from the_gatehouse.models import Profile


def personal_upload_token(thread):
    """The token whose upload is staged on `thread`, or None.

    APPLIED with a payload: a personal token keeps its payload after it is
    applied, because it is the only record of who an unresolved seat was."""
    from the_databot.models import BoxScoreUploadToken
    return (BoxScoreUploadToken.objects
            .filter(thread=thread, status=BoxScoreUploadToken.Status.APPLIED,
                    payload__isnull=False)
            .order_by("-created_at").first())


def personal_seat_hints(thread):
    """[seat dict, ...] from the staged payload, seat 1 first, or [].

    Each dict carries `label`, `player_slug`, `player_steam_id` and
    `profile_pk` (None when the file's player wasn't matched)."""
    token = personal_upload_token(thread)
    if token is None:
        return []
    return list((token.payload or {}).get("seats") or [])


def unresolved_player_queryset(thread):
    """Who may fill a seat the box score couldn't match.

    Discord Gate 0's rule (_boxscore_gate_zero_needed) with the pool widened
    from one roster to every player: no VERIFIED steam_id (a verified identity
    is settled, and would have matched the file if it were this person), and
    not already seated in this game (one person can't hold two seats)."""
    seated = thread.seats.filter(profile__isnull=False).values("profile_id")
    return (Profile.objects
            .filter(Q(steam_id__isnull=True) | Q(steam_id=""))
            .exclude(pk__in=seated))


def finish_personal_record(thread, players_by_index, *, learn):
    """After recording a personal thread's game, keep the thread in step with it.

    `players_by_index` is {formset index: profile_pk or None}.

    1. Every chosen player joins thread.players and their LFGSeat, so editing
       the game later (lfg_mode via obj.lfg_thread) offers them, and validates.
    2. When `learn` (the thread's OPEN -> RECORDED transition, so once), each
       seat the file couldn't match teaches the chosen profile its Steam id as an
       ASSUMED identity, so the next upload with them resolves on its own. That
       is Gate 0's job, done more carefully because the pool is site-wide: only
       a profile with NO assumed id yet, and only an id nobody holds -- otherwise
       one user could re-point someone else's identity.
    """
    from the_warroom.services.box_score_import import (
        assign_assumed_steam_ids, is_plausible_steam_id)

    chosen = {int(i): pk for i, pk in players_by_index.items() if pk}
    if not chosen:
        return

    thread.players.add(*Profile.objects.filter(pk__in=set(chosen.values())))
    seats = {s.seat_number: s for s in thread.seats.all()}
    for index, pk in chosen.items():
        seat = seats.get(index + 1)
        if seat is not None and seat.profile_id != pk:
            seat.profile_id = pk
            seat.save(update_fields=["profile"])

    if not learn:
        return
    hints = personal_seat_hints(thread)
    candidates = {}
    for index, pk in chosen.items():
        if index >= len(hints):
            continue
        hint = hints[index]
        steam_id = str(hint.get("player_steam_id") or "").strip()
        if hint.get("profile_pk") or not is_plausible_steam_id(steam_id):
            continue
        candidates[pk] = steam_id
    if not candidates:
        return
    # An id someone already holds (verified or assumed) is left alone:
    # assign_assumed_steam_ids would RELEASE it from its holder.
    ids = set(candidates.values())
    held_ids = set()
    for verified, assumed in Profile.objects.filter(
            Q(steam_id__in=ids) | Q(assumed_steam_id__in=ids)).values_list(
            "steam_id", "assumed_steam_id"):
        held_ids.update(v for v in (verified, assumed) if v in ids)
    candidates = {pk: sid for pk, sid in candidates.items() if sid not in held_ids}
    eligible = set(Profile.objects
                   .filter(pk__in=candidates)
                   .filter(Q(steam_id__isnull=True) | Q(steam_id=""))
                   .filter(Q(assumed_steam_id__isnull=True) | Q(assumed_steam_id=""))
                   .values_list("pk", flat=True))
    assign_assumed_steam_ids(
        [(pk, sid) for pk, sid in candidates.items() if pk in eligible])


# Page states, in the order a token moves through them.
WAITING = "waiting"          # minted, not yet uploaded against
PROCESSING = "processing"    # mid-upload (PENDING); only ever seen briefly
REVIEW = "review"            # uploaded, but not recorded -- the record link
RECORDED = "recorded"
EXPIRED = "expired"
REVOKED = "revoked"
FAILED = "failed"            # the upload couldn't be read


def personal_upload_rows(profile, limit=20):
    """The Box Score Token page's list: one dict per personal thread, newest
    first.

    State comes from the THREAD's status first, not game_id: a save-progress
    draft sets `game` while the thread stays OPEN, and is still to be finished.
    Everything else reads the token's own status.
    """
    from the_databot.models import BoxScoreUploadToken, LFGThread

    Status = BoxScoreUploadToken.Status
    threads = (LFGThread.objects
               .filter(personal=True, host=profile)
               .select_related("game", "map", "deck")
               .prefetch_related("upload_tokens", "seats__profile")
               .order_by("-created_at")[:limit])

    rows = []
    for thread in threads:
        tokens = sorted(thread.upload_tokens.all(), key=lambda t: t.created_at,
                        reverse=True)
        token = tokens[0] if tokens else None
        if thread.status == LFGThread.Status.RECORDED:
            state = RECORDED
        elif token is None:
            continue                     # nothing to show or act on
        elif token.status == Status.ISSUED:
            state = EXPIRED if token.is_expired else WAITING
        elif token.status == Status.PENDING:
            state = PROCESSING
        elif token.status == Status.APPLIED:
            state = REVIEW
        elif token.status == Status.CANCELLED:
            # used_at is stamped when an upload claims the token, so a cancelled
            # token that was never used was revoked from this page.
            state = FAILED if token.used_at else REVOKED
        else:
            state = EXPIRED

        row = {"thread": thread, "token": token, "state": state,
               "created_at": thread.created_at}
        if state == REVIEW:
            hints = list(((token.payload or {}).get("seats")) or [])
            seats = []
            for i, seat in enumerate(thread.seats.all()):
                hint = hints[i] if i < len(hints) else {}
                seats.append({
                    "number": seat.seat_number,
                    "name": (seat.profile.name if seat.profile_id
                             else hint.get("label") or "?"),
                    "matched": bool(seat.profile_id),
                })
            row["seats"] = seats
            row["unmatched"] = sum(1 for s in seats if not s["matched"])
            row["missing_map_deck"] = not (thread.map_id and thread.deck_id)
        rows.append(row)
    return rows
