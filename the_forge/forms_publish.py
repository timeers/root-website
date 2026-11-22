import hashlib
import html
import json
import os
import re

from django import forms
from django.db.models import Q
from django.utils.html import strip_tags

from .services.upload_paths import copy_image_field

from the_keep.forms import FactionCreateForm
from the_keep.models import (
    Card as KeepCard,
    DeckGroup as KeepDeckGroup,
    Faction,
    Piece as KeepPiece,
    PostTranslation,
    TTSAsset,
)
from the_keep.utils import tts_json_hash

from .models import (
    CharacterImage,
    FactionBack,
    FactionSheet,
    ForgedFaction,
    Piece as ForgePiece,
    SetupCard,
)


FACTION_SYNC_FIELDS = (
    'lore',
    'description',
    'color',
    'version',
    'reach',
    'type',
    'complexity',
    'card_wealth',
    'aggression',
    'crafting_ability',
    'small_icon',
    'board_image',
    'board_2_image',
    'card_image',
)

TRANSLATION_SYNC_FIELDS = (
    'translated_lore',
    'translated_description',
    'translated_board_image',
    'translated_card_image',
    'translated_board_2_image',
    'version',
)

FACTION_FIELD_LABELS = {
    'lore': 'Lore',
    'description': 'Description',
    'color': 'Color',
    'version': 'Version',
    'reach': 'Reach',
    'type': 'Type',
    'complexity': 'Complexity',
    'card_wealth': 'Card Wealth',
    'aggression': 'Aggression',
    'crafting_ability': 'Crafting Ability',
    'small_icon': 'Icon',
    'board_image': 'Faction Board Front',
    'board_2_image': 'Faction Board Back',
    'card_image': 'ADSET Card',
}

TRANSLATION_FIELD_LABELS = {
    'translated_lore': 'Lore (translation)',
    'translated_description': 'Description (translation)',
    'translated_board_image': 'Board Front (translation)',
    'translated_card_image': 'ADSET Card (translation)',
    'translated_board_2_image': 'Board Back (translation)',
    'version': 'Version (translation)',
}

IMAGE_FIELDS = {
    'small_icon', 'board_image', 'board_2_image', 'card_image',
    'translated_board_image', 'translated_card_image', 'translated_board_2_image',
}

ATTRIBUTE_FIELDS = {'complexity', 'card_wealth', 'aggression', 'crafting_ability'}

ATTRIBUTE_BAR_WIDTH = {
    'N': '2%',
    'L': '28%',
    'M': '59%',
    'H': '100%',
}

ATTRIBUTE_DISPLAY = {
    'N': 'None',
    'L': 'Low',
    'M': 'Moderate',
    'H': 'High',
}

FACTION_TYPE_DISPLAY = {
    'M': 'Militant',
    'I': 'Insurgent',
    'C': 'Clockwork',
    'U': '',
}


def _wrap_attribute(value):
    """Return a dict the template can render as a bar."""
    code = value or 'N'
    return {
        'code': code,
        'display': ATTRIBUTE_DISPLAY.get(code, ''),
        'width': ATTRIBUTE_BAR_WIDTH.get(code, '2%'),
        'is_none': code == 'N',
    }


def _wrap_type(value):
    """Return the display name for a Faction.type code; blank for Unknown."""
    return FACTION_TYPE_DISPLAY.get(value or 'U', '')


