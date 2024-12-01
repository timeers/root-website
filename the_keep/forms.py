from django import forms
from .models import (
    Post, Map, Deck, Vagabond, Hireling, Landmark, Faction,
    Warrior, Building, Token, Card, OtherPiece
)
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator


top_fields = ['title']
bottom_fields = ['lore','description', 'bgg_link', 'tts_link', 'ww_link', 'wr_link', 'pnp_link', 'artist']

class PostSearchForm(forms.ModelForm):
    search_term = forms.CharField(required=True, max_length=100)



class PostCreateForm(forms.ModelForm):
    form_type = 'Component'
    class Meta:
        model = Post
        fields = ['title', 'lore', 'description', 'artist', 'bgg_link', 'tts_link', 'ww_link', 'wr_link', 'pnp_link']
        labels = {
            'bgg_link': "Board Game Geek Post", 
            'tts_link': "Tabletop Simulator", 
            'ww_link': "Woodland Warriors Thread", 
            'wr_link': "Weird Root Thread", 
            'pnp_link': "Link to Print and Play Files"
        }
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['lore'].widget.attrs.update({
            'rows': '2',
            'placeholder': 'Enter any thematic text here...'
        })
        self.fields['description'].widget.attrs.update({
            'rows': '2'
            })


    def clean(self):
            cleaned_data = super().clean()
            bgg_link = cleaned_data.get('bgg_link')
            tts_link = cleaned_data.get('tts_link')
            ww_link = cleaned_data.get('ww_link')
            wr_link = cleaned_data.get('wr_link')
            pnp_link = cleaned_data.get('pnp_link')
            # Check that at least one of the links are filled
            if not any([bgg_link, tts_link, ww_link, wr_link, pnp_link]):
                raise ValidationError("Please include a link to one of the following: a Board Game Geek post, a Tabletop Simulator Mod, a Woodland Warriors Discord Thread, a Weird Root Discord Thread, or Print and Play Files.")
            # Validate URLs
            url_validator = URLValidator()
            for url in [bgg_link, tts_link, ww_link, wr_link, pnp_link]:
                if url:  # Only validate if the field is filled
                    try:
                        url_validator(url)
                    except ValidationError:
                        raise ValidationError(f"The field '{url}' must be a valid URL.")
            return cleaned_data



class MapCreateForm(PostCreateForm):  # Inherit from PostCreateForm
    form_type = 'Map'
    title = forms.CharField(
        label='Map Name',
        required=True
    )
    clearings = forms.IntegerField(
        label='Number of Clearings', initial=12,
        min_value=1,  # Add validation for minimum value if necessary
        required=True
    )
    fixed_clearings = forms.BooleanField(
        label='This map has fixed suits for each clearing by default', initial=False, required=False
    )
    based_on = forms.ModelChoiceField(
        queryset=Post.objects.filter(component__in=['Map']),
        required=False
    )
    class Meta(PostCreateForm.Meta):  # Inherit Meta from PostCreateForm
        model = Map  # Specify the model to be Map
        fields = top_fields + ['clearings', 'fixed_clearings', 'based_on'] + bottom_fields

    def __init__(self, *args, **kwargs):
        # Check if an instance is being created or updated
        instance = kwargs.get('instance', None)
        super().__init__(*args, **kwargs)
        self.fields['description'].widget.attrs.update({
            'placeholder': 'Give a brief explanation of how to play on this Map...'
            })
        if instance:
            # Exclude the current instance from the queryset
            self.fields['based_on'].queryset = self.fields['based_on'].queryset.exclude(id=instance.id)

    def clean(self):
        cleaned_data = super().clean()
        title = cleaned_data.get('title')
            # Check if the same name already exists
        if Map.objects.exclude(id=self.instance.id).filter(title__iexact=title).exists():
            raise ValidationError(f'A map with the name "{title}" already exists. Please choose a different name.')
        return cleaned_data

class MapImportForm(MapCreateForm):  # Inherit from PostCreateForm
    class Meta(MapCreateForm.Meta):  # Inherit Meta from PostCreateForm
        model = Map  # Specify the model to be Map
        fields = top_fields + ['clearings', 'date_posted', 'designer', 'official', 'stable'] + bottom_fields



