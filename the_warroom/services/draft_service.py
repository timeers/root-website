"""Derivation of the per-seat faction draft pool cached on SeatDraftOption.

Pure functions (no signal/task knowledge) so the Celery task and the
recalculate_draft_options management command share one implementation, mirroring
winrate_service.py.

The draft runs in REVERSE seat order — the highest seat picks first from the full pool and
seat 1 takes whatever survives — so each seat's pool is the leftover plus every pick from
seat 1 up to and including its own: size seat + 1.
"""
import logging

from django.db import transaction
from django.db.models import F

logger = logging.getLogger(__name__)


class DraftDataError(ValueError):
    """A game's recorded draft is self-contradictory (the undrafted option was also
    played). Raised by compute_draft_options; callers skip and log the game."""


def _vagabond_faction():
    """The shared 'Vagabond' Faction row, resolved by title rather than a hardcoded PK
    (which differs across environments). Matches the lookup the RDL importer uses when
    mapping a vagabond seat, so an undrafted vagabond compares directly against played
    ones. Returns None if the row is missing."""
    from the_keep.models import Faction

    return Faction.objects.filter(title='Vagabond').first()


def undrafted_option(game):
    """(faction_id, vagabond_id) for the game's leftover, or None if not a drafted game.

    A game counts as drafted when EITHER undrafted field is set. ~2,189 RDL games have
    undrafted_vagabond set with undrafted_faction NULL (the importer can resolve the
    vagabond without the faction); those resolve to the shared 'Vagabond' faction so the
    option is comparable to played vagabond seats.
    """
    faction_id = game.undrafted_faction_id
    vagabond_id = game.undrafted_vagabond_id

    if faction_id is None and vagabond_id is None:
        return None

    if faction_id is None:
        vagabond_faction = _vagabond_faction()
        if vagabond_faction is None:
            logger.warning(
                'draft options: game %s has undrafted_vagabond but no "Vagabond" faction '
                'row exists; treating as not drafted', game.pk,
            )
            return None
        faction_id = vagabond_faction.pk

    return (faction_id, vagabond_id)


def compute_draft_options(game, efforts=None):
    """{effort_id: [(faction_id, vagabond_id), ...]} — the pool each seat chose from.

    Seat 1 picks LAST, so its pool is just the leftover plus its own pick; each higher
    seat's pool is the previous one plus that seat's pick. Pool size is seat + 1.

    Returns empty lists for every effort when the game isn't drafted, so a game edited to
    remove its undrafted option gets its stale rows cleared.

    Raises DraftDataError when the undrafted option was also played by a seat —
    contradictory input (a faction cannot be both drafted and left over) that would
    otherwise silently produce a short pool.
    """
    if efforts is None:
        efforts = ordered_efforts(game)

    leftover = undrafted_option(game)
    if leftover is None:
        return {effort.pk: [] for effort in efforts}

    pools = {}
    pool = [leftover]
    seen = {leftover}
    for effort in efforts:
        if effort.faction_id is None:
            # Nullable at the model level and producible by the RDL importer on an
            # unmapped key. Snapshot the pool so far; there is no pick to add.
            pools[effort.pk] = list(pool)
            continue
        option = (effort.faction_id, effort.vagabond_id)
        if option in seen:
            raise DraftDataError(
                f'game {game.pk}: option {option} appears twice — the undrafted option '
                f'was also played (or two seats share a faction/vagabond pair)'
            )
        seen.add(option)
        pool.append(option)
        pools[effort.pk] = list(pool)
    return pools


def ordered_efforts(game):
    """The game's efforts in draft-accumulation order (ascending seat).

    nulls_last because Effort.seat is nullable and Postgres/SQLite disagree on default
    NULL ordering; an unseated effort sorting first would corrupt the prefix accumulation.
    """
    return list(
        game.efforts.all()
        .only('id', 'seat', 'faction_id', 'vagabond_id')
        .order_by(F('seat').asc(nulls_last=True))
    )


def refresh_game_draft_options(game):
    """Recompute and persist SeatDraftOption rows for every effort on a game.

    Single writer, one transaction. Diffs against existing rows and only writes where a
    seat's set actually changed, so a no-op save doesn't churn the table. Returns the
    number of efforts rewritten.

    A game with contradictory draft data is left uncached (rows deleted) and logged,
    rather than cached with a malformed pool.
    """
    from the_warroom.models import SeatDraftOption

    efforts = ordered_efforts(game)
    if not efforts:
        return 0

    # pick_number counts from the FIRST picker. Derived from the live effort count rather
    # than game.cached_player_count, which is refreshed by a separate async task and can
    # lag during a bulk edit.
    player_count = len(efforts)

    try:
        pools = compute_draft_options(game, efforts=efforts)
    except DraftDataError as exc:
        logger.warning('draft options: skipping game %s (%s)', game.pk, exc)
        pools = {effort.pk: [] for effort in efforts}

    existing = {}
    for row in SeatDraftOption.objects.filter(effort__in=efforts):
        existing.setdefault(row.effort_id, set()).add(
            (row.faction_id, row.vagabond_id, row.chosen)
        )

    changed, to_create = [], []
    for effort in efforts:
        options = pools.get(effort.pk, [])
        chosen_option = (effort.faction_id, effort.vagabond_id)
        desired = {
            (faction_id, vagabond_id, (faction_id, vagabond_id) == chosen_option)
            for faction_id, vagabond_id in options
        }
        if desired == existing.get(effort.pk, set()):
            continue
        changed.append(effort.pk)
        pick_number = None if effort.seat is None else player_count + 1 - effort.seat
        for faction_id, vagabond_id, chosen in desired:
            to_create.append(SeatDraftOption(
                effort_id=effort.pk,
                faction_id=faction_id,
                vagabond_id=vagabond_id,
                seat=effort.seat,
                pick_number=pick_number,
                chosen=chosen,
            ))

    if not changed:
        return 0

    with transaction.atomic():
        SeatDraftOption.objects.filter(effort_id__in=changed).delete()
        if to_create:
            SeatDraftOption.objects.bulk_create(to_create, batch_size=500)
    return len(changed)
