import os
import uuid
import re
import inflect

from django.db import models
from django.db.models.signals import pre_save, post_save
from django.db.models import Count, F, ExpressionWrapper, FloatField, Q, Case, When, Value, IntegerField
from django.db.models.functions import Cast, Abs
from django.utils import timezone 
# from datetime import timedelta
from django.urls import reverse
from django.core.cache import cache
from django.core.validators import MinValueValidator, MaxValueValidator

import json
from PIL import Image
from io import BytesIO
from django.apps import apps
from .utils import slugify_post_title, slugify_expansion_title
from the_gatehouse.models import Profile, Language
from .utils import validate_hex_color, resize_image, delete_old_image, hex_to_rgb
import random
from django.conf import settings
from the_gatehouse.discordservice import send_rich_discord_message
# from the_gatehouse.utils import build_absolute_uri
from django.utils.translation import gettext_lazy as _

# Stable requirements
# 10 games played
game_threshold = 10
# 5 different players
player_threshold = 5

p = inflect.engine()

def get_default_language():
    # Return the first Language object if it exists, otherwise return None
    return Language.objects.first()

def convert_animals_to_singular(text):
    # Remove any instances of "and" from the input string
    text = text.replace("and", ",")
    # Replace slashes with commas to unify the separator
    text = text.replace("/", ",")
    
    # Split the string by commas
    words = [word.strip() for word in text.split(",")]
    
    # Convert each word to its singular form
    singular_words = [p.singular_noun(word) or word for word in words]
    
    # Capitalize the first letter of each word
    capitalized_words = [word.capitalize() for word in singular_words]

    # Join the words with commas except for the last one
    if len(capitalized_words) > 1:
        return ", ".join(capitalized_words[:-1]) + " and " + capitalized_words[-1]
    else:
        # If there's only one word, just return it
        return capitalized_words[0]

class ColorChoices(models.TextChoices):
    RED = ('#FF0000', _('Red'))
    ORANGE = ('#FFA500', _('Orange'))
    YELLOW = ('#FFFF00', _('Yellow'))
    GREEN = ('#008000', _('Green'))
    BLUE = ('#0000FF', _('Blue'))
    PURPLE = ('#800080', _('Purple'))
    WHITE = ('#FFFFFF', _('White'))
    GREY = ('#808080', _('Grey'))
    BLACK = ('#000000', _('Black'))
    PINK = ('#FFC0CB', _('Pink'))
    BROWN = ('#A52A2A', _('Brown'))
    TAN = ('#D2B48C', _('Tan'))

    @classmethod
    def get_color_by_name(cls, color_name):
        """
        Given a color name, return the corresponding color tuple from ColorChoices.
        Color name should be case-insensitive (e.g., 'red', 'Red', 'RED' all work).
        """
        # Normalize the input color name to be case-insensitive
        color_name = color_name.strip().capitalize()

        # Loop through each choice and match by the 'name' of the color (not the value)
        for color in cls:
            if color_name == color.name.capitalize():  # Use .capitalize() to ensure case-insensitivity
                return color
        return None
    
    @classmethod
    def get_color_group_by_hex(cls, hex_code):
        """
        Given a hex code, return the corresponding color name from ColorChoices.
        Hex code should be case-insensitive (e.g., '#ff0000', '#FF0000', '#Ff0000' all work).
        """

        # Loop through each choice and match by the 'hex' part of the color (first part of the tuple)
        for color in cls:
            if hex_code == color.value:
                return color.name.capitalize()  # Return the color name, properly capitalized
        return None

class StatusChoices(models.TextChoices):
    STABLE = '1', _('Stable')
    TESTING = '2', _('Testing')
    DEVELOPMENT = '3', _('Development')
    INACTIVE = '4', _('Inactive')
    ABANDONED = '5', _('Abandoned')
    DEEPFREEZE = '9', 'Deep Freeze'

# def get_status_name_from_int(status_int):
#     # Iterate through the choices to find the matching integer
#     for status in StatusChoices:
#         if status.value == str(status_int):  # Compare as string because your choices are strings
#             return status.label
#     return None  # Return None if not found

class PostQuerySet(models.QuerySet):
    def search(self, query=None):
        if query is None or query == "":
            return self.none()
        lookups = Q(title__icontains=query) | Q(designer__discord__icontains=query)
        return self.filter(lookups)
    
class PostManager(models.Manager):
    def get_queryset(self):
        return PostQuerySet(self.model, using=self._db)
    def search(self, query=None):
        return self.get_queryset().search(query=query)

class Expansion(models.Model):
    title = models.CharField(max_length=100)
    designer = models.ForeignKey(Profile, on_delete=models.SET_NULL, null=True, blank=True)
    description = models.TextField(null=True, blank=True)
    lore = models.TextField(null=True, blank=True)
    slug = models.SlugField(unique=True, null=True, blank=True)
    picture = models.ImageField(upload_to='boards', null=True, blank=True)
    bgg_link = models.CharField(max_length=400, null=True, blank=True)
    tts_link = models.CharField(max_length=400, null=True, blank=True)
    ww_link = models.CharField(max_length=400, null=True, blank=True)
    wr_link = models.CharField(max_length=400, null=True, blank=True)
    pnp_link = models.CharField(max_length=400, null=True, blank=True)
    stl_link = models.CharField(max_length=400, null=True, blank=True)
    leder_games_link = models.CharField(max_length=400, null=True, blank=True)
    fr_link = models.CharField(max_length=400, null=True, blank=True)
    end_date = models.DateTimeField(null=True, blank=True)
    open_roster = models.BooleanField(default=False)

    # Get groups of components for selected expansion
    def get_posts(self):
        return Post.objects.filter(expansion=self)
    def get_decks(self):
        return Deck.objects.filter(expansion=self)
    def get_maps(self):
        return Map.objects.filter(expansion=self)
    def get_hirelings(self):
        return Hireling.objects.filter(expansion=self)
    def get_vagabonds(self):
        return Vagabond.objects.filter(expansion=self)
    def get_landmarks(self):
        return Landmark.objects.filter(expansion=self)
    def get_factions(self):
        return Faction.objects.filter(expansion=self).exclude(component="Clockwork")
    def get_clockwork(self):
        return Faction.objects.filter(expansion=self, component="Clockwork")
    
    def get_absolute_url(self):
        return reverse('expansion-detail', kwargs={'slug': self.slug})
    def __str__(self):
        return self.title
    
    def count_links(self, user):
        # List of link field names you want to check
        link_fields = ['bgg_link', 'tts_link', 'ww_link', 'wr_link', "fr_link", 'pnp_link', 'stl_link', 'leder_games_link']
        
        # Count how many of these fields are not None or empty
        count = 0
        for field in link_fields:
            if getattr(self, field):  # Checks if the field value is not None or empty string
                count += 1
        return count

    def save(self, *args, **kwargs):

        # Check if the image field has changed (only works if the instance is already saved)
        if self.pk:  # If the object already exists in the database
            old_instance = Expansion.objects.get(pk=self.pk)
            # List of fields to check and delete old images if necessary
            field_name = 'picture'

            old_image = getattr(old_instance, field_name)
            new_image = getattr(self, field_name)
            if old_image != new_image:
                delete_old_image(old_image)
        
        super().save(*args, **kwargs)
        # Resize images before saving
        if self.picture:
            resize_image(self.picture, 950)  # Resize expansion image