class DeckCreateForm(PostCreateForm):  # Inherit from PostCreateForm
    form_type = 'Deck'
    title = forms.CharField(
        label='Deck Name',
        required=True
    )
    card_total = forms.IntegerField(
        label='Card Count', initial=54,
        min_value=1,  # Add validation for minimum value if necessary
        required=True
    )
    based_on = forms.ModelChoiceField(
        queryset=Post.objects.filter(component__in=['Deck']),
        required=False
    )
    class Meta(PostCreateForm.Meta):  # Inherit Meta from PostCreateForm
        model = Deck  # Specify the model to be Deck
        fields = top_fields + ['card_total', 'based_on'] + bottom_fields

    def __init__(self, *args, **kwargs):
        # Check if an instance is being created or updated
        instance = kwargs.get('instance', None)
        super().__init__(*args, **kwargs)
        self.fields['description'].widget.attrs.update({
            'placeholder': 'Give a brief explanation on how to use this Deck...'
            })
        if instance:
            # Exclude the current instance from the queryset
            self.fields['based_on'].queryset = self.fields['based_on'].queryset.exclude(id=instance.id)

    def clean(self):
        cleaned_data = super().clean()
        title = cleaned_data.get('title')
            # Check if the same name already exists
        if Deck.objects.exclude(id=self.instance.id).filter(title__iexact=title).exists():
            raise ValidationError(f'A deck with the name "{title}" already exists. Please choose a different name.')
        return cleaned_data

class DeckImportForm(DeckCreateForm):
    class Meta(PostCreateForm.Meta):  # Inherit Meta from PostCreateForm
        model = Deck  # Specify the model to be Deck
        fields = top_fields + ['card_total', 'date_posted', 'designer', 'official', 'stable'] + bottom_fields


class LandmarkCreateForm(PostCreateForm):  # Inherit from PostCreateForm
    form_type = 'Landmark'
    title = forms.CharField(
        label='Landmark Name',
        required=True
    )
    based_on = forms.ModelChoiceField(
        queryset=Post.objects.filter(component__in=['Landmark']),
        required=False
    )
    def __init__(self, *args, **kwargs):
        instance = kwargs.get('instance', None)
        super().__init__(*args, **kwargs)

        self.fields['card_text'].widget.attrs.update({'rows': '2'})
        self.fields['description'].widget.attrs.update({
            'placeholder': 'Give a brief explanation on how to use this Landmark...'
            })
        if instance:
            # Exclude the current instance from the queryset
            self.fields['based_on'].queryset = self.fields['based_on'].queryset.exclude(id=instance.id)

    class Meta(PostCreateForm.Meta):  # Inherit Meta from PostCreateForm
        model = Landmark  # Specify the model to be Landmark
        fields = top_fields + ['card_text', 'based_on'] + bottom_fields
    def clean(self):
        cleaned_data = super().clean()
        title = cleaned_data.get('title')
            # Check if the same name already exists
        if Landmark.objects.exclude(id=self.instance.id).filter(title__iexact=title).exists():
            raise ValidationError(f'A landmark with the name "{title}" already exists. Please choose a different name.')
        return cleaned_data

class HirelingCreateForm(PostCreateForm):  # Inherit from PostCreateForm
    form_type = 'Hireling'
    title = forms.CharField(
        label='Hireling Name',
        required=True
    )
    TYPE_CHOICES = [
        ('P', 'Promoted'),
        ('D', 'Demoted'),
    ]
    type = forms.ChoiceField(
        choices=TYPE_CHOICES, initial="P",
        widget=forms.RadioSelect(),
        required=True
    )
    based_on = forms.ModelChoiceField(
        queryset=Post.objects.filter(component__in=['Faction', 'Vagabond', 'Hireling']),
        required=False
    )
    class Meta(PostCreateForm.Meta): 
        model = Hireling 
        fields = top_fields + ['animal', 'type', 'based_on'] + bottom_fields

    def __init__(self, *args, **kwargs):
        # Check if an instance is being created or updated
        instance = kwargs.get('instance', None)
        super().__init__(*args, **kwargs)
        self.fields['description'].widget.attrs.update({
            'placeholder': 'Give a brief explanation on how to use this Hireling...'
            })
        if instance:
            # Exclude the current instance from the queryset
            self.fields['based_on'].queryset = self.fields['based_on'].queryset.exclude(id=instance.id)

    def clean(self):
        cleaned_data = super().clean()
        title = cleaned_data.get('title')
            # Check if the same name already exists
        if Hireling.objects.exclude(id=self.instance.id).filter(title__iexact=title).exists():
            raise ValidationError(f'A hireling with the name "{title}" already exists. Please choose a different name.')
        return cleaned_data

