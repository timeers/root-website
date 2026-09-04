"""Turning an uploaded game JSON into the shapes the record-game form needs.

The wire format mirrors the read API (``GameSerializer`` /
``ParticipantSerializer`` in ``the_warroom/api/game_serializers.py``), so a game
fetched from ``/api/games/`` round-trips back into the form. The one addition is
a per-participant ``turns`` array, which the API does not emit.

Two callers share this module:

  * the upload endpoint (``import_box_score``), which validates AND resolves
    slugs against the tournament's allowed assets; and
  * ``LFGThread.turns_data``, which validates only -- a thread stores the
    ``participants`` array and has no tournament context at write time.

Resolution never queries a model directly. Callers hand in the queryset dict
that ``Tournament.get_asset_querysets()`` / ``lfg_option_querysets()`` already
produce, so "is this faction allowed here" is answered by the same querysets
every other path uses rather than by a fourth copy of the rules.
"""

from django.utils.translation import gettext as _


class BoxScoreImportError(ValueError):
    """Structurally invalid payload. The message is shown to the user."""


# Per-turn keys that carry a CUMULATIVE running total, in preference order.
# `score` is what this format documents; the other two are what
# ScoreCard.turns_data / get_turns() use, so a scorecard dict pastes in as-is.
CUMULATIVE_KEYS = ('score', 'game_points_total', 'game_points')

# Per-turn keys that carry a DELTA for that turn alone. `total_points` is
# checked first and wins outright -- in turns_data it is the turn's total, not
# another category alongside the rest -- otherwise the categories are summed.
DELTA_TOTAL_KEY = 'total_points'
DELTA_CATEGORY_KEYS = (
    'generic_points', 'battle_points', 'crafting_points',
    'faction_points', 'other_points',
)

# Category keys whose presence means the source had detail the V2 grid cannot
# show (it has one cumulative number per turn). Used only to report the
# collapse; `generic_points` is excluded because a generic-only turn loses
# nothing by being collapsed.
DETAIL_KEYS = ('battle_points', 'crafting_points', 'faction_points', 'other_points')

MAX_TURNS = 30  # mirrors ScorecardGrid.MAX_TURNS in record_game_v2.html


def _seat_label(label):
    """The 'Seat N' prefix for an error, falling back when the caller has none."""
    return label or _('Participant')


def _as_int(value, what, label=None):
    """int() that rejects the things int() quietly accepts (bools, floats with
    a fraction, numeric strings with junk) and reports which field failed.

    `label` names the seat. It matters most on the Discord path, where the user
    gets one line of feedback and cannot see the file they uploaded -- "Turn
    score must be a number" alone leaves them hunting through every seat.
    """
    prefix = '%s: ' % _seat_label(label) if label else ''
    if isinstance(value, bool):
        raise BoxScoreImportError(
            _('%(prefix)s%(what)s must be a number.') % {'prefix': prefix, 'what': what})
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise BoxScoreImportError(
            _('%(prefix)s%(what)s must be a number.') % {'prefix': prefix, 'what': what})
    if isinstance(value, float) and value != number:
        raise BoxScoreImportError(
            _('%(prefix)s%(what)s must be a whole number.')
            % {'prefix': prefix, 'what': what})
    return number


