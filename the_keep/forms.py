import json
from django import forms
from .models import (
    Post, Map, Deck, Vagabond, Hireling, Landmark, Faction,
    Piece, Expansion, Tweak, PNPAsset, ColorChoices, PostTranslation,
    LawGroup, Law, FAQ
)
from the_gatehouse.models import Profile, Language
from the_keep.utils import generate_abbreviation_choices
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext as _

with open('/etc/config.json') as config_file:
    config = json.load(config_file)
top_fields = ['designer', 'official', 'in_root_digital', 'title', 'expansion', 'status', 'version']
bottom_fields = ['lore', 'description', 'leder_games_link', 'bgg_link', 'tts_link', 'ww_link', 'wr_link', 'fr_link', 'pnp_link', 'stl_link', 'rootjam_link', 'artist', 'art_by_kyle_ferrin', 'language']


class PostSearchForm(forms.ModelForm):
    search_term = forms.CharField(required=True, max_length=100)

class ExpansionCreateForm(forms.ModelForm):
    form_type = 'Expansion'
    class Meta:
        model = Expansion
        fields = ['title', 'picture', 'description', 'lore', 'bgg_link', 'tts_link', 'ww_link', 'wr_link', 'fr_link', 'pnp_link', 'stl_link', 'open_roster', 'end_date']
        labels = {
            'bgg_link': "Board Game Geek Post", 
            'tts_link': "Tabletop Simulator", 
            'ww_link': "Woodland Warriors Thread", 
            'wr_link': "Weird Root Thread", 
            'fr_link': "French Root Thread",
            'pnp_link': "Link to Print and Play Files",
            'stl_link': "Link to STL Files (if not in PNP)",
            'rootjam_link': "Link to itch.io RootJam",
            'open_roster': "Allow Others to Contribute",
            'end_date': "Close Date",
        }
    def __init__(self, user=None, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.fields['description'].widget.attrs.update({
                'rows': '2'
                })
            self.fields['lore'].widget.attrs.update({
                'rows': '2',
                'placeholder': 'Enter any thematic text here...'
                })
            # Add jQuery UI Datepicker for 'end_date'
            self.fields['end_date'].widget.attrs.update({'class': 'datepicker'}) 
            if not user.profile.admin:
                self.fields.pop('open_roster', None)
                self.fields.pop('end_date', None)
            # Remove Weird Root link option if not in Weird Root
            if not user.profile.in_weird_root:
                self.fields.pop('wr_link', None)
            # Remove French Root link option if not in French Root
            if not user.profile.in_french_root:
                self.fields.pop('fr_link', None)

    def clean(self):
            cleaned_data = super().clean()
            bgg_link = cleaned_data.get('bgg_link')
            tts_link = cleaned_data.get('tts_link')
            ww_link = cleaned_data.get('ww_link')
            wr_link = cleaned_data.get('wr_link')
            fr_link = cleaned_data.get('fr_link')
            pnp_link = cleaned_data.get('pnp_link')
            leder_games_link = cleaned_data.get('leder_games_link')
            rootjam_link = cleaned_data.get('rootjam_link')
            # Validate URLs
            url_validator = URLValidator()
            for url in [bgg_link, tts_link, ww_link, wr_link, pnp_link, leder_games_link, rootjam_link]:
                if url:  # Only validate if the field is filled
                    try:
                        url_validator(url)
                    except ValidationError:
                        raise ValidationError(f"The field '{url}' must be a valid URL.")
            if ww_link and not f"discord.com/channels/{config['WW_GUILD_ID']}" in ww_link:
                raise ValidationError(f"Link to Woodland Warriors is not a valid thread")
            if wr_link and not f"discord.com/channels/{config['WR_GUILD_ID']}" in wr_link:
                raise ValidationError(f"Link to Weird Root is not a valid thread")
            if fr_link and not f"discord.com/channels/{config['FR_GUILD_ID']}" in fr_link:
                raise ValidationError(f"Link to French Root is not a valid thread")
            if bgg_link and not "boardgamegeek.com/thread/" in bgg_link:
                raise ValidationError('Link to Board Game Geek is not a valid thread')
            if tts_link and not "steamcommunity.com/sharedfiles/" in tts_link:
                raise ValidationError('Link to Tabletop Simulator is not a valid shared file')
            if leder_games_link and not "ledergames.com/products" in leder_games_link:
                    raise ValidationError('Link to Leder Games is not a valid product')
            if pnp_link and not "dropbox.com" in pnp_link and not "drive.google.com" in pnp_link and not "docs.google.com" in pnp_link:
                    raise ValidationError('PNP links must be to Dropbox or Google Drive')
            if rootjam_link and not "itch.io/" in rootjam_link:
                    raise ValidationError('Link to RootJam page is not a valid itch.io page')
            return cleaned_data

class PostImportForm(forms.ModelForm):
    form_type = 'Component'
    expansion = forms.ModelChoiceField(
        queryset=Expansion.objects.all(),  # Default empty queryset
        required=False
    )
    class Meta:
        model = Post
        fields = ['picture', 'title','in_root_digital', 'expansion', 'lore', 'description', 'artist', 'bgg_link', 'tts_link', 'ww_link', 'wr_link', 'pnp_link', 'stl_link']
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



class TranslationCreateForm(forms.ModelForm):
    class Meta:
        model = PostTranslation
        fields = ['language', 'translated_title', 
                  'translated_lore', 'translated_description', 'translated_animal',
                  'ability', 'ability_description',
                  'translated_board_image', 'translated_board_2_image',
                  'translated_card_image', 'translated_card_2_image', 
                  'bgg_link', 'tts_link', 'pnp_link',
                  'designer', 'version'
                  ]
        labels = {
            'designer': 'Translated By',
            'translated_title': 'Title',
            'translated_lore': 'Lore',
            'translated_description': 'Description',
            'translated_animal': 'Animal',
            'translated_board_image': 'Board Image', 
            'translated_board_2_image': 'Board Back',
            'translated_card_image': 'Card Image', 
            'translated_card_2_image': 'Second Card Image',
            'version': 'Translation Version (Optional)',
            'bgg_link': "Language Specific Board Game Geek Post", 
            'tts_link': "Language Specific TTS Link", 
            'pnp_link': "Language Specific Print and Play Link",
        }
        translated_board_image = forms.ImageField(required=False)
        translated_board_2_image = forms.ImageField(required=False)
        translated_card_image = forms.ImageField(required=False)
        translated_card_2_image = forms.ImageField(required=False)

    def __init__(self, *args, user=None, post=None, **kwargs):
        
        super().__init__(*args, **kwargs)
        
        self.post = post
        # If we are updating an existing translation, make the language field read-only
        if self.instance and self.instance.pk:
            # Make the 'language' field read-only
            self.fields['language'].disabled = True
        else:
        # Otherwise find any available languages
            used_languages = PostTranslation.objects.filter(post=post).values_list('language', flat=True)
            if post.language:
                excluded_languages = list(used_languages) + [post.language.id]  # Exclude already used languages + post's language
            else:
                excluded_languages = list(used_languages)
            self.fields['language'].queryset = Language.objects.exclude(id__in=excluded_languages)
            # Set the initial translator as the designer
            self.fields['designer'].initial = user
        
        match post.component:
            case 'Faction':
                self.fields['translated_board_image'].label = "Faction Board"
                self.fields['translated_board_2_image'].label = "Faction Board Back"
                self.fields['translated_card_image'].label = "Adset Card"
                self.fields['translated_card_2_image'].label = "Misc Card"
            case 'Map':
                self.fields['translated_board_image'].label = "Map"
                self.fields['translated_board_2_image'].label = "Misc Board"
                self.fields['translated_card_image'].label = "Setup Card"
                self.fields['translated_card_2_image'].label = "Misc Card"
            case 'Deck':
                self.fields['translated_board_image'].label = "Suit Distribution"
                self.fields['translated_board_2_image'].label = "Misc Board"
                self.fields['translated_card_image'].label = "Card Back"
                self.fields['translated_card_2_image'].label = "Misc Card"
            case 'Vagabond':
                self.fields['translated_board_image'].label = "Board"
                self.fields['translated_board_2_image'].label = "Misc Board"
                self.fields['translated_card_image'].label = "Vagabond Card"
                self.fields['translated_card_2_image'].label = "Misc Card"
            case 'Hireling':
                self.fields['translated_board_image'].label = "Hireling Card"
                self.fields['translated_board_2_image'].label = "Misc Board"
                self.fields['translated_card_image'].label = "Card"
                self.fields['translated_card_2_image'].label = "Misc Card"
            case 'Landmark':
                self.fields['translated_board_image'].label = "Board"
                self.fields['translated_board_2_image'].label = "Misc Board"
                self.fields['translated_card_image'].label = "Landmark Card"
                self.fields['translated_card_2_image'].label = "Setup Card"
            case 'Clockwork':
                self.fields['translated_board_image'].label = "FactionBoard"
                self.fields['translated_board_2_image'].label = "Misc Board"
                self.fields['translated_card_image'].label = "Card"
                self.fields['translated_card_2_image'].label = "Misc Card"
            case 'Tweak':
                self.fields['translated_board_image'].label = "Board"
                self.fields['translated_board_2_image'].label = "Misc Board"
                self.fields['translated_card_image'].label = "Card"
                self.fields['translated_card_2_image'].label = "Misc Card"

        self.fields['translated_title'].help_text = post.title

        self.fields['translated_lore'].widget.attrs.update({
                'rows': '2',
            })
        if not post.lore:
            self.fields['translated_lore'].help_text = "None"
        else:
            self.fields['translated_lore'].help_text = post.lore

        self.fields['translated_description'].widget.attrs.update({
                'rows': '2',
            })
        if not post.description:
            self.fields['translated_description'].help_text = "None"
        else:
            self.fields['translated_description'].help_text = post.description

        if not post.component == 'Vagabond':
            self.fields.pop('ability', None)
            self.fields.pop('ability_description', None)
        else:
            vagabond_instance = Vagabond.objects.get(id=post.id)
            self.fields['ability_description'].widget.attrs.update({
                'rows': '2',
            })
            self.fields['ability'].help_text = vagabond_instance.ability
            self.fields['ability_description'].help_text = vagabond_instance.ability_description

        if not post.animal:
            self.fields.pop('translated_animal', None)
        else:
            self.fields['translated_animal'].widget.attrs.update({
                'rows': '2',
            })
            self.fields['translated_animal'].help_text = post.animal

        if not post.board_image:
            self.fields['translated_board_image'].help_text = "None"
        if not post.board_2_image:
            self.fields['translated_board_2_image'].help_text = "None"
        if not post.card_image:
            self.fields['translated_card_image'].help_text = "None"
        if not post.card_2_image:
            self.fields['translated_card_2_image'].help_text = "None"

        # Define which components should ignore which image fields
        component_field_map = {
            'translated_board_image': {'Vagabond', 'Deck', 'Landmark'},
            'translated_board_2_image': {'Vagabond', 'Map', 'Deck', 'Landmark', 'Clockwork', 'Hireling', 'Tweak'},
            'translated_card_image': {'Clockwork', 'Hireling'},
            'translated_card_2_image': {'Faction', 'Clockwork', 'Vagabond', 'Deck', 'Hireling', 'Tweak'},
        }

        # For each field, check if the post.component is relevant and if the corresponding image is missing
        for field_name, components in component_field_map.items():
            if post.component in components:
                # derive original image field name from translated field
                original_field_name = field_name.replace('translated_', '')
                if not getattr(post, original_field_name):
                    self.fields.pop(field_name, None)


    def clean(self):
            cleaned_data = super().clean()
            # post = cleaned_data.get('post')
            post = self.post
            language = cleaned_data.get('language')
            bgg_link = cleaned_data.get('bgg_link')
            tts_link = cleaned_data.get('tts_link')
            pnp_link = cleaned_data.get('pnp_link')
            title = cleaned_data.get('translated_title')

            # Check if the same name already exists for another post or translation of another post
            if Post.objects.exclude(id=post.id).filter(title__iexact=title, component=post.component).exists() or PostTranslation.objects.exclude(Q(id=self.instance.id)|Q(post=post)).filter(translated_title__iexact=title, post__component=post.component).exists():
                raise ValidationError(f'A {post.component} with the name "{title}" already exists. Please choose a different name.')


            url_validator = URLValidator()
            for url in [bgg_link, tts_link, pnp_link]:
                if url:  # Only validate if the field is filled
                    try:
                        url_validator(url)
                    except ValidationError:
                        raise ValidationError(f"The field '{url}' must be a valid URL.")
            if bgg_link and not "boardgamegeek.com/thread/" in bgg_link:
                raise ValidationError('Link to Board Game Geek is not a valid thread')
            if tts_link and not "steamcommunity.com/sharedfiles/" in tts_link:
                raise ValidationError('Link to Tabletop Simulator is not a valid shared file')
            if pnp_link and not "dropbox.com" in pnp_link and not "drive.google.com" in pnp_link and not "docs.google.com" in pnp_link:
                    raise ValidationError('PNP links must be to Dropbox or Google Drive')


            # Add custom validation if needed (e.g., ensure a translation doesn't already exist for this post and language)
            if post and language:
                if self.instance.pk:
                  if PostTranslation.objects.filter(post=post, language=language).exclude(id=self.instance.id).exists():
                        raise forms.ValidationError("A translation for this post in this language already exists.")
                else:
                    if PostTranslation.objects.filter(post=post, language=language).exists():
                        raise forms.ValidationError("A translation for this post in this language already exists.")
                
            # List of the fields you want to check
            required_fields = [
                'translated_title', 'translated_lore', 'translated_description', 'translated_animal',
                'translated_board_image', 'translated_board_2_image',
                'translated_card_image', 'translated_card_2_image'
            ]
            
            # Check if at least one of the required fields has data
            if not any(cleaned_data.get(field) for field in required_fields):
                raise ValidationError("At least one of the following fields must be filled: Title, Lore, Description, Animal, Board Image, Board Back, Card Image, Card Back.")
            

            return cleaned_data


class PostCreateForm(forms.ModelForm):
    form_type = 'Component'
    expansion = forms.ModelChoiceField(
        queryset=Expansion.objects.none(),  # Default empty queryset
        required=False
    )
    STATUS_CHOICES = [
        ('1', 'Stable'),
        ('2', 'Testing'),
        ('3', 'Development'),
        ('4', 'Inactive'),
    ]
    status = forms.ChoiceField(
        choices=STATUS_CHOICES, initial="3",
        required=True,
        help_text=_('Set the current status. Set status to "Testing" once a game has been recorded. Once thoroughly playtested the status can be set to "Stable". Set to "Inactive" if you are no longer working on this project.')
    )
    artist = forms.ModelChoiceField(
        queryset=Profile.objects.all(),
        required=False
    )
    language = forms.ModelChoiceField(
        queryset=Language.objects.all(),
        empty_label=None
    )
    class Meta:
        model = Post
        fields = top_fields + bottom_fields
        labels = {
            'bgg_link': "Board Game Geek Post", 
            'tts_link': "Tabletop Simulator", 
            'ww_link': "Woodland Warriors Thread", 
            'wr_link': "Weird Root Thread", 
            'fr_link': "French Root Thread",
            'pnp_link': "Print and Play Files",
            'stl_link': "STL Files (if not in PNP)",
            'rootjam_link': "RootJam itch.io Entry",
            'leder_games_link': "Leder Games",
            'art_by_kyle_ferrin': "Art by Kyle Ferrin",
            'version': "Version (Optional)",
        }
    def __init__(self, *args, user=None, expansion=None, **kwargs):
        
        super().__init__(*args, **kwargs)
        # If a user is provided, filter the queryset for the `expansion` field
        if user:
            self.fields['expansion'].queryset = Expansion.objects.filter(Q(designer=user.profile)|Q(open_roster=True, end_date__gte=timezone.now()))

        # If a specific expansion is provided, add it to the queryset
        if expansion:
            # Ensure expansion is a single object, otherwise handle accordingly
            if isinstance(expansion, Expansion):
                self.fields['expansion'].queryset |= Expansion.objects.filter(id=expansion.id)
                if expansion.designer != user.profile and (not expansion.open_roster or (expansion.end_date and expansion.end_date < timezone.now())):
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

        if post_instance and post_instance.title and not user.profile.admin:
            self.fields['title'].disabled = True

        user_language = user.profile.language if user.profile.language else None
        if user_language:
            self.fields['language'].initial = user_language
        else:
            # If no language preference, fallback to English
            self.fields['language'].initial = Language.objects.get(code='en')

        # Conditionally exclude artists for non admin users
        exclude_profiles = ['kyleferrin', 'leder games', 'joshuayearsley', 'patrickleder']
        
        # If not admin user the designer will be the active user
        # Admin users can create posts for any user.
        if not user.profile.admin:
            self.fields['artist'].queryset = Profile.objects.exclude(discord__in=exclude_profiles)
            self.fields['designer'].queryset = self.fields['designer'].queryset.filter(id=user.profile.id)
            # Limit language choices to English and the user's language
            if user_language:
                self.fields['language'].queryset = Language.objects.filter(
                    pk__in=[user_language.pk, Language.objects.get(code='en').pk]
                )
            else:
                # If no language only allow English
                self.fields['language'].queryset = Language.objects.filter(code='en')
        else:
            self.fields['designer'].queryset = self.fields['designer'].queryset.filter(
            Q(id=user.profile.id) | Q(group="O") | Q(group="P") | Q(group="B") | Q(group='E')
            )
        # Remove language if it already exists
        if post_instance:
            self.fields.pop('language', None)

        


        if not post_instance:
            self.fields['designer'].initial = user.profile.id

        self.fields['designer'].label = "Designer"

        # Hide the designer field for non-admin users

        if not user.profile.admin or post_instance:
            self.fields.pop('designer', None)  # Remove designer field entirely
        if not user.profile.admin:
            self.fields.pop('official', None)  # Remove official field entirely
            self.fields.pop('in_root_digital', None)  # Remove designer field entirely
            self.fields.pop('leder_games_link', None)
        else:
            self.fields['official'].label = "Official"
            self.fields['in_root_digital'].label = "Playable In DWD Root Digital"
        
        # Remove Weird Root link option if not in Weird Root
        if not user.profile.in_weird_root:
            self.fields.pop('wr_link', None)

        # Remove French Root link option if not in French Root
        if not user.profile.in_french_root:
            self.fields.pop('fr_link', None)

        # Check if the post has any related plays and adjust the status choices accordingly
        if post_instance and post_instance.plays() > 0:
            # Add status '2' (Testing) only if the post has plays
            # self.fields['status'].choices.append(('2', 'Testing'))
            pass
        else:
            # Remove '2' (Testing) if no plays exist
            self.fields['status'].choices = [choice for choice in self.fields['status'].choices if choice[0] != '2']

        if post_instance and post_instance.status == '1':
            # self.fields['status'].choices.append(('1', 'Stable'))
            pass
        else:
            # Remove '1' (Stable) if not already stable
            self.fields['status'].choices = [choice for choice in self.fields['status'].choices if choice[0] != '1']


    def clean(self):
            cleaned_data = super().clean()
            bgg_link = cleaned_data.get('bgg_link')
            tts_link = cleaned_data.get('tts_link')
            ww_link = cleaned_data.get('ww_link')
            wr_link = cleaned_data.get('wr_link')
            fr_link = cleaned_data.get('fr_link')
            pnp_link = cleaned_data.get('pnp_link')
            rootjam_link = cleaned_data.get('rootjam_link')
            leder_games_link = cleaned_data.get('leder_games_link')
            official = cleaned_data.get('official')
            in_root_digital = cleaned_data.get('in_root_digital')
            # designer = cleaned_data.get('designer')

            # # # If designer is None, set it to the current user's profile
            # # if not designer and self.user:
            # #     designer = self.user.profile

            # Check that at least one of the links are filled
            if not any([bgg_link, tts_link, ww_link, wr_link, fr_link, leder_games_link, pnp_link, rootjam_link]):
                raise ValidationError("Please include a link to one of the following: a Board Game Geek post, Steam Community Mod, PNP File or Discord Thread.")
            # Validate URLs
            url_validator = URLValidator()
            for url in [bgg_link, tts_link, ww_link, wr_link, fr_link, pnp_link, leder_games_link, rootjam_link]:
                if url:  # Only validate if the field is filled
                    try:
                        url_validator(url)
                    except ValidationError:
                        raise ValidationError(f"The field '{url}' must be a valid URL.")
            if ww_link and not f"discord.com/channels/{config['WW_GUILD_ID']}" in ww_link:
                raise ValidationError(f"Link to Woodland Warriors is not a valid thread")
            if wr_link and not f"discord.com/channels/{config['WR_GUILD_ID']}" in wr_link:
                raise ValidationError(f"Link to Weird Root is not a valid thread")
            if fr_link and not f"discord.com/channels/{config['FR_GUILD_ID']}" in fr_link:
                raise ValidationError(f"Link to French Root is not a valid thread")
            if bgg_link and not "boardgamegeek.com/thread/" in bgg_link:
                raise ValidationError('Link to Board Game Geek is not a valid thread')
            if tts_link and not "steamcommunity.com/sharedfiles/" in tts_link:
                raise ValidationError('Link to Tabletop Simulator is not a valid shared file')
            if pnp_link and not "dropbox.com" in pnp_link and not "drive.google.com" in pnp_link and not "docs.google.com" in pnp_link:
                    raise ValidationError('PNP links must be to Dropbox or Google Drive')
            if rootjam_link and not "itch.io/" in rootjam_link:
                    raise ValidationError('Link to RootJam entry is not a valid itch.io page')
            if leder_games_link:
                if not "ledergames.com/products" in leder_games_link:
                    raise ValidationError('Link to Leder Games is not a valid product')
                if not official:
                    raise ValidationError('Only Official products can be linked to Leder Games')
            if in_root_digital and not official:
                raise ValidationError('Only official products can be included in Root Digital')
            
            return cleaned_data



class MapCreateForm(PostCreateForm):  # Inherit from PostCreateForm
    form_type = 'Map'
    title = forms.CharField(
        label=_('Map Name'),
        required=True
    )
    clearings = forms.IntegerField(
        label=_('Number of Clearings'), initial=12,
        min_value=1,  # Add validation for minimum value if necessary
        required=True
    )
    forests = forms.IntegerField(
        label=_('Number of Forests'),
        required=False
    )
    fixed_clearings = forms.BooleanField(
        label=_('This map has fixed suits for each clearing by default'), initial=False, required=False
    )
    based_on = forms.ModelChoiceField(
        queryset=Post.objects.filter(component__in=['Map']),
        required=False
    )
    # picture = forms.ImageField(
    #     label='Art',  # Set the label for the picture field
    #     required=False
    # )
    board_image = forms.ImageField(
        label=_('Map'),  # Set the label for the picture field
        required=True
    )
    card_image = forms.ImageField(
        label=_('Card'), 
        required=False
    )
    card_2_image = forms.ImageField(
        label=_('Card Back'), 
        required=False
    )
    class Meta(PostCreateForm.Meta):  # Inherit Meta from PostCreateForm
        model = Map  # Specify the model to be Map
        fields = top_fields + ['clearings', 'forests', 'fixed_clearings', 'board_image', 'card_image', 'card_2_image', 'based_on'] + bottom_fields

    def __init__(self, *args, **kwargs):
        # Check if an instance is being created or updated
        instance = kwargs.get('instance', None)
        super().__init__(*args, **kwargs)
        self.fields['description'].widget.attrs.update({
            'placeholder': 'Give a brief explanation of how to play on this Map...'
            })
        if instance:
            # Exclude the current instance from the queryset
            base_queryset = self.fields['based_on'].queryset.exclude(id=instance.id)
    
            if instance.based_on:
                # Ensure the instance's current based_on is included
                base_queryset = base_queryset | Post.objects.filter(id=instance.based_on.id)

            self.fields['based_on'].queryset = base_queryset

    def clean(self):
        cleaned_data = super().clean()
        title = cleaned_data.get('title')
            # Check if the same name already exists
        if Map.objects.exclude(id=self.instance.id).filter(title__iexact=title).exists() or PostTranslation.objects.exclude(post__id=self.instance.id).filter(translated_title__iexact=title, post__component="Map").exists():
            raise ValidationError(f'A map with the name "{title}" already exists. Please choose a different name.')
        return cleaned_data

class MapImportForm(PostImportForm):  # Inherit from PostCreateForm
    class Meta(PostImportForm.Meta):  # Inherit Meta from PostCreateForm
        model = Map  # Specify the model to be Map
        fields = top_fields + ['picture', 'clearings', 'date_posted', 'designer', 'official', 'status', 'in_root_digital', 'expansion', 'leder_games_link'] + bottom_fields



class DeckCreateForm(PostCreateForm):  # Inherit from PostCreateForm
    form_type = 'Deck'
    title = forms.CharField(
        label=_('Deck Name'),
        required=True
    )
    card_total = forms.IntegerField(
        label=_('Card Count'), initial=54,
        min_value=1,  # Add validation for minimum value if necessary
        required=True
    )
    based_on = forms.ModelChoiceField(
        queryset=Post.objects.filter(component__in=['Deck']),
        required=False
    )
    card_image = forms.ImageField(
        label=_('Card Back'),  # Set the label for the card_image field
        required=True
    )
    # picture = forms.ImageField(
    #     label='Card Art',  # Set the label for the picture field
    #     required=False
    # )
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
            base_queryset = self.fields['based_on'].queryset.exclude(id=instance.id)
    
            if instance.based_on:
                # Ensure the instance's current based_on is included
                base_queryset = base_queryset | Post.objects.filter(id=instance.based_on.id)

            self.fields['based_on'].queryset = base_queryset

    def clean(self):
        cleaned_data = super().clean()
        title = cleaned_data.get('title')
            # Check if the same name already exists
        if Deck.objects.exclude(id=self.instance.id).filter(title__iexact=title).exists() or PostTranslation.objects.exclude(post__id=self.instance.id).filter(translated_title__iexact=title, post__component="Deck").exists():
            raise ValidationError(f'A deck with the name "{title}" already exists. Please choose a different name.')
        return cleaned_data

class DeckImportForm(PostImportForm):
    class Meta(PostImportForm.Meta):  # Inherit Meta from PostCreateForm
        model = Deck  # Specify the model to be Deck
        fields = top_fields + ['leder_games_link', 'card_total', 'date_posted', 'designer', 'official', 'in_root_digital', 'status'] + bottom_fields

class LandmarkImportForm(PostImportForm):  # Inherit from PostCreateForm
    class Meta(PostImportForm.Meta):  # Inherit Meta from PostCreateForm
        model = Landmark  # Specify the model to be Map
        fields = top_fields + ['leder_games_link', 'card_text', 'based_on', 'designer', 'official', 'status', 'in_root_digital', 'date_posted',] + bottom_fields


class LandmarkCreateForm(PostCreateForm):  # Inherit from PostCreateForm
    form_type = 'Landmark'
    title = forms.CharField(
        label=_('Landmark Name'),
        required=True
    )
    based_on = forms.ModelChoiceField(
        queryset=Post.objects.filter(component__in=['Landmark']),
        required=False
    )
    picture = forms.ImageField(
        label=_('Landmark Art'),  # Set the label for the picture field
        required=False
    )
    card_image = forms.ImageField(
        label=_('Landmark Card Front'),  # Set the label for the card_image field
        required=True
    )
    card_2_image = forms.ImageField(
        label=_('Card Back'),  # Set the label for the card_image field
        required=False
    )
    def __init__(self, *args, **kwargs):
        instance = kwargs.get('instance', None)
        super().__init__(*args, **kwargs)

        self.fields['card_text'].widget.attrs.update({
            'rows': '2',
            'placeholder': 'Text of the Landmark Card'
            })
        self.fields['description'].widget.attrs.update({
            'placeholder': 'Give a brief explanation on how to use this Landmark...'
            })
        if instance:
            # Exclude the current instance from the queryset
            base_queryset = self.fields['based_on'].queryset.exclude(id=instance.id)
            
            if instance.based_on:
                # Ensure the instance's current based_on is included
                base_queryset = base_queryset | Post.objects.filter(id=instance.based_on.id)

            self.fields['based_on'].queryset = base_queryset

    class Meta(PostCreateForm.Meta):  # Inherit Meta from PostCreateForm
        model = Landmark  # Specify the model to be Landmark
        fields = top_fields + ['picture', 'card_text', 'based_on', 'card_image', 'card_2_image'] + bottom_fields
    def clean(self):
        cleaned_data = super().clean()
        title = cleaned_data.get('title')
            # Check if the same name already exists
        if Landmark.objects.exclude(id=self.instance.id).filter(title__iexact=title).exists() or PostTranslation.objects.exclude(post__id=self.instance.id).filter(translated_title__iexact=title, post__component="Landmark").exists():
            raise ValidationError(f'A landmark with the name "{title}" already exists. Please choose a different name.')
        return cleaned_data

class TweakCreateForm(PostCreateForm):  # Inherit from PostCreateForm
    form_type = 'Tweak'
    title = forms.CharField(
        label=_('House Rule Name'),
        required=True
    )
    based_on = forms.ModelChoiceField(
        queryset=Post.objects.all(),
        required=False
    )
    picture = forms.ImageField(
        label=_('House Rule Art'),  # Set the label for the picture field
        required=False
    )
    def __init__(self, *args, **kwargs):
        instance = kwargs.get('instance', None)
        super().__init__(*args, **kwargs)

        self.fields['description'].widget.attrs.update({
            'placeholder': 'Give a brief explanation on how to use this Tweak...'
            })
        if instance:
            # Exclude the current instance from the queryset
            base_queryset = self.fields['based_on'].queryset.exclude(id=instance.id)
            
            if instance.based_on:
                # Ensure the instance's current based_on is included
                base_queryset = base_queryset | Post.objects.filter(id=instance.based_on.id)

            self.fields['based_on'].queryset = base_queryset

    class Meta(PostCreateForm.Meta):  # Inherit Meta from PostCreateForm
        model = Tweak  # Specify the model to be Tweak
        fields = top_fields + ['picture', 'based_on', 'card_image', 'board_image'] + bottom_fields
    def clean(self):
        cleaned_data = super().clean()
        title = cleaned_data.get('title')
            # Check if the same name already exists
        if Tweak.objects.exclude(id=self.instance.id).filter(title__iexact=title).exists() or PostTranslation.objects.exclude(post__id=self.instance.id).filter(translated_title__iexact=title, post__component="Tweak").exists():
            raise ValidationError(f'A tweak with the name "{title}" already exists. Please choose a different name.')
        return cleaned_data

class HirelingImportForm(PostImportForm):  # Inherit from PostCreateForm
    class Meta(PostImportForm.Meta):  # Inherit Meta from PostCreateForm
        model = Hireling  # Specify the model to be Map
        fields = top_fields + ['leder_games_link', 'animal', 'type', 'based_on', 'designer', 'official', 'status', 'in_root_digital', 'date_posted',] + bottom_fields



class HirelingCreateForm(PostCreateForm):  # Inherit from PostCreateForm
    form_type = 'Hireling'
    title = forms.CharField(
        label=_('Hireling Name'),
        required=True
    )
    animal = forms.CharField(required=True, max_length=35)
    TYPE_CHOICES = [
        ('P', _('Promoted')),
        ('D', _('Demoted')),
    ]
    type = forms.ChoiceField(
        choices=TYPE_CHOICES, initial="P",
        widget=forms.RadioSelect(),
        required=True
    )
    based_on = forms.ModelChoiceField(
        queryset=Post.objects.filter(component__in=['Faction', 'Hireling']),
        required=False,
        label=_("Based On (Faction or other Hireling):")
    )
    other_side = forms.ModelChoiceField(
        queryset=Hireling.objects.all(),
        required=False,
        label=_("Other Side")
    )
    picture = forms.ImageField(
        label=_('Character Art'),  # Set the label for the picture field
        required=False
    )
    color = forms.CharField(
        max_length=7,  # Color code length (e.g., #FFFFFF)
        widget=forms.TextInput(attrs={'type': 'color', 'class': 'form-control'}),
        required=False,
        label=_("Hireling Color")
    )
    color_group = forms.ChoiceField(
        choices=ColorChoices.choices,
        required=True
    )
    board_image = forms.ImageField(
        label=_('Hireling Card'),
        required=True
    )
    class Meta(PostCreateForm.Meta): 
        model = Hireling 
        fields = top_fields + ['picture', 'color', 'color_group', 'animal', 'type', 'other_side', 'based_on', 'board_image'] + bottom_fields

    def __init__(self, *args,  **kwargs):
        # Check if an instance is being created or updated
        instance = kwargs.get('instance', None)
        designer = kwargs.pop('designer', None)
        super().__init__(*args, **kwargs)
        self.fields['description'].widget.attrs.update({
            'placeholder': 'Give a brief explanation on how to use this Hireling...'
            })
        if instance:
            # Exclude the current instance from the queryset
            base_queryset = self.fields['based_on'].queryset.exclude(id=instance.id)
            
            if instance.based_on:
                # Ensure the instance's current based_on is included
                base_queryset = base_queryset | Post.objects.filter(id=instance.based_on.id)

            self.fields['based_on'].queryset = base_queryset
            # Prepare filter for 'other_side'
            other_side_filter = Q(designer=designer, status__lte=designer.view_status)
            
            # Include current other_side if it exists
            if instance.other_side:
                other_side_filter |= Q(id=instance.other_side.id)

            # Apply filter and exclude the current instance
            self.fields['other_side'].queryset = (
                self.fields['other_side'].queryset
                .filter(other_side_filter)
                .exclude(id=instance.id)
            )
        else: 
            self.fields['other_side'].queryset = self.fields['other_side'].queryset.filter(designer=designer, status__lte=designer.view_status)


    def clean(self):
        cleaned_data = super().clean()
        title = cleaned_data.get('title')
            # Check if the same name already exists
        if Hireling.objects.exclude(id=self.instance.id).filter(title__iexact=title).exists() or PostTranslation.objects.exclude(post__id=self.instance.id).filter(translated_title__iexact=title, post__component="Hireling").exists():
            raise ValidationError(f'A hireling with the name "{title}" already exists. Please choose a different name.')
        return cleaned_data

class VagabondCreateForm(PostCreateForm): 
    form_type = 'Vagabond'
    title = forms.CharField(
        label=_('Vagabond Name'),
        required=True
    )
    animal = forms.CharField(required=True, max_length=35)
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
        label=_('Character Art'),  # Set the label for the picture field
        required=False
    )
    card_image = forms.ImageField(
        label=_('Vagabond Card'),  # Set the label for the card_image field
        required=True
    )
    class Meta(PostCreateForm.Meta):  # Inherit Meta from PostCreateForm
        model = Vagabond  # Specify the model to be Vagabond
        fields = top_fields + ['picture', 'animal', 'based_on', 'card_image',
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
            base_queryset = self.fields['based_on'].queryset.exclude(id=instance.id)
            
            if instance.based_on:
                # Ensure the instance's current based_on is included
                base_queryset = base_queryset | Post.objects.filter(id=instance.based_on.id)

            self.fields['based_on'].queryset = base_queryset
    
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
            if Vagabond.objects.exclude(id=self.instance.id).filter(title__iexact=title).exists() or PostTranslation.objects.exclude(post__id=self.instance.id).filter(translated_title__iexact=title, post__component="Vagabond").exists():
                raise ValidationError(f'A vagabond with the name "{title}" already exists. Please choose a different name.')
            # Check that the VB doesn't have a wild amount of items
            if (starting_torch+starting_coins+starting_boots+starting_bag+starting_tea+starting_sword+starting_hammer+starting_crossbow) > 8:
                raise ValidationError("Please check the number of starting items (8 max). A Vagabond typically starts with 3-4 items.")
            return cleaned_data

class VagabondImportForm(PostImportForm): 
    class Meta(PostImportForm.Meta):  # Inherit Meta from PostCreateForm
        model = Vagabond  # Specify the model to be Vagabond
        fields = top_fields + ['animal', 'status', 'official', 'designer', 'expansion', 'in_root_digital',
                                'ability_item', 'ability', 'ability_description', 'leder_games_link',
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
        label=_('Faction Name'),
        required=True
    )
    animal = forms.CharField(required=True, max_length=35)
    TYPE_CHOICES = [
        ('U', _('Unknown')),
        ('I', _('Insurgent')),
        ('M', _('Militant')),
    ]

    def create_style_choice_field(field_name):
        STYLE_CHOICES = [
            ('N', _('None')),
            ('L', _('Low')),
            ('M', _('Moderate')),
            ('H', _('High')),
        ]
        return forms.ChoiceField(
            choices=STYLE_CHOICES, initial="N", 
            widget=forms.RadioSelect(), required=True, label=field_name
        )
    type = forms.ChoiceField(
        choices=TYPE_CHOICES, initial='U',
        widget=forms.RadioSelect(),
        required=True
    )
    reach = forms.IntegerField(initial=0, min_value=0, max_value=10)
    complexity = create_style_choice_field('Complexity')
    card_wealth = create_style_choice_field('Card Wealth')
    aggression = create_style_choice_field('Aggression')
    crafting_ability = create_style_choice_field('Crafting Ability')
    based_on = forms.ModelChoiceField(
        queryset=Post.objects.filter(component__in=['Faction']),
        required=False
    )
    card_image = forms.ImageField(
        label=_('ADSET Card'),  # Set the label for the card_image field
        required=False
    )
    picture = forms.ImageField(
        label=_('Character Art'),  # Set the label for the picture field
        required=False
    )
    board_image = forms.ImageField(
        label=_('Faction Board Front'),  # Set the label for the faction board field
        required=True
    )
    board_2_image = forms.ImageField(
        label=_('Faction Board Back'),  # Set the label for the faction board back field
        required=False
    )
    small_icon = forms.ImageField(
        label=_('Icon (Faction Head or Meeple)'),  # Set the label for the picture field
        required=False
    )
    color = forms.CharField(
        max_length=7,  # Color code length (e.g., #FFFFFF)
        widget=forms.TextInput(attrs={'type': 'color', 'class': 'form-control'}),
        required=False,
        label=_("Faction Color")
    )
    color_group = forms.ChoiceField(
        choices=ColorChoices.choices,
        required=True
    )
    class Meta(PostCreateForm.Meta): 
        model = Faction 
        fields = top_fields + ['picture', 'color', 'color_group', 'type', 'reach', 'animal', 'board_image', 'small_icon', 'card_image', 'board_2_image',  'complexity', 'card_wealth', 
                               'aggression', 'crafting_ability', 'based_on'] + bottom_fields

    def clean_reach_and_type(self, cleaned_data):
        reach = cleaned_data.get('reach')
        type = cleaned_data.get('type')
        if reach != 0:
            if type == 'I' and reach > 6:
                raise ValidationError('Reach Score does not match Type selected. Either decrease Reach or select "Militant"')
            elif type == 'M' and reach < 6:
                raise ValidationError('Reach Score does not match Type selected. Either increase Reach or select "Insurgent"')
            elif type == "U" and reach != 0:
                raise ValidationError('Reach Score does not match Type selected.')

    def clean_title_uniqueness(self, cleaned_data):
        title = cleaned_data.get('title')

        if Faction.objects.exclude(id=self.instance.id).filter(title__iexact=title).exists() or PostTranslation.objects.exclude(post__id=self.instance.id).filter(translated_title__iexact=title, post__component="Faction").exists() or PostTranslation.objects.exclude(post__id=self.instance.id).filter(translated_title__iexact=title, post__component="Clockwork").exists():
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
            base_queryset = self.fields['based_on'].queryset.exclude(id=instance.id)
            
            if instance.based_on:
                # Ensure the instance's current based_on is included
                base_queryset = base_queryset | Post.objects.filter(id=instance.based_on.id)

            self.fields['based_on'].queryset = base_queryset



class FactionImportForm(PostImportForm):
    reach = forms.IntegerField(min_value=0, max_value=10)
    class Meta(PostImportForm):
        model = Faction 
        fields = top_fields + ['small_icon', 'official', 'type', 'reach', 'animal',  
                               'in_root_digital', 'color', 'based_on', 'leder_games_link',
                               'complexity', 'card_wealth', 'aggression', 'crafting_ability',
                                'designer', 'expansion', 'status', 'date_posted'] + bottom_fields

    def __init__(self, *args, **kwargs):
            faction_instance = kwargs.pop('instance', None)
            super().__init__(*args, **kwargs)

            # Set the initial value for small_icon if the instance exists
            if faction_instance and faction_instance.small_icon:
                self.fields['small_icon'].initial = faction_instance.small_icon


class ClockworkCreateForm(PostCreateForm):  # Inherit from PostCreateForm
    form_type = 'Clockwork Faction'
    title = forms.CharField(
        label=_('Clockwork Faction Name'),
        required=True
    )
    animal = forms.CharField(required=True, max_length=35)

    based_on = forms.ModelChoiceField(
        queryset=Post.objects.filter(component__in=['Faction']),
        required=False
    )
    picture = forms.ImageField(
        label=_('Character Art'),  # Set the label for the picture field
        required=False
    )
    small_icon = forms.ImageField(
        label=_('Icon (Meeple or Relationship Marker)'),  # Set the label for the picture field
        required=False
    )
    color = forms.CharField(
        max_length=7,  # Color code length (e.g., #FFFFFF)
        widget=forms.TextInput(attrs={'type': 'color', 'class': 'form-control'}),
        required=False,
        label=_("Faction Color")
    )
    color_group = forms.ChoiceField(
        choices=ColorChoices.choices,
        required=True
    )
    board_image = forms.ImageField(
        label=_('Faction Board Front'),  # Set the label for the faction board field
        required=True
    )
    class Meta(PostCreateForm.Meta): 
        model = Faction 
        fields = top_fields + ['picture', 'color', 'color_group', 'small_icon', 'animal', 'based_on', 'board_image'] + bottom_fields

    def clean_title_uniqueness(self, cleaned_data):
        title = cleaned_data.get('title')

        if Faction.objects.exclude(id=self.instance.id).filter(title__iexact=title).exists() or PostTranslation.objects.exclude(post__id=self.instance.id).filter(translated_title__iexact=title, post__component="Faction").exists() or PostTranslation.objects.exclude(post__id=self.instance.id).filter(translated_title__iexact=title, post__component="Clockwork").exists():
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
            'placeholder': _('Give a brief explanation on how to use this Clockwork Faction...')
            })
        if instance:
            # Exclude the current instance from the queryset
            base_queryset = self.fields['based_on'].queryset.exclude(id=instance.id)
            
            if instance.based_on:
                # Ensure the instance's current based_on is included
                base_queryset = base_queryset | Post.objects.filter(id=instance.based_on.id)

            self.fields['based_on'].queryset = base_queryset



