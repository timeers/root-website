"""Writing a recorded game -- THE one place a Game is saved.

Lifted wholesale out of `manage_game`, which was the only way to record a game
and therefore the only place this logic could live. It now has a second caller:
a Discord box score that validated cleanly can record itself without anyone
opening the form. Both hand in a VALIDATED form + formset and get identical
behaviour, so the web and the bot cannot drift.

Everything here runs in one transaction. The status snapshots the caller passes
(`initial_game_status`, `game_was_final`, `lfg_initial_status`) are what gate the
announcements: they must be read BEFORE the form rebinds the instance, or a
re-save re-announces a game that was already public.
"""
import json

from django.conf import settings
from django.db import transaction

from the_warroom.models import (
    Game, Effort, TurnScore, ScoreCard, Match, CompetitionStatus,
)
from the_warroom.services.channel_posts import (
    post_to_tournament_channel, match_thread_id, game_thread_url)
from the_keep.models import StatusChoices
from the_databot.models import LFGThread
from the_databot.tasks import post_channel_message_task, edit_channel_message_task
from the_gatehouse.tasks import send_rich_discord_message_task


def _match_captured_thread(match):
    """The LFGThread capturing rolls in this match's group thread, or None.

    A tournament group thread accumulates rolls/drafts into the same tables an
    LFG game does, created on first use by record_lfg_components_task. `series`
    is the link, so this is a plain FK lookup -- no URL parsing, and it stays
    correct if a moderator edits the group's thread URL afterwards.

    Match.series is non-nullable, so series_id is never None here; a None would
    match every plain LFG thread (they all have series=None).

    Lives here rather than in views because record_game itself needs it (the
    boxscore-message rewrite); views re-imports it from this module.
    """
    return LFGThread.objects.filter(series_id=match.series_id).first()


class AlreadyRecorded(Exception):
    """This match already has a game, so this payload must not create another.

    Carries the existing game's id: the view redirects the user to edit it, the
    bot just declines. Raised rather than returned so neither caller can forget
    to check.
    """

    def __init__(self, game_id):
        super().__init__(f"match already has game {game_id}")
        self.game_id = game_id


