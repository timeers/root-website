"""Factions and Clockwork: the original Forge publishing, as a Publisher. The
field sources, piece / deck / TTS-asset plans and the TTS conversion are the
long-standing helpers in forms_publish.py and services/tts_convert.py."""
from the_keep.models import Faction, Post

from .. import forms_publish as fp
from .base import Publisher, Requirement, SyncField

# The keep field each Faction image comes from on the draft (the related
# element whose preview_version is compared with the keep's version counter).
_POST_IMAGES = {
    'board_image': ('faction_sheet', 'board_image_version'),
    'board_2_image': ('faction_back', 'board_2_image_version'),
    'card_image': ('setup_card', 'card_image_version'),
}
_TRANSLATION_IMAGES = {
    'translated_board_image': ('faction_sheet', 'translated_board_image_version'),
    'translated_board_2_image': ('faction_back', 'translated_board_2_image_version'),
    'translated_card_image': ('setup_card', 'translated_card_image_version'),
}
_KINDS = {'complexity': 'choice', 'card_wealth': 'choice', 'aggression': 'choice',
          'crafting_ability': 'choice', 'type': 'choice', 'reach': 'int'}


def _icon_in_sync(forged, current, proposed):
    # The keep reprocesses the icon at a smaller size, so its bytes never match
    # the Forge icon. Instead the Forge icon's name is remembered at each push,
    # and the icon is out of date only when that name has changed since.
    name = proposed.name if proposed else ''
    return bool(name) and name == (forged.icon_synced_name or '')


def _remember_icon(forged):
    forged.icon_synced_name = forged.faction_icon.name if forged.faction_icon else ''
    forged.save(update_fields=['icon_synced_name'])


def _post_field(name):
    source = lambda forged, name=name: fp._proposed_for_faction(forged, name)  # noqa: E731
    label = fp.FACTION_FIELD_LABELS[name]
    if name in _POST_IMAGES:
        related, keep_attr = _POST_IMAGES[name]
        return SyncField(name, label, source, kind='image', keep_version_attr=keep_attr,
                         version=lambda forged, related=related: fp._forge_preview_version(forged, related))
    if name == 'small_icon':
        return SyncField(name, label, source, kind='image', in_sync=_icon_in_sync, on_applied=_remember_icon)
    display = None
    if name in fp.ATTRIBUTE_FIELDS:
        display = fp._wrap_attribute
    elif name == 'type':
        display = fp._wrap_type
    return SyncField(name, label, source, kind=_KINDS.get(name, 'text'), display=display)


def _translation_field(name):
    source = lambda forged, name=name: fp._proposed_for_translation(forged, name)  # noqa: E731
    label = fp.TRANSLATION_FIELD_LABELS[name]
    if name in _TRANSLATION_IMAGES:
        related, keep_attr = _TRANSLATION_IMAGES[name]
        return SyncField(name, label, source, kind='image', keep_version_attr=keep_attr,
                         version=lambda forged, related=related: fp._forge_preview_version(forged, related))
    return SyncField(name, label, source)