def normalize_turns(turns, *, label=None):
    """Turn entries -> ``[{'turn', 'value', 'dominance'}, ...]``, cumulative,
    contiguous from turn 1.

    Accepts either form, per turn:

      * a cumulative total (``score`` / ``game_points_total`` / ``game_points``);
      * per-turn deltas (``total_points``, or the category keys summed), which
        are accumulated into a running total.

    Returns ``(cells, notes)``. `notes` carries user-facing strings for anything
    that was altered rather than rejected -- currently the collapse of category
    detail, which the V2 grid has no columns for.

    Missing interior turns are backfilled with the previous cumulative value,
    matching the server's Phase-1 backfill in manage_game_v2.
    """
    notes = []
    if turns is None:
        return [], notes
    if not isinstance(turns, list):
        raise BoxScoreImportError(
            _('%(label)s: "turns" must be a list.') % {'label': _seat_label(label)})

    by_turn = {}
    saw_detail = False
    for entry in turns:
        if not isinstance(entry, dict):
            raise BoxScoreImportError(
                _('%(label)s: each turn must be an object.') % {'label': _seat_label(label)})

        raw_turn = entry.get('turn', entry.get('turn_number'))
        if raw_turn is None:
            raise BoxScoreImportError(
                _('%(label)s: a turn is missing its "turn" number.') % {'label': _seat_label(label)})
        turn = _as_int(raw_turn, _('Turn number'), label)
        if turn < 1:
            raise BoxScoreImportError(
                _('%(label)s: turn numbers start at 1.') % {'label': _seat_label(label)})
        if turn > MAX_TURNS:
            raise BoxScoreImportError(
                _('%(label)s: turn %(turn)s is beyond the %(max)s-turn maximum.')
                % {'label': _seat_label(label), 'turn': turn, 'max': MAX_TURNS})
        if turn in by_turn:
            raise BoxScoreImportError(
                _('%(label)s: turn %(turn)s appears more than once.')
                % {'label': _seat_label(label), 'turn': turn})

        cumulative = None
        for key in CUMULATIVE_KEYS:
            if entry.get(key) is not None:
                cumulative = _as_int(entry[key], _('Turn score'), label)
                break

        delta = None
        if cumulative is None:
            if entry.get(DELTA_TOTAL_KEY) is not None:
                delta = _as_int(entry[DELTA_TOTAL_KEY], _('Turn score'), label)
                if any(entry.get(k) for k in DETAIL_KEYS):
                    saw_detail = True
            elif any(entry.get(k) is not None for k in DELTA_CATEGORY_KEYS):
                delta = sum(_as_int(entry[k], _('Turn score'), label)
                            for k in DELTA_CATEGORY_KEYS if entry.get(k) is not None)
                if any(entry.get(k) for k in DETAIL_KEYS):
                    saw_detail = True

        by_turn[turn] = {
            'cumulative': cumulative,
            'delta': delta,
            'dominance': bool(entry.get('dominance')),
        }

    if not by_turn:
        return [], notes

    cells = []
    running = 0
    for turn in range(1, max(by_turn) + 1):
        entry = by_turn.get(turn)
        if entry is None:
            # Backfilled gap: the score did not move and no dominance was played.
            cells.append({'turn': turn, 'value': running, 'dominance': False})
            continue
        if entry['cumulative'] is not None:
            running = entry['cumulative']
        elif entry['delta'] is not None:
            running += entry['delta']
        cells.append({'turn': turn, 'value': running, 'dominance': entry['dominance']})

    if saw_detail:
        notes.append(
            _('%(label)s: point categories were collapsed into turn totals. '
              'Enter category detail from the Game detail page.')
            % {'label': label or _('Participant')})

    return cells, notes


def grid_cells_from_turns(turns, *, label=None):
    """Turn entries -> the ``{'turn', 'value', 'dominance'}`` cells that
    ``grid_prefill_json`` carries and ``ScorecardGrid.applyDraft`` consumes.

    Thin wrapper dropping ``normalize_turns``' notes, for callers that only want
    the cells -- notably the LFG prefill, which has nowhere to show a note.
    """
    cells, _notes = normalize_turns(turns, label=label)
    return cells