class VagabondCreateForm(PostCreateForm): 
    form_type = 'Vagabond'
    title = forms.CharField(
        label='Vagabond Name',
        required=True
    )
    starting_torch = forms.IntegerField(initial=1, min_value=0, max_value=2)
    starting_coins = forms.IntegerField(initial=0, min_value=0, max_value=4)
    starting_boots = forms.IntegerField(initial=0, min_value=0, max_value=4)
    starting_bag = forms.IntegerField(initial=0, min_value=0, max_value=4)
    starting_tea = forms.IntegerField(initial=0, min_value=0, max_value=4)
    starting_sword = forms.IntegerField(initial=0, min_value=0, max_value=4)
    starting_hammer = forms.IntegerField(initial=0, min_value=0, max_value=4)
    starting_crossbow = forms.IntegerField(initial=0, min_value=0, max_value=4)

    based_on = forms.ModelChoiceField(
        queryset=Post.objects.filter(component__in=['Faction', 'Vagabond']).exclude(slug='vagabond'),
        required=False
    )


    class Meta(PostCreateForm.Meta):  # Inherit Meta from PostCreateForm
        model = Vagabond  # Specify the model to be Vagabond
        fields = top_fields + ['animal', 'based_on',
                                'ability_item', 'ability', 'ability_description', 
                                'starting_torch', 'starting_coins', 'starting_boots',
                                'starting_bag', 'starting_tea', 'starting_sword', 'starting_hammer', 'starting_crossbow'] + bottom_fields
    def __init__(self, *args, **kwargs):
        instance = kwargs.get('instance', None)
        super().__init__(*args, **kwargs)
        self.fields['ability_description'].widget.attrs.update({'rows': '2'})
        self.fields['description'].widget.attrs.update({
            'placeholder': 'Give a brief explanation on how to use this Vagabond...'
            })
        if instance:
            # Exclude the current instance from the queryset
            self.fields['based_on'].queryset = self.fields['based_on'].queryset.exclude(id=instance.id)
    
    def clean(self):
            cleaned_data = super().clean()
            starting_torch = cleaned_data.get('starting_torch')
            starting_coins = cleaned_data.get('starting_coins')
            starting_boots = cleaned_data.get('starting_boots')
            starting_bag = cleaned_data.get('starting_bag')
            starting_tea = cleaned_data.get('starting_tea')
            starting_sword = cleaned_data.get('starting_sword')
            starting_hammer = cleaned_data.get('starting_hammer')
            starting_crossbow = cleaned_data.get('starting_crossbow')
            title = cleaned_data.get('title')
            # Check if the same name already exists
            if Vagabond.objects.exclude(id=self.instance.id).filter(title__iexact=title).exists():
                raise ValidationError(f'A vagabond with the name "{title}" already exists. Please choose a different name.')
            # Check that the VB doesn't have a wild amount of items
            if (starting_torch+starting_coins+starting_boots+starting_bag+starting_tea+starting_sword+starting_hammer+starting_crossbow) > 6:
                raise ValidationError("Please check the number of starting items. A Vagabond typically starts with 3-4 items.")
            return cleaned_data

class VagabondImportForm(PostCreateForm): 
    class Meta(PostCreateForm.Meta):  # Inherit Meta from PostCreateForm
        model = Vagabond  # Specify the model to be Vagabond
        fields = top_fields + ['animal', 'official', 'designer',
                                'ability_item', 'ability', 'ability_description', 
                                'starting_torch', 'starting_coins', 'starting_boots',
                                'starting_bag', 'starting_tea', 'starting_sword', 'starting_hammer', 'starting_crossbow'] + bottom_fields    
    def clean(self):
            cleaned_data = super().clean()
            starting_torch = cleaned_data.get('starting_torch')
            starting_coins = cleaned_data.get('starting_coins')
            starting_boots = cleaned_data.get('starting_boots')
            starting_bag = cleaned_data.get('starting_bag')
            starting_tea = cleaned_data.get('starting_tea')
            starting_sword = cleaned_data.get('starting_sword')
            starting_hammer = cleaned_data.get('starting_hammer')
            starting_crossbow = cleaned_data.get('starting_crossbow')
            # Check that the VB doesn't have a wild amount of items
            if (starting_torch+starting_coins+starting_boots+starting_bag+starting_tea+starting_sword+starting_hammer+starting_crossbow) > 6:
                raise ValidationError("Please check the number of starting items. A Vagabond typically starts with 3-4 items.")
            return cleaned_data



