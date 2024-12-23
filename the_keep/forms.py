from django import forms
from .models import (
    Post, Map, Deck, Vagabond, Hireling, Landmark, Faction,
    Piece, Expansion
)
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.db.models import Q


top_fields = ['designer', 'title', 'expansion']
bottom_fields = ['lore', 'description', 'bgg_link', 'tts_link', 'ww_link', 'wr_link', 'pnp_link', 'stl_link', 'picture', 'artist']

class PostSearchForm(forms.ModelForm):
    search_term = forms.CharField(required=True, max_length=100)

class ExpansionCreateForm(forms.ModelForm):
    form_type = 'Expansion'
    class Meta:
        model = Expansion
        fields = ['title', 'description', 'lore']
    def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.fields['description'].widget.attrs.update({
                'rows': '2'
                })
            self.fields['lore'].widget.attrs.update({
                'rows': '2',
                'placeholder': 'Enter any thematic text here...'
                })

class PostImportForm(forms.ModelForm):
    form_type = 'Component'
    expansion = forms.ModelChoiceField(
        queryset=Expansion.objects.all(),  # Default empty queryset
        required=False
    )
    class Meta:
        model = Post
        fields = ['picture', 'title', 'expansion', 'lore', 'description', 'artist', 'bgg_link', 'tts_link', 'ww_link', 'wr_link', 'pnp_link']
    def clean(self):
            cleaned_data = super().clean()
            bgg_link = cleaned_data.get('bgg_link')
            tts_link = cleaned_data.get('tts_link')
            ww_link = cleaned_data.get('ww_link')
            wr_link = cleaned_data.get('wr_link')
            pnp_link = cleaned_data.get('pnp_link')
            url_validator = URLValidator()
            for url in [bgg_link, tts_link, ww_link, wr_link, pnp_link]:
                if url:  # Only validate if the field is filled
                    try:
                        url_validator(url)
                    except ValidationError:
                        raise ValidationError(f"The field '{url}' must be a valid URL.")
            return cleaned_data








class PostCreateForm(forms.ModelForm):
    form_type = 'Component'
    expansion = forms.ModelChoiceField(
        queryset=Expansion.objects.none(),  # Default empty queryset
        required=False
    )
    class Meta:
        model = Post
        fields = top_fields + bottom_fields
        labels = {
            'bgg_link': "Board Game Geek Post", 
            'tts_link': "Tabletop Simulator", 
            'ww_link': "Woodland Warriors Thread", 
            'wr_link': "Weird Root Thread", 
            'pnp_link': "Link to Print and Play Files",
            'stl_link': "Link to STL Files (if not in PNP)"
        }
    def __init__(self, *args, user=None, expansion=None, **kwargs):
        
        super().__init__(*args, **kwargs)
        # If a user is provided, filter the queryset for the `expansion` field
        if user:
            self.fields['expansion'].queryset = Expansion.objects.filter(designer=user.profile)

        # If a specific expansion is provided, add it to the queryset
        if expansion:
            # Ensure expansion is a single object, otherwise handle accordingly
            if isinstance(expansion, Expansion):
                self.fields['expansion'].queryset |= Expansion.objects.filter(id=expansion.id)
                if expansion.designer != user.profile:
                    self.fields['expansion'].disabled = True  # Disable the field
            

        # If no expansions exist in the queryset, hide the field
        if not self.fields['expansion'].queryset.exists():
            del self.fields['expansion']
        
        self.fields['lore'].widget.attrs.update({
            'rows': '2',
            'placeholder': 'Enter any thematic text here...'
        })
        self.fields['description'].widget.attrs.update({
            'rows': '2'
            })
        
        post_instance = kwargs.pop('instance', None)



        # If not admin user the designer will be the active user
        # Admin users can create posts for any user.
        if not user.profile.admin:
            self.fields['designer'].queryset = self.fields['designer'].queryset.filter(id=user.profile.id)
        else:
            self.fields['designer'].queryset = self.fields['designer'].queryset.filter(
            Q(id=user.profile.id) | Q(group="O") | Q(group="P") | Q(group="B")
        )
        if not post_instance:
            self.fields['designer'].initial = user.profile.id

        self.fields['designer'].label = "Designer (Admin Only)"
        # Hide the designer field for non-admin users
        if not user.profile.admin:
            self.fields.pop('designer', None)  # Remove designer field entirely


    def clean(self):
            cleaned_data = super().clean()
            bgg_link = cleaned_data.get('bgg_link')
            tts_link = cleaned_data.get('tts_link')
            ww_link = cleaned_data.get('ww_link')
            wr_link = cleaned_data.get('wr_link')
            pnp_link = cleaned_data.get('pnp_link')
            # designer = cleaned_data.get('designer')

            # # # If designer is None, set it to the current user's profile
            # # if not designer and self.user:
            # #     designer = self.user.profile

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
    picture = forms.ImageField(
        label='Art',  # Set the label for the picture field
        required=False
    )
    board_image = forms.ImageField(
        label='Map',  # Set the label for the picture field
        required=False
    )
    class Meta(PostCreateForm.Meta):  # Inherit Meta from PostCreateForm
        model = Map  # Specify the model to be Map
        fields = top_fields + ['clearings', 'fixed_clearings', 'board_image', 'based_on'] + bottom_fields

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