class Post(models.Model):
    
    class ComponentChoices(models.TextChoices):
        MAP = 'Map', _('Map')
        DECK = 'Deck', _('Deck')
        HIRELING = 'Hireling', _('Hireling')
        VAGABOND = 'Vagabond', _('Vagabond')
        LANDMARK = 'Landmark', _('Landmark')
        FACTION = 'Faction', _('Faction')
        CLOCKWORK = 'Clockwork', _('Clockwork')
        TWEAK = 'Tweak', _('House Rule')



    title = models.CharField(max_length=40)
    designer = models.ForeignKey(Profile, on_delete=models.SET_NULL, null=True, related_name='posts')
    animal = models.CharField(max_length=25, null=True, blank=True)
    slug = models.SlugField(unique=True, null=True, blank=True)
    expansion = models.ForeignKey(Expansion, on_delete=models.SET_NULL, null=True, blank=True, related_name='posts')
    lore = models.TextField(null=True, blank=True)
    description = models.TextField(null=True, blank=True)
    date_posted = models.DateTimeField(default=timezone.now)
    artist = models.ForeignKey(Profile, on_delete=models.SET_NULL, null=True, related_name='artist_posts', blank=True)
    official = models.BooleanField(default=False)
    in_root_digital = models.BooleanField(default=False)
    status = models.CharField(max_length=15 , default=StatusChoices.DEVELOPMENT, choices=StatusChoices.choices)
    language = models.ForeignKey(Language, on_delete=models.SET_DEFAULT, null=True, blank=True, default=get_default_language)

    bgg_link = models.CharField(max_length=400, null=True, blank=True)
    tts_link = models.CharField(max_length=400, null=True, blank=True)
    ww_link = models.CharField(max_length=400, null=True, blank=True)
    wr_link = models.CharField(max_length=400, null=True, blank=True)
    pnp_link = models.CharField(max_length=400, null=True, blank=True)
    stl_link = models.CharField(max_length=400, null=True, blank=True)
    leder_games_link = models.CharField(max_length=400, null=True, blank=True)
    fr_link = models.CharField(max_length=400, null=True, blank=True)
    based_on = models.ForeignKey('self', on_delete=models.SET_NULL, null=True, blank=True)
    small_icon = models.ImageField(upload_to='small_component_icons/custom', null=True, blank=True)
    picture = models.ImageField(upload_to='component_pictures', null=True, blank=True)
    board_image = models.ImageField(upload_to='boards', null=True, blank=True)
    card_image = models.ImageField(upload_to='cards', null=True, blank=True)
    board_2_image = models.ImageField(upload_to='boards', null=True, blank=True)
    card_2_image = models.ImageField(upload_to='cards', null=True, blank=True)
    bookmarks = models.ManyToManyField(Profile, related_name='bookmarkedposts', through='PostBookmark')
    color = models.CharField(
        max_length=7,
        blank=True,
        null=True,
        validators=[validate_hex_color],
        help_text="Enter a hex color code (e.g., #RRGGBB)."
    )
    color_group = models.CharField(max_length=30, choices=ColorChoices.choices, blank=True, null=True)

    color_r = models.IntegerField(blank=True, null=True)
    color_g = models.IntegerField(blank=True, null=True)
    color_b = models.IntegerField(blank=True, null=True)
    component = models.CharField(max_length=20, choices=ComponentChoices.choices, null=True, blank=True)
    sorting = models.IntegerField(default=10)
    component_snippet = models.CharField(max_length=100, null=True, blank=True)
    date_updated = models.DateTimeField(default=timezone.now)



    objects = PostManager()

    @classmethod
    def get_color_group(cls, color_group):
        """
        Get all posts that have the same color group.
        The tolerance defines how strict the color match should be.
        """
        # Query to find posts with the same color group
        queryset = cls.objects.filter(Q(color_group=color_group) | Q(based_on__color_group=color_group))

        return queryset

    # Method to clean the animal name: remove special characters and handle plurals
    def clean_animal_name(self, name):
        # Remove special characters (keep only alphabetic characters and spaces)
        name = re.sub(r'[^a-zA-Z\s]', '', name)
       
        # Convert plural to singular
        singular_name = p.singular_noun(name) or name  # `singular_noun` returns None if no plural found
        return singular_name

    def matching_animals(self):
        # Check if animal is None or empty
        if not self.animal:
            return Post.objects.none()  # or return an empty queryset
 
        # # Remove any instances of "and" from the animal string
        # cleaned_animal_string = self.animal.replace("and", "").strip()

        base_animal_string = self.animal or ""
        
        # Add translated animals from all translations
        translated_animals = [
            t.translated_animal for t in self.translations.all()
            if t.translated_animal
        ]
        all_animals_combined = " ".join([base_animal_string] + translated_animals)

        # Clean out "and" and extra spaces
        cleaned_animal_string = all_animals_combined.replace("and", "").strip()

        # Split the animal into individual animals and clean them
        animals_list = cleaned_animal_string.split()
        cleaned_animals_list = [self.clean_animal_name(animal) for animal in animals_list]
 
        # Build the query to check if the cleaned animals appear in other objects
        query = Q()
        for animal in cleaned_animals_list:
            query |= Q(animal__icontains=animal)|Q(translations__translated_animal__icontains=animal)
 
        # Query other objects that contain at least one of the cleaned animals
        return Post.objects.filter(query).exclude(id=self.id).distinct()


    def count_links(self, user):
        # List of link field names you want to check
        link_fields = ['bgg_link', 'tts_link', 'ww_link', 'wr_link', 'fr_link', 'pnp_link', 'stl_link', 'leder_games_link']
        
        # Count how many of these fields are not None or empty
        count = 0
        for field in link_fields:
                if getattr(self, field):  # Checks if the field value is not None or empty string
                    count += 1
        return count


    def warriors(self):
        return Piece.objects.filter(parent=self, type=Piece.TypeChoices.WARRIOR)
    def buildings(self):
        return Piece.objects.filter(parent=self, type=Piece.TypeChoices.BUILDING)
    def tokens(self):
        return Piece.objects.filter(parent=self, type=Piece.TypeChoices.TOKEN)
    def cards(self):
        return Piece.objects.filter(parent=self, type=Piece.TypeChoices.CARD)
    def otherpieces(self):
        return Piece.objects.filter(parent=self, type=Piece.TypeChoices.OTHER)

    def delete(self, *args, **kwargs):
        for piece in self.pieces.all():
            if piece.small_icon:
                piece_image = getattr(piece, "small_icon")
                delete_old_image(piece_image)

        # Delete the old image file from storage before deleting the instance
        image_fields = ['card_image', 'picture', 'small_icon', 'board_image']
        for field_name in image_fields:
            post_image = getattr(self, field_name)

            # The image has changed, so check if it's not a default image
            if post_image and not post_image.name.startswith('default_images/'):
                # Delete non-default images
                print(f'deleting {post_image}')
                self._delete_old_image(post_image)
            else:
                print(f'not deleting {post_image}')
        
        # Now delete the post instance
        super().delete(*args, **kwargs)


    def save(self, *args, **kwargs):

        # Check if the image field has changed (only works if the instance is already saved)
        if self.pk:  # If the object already exists in the database
            old_instance = Post.objects.get(pk=self.pk)
            # List of fields to check and delete old images if necessary
            image_fields = ['card_image', 'picture', 'small_icon', 'board_image']
            for field_name in image_fields:
                old_image = getattr(old_instance, field_name)
                new_image = getattr(self, field_name)
                if old_image != new_image:
                    # The image has changed, so check if it's not a default image
                    if old_image and not old_image.name.startswith('default_images/'):
                        # Delete non-default images
                        self._delete_old_image(old_image)
                    else:
                        # Ignore any files in the default_images folder
                        print(f"Default image saved: {old_image}")
            new_post = False
        else:
            new_post = True

        if self.color:
            rgb = hex_to_rgb(self.color)
            self.color_r, self.color_g, self.color_b = rgb

        if self.animal:
            animals = convert_animals_to_singular(self.animal)
            self.animal = animals

        if not self.language:
            language = Language.objects.first()
            self.language = language

        super().save(*args, **kwargs)

        if new_post:
            fields = []
            fields.append({
                    'name': 'By:',
                    'value': self.designer.discord
                })
            send_rich_discord_message(f'[{self.title}](https://therootdatabase.com{self.get_absolute_url()})', category='New Post', title=f'New {self.component}', fields=fields)

        # if self.small_icon:
        resize_image(self.small_icon, 80)
        # if self.board_image:
        resize_image(self.board_image, 950)  # Resize board_image
        # if self.board_2_image:
        resize_image(self.board_2_image, 950)  # Resize board_image
        # if self.card_image:
        resize_image(self.card_image, 350)  # Resize card_image
        # if self.card_2_image:
        resize_image(self.card_2_image, 350)  # Resize card_image
        # if self.picture:
        resize_image(self.picture, 350)

    def _delete_old_image(self, old_image):
        """Helper method to delete old image if it exists."""
        if not old_image.name.startswith('default_images/'):
            if old_image and os.path.exists(old_image.path):
                os.remove(old_image.path)
                print(f"Old image deleted: {old_image}")
        else:
            print(f"Default image saved: {old_image}")


    def _resize_image(self, image_field, max_size):
        """Helper method to resize the image if necessary."""
        try:
            if image_field and os.path.exists(image_field.path):  # Check if the image exists
                img = Image.open(image_field.path)

                # Resize if the image is larger than the max_size
                if img.height > max_size or img.width > max_size:
                    # Calculate the new size while maintaining the aspect ratio
                    if img.width > img.height:
                        ratio = max_size / img.width
                        new_size = (max_size, int(img.height * ratio))
                    else:
                        ratio = max_size / img.height
                        new_size = (int(img.width * ratio), max_size)

                    # Resize image and save
                    img = img.resize(new_size, Image.LANCZOS)
                    img.save(image_field.path)
                    print(f'Resized image saved at: {image_field.path}')
                else:
                    print(f'Original image saved at: {image_field.path}')
        except Exception as e:
            print(f"Error resizing image: {e}")

    def process_and_save_small_icon(self, image_field_name):
        """
        Process the image field, resize it, and save it to the `small_icon` field.
        This method can be used by any subclass of Post to reuse the image processing logic.
        """
        # Get the image from the specified field
        image = getattr(self, image_field_name)

        if image:
            # Open the image from the ImageFieldFile
            img = Image.open(image)
                # Convert the image to RGB if it's in a mode like 'P' (palette-based)
            # if img.mode != 'RGB':
            #     img = img.convert('RGB')
            # Create a copy of the image
            small_icon_copy = img.copy()

            # Optionally, save the image to a new BytesIO buffer
            img_io = BytesIO()
            small_icon_copy.save(img_io, format='PNG')  # Save as a PNG, or another format as needed
            img_io.seek(0)
            # Now you can assign the img_io to your model field or save it to a new ImageField
            # Generate a unique filename using UUID
            unique_filename = f"{uuid.uuid4().hex}.png"

            # Save to small_icon field with unique filename
            self.small_icon.save(unique_filename, img_io, save=False)

    def process_and_save_picture(self, image_field_name):
        """
        Process the image field, resize it, and save it to the `picture` field.
        This method can be used by any subclass of Post to reuse the image processing logic.
        """
        # Get the image from the specified field
        image = getattr(self, image_field_name)

        if image:
            # Open the image from the ImageFieldFile
            img = Image.open(image)
                # Convert the image to RGB if it's in a mode like 'P' (palette-based)
            # if img.mode != 'RGB':
            #     img = img.convert('RGB')
            # Create a copy of the image
            picture_copy = img.copy()

            # Optionally, save the image to a new BytesIO buffer
            img_io = BytesIO()
            picture_copy.save(img_io, format='PNG')  # Save as a PNG, or another format as needed
            img_io.seek(0)
            # Now you can assign the img_io to your model field or save it to a new ImageField
            # Generate a unique filename using UUID
            unique_filename = f"{uuid.uuid4().hex}.png"

            # Save to picture field with unique filename
            self.picture.save(unique_filename, img_io, save=False)

    def get_absolute_url(self):
        match self.component:
            case "Map":
                return reverse('map-detail', kwargs={'slug': self.slug})
            case "Deck":
                return reverse('deck-detail', kwargs={'slug': self.slug})
            case "Landmark":
                return reverse('landmark-detail', kwargs={'slug': self.slug})
            case "Tweak":
                return reverse('tweak-detail', kwargs={'slug': self.slug})
            case "Hireling":
                return reverse('hireling-detail', kwargs={'slug': self.slug})        
            case "Vagabond":
                return reverse('vagabond-detail', kwargs={'slug': self.slug})
            case "Clockwork":
                return reverse('clockwork-detail', kwargs={'slug': self.slug})
            case _:
                return reverse('faction-detail', kwargs={'slug': self.slug})


    def get_edit_url(self):
        match self.component:
            case "Map":
                return reverse('map-update', kwargs={'slug': self.slug})
            case "Deck":
                return reverse('deck-update', kwargs={'slug': self.slug})
            case "Landmark":
                return reverse('landmark-update', kwargs={'slug': self.slug})
            case "Tweak":
                return reverse('tweak-update', kwargs={'slug': self.slug})
            case "Hireling":
                return reverse('hireling-update', kwargs={'slug': self.slug})        
            case "Vagabond":
                return reverse('vagabond-update', kwargs={'slug': self.slug})
            case "Clockwork":
                return reverse('clockwork-update', kwargs={'slug': self.slug})
            case _:
                return reverse('faction-update', kwargs={'slug': self.slug})

    def get_games_url(self):
        match self.component:
            case "Map":
                return reverse('map-games', kwargs={'slug': self.slug})
            case "Deck":
                return reverse('deck-games', kwargs={'slug': self.slug})
            case "Landmark":
                return reverse('landmark-games', kwargs={'slug': self.slug})
            case "Tweak":
                return reverse('tweak-games', kwargs={'slug': self.slug})
            case "Hireling":
                return reverse('hireling-games', kwargs={'slug': self.slug})        
            case "Vagabond":
                return reverse('vagabond-games', kwargs={'slug': self.slug})
            case "Clockwork":
                return reverse('clockwork-games', kwargs={'slug': self.slug})
            case _:
                return reverse('faction-games', kwargs={'slug': self.slug})

 
    def __str__(self):
        return self.title
    
    
    def get_plays_queryset(self):
        match self.component:
            case "Map" | "Deck" | "Landmark" | "Tweak" | "Hireling":
                return self.games.order_by('-date_posted') 
            case "Faction" | "Vagabond" | "Clockwork":
                return self.efforts.order_by('-date_posted') 
            case _:
                return Post.objects.none()  # or return an empty queryset

    def get_games_queryset(self):
        Game = apps.get_model('the_warroom', 'Game')
        match self.component:
            case "Map" | "Deck" | "Landmark" | "Tweak" | "Hireling":
                return self.games.order_by('-date_posted')  # Return a queryset directly
            case "Vagabond":
                return Game.objects.filter(efforts__vagabond=self, efforts__game__final=True)
            case "Faction" | "Clockwork":
                return Game.objects.filter(efforts__faction=self, efforts__game__final=True)

            case _:
                return Game.objects.none()  # No games if no component matches

    def plays(self):
        plays = self.get_plays_queryset()
        return plays.count() if plays else 0
    

        
    class Meta:
        ordering = ['sorting', '-official', 'status', '-date_posted']



