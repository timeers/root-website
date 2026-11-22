"""Forge maps (ForgedMap) published as keep Maps.

What's published is always the last *rendered* map: the generated image and
the layout it was rendered from (ForgedMap.rendered_builder), saved together by
refresh_map_image. The live layout (tts_builder) can be ahead of the image
while the designer works; that's only published after the next Complete."""
import hashlib
import json
from types import SimpleNamespace

from django import forms

from the_keep.forms import MapCreateForm
from the_keep.models import Map, Post
from the_keep.services.tts_map import (apply_builder_counts, build_map_tts_json, check_builder,
                                       clean_builder_state, layout_counts)

from .base import Publisher, Requirement, SyncField, require_thread_link

# A keep post's title is at most this long (Post.title), so a map's name must be too.
MAX_TITLE = Post._meta.get_field('title').max_length


def forged_map_of(forged):
    from ..models import ForgedMap
    try:
        return forged.forged_map
    except ForgedMap.DoesNotExist:
        return None


def rendered(forged):
    """(forged_map, the layout its image was rendered from), or (map, None)
    while there's no rendered map yet."""
    forged_map = forged_map_of(forged)
    if forged_map is None or not forged_map.image_preview or not forged_map.rendered_builder:
        return forged_map, None
    return forged_map, forged_map.rendered_builder


def rendered_errors(state):
    """The builder's errors for a rendered layout (the Forge's rules: at least
    one building slot and one ruin)."""
    stand_in = SimpleNamespace(fixed_clearings=bool(state.get('fixed')))
    errors, _warnings = check_builder(stand_in, state, require_buildings=True)
    return errors


def _count(field):
    def source(forged):
        _forged_map, state = rendered(forged)
        return layout_counts(state)[field] if state else None
    return source


def _fixed(forged):
    _forged_map, state = rendered(forged)
    return bool(state.get('fixed')) if state else None


def _layout(forged):
    _forged_map, state = rendered(forged)
    return clean_builder_state(state) if state else None


def _layout_in_sync(forged, current, proposed):
    return clean_builder_state(current or {}) == proposed


def _layout_summary(state):
    if not state:
        return 'No layout'
    counts = layout_counts(state)
    return (f"{counts['clearings']} clearings, {counts['building_slots']} building slots "
            f"({counts['ruins']} ruins), {counts['forests']} forests")


def _image(forged):
    forged_map, state = rendered(forged)
    return forged_map.image_preview if state else None


def _image_version(forged):
    forged_map = forged_map_of(forged)
    return (forged_map.preview_version or 0) if forged_map else 0


