"""Recording a game straight from a box score, with no human at the form.

A box score that needs no corrections already contains everything the record
form asks for. An LFG thread records in LFG mode; a series (tournament group)
thread records in MATCH mode, onto the series' next open match. This drives that form HEADLESSLY -- the real GameCreateFormV2 and
the real Effort formset, built from a plain dict rather than a POST -- so every
tournament rule, roster check and coalition rule applies exactly as it does on
the web. Nothing here re-implements validation; if the form says no, the box
score falls back to offering a record link and a person finishes it.

Why a data dict and not `initial`: a form built with `data=` is BOUND, and Django
reads bound values only from `data`. The view's prefill helpers all write
`.initial` and are gated `if not request.POST` for exactly that reason, so they
cannot be reused to produce something validatable.
"""
import json
import logging

from django.forms.models import modelformset_factory

from the_warroom.forms import GameCreateFormV2, EffortCreateForm
from the_warroom.models import Effort, Game, PlatformChoices
from the_warroom.services.box_score_import import (
    BoxScoreImportError, grid_cells_from_turns)

logger = logging.getLogger(__name__)


def _declined(thread, reason, *args):
    """Log why auto-record is not happening and return None.

    Every refusal used to be a bare `return None`, which made the feature
    undiagnosable in production: "not enabled for this guild", "recorder isn't
    onboarded" and "the box score failed validation" were indistinguishable from
    each other and from "auto-record never ran at all".

    WARNING, not INFO, and deliberately so: this project configures no LOGGING
    dict, so Django's default applies and a non-Django logger is only emitted at
    WARNING and above -- `logger.isEnabledFor(logging.INFO)` is False in
    production. An INFO line here would be invisible in exactly the place it
    exists to be read, which is the whole point of the message. Same reason the
    timing diagnostics in the_warroom/views.py use .warning().

    Revisit if a LOGGING dict is ever added: declining is a normal outcome, so
    INFO is the level this *deserves* once INFO is actually emitted.

    The shared "auto-record declined" prefix is deliberate -- the guild gate logs
    the same prefix from a DIFFERENT module (and so a different logger name), and
    the prefix is what makes the two greppable as one stream.
    """
    logger.warning("auto-record declined for thread %s: " + reason,
                   thread.pk, *args)
    return None


def _seat_rows(thread):
    """[(seat_number, Profile|None, faction_slug, vagabond_slug), ...] or [].

    Empty means the thread has no established ORDER (seating_set False), which is
    disqualifying here: without it we would be placing players on rows nobody
    chose, and an auto-recorded game must never invent a seating.
    """
    from the_databot.services.lfg_game import seated_profiles
    return seated_profiles(thread)


def _entry_by_seat(turns_data):
    """turn_order -> entry, for the box score's participant array."""
    out = {}
    for entry in (turns_data or []):
        if not isinstance(entry, dict):
            continue
        seat_no = entry.get('turn_order', entry.get('seat'))
        if seat_no is not None:
            out[seat_no] = entry
    return out


def _platform_for(thread, tournament):
    """The platform this game must record under.

    A tournament can LOCK its platform, and the form errors when they disagree
    ("Select <platform> for this <tournament> Game"), so the tournament wins --
    defaulting to TTS would make every auto-record fail on a Root Digital
    tournament. Otherwise a /draft may have recorded one. Failing both, TTS: the
    box score API is a Tabletop Simulator object by construction, and a JSON
    export is overwhelmingly a TTS game.
    """
    if tournament is not None and tournament.platform:
        return tournament.platform
    draft = getattr(thread, 'draft', None)
    if draft is not None and draft.platform:
        return draft.platform
    return PlatformChoices.TTS


def _type_for(payload):
    """Game.type from the file's turn_timing, else the model default (Live)."""
    timing = (payload or {}).get('turn_timing')
    if isinstance(timing, str) and timing.strip():
        for choice in Game.TypeChoices:
            if choice.value.lower() == timing.strip().lower():
                return choice.value
    return Game.TypeChoices.LIVE


