from django import forms
from django.core.validators import MinValueValidator, MaxValueValidator

from .limits import (
    MAX_ABILITY_BODY,
    MAX_BORDERED_BOX_BODY,
    MAX_CARD_PILE_BODY,
    MAX_CONTENT_TEXT,
    MAX_HOW_TO_PLAY_TEXT,
    MAX_HOW_TO_PLAY_TITLE,
    MAX_LEGEND_BODY,
    MAX_LEGEND_ROW_BODY,
    MAX_PHASE_STEP_TEXT,
    MAX_PIECE_QUANTITY,
    MAX_SETUP_STEP_TEXT,
    MAX_STEP_ACTION_TEXT,
    MAX_TRACK_BODY,
    MAX_TRACK_COLS,
    MAX_TRACK_ROWS,
)
from .models import (
    BorderedBox,
    CardboardSlot,
    CardboardTrack,
    CardPile,
    CardSlot,
    CharacterImage,
    ContentBox,
    CustomInlineImage,
    DecreeSection,
    FactionAbility,
    FactionBack,
    FactionSheet,
    ForgedFaction,
    ForgedDeckGroup,
    ForgedCard,
    Legend,
    LegendRow,
    PhaseStep,
    Piece,
    Scale,
    ScaleRow,
    SetupCard,
    SetupStep,
    StepAction,
)


class RichTextarea(forms.Textarea):
    """Textarea that opts a field into the forge rich-text toolbar.

    The template-side code looks for `.forge-rich-text` wrappers, so we tag the
    textarea with that class; the actual toolbar is rendered as a sibling via the
    `the_forge/partials/richtext_toolbar.html` include in the template.
    """

    def __init__(self, attrs=None):
        base = {'class': 'forge-rich-text-input', 'rows': 4}
        if attrs:
            base.update(attrs)
        super().__init__(base)


