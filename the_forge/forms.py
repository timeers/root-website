from django import forms
from django.core.validators import MinValueValidator, MaxValueValidator

from .models import (
    BorderedBox,
    CardboardSlot,
    CardboardTrack,
    CardPile,
    CardSlot,
    CharacterImage,
    ContentBox,
    DecreeSection,
    FactionAbility,
    FactionBack,
    FactionSheet,
    ForgedFaction,
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

    class Meta:
        model = ForgedFaction
        fields = [
            'faction_name',
            'color',
            'background_preset',
            'background_image',
            'repeat_background_image',
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
        }

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
        widget=forms.TextInput(attrs={'placeholder': 'Faction', 'class': 'form-control form-control-lg'}),
    )
    how_to_play_text = forms.CharField(
        required=False,
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

    def save(self, back=None):
        back = back or self.back
        back.complexity = self.cleaned_data['complexity']
        back.card_wealth = self.cleaned_data['card_wealth']
        back.aggression = self.cleaned_data['aggression']
        back.crafting_ability = self.cleaned_data['crafting_ability']
        back.setup_order = self.cleaned_data.get('setup_order') or 'X'
        back.how_to_play_title = self.cleaned_data['how_to_play_title']
        back.how_to_play_text = self.cleaned_data.get('how_to_play_text') or ''
        update_fields = [
            'complexity', 'card_wealth', 'aggression', 'crafting_ability',
            'setup_order', 'how_to_play_title', 'how_to_play_text',
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


class ContentBoxForm(forms.ModelForm):
    class Meta:
        model = ContentBox
        fields = ['order', 'title', 'text']
        widgets = {
            'text': RichTextarea(),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['order'].required = False
        self.fields['title'].required = False
        self.fields['text'].required = False


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


class StepActionForm(forms.ModelForm):
    class Meta:
        model = StepAction
        fields = ['text', 'cost', 'cost_image']
        widgets = {
            'text': RichTextarea(attrs={'rows': 3}),
            'cost': forms.Select(attrs={'class': 'form-select form-select-sm'}),
            'cost_image': forms.ClearableFileInput(attrs={'class': 'form-control form-control-sm'}),
        }


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
        fields = ['title', 'body', 'height']
        widgets = {
            'title': forms.TextInput(attrs={'class': 'form-control form-control-sm'}),
            'body': RichTextarea(),
            'height': forms.Select(attrs={'class': 'form-select form-select-sm'}),
        }


class CardboardTrackForm(forms.ModelForm):
    """Used by the inline create endpoint (track_add). The compact create UI
    only submits a subset, so optional fields are relaxed at the form level."""
    class Meta:
        model = CardboardTrack
        fields = [
            'title', 'body', 'type',
            'num_columns', 'num_rows', 'column_headers', 'row_titles',
            'column_dividers', 'header_position', 'header_title',
            'row_title_orientation', 'background_image',
        ]
        widgets = {
            'title': forms.TextInput(attrs={'class': 'form-control form-control-sm'}),
            'body': RichTextarea(),
            'type': forms.Select(attrs={'class': 'form-select form-select-sm'}),
            'num_columns': forms.NumberInput(attrs={'class': 'form-control form-control-sm', 'min': 1}),
            'num_rows': forms.NumberInput(attrs={'class': 'form-control form-control-sm', 'min': 1}),
            'column_headers': forms.HiddenInput(),
            'row_titles': forms.HiddenInput(),
            'column_dividers': forms.HiddenInput(),
            'header_position': forms.HiddenInput(),
            'header_title': forms.TextInput(attrs={'class': 'form-control form-control-sm'}),
            'row_title_orientation': forms.HiddenInput(),
            'background_image': forms.ClearableFileInput(attrs={'class': 'form-control form-control-sm'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['header_position'].required = False
        self.fields['row_title_orientation'].required = False
        self.fields['body'].required = False
        self.fields['row_titles'].required = False
        self.fields['column_headers'].required = False
        self.fields['column_dividers'].required = False


class CardboardSlotForm(forms.ModelForm):
    class Meta:
        model = CardboardSlot
        fields = ['content', 'background_image']


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
        fields = ['number', 'title', 'body']
        widgets = {
            'body': RichTextarea(),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['number'].required = False
        self.fields['title'].required = False
        self.fields['body'].required = False


class PieceForm(forms.ModelForm):
    class Meta:
        model = Piece
        fields = ['name', 'quantity', 'type', 'small_icon']


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
    title_text_color = forms.ChoiceField(
        choices=FactionSheet.TitleTextColor.choices,
        widget=forms.Select(attrs={
            'class': 'form-select form-select-sm forge-header-title-color',
        }),
    )

    def __init__(self, *args, sheet=None, **kwargs):
        self.sheet = sheet
        super().__init__(*args, **kwargs)
        if sheet is not None and not self.is_bound:
            self.fields['faction_name'].initial = sheet.faction.faction_name
            self.fields['title_text_color'].initial = sheet.title_text_color

    def save(self):
        sheet = self.sheet
        faction = sheet.faction
        faction.faction_name = self.cleaned_data['faction_name']
        faction.save(update_fields=['faction_name'])
        sheet.title_text_color = self.cleaned_data['title_text_color']
        update_fields = ['title_text_color']
        new_img = self.cleaned_data.get('header_image')
        if new_img:
            sheet.header_image = new_img
            update_fields.append('header_image')
        elif new_img is False:
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

    def __init__(self, *args, card=None, **kwargs):
        self.card = card
        super().__init__(*args, **kwargs)
        if card is not None and not self.is_bound:
            self.fields['faction_name'].initial = card.faction.faction_name
            self.fields['type'].initial = card.type
            self.fields['reach'].initial = card.reach

    def save(self, card=None):
        card = card or self.card
        card.faction.faction_name = self.cleaned_data['faction_name']
        card.faction.save(update_fields=['faction_name'])
        card.type = self.cleaned_data['type']
        card.reach = self.cleaned_data['reach']
        update_fields = ['type', 'reach']
        new_img = self.cleaned_data.get('header_image')
        if new_img:
            card.header_image = new_img
            update_fields.append('header_image')
        elif new_img is False:
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


class LegendForm(forms.ModelForm):
    class Meta:
        model = Legend
        fields = ['title']
        widgets = {
            'title': forms.TextInput(attrs={'class': 'form-control form-control-sm luminari'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['title'].required = False


class LegendRowForm(forms.ModelForm):
    class Meta:
        model = LegendRow
        fields = ['title', 'image', 'body']
        widgets = {
            'title': forms.TextInput(attrs={'class': 'form-control form-control-sm luminari'}),
            'image': forms.ClearableFileInput(attrs={'class': 'form-control form-control-sm'}),
            'body': RichTextarea(attrs={'rows': 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['body'].required = False
        self.fields['image'].required = False


class CharacterImageForm(forms.ModelForm):
    class Meta:
        model = CharacterImage
        fields = ['image']
        widgets = {
            'image': forms.ClearableFileInput(attrs={
                'class': 'form-control form-control-sm forge-character-image-input',
                'accept': 'image/*',
            }),
        }


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