def _match_for(thread):
    """The match this series thread's next game records against, or None.

    The same query the record view uses for a series thread (manage_game): the
    lowest-numbered match with no game that isn't COMPLETED. A series that is
    already decided can still have open matches, and those stay recordable.
    """
    from the_warroom.models import CompetitionStatus, Match
    return (Match.objects
            .filter(series_id=thread.series_id, game__isnull=True)
            .exclude(status=CompetitionStatus.COMPLETED)
            .select_related('round', 'series')
            .order_by('match_number').first())


def needs_message_link(thread):
    """Whether this thread's next game can only be recorded once its box score
    message exists: a series thread whose open match is in a link_required
    tournament.

    Game.link is unique, and a series thread is shared by every game of its
    best-of-N, so the THREAD's link can identify at most one of them. The box
    score message's own link is unique to the game -- but on a clean upload it
    is posted by post_boxscore_result_task after this request, so recording
    waits for it (that task records the game once it has the link).
    """
    if not thread.series_id:
        return False
    match = _match_for(thread)
    if match is None:
        return False
    tournament = match.round.get_tournament()
    return bool(tournament and tournament.link_required)


def build_form_data(thread, seats, entries, *, tournament, opts,
                    platform, game_type, grid_rows, link=None):
    """The POST-shaped dict the form and formset validate against.

    `grid_rows` is filled IN PLACE with {form_index: cells} so the caller can
    serialize the same grid into `scorecard_grid` -- the form reads it as a JSON
    string for the box_score_required rule, and record_game takes the parsed
    dict. One source, two representations.
    """
    data = {
        'form-TOTAL_FORMS': str(len(seats)),
        'form-INITIAL_FORMS': '0',
        'form-MIN_NUM_FORMS': '0',
        'form-MAX_NUM_FORMS': '1000',
        'platform': platform,
        'type': game_type,
        # WITHOUT THIS the form takes clean()'s draft branch, which skips almost
        # every rule -- including "Select a winner". A winnerless game would
        # validate and then be saved as final.
        'final': 'True',
    }
    # A tournament with link_required rejects a game without one ("Provide a
    # unique link to this game's thread") -- and the record form only ever got
    # it from a prefill, so every auto-record into such a tournament failed.
    # Game.link must be UNIQUE, and a series thread is shared by every game of
    # its best-of-N, so its link can identify at most one of them. A series
    # game takes `link` -- its box score message's own link, when the caller
    # has one -- and never the thread's; an LFG game holds the thread alone.
    if not thread.series_id:
        link = thread.thread_url()
    if link:
        data['link'] = link
    if thread.map_id:
        data['map'] = str(thread.map_id)
    if thread.deck_id:
        data['deck'] = str(thread.deck_id)

    for i, (seat_no, profile, faction_slug, vagabond_slug) in enumerate(seats):
        entry = entries.get(seat_no, {})
        prefix = f'form-{i}-'

        if profile is not None:
            data[prefix + 'player'] = str(profile.pk)

        if faction_slug:
            faction = opts['factions'].filter(slug=faction_slug).first()
            if faction:
                data[prefix + 'faction'] = str(faction.pk)
        if vagabond_slug:
            vagabond = opts['vagabonds'].filter(slug=vagabond_slug).first()
            if vagabond:
                data[prefix + 'vagabond'] = str(vagabond.pk)

        # Effort.dominance IS the suit, not a per-turn flag.
        dominance = entry.get('dominance')
        if dominance in {c.value for c in Effort.DominanceChoices}:
            data[prefix + 'dominance'] = dominance
            if entry.get('brazen_demagogue'):
                data[prefix + 'brazen_demagogue'] = 'on'

        starting_leader = entry.get('starting_leader')
        if starting_leader in {c.value for c in Effort.LeaderChoices}:
            data[prefix + 'starting_leader'] = starting_leader

        coalition = entry.get('coalition')
        if coalition:
            partner = opts['factions'].filter(slug=coalition).first()
            if partner:
                data[prefix + 'coalition_with'] = str(partner.pk)

        # tournament_score is how a box score carries the win: 0 loss, 0.5
        # coalition win, 1 solo win. A sub-30 victory has no other signal.
        tournament_score = entry.get('tournament_score')
        if tournament_score is not None:
            try:
                if float(tournament_score) > 0:
                    data[prefix + 'win'] = 'on'
            except (TypeError, ValueError):
                pass

        # Score comes from the LAST grid cell and must equal it exactly, or a
        # box_score_required tournament rejects the game for disagreeing with
        # its own scorecard.
        try:
            cells = grid_cells_from_turns(entry.get('turns'))
        except BoxScoreImportError:
            cells = []
        if cells:
            grid_rows[i] = cells
            data[prefix + 'score'] = str(cells[-1]['value'])
        else:
            data[prefix + 'score'] = '0'

    return data