class PostTranslation(models.Model):
    post = models.ForeignKey(Post, related_name='translations', on_delete=models.CASCADE)
    language = models.ForeignKey(Language, on_delete=models.CASCADE)
    designer = models.ForeignKey(Profile, on_delete=models.SET_NULL, null=True, blank=True)

    translated_title = models.CharField(max_length=40, null=True, blank=True)
    translated_lore = models.TextField(null=True, blank=True)
    translated_description = models.TextField(null=True, blank=True)
    translated_animal = models.CharField(max_length=25, null=True, blank=True)


    translated_board_image = models.ImageField(upload_to='boards', null=True, blank=True)
    translated_card_image = models.ImageField(upload_to='cards', null=True, blank=True)
    translated_board_2_image = models.ImageField(upload_to='boards', null=True, blank=True)
    translated_card_2_image = models.ImageField(upload_to='cards', null=True, blank=True)

    class Meta:
        unique_together = ('post', 'language')

    def __str__(self):
        if self.translated_title:
            return self.translated_title
        else:
            return f'{self.post.title} ({self.language.code})'

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)

        resize_image(self.translated_board_image, 950)  # Resize board_image
        resize_image(self.translated_board_2_image, 950)  # Resize board_image
        resize_image(self.translated_card_image, 350)  # Resize card_image
        resize_image(self.translated_card_2_image, 350)  # Resize card_image

    def get_absolute_url(self):
        parent_url = self.post.get_absolute_url()
        translation_url = parent_url + f'?lang={self.language.code}'
        return translation_url
    

