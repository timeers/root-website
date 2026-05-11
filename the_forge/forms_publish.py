import html
import re

from django import forms
from django.core.files.base import ContentFile
from django.db.models import Q
from django.utils.html import strip_tags

from the_keep.forms import FactionCreateForm
from the_keep.models import Faction, Piece as KeepPiece, PostTranslation

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


def build_diff(forged):
    """Return a list of (field_name, label, current, proposed, is_image, in_sync)
    tuples for whichever sync mode applies. Empty-source rows are omitted; rows
    whose values already match are flagged ``in_sync=True`` so the template can
    show them as "Up to date" without an Apply checkbox."""
    rows = []
    if forged.published_faction_id:
        faction = forged.published_faction
        for field in FACTION_SYNC_FIELDS:
            proposed = _proposed_for_faction(forged, field)
            if proposed in (None, ''):
                continue
            current = getattr(faction, field, None)
            is_image = field in IMAGE_FIELDS
            if field in FACTION_IMAGE_VERSION_MAP:
                source_attr, keep_version_attr = FACTION_IMAGE_VERSION_MAP[field]
                forge_version = _forge_preview_version(forged, source_attr)
                keep_version = getattr(faction, keep_version_attr, 0) or 0
                in_sync = forge_version <= keep_version
            else:
                in_sync = _values_equal(current, proposed, is_image)
            if field in ATTRIBUTE_FIELDS:
                current_display = _wrap_attribute(current)
                proposed_display = _wrap_attribute(proposed)
            elif field == 'type':
                current_display = _wrap_type(current)
                proposed_display = _wrap_type(proposed)
            else:
                current_display = current
                proposed_display = proposed
            rows.append((
                field, FACTION_FIELD_LABELS[field],
                current_display, proposed_display, is_image, in_sync,
            ))
    elif forged.published_translation_id:
        translation = forged.published_translation
        for field in TRANSLATION_SYNC_FIELDS:
            proposed = _proposed_for_translation(forged, field)
            if not proposed:
                continue
            current = getattr(translation, field, None)
            is_image = field in IMAGE_FIELDS
            in_sync = _values_equal(current, proposed, is_image)
            rows.append((
                field, TRANSLATION_FIELD_LABELS[field],
                current, proposed, is_image, in_sync,
            ))
    return rows


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
    existing keep pieces on ``forged.published_faction`` by case-insensitive
    name + identical type, and decide create/update/in_sync per piece. Never
    proposes deletion of keep pieces that have no forge counterpart."""
    if not forged.published_faction_id:
        return []
    back = _safe_related(forged, 'faction_back')
    if back is None:
        return []
    keep_pieces = list(forged.published_faction.pieces.all())
    plan = []
    for forge_piece in back.pieces.all():
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
    if card is None:
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
    """Submit a ForgedFaction as a brand new Faction in the_keep."""

    PICTURE_UPLOAD = 'upload'
    PICTURE_NONE = 'none'

    picture_source = forms.ChoiceField(
        required=True,
        widget=forms.RadioSelect,
        label='Character Art',
        help_text='Pick a character image from your Forge draft, upload a new one, or skip.',
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

        # The view supplies board images from the forge previews — make the
        # ImageField inputs themselves optional on this subclass.
        if 'board_image' in self.fields:
            self.fields['board_image'].required = False
        if 'board_2_image' in self.fields:
            self.fields['board_2_image'].required = False
        # picture is filled from picture_source; the bare upload field stays
        # available for the "upload my own" branch.
        if 'picture' in self.fields:
            self.fields['picture'].required = False

        # Disable identity fields the user shouldn't change at submit time.
        for fname in ('title', 'color', 'language', 'version'):
            if fname in self.fields:
                self.fields[fname].disabled = True

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


def _copy_image_field(image_field):
    """Return a Django File the form can save as a fresh upload, copied from
    an existing ImageField. Reading the file leaves the original untouched and
    avoids both fields pointing at the same storage path."""
    image_field.open('rb')
    try:
        data = image_field.read()
    finally:
        image_field.close()
    name = image_field.name.rsplit('/', 1)[-1]
    return ContentFile(data, name=name)


class ForgedFactionLinkForm(forms.Form):
    """Choose an existing Faction or PostTranslation to link to, or start a
    new translation of a cross-language Faction match."""

    target = forms.ChoiceField(widget=forms.RadioSelect)
    show_all_factions = forms.BooleanField(required=False, label='Show all my Factions')

    PREFIX_FACTION = 'faction'
    PREFIX_TRANSLATION = 'translation'
    PREFIX_NEW_TRANSLATION = 'new_translation'

    def __init__(self, *args, user=None, forged=None, **kwargs):
        if user is None or forged is None:
            raise ValueError('ForgedFactionLinkForm requires user and forged kwargs')
        self.user = user
        self.forged = forged
        self.resolved = None
        self.resolved_mode = None

        super().__init__(*args, **kwargs)

        show_all = self._raw('show_all_factions') in ('on', 'true', '1', True)
        self.fields['target'].choices = self._build_choices(show_all=show_all)

    def _raw(self, name):
        if self.is_bound:
            return self.data.get(name)
        return self.initial.get(name)

    def _build_choices(self, show_all=False):
        profile = self.user.profile
        forged = self.forged

        faction_qs = Faction.objects.filter(
            designer=profile,
            source_forged_faction__isnull=True,
            language=forged.language,
        )
        if not show_all:
            faction_qs = faction_qs.filter(title__iexact=forged.faction_name)

        translation_qs = PostTranslation.objects.filter(
            post__designer=profile,
            source_forged_faction__isnull=True,
            translated_title__iexact=forged.faction_name,
            language=forged.language,
            post__component__in=['Faction', 'Clockwork'],
        ).select_related('post', 'language')

        cross_lang_factions = Faction.objects.filter(
            designer=profile,
            title__iexact=forged.faction_name,
        ).exclude(
            language=forged.language,
        ).exclude(
            translations__language=forged.language,
        )

        choices = []
        for f in faction_qs:
            choices.append((f'{self.PREFIX_FACTION}:{f.pk}', f'{f.title} (Faction)'))
        for t in translation_qs:
            lang_name = t.language.name if t.language else '?'
            choices.append((
                f'{self.PREFIX_TRANSLATION}:{t.pk}',
                f'{t.translated_title} ({lang_name} translation of {t.post.title})',
            ))
        forged_lang_name = forged.language.name if forged.language else '?'
        for f in cross_lang_factions:
            choices.append((
                f'{self.PREFIX_NEW_TRANSLATION}:{f.pk}',
                f'Publish {forged_lang_name} translation of {f.title}',
            ))
        return choices

    def clean_target(self):
        value = self.cleaned_data['target']
        prefix, _, pk_str = value.partition(':')
        try:
            pk = int(pk_str)
        except ValueError:
            raise forms.ValidationError('Invalid selection.')
        profile = self.user.profile
        if prefix == self.PREFIX_FACTION:
            obj = Faction.objects.filter(
                pk=pk, designer=profile,
                source_forged_faction__isnull=True,
                language=self.forged.language,
            ).first()
        elif prefix == self.PREFIX_TRANSLATION:
            obj = PostTranslation.objects.filter(
                pk=pk, post__designer=profile,
                source_forged_faction__isnull=True,
                language=self.forged.language,
            ).first()
        elif prefix == self.PREFIX_NEW_TRANSLATION:
            obj = Faction.objects.filter(
                pk=pk, designer=profile,
            ).exclude(language=self.forged.language).exclude(
                translations__language=self.forged.language,
            ).first()
        else:
            raise forms.ValidationError('Unknown selection type.')
        if obj is None:
            raise forms.ValidationError('Selection is no longer available.')
        self.resolved = obj
        self.resolved_mode = prefix
        return value


class ForgedFactionSyncForm(forms.Form):
    """Per-field opt-in sync of forge values into the linked Faction or
    PostTranslation. Constructor builds the diff rows; each row becomes a
    BooleanField named ``sync_<field>``."""

    def __init__(self, *args, forged=None, **kwargs):
        if forged is None:
            raise ValueError('ForgedFactionSyncForm requires forged kwarg')
        self.forged = forged
        self.diff_rows = build_diff(forged)
        self.piece_plan = build_piece_plan(forged)
        super().__init__(*args, **kwargs)
        for field_name, label, _current, _proposed, _is_image, in_sync in self.diff_rows:
            if in_sync:
                continue
            self.fields[f'sync_{field_name}'] = forms.BooleanField(
                required=False, initial=True, label=label,
            )
        for entry in self.piece_plan:
            if entry['in_sync']:
                continue
            self.fields[f'piece_{entry["forge_piece"].pk}'] = forms.BooleanField(
                required=False, initial=True,
                label=f"{entry['type_display']}: {entry['resolved_name']}",
            )

    @property
    def mode(self):
        if self.forged.published_faction_id:
            return 'faction'
        if self.forged.published_translation_id:
            return 'translation'
        return None

    def save(self):
        if not self.is_valid():
            raise ValueError('Form must be valid before save()')
        if self.mode == 'faction':
            target = self.forged.published_faction
            proposed_fn = _proposed_for_faction
        elif self.mode == 'translation':
            target = self.forged.published_translation
            proposed_fn = _proposed_for_translation
        else:
            return None

        changed_fields = []
        for field_name, _label, _current, _proposed, is_image, in_sync in self.diff_rows:
            if in_sync:
                continue
            if not self.cleaned_data.get(f'sync_{field_name}'):
                continue
            proposed = proposed_fn(self.forged, field_name)
            if is_image:
                setattr(target, field_name, _copy_image_field(proposed))
                if self.mode == 'faction' and field_name in FACTION_IMAGE_VERSION_MAP:
                    source_attr, keep_version_attr = FACTION_IMAGE_VERSION_MAP[field_name]
                    setattr(target, keep_version_attr,
                            _forge_preview_version(self.forged, source_attr))
            else:
                setattr(target, field_name, proposed)
            changed_fields.append(field_name)

        if changed_fields:
            target.save()

        if self.mode == 'faction':
            for entry in self.piece_plan:
                if entry['in_sync']:
                    continue
                if not self.cleaned_data.get(f'piece_{entry["forge_piece"].pk}'):
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

        return target
