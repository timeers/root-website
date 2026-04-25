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
    FactionSheet,
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


class FactionSheetForm(forms.ModelForm):
    class Meta:
        model = FactionSheet
        fields = [
            'flavor_text',
            'action_image',
            'include_crafted_items',
            'layout_mode',
        ]


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
        fields = ['phase', 'number', 'content_box', 'text']
        widgets = {
            'text': RichTextarea(),
        }

    def __init__(self, *args, **kwargs):
        sheet = kwargs.pop('sheet', None)
        super().__init__(*args, **kwargs)
        if sheet is not None:
            self.fields['content_box'].queryset = sheet.content_boxes.all()
        self.fields['content_box'].required = False


class StepActionForm(forms.ModelForm):
    class Meta:
        model = StepAction
        fields = ['order', 'text', 'cost', 'cost_image']
        widgets = {
            'text': RichTextarea(attrs={'rows': 3}),
        }


class BorderedBoxForm(forms.ModelForm):
    class Meta:
        model = BorderedBox
        fields = ['order', 'title', 'body', 'height']
        widgets = {
            'body': RichTextarea(),
        }


class CardboardTrackForm(forms.ModelForm):
    class Meta:
        model = CardboardTrack
        fields = [
            'order', 'title', 'body', 'type',
            'num_columns', 'column_headers', 'column_cost_type',
            'column_dividers', 'header_position', 'header_title',
            'row_title_orientation', 'background_image',
        ]
        widgets = {
            'body': RichTextarea(),
        }


class CardboardSlotForm(forms.ModelForm):
    class Meta:
        model = CardboardSlot
        fields = ['number', 'row', 'column', 'row_title', 'content', 'background_image']


class DecreeSectionForm(forms.ModelForm):
    class Meta:
        model = DecreeSection
        fields = ['title', 'body']


class CardSlotForm(forms.ModelForm):
    class Meta:
        model = CardSlot
        fields = ['number', 'title', 'body']
        widgets = {
            'body': RichTextarea(),
        }


class CardPileForm(forms.ModelForm):
    class Meta:
        model = CardPile
        fields = ['number', 'title', 'body']
        widgets = {
            'body': RichTextarea(),
        }


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
        fields = ['number', 'text']
        widgets = {
            'text': RichTextarea(attrs={'rows': 3}),
        }