def attempt_autorecord(thread, recorder, *, payload=None, link=None):
    """Record `thread`'s game from its captured box score, or return None.

    Returns the saved Game, or None whenever ANYTHING stops it -- no recorder, an
    ineligible one, no seating, a missing round, a validation error. Never
    raises: a box score must still post when it cannot be recorded, and the
    fallback is simply the record link the message already carried.

    `link` is the box score MESSAGE's Discord link, for a series game; see
    build_form_data. Ignored on an LFG thread, which uses its own.
    """
    try:
        return _attempt(thread, recorder, payload, link)
    except Exception:
        # Deliberately broad. GameCreateForm.clean() calls effort_formset
        # .is_valid() unguarded and resolution touches a dozen models; an
        # unexpected shape must degrade to "a human records it", never take the
        # box score message down with it.
        logger.exception("auto-record failed for thread %s", thread.pk)
        return None


def _attempt(thread, recorder, payload, link=None):
    from the_warroom.models import Round
    from the_warroom.services.game_recording import AlreadyRecorded, record_game
    from the_warroom.views import (
        _can_record_lfg, _can_record_match, _get_match_profiles, _lfg_round_for,
        _lfg_tournament_for)
    from the_databot.services.lfg_game import lfg_option_querysets

    if recorder is None:
        return _declined(thread, "no recorder resolved")
    # The same bar the web form's decorator sets. A Profile created from a
    # Discord upload is OUTCAST and not onboarded, so it exists but may not yet
    # record -- the link walks them through that rather than the bot silently
    # granting rights the site withholds.
    if not recorder.player or not recorder.player_onboard:
        return _declined(thread, "recorder %s is not an onboarded player "
                                 "(player=%s onboard=%s)",
                         recorder.pk, recorder.player, recorder.player_onboard)

    if thread.game_id:
        return _declined(thread, "already recorded as game %s", thread.game_id)

    # A series thread is a tournament group thread spanning a best-of-N, so it
    # records in MATCH mode, exactly as the record view does for one: the game
    # goes on the series' next open match, and record_game does the bracket
    # wiring. The form enforces the match roster ("X is required in this
    # match"), so a seat nobody identified declines here like any other error.
    match = None
    if thread.series_id:
        match = _match_for(thread)
        if match is None:
            return _declined(thread, "series %s has no open match",
                             thread.series_id)
        # FINALIZED is the PUBLISHED bracket, not a finished round: a DRAFT one
        # can still be redrawn, and the record view refuses it the same way.
        if match.round.bracket_status != Round.BracketStatusChoices.FINALIZED:
            return _declined(thread, "match %s bracket is not published",
                             match.pk)

    seats = _seat_rows(thread)
    if not seats:
        return _declined(thread, "no established seating (seating_set=%s)",
                         thread.seating_set)
    # Checked up front rather than left to the form's "Select a Map and a Deck":
    # a box score that names neither can't be auto-recorded, and the record link
    # is where someone fills them in. Same outcome, but the reason is logged as
    # itself instead of as a generic validation failure.
    if not thread.map_id or not thread.deck_id:
        return _declined(thread, "no map/deck (map=%s deck=%s)",
                         thread.map_id, thread.deck_id)

    if match is not None:
        # The round is the match's own (the form locks it from `match`).
        tournament = match.round.get_tournament()
        lfg_round = None
        if not _can_record_match(recorder, match):
            return _declined(thread, "recorder %s may not record match %s",
                             recorder.pk, match.pk)
        # Said as itself rather than left to the form's "Provide a unique link":
        # this is the one decline that means "try again once the message is
        # posted", not "a person must fix something".
        if tournament and tournament.link_required and not link:
            return _declined(thread, "match %s needs a unique link and has none "
                             "yet", match.pk)
    else:
        tournament = _lfg_tournament_for(thread)
        lfg_round = _lfg_round_for(tournament)
        if tournament is not None and lfg_round is None:
            # No open round: Game.round is nullable, so the form would clean to
            # None and skip the ENTIRE tournament rule block rather than
            # erroring. The view refuses outright here; so do we.
            return _declined(thread, "tournament %s has no open round",
                             tournament.pk)
        if not (recorder.admin or _can_record_lfg(recorder, thread, lfg_round)):
            return _declined(thread, "recorder %s may not record this thread",
                             recorder.pk)

    opts = lfg_option_querysets(thread, tournament)
    grid_rows = {}
    data = build_form_data(
        thread, seats, _entry_by_seat(thread.turns_data),
        tournament=tournament, opts=opts,
        platform=_platform_for(thread, tournament),
        game_type=_type_for(payload),
        grid_rows=grid_rows,
        link=link,
    )

    if match is not None:
        # The view's match-mode initial, which a bound form never reads.
        data['nickname'] = f'{match.round} {match.name}'[:50]
        data['match_id'] = str(match.pk)

    grid_payload = {'rows': [
        {'form_index': i, 'locked': False, 'cells': cells}
        for i, cells in sorted(grid_rows.items())
    ]}
    # The form reads this as a JSON STRING out of its own data (the
    # box_score_required rule); record_game takes the parsed dict.
    data['scorecard_grid'] = json.dumps(grid_payload)

    EffortFormset = modelformset_factory(
        Effort, form=EffortCreateForm, extra=len(seats))
    formset = EffortFormset(data, queryset=Effort.objects.none())
    if match is not None:
        # The view narrows the player dropdown to the match the same way. Made
        # distinct: the join through stage participations can repeat a profile,
        # and a ModelChoiceField raises on a value that matches twice.
        match_profiles = _get_match_profiles(match).distinct()
        for effort_form in formset.forms:
            effort_form.fields['player'].queryset = match_profiles
    form = GameCreateFormV2(
        data,
        instance=Game(),
        profile=recorder,
        effort_formset=formset,
        match=match,
        lfgthread=thread,
        lfg_round=lfg_round,
    )

    if not (form.is_valid() and formset.is_valid()):
        # The decline most likely to need explaining -- and the one that was
        # previously logged at DEBUG, i.e. never seen.
        return _declined(thread, "validation failed: form=%s formset=%s",
                         form.errors.as_json(), formset.errors)

    if match is not None:
        try:
            # lfgthread is still passed, as the view passes it in match mode: it
            # is how record_game finds this thread's box score message to tidy.
            # lfg_mode stays False, so the thread is NOT linked to the game --
            # a series thread outlives any one of its games.
            return record_game(
                form=form,
                formset=formset,
                recorder=recorder,
                match=match,
                match_mode=True,
                lfgthread=thread,
                lfg_mode=False,
                final=True,
                seat_order=None,
                scorecard_grid=grid_payload,
                initial_game_status=None,
                game_was_final=False,
                match_initial_status=None,
            )
        except AlreadyRecorded as exc:
            # Lost a race to another submission for the same match, which
            # record_game detects under the match row lock.
            return _declined(thread, "match %s was recorded meanwhile (game %s)",
                             match.pk, exc.game_id)

    return record_game(
        form=form,
        formset=formset,
        recorder=recorder,
        lfgthread=thread,
        lfg_mode=True,
        lfg_round=lfg_round,
        final=True,
        seat_order=None,
        scorecard_grid=grid_payload,
        initial_game_status=None,
        game_was_final=False,
        lfg_initial_status=thread.status,
    )