class MapSubmitForm(MapCreateForm):
    """Submit a Forge map as a new keep Map. The name, language and the counts
    come from the map (its rendered layout) and are shown read-only; the
    designer adds the lore, description, links, what it's based on, its
    version and artist."""

    EDITABLE_FIELDS = {
        'lore', 'description', 'ww_link', 'bgg_link', 'tts_link', 'wr_link', 'fr_link',
        'pnp_link', 'stl_link', 'based_on', 'version', 'artist', 'river_clearings',
    }
    OPTIONAL_LINK_FIELDS = ('bgg_link', 'tts_link', 'wr_link', 'fr_link', 'pnp_link', 'stl_link')

    def __init__(self, *args, forged=None, **kwargs):
        if forged is None:
            raise ValueError('MapSubmitForm requires forged')
        self.forged = forged
        forged_map, state = rendered(forged)
        initial = kwargs.setdefault('initial', {})
        initial.setdefault('title', forged.faction_name)
        initial.setdefault('language', forged.language_id)
        if state:
            initial.update(layout_counts(state))
            initial['fixed_clearings'] = bool(state.get('fixed'))
        super().__init__(*args, **kwargs)

        # PostCreateForm sets the language from the user's profile: use the map's.
        if 'language' in self.fields and forged.language_id:
            self.fields['language'].initial = forged.language_id

        # Not on this page: images come from the map, and the rest can be set
        # on the published Map later, as for any post (and as Forge factions do).
        for name in ('board_image', 'card_image', 'card_2_image', 'picture', 'small_icon',
                     'designer', 'co_designers', 'co_designers_can_edit', 'moderators', 'official',
                     'in_root_digital', 'expansion', 'status', 'art_by_kyle_ferrin', 'ai_generated_art',
                     'rootjam_link', 'leder_games_link'):
            self.fields.pop(name, None)

        # Every link is optional on its own: clean() requires a thread on one
        # of Woodland Warriors, French Root or Board Game Geek.
        for name in ('ww_link',) + self.OPTIONAL_LINK_FIELDS:
            if name in self.fields:
                self.fields[name].required = False

        # Everything else is the map's own, shown but not editable. Django
        # still validates these against their initial values, which a
        # completed map always satisfies (the Forge builder requires a clearing,
        # a building slot and a ruin).
        for name, field in self.fields.items():
            if name not in self.EDITABLE_FIELDS:
                field.disabled = True

        # The details card's inputs (map_submit.html).
        details = {
            'lore': ('Lore', {'rows': 3, 'placeholder': 'Thematic text for your map.'}),
            'description': ('How to Play', {'rows': 4, 'placeholder': 'Special instructions or details about how to setup or play on this map.'}),
            'version': ('Version', {'placeholder': 'e.g. 1.0'}),
            'river_clearings': ('River Clearings', {'min': 0, 'placeholder': '0'}),
            'based_on': ('Based On', {}),
            'artist': ('Artist', {}),
        }
        for name, (label, attrs) in details.items():
            field = self.fields.get(name)
            if field is None:
                continue
            field.label = label
            field.help_text = ''
            css = 'form-select' if name in ('based_on', 'artist') else 'form-control'
            field.widget.attrs.update({'class': css, **attrs})

    def clean(self):
        cleaned = super().clean()
        require_thread_link(self, cleaned)
        return cleaned