class FactionPublisher(Publisher):
    components = (Post.ComponentChoices.FACTION, Post.ComponentChoices.CLOCKWORK)
    keep_model = Faction
    noun = 'Faction'
    detail_url_name = 'forge-faction-detail'
    submit_url_name = 'forge-faction-submit'
    link_url_name = 'forge-faction-link'
    unlink_url_name = 'forge-faction-unlink'
    sync_url_name = 'forge-faction-sync'
    submit_template = 'the_forge/forgedfaction_submit.html'
    tts_forced_fields = fp.TTS_FORCED_FIELDS

    # ── Forge side ──────────────────────────────────────────────────────────
    def refresh(self, forged, for_sync=False):
        from ..views import _ensure_decree_preview, _refresh_all_element_previews
        _refresh_all_element_previews(forged)
        fp.ensure_forge_figures(forged)
        if for_sync:
            # _refresh_all_element_previews skips the decree, which the TTS sync copies.
            sheet = getattr(forged, 'faction_sheet', None)
            if sheet:
                _ensure_decree_preview(sheet)

    def prerequisites(self, forged):
        sheet = fp._safe_related(forged, 'faction_sheet')
        back = fp._safe_related(forged, 'faction_back')
        card = fp._safe_related(forged, 'setup_card')
        reqs = [
            Requirement('Faction Board', sheet is not None),
            Requirement('Faction Board Back', back is not None),
        ]
        if forged.uses_setup_card:   # Clockwork has no Adset Card
            # Type and reach must agree with the Faction model's rule: Insurgent
            # factions have reach <= 6, Militant >= 6.
            warning = None
            if card is not None and card.reach:
                if card.type == 'I' and card.reach > 6:
                    warning = (f'Reach is {card.reach} but Type is Insurgent. '
                               'Lower the reach or switch the type to Militant.')
                elif card.type == 'M' and card.reach < 6:
                    warning = (f'Reach is {card.reach} but Type is Militant. '
                               'Raise the reach or switch the type to Insurgent.')
            reqs.append(Requirement('Adset Card', card is not None, warning))
        reqs.append(Requirement('Faction Icon', bool(forged.faction_icon)))
        return reqs

    def missing(self, forged):
        # The labels the submit message has always used.
        return fp.submit_prerequisites_missing(forged)

    # ── Submit ──────────────────────────────────────────────────────────────
    def submit_form(self, forged, user, data=None, files=None):
        if data is None:
            return fp.ForgedFactionSubmitForm(forged_faction=forged, user=user)
        return fp.ForgedFactionSubmitForm(data, files, forged_faction=forged, user=user)

    def submit_images(self, forged):
        images = {}
        for keep_field, (related_attr, _version_attr) in _POST_IMAGES.items():
            related = fp._safe_related(forged, related_attr)
            if related and related.image_preview:
                images[keep_field] = (related.image_preview, related.preview_version or 0)
        if forged.faction_icon:
            images['small_icon'] = (forged.faction_icon, None)
        return images

    def before_submit_save(self, forged, post, form):
        if forged.art_by and 'kyle ferrin' in forged.art_by.lower():
            post.art_by_kyle_ferrin = True

    def after_submit(self, forged, post, profile):
        forged.icon_synced_name = forged.faction_icon.name if forged.faction_icon else ''
        forged.save(update_fields=['icon_synced_name'])

    def submit_context(self, forged, form):
        from .base import link_context
        return {
            **link_context(),
            'art_by_hint': forged.art_by,
            'picture_options': form._picture_options,
            'sheet': fp._safe_related(forged, 'faction_sheet'),
            'back': fp._safe_related(forged, 'faction_back'),
            'card': fp._safe_related(forged, 'setup_card'),
        }

    # ── Sync ────────────────────────────────────────────────────────────────
    def post_fields(self, forged):
        return [_post_field(name) for name in fp.FACTION_SYNC_FIELDS]

    def translation_fields(self, forged):
        return [_translation_field(name) for name in fp.TRANSLATION_SYNC_FIELDS]

    def extra_plans(self, forged):
        piece_plan = fp.build_piece_plan(forged)
        return {
            'piece_plan': piece_plan,
            'deck_plan': fp.build_deck_plan(forged, piece_plan),
            'tts_asset_plan': fp.build_tts_asset_plan(forged),
        }

    def tts_plan(self, forged, diff_rows, extras):
        return fp.build_tts_plan(forged, diff_rows, extras['piece_plan'], extras['deck_plan'],
                                 extras['tts_asset_plan'])

    def tts_fingerprint(self, forged):
        return fp.forge_tts_fingerprint(forged)

    def build_keep_tts(self, forged, target):
        """The Forge export, with every Forge file swapped for its keep copy."""
        from the_keep.services.tts import SiteURLRequest
        from ..services.tts_convert import build_url_map, convert_save, find_forge_urls
        from ..views import _build_forged_tts_save

        site_request = SiteURLRequest()
        try:
            # refresh_previews=False: the keep copies were taken from these
            # exact previews, so nothing may re-render now.
            save_file, error, _parts = _build_forged_tts_save(site_request, forged, refresh_previews=False)
            if save_file is None:
                return None, error
            # The export just made sure the Forge figures are current: copy them.
            fp.copy_figures(forged)
            url_map = build_url_map(forged, target, site_request)
        except ValueError as exc:  # sprite sheet over the pixel cap
            return None, str(exc)
        converted = convert_save(save_file, url_map)
        leftovers = find_forge_urls(converted)
        if leftovers:
            return None, "these objects still reference Forge files: " + ", ".join(leftovers)
        return converted, None
