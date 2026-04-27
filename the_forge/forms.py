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
    PhaseStep,
    Piece,
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
            'color': forms.TextInput(attrs={'type': 'color'}),
        }


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


class PhaseStepForm(forms.ModelForm):
    class Meta:
        model = PhaseStep
        fields = ['phase', 'text']
        widgets = {
            'text': RichTextarea(),
        }

    def __init__(self, *args, **kwargs):
        kwargs.pop('sheet', None)
        super().__init__(*args, **kwargs)


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
            'num_columns', 'num_rows', 'column_headers',
            'column_dividers', 'header_position', 'header_title',
            'row_title_orientation', 'background_image',
        ]
        widgets = {
            'title': forms.TextInput(attrs={'class': 'form-control form-control-sm'}),
            'body': RichTextarea(),
            'type': forms.Select(attrs={'class': 'form-select form-select-sm'}),
            'num_columns': forms.NumberInput(attrs={'class': 'form-control form-control-sm', 'min': 1}),
            'num_rows': forms.NumberInput(attrs={'class': 'form-control form-control-sm', 'min': 1}),
            'column_headers': forms.TextInput(attrs={'class': 'form-control form-control-sm'}),
            'column_dividers': forms.TextInput(attrs={'class': 'form-control form-control-sm'}),
            'header_position': forms.Select(attrs={'class': 'form-select form-select-sm'}),
            'header_title': forms.TextInput(attrs={'class': 'form-control form-control-sm'}),
            'row_title_orientation': forms.Select(attrs={'class': 'form-select form-select-sm'}),
            'background_image': forms.ClearableFileInput(attrs={'class': 'form-control form-control-sm'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['header_position'].required = False
        self.fields['row_title_orientation'].required = False
        self.fields['body'].required = False


class CardboardSlotForm(forms.ModelForm):
    class Meta:
        model = CardboardSlot
        fields = ['row_title', 'content', 'background_image']


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