class FactionCreateForm(PostCreateForm):  # Inherit from PostCreateForm
    form_type = 'Faction'
    title = forms.CharField(
        label='Faction Name',
        required=True
    )
    
    TYPE_CHOICES = [
        ('I', 'Insurgent'),
        ('M', 'Militant'),
    ]
    STYLE_CHOICES = [
        ('N', 'None'),
        ('L', 'Low'),
        ('M', 'Moderate'),
        ('H', 'High'),
    ]
    type = forms.ChoiceField(
        choices=TYPE_CHOICES, initial='I',
        widget=forms.RadioSelect(),
        required=True
    )
    reach = forms.IntegerField(min_value=1, max_value=10)
    complexity = forms.ChoiceField(
        choices=STYLE_CHOICES, initial="M",
        widget=forms.RadioSelect(),
        required=True
    )
    card_wealth = forms.ChoiceField(
        choices=STYLE_CHOICES, initial="M",
        widget=forms.RadioSelect(),
        required=True
    )
    aggression = forms.ChoiceField(
        choices=STYLE_CHOICES, initial="M",
        widget=forms.RadioSelect(),
        required=True
    )
    crafting_ability = forms.ChoiceField(
        choices=STYLE_CHOICES, initial="M",
        widget=forms.RadioSelect(),
        required=True
    )
    based_on = forms.ModelChoiceField(
        queryset=Post.objects.filter(component__in=['Faction']),
        required=False
    )
    class Meta(PostCreateForm.Meta): 
        model = Faction 
        fields = top_fields + ['small_icon', 'type', 'reach', 'animal', 'based_on',  'complexity', 'card_wealth', 
                               'aggression', 'crafting_ability'] + bottom_fields

    def clean(self):
            cleaned_data = super().clean()
            reach = cleaned_data.get('reach')
            type = cleaned_data.get('type')
            title = cleaned_data.get('title')

            # Check that reach matches the type
            if type == 'I' and reach > 6:
                raise ValidationError('Reach Score does not match Type selected. Either decrease Reach or select "Militant"')
            elif type == 'M' and reach < 6:
                raise ValidationError('Reach Score does not match Type selected. Either increase Reach or select "Insurgent"')
            
                # Check if a faction with the same name already exists
            if Faction.objects.exclude(id=self.instance.id).filter(title__iexact=title).exists():
                raise ValidationError(f'A faction with the name "{title}" already exists. Please choose a different name.')
            return cleaned_data
    
    def __init__(self, *args, **kwargs):
        # Check if an instance is being created or updated
        instance = kwargs.get('instance', None)
        super().__init__(*args, **kwargs)
        self.fields['description'].widget.attrs.update({
            'placeholder': 'Give a brief explanation on how to use this Faction...'
            })
        if instance:
            # Exclude the current instance from the queryset
            self.fields['based_on'].queryset = self.fields['based_on'].queryset.exclude(id=instance.id)



class FactionImportForm(FactionCreateForm):
    reach = forms.IntegerField(min_value=0, max_value=10)
    class Meta(FactionCreateForm):
        model = Faction 
        fields = top_fields + ['small_icon', 'official', 'type', 'reach', 'animal',  'complexity', 'card_wealth', 
                               'aggression', 'crafting_ability', 'designer', 'expansion', 'stable', 'date_posted'] + bottom_fields

    def __init__(self, *args, **kwargs):
            faction_instance = kwargs.pop('instance', None)
            super().__init__(*args, **kwargs)

            # Set the initial value for small_icon if the instance exists
            if faction_instance and faction_instance.small_icon:
                self.fields['small_icon'].initial = faction_instance.small_icon


class WarriorForm(forms.ModelForm):
    class Meta:
        model = Warrior
        fields = ['name', 'quantity', 'description', 'suited', 'faction', 'hireling']
        widgets = {
            'description': forms.Textarea(attrs={'rows': 3, 'cols': 20}),
        }

class BuildingForm(forms.ModelForm):
    class Meta:
        model = Building
        fields = ['name', 'quantity', 'description', 'suited', 'faction', 'hireling']
        widgets = {
            'description': forms.Textarea(attrs={'rows': 3, 'cols': 20}),
        }

class TokenForm(forms.ModelForm):
    class Meta:
        model = Token
        fields = ['name', 'quantity', 'description', 'suited', 'faction', 'hireling']
        widgets = {
            'description': forms.Textarea(attrs={'rows': 3, 'cols': 20}),
        }

class CardForm(forms.ModelForm):
    class Meta:
        model = Card
        fields = ['name', 'quantity', 'description', 'suited', 'faction', 'hireling']
        widgets = {
            'description': forms.Textarea(attrs={'rows': 3, 'cols': 20}),
        }

class OtherPieceForm(forms.ModelForm):
    class Meta:
        model = OtherPiece
        fields = ['name', 'quantity', 'description', 'suited', 'faction', 'hireling', 'vagabond']
        widgets = {
            'description': forms.Textarea(attrs={'rows': 3, 'cols': 20}),
        }