class ForgedFactionForm(forms.ModelForm):
    BG_MODE_NONE = 'none'
    BG_MODE_CUSTOM = 'custom'
    BG_MODE_PRESET = 'preset'

    background_tile_size = forms.IntegerField(
        min_value=ForgedFaction.BACKGROUND_TILE_SIZE_MIN,
        max_value=ForgedFaction.BACKGROUND_TILE_SIZE_MAX,
        initial=ForgedFaction.BACKGROUND_TILE_SIZE_DEFAULT,
        widget=forms.NumberInput(attrs={
            'type': 'range',
            'min': ForgedFaction.BACKGROUND_TILE_SIZE_MIN,
            'max': ForgedFaction.BACKGROUND_TILE_SIZE_MAX,
            'step': 1,
            'class': 'form-range',
        }),
    )

    class Meta:
        model = ForgedFaction
        fields = [
            'faction_name',
            'language',
            'color',
            'secondary_color',
            'background_preset',
            'background_image',
            'repeat_background_image',
            'background_tile_size',
        ]
        widgets = {
            'faction_name': forms.TextInput(attrs={
                'class': 'form-control form-control-lg forge-faction-title-input',
                'placeholder': 'Faction Name',
            }),
            'color': forms.TextInput(attrs={
                'type': 'color',
                'class': 'form-control form-control-color forge-color-swatch',
            }),
            'secondary_color': forms.TextInput(attrs={
                'type': 'color',
                'class': 'form-control form-control-color forge-color-swatch forge-secondary-color-swatch',
            }),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Whitelist language choices to codes that have a populated PDF_TEXT
        # entry — the dropdown should only offer languages the PDF engine can
        # actually render. Imports are deferred to avoid pulling another app's
        # models at module-load time.
        from the_forge.pdf_engine import PDF_TEXT
        from the_gatehouse.models import Language
        self.fields['language'].queryset = Language.objects.filter(code__in=PDF_TEXT.keys())
        self.fields['language'].widget.attrs.setdefault('class', 'form-select')
        self.fields['language'].required = True
        self.fields['language'].empty_label = None
        # If this ForgedFaction is already published/linked to a Faction or
        # PostTranslation, lock the name and language so they don't drift
        # away from the linked source.
        if self.instance and self.instance.pk and self.instance.is_published_linked:
            for fname in ('faction_name', 'language'):
                self.fields[fname].disabled = True
                self.fields[fname].help_text = (
                    "This field is locked and can no longer be changed."
                )

    def clean_background_tile_size(self):
        raw = self.cleaned_data.get('background_tile_size')
        if raw is None:
            return ForgedFaction.BACKGROUND_TILE_SIZE_DEFAULT
        return max(
            ForgedFaction.BACKGROUND_TILE_SIZE_MIN,
            min(ForgedFaction.BACKGROUND_TILE_SIZE_MAX, int(raw)),
        )

    def clean(self):
        cleaned = super().clean()
        mode = (self.data.get('background_mode') or '').strip()
        if mode == self.BG_MODE_NONE:
            cleaned['background_preset'] = ''
            cleaned['background_image'] = False
            cleaned['repeat_background_image'] = False
        elif mode == self.BG_MODE_CUSTOM:
            cleaned['background_preset'] = ''
        elif mode == self.BG_MODE_PRESET:
            cleaned['background_image'] = False
            cleaned['repeat_background_image'] = False
        return cleaned


class FactionMarkersForm(forms.ModelForm):
    """Edit a faction's icon plus VP/Relationship marker color. Markers
    themselves (the composite PNGs) are generated client-side and posted
    alongside this form's data — see the view."""

    use_faction_color = forms.BooleanField(
        required=False,
        label="Use faction color",
        help_text="Use the faction's primary color for marker backgrounds.",
    )

    class Meta:
        model = ForgedFaction
        fields = ['faction_icon', 'icon_color']
        widgets = {
            'icon_color': forms.TextInput(attrs={
                'type': 'color',
                'class': 'form-control form-control-color forge-color-swatch',
            }),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Toggle is on whenever no icon_color override is set.
        if self.instance and self.instance.pk and not self.is_bound:
            self.fields['use_faction_color'].initial = not bool(self.instance.icon_color)

    def clean(self):
        cleaned = super().clean()
        if cleaned.get('use_faction_color'):
            cleaned['icon_color'] = None
        # Require a faction icon: either a freshly uploaded file in this
        # submission, or one already persisted on the instance.
        uploaded_icon = cleaned.get('faction_icon')
        existing_icon = self.instance.faction_icon if self.instance else None
        if not uploaded_icon and not existing_icon:
            self.add_error('faction_icon', 'A faction icon is required.')
        return cleaned


class FactionBackForm(forms.Form):
    """Edits all simple FactionBack fields. Pieces and setup steps are
    reconciled separately by the view from parallel POST arrays."""

    complexity = forms.ChoiceField(choices=FactionBack.AttributeChoices.choices)
    card_wealth = forms.ChoiceField(choices=FactionBack.AttributeChoices.choices)
    aggression = forms.ChoiceField(choices=FactionBack.AttributeChoices.choices)
    crafting_ability = forms.ChoiceField(choices=FactionBack.AttributeChoices.choices)
    setup_order = forms.CharField(
        label='Setup',
        max_length=1,
        required=False,
        widget=forms.TextInput(attrs={
            'class': 'form-control form-control-lg',
            'maxlength': '1',
            'style': 'width: 3rem;',
        }),
    )
    how_to_play_title = forms.CharField(
        max_length=MAX_HOW_TO_PLAY_TITLE,
        widget=forms.TextInput(attrs={'placeholder': 'Faction', 'class': 'form-control form-control-lg'}),
    )
    how_to_play_text = forms.CharField(
        required=False,
        max_length=MAX_HOW_TO_PLAY_TEXT,
        widget=RichTextarea(attrs={'rows': 8}),
    )
    back_image = forms.ImageField(
        label='Character Image',
        required=False,
        widget=forms.ClearableFileInput(attrs={
            'class': 'form-control form-control-sm',
            'accept': 'image/*',
        }),
    )
    back_image_size = forms.IntegerField(
        required=False,
        min_value=FactionBack.BACK_IMAGE_SIZE_MIN,
        max_value=FactionBack.BACK_IMAGE_SIZE_MAX,
        initial=FactionBack.BACK_IMAGE_SIZE_DEFAULT,
        widget=forms.NumberInput(attrs={
            'type': 'range',
            'min': FactionBack.BACK_IMAGE_SIZE_MIN,
            'max': FactionBack.BACK_IMAGE_SIZE_MAX,
            'step': 1,
            'class': 'form-range',
        }),
    )

    def __init__(self, *args, back=None, **kwargs):
        self.back = back
        super().__init__(*args, **kwargs)
        if back is not None:
            self.fields['back_image'].initial = back.back_image
            if not self.is_bound:
                self.fields['complexity'].initial = back.complexity
                self.fields['card_wealth'].initial = back.card_wealth
                self.fields['aggression'].initial = back.aggression
                self.fields['crafting_ability'].initial = back.crafting_ability
                self.fields['setup_order'].initial = back.setup_order
                self.fields['how_to_play_title'].initial = back.how_to_play_title
                self.fields['how_to_play_text'].initial = back.how_to_play_text
                self.fields['back_image_size'].initial = back.back_image_size

    def clean_back_image_size(self):
        raw = self.cleaned_data.get('back_image_size')
        if raw in (None, ''):
            return FactionBack.BACK_IMAGE_SIZE_DEFAULT
        return max(
            FactionBack.BACK_IMAGE_SIZE_MIN,
            min(FactionBack.BACK_IMAGE_SIZE_MAX, int(raw)),
        )

    def save(self, back=None):
        back = back or self.back
        back.complexity = self.cleaned_data['complexity']
        back.card_wealth = self.cleaned_data['card_wealth']
        back.aggression = self.cleaned_data['aggression']
        back.crafting_ability = self.cleaned_data['crafting_ability']
        back.setup_order = self.cleaned_data.get('setup_order') or ''
        back.how_to_play_title = self.cleaned_data['how_to_play_title']
        back.how_to_play_text = self.cleaned_data.get('how_to_play_text') or ''
        back.back_image_size = self.cleaned_data['back_image_size']
        update_fields = [
            'complexity', 'card_wealth', 'aggression', 'crafting_ability',
            'setup_order', 'how_to_play_title', 'how_to_play_text',
            'back_image_size',
            'last_updated',
        ]
        new_img = self.cleaned_data.get('back_image')
        if new_img:
            back.back_image = new_img
            update_fields.append('back_image')
        elif new_img is False:
            back.back_image = None
            update_fields.append('back_image')
        back.save(update_fields=update_fields)
        return back


def _cap(value, limit, label):
    text = value or ''
    if len(text) > limit:
        raise forms.ValidationError(f"{label} limited to {limit} characters.")
    return text


def _cap_visible(value, limit, label):
    """Cap based on visible character count (HTML stripped) so inline tags and
    image embeds don't push otherwise-fine input over the limit. The raw value
    is still saved as-is — only the length check uses the stripped form."""
    from django.utils.html import strip_tags
    raw = value or ''
    visible_len = len(strip_tags(raw))
    if visible_len > limit:
        raise forms.ValidationError(f"{label} limited to {limit} characters.")
    return raw


class FactionAbilityForm(forms.ModelForm):
    class Meta:
        model = FactionAbility
        fields = ['order', 'title', 'body']
        widgets = {
            'body': RichTextarea(),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['order'].required = False

    def clean_body(self):
        return _cap_visible(self.cleaned_data.get('body'), MAX_ABILITY_BODY, 'Body')


class ContentBoxForm(forms.ModelForm):
    class Meta:
        model = ContentBox
        fields = ['order', 'title', 'text', 'paper_background']
        widgets = {
            'text': RichTextarea(),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['order'].required = False
        self.fields['title'].required = False
        self.fields['text'].required = False
        self.fields['paper_background'].required = False

    def clean_text(self):
        return _cap(self.cleaned_data.get('text'), MAX_CONTENT_TEXT, 'Text')


class PhaseStepForm(forms.ModelForm):
    class Meta:
        model = PhaseStep
        fields = ['phase', 'content_box', 'text']
        widgets = {
            'text': RichTextarea(),
        }

    def __init__(self, *args, **kwargs):
        kwargs.pop('sheet', None)
        super().__init__(*args, **kwargs)
        self.fields['content_box'].required = False
        self.fields['text'].required = False

    def clean_text(self):
        return _cap_visible(self.cleaned_data.get('text'), MAX_PHASE_STEP_TEXT, 'Text')


class StepActionForm(forms.ModelForm):
    cost = forms.CharField(
        max_length=20,
        widget=forms.Select(attrs={'class': 'form-select form-select-sm'}),
    )

    class Meta:
        model = StepAction
        fields = ['text', 'cost', 'cost_image']
        widgets = {
            'text': RichTextarea(attrs={'rows': 3}),
            'cost_image': forms.ClearableFileInput(attrs={'class': 'form-control form-control-sm'}),
        }

    def clean_text(self):
        return _cap(self.cleaned_data.get('text'), MAX_STEP_ACTION_TEXT, 'Text')


class PhaseStepCostImageForm(forms.ModelForm):
    class Meta:
        model = PhaseStep
        fields = ['step_cost_image']
        widgets = {
            'step_cost_image': forms.ClearableFileInput(attrs={'class': 'form-control form-control-sm'}),
        }


class BorderedBoxForm(forms.ModelForm):
    class Meta:
        model = BorderedBox
        fields = ['title', 'body', 'height', 'element_color']
        widgets = {
            'title': forms.TextInput(attrs={'class': 'form-control form-control-sm'}),
            'body': RichTextarea(),
            'height': forms.Select(attrs={'class': 'form-select form-select-sm'}),
        }

    def clean_body(self):
        return _cap(self.cleaned_data.get('body'), MAX_BORDERED_BOX_BODY, 'Body')


class CardboardTrackForm(forms.ModelForm):
    """Used by the inline create endpoint (track_add). The compact create UI
    only submits a subset, so optional fields are relaxed at the form level."""
    class Meta:
        model = CardboardTrack
        fields = [
            'title', 'body', 'type',
            'num_columns', 'num_rows',
            'column_headers_json', 'row_titles_json',
            'column_dividers', 'row_dividers',
            'header_position', 'header_title',
            'row_title_orientation', 'background_image',
        ]
        widgets = {
            'title': forms.TextInput(attrs={'class': 'form-control form-control-sm'}),
            'body': RichTextarea(),
            'type': forms.Select(attrs={'class': 'form-select form-select-sm'}),
            'num_columns': forms.NumberInput(attrs={'class': 'form-control form-control-sm', 'min': 1}),
            'num_rows': forms.NumberInput(attrs={'class': 'form-control form-control-sm', 'min': 1}),
            'column_headers_json': forms.HiddenInput(),
            'row_titles_json': forms.HiddenInput(),
            'column_dividers': forms.HiddenInput(),
            'row_dividers': forms.HiddenInput(),
            'header_position': forms.HiddenInput(),
            'header_title': forms.HiddenInput(),
            'row_title_orientation': forms.HiddenInput(),
            'background_image': forms.ClearableFileInput(attrs={'class': 'form-control form-control-sm'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['header_position'].required = False
        self.fields['row_title_orientation'].required = False
        self.fields['body'].required = False
        self.fields['row_titles_json'].required = False
        self.fields['column_headers_json'].required = False
        self.fields['column_dividers'].required = False
        self.fields['row_dividers'].required = False
        self.fields['header_title'].required = False

    def _parse_json_list(self, field_name):
        import json
        raw = self.data.get(field_name, '')
        if not raw:
            return []
        if isinstance(raw, list):
            return raw
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            raise forms.ValidationError(f'{field_name}: invalid JSON.')
        if not isinstance(data, list):
            raise forms.ValidationError(f'{field_name}: must be a list.')
        return [str(x) for x in data]

    def clean_row_titles_json(self):
        return self._parse_json_list('row_titles_json')

    def clean_column_headers_json(self):
        return self._parse_json_list('column_headers_json')

    def clean_num_rows(self):
        n = self.cleaned_data['num_rows']
        if n > MAX_TRACK_ROWS:
            raise forms.ValidationError(f"Maximum {MAX_TRACK_ROWS} rows.")
        return n

    def clean_num_columns(self):
        n = self.cleaned_data['num_columns']
        if n > MAX_TRACK_COLS:
            raise forms.ValidationError(f"Maximum {MAX_TRACK_COLS} columns.")
        return n

    def clean_body(self):
        text = self.cleaned_data.get('body') or ''
        if len(text) > MAX_TRACK_BODY:
            raise forms.ValidationError(f"Body limited to {MAX_TRACK_BODY} characters.")
        return text


class CardboardSlotForm(forms.ModelForm):
    class Meta:
        model = CardboardSlot
        fields = ['content', 'centered_text', 'background_image']


class DecreeSectionForm(forms.ModelForm):
    class Meta:
        model = DecreeSection
        fields = ['title', 'body']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['title'].required = False
        self.fields['body'].required = False


class CardSlotForm(forms.ModelForm):
    class Meta:
        model = CardSlot
        fields = ['number', 'title', 'body']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['number'].required = False
        self.fields['title'].required = False
        self.fields['body'].required = False


class CardPileForm(forms.ModelForm):
    class Meta:
        model = CardPile
        fields = ['number', 'title', 'body', 'element_color', 'background_screen']
        widgets = {
            'body': RichTextarea(),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['number'].required = False
        self.fields['title'].required = False
        self.fields['body'].required = False

    def clean_body(self):
        return _cap(self.cleaned_data.get('body'), MAX_CARD_PILE_BODY, 'Body')


class PieceForm(forms.ModelForm):
    class Meta:
        model = Piece
        fields = ['name', 'quantity', 'type', 'small_icon']

    def clean_quantity(self):
        return min(self.cleaned_data['quantity'], MAX_PIECE_QUANTITY)


class FactionHeaderForm(forms.Form):
    """Edits faction_name (on ForgedFaction) plus header_image and
    title_text_color (on FactionSheet) in a single form."""

    faction_name = forms.CharField(
        max_length=100,
        widget=forms.TextInput(attrs={
            'class': 'form-control luminari forge-header-name-input',
            'placeholder': 'Faction Name',
        }),
    )
    header_image = forms.ImageField(
        required=False,
        widget=forms.ClearableFileInput(attrs={
            'class': 'form-control form-control-sm forge-header-image-input',
            'accept': 'image/*',
        }),
    )
    clear_header_image = forms.BooleanField(required=False, widget=forms.HiddenInput())
    title_text_color = forms.ChoiceField(
        choices=FactionSheet.TitleTextColor.choices,
        widget=forms.RadioSelect(attrs={
            'class': 'btn-check forge-header-title-color',
        }),
    )
    pnp_version = forms.CharField(
        required=False,
        max_length=100,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'Version #...',
        }),
    )
    art_by = forms.CharField(
        required=False,
        max_length=100,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'Artists...',
        }),
    )

    def __init__(self, *args, sheet=None, **kwargs):
        self.sheet = sheet
        super().__init__(*args, **kwargs)
        if sheet is not None and not self.is_bound:
            faction = sheet.faction
            self.fields['faction_name'].initial = faction.faction_name
            self.fields['title_text_color'].initial = sheet.title_text_color
            self.fields['pnp_version'].initial = faction.pnp_version or ''
            self.fields['art_by'].initial = faction.art_by or ''
        if sheet is not None and sheet.faction.is_published_linked:
            self.fields['faction_name'].disabled = True
            self.fields['faction_name'].help_text = (
                "This Faction's name can no longer be changed."
            )

    def save(self):
        sheet = self.sheet
        faction = sheet.faction
        if not faction.is_published_linked:
            faction.faction_name = self.cleaned_data['faction_name']
        faction.pnp_version = self.cleaned_data.get('pnp_version') or None
        faction.art_by = self.cleaned_data.get('art_by') or None
        faction.save(update_fields=['faction_name', 'pnp_version', 'art_by'])
        sheet.title_text_color = self.cleaned_data['title_text_color']
        update_fields = ['title_text_color', 'last_updated']
        new_img = self.cleaned_data.get('header_image')
        if new_img:
            sheet.header_image = new_img
            update_fields.append('header_image')
        elif self.cleaned_data.get('clear_header_image') or new_img is False:
            sheet.header_image = None
            update_fields.append('header_image')
        sheet.save(update_fields=update_fields)
        return sheet


class SetupCardForm(forms.Form):
    """Edits faction_name (on ForgedFaction) plus type, reach, and header_image
    (on SetupCard) in a single form."""

    faction_name = forms.CharField(
        max_length=100,
        widget=forms.TextInput(attrs={
            'class': 'form-control luminari',
            'placeholder': 'Faction Name',
        }),
    )
    type = forms.ChoiceField(
        choices=SetupCard.TypeChoices.choices,
        widget=forms.Select(attrs={'class': 'form-select'}),
    )
    reach = forms.IntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(10)],
        widget=forms.NumberInput(attrs={'min': 1, 'max': 10, 'class': 'form-control'}),
    )
    header_image = forms.ImageField(
        required=False,
        widget=forms.ClearableFileInput(attrs={
            'class': 'form-control form-control-sm',
            'accept': 'image/*',
        }),
    )
    clear_header_image = forms.BooleanField(required=False, widget=forms.HiddenInput())

    def __init__(self, *args, card=None, **kwargs):
        self.card = card
        super().__init__(*args, **kwargs)
        if card is not None and not self.is_bound:
            self.fields['faction_name'].initial = card.faction.faction_name
            self.fields['type'].initial = card.type
            self.fields['reach'].initial = card.reach
        if card is not None and card.faction.is_published_linked:
            self.fields['faction_name'].disabled = True
            self.fields['faction_name'].help_text = (
                "This Faction's name can no longer be changed."
            )

    def save(self, card=None):
        card = card or self.card
        if not card.faction.is_published_linked:
            card.faction.faction_name = self.cleaned_data['faction_name']
            card.faction.save(update_fields=['faction_name'])
        card.type = self.cleaned_data['type']
        card.reach = self.cleaned_data['reach']
        update_fields = ['type', 'reach', 'last_updated']
        new_img = self.cleaned_data.get('header_image')
        if new_img:
            card.header_image = new_img
            update_fields.append('header_image')
        elif self.cleaned_data.get('clear_header_image') or new_img is False:
            card.header_image = None
            update_fields.append('header_image')
        card.save(update_fields=update_fields)
        return card


class SetupStepForm(forms.ModelForm):
    class Meta:
        model = SetupStep
        fields = ['text']
        widgets = {
            'text': RichTextarea(attrs={'rows': 3}),
        }

    def clean_text(self):
        return _cap(self.cleaned_data.get('text'), MAX_SETUP_STEP_TEXT, 'Text')


class LegendForm(forms.ModelForm):
    class Meta:
        model = Legend
        fields = ['title', 'body']
        widgets = {
            'title': forms.TextInput(attrs={'class': 'form-control form-control-sm luminari'}),
            'body': RichTextarea(attrs={'rows': 2}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['title'].required = False
        self.fields['body'].required = False

    def clean_body(self):
        return _cap(self.cleaned_data.get('body'), MAX_LEGEND_BODY, 'Body')


class LegendRowForm(forms.ModelForm):
    class Meta:
        model = LegendRow
        fields = ['title', 'image', 'icon', 'body']
        widgets = {
            'title': forms.TextInput(attrs={'class': 'form-control form-control-sm luminari'}),
            'image': forms.ClearableFileInput(attrs={'class': 'form-control form-control-sm'}),
            'icon': forms.HiddenInput(),
            'body': RichTextarea(attrs={'rows': 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['body'].required = False
        self.fields['image'].required = False
        self.fields['icon'].required = False

    def clean_body(self):
        return _cap(self.cleaned_data.get('body'), MAX_LEGEND_ROW_BODY, 'Body')


class CharacterImageForm(forms.ModelForm):
    class Meta:
        model = CharacterImage
        fields = ['image', 'in_front']
        widgets = {
            'image': forms.ClearableFileInput(attrs={
                'class': 'form-control form-control-sm forge-character-image-input',
                'accept': 'image/*',
            }),
            'in_front': forms.CheckboxInput(attrs={
                'class': 'form-check-input mt-0',
            }),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # On edit (instance has pk), keeping the existing image is allowed.
        if self.instance and self.instance.pk:
            self.fields['image'].required = False


CUSTOM_INLINE_IMAGE_MAX_BYTES = 2 * 1024 * 1024


class CustomInlineImageForm(forms.ModelForm):
    class Meta:
        model = CustomInlineImage
        fields = ['name', 'image', 'card_icon']
        widgets = {
            'name': forms.TextInput(attrs={
                'class': 'form-control form-control-sm',
                'placeholder': 'Label',
                'maxlength': 40,
                'required': True,
            }),
            'image': forms.ClearableFileInput(attrs={
                'class': 'form-control form-control-sm',
                'accept': 'image/png,image/jpeg,image/webp,image/gif',
            }),
            'card_icon': forms.CheckboxInput(attrs={
                'class': 'form-check-input',
            }),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            self.fields['image'].required = False

    def clean_image(self):
        f = self.cleaned_data.get('image')
        if f and getattr(f, 'size', 0) > CUSTOM_INLINE_IMAGE_MAX_BYTES:
            raise forms.ValidationError("Image must be 2 MB or smaller.")
        return f


class ScaleForm(forms.ModelForm):
    class Meta:
        model = Scale
        fields = ['title']
        widgets = {
            'title': forms.TextInput(attrs={'class': 'form-control form-control-sm luminari'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['title'].required = False


class ScaleRowForm(forms.ModelForm):
    class Meta:
        model = ScaleRow
        fields = ['range', 'result']
        widgets = {
            'range': forms.TextInput(attrs={
                'class': 'form-control form-control-sm',
                'placeholder': 'Range',
                'style': 'max-width:6rem;',
            }),
            'result': forms.TextInput(attrs={
                'class': 'form-control form-control-sm',
                'data-track-image-input': '',
            }),
        }


class ForgedDeckGroupForm(forms.ModelForm):
    class Meta:
        model = ForgedDeckGroup
        fields = ['name', 'back_image']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'back_image': forms.ClearableFileInput(attrs={'class': 'form-control'}),
        }


class ForgedCardForm(forms.ModelForm):
    class Meta:
        model = ForgedCard
        fields = ['name', 'text', 'front_image']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control form-control-sm'}),
            'text': forms.TextInput(attrs={'class': 'form-control form-control-sm'}),
            'front_image': forms.ClearableFileInput(attrs={'class': 'form-control form-control-sm'}),
        }