class MapPublisher(Publisher):
    components = (Post.ComponentChoices.MAP,)
    keep_model = Map
    noun = 'Map'
    detail_url_name = 'forge-map-detail'
    submit_url_name = 'forge-map-submit'
    link_url_name = 'forge-map-link'
    unlink_url_name = 'forge-map-unlink'
    sync_url_name = 'forge-map-sync'
    submit_template = 'the_forge/map_submit.html'
    # The keep TTS JSON is built from the keep's board image and layout.
    tts_forced_fields = ('board_image', 'tts_builder')

    # ── Forge side ──────────────────────────────────────────────────────────
    # refresh: nothing. Only the builder's Complete (or a download) renders a
    # map, so publishing never renders and never waits for the renderer.

    def prerequisites(self, forged):
        forged_map, state = rendered(forged)
        name_ok = 0 < len(forged.faction_name or '') <= MAX_TITLE
        reqs = [
            Requirement('Map Name', name_ok, None if name_ok else
                        f'Keep the name to {MAX_TITLE} characters (rename it on the Start step).'),
            Requirement('Map Art', bool(forged_map and forged_map.art)),
        ]
        if state is None:
            reqs.append(Requirement('Completed Map', False,
                                    'Finish the map in the builder and click Complete.'))
        else:
            errors = rendered_errors(state)
            reqs.append(Requirement('Completed Map', not errors, ' '.join(errors) or None))
        return reqs

    def pending_note(self, forged):
        """Shown on the publish card when the map has changes that aren't
        rendered yet (so they won't be published)."""
        forged_map = forged_map_of(forged)
        if forged_map and forged_map.rendered_builder and forged_map.preview_stale:
            return "You have changes that haven't been completed yet: click Complete in the builder to publish them."
        return None

    def sync_blocked(self, forged):
        _forged_map, state = rendered(forged)
        if state is None:
            return 'Complete the map in the builder first.'
        errors = rendered_errors(state)
        return ('Fix the map in the builder first: ' + ' '.join(errors)) if errors else None

    # ── Submit ──────────────────────────────────────────────────────────────
    def submit_form(self, forged, user, data=None, files=None):
        if data is None:
            return MapSubmitForm(forged=forged, user=user)
        return MapSubmitForm(data, files, forged=forged, user=user)

    def submit_images(self, forged):
        forged_map, state = rendered(forged)
        if not state:
            return {}
        return {'board_image': (forged_map.image_preview, forged_map.preview_version or 0)}

    def after_submit(self, forged, post, profile):
        """The new Map gets the map's layout and counts (the form already set
        the counts; apply_builder_counts is the keep builder's own writer), then
        its Tabletop Simulator JSON, built on its own (keep) board image."""
        _forged_map, state = rendered(forged)
        layout = clean_builder_state(state)
        Map.objects.filter(pk=post.pk).update(tts_builder=layout)
        post.tts_builder = layout
        apply_builder_counts(post, layout)
        from .tts import store_keep_tts
        store_keep_tts(forged, self, post, profile)

    def submit_context(self, forged, form):
        forged_map, state = rendered(forged)
        from .base import link_context
        return {'forged_map': forged_map, 'counts': layout_counts(state) if state else {}, **link_context()}

    # ── Sync ────────────────────────────────────────────────────────────────
    def post_fields(self, forged):
        return [
            SyncField('board_image', 'Map Image', _image, kind='image',
                      version=_image_version, keep_version_attr='board_image_version'),
            SyncField('clearings', 'Clearings', _count('clearings'), kind='int'),
            SyncField('building_slots', 'Building Slots', _count('building_slots'), kind='int'),
            SyncField('ruins', 'Ruins', _count('ruins'), kind='int'),
            SyncField('forests', 'Forests', _count('forests'), kind='int'),
            SyncField('fixed_clearings', 'Fixed Clearing Suits', _fixed, kind='bool'),
            SyncField('tts_builder', 'Map Layout', _layout, kind='json',
                      in_sync=_layout_in_sync, display=_layout_summary),
        ]

    def translation_fields(self, forged):
        return [SyncField('translated_board_image', 'Map Image (translation)', _image, kind='image',
                          version=_image_version, keep_version_attr='translated_board_image_version')]

    def tts_fingerprint(self, forged):
        forged_map, state = rendered(forged)
        payload = {'image': forged_map.preview_version if forged_map else 0, 'layout': state}
        return hashlib.md5(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()

    def tts_plan(self, forged, diff_rows, extras):
        from the_keep.utils import tts_json_hash
        if not forged.published_post_id or rendered(forged)[1] is None:
            return None
        keep_map = forged.published_keep_post
        stale_count = sum(1 for row in diff_rows if row[0] in self.tts_forced_fields and not row[5])
        keep_json = keep_map.tts_json
        json_matches = bool(keep_json) and tts_json_hash(keep_json) == forged.tts_synced_hash
        source_matches = self.tts_fingerprint(forged) == forged.tts_source_fingerprint
        return {
            'in_sync': json_matches and source_matches and stale_count == 0,
            'keep_object_count': len(keep_json.get('ObjectStates', [])) if isinstance(keep_json, dict) else 0,
            'keep_replaced': bool(keep_json) and not json_matches,
            'forge_changed': not source_matches,
            'stale_count': stale_count,
        }

    def build_keep_tts(self, forged, target):
        keep_map = Map.objects.get(pk=target.pk)
        if not keep_map.board_image or not keep_map.tts_builder:
            return None, "the Map has no board image or layout yet."
        errors, _warnings = check_builder(keep_map, keep_map.tts_builder)
        if errors:
            return None, ' '.join(errors)
        return build_map_tts_json(keep_map, keep_map.tts_builder), None