class PieceImportForm(forms.ModelForm):
    class Meta:
        model = Piece
        fields = ['name', 'quantity', 'description', 'suited', 'parent', 'type']


class PieceForm(forms.ModelForm):
    class Meta:
        model = Piece
        fields = ['name', 'quantity', 'suited', 'small_icon', 'id']  # These fields are automatically recognized by Django
        widgets = {
            'name': forms.TextInput(attrs={
                'style': 'width: 75px;',  # Adjust the width here
                'placeholder': _('Name')  # Add placeholder text for the name field
            }),
            'quantity': forms.NumberInput(attrs={
                'inputmode': 'numeric',  # Mobile-friendly number input
                'min': 0, 
                'max': 99,
                'placeholder': '#'  # Add placeholder text for the quantity field
            }),
            'small_icon': forms.ClearableFileInput(attrs={'class': 'form-control compact-file-input'}),
        }
    # Override clean_quantity method for custom validation
    def clean_quantity(self):
        quantity = self.cleaned_data.get('quantity')

        if quantity > 99:
            raise ValidationError('Quantity cannot be greater than 99.')
        
        return quantity

class StatusConfirmForm(forms.Form):
    # This form doesn't need any fields, just the submit button
    pass


class PNPAssetCreateForm(forms.ModelForm):
    class Meta:
        model = PNPAsset
        fields = ['title', 'category', 'link', 'file_type', 'description', 'shared_by']
        help_texts = {
            'shared_by': 'Selected user will be able to edit and delete this link.',
            'link': 'Enter the direct link to this asset (Google Drive, Dropbox, etc). The more specific the better.'
        }
        labels = {
            'file_type': _('File Type')
        }

    def __init__(self, *args, **kwargs):
        self.profile = kwargs.pop('profile', None)
        super().__init__(*args, **kwargs)
        
        if self.profile:
            self.fields['shared_by'].initial = self.profile


        

        # If not admin user the designer will be the active user
        # Admin users can create posts for any user.
        if not self.profile.admin:
            self.fields['shared_by'].queryset = self.fields['shared_by'].queryset.filter(id=self.profile.id)
        else:
            self.fields['shared_by'].queryset = self.fields['shared_by'].queryset.filter(
            Q(id=self.profile.id) | Q(group="O") | Q(group="P") | Q(group="B")
        )
        if not self.instance:
            self.fields['shared_by'].initial = self.profile.id

        self.fields['shared_by'].label = "Shared By (Admin Only)"
        # # Hide the shared by field for non-admin userss
        # if not self.profile.admin:
        #     self.fields.pop('shared_by', None)  # Remove shared by field entirely



        # Check if an instance is being updated (i.e., it's an existing object)
        if self.instance and self.instance.pk and self.instance.shared_by or self.profile.admin == False:
            # If the object exists (it's being updated), remove 'shared_by' field
            self.fields.pop('shared_by')
            
        



    def save(self, commit=True):
        # Get the instance and update date_updated to current time
        instance = super().save(commit=False)
        
        # Set the date_updated field to the current time
        instance.date_updated = timezone.now()

        if commit:
            instance.save()
        
        return instance
    