def record_game(*, form, formset, recorder, match=None, lfgthread=None,
                lfg_mode=False, match_mode=False, lfg_round=None,
                final=True, seat_order=None, scorecard_grid=None,
                initial_game_status=None, game_was_final=False,
                lfg_initial_status=None, match_initial_status=None,
                existing_id=None):
    """Save a validated game and run every side effect recording implies.

    `form`/`formset` must already have passed is_valid(). `scorecard_grid` is the
    PARSED grid dict ({"rows": [...]}), not the JSON string the form posts.
    `seat_order` is the already-split list of form indices, or None.

    Returns the saved Game. Raises AlreadyRecorded when a concurrent submission
    beat this one to the match.
    """
    with transaction.atomic():
        if match_mode and match:
            match = Match.objects.select_for_update().get(id=match.id)
            # Read the status from the LOCKED row: capturing it back at
            # mode-detection time would let two concurrent submissions both
            # see a pre-COMPLETED status and both announce the game.
            match_initial_status = match.status
            if match.game_id and not existing_id:
                # The caller decides how to surface this: the view redirects to
                # the edit page, the bot simply declines to auto-record.
                raise AlreadyRecorded(match.game_id)
        parent = form.save(commit=False)

        # Set final status
        parent.final = final

        if not existing_id:
            parent.recorder = recorder

        # Belt-and-braces: the form now sets the locked round via the
        # disabled field's initial, so this is a redundant backstop.
        if match_mode and match:
            parent.round = match.round
        elif lfg_mode and lfg_round:
            parent.round = lfg_round

        parent.save()
        form.save_m2m()

        # Process seat ordering (already split by the caller)

        game_status = max(parent.map.status, parent.deck.status)
        for landmark in parent.landmarks.all():
            game_status = max(game_status, landmark.status)
        for hireling in parent.hirelings.all():
            game_status = max(game_status, hireling.status)
        for tweak in parent.tweaks.all():
            game_status = max(game_status, tweak.status)

        saved_efforts = []
        for idx, effort_form in enumerate(formset):
            if effort_form.cleaned_data.get('delete'):
                if effort_form.instance.id:
                    effort_form.instance.delete()
            elif not effort_form.cleaned_data.get('faction') and not effort_form.cleaned_data.get('score') and not effort_form.cleaned_data.get('player'):
                if effort_form.instance.id:
                    effort_form.instance.delete()
            else:
                child = effort_form.save(commit=False)
                if child.faction_id is not None:
                    game_status = max(game_status, child.faction.status)
                    if child.vagabond:
                        game_status = max(game_status, child.vagabond.status)
                    child.faction_status = child.faction.status
                    child.game = parent
                    # Brazen Demagogue is only valid with the Squires & Disciples deck.
                    if not (parent.deck and parent.deck.title == 'Squires & Disciples'):
                        child.brazen_demagogue = False
                    # captains is a M2M relation; capture now and set after save().
                    captains = effort_form.cleaned_data.get('captains')
                    saved_efforts.append((idx, child, captains))

        # Assign seats based on seat_order or sequential
        if seat_order:
            form_index_to_seat = {}
            for seat_num, form_idx_str in enumerate(seat_order, start=1):
                try:
                    form_index_to_seat[int(form_idx_str)] = seat_num
                except (ValueError, TypeError):
                    pass
            for idx, child, captains in saved_efforts:
                child.seat = form_index_to_seat.get(idx, idx + 1)
                child.save()
                child.captains.set(captains or [])
        else:
            for seat_num, (idx, child, captains) in enumerate(saved_efforts, start=1):
                child.seat = seat_num
                child.save()
                child.captains.set(captains or [])

        parent.status = StatusChoices(game_status)
        parent.save()

        # Match linkage
        if match_mode and match:
            if not match.game_id or match.game_id != parent.id:
                match.game = parent
            match.status = CompetitionStatus.COMPLETED if parent.final else CompetitionStatus.ACTIVE
            match.save()

            # Trigger series/round completion logic
            if parent.final:
                from the_warroom.services.bracket import BracketService
                BracketService.on_game_complete(match)

        # LFG thread linkage: the thread remembers its game (OneToOne, so a
        # later visit edits it instead of recording a duplicate) and only
        # counts as RECORDED once the game is final -- a save-progress
        # draft leaves it OPEN.
        if lfg_mode and lfgthread:
            lfgthread.game = parent
            lfgthread.status = (LFGThread.Status.RECORDED if parent.final
                                else LFGThread.Status.OPEN)
            lfgthread.save(update_fields=['game', 'status'])

        # ── Scorecard grid → ScoreCard + TurnScore persistence ──
        # Each grid row maps to one effort by its formset index. Turn cells
        # hold cumulative game points; per-turn generic/total points are the
        # delta from the previous non-blank cell so the model's running-sum
        # recalculation reproduces game_points_total.
        grid_touched_effort_ids = set()
        grid_payload = scorecard_grid or {'rows': []}

        idx_to_child = {idx: child for idx, child, _ in saved_efforts}

        # ── Phase 1: parse every row into contiguous cells (T1..N) ──
        # Editable rows carry full cells (score + dominance); locked (detailed)
        # rows carry dominance-only cells and are collected separately so we
        # only update their dominance, never their scores/turns.
        parsed_rows = []
        locked_dominance = {}  # form_index -> {turn_number: bool}
        for row in grid_payload.get('rows', []):
            try:
                form_index = int(row.get('form_index'))
            except (TypeError, ValueError):
                continue
            child = idx_to_child.get(form_index)
            if child is None:
                continue

            if row.get('locked'):
                dom_by_turn = {}
                for cell in row.get('cells', []):
                    try:
                        turn = int(cell.get('turn'))
                    except (TypeError, ValueError):
                        continue
                    dom_by_turn[turn] = bool(cell.get('dominance'))
                if dom_by_turn:
                    locked_dominance[form_index] = dom_by_turn
                continue

            # Parse and sort valid (numeric) cells by turn. `carried` marks a
            # cell that has no entered score (a blank cell toggled dominant),
            # so it may be trimmed if it runs past the game-end point.
            cells = []
            for cell in row.get('cells', []):
                try:
                    turn = int(cell.get('turn'))
                    value = int(cell.get('value'))
                except (TypeError, ValueError):
                    continue
                cells.append({
                    'turn': turn,
                    'value': value,
                    'dominance': bool(cell.get('dominance')),
                    'carried': bool(cell.get('carried')),
                })
            cells.sort(key=lambda c: c['turn'])
            if not cells:
                continue

            # Backfill blank leading/interior turns with 0 so turns always
            # start at T1 and turn_number stays contiguous (blank cells are a
            # cumulative 0). A blank cell carries the previous turn's
            # cumulative value (no change) and its dominance forward-fill.
            by_turn = {c['turn']: c for c in cells}
            filled = []
            prev_value = 0
            prev_dominance = False
            for turn in range(1, cells[-1]['turn'] + 1):
                cell = by_turn.get(turn)
                if cell is None:
                    cell = {'turn': turn, 'value': prev_value,
                            'dominance': prev_dominance, 'carried': True}
                prev_value = cell['value']
                prev_dominance = cell['dominance']
                filled.append(cell)
            parsed_rows.append({'child': child, 'cells': filled})

        # ── Pad dominance rows with trailing turns ──
        # A dominance player often stops filling cells once their score stops
        # changing, but their turns should extend to match the winner. Derive
        # the target turn count from the winner's turn count and seat order:
        #   winner seated AFTER the dom player  → same as winner
        #   winner seated BEFORE the dom player → one less than winner
        #   winner IS the dom player            → match the seat ahead (seat-1);
        #       if dom player is the first seat, match the last seat + 1
        # (Only rows present in this grid submission are considered; padding
        # only adds trailing turns and never truncates.)
        _pad_rows_present = [r for r in parsed_rows if r['cells']]
        if _pad_rows_present:
            def _row_count(r):
                return len(r['cells'])
            def _has_dom(r):
                return any(c['dominance'] for c in r['cells'])

            # Pick the winner reference row. With multiple winners (a vagabond
            # winning alongside its coalition partner), prefer the winner that
            # is NOT in a coalition — the coalition vagabond isn't a valid
            # reference and shouldn't drive the "winner + 1 turn" padding.
            winner_rows = [r for r in _pad_rows_present if r['child'].win]
            winner_row = next(
                (r for r in winner_rows if not r['child'].coalition_with_id),
                winner_rows[0] if winner_rows else None,
            )
            winner_seat = winner_row['child'].seat if winner_row else None

            # The reference for the target turn count must be a non-dominance
            # row (other dominance rows shouldn't be referenced). Two regimes:
            #   • Non-dominance winner → derive each dom row's target from the
            #     winner's turn count and seat (same / winner-1).
            #   • Every row is dominance (or the winner has dominance) → there
            #     is no non-dom reference, so anchor on the longest row and
            #     split by the winner's seat: seats up to & including the
            #     winner get the full count, later seats get one less. If the
            #     longest row is a later ("-1") seat, bump full by 1 so it
            #     isn't truncated.
            winner_is_dom = winner_row is not None and _has_dom(winner_row)

            if winner_row is not None and winner_seat is not None and not winner_is_dom:
                winner_count = _row_count(winner_row)
                for r in _pad_rows_present:
                    if not _has_dom(r):
                        continue
                    dom_seat = r['child'].seat
                    if dom_seat is None:
                        continue
                    # Winner seated after → same; seated before → one less.
                    r['pad_target'] = winner_count if winner_seat > dom_seat else winner_count - 1
            elif winner_seat is not None:
                # All-dominance regime (the winner reference itself has
                # dominance). Anchor on the longest entered row — the game's
                # length — and split by the winner's seat: seats up to and
                # including the winner get the full count, later seats one
                # less. We never exceed the longest real entry (no +1 bump)
                # and never truncate, so no turns are invented past the game.
                max_turns = max(_row_count(r) for r in _pad_rows_present)
                for r in _pad_rows_present:
                    if not _has_dom(r):
                        continue
                    seat = r['child'].seat
                    if seat is None:
                        continue
                    r['pad_target'] = max_turns if seat <= winner_seat else max_turns - 1

            # Apply targets to the dominance streak, but only when the row's
            # LAST entered cell is dominant (we're extending/capping an active
            # streak; non-dominant trailing turns are intentionally blank).
            #   • Short of target → add trailing dominant cells (carried value).
            #   • Past target → trim trailing cells down to target, but ONLY
            #     `carried` cells (no entered score). Stop at the first cell
            #     with a real value so entered data is never removed.
            for r in _pad_rows_present:
                target = r.get('pad_target')
                if not target:
                    continue
                cells = r['cells']
                if not cells[-1]['dominance']:
                    continue
                if target > len(cells):
                    last = cells[-1]
                    for turn in range(len(cells) + 1, target + 1):
                        cells.append({'turn': turn, 'value': last['value'],
                                      'dominance': True, 'carried': True})
                elif target < len(cells):
                    # Trim trailing carried (no-score) cells down to target.
                    while len(cells) > target and cells[-1].get('carried'):
                        cells.pop()

        # ── Phase 2: persist each row as ScoreCard + turns_data ──
        for _row in parsed_rows:
            child = _row['child']
            cells = _row['cells']

            # Load any existing scorecard; skip detailed ones defensively so
            # grid submission never clobbers battle/crafting/faction/other data.
            try:
                existing_scorecard = child.scorecard
            except ScoreCard.DoesNotExist:
                existing_scorecard = None
            if existing_scorecard and existing_scorecard.is_detailed:
                continue

            scorecard, _ = ScoreCard.objects.get_or_create(
                effort=child,
                defaults={'faction': child.faction, 'recorder': recorder},
            )
            last_value = cells[-1]['value']
            scorecard.faction = child.faction
            if scorecard.recorder_id is None:
                scorecard.recorder = recorder
            scorecard.total_points = last_value
            scorecard.total_generic_points = last_value
            scorecard.total_battle_points = 0
            scorecard.total_crafting_points = 0
            scorecard.total_faction_points = 0
            scorecard.total_other_points = 0
            scorecard.final = True

            # Each cell holds a cumulative value; store the per-turn delta as
            # generic/total points. set_turns recomputes game_points_total
            # (== the cell's cumulative value) and the dominance/is_detailed flags.
            new_turns = []
            prev_value = 0
            for cell in cells:
                delta = cell['value'] - prev_value
                prev_value = cell['value']
                new_turns.append({
                    'turn_number': cell['turn'],
                    'generic_points': delta,
                    'total_points': delta,
                    'battle_points': 0,
                    'crafting_points': 0,
                    'faction_points': 0,
                    'other_points': 0,
                    'dominance': cell['dominance'],
                })
            scorecard.set_turns(new_turns)
            scorecard.save()
            grid_touched_effort_ids.add(child.id)

        # ── Detailed (locked) rows: update dominance only ──
        # The user can toggle dominance on a detailed scorecard without
        # editing its scores. Apply those toggles to each turn and the
        # scorecard's dominance flag, leaving all point values untouched.
        for form_index, dom_by_turn in locked_dominance.items():
            child = idx_to_child.get(form_index)
            if child is None:
                continue
            try:
                scorecard = child.scorecard
            except ScoreCard.DoesNotExist:
                scorecard = None
            if scorecard is None:
                continue
            changed = False
            turns = scorecard.turns_data or []
            for turn in turns:
                new_dom = bool(dom_by_turn.get(turn['turn_number'], turn.get('dominance')))
                if bool(turn.get('dominance')) != new_dom:
                    turn['dominance'] = new_dom
                    changed = True
            any_dom = any(dom_by_turn.values())
            if scorecard.dominance != any_dom:
                scorecard.dominance = any_dom
                changed = True
            if changed:
                scorecard.turns_data = turns
                scorecard.save(update_fields=['turns_data', 'dominance'])
                grid_touched_effort_ids.add(child.id)

        # ScoreCard consistency checks
        for idx, child, captains in saved_efforts:
            # Grid-created scorecards already set score/dominance/final consistently.
            if child.id in grid_touched_effort_ids:
                continue
            try:
                scorecard = child.scorecard
            except ScoreCard.DoesNotExist:
                continue
            if scorecard is None:
                continue
            # Faction mismatch → detach scorecard
            if child.faction_id != scorecard.faction_id:
                scorecard.effort = None
                scorecard.final = False
                scorecard.save(update_fields=['effort', 'final'])
                continue
            # Score or dominance mismatch → mark non-final
            score_mismatch = (child.score != scorecard.total_points)
            dominance_mismatch = (bool(child.dominance) != scorecard.dominance)
            if score_mismatch or dominance_mismatch:
                scorecard.final = False
                scorecard.save(update_fields=['final'])

        # Discord notification
        if parent.final:
            fields = []
            fields.append({
                'name': 'Recorder:',
                'value': recorder.name
            })
            game_title = parent.nickname if parent.nickname else f"{parent.platform} Game"
            if not initial_game_status and parent.final:
                send_rich_discord_message_task.delay(
                    f'[{game_title}]({settings.SITE_URL}{parent.get_absolute_url()})',
                    category='New Game', title='Game Recorded', fields=fields
                )
                # DM opted-in players / component designers / tournament hosts
                from the_databot.services.notifyservice import notify_game_recorded
                notify_game_recorded(parent)

            # Post the game link back into the LFG thread it came from, so
            # everyone there sees the result -- the /record reply that
            # started this was ephemeral, visible only to its author. Gated
            # on the thread's own OPEN -> RECORDED transition so editing the
            # game later never reposts.
            # The two modes are exclusive in practice (the LFG path only sets
            # `round` and never creates a Match), but nothing stops
            # ?match=X&lfg=Y setting both flags -- elif makes a double
            # announcement structurally impossible rather than assumed.
            if (lfg_mode and lfgthread and lfgthread.thread_id
                    and lfg_initial_status != LFGThread.Status.RECORDED
                    and lfgthread.status == LFGThread.Status.RECORDED):
                site = (settings.SITE_URL or '').rstrip('/')
                if site:
                    # on_commit: this runs inside the atomic block above, so a
                    # bare delay() could post a link to a game the worker
                    # can't read yet (or that a later error rolls back).
                    # Bind the args as defaults -- a bare closure resolves the
                    # names at commit time, long after this view moves on.
                    _message = f'Game submitted! See the results [here]({site}{parent.get_absolute_url()}).'
                    transaction.on_commit(
                        lambda tid=lfgthread.thread_id, msg=_message:
                            post_channel_message_task.delay(tid, msg))

            # Same courtesy for a tournament match: announce into the player
            # group's thread, but only when it demonstrably belongs to the
            # tournament's own guild (see match_thread_id).
            elif (match_mode and match
                    and match_initial_status != CompetitionStatus.COMPLETED
                    and match.status == CompetitionStatus.COMPLETED):
                _thread_id = match_thread_id(match)
                site = (settings.SITE_URL or '').rstrip('/')
                if _thread_id and site:
                    _message = f'Game submitted! See the results [here]({site}{parent.get_absolute_url()})'
                    transaction.on_commit(
                        lambda tid=_thread_id, msg=_message:
                            post_channel_message_task.delay(tid, msg))

            # The box score message (if any) still carries a "record the
            # game" link at this point -- rewrite it back to its stored
            # pre-record-line content now that recording it is exactly what
            # just happened. Same on_commit reasoning as above: the message
            # being rewritten belongs to a Game this transaction might yet
            # roll back.
            #
            # OUTSIDE the mode chain, and not gated on lfg_mode: a tournament
            # group thread records in MATCH mode (see the promotion where the
            # modes are decided), so keying this off lfg_mode left exactly
            # those threads showing a stale record link forever. The thread
            # is whichever one is in play -- its own, or the match's captured
            # group thread.
            #
            # The tracked ids are CLEARED as it fires, which is what makes
            # this one-shot: the message now matches its stored body, so
            # every later edit of the same game would otherwise re-send an
            # identical edit to Discord. The old lfg_mode placement got that
            # for free from the OPEN -> RECORDED transition around it.
            _bs_thread = lfgthread or (_match_captured_thread(match)
                                       if match_mode and match else None)
            if (_bs_thread and _bs_thread.thread_id
                    and _bs_thread.boxscore_message_id
                    and _bs_thread.boxscore_message_body is not None):
                transaction.on_commit(
                    lambda tid=_bs_thread.thread_id,
                           mid=_bs_thread.boxscore_message_id,
                           body=_bs_thread.boxscore_message_body:
                        edit_channel_message_task.delay(tid, mid, body))
                _bs_thread.boxscore_message_id = None
                _bs_thread.boxscore_message_body = None
                _bs_thread.save(update_fields=['boxscore_message_id',
                                               'boxscore_message_body'])

            # Announce in the tournament's results channel, if it has one.
            # Deliberately OUTSIDE the mode chain above: every game recorded
            # into a tournament belongs here -- match, LFG, or a standalone
            # game that simply picked one of its rounds. This is independent
            # of, and additional to, any thread post above (different
            # audience, different wording); a tournament can have a results
            # channel with no threads, or vice versa.
            _tournament = parent.get_tournament()
            _res_site = (settings.SITE_URL or '').rstrip('/')
            # `game_was_final` (not initial_game_status) is what makes this
            # fire once, on the submission that first makes the game final,
            # rather than again on every later edit -- see its capture above.
            if (not game_was_final and parent.final
                    and _tournament is not None and _res_site):
                # recorder is nullable (league-imported games have none) and
                # is only assigned on CREATE, so fall back to whoever is
                # submitting -- this branch only runs on a live submission.
                # The PROFILE, not its name: the mention below needs the row.
                _recorder = parent.recorder or recorder
                # A tag rather than a plain name, suppressed to a name chip by
                # the allowed_mentions below. The truthiness guard is not
                # cosmetic: discord_id is null AND blank, and a literal "<@>"
                # makes Discord reject the whole payload with a 400.
                _who = (f'<@{_recorder.discord_id}>' if _recorder.discord_id
                        else _recorder.name)
                # Name the game and link the thread it was played in, the way
                # the schedule announcement names a match -- "Game recorded
                # by" read identically for every post in the channel. Same
                # expression as the rich-message title above, so both
                # announcements for one game call it the same thing.
                _game_name = parent.nickname if parent.nickname else f"{parent.platform} Game"
                _thread_url = game_thread_url(
                    match=match if match_mode else None,
                    lfg_thread=lfgthread if lfg_mode else None,
                    tournament=_tournament)
                _subject = (f'[{_game_name}]({_thread_url})' if _thread_url
                            else _game_name)
                _res_msg = (f'{_subject} recorded by {_who}. '
                            f'See the results [here]({_res_site}{parent.get_absolute_url()}).')
                # parse: [] renders the mention without notifying anyone --
                # omitting allowed_mentions entirely would let Discord's
                # default ping the recorder about their own submission.
                transaction.on_commit(
                    lambda t=_tournament, msg=_res_msg:
                        post_to_tournament_channel(
                            t, 'results_channel', msg,
                            allowed_mentions={"parse": []}))

        return parent