class PostBookmark(models.Model):
    player = models.ForeignKey(Profile, on_delete=models.CASCADE)
    post = models.ForeignKey(Post, on_delete=models.CASCADE)
    public = models.BooleanField(default=False)
    date_posted = models.DateTimeField(default=timezone.now)
    def __str__(self):
        return f"{self.player.name}: {self.post.title} - {self.post.designer.name}"




class Deck(Post):
    card_total = models.IntegerField()
    def save(self, *args, **kwargs):
        self.component = 'Deck'  # Set the component type
        self.sorting = 3
        if not self.picture:
            self.picture = 'default_images/deck.png'
        self.component_snippet = f"{self.card_total} Card"

        # Check if the image field has changed (only works if the instance is already saved)
        if self.pk:  # If the object already exists in the database
            old_instance = Post.objects.get(pk=self.pk)
           
            if self.card_image:
                field_name = 'card_image'
            else:
                field_name = 'picture'
            old_image = getattr(old_instance, field_name)
            new_image = getattr(self, field_name)
            if old_image != new_image or not self.small_icon:
                delete_old_image(getattr(old_instance,'small_icon'))
                self.process_and_save_small_icon(field_name)
                delete_old_image(getattr(old_instance,'picture'))
                self.process_and_save_picture(field_name)
        else:
            if self.card_image:
                field_name = 'card_image'
            else:
                field_name = 'picture'
            self.process_and_save_small_icon(field_name)
            self.process_and_save_picture(field_name)

        super().save(*args, **kwargs)  # Call the parent save method


    def stable_check(self):
        plays = self.get_plays_queryset()
        faction_threshold = Faction.objects.filter(official=True, status=1, component="Faction").count()
        official_faction_count = Faction.objects.filter(efforts__game__deck=self, official=True, status=1, component="Faction").distinct().count()
        unique_players = plays.aggregate(
                    total_players=Count('efforts__player', distinct=True)
                )['total_players']
        

        play_count = plays.count()

        map_threshold = Map.objects.filter(official=True, status=1).count()
        official_map_count = Map.objects.filter(games__deck=self, official=True, status=1).distinct().count()
        deck_threshold = 0
        official_deck_count = 0

        if play_count >= game_threshold and self.status != 'Stable' and unique_players >= player_threshold and official_faction_count >= faction_threshold and official_map_count >= map_threshold and official_deck_count >= deck_threshold:
            stable_ready = True
        else:
            stable_ready = False
        print(f'Stable Ready: {stable_ready}, Plays: {play_count}/{game_threshold}, Players: {unique_players}/{player_threshold}, Official Factions: {official_faction_count}/{faction_threshold}')
        return (stable_ready, play_count, unique_players, official_faction_count, game_threshold, player_threshold, faction_threshold, official_map_count, map_threshold, official_deck_count, deck_threshold)






class Landmark(Post):
    card_text = models.TextField(blank=True, null=True)
    def save(self, *args, **kwargs):
        self.component = 'Landmark'  # Set the component type
        self.sorting = 5
        if not self.picture:
            self.picture = 'default_images/landmark.png'

        # Check if the image field has changed (only works if the instance is already saved)
        if self.pk:  # If the object already exists in the database
            old_instance = Post.objects.get(pk=self.pk)
           

            field_name = 'picture'
            old_image = getattr(old_instance, field_name)
            new_image = getattr(self, field_name)
            if old_image != new_image or not self.small_icon:
                delete_old_image(getattr(old_instance,'small_icon'))
                self.process_and_save_small_icon(field_name)
        else:
            field_name = 'picture'
            self.process_and_save_small_icon(field_name)
    
        super().save(*args, **kwargs)  # Call the parent save method

    def stable_check(self):
        faction_threshold = Faction.objects.filter(official=True, status=1, component="Faction").count()
        plays = self.get_plays_queryset()
        official_faction_count = Faction.objects.filter(efforts__game__landmarks=self, official=True, status=1, component="Faction").distinct().count()
        unique_players = plays.aggregate(
                    total_players=Count('efforts__player', distinct=True)
                )['total_players']
        

        play_count = plays.count()

        map_threshold = Map.objects.filter(official=True, status=1).count()
        official_map_count = Map.objects.filter(games__landmarks=self, official=True, status=1).distinct().count()
        deck_threshold = Deck.objects.filter(official=True, status=1).count() - 1
        official_deck_count = Deck.objects.filter(games__landmarks=self, official=True, status=1).distinct().count()

        if play_count >= game_threshold and self.status != 'Stable' and unique_players >= player_threshold and official_faction_count >= faction_threshold and official_map_count >= map_threshold and official_deck_count >= deck_threshold:
            stable_ready = True
        else:
            stable_ready = False
        print(f'Stable Ready: {stable_ready}, Plays: {play_count}/{game_threshold}, Players: {unique_players}/{player_threshold}, Official Factions: {official_faction_count}/{faction_threshold}')
        return (stable_ready, play_count, unique_players, official_faction_count, game_threshold, player_threshold, faction_threshold, official_map_count, map_threshold, official_deck_count, deck_threshold)