def _html_to_plain(value):
    """Strip HTML tags and decode entities from a rich-text value. Block-level
    tags become paragraph breaks so the result reads naturally as plain text."""
    if not value:
        return value or ''
    block_break = re.compile(r'</(p|div|li|h[1-6]|tr)>', re.IGNORECASE)
    br_tag = re.compile(r'<br\s*/?>', re.IGNORECASE)
    text = block_break.sub('\n', value)
    text = br_tag.sub('\n', text)
    text = strip_tags(text)
    text = html.unescape(text)
    text = re.sub(r'[ \t]+\n', '\n', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def _proposed_for_faction(forged, field_name):
    """Compute the forge-side value that would be written to a Faction sync field."""
    sheet = _safe_related(forged, 'faction_sheet')
    back = _safe_related(forged, 'faction_back')
    card = _safe_related(forged, 'setup_card')
    if field_name == 'lore':
        return sheet.flavor_text if sheet else None
    if field_name == 'description':
        return _html_to_plain(back.how_to_play_text) if back else None
    if field_name == 'color':
        return forged.color
    if field_name == 'version':
        return forged.pnp_version
    if field_name == 'reach':
        return card.reach if card else None
    if field_name == 'type':
        return card.type if card else None
    if field_name in ('complexity', 'card_wealth', 'aggression', 'crafting_ability'):
        return getattr(back, field_name) if back else None
    if field_name == 'small_icon':
        return forged.faction_icon
    if field_name == 'board_image':
        return sheet.image_preview if sheet else None
    if field_name == 'board_2_image':
        return back.image_preview if back else None
    if field_name == 'card_image':
        return card.image_preview if card else None
    return None


def _proposed_for_translation(forged, field_name):
    """Compute the forge-side value that would be written to a PostTranslation sync field."""
    sheet = _safe_related(forged, 'faction_sheet')
    back = _safe_related(forged, 'faction_back')
    card = _safe_related(forged, 'setup_card')
    if field_name == 'translated_lore':
        return sheet.flavor_text if sheet else None
    if field_name == 'translated_description':
        return _html_to_plain(back.how_to_play_text) if back else None
    if field_name == 'translated_board_image':
        return sheet.image_preview if sheet else None
    if field_name == 'translated_card_image':
        return card.image_preview if card else None
    if field_name == 'translated_board_2_image':
        return back.image_preview if back else None
    if field_name == 'version':
        return forged.pnp_version
    return None


def _safe_related(forged, attr):
    """Return the related forge child or None if absent (RelatedObjectDoesNotExist)."""
    try:
        return getattr(forged, attr)
    except (FactionSheet.DoesNotExist, FactionBack.DoesNotExist, SetupCard.DoesNotExist):
        return None


# Maps each forge-sourced image field to (forge_related_attr, keep_version_attr).
# Used so the sync diff can compare forge preview_version against the keep
# Post/Translation's stored version counter instead of file basenames.
FACTION_IMAGE_VERSION_MAP = {
    'board_image': ('faction_sheet', 'board_image_version'),
    'board_2_image': ('faction_back', 'board_2_image_version'),
    'card_image': ('setup_card', 'card_image_version'),
}

# Same pattern for the PostTranslation side.
TRANSLATION_IMAGE_VERSION_MAP = {
    'translated_board_image': ('faction_sheet', 'translated_board_image_version'),
    'translated_board_2_image': ('faction_back', 'translated_board_2_image_version'),
    'translated_card_image': ('setup_card', 'translated_card_image_version'),
}


def _forge_preview_version(forged, source_attr):
    related = _safe_related(forged, source_attr)
    if related is None:
        return 0
    return getattr(related, 'preview_version', 0) or 0


def _values_equal(current, proposed, is_image):
    if is_image:
        current_name = getattr(current, 'name', '') if current else ''
        proposed_name = getattr(proposed, 'name', '') if proposed else ''
        return current_name == proposed_name
    if current in (None, '') and proposed in (None, ''):
        return True
    return current == proposed


# build_diff and the sync form are generic now (publishing/diff.py); these
# names stay importable from here.
from .publishing.diff import ForgedFactionSyncForm, ForgedPostSyncForm, build_diff  # noqa: E402,F401


PIECE_TYPE_ORDER = ('W', 'B', 'T', 'C', 'O')


def _resolved_piece_name(forge_piece):
    """Forge piece names are optional but keep pieces require one. Fall back
    to the type's human label so unnamed forge pieces are still publishable."""
    name = (forge_piece.name or '').strip()
    if name:
        return name
    return ForgePiece.TypeChoices(forge_piece.type).label


def build_piece_plan(forged):
    """For Faction sync mode, compute per-piece actions: match forge pieces to
    existing keep pieces on ``forged.published_keep_post`` by case-insensitive
    name + identical type, and decide create/update/in_sync per piece. Never
    proposes deletion of keep pieces that have no forge counterpart."""
    if not forged.published_post_id:
        return []
    keep_pieces = list(forged.published_keep_post.pieces.all())
    plan = []
    for forge_piece in forged.pieces.all():
        resolved_name = _resolved_piece_name(forge_piece)
        match = next(
            (
                kp for kp in keep_pieces
                if kp.type == forge_piece.type
                and (kp.name or '').strip().lower() == resolved_name.lower()
            ),
            None,
        )
        if match is None:
            action = 'create'
            quantity_changed = True
            front_changed = bool(forge_piece.small_icon)
            back_changed = bool(forge_piece.back_image)
        else:
            quantity_changed = match.quantity != forge_piece.quantity
            front_changed = (
                bool(forge_piece.small_icon)
                and (forge_piece.front_version or 0) > (match.front_version or 0)
            )
            back_changed = (
                bool(forge_piece.back_image)
                and (forge_piece.back_version or 0) > (match.back_version or 0)
            )
            action = 'update' if (quantity_changed or front_changed or back_changed) else 'in_sync'
        plan.append({
            'forge_piece': forge_piece,
            'keep_piece': match,
            'action': action,
            'resolved_name': resolved_name,
            'type_display': ForgePiece.TypeChoices(forge_piece.type).label,
            'quantity_changed': quantity_changed,
            'front_changed': front_changed,
            'back_changed': back_changed,
            'in_sync': action == 'in_sync',
        })
    plan.sort(key=lambda p: (
        PIECE_TYPE_ORDER.index(p['forge_piece'].type)
        if p['forge_piece'].type in PIECE_TYPE_ORDER else len(PIECE_TYPE_ORDER),
        p['resolved_name'].lower(),
    ))
    return plan


# ── Tabletop Simulator: forge -> keep conversion support ────────────────────
# The keep's tts_json is rebuilt from keep-owned copies of every forge asset the
# TTS export references (see services/tts_convert.py). These plans cover the
# assets build_diff/build_piece_plan don't: decks, and the decree/markers.

# diff rows whose values end up in the TTS JSON. Ticking the TTS row forces
# these (plus every piece/deck/tts-asset row) so the conversion never has to
# reference a forge file. color is here because it drives the boards'
# ColorDiffuse without changing any image.
TTS_FORCED_FIELDS = ('small_icon', 'board_image', 'board_2_image', 'card_image', 'color')


def _md5(text):
    return hashlib.md5(text.encode('utf-8')).hexdigest()


def _file_sig(image_field):
    """Name + mtime, so an overwrite at a pinned path still changes the sig."""
    if not image_field:
        return ''
    try:
        mtime = os.path.getmtime(image_field.path)
    except (OSError, ValueError, NotImplementedError):
        mtime = 0
    return f'{image_field.name}@{mtime}'


def forge_deck_fingerprint(group):
    """Everything about a forge deck group that the keep copy reproduces."""
    parts = [group.name or '', _file_sig(group.back_image)]
    for card in group.ordered_cards:
        parts.append('|'.join([
            card.name or '',
            card.text or '',
            json.dumps(card.tags or [], sort_keys=True),
            _file_sig(card.front_image),
        ]))
    return _md5('\n'.join(parts))


def build_deck_plan(forged, piece_plan):
    """One entry per forge card piece with cards. The keep side is the
    DeckGroup on the matched keep piece in the keep Faction's own language,
    because the keep TTS export only reads decks.filter(language=post.language)."""
    if not forged.published_post_id:
        return []
    faction = forged.published_keep_post
    if not faction.language_id:
        return []
    keep_piece_by_forge_pk = {e['forge_piece'].pk: e['keep_piece'] for e in piece_plan}
    plan = []
    for forge_piece in forged.pieces.filter(type='C').select_related('deck_group').order_by('pk'):
        group = getattr(forge_piece, 'deck_group', None)
        if not (group and group.has_actual_cards):
            continue
        keep_piece = keep_piece_by_forge_pk.get(forge_piece.pk)
        keep_group = None
        if keep_piece is not None:
            keep_group = KeepDeckGroup.objects.filter(
                post=faction, piece=keep_piece, language=faction.language,
            ).first()
        fingerprint = forge_deck_fingerprint(group)
        in_sync = keep_group is not None and keep_group.source_fingerprint == fingerprint
        plan.append({
            'forge_piece': forge_piece,
            'forge_group': group,
            'keep_piece': keep_piece,
            'keep_group': keep_group,
            'fingerprint': fingerprint,
            'card_count': len(group.ordered_cards),
            'keep_card_count': keep_group.cards.count() if keep_group else 0,
            'action': 'create' if keep_group is None else ('in_sync' if in_sync else 'update'),
            'in_sync': in_sync,
        })
    return plan


def apply_deck_entry(entry, faction, keep_piece):
    """Copy one forge deck group into the keep, updating cards in place by
    position so FAQ.reference_cards links on existing cards survive."""
    forge_group = entry['forge_group']
    group = entry['keep_group'] or KeepDeckGroup(
        post=faction, piece=keep_piece, language=faction.language,
    )
    group.name = forge_group.name or keep_piece.name
    if forge_group.back_image:
        group.back_image = copy_image_field(forge_group.back_image)
    group.save()

    forge_cards = list(forge_group.ordered_cards)
    keep_cards = list(group.cards.order_by('order'))
    for index, forge_card in enumerate(forge_cards):
        keep_card = keep_cards[index] if index < len(keep_cards) else KeepCard(group=group)
        keep_card.name = forge_card.name
        keep_card.text = forge_card.text
        keep_card.tags = list(forge_card.tags or [])
        # Card.save() deletes the replaced front image.
        keep_card.front_image = copy_image_field(forge_card.front_image)
        keep_card.save()
    for surplus in keep_cards[len(forge_cards):]:
        surplus.delete()

    # TTSDeckGroup.build only creates CardDecks when none exist, so reconcile
    # here: one per 99 cards, and drop any past the end (their sprite files go
    # with them via the CardDeck post_delete signal).
    needed = (len(forge_cards) - 1) // 99 + 1 if forge_cards else 0
    for stale in group.decks.filter(deck_index__gte=needed):
        stale.delete()
    for deck_number in range(needed):
        group.get_or_create_deck_for_card_index(deck_number * 99)

    group.source_fingerprint = entry['fingerprint']
    group.save(update_fields=['source_fingerprint'])
    return group


def forge_tts_asset_sources(forged):
    """(role, label, forge image, fingerprint) for the TTS images that live in
    keep TTSAssets. The decree only counts while include_decree is on."""
    sources = []
    sheet = _safe_related(forged, 'faction_sheet')
    if sheet and sheet.include_decree and sheet.decree_preview:
        sources.append((TTSAsset.RoleChoices.DECREE, 'Decree', sheet.decree_preview,
                        f'{sheet.decree_fingerprint}:{sheet.decree_preview.name}'))
    markers = (
        (TTSAsset.RoleChoices.VP_MARKER, 'VP Marker', forged.vp_marker),
        (TTSAsset.RoleChoices.RELATIONSHIP_MARKER, 'Relationship Marker', forged.relationship_marker),
    )
    for role, label, image in markers:
        if image:
            sources.append((role, label, image, f'{forged.markers_version}:{_file_sig(image)}'))
    return sources


def build_tts_asset_plan(forged):
    if not forged.published_post_id:
        return []
    keep_assets = {a.role: a for a in forged.published_keep_post.tts_assets.all()}
    plan = []
    for role, label, forge_image, fingerprint in forge_tts_asset_sources(forged):
        keep_asset = keep_assets.get(role)
        in_sync = keep_asset is not None and keep_asset.source_fingerprint == fingerprint
        plan.append({
            'role': role,
            'label': label,
            'forge_image': forge_image,
            'keep_asset': keep_asset,
            'fingerprint': fingerprint,
            'in_sync': in_sync,
        })
    return plan


def apply_tts_asset_entry(entry, faction):
    asset = entry['keep_asset'] or TTSAsset(post=faction, role=entry['role'])
    asset.image = copy_image_field(entry['forge_image'])
    asset.source_fingerprint = entry['fingerprint']
    asset.save()
    return asset


def forge_tts_fingerprint(forged):
    """Hash of every forge input to the TTS export. Unlike the per-row plans it
    also changes on REMOVALS (a deleted piece/deck/marker, the decree turned
    off), which build_piece_plan never proposes and so would never flag."""
    sheet = _safe_related(forged, 'faction_sheet')
    back = _safe_related(forged, 'faction_back')
    card = _safe_related(forged, 'setup_card')
    decks = []
    pieces = []
    for piece in forged.pieces.all().select_related('deck_group').order_by('pk'):
        pieces.append([piece.pk, piece.type, piece.name or '', piece.quantity,
                       piece.front_version or 0, piece.back_version or 0])
        group = getattr(piece, 'deck_group', None)
        if piece.type == 'C' and group and group.has_actual_cards:
            decks.append([piece.pk, forge_deck_fingerprint(group)])
    inputs = {
        'sheet': [sheet.preview_version, sheet.include_decree, sheet.decree_fingerprint,
                  sheet.snap_points] if sheet else None,
        'back': back.preview_version if back else None,
        'card': card.preview_version if card else None,
        'color': forged.color,
        'name': forged.faction_name,
        'icon': forged.faction_icon.name if forged.faction_icon else '',
        'markers': [forged.markers_version,
                    forged.vp_marker.name if forged.vp_marker else '',
                    forged.relationship_marker.name if forged.relationship_marker else ''],
        'pieces': pieces,
        'decks': decks,
    }
    return _md5(json.dumps(inputs, sort_keys=True, default=str))


def build_tts_plan(forged, diff_rows, piece_plan, deck_plan, asset_plan):
    """The TTS row. In sync only when the keep JSON is exactly what the last
    sync wrote, the forge inputs haven't changed since, and every forced row is
    up to date."""
    if not forged.published_post_id:
        return None
    if not any(_safe_related(forged, attr) for attr in ('faction_sheet', 'faction_back', 'setup_card')):
        return None
    faction = forged.published_keep_post
    stale_fields = [row[1] for row in diff_rows if row[0] in TTS_FORCED_FIELDS and not row[5]]
    stale_count = (
        len(stale_fields)
        + sum(1 for e in piece_plan if not e['in_sync'])
        + sum(1 for e in deck_plan if not e['in_sync'])
        + sum(1 for e in asset_plan if not e['in_sync'])
    )
    keep_json = faction.tts_json
    json_matches = bool(keep_json) and tts_json_hash(keep_json) == forged.tts_synced_hash
    source_matches = forge_tts_fingerprint(forged) == forged.tts_source_fingerprint
    return {
        'in_sync': json_matches and source_matches and stale_count == 0,
        'keep_object_count': len(keep_json.get('ObjectStates', [])) if isinstance(keep_json, dict) else 0,
        # Keep JSON exists but isn't what the forge sync last wrote (pasted in the admin).
        'keep_replaced': bool(keep_json) and not json_matches,
        'forge_changed': not source_matches,
        'stale_count': stale_count,
    }


def submit_prerequisites_missing(forged):
    """List human-readable forge pieces that must exist before submit is allowed.

    Previews are generated automatically when the user enters the submit or
    sync view, so the check is purely about structural pieces existing on the
    forge draft."""
    missing = []
    sheet = _safe_related(forged, 'faction_sheet')
    back = _safe_related(forged, 'faction_back')
    card = _safe_related(forged, 'setup_card')
    if sheet is None:
        missing.append('Faction Sheet')
    if back is None:
        missing.append('Faction Back')
    if card is None and forged.uses_setup_card:  # Clockwork has no Adset Card
        missing.append('Setup Card')
    if not forged.faction_icon:
        missing.append('Faction Icon')
    return missing


def _picture_choices(forged):
    """Build the (value, label, file, thumbnail_url) tuples for the picture-source radio."""
    options = []
    sheet = _safe_related(forged, 'faction_sheet')
    if sheet is not None:
        for ci in sheet.character_images.all():
            if ci.image:
                options.append((
                    f'character_image:{ci.pk}',
                    f'Character image #{ci.order + 1}',
                    ci.image,
                ))
    back = _safe_related(forged, 'faction_back')
    if back is not None and back.back_image:
        options.append(('faction_back', 'Faction Back image', back.back_image))
    return options


class ForgedFactionSubmitForm(FactionCreateForm):
    """Submit a ForgedFaction as a brand new Faction in the_keep.

    Most fields are populated from the ForgedFaction / FactionSheet /
    FactionBack / SetupCard and shown read-only in the template. The user
    only edits color_group, animal, picture_source (+ optional upload), and
    ww_link. Other thread links can be revealed via the "Add more links"
    toggle on the template but stay optional.
    """

    PICTURE_UPLOAD = 'upload'
    PICTURE_NONE = 'none'

    # The user-editable fields on this form. Everything else is initialised
    # from the forge draft, disabled, and saved through silently.
    EDITABLE_FIELDS = {
        'color_group', 'animal', 'picture_source', 'picture', 'ww_link',
        'bgg_link', 'tts_link', 'wr_link', 'fr_link', 'pnp_link', 'stl_link',
    }
    OPTIONAL_LINK_FIELDS = (
        'bgg_link', 'tts_link', 'wr_link', 'fr_link', 'pnp_link', 'stl_link',
    )

    picture_source = forms.ChoiceField(
        required=True,
        widget=forms.RadioSelect,
        label='Character Art',
        help_text='Pick a character image for your Faction.',
    )

    def __init__(self, *args, forged_faction=None, **kwargs):
        if forged_faction is None:
            raise ValueError('ForgedFactionSubmitForm requires forged_faction')
        self.forged = forged_faction

        initial = kwargs.setdefault('initial', {})
        initial.setdefault('title', forged_faction.faction_name)
        initial.setdefault('color', forged_faction.color)
        initial.setdefault('language', forged_faction.language_id)
        initial.setdefault('version', forged_faction.pnp_version)

        sheet = _safe_related(forged_faction, 'faction_sheet')
        back = _safe_related(forged_faction, 'faction_back')
        card = _safe_related(forged_faction, 'setup_card')

        if sheet is not None:
            initial.setdefault('lore', sheet.flavor_text)
        if back is not None:
            initial.setdefault('description', _html_to_plain(back.how_to_play_text))
            initial.setdefault('complexity', back.complexity)
            initial.setdefault('card_wealth', back.card_wealth)
            initial.setdefault('aggression', back.aggression)
            initial.setdefault('crafting_ability', back.crafting_ability)
        if card is not None:
            initial.setdefault('reach', card.reach)
            initial.setdefault('type', card.type)

        super().__init__(*args, **kwargs)

        # Clockwork drafts have no Adset Card, so no reach or Militant/Insurgent
        # type. Mirror the keep's ClockworkCreateForm: drop the fields and set
        # type 'C' on the instance (FactionCreateForm's type choices don't
        # include it). Faction.save() then sets component='Clockwork'.
        if forged_faction.is_clockwork:
            self.fields.pop('type', None)
            self.fields.pop('reach', None)
            self.instance.type = 'C'
            self.instance.reach = 0

        # PostCreateForm.__init__ overrides language.initial from user profile; force it back to the forge's language.
        if 'language' in self.fields and forged_faction.language_id:
            self.fields['language'].initial = forged_faction.language_id

        self._picture_options = _picture_choices(forged_faction)
        choices = [(value, label) for value, label, _file in self._picture_options]
        choices.append((self.PICTURE_UPLOAD, 'Upload my own'))
        choices.append((self.PICTURE_NONE, 'No character art'))
        self.fields['picture_source'].choices = choices
        # Default to the first forge image if any, else upload.
        if self._picture_options:
            self.fields['picture_source'].initial = self._picture_options[0][0]
        else:
            self.fields['picture_source'].initial = self.PICTURE_UPLOAD

        # Drop fields the submit page doesn't expose at all. The published
        # Faction can be edited normally after approval to fill these in.
        for fname in (
            'co_designers', 'co_designers_can_edit', 'artist',
            'art_by_kyle_ferrin', 'designer', 'based_on', 'expansion',
            'official', 'in_root_digital', 'status', 'rootjam_link',
            'leder_games_link',
        ):
            self.fields.pop(fname, None)

        # The view supplies board/card images from the forge previews —
        # drop the image inputs entirely so they're not required.
        for fname in ('board_image', 'board_2_image', 'card_image', 'small_icon'):
            self.fields.pop(fname, None)

        # picture is filled from picture_source; the bare upload field stays
        # available for the "upload my own" branch.
        if 'picture' in self.fields:
            self.fields['picture'].required = False

        # ww_link is the only required link on this form. The "add more"
        # links stay optional and start hidden in the template.
        if 'ww_link' in self.fields:
            self.fields['ww_link'].required = True
        for fname in self.OPTIONAL_LINK_FIELDS:
            if fname in self.fields and fname != 'ww_link':
                self.fields[fname].required = False

        # Disable every field that isn't user-editable on this page. The
        # values still POST back (Django sends disabled fields' initial),
        # and the view overwrites them from the forge draft anyway.
        for fname, field in self.fields.items():
            if fname not in self.EDITABLE_FIELDS:
                field.disabled = True

    def clean_reach_and_type(self, cleaned_data):
        # reach/type come from the forge draft and aren't editable on this
        # page, so a parent-form consistency error here would be unfixable
        # by the user. The draft editor is where this gets resolved.
        return

    def clean_title_uniqueness(self, cleaned_data):
        # Title is locked to the forge draft's name on this page. Uniqueness
        # is still important — but if there's a clash we surface it as a
        # non-field error pointing back at the draft, not a field error on
        # a disabled control.
        from .publishing import publisher_for
        title = cleaned_data.get('title')
        if not title:
            return
        instance_id = self.instance.id if self.instance else None
        if publisher_for(self.forged).title_taken(title, exclude_pk=instance_id):
            self.add_error(
                None,
                f'A Faction named "{title}" already exists. Rename your forge draft '
                'before submitting.',
            )

    def clean(self):
        cleaned = super().clean()
        source = cleaned.get('picture_source')
        if source == self.PICTURE_UPLOAD:
            if not cleaned.get('picture'):
                self.add_error('picture', 'Upload an image or pick a different source.')
        elif source == self.PICTURE_NONE:
            cleaned['picture'] = None
        elif source == 'faction_back':
            back = _safe_related(self.forged, 'faction_back')
            if back and back.back_image:
                cleaned['picture'] = _copy_image_field(back.back_image)
            else:
                self.add_error('picture_source', 'Selected source no longer has an image.')
        elif source and source.startswith('character_image:'):
            try:
                pk = int(source.split(':', 1)[1])
            except (ValueError, IndexError):
                self.add_error('picture_source', 'Invalid selection.')
            else:
                ci = CharacterImage.objects.filter(
                    pk=pk, sheet__faction=self.forged,
                ).first()
                if ci is None or not ci.image:
                    self.add_error('picture_source', 'Selected character image not found.')
                else:
                    cleaned['picture'] = _copy_image_field(ci.image)
        return cleaned


# Re-homed to services.upload_paths so the clone service can reuse it without a
# forms->service import cycle. Kept aliased here for the existing call sites.
_copy_image_field = copy_image_field


class ForgedPostLinkForm(forms.Form):
    """Choose an existing keep post or translation of the draft's kind to link
    to, or start a new translation of one in another language. The candidates
    come from the draft's Publisher (publishing/base.py)."""

    target = forms.ChoiceField(widget=forms.RadioSelect)
    show_all_factions = forms.BooleanField(required=False, label='Show all my posts')

    PREFIX_POST = 'post'
    PREFIX_TRANSLATION = 'translation'
    PREFIX_NEW_TRANSLATION = 'new_translation'
    PREFIX_FACTION = PREFIX_POST   # the old name

    def __init__(self, *args, user=None, forged=None, show_all=False, **kwargs):
        if user is None or forged is None:
            raise ValueError('ForgedPostLinkForm requires user and forged kwargs')
        from .publishing import publisher_for
        self.user = user
        self.forged = forged
        self.publisher = publisher_for(forged)
        self.resolved = None
        self.resolved_mode = None
        # Populated alongside the radio choices so the template can render
        # each candidate's picture / icon next to the option.
        self.target_options = []

        super().__init__(*args, **kwargs)
        self.fields['show_all_factions'].label = f'Show all my {self.publisher.noun}s'

        # `show_all` is normally driven by the GET query string (the checkbox
        # auto-submits a GET to refresh the list). On POST, we still honour it
        # from form data so a re-render after a failed submit keeps the list
        # expanded.
        if self.is_bound:
            show_all = show_all or self.data.get('show_all_factions') in (
                'on', 'true', '1', True,
            )
        self.show_all = show_all
        choices, options = self._build_choices(show_all=show_all)
        self.fields['target'].choices = choices
        self.target_options = options
        # Keep the checkbox visually in sync with whichever source set show_all.
        self.fields['show_all_factions'].initial = show_all

    def _querysets(self, show_all):
        return self.publisher.link_querysets(self.forged, self.user.profile, show_all=show_all)

    def _build_choices(self, show_all=False):
        forged = self.forged
        noun = self.publisher.noun
        posts, translations, cross_language = self._querysets(show_all)

        def _img_url(field):
            return field.url if field and field.name else ''

        choices = []
        options = []
        for post in posts:
            value = f'{self.PREFIX_POST}:{post.pk}'
            label = f'{post.title} ({noun})'
            choices.append((value, label))
            options.append({
                'value': value,
                'label': label,
                'title': post.title,
                'subtitle': noun,
                'picture_url': _img_url(post.picture),
                'icon_url': _img_url(post.small_icon),
                'kind': self.PREFIX_POST,
            })
        for t in translations:
            lang_name = t.language.name if t.language else '?'
            value = f'{self.PREFIX_TRANSLATION}:{t.pk}'
            label = f'{t.translated_title} ({lang_name} translation of {t.post.title})'
            choices.append((value, label))
            options.append({
                'value': value,
                'label': label,
                'title': t.translated_title or t.post.title,
                'subtitle': f'{lang_name} translation of {t.post.title}',
                'picture_url': _img_url(t.post.picture),
                'icon_url': _img_url(t.post.small_icon),
                'kind': self.PREFIX_TRANSLATION,
            })
        forged_lang_name = forged.language.name if forged.language else '?'
        for post in cross_language:
            value = f'{self.PREFIX_NEW_TRANSLATION}:{post.pk}'
            label = f'Publish {forged_lang_name} translation of {post.title}'
            choices.append((value, label))
            options.append({
                'value': value,
                'label': label,
                'title': post.title,
                'subtitle': f'Start a new {forged_lang_name} translation',
                'picture_url': _img_url(post.picture),
                'icon_url': _img_url(post.small_icon),
                'kind': self.PREFIX_NEW_TRANSLATION,
            })
        return choices, options

    def clean_target(self):
        value = self.cleaned_data['target']
        prefix, _, pk_str = value.partition(':')
        try:
            pk = int(pk_str)
        except ValueError:
            raise forms.ValidationError('Invalid selection.')
        # Re-checked against the same rules (all of the designer's posts, not
        # just the name matches), so a stale page can't link anything else.
        posts, translations, cross_language = self._querysets(show_all=True)
        if prefix == self.PREFIX_POST:
            obj = posts.filter(pk=pk).first()
        elif prefix == self.PREFIX_TRANSLATION:
            obj = PostTranslation.objects.filter(
                pk=pk, post__designer=self.user.profile, post__component=self.forged.component,
                source_forged_faction__isnull=True, language=self.forged.language,
            ).first()
        elif prefix == self.PREFIX_NEW_TRANSLATION:
            obj = cross_language.filter(pk=pk).first()
        else:
            raise forms.ValidationError('Unknown selection type.')
        if obj is None:
            raise forms.ValidationError('Selection is no longer available.')
        self.resolved = obj
        self.resolved_mode = prefix
        return value


# The name the Faction code and tests have always used.
ForgedFactionLinkForm = ForgedPostLinkForm


def apply_extra_plans(form, target):
    """Apply the ticked piece, deck and TTS-asset rows of a sync form (Faction
    only: other components have no such rows)."""
    cleaned = form.cleaned_data
    for entry in form.piece_plan:
        if entry['in_sync'] or not cleaned.get(f'piece_{entry["forge_piece"].pk}'):
            continue
        forge_piece = entry['forge_piece']
        if entry['action'] == 'create':
            keep_piece = KeepPiece(
                parent=target,
                name=entry['resolved_name'],
                type=forge_piece.type,
                quantity=forge_piece.quantity,
                description='',
                suited=False,
            )
            if forge_piece.small_icon:
                keep_piece.small_icon = _copy_image_field(forge_piece.small_icon)
                keep_piece.front_version = forge_piece.front_version or 0
            if forge_piece.back_image:
                keep_piece.back_image = _copy_image_field(forge_piece.back_image)
                keep_piece.back_version = forge_piece.back_version or 0
            keep_piece.save()
        else:  # update
            keep_piece = entry['keep_piece']
            keep_piece.quantity = forge_piece.quantity
            if entry['front_changed'] and forge_piece.small_icon:
                keep_piece.small_icon = _copy_image_field(forge_piece.small_icon)
                keep_piece.front_version = forge_piece.front_version or 0
            if entry['back_changed'] and forge_piece.back_image:
                keep_piece.back_image = _copy_image_field(forge_piece.back_image)
                keep_piece.back_version = forge_piece.back_version or 0
            keep_piece.save()
        # build_deck_plan resolved keep pieces before this save; record the
        # created/updated one so the deck pass below can attach to it.
        entry['keep_piece'] = keep_piece

    keep_piece_by_forge_pk = {e['forge_piece'].pk: e['keep_piece'] for e in form.piece_plan}
    for entry in form.deck_plan:
        pk = entry['forge_piece'].pk
        if entry['in_sync'] or not cleaned.get(f'deck_{pk}'):
            continue
        keep_piece = keep_piece_by_forge_pk.get(pk)
        if keep_piece is None or keep_piece.pk is None:
            continue
        apply_deck_entry(entry, target, keep_piece)

    for entry in form.tts_asset_plan:
        if entry['in_sync'] or not cleaned.get(f'ttsasset_{entry["role"]}'):
            continue
        apply_tts_asset_entry(entry, target)