def validate_participants(participants):
    """Structural validation of a ``participants`` array, independent of any
    tournament. Returns the list on success; raises BoxScoreImportError.

    Deliberately does NOT check whether slugs exist or are allowed -- that needs
    querysets the caller supplies, and LFGThread.clean() has none. It checks the
    shape: that entries are objects, that turn numbers and scores are sane, and
    that seat numbers don't collide.
    """
    if participants is None:
        return []
    if not isinstance(participants, list):
        raise BoxScoreImportError(_('"participants" must be a list.'))

    seen_seats = set()
    for index, participant in enumerate(participants):
        if not isinstance(participant, dict):
            raise BoxScoreImportError(_('Each participant must be an object.'))

        # Positional label until turn_order is known -- these errors are ABOUT the
        # seat number, so "the 3rd participant" is the only handle the user has.
        label = _('Participant %(n)s') % {'n': index + 1}
        raw_seat = participant.get('turn_order', participant.get('seat'))
        if raw_seat is not None:
            seat = _as_int(raw_seat, _('turn_order'), label)
            if seat < 1:
                raise BoxScoreImportError(
                    _('%(label)s: turn_order starts at 1.') % {'label': label})
            if seat in seen_seats:
                raise BoxScoreImportError(
                    _('Two participants share turn_order %(n)s.') % {'n': seat})
            seen_seats.add(seat)
            label = _('Seat %(n)s') % {'n': seat}

        # Raises on malformed turns; the cells themselves are recomputed later.
        normalize_turns(participant.get('turns'), label=label)

        for key in ('captains',):
            if participant.get(key) is not None and not isinstance(participant[key], list):
                raise BoxScoreImportError(
                    _('%(label)s: "%(key)s" must be a list.') % {'label': label, 'key': key})

    return participants


def parse_box_score_json(raw_text):
    """Uploaded file text -> validated payload dict.

    Accepts a bare ``participants`` list as well as a full game object, so a
    thread's stored ``turns_data`` can be pasted in directly.
    """
    import json

    if isinstance(raw_text, bytes):
        try:
            raw_text = raw_text.decode('utf-8')
        except UnicodeDecodeError:
            raise BoxScoreImportError(_('That file is not valid UTF-8 text.'))

    if not (raw_text or '').strip():
        raise BoxScoreImportError(_('That file is empty.'))

    try:
        payload = json.loads(raw_text)
    except ValueError:
        raise BoxScoreImportError(_("That file isn't valid JSON."))

    if isinstance(payload, list):
        payload = {'participants': payload}
    if not isinstance(payload, dict):
        raise BoxScoreImportError(_('The file must contain a game object.'))

    if 'participants' not in payload:
        raise BoxScoreImportError(_('The file has no "participants" list.'))

    validate_participants(payload.get('participants'))
    return payload


# ── Resolution ───────────────────────────────────────────────────────────────
#
# Every lookup below filters a queryset the CALLER supplied. That is the whole
# legality mechanism: the caller passes the tournament's allowed assets, so a
# faction the tournament excludes simply doesn't resolve and is reported as a
# skip. See the module docstring.

# Faction titles that gate a dependent field, mirroring EffortCreateForm.clean.
VAGABOND_FACTION = 'Vagabond'
COALITION_FACTION = 'Chameleander'
CAPTAINS_FACTION = 'Knaves of the Deepwood'
LEADER_FACTION = 'Eyrie Dynasties'
CAPTAIN_COUNT = 3
BRAZEN_DECK = 'Squires & Disciples'

# Game-level JSON keys -> (form field, asset bucket). Named as GameSerializer
# emits them; the form field is what the client writes.
GAME_ASSET_FIELDS = {
    'board_map': ('map', 'maps'),
    'deck': ('deck', 'decks'),
    'undrafted_faction': ('undrafted_faction', 'factions'),
    'undrafted_vagabond': ('undrafted_vagabond', 'vagabonds'),
}
GAME_ASSET_LIST_FIELDS = {
    'landmarks': ('landmarks', 'landmarks'),
    'hirelings': ('hirelings', 'hirelings'),
    'tweaks': ('tweaks', 'tweaks'),
    'undrafted_captains': ('undrafted_captains', 'captains'),
}
GAME_TEXT_FIELDS = {
    'title': 'nickname',
    'table_talk_url': 'link',
    'video_link': 'video_link',
    'notes': 'notes',
}