class Tweak(Post):
    def save(self, *args, **kwargs):
        self.component = 'Tweak'  # Set the component type
        self.sorting = 8
        if not self.picture:
            self.picture = 'default_images/tweak.png'
        # Check if the image field has changed (only works if the instance is already saved)
        if self.pk:  # If the object already exists in the database
            old_instance = Post.objects.get(pk=self.pk)

            field_name = 'picture'
            old_image = getattr(old_instance, field_name)
            new_image = getattr(self, field_name)
            if old_image != new_image or not self.small_icon:
                delete_old_image(getattr(old_instance,'small_icon'))
                self.process_and_save_small_icon(field_name)
        else:
            field_name = 'picture'
            self.process_and_save_small_icon(field_name)
        super().save(*args, **kwargs)  # Call the parent save method

    def stable_check(self):
        faction_threshold = Faction.objects.filter(official=True, status=1, component="Faction").count()
        plays = self.get_plays_queryset()
        official_faction_count = Faction.objects.filter(efforts__game__tweaks=self, official=True, status=1, component="Faction").distinct().count()
        unique_players = plays.aggregate(
                    total_players=Count('efforts__player', distinct=True)
                )['total_players']
        

        play_count = plays.count()

        map_threshold = Map.objects.filter(official=True, status=1).count()
        official_map_count = Map.objects.filter(games__tweaks=self, official=True, status=1).distinct().count()
        deck_threshold = Deck.objects.filter(official=True, status=1).count() - 1
        official_deck_count = Deck.objects.filter(games__tweaks=self, official=True, status=1).distinct().count()


        if play_count >= game_threshold and self.status != 'Stable' and unique_players >= player_threshold and official_faction_count >= faction_threshold and official_map_count >= map_threshold and official_deck_count >= deck_threshold:
            stable_ready = True
        else:
            stable_ready = False
        print(f'Stable Ready: {stable_ready}, Plays: {play_count}/{game_threshold}, Players: {unique_players}/{player_threshold}, Official Factions: {official_faction_count}/{faction_threshold}')
        return (stable_ready, play_count, unique_players, official_faction_count, game_threshold, player_threshold, faction_threshold, official_map_count, map_threshold, official_deck_count, deck_threshold)

class Map(Post):
    clearings = models.IntegerField(default=12)
    fixed_clearings = models.BooleanField(default=False)
    default_landmark = models.ForeignKey(Landmark, on_delete=models.PROTECT, null=True, blank=True)
    def save(self, *args, **kwargs):
        self.component = 'Map'  # Set the component type
        self.sorting = 2
        if not self.picture:
            self.picture = 'default_images/map.png'

        self.component_snippet = f"{self.clearings} Clearing"

        # Check if the image field has changed (only works if the instance is already saved)
        if self.pk:  # If the object already exists in the database
            old_instance = Post.objects.get(pk=self.pk)
           
            if self.board_image:
                field_name = 'board_image'
            else:
                field_name = 'picture'
            old_image = getattr(old_instance, field_name)
            new_image = getattr(self, field_name)
            if old_image != new_image or not self.small_icon:
                delete_old_image(getattr(old_instance,'small_icon'))
                self.process_and_save_small_icon(field_name)
                delete_old_image(getattr(old_instance,'picture'))
                self.process_and_save_picture(field_name)
        else:
            if self.board_image:
                field_name = 'board_image'
            else:
                field_name = 'picture'
            self.process_and_save_small_icon(field_name)
            self.process_and_save_picture(field_name)
            

        super().save(*args, **kwargs)








    def stable_check(self):
        faction_threshold = Faction.objects.filter(official=True, status=1, component="Faction").count()
        plays = self.get_plays_queryset()
        official_faction_count = Faction.objects.filter(efforts__game__map=self, official=True, status=1, component="Faction").distinct().count()
        unique_players = plays.aggregate(
                    total_players=Count('efforts__player', distinct=True)
                )['total_players']
        
        play_count = plays.count()

        map_threshold = 0
        official_map_count = 0
        deck_threshold = Deck.objects.filter(official=True, status=1).count() - 1
        official_deck_count = Deck.objects.filter(games__map=self, official=True, status=1).distinct().count()


        if play_count >= game_threshold and self.status != 'Stable' and unique_players >= player_threshold and official_faction_count >= faction_threshold and official_map_count >= map_threshold and official_deck_count >= deck_threshold:
            stable_ready = True
        else:
            stable_ready = False
        print(f'Stable Ready: {stable_ready}, Plays: {play_count}/{game_threshold}, Players: {unique_players}/{player_threshold}, Official Factions: {official_faction_count}/{faction_threshold}')
        return (stable_ready, play_count, unique_players, official_faction_count, game_threshold, player_threshold, faction_threshold, official_map_count, map_threshold, official_deck_count, deck_threshold)




