from django import forms

from .models import (
    BorderedBox,
    CardboardSlot,
    CardboardTrack,
    CardPile,
    CardSlot,
    ContentBox,
    DecreeSection,
    FactionAbility,
    FactionBack,
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


class FactionBackForm(forms.ModelForm):
    class Meta:
        model = FactionBack
        fields = [
            'complexity',
            'card_wealth',
            'aggression',
            'crafting_ability',
            'setup_order',
            'how_to_play_title',
            'how_to_play_text',
            'back_image',
        ]
        widgets = {
            'how_to_play_title': forms.TextInput(),
            'how_to_play_text': RichTextarea(attrs={'rows': 8}),
        }


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
        widgets = {
            'body': RichTextarea(),
        }

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


class SetupCardForm(forms.ModelForm):
    class Meta:
        model = SetupCard
        fields = ['type', 'reach']


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