class AddLawForm(forms.Form):
    title = forms.CharField(max_length=50)
    description = forms.CharField(required=False)
    group_id = forms.IntegerField()
    parent_id = forms.IntegerField(required=False)
    prev_id = forms.IntegerField(required=False)
    next_id = forms.IntegerField(required=False)
    language_id = forms.IntegerField()
    reference_laws = forms.ModelMultipleChoiceField(
        queryset=Law.objects.all(),
        required=False,
        widget=forms.SelectMultiple(attrs={'class': 'multi-select'})
    )


class EditLawForm(forms.ModelForm):
    class Meta:
        model = Law
        fields = ['title', 'description', 'reference_laws']
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['description'].required = False

class EditLawDescriptionForm(forms.ModelForm):
    class Meta:
        model = Law
        fields = ['description', 'reference_laws']
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['description'].required = False

class EditLawGroupForm(forms.ModelForm):
    class Meta:
        model = LawGroup
        fields = ['title', 'abbreviation', 'type', 'public']
        help_texts = {
            'public': "Public laws are visible to other users in all available languages.",
            # 'abbreviation': 'Choose an abbreviation for the Law',
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)

        if not user.profile.admin:
            # Non-admin: hide title and type, make abbreviation a select field
            self.fields.pop('title')
            self.fields.pop('type')

            instance = kwargs.get('instance')
            if instance:
                choices = generate_abbreviation_choices(instance.title, instance.abbreviation)
                self.fields['abbreviation'] = forms.ChoiceField(
                    choices=[(abbr, abbr) for abbr in choices],
                    label="Choose Abbreviation"
                )

class CopyLawGroupForm(forms.Form):
    language = forms.ModelChoiceField(queryset=Language.objects.none())

    def __init__(self, source_group=None, *args, **kwargs):
        super().__init__(*args, **kwargs)

        if source_group:
            base_qs = Language.objects.all()

            # Get language codes used in the laws of this group
            used_languages = Law.objects.filter(group=source_group).values_list('language__code', flat=True).distinct()

            # Exclude those from the language field queryset
            base_qs = base_qs.exclude(code__in=used_languages)

            self.fields['language'].queryset = base_qs

class LanguageSelectionForm(forms.Form):
    language = forms.ModelChoiceField(queryset=Language.objects.none(), empty_label=None)

class FAQForm(forms.ModelForm):
    class Meta:
        model = FAQ
        fields = ['question', 'answer', 'reference_laws']

    def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.fields['question'].widget.attrs.update({
                'rows': '2'
                })
            self.fields['answer'].widget.attrs.update({
                'rows': '2'
                })

class YAMLUploadForm(forms.Form):
    file = forms.FileField(label="Upload YAML Law file")