class Vagabond(Post):
    class AbilityChoices(models.TextChoices):
        NONE = 'None'
        ANY = 'Any'
        TORCH = 'Torch'
        BAG = 'Bag'
        TEA = 'Tea'
        BOOTS = 'Boots'
        COINS = 'Coins'
        SWORD = 'Sword'
        HAMMER = 'Hammer'
        CROSSBOW = 'Crossbow'
        OTHER = 'Other'


    
    ability_item = models.CharField(max_length=150, choices=AbilityChoices.choices, default=AbilityChoices.NONE)
    ability = models.CharField(max_length=150)
    ability_description = models.TextField(null=True, blank=True)
    starting_coins = models.IntegerField(default=0, validators=[MinValueValidator(0),MaxValueValidator(4)])
    starting_boots = models.IntegerField(default=0, validators=[MinValueValidator(0),MaxValueValidator(4)])
    starting_bag = models.IntegerField(default=0, validators=[MinValueValidator(0),MaxValueValidator(4)])
    starting_tea = models.IntegerField(default=0, validators=[MinValueValidator(0),MaxValueValidator(4)])
    starting_hammer = models.IntegerField(default=0, validators=[MinValueValidator(0),MaxValueValidator(4)])
    starting_crossbow = models.IntegerField(default=0, validators=[MinValueValidator(0),MaxValueValidator(4)])
    starting_sword = models.IntegerField(default=0, validators=[MinValueValidator(0),MaxValueValidator(4)])
    starting_torch = models.IntegerField(default=1, validators=[MinValueValidator(0),MaxValueValidator(2)])
    def save(self, *args, **kwargs):
        self.component = 'Vagabond'  # Set the component type
        self.sorting = 4
        if not self.picture or self.picture == 'default_images/animals/default_animal.png':
            self.picture = animal_default_picture(self)

        # Check if the image field has changed (only works if the instance is already saved)
        if self.pk:  # If the object already exists in the database
            old_instance = Post.objects.get(pk=self.pk)

            field_name = 'picture'
            old_image = getattr(old_instance, field_name)
            new_image = getattr(self, field_name)
            if old_image != new_image or not self.small_icon:
                delete_old_image(getattr(old_instance,'small_icon'))
                self.process_and_save_small_icon(field_name)
        else:

            field_name = 'picture'
            self.process_and_save_small_icon(field_name)

        super().save(*args, **kwargs)  # Call the parent save method

    
    def wins(self):
        # plays = self.get_plays_queryset()
        # wins = plays.filter(win=True, game__test_match=False)
        wins = self.efforts.filter(win=True, game__test_match=False, game__final=True)
        return wins.count() if wins else 0
    
    def losses(self):
        # plays = self.get_plays_queryset()
        # wins = plays.filter(win=True, game__test_match=False)
        losses = self.efforts.filter(win=False, game__test_match=False, game__final=True)
        return losses.count() if losses else 0


    def get_wins_queryset(self):
        return self.efforts.all().filter(win=True, game__test_match=False, game__final=True)

    @property
    def winrate(self):
        points = 0
        wins = self.get_wins_queryset()

        for effort in wins:
            winners_count = effort.game.get_winners().count()
            if winners_count > 0:
                points += 1 / winners_count

        total_plays = self.get_plays_queryset().filter(game__test_match=False, game__final=True).count()
        return points / total_plays * 100 if total_plays > 0 else 0
    
    def stable_check(self):
        faction_threshold = Faction.objects.filter(official=True, status=1, component="Faction").count()
        plays = self.get_plays_queryset()
        official_faction_count = Faction.objects.filter(efforts__game__efforts__vagabond=self, official=True, status=1, component="Faction").distinct().count()
        unique_players = plays.aggregate(
                    total_players=Count('player', distinct=True)
                )['total_players']
        

        play_count = plays.count()

        win_count = self.wins()
        loss_count = self.losses()

        map_threshold = Map.objects.filter(official=True, status=1).count()
        official_map_count = Map.objects.filter(games__efforts__vagabond=self, official=True, status=1).distinct().count()
        deck_threshold = Deck.objects.filter(official=True, status=1).count() - 1
        official_deck_count = Deck.objects.filter(games__efforts__vagabond=self, official=True, status=1).distinct().count()



        if play_count >= game_threshold and self.status != 'Stable' and unique_players >= player_threshold and official_faction_count >= faction_threshold and official_map_count >= map_threshold and official_deck_count >= deck_threshold and win_count != 0 and loss_count != 0:
            stable_ready = True
        else:
            stable_ready = False
        print(f'Stable Ready: {stable_ready}, Plays: {play_count}/{game_threshold}, Players: {unique_players}/{player_threshold}, Official Factions: {official_faction_count}/{faction_threshold}')
        return (stable_ready, play_count, unique_players, official_faction_count, game_threshold, player_threshold, faction_threshold, official_map_count, map_threshold, official_deck_count, deck_threshold, win_count, loss_count)

class Faction(Post):
    class TypeChoices(models.TextChoices):
        MILITANT = 'M', _('Militant')  # Marked for translation
        INSURGENT = 'I', _('Insurgent')
        CLOCKWORK = 'C', _('Clockwork')
    
    class StyleChoices(models.TextChoices):
        NONE = 'N', _('None')  # Marked for translation
        LOW = 'L', _('Low')  # Marked for translation
        MODERATE = 'M', _('Moderate')  # Marked for translation
        HIGH = 'H', _('High')  # Marked for translation

    type = models.CharField(max_length=10, choices=TypeChoices.choices)
    reach = models.IntegerField(validators=[MinValueValidator(0), MaxValueValidator(10)], default=0)
    complexity = models.CharField(max_length=1, choices=StyleChoices.choices, default=StyleChoices.NONE)
    card_wealth = models.CharField(max_length=1, choices=StyleChoices.choices, default=StyleChoices.NONE)
    aggression = models.CharField(max_length=1, choices=StyleChoices.choices, default=StyleChoices.NONE)
    crafting_ability = models.CharField(max_length=1, choices=StyleChoices.choices, default=StyleChoices.NONE)


    def __add__(self, other):
        if isinstance(other, Faction):
            return self.reach + other.reach
        return NotImplemented
    

    def save(self, *args, **kwargs):
        
        # if not self.card_image:  # Only set if it's not already defined
        #     if self.reach != 0:
        #         self.card_image = f'default_images/adset_cards/ADSET_{self.get_type_display()}_{self.reach}.png'
        if not self.small_icon:  # Only set if it's not already defined
            self.small_icon = 'default_images/default_faction_icon.png'
        if not self.picture or self.picture == 'default_images/animals/default_animal.png': #Update animal if default was previously used.
            self.picture = animal_default_picture(self)

        if self.type == "C":
            self.component = 'Clockwork'
            self.sorting = 7
        else:
            self.component = 'Faction'  # Set the component type
            self.sorting = 1
            if self.reach > 0:
                self.component_snippet = f"{self.get_type_display()} ({self.reach} Reach)"
            else:
                self.component_snippet = f"{self.get_type_display()}"

        super().save(*args, **kwargs)  # Call the parent save method

    # def get_absolute_url(self):
    #     if self.component == 'Clockwork':
    #         return reverse('clockwork-detail', kwargs={'slug': self.slug})
    #     else:
    #         return reverse('faction-detail', kwargs={'slug': self.slug})
    
    def wins(self):
        plays = self.get_plays_queryset()
        wins = plays.filter(win=True, game__test_match=False, game__final=True)
        return wins.count() if plays else 0

    def losses(self):
        plays = self.get_plays_queryset()
        losses = plays.filter(win=False, game__test_match=False, game__final=True)
        return losses.count() if plays else 0

    def get_wins_queryset(self):
        return self.efforts.all().filter(win=True, game__test_match=False, game__final=True)

    @property
    def winrate(self):
        points = 0
        wins = self.get_wins_queryset()
        # print(len(wins))
        for effort in wins:
            winners_count = effort.game.get_winners().count()
            if winners_count > 0:
                points += 1 / winners_count

        total_plays = self.get_plays_queryset().filter(game__test_match=False, game__final=True).count()
        return points / total_plays * 100 if total_plays > 0 else 0


    @classmethod
    def leaderboard(cls, effort_qs, top_quantity=False, limit=5, game_threshold=10):
        """
        Get the top factions based on their win rate (default) or total efforts.
        If player_id is provided, get the top factions for that faction.
        Otherwise, get the top factions across all factions.
        The `limit` parameter controls how many factions to return.
        """
        # Start with the base queryset for factions
        queryset = cls.objects.all()

        # Filter for finished games only
        queryset = queryset.filter(efforts__in=effort_qs, efforts__game__final=True)
        # queryset = queryset.annotate(
        #     total_efforts=Count('efforts', filter=Q(efforts__in=effort_qs, efforts__game__final=True, efforts__game__test_match=False)),
        #     win_count=Count('efforts', filter=Q(efforts__in=effort_qs, efforts__win=True, efforts__game__final=True, efforts__game__test_match=False)),
        #     coalition_count=Count('efforts', filter=Q(efforts__in=effort_qs, efforts__win=True, efforts__game__coalition_win=True, efforts__game__final=True, efforts__game__test_match=False))
        # )

        # Now, annotate with the total efforts and win counts
        queryset = queryset.annotate(
            total_efforts=Count('efforts'),
            win_count=Count('efforts', filter=Q(efforts__win=True)),
            coalition_count=Count('efforts', filter=Q(efforts__win=True, efforts__game__coalition_win=True))
        )
        
        # Filter factions who have enough efforts (before doing the annotation)

        queryset = queryset.filter(total_efforts__gte=game_threshold)

        # Annotate with win_rate after filtering
        queryset = queryset.annotate(
            win_rate=Case(
                When(total_efforts=0, then=Value(0)),
                default=ExpressionWrapper(
                    (Cast(F('win_count'), FloatField()) - ( Cast(F('coalition_count'), FloatField()) / 2 )) / Cast(F('total_efforts'), FloatField()) * 100,  # Win rate as percentage
                    output_field=FloatField()
                ),
                output_field=FloatField()
            ),
            tourney_points=Case(
                When(total_efforts=0, then=Value(0)),
                default=ExpressionWrapper(
                    Cast(F('win_count'), FloatField()) - ( Cast(F('coalition_count'), FloatField()) / 2 ),  # Win rate as percentage
                    output_field=FloatField()
                ),
                output_field=FloatField()
            )
        )
        # Now we can order the queryset
        if top_quantity:
            # If top_quantity is True, order by total_efforts (most efforts) first
            return queryset.order_by('-tourney_points', '-win_rate')[:limit]
        else:
            # Otherwise, order by win_rate (highest win rate) first
            return queryset.order_by('-win_rate', '-total_efforts')[:limit]



    def stable_check(self):
        faction_threshold = Faction.objects.filter(official=True, status=1, component="Faction").count()
        plays = self.get_plays_queryset()
        official_faction_count = Faction.objects.filter(efforts__game__efforts__faction=self, official=True, status=1, component="Faction").distinct().count()
        if self.component == 'Faction':
            unique_players = plays.aggregate(
                        total_players=Count('player', distinct=True)
                    )['total_players']
        else:
            # unique_players = plays.aggregate(
            #         total_players=Count('player', distinct=True)
            #     )['total_players']
            unique_players = Profile.objects.filter(efforts__game__efforts__faction=self).distinct().count()

        play_count = plays.count()

        map_threshold = Map.objects.filter(official=True, status=1).count()
        official_map_count = Map.objects.filter(games__efforts__faction=self, official=True, status=1).distinct().count()
        deck_threshold = Deck.objects.filter(official=True, status=1).count() - 1
        official_deck_count = Deck.objects.filter(games__efforts__faction=self, official=True, status=1).distinct().count()


        win_count = self.wins()
        loss_count = self.losses()


        if play_count >= game_threshold and self.status != 'Stable' and unique_players >= player_threshold and official_faction_count >= faction_threshold and official_map_count >= map_threshold and official_deck_count >= deck_threshold and win_count != 0 and loss_count != 0:
            stable_ready = True
        else:
            stable_ready = False
        print(f'Stable Ready: {stable_ready}, Plays: {play_count}/{game_threshold}, Players: {unique_players}/{player_threshold}, Official Factions: {official_faction_count}/{faction_threshold}')
        return (stable_ready, play_count, unique_players, official_faction_count, game_threshold, player_threshold, faction_threshold, official_map_count, map_threshold, official_deck_count, deck_threshold, win_count, loss_count)