class ImportResult:
    """What the resolver produces: values ready to write, plus what was dropped.

    `seats` entries are ``{'form_index', 'fields', 'cells'}`` where `fields`
    holds resolved primary keys (and plain values for booleans/choices), so the
    client writes them straight into ``<option>`` values.
    """

    def __init__(self):
        self.seats = []
        self.game = {}
        self.skipped = []
        self.notes = []

    def as_dict(self):
        return {
            'participants': self.seats,
            'game': self.game,
            'skipped': self.skipped,
            'notes': self.notes,
        }


def _resolve_slug(slug, queryset, model, *, label, what, result, is_player=False):
    """Find one object by slug within `queryset` (the allowed set).

    Falls back to an unfiltered lookup ONLY to choose the message, so the user
    learns whether the slug is unknown or merely disallowed here. Returns the
    object, or None after recording a skip.
    """
    if not slug:
        return None
    if not isinstance(slug, str):
        result.skipped.append(
            _('%(label)s: %(what)s must be a slug.') % {'label': label, 'what': what})
        return None

    found = queryset.filter(slug=slug).first()
    if found is not None:
        return found

    exists = model is not None and model.objects.filter(slug=slug).exists()
    if exists and is_player:
        # A player isn't "unplayable" -- they're just not on this roster.
        result.skipped.append(
            _('%(label)s: %(slug)s is not available for this game.')
            % {'label': label, 'slug': slug})
    elif exists:
        result.skipped.append(
            _('%(label)s: %(what)s "%(slug)s" is not playable here.')
            % {'label': label, 'what': what, 'slug': slug})
    else:
        result.skipped.append(
            _('%(label)s: no %(what)s matching "%(slug)s".')
            % {'label': label, 'what': what, 'slug': slug})
    return None