class MapImportForm(PostImportForm):  # Inherit from PostCreateForm
    class Meta(PostImportForm.Meta):  # Inherit Meta from PostCreateForm
        model = Map  # Specify the model to be Map
        fields = top_fields + ['clearings', 'date_posted', 'designer', 'official', 'stable', 'expansion'] + bottom_fields



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
    card_image = forms.ImageField(
        label='Card Back',  # Set the label for the card_image field
        required=False
    )
    picture = forms.ImageField(
        label='Card Art',  # Set the label for the picture field
        required=False
    )
    class Meta(PostCreateForm.Meta):  # Inherit Meta from PostCreateForm
        model = Deck  # Specify the model to be Deck
        fields = top_fields + ['card_total', 'card_image', 'based_on'] + bottom_fields

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

class DeckImportForm(PostImportForm):
    class Meta(PostImportForm.Meta):  # Inherit Meta from PostCreateForm
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
    picture = forms.ImageField(
        label='Landmark Art',  # Set the label for the picture field
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
    animal = forms.CharField(required=True)
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
        queryset=Post.objects.filter(component__in=['Faction', 'Hireling']),
        required=False
    )
    picture = forms.ImageField(
        label='Character Art',  # Set the label for the picture field
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
    animal = forms.CharField(required=True)
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
    picture = forms.ImageField(
        label='Character Art',  # Set the label for the picture field
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

class VagabondImportForm(PostImportForm): 
    class Meta(PostImportForm.Meta):  # Inherit Meta from PostCreateForm
        model = Vagabond  # Specify the model to be Vagabond
        fields = top_fields + ['animal', 'stable', 'official', 'designer', 'expansion',
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
    STYLE_WIDGET = forms.RadioSelect()
    title = forms.CharField(
        label='Faction Name',
        required=True
    )
    animal = forms.CharField(required=True)
    TYPE_CHOICES = [
        ('I', 'Insurgent'),
        ('M', 'Militant'),
    ]

    def create_style_choice_field(field_name):
        STYLE_CHOICES = [
            ('L', 'Low'),
            ('M', 'Moderate'),
            ('H', 'High'),
        ]
        return forms.ChoiceField(
            choices=STYLE_CHOICES, initial="M", 
            widget=forms.RadioSelect(), required=True, label=field_name
        )
    type = forms.ChoiceField(
        choices=TYPE_CHOICES, initial='I',
        widget=forms.RadioSelect(),
        required=True
    )
    reach = forms.IntegerField(min_value=1, max_value=10)
    complexity = create_style_choice_field('Complexity')
    card_wealth = create_style_choice_field('Card Wealth')
    aggression = create_style_choice_field('Aggression')
    crafting_ability = create_style_choice_field('Crafting Ability')
    # complexity = forms.ChoiceField(choices=STYLE_CHOICES, initial="M", widget=STYLE_WIDGET, required=True)
    # card_wealth = forms.ChoiceField(choices=STYLE_CHOICES, initial="M", widget=STYLE_WIDGET, required=True)
    # aggression = forms.ChoiceField(choices=STYLE_CHOICES, initial="M", widget=STYLE_WIDGET, required=True)
    # crafting_ability = forms.ChoiceField(choices=STYLE_CHOICES, initial="M", widget=STYLE_WIDGET, required=True)
    based_on = forms.ModelChoiceField(
        queryset=Post.objects.filter(component__in=['Faction']),
        required=False
    )
    card_image = forms.ImageField(
        label='ADSET Card',  # Set the label for the card_image field
        required=False
    )
    picture = forms.ImageField(
        label='Character Art',  # Set the label for the picture field
        required=False
    )
    small_icon = forms.ImageField(
        label='Icon (Meeple or Relationship Marker)',  # Set the label for the picture field
        required=False
    )
    class Meta(PostCreateForm.Meta): 
        model = Faction 
        fields = top_fields + ['small_icon', 'type', 'reach', 'animal', 'based_on',  'complexity', 'card_wealth', 
                               'aggression', 'crafting_ability', 'card_image', 'board_image'] + bottom_fields

    def clean_reach_and_type(self, cleaned_data):
        reach = cleaned_data.get('reach')
        type = cleaned_data.get('type')

        if type == 'I' and reach > 6:
            raise ValidationError('Reach Score does not match Type selected. Either decrease Reach or select "Militant"')
        elif type == 'M' and reach < 6:
            raise ValidationError('Reach Score does not match Type selected. Either increase Reach or select "Insurgent"')

    def clean_title_uniqueness(self, cleaned_data):
        title = cleaned_data.get('title')

        if Faction.objects.exclude(id=self.instance.id).filter(title__iexact=title).exists():
            raise ValidationError(f'A faction with the name "{title}" already exists. Please choose a different name.')

    def clean(self):
        cleaned_data = super().clean()
        self.clean_reach_and_type(cleaned_data)
        self.clean_title_uniqueness(cleaned_data)
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



class FactionImportForm(PostImportForm):
    reach = forms.IntegerField(min_value=0, max_value=10)
    class Meta(PostImportForm):
        model = Faction 
        fields = top_fields + ['small_icon', 'official', 'type', 'reach', 'animal',  'complexity', 'card_wealth', 
                               'aggression', 'crafting_ability', 'designer', 'expansion', 'stable', 'date_posted'] + bottom_fields

    def __init__(self, *args, **kwargs):
            faction_instance = kwargs.pop('instance', None)
            super().__init__(*args, **kwargs)

            # Set the initial value for small_icon if the instance exists
            if faction_instance and faction_instance.small_icon:
                self.fields['small_icon'].initial = faction_instance.small_icon


class ClockworkCreateForm(PostCreateForm):  # Inherit from PostCreateForm
    form_type = 'Clockwork Faction'
    title = forms.CharField(
        label='Clockwork Faction Name',
        required=True
    )
    animal = forms.CharField(required=True)

    based_on = forms.ModelChoiceField(
        queryset=Post.objects.filter(component__in=['Faction']),
        required=False
    )
    picture = forms.ImageField(
        label='Character Art',  # Set the label for the picture field
        required=False
    )
    small_icon = forms.ImageField(
        label='Icon (Meeple or Relationship Marker)',  # Set the label for the picture field
        required=False
    )
    class Meta(PostCreateForm.Meta): 
        model = Faction 
        fields = top_fields + ['small_icon', 'animal', 'based_on', 'board_image'] + bottom_fields

    def clean_title_uniqueness(self, cleaned_data):
        title = cleaned_data.get('title')

        if Faction.objects.exclude(id=self.instance.id).filter(title__iexact=title).exists():
            raise ValidationError(f'A faction with the name "{title}" already exists. Please choose a different name.')

    def clean(self):
        cleaned_data = super().clean()
        self.clean_title_uniqueness(cleaned_data)
        return cleaned_data

    
    def __init__(self, *args, **kwargs):
        # Check if an instance is being created or updated
        instance = kwargs.get('instance', None)
        super().__init__(*args, **kwargs)

        # Set the default value for the 'clockwork' field here
        if not self.instance.pk:  # Only set it for new instances (not when updating)
            self.instance.type = "C"

        self.fields['description'].widget.attrs.update({
            'placeholder': 'Give a brief explanation on how to use this Clockwork Faction...'
            })
        if instance:
            # Exclude the current instance from the queryset
            self.fields['based_on'].queryset = self.fields['based_on'].queryset.exclude(id=instance.id)



class PieceImportForm(forms.ModelForm):
    class Meta:
        model = Piece
        fields = ['name', 'quantity', 'description', 'suited', 'parent', 'type']


class PieceForm(forms.ModelForm):
    class Meta:
        model = Piece
        fields = ['name', 'quantity', 'suited', 'id']

    # Override clean_quantity method for custom validation
    def clean_quantity(self):
        quantity = self.cleaned_data.get('quantity')

        if quantity > 36:
            raise ValidationError('Quantity cannot be greater than 36.')
        
        return quantity