class Hireling(Post):
    class TypeChoices(models.TextChoices):
        PROMOTED = 'P', 'Promoted'
        DEMOTED = 'D', 'Demoted'

    type = models.CharField(max_length=1, choices=TypeChoices.choices)
    # promoted = models.ForeignKey('self', on_delete=models.SET_NULL, related_name='demoted_side', blank=True, null=True)
    # demoted = models.ForeignKey('self', on_delete=models.SET_NULL, related_name='promoted_side', blank=True, null=True)
    other_side = models.OneToOneField('self', null=True, blank=True, on_delete=models.SET_NULL, related_name='other_side_of')

    def save(self, commit=True, *args, **kwargs):
        self.component = 'Hireling'  # Set the component type
        self.sorting = 6
        if not self.picture or self.picture == 'default_images/animals/default_animal.png':
            self.picture = animal_default_picture(self)
        self.component_snippet = f"{self.get_type_display()}"
        # Call the parent class's save() method (this saves self to the database)

        # Check if the image field has changed (only works if the instance is already saved)
        if self.pk:  # If the object already exists in the database
            old_instance = Post.objects.get(pk=self.pk)

            field_name = 'picture'
            old_image = getattr(old_instance, field_name)
            new_image = getattr(self, field_name)
            if old_image != new_image or not self.small_icon:
                delete_old_image(getattr(old_instance,'small_icon'))
                self.process_and_save_small_icon(field_name)
        else:
            field_name = 'picture'
            self.process_and_save_small_icon(field_name)



        super().save(*args, **kwargs)

        # # Handle the reverse relationship for `other_side` before saving
        # if self.other_side:
        #     other_side = self.other_side

        #     # Check if the other side already has a reverse relationship to this object
        #     if other_side.other_side == self:
        #         # If it does, clear the reverse relationship first
        #         other_side.other_side = None
        #         other_side.save()  # Save the other side to ensure consistency

        #     # Now, set the reverse relationship from the other side
        #     other_side.other_side = self
        #     other_side.save()  # Save the other side to ensure consistency

        #     # Also ensure the current object (self) doesn't have any conflicting `other_side`
        #     if self != other_side:
        #         self.other_side = other_side
        #         self.save()

        return self

        # super().save(*args, **kwargs)  # Call the parent save method

    def stable_check(self):
        faction_threshold = Faction.objects.filter(official=True, status=1, component="Faction").count()
        plays = self.get_plays_queryset()
        official_faction_count = Faction.objects.filter(efforts__game__hirelings=self, official=True, status=1, component="Faction").distinct().count()
        unique_players = plays.aggregate(
                    total_players=Count('efforts__player', distinct=True)
                )['total_players']
        

        play_count = plays.count()

        map_threshold = Map.objects.filter(official=True, status=1).count()
        official_map_count = Map.objects.filter(games__hirelings=self, official=True, status=1).distinct().count()
        deck_threshold = Deck.objects.filter(official=True, status=1).count() - 1
        official_deck_count = Deck.objects.filter(games__hirelings=self, official=True, status=1).distinct().count()


        if play_count >= game_threshold and self.status != 'Stable' and unique_players >= player_threshold and official_faction_count >= faction_threshold and official_map_count >= map_threshold and official_deck_count >= deck_threshold:
            stable_ready = True
        else:
            stable_ready = False
        print(f'Stable Ready: {stable_ready}, Plays: {play_count}/{game_threshold}, Players: {unique_players}/{player_threshold}, Official Factions: {official_faction_count}/{faction_threshold}')
        return (stable_ready, play_count, unique_players, official_faction_count, game_threshold, player_threshold, faction_threshold, official_map_count, map_threshold, official_deck_count, deck_threshold)