def resolve_import(participants, *, option_querysets, player_queryset,
                   seat_limit=None, locked_indices=(), game_data=None,
                   allow_game_fields=True):
    """Validated payload -> ImportResult.

    `option_querysets` is the bucket dict from ``Tournament.get_asset_querysets()``
    or ``lfg_option_querysets()`` (a 'notices' key, if present, is ignored).
    `player_queryset` is the roster the form's player dropdown offers.
    `seat_limit` caps how many seats may be filled (match/LFG have a fixed count);
    `locked_indices` are rows whose scorecard is server-owned and must not receive
    cells.
    """
    from the_keep.models import Faction, Vagabond, Landmark, Hireling, Tweak, Map, Deck
    from the_gatehouse.models import Profile
    from ..models import Effort

    result = ImportResult()
    participants = validate_participants(participants)
    locked_indices = set(locked_indices or ())

    buckets = {k: v for k, v in (option_querysets or {}).items() if k != 'notices'}
    models_by_bucket = {
        'factions': Faction, 'maps': Map, 'decks': Deck, 'vagabonds': Vagabond,
        'captains': Vagabond, 'landmarks': Landmark, 'tweaks': Tweak,
        'hirelings': Hireling,
    }

    dominance_values = {c.value for c in Effort.DominanceChoices}
    leader_values = {c.value for c in Effort.LeaderChoices}

    # ── Game-level fields ────────────────────────────────────────────────
    game_data = game_data or {}
    deck_title = None
    if allow_game_fields:
        label = _('Game')
        for key, (field, bucket) in GAME_ASSET_FIELDS.items():
            if game_data.get(key) is None:
                continue
            obj = _resolve_slug(game_data[key], buckets.get(bucket),
                                models_by_bucket.get(bucket),
                                label=label, what=field.replace('_', ' '), result=result)
            if obj is not None:
                result.game[field] = obj.pk
                if field == 'deck':
                    deck_title = obj.title

        for key, (field, bucket) in GAME_ASSET_LIST_FIELDS.items():
            values = game_data.get(key)
            if not values:
                continue
            if not isinstance(values, list):
                result.skipped.append(
                    _('Game: "%(key)s" must be a list.') % {'key': key})
                continue
            ids = []
            for slug in values:
                obj = _resolve_slug(slug, buckets.get(bucket), models_by_bucket.get(bucket),
                                    label=label, what=key.rstrip('s'), result=result)
                if obj is not None:
                    ids.append(obj.pk)
            if ids:
                result.game[field] = ids

        for key, field in GAME_TEXT_FIELDS.items():
            value = game_data.get(key)
            if isinstance(value, str) and value.strip():
                result.game[field] = value.strip()

        if game_data.get('random_suits') is not None:
            result.game['random_clearing'] = bool(game_data['random_suits'])

        # The API lowercases Game.type on the way out; map it back to the
        # TextChoices value the form expects.
        timing = game_data.get('turn_timing')
        if isinstance(timing, str) and timing.strip():
            match = next((c.value for c in _game_type_choices()
                          if c.value.lower() == timing.strip().lower()), None)
            if match:
                result.game['type'] = match
            else:
                result.skipped.append(
                    _('Game: unknown turn timing "%(value)s".') % {'value': timing})

        date_value = game_data.get('date_registered') or game_data.get('date_posted')
        if isinstance(date_value, str) and date_value.strip():
            result.game['date_posted'] = date_value.strip()

        if game_data.get('tournament'):
            result.notes.append(
                _('The file names a tournament round. The round is set by how you '
                  'opened this form, so it was left unchanged.'))

    # A deck already chosen on the form wins over one in the file for the
    # purpose of brazen gating -- the caller passes what the form currently has.
    if deck_title is None:
        deck_title = (game_data or {}).get('_current_deck_title')

    # ── Per-seat fields ──────────────────────────────────────────────────
    for index, participant in enumerate(participants):
        raw_seat = participant.get('turn_order', participant.get('seat'))
        seat_number = int(raw_seat) if raw_seat is not None else index + 1
        form_index = seat_number - 1
        label = _('Seat %(n)s') % {'n': seat_number}

        if seat_limit is not None and form_index >= seat_limit:
            result.skipped.append(
                _('%(label)s: this game only has %(n)s seats.')
                % {'label': label, 'n': seat_limit})
            continue

        fields = {}
        faction = _resolve_slug(participant.get('faction'), buckets.get('factions'),
                                Faction, label=label, what=_('faction'), result=result)
        if faction is not None:
            fields['faction'] = faction.pk

        player = None
        player_slug = participant.get('player')
        if player_slug:
            player = _resolve_slug(player_slug, player_queryset, Profile,
                                   label=label, what=_('player'), result=result,
                                   is_player=True)
            if player is not None:
                fields['player'] = player.pk

        faction_title = faction.title if faction is not None else None

        # Dependent fields, gated on faction exactly as EffortCreateForm.clean
        # gates them -- so the importer can't produce a state the form rejects.
        if participant.get('vagabond'):
            if faction_title == VAGABOND_FACTION:
                vagabond = _resolve_slug(participant['vagabond'], buckets.get('vagabonds'),
                                         Vagabond, label=label, what=_('vagabond'),
                                         result=result)
                if vagabond is not None:
                    fields['vagabond'] = vagabond.pk
            else:
                result.skipped.append(
                    _('%(label)s: a vagabond only applies to the Vagabond faction.')
                    % {'label': label})

        if participant.get('coalition'):
            if faction_title == COALITION_FACTION:
                coalition = _resolve_slug(participant['coalition'], buckets.get('factions'),
                                          Faction, label=label, what=_('coalition'),
                                          result=result)
                if coalition is not None:
                    fields['coalition_with'] = coalition.pk
            else:
                result.skipped.append(
                    _('%(label)s: a coalition only applies to %(faction)s.')
                    % {'label': label, 'faction': COALITION_FACTION})

        if participant.get('starting_leader'):
            leader = participant['starting_leader']
            if faction_title != LEADER_FACTION:
                result.skipped.append(
                    _('%(label)s: a starting leader only applies to %(faction)s.')
                    % {'label': label, 'faction': LEADER_FACTION})
            elif leader not in leader_values:
                result.skipped.append(
                    _('%(label)s: unknown starting leader "%(value)s".')
                    % {'label': label, 'value': leader})
            else:
                fields['starting_leader'] = leader

        if participant.get('captains'):
            if faction_title != CAPTAINS_FACTION:
                result.skipped.append(
                    _('%(label)s: captains only apply to %(faction)s.')
                    % {'label': label, 'faction': CAPTAINS_FACTION})
            else:
                captain_ids = []
                for slug in participant['captains']:
                    captain = _resolve_slug(slug, buckets.get('captains'), Vagabond,
                                            label=label, what=_('captain'), result=result)
                    if captain is not None:
                        captain_ids.append(captain.pk)
                if len(captain_ids) == CAPTAIN_COUNT:
                    fields['captains'] = captain_ids
                elif captain_ids:
                    result.skipped.append(
                        _('%(label)s: captains need exactly %(n)s, so they were left blank.')
                        % {'label': label, 'n': CAPTAIN_COUNT})

                if participant.get('discarded_captain'):
                    discarded = _resolve_slug(
                        participant['discarded_captain'], buckets.get('captains'),
                        Vagabond, label=label, what=_('discarded captain'), result=result)
                    if discarded is not None and discarded.pk not in captain_ids:
                        fields['discarded_captain'] = discarded.pk

        dominance = participant.get('dominance')
        if dominance:
            if dominance in dominance_values:
                fields['dominance'] = dominance
            else:
                result.skipped.append(
                    _('%(label)s: unknown dominance "%(value)s".')
                    % {'label': label, 'value': dominance})

        # Brazen demagogue rides on dominance only. The deck is NOT gated here:
        # it may not be chosen yet, and the view clears the field at save time
        # unless the deck is Squires & Disciples (see manage_game_v2), so an
        # invalid value can never be stored. The client stashes it until the
        # deck question is settled.
        if participant.get('brazen_demagogue'):
            if 'dominance' in fields:
                fields['brazen_demagogue'] = True
                if deck_title and deck_title != BRAZEN_DECK:
                    result.notes.append(
                        _('%(label)s: Brazen Demagogue needs the %(deck)s deck, so it '
                          'will be cleared when you submit.')
                        % {'label': label, 'deck': BRAZEN_DECK})
            else:
                result.skipped.append(
                    _('%(label)s: Brazen Demagogue needs a dominance.') % {'label': label})

        # tournament_score is how the API carries Effort.win: 0 loss, 0.5
        # coalition win, 1 solo win. Without this a sub-30 victory (dominance,
        # coalition, timed finish) would import with no winner at all, since the
        # form only auto-checks Win at 30+.
        tournament_score = participant.get('tournament_score')
        if tournament_score is not None:
            try:
                fields['win'] = float(tournament_score) > 0
            except (TypeError, ValueError):
                result.skipped.append(
                    _('%(label)s: tournament_score must be a number.') % {'label': label})

        cells = []
        if form_index in locked_indices:
            if participant.get('turns'):
                result.skipped.append(
                    _('%(label)s: its box score is detailed and locked, so the turns '
                      'in the file were ignored.') % {'label': label})
        else:
            cells, turn_notes = normalize_turns(participant.get('turns'), label=label)
            result.notes.extend(turn_notes)

            # game_score is accepted but the grid recomputes Score from the last
            # cumulative cell, so surface a disagreement rather than hiding it.
            game_score = participant.get('game_score')
            if game_score is not None and cells and int(game_score) != cells[-1]['value']:
                result.notes.append(
                    _('%(label)s: game_score (%(score)s) disagrees with the last turn '
                      '(%(last)s); the turns were used.')
                    % {'label': label, 'score': game_score, 'last': cells[-1]['value']})

        if fields or cells:
            result.seats.append({
                'form_index': form_index,
                'fields': fields,
                'cells': cells,
            })

    return result


def _game_type_choices():
    from ..models import Game
    return list(Game.TypeChoices)