# Game Pieces for Factions and Hirelings
class Piece(models.Model):
    class TypeChoices(models.TextChoices):
        WARRIOR = 'W'
        BUILDING = 'B'
        TOKEN = 'T'
        CARD = 'C'
        OTHER = 'O'
    name = models.CharField(max_length=30)
    quantity = models.IntegerField(validators=[MinValueValidator(1), MaxValueValidator(99)])
    description = models.TextField(null=True, blank=True)
    suited = models.BooleanField(default=False)
    parent = models.ForeignKey(Post, on_delete=models.CASCADE, related_name='pieces')
    type = models.CharField(max_length=1, choices=TypeChoices.choices)
    small_icon = models.ImageField(upload_to='small_component_icons/custom', null=True, blank=True)

    def __str__(self):
        return f"{self.name} (x{self.quantity})"

    def  get_absolute_url(self):
        match self.parent.component:
            case "Map":
                return reverse('map-detail', kwargs={'slug': self.parent.slug})
            case "Deck":
                return reverse('deck-detail', kwargs={'slug': self.parent.slug})
            case "Landmark":
                return reverse('landmark-detail', kwargs={'slug': self.parent.slug})
            case "Tweak":
                return reverse('tweak-detail', kwargs={'slug': self.parent.slug})
            case "Hireling":
                return reverse('hireling-detail', kwargs={'slug': self.parent.slug})        
            case "Vagabond":
                return reverse('vagabond-detail', kwargs={'slug': self.parent.slug})
            case "Clockwork":
                return reverse('clockwork-detail', kwargs={'slug': self.parent.slug})
            case _:
                return reverse('faction-detail', kwargs={'slug': self.parent.slug})
    

    def save(self, *args, **kwargs):

        # Check if the image field has changed (only works if the instance is already saved)
        if self.pk:  # If the object already exists in the database
            old_instance = Piece.objects.get(pk=self.pk)
            # List of fields to check and delete old images if necessary
            field_name = 'small_icon'

            old_image = getattr(old_instance, field_name)
            new_image = getattr(self, field_name)
            if old_image != new_image:
                delete_old_image(old_image)
        
        super().save(*args, **kwargs)
        # Resize images before saving
        if self.small_icon:
            resize_image(self.small_icon, 80)  # Resize small_icon

    def delete(self, *args, **kwargs):
        # Delete the old image file from storage before deleting the instance

        if self.small_icon:
            # Check if the file exists
            if os.path.isfile(self.small_icon.path):
                os.remove(self.small_icon.path)
        
        # Now delete the Piece instance
        super().delete(*args, **kwargs)


def animal_default_picture(instance):
    animal_lower = instance.animal.lower()
    if animal_lower == "mongoose" or animal_lower == "meerkat" or animal_lower == 'ferret':
        animal_lower = 'weasel'
    if animal_lower == "tortoise":
        animal_lower = 'turtle'
    if animal_lower == "puppy" or animal_lower == "canine" or animal_lower == "pup" or animal_lower == "hound" or animal_lower == "pooch":
        animal_lower = 'dog'
    if animal_lower == "warthog" or animal_lower == "pig" or animal_lower == "hog":
        animal_lower = 'boar'
    if animal_lower == "hare" or animal_lower == "bunny":
        animal_lower = 'rabbit'
    if animal_lower == "bees" or animal_lower == "wasp" or animal_lower == "wasps":
        animal_lower = 'bee'
    if animal_lower == "gator" or animal_lower == "aligator" or animal_lower == "croc" or animal_lower == "crocodile":
        animal_lower = 'lizard'
    if animal_lower == "person" or animal_lower == "people" or animal_lower == "man":
        animal_lower = 'human'
    if animal_lower == "tarsier":
        animal_lower = 'monkey'
    return check_for_image('animals', animal_lower)


## Using the os Media folder
def check_for_image(folder, image_png_name):
    # Construct the full path to the folder
    folder_path = os.path.join(settings.MEDIA_ROOT, f'default_images/{folder}')

    # Check if the folder exists
    if not os.path.exists(folder_path):
        print('Specified folder does not exist.')
        return f'default_images/{folder}/default_{folder}.png'

    matching_images = []    
    # Iterate through the files in the specified folder
    for filename in os.listdir(folder_path):
        if filename.endswith(f'{image_png_name}.png'):
            matching_images.append(os.path.join(f'default_images/{folder}', filename))  # Append the relative path

    if matching_images:
        return random.choice(matching_images)  # Return a random matching image
    return f'default_images/{folder}/default_{folder}.png'




class PNPAsset(models.Model):
    class CategoryChoices(models.TextChoices):
        FACTION = 'Faction'
        MAP = 'Map'
        DECK = 'Deck'
        VAGABOND = 'Vagabond'
        LANDMARK = 'Landmark'
        HIRELING = 'Hireling'
        ICONS = 'Icons'
        GUIDE = 'Guide'
        OTHER = 'Other'
    class FileChoices(models.TextChoices):
        PDF = 'PDF', 'PDF'
        XCF = 'XCF', 'XCF'
        PNG = 'PNG', 'PNG'
        JPEG = 'JPEG', 'JPEG'
        DOC = 'DOC', 'DOC'
        PSD = 'PSD', 'PSD'
        VIDEO = 'Video', 'Video'
        OTHER = 'Other'

    date_updated = models.DateTimeField(default=timezone.now)
    title = models.CharField(max_length=50)
    link = models.URLField(max_length=300)
    file_type = models.CharField(choices=FileChoices, max_length=10, default="XCF")
    category = models.CharField(choices=CategoryChoices, max_length=15)
    shared_by = models.ForeignKey(Profile, on_delete=models.SET_NULL, related_name='assets', null=True, blank=True)
    pinned = models.BooleanField(default=False)

    def get_absolute_url(self):
        return reverse('asset-detail', kwargs={'pk': self.id})

    class Meta:
        ordering = ['pinned', 'category', 'date_updated']






def component_pre_save(sender, instance, *args, **kwargs):
    # print('pre_save')
    if instance.slug is None:
        slugify_post_title(instance, save=False)

def expansion_pre_save(sender, instance, *args, **kwargs):
    # print('pre_save')
    if instance.slug is None:
        slugify_expansion_title(instance, save=False)

pre_save.connect(component_pre_save, sender=Map)
pre_save.connect(component_pre_save, sender=Deck)
pre_save.connect(component_pre_save, sender=Faction)
pre_save.connect(component_pre_save, sender=Vagabond)
pre_save.connect(component_pre_save, sender=Hireling)
pre_save.connect(component_pre_save, sender=Landmark)
pre_save.connect(component_pre_save, sender=Tweak)
pre_save.connect(expansion_pre_save, sender=Expansion)


def component_post_save(sender, instance, created, *args, **kwargs):
    # print('post_save')
    if created:
        slugify_post_title(instance, save=True)

def expansion_post_save(sender, instance, created, *args, **kwargs):
    # print('post_save')
    if created:
        slugify_expansion_title(instance, save=True)

post_save.connect(component_post_save, sender=Map)
post_save.connect(component_post_save, sender=Deck)
post_save.connect(component_post_save, sender=Faction)
post_save.connect(component_post_save, sender=Vagabond)
post_save.connect(component_post_save, sender=Hireling)
post_save.connect(component_post_save, sender=Landmark)
post_save.connect(component_post_save, sender=Tweak)
post_save.connect(expansion_post_save, sender=Expansion)

