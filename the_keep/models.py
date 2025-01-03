import os
from django.db import models
from django.db.models.signals import pre_save, post_save
from django.db.models import Count, F, ExpressionWrapper, FloatField, Q, Case, When, Value
from django.db.models.functions import Cast
from django.utils import timezone 
from datetime import timedelta
from django.urls import reverse
from django.core.validators import MinValueValidator, MaxValueValidator
import json
from PIL import Image
from django.apps import apps
from .utils import slugify_post_title, slugify_expansion_title
from the_gatehouse.models import Profile
from .utils import validate_hex_color
import boto3
import random
from django.conf import settings



stable_game_count = 10
stable_player_count = 5
stable_faction_count = 6

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


class Post(models.Model):
    class ComponentChoices(models.TextChoices):
        MAP = 'Map'
        DECK = 'Deck'
        HIRELING = 'Hireling'
        VAGABOND = 'Vagabond'
        LANDMARK = 'Landmark'
        FACTION = 'Faction'
        CLOCKWORK = 'Clockwork'
        TWEAK = 'Tweak'
    class StatusChoices(models.TextChoices):
        STABLE = '1','Stable'
        TESTING = '2', 'Testing'
        DEVELOPMENT = '3', 'Development'
        INACTIVE = '4', 'Inactive'

    title = models.CharField(max_length=35)
    animal = models.CharField(max_length=15, null=True, blank=True)
    slug = models.SlugField(unique=True, null=True, blank=True)
    expansion = models.ForeignKey(Expansion, on_delete=models.SET_NULL, null=True, blank=True, related_name='posts')
    lore = models.TextField(null=True, blank=True)
    description = models.TextField(null=True, blank=True)
    date_posted = models.DateTimeField(default=timezone.now)
    date_updated = models.DateTimeField(default=timezone.now)
    designer = models.ForeignKey(Profile, on_delete=models.SET_NULL, null=True, related_name='posts')
    artist = models.ForeignKey(Profile, on_delete=models.SET_NULL, null=True, related_name='artist_posts', blank=True)
    official = models.BooleanField(default=False)
    in_root_digital = models.BooleanField(default=False)
    # stable = models.BooleanField(default=False)
    status = models.CharField(max_length=15 , default=StatusChoices.DEVELOPMENT, choices=StatusChoices.choices)

    bgg_link = models.CharField(max_length=200, null=True, blank=True)
    tts_link = models.CharField(max_length=200, null=True, blank=True)
    ww_link = models.CharField(max_length=200, null=True, blank=True)
    wr_link = models.CharField(max_length=200, null=True, blank=True)
    pnp_link = models.CharField(max_length=200, null=True, blank=True)
    stl_link = models.CharField(max_length=200, null=True, blank=True)
    leder_games_link = models.CharField(max_length=200, null=True, blank=True)
    change_log = models.TextField(default='[]') 
    component = models.CharField(max_length=20, choices=ComponentChoices.choices, null=True, blank=True)
    sorting = models.IntegerField(default=10)
    based_on = models.ForeignKey('self', on_delete=models.SET_NULL, null=True, blank=True)
    small_icon = models.ImageField(upload_to='small_component_icons/custom', null=True, blank=True)
    picture = models.ImageField(upload_to='component_pictures', null=True, blank=True)
    board_image = models.ImageField(upload_to='boards', null=True, blank=True)
    card_image = models.ImageField(upload_to='cards', null=True, blank=True)
    bookmarks = models.ManyToManyField(Profile, related_name='bookmarkedposts', through='PostBookmark')

    objects = PostManager()


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
        
        super().save(*args, **kwargs)
        self._resize_image(self.small_icon, 40)  # Resize small_icon
        self._resize_image(self.board_image, 950)  # Resize board_image
        self._resize_image(self.card_image, 350)  # Resize card_image

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



    def add_change(self, note):
        changes = json.loads(self.change_log)
        change_entry = {
            'timestamp': timezone.now(),
            'note': note,
        }
        changes.append(change_entry) 
        self.change_log = json.dumps(changes) 
        self.save() 

    def get_change_log(self):
        return json.loads(self.change_log)
 
    def __str__(self):
        return self.title
    
    
    def get_plays_queryset(self):
        match self.component:
            case "Map" | "Deck" | "Landmark" | "Tweak" | "Hireling":
                return self.games.order_by('-date_posted') 
            case "Faction" | "Vagabond":
                return self.efforts.order_by('-date_posted') 
            case _:
                return Post.objects.none()  # or return an empty queryset

    def get_games_queryset(self):
        Game = apps.get_model('the_warroom', 'Game')
        match self.component:
            case "Map" | "Deck" | "Landmark" | "Tweak" | "Hireling":
                return self.games.order_by('-date_posted')  # Return a queryset directly
            case "Vagabond":
                return Game.objects.filter(efforts__vagabond=self)
            case "Faction":
                return Game.objects.filter(efforts__faction=self)

            case _:
                return Game.objects.none()  # No games if no component matches
            
    # def status(self):
    #     plays = self.get_plays_queryset()
    #     play_count = plays.count() if plays is not None else 0

    #     if self.stable:
    #         return "Stable"

    #     if play_count > 0:
    #         return "Testing"
    #     else:
    #         return "Development"

    def plays(self):
        plays = self.get_plays_queryset()
        return plays.count() if plays else 0
    

        
    class Meta:
        ordering = ['sorting', '-official', '-status', '-date_posted']

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
        super().save(*args, **kwargs)  # Call the parent save method


    def stable_check(self):
        plays = self.get_plays_queryset()
        official_faction_count = Faction.objects.filter(efforts__game__deck=self, official=True).distinct().count()
        unique_players = plays.aggregate(
                    total_players=Count('efforts__player', distinct=True)
                )['total_players']
        

        play_count = plays.count()

        if play_count >= stable_game_count and self.status != 'Stable' and unique_players >= stable_player_count and official_faction_count >= stable_faction_count:
            stable_ready = True
        else:
            stable_ready = False
        print(f'Stable Ready: {stable_ready}, Plays: {play_count}, Players: {unique_players}, Official Factions: {official_faction_count}')
        return (stable_ready, play_count, unique_players, official_faction_count)






class Landmark(Post):
    card_text = models.TextField(blank=True, null=True)
    def save(self, *args, **kwargs):
        self.component = 'Landmark'  # Set the component type
        self.sorting = 5
        super().save(*args, **kwargs)  # Call the parent save method

    def stable_check(self):
        plays = self.get_plays_queryset()
        official_faction_count = Faction.objects.filter(efforts__game__landmarks=self, official=True).distinct().count()
        unique_players = plays.aggregate(
                    total_players=Count('efforts__player', distinct=True)
                )['total_players']
        

        play_count = plays.count()
        stable = self.stable


        if play_count >= stable_game_count and self.status != 'Stable' and unique_players >= stable_player_count and official_faction_count >= stable_faction_count:
            stable_ready = True
        else:
            stable_ready = False
        print(f'Stable Ready: {stable_ready}, Plays: {play_count}, Players: {unique_players}, Official Factions: {official_faction_count}')
        return (stable_ready, play_count, unique_players, official_faction_count)

class Tweak(Post):
    def save(self, *args, **kwargs):
        self.component = 'Tweak'  # Set the component type
        self.sorting = 8
        super().save(*args, **kwargs)  # Call the parent save method

    def stable_check(self):
        plays = self.get_plays_queryset()
        official_faction_count = Faction.objects.filter(efforts__game__tweaks=self, official=True).distinct().count()
        unique_players = plays.aggregate(
                    total_players=Count('efforts__player', distinct=True)
                )['total_players']
        

        play_count = plays.count()


        if play_count >= stable_game_count and self.status != 'Stable' and unique_players >= stable_player_count and official_faction_count >= stable_faction_count:
            stable_ready = True
        else:
            stable_ready = False
        print(f'Stable Ready: {stable_ready}, Plays: {play_count}, Players: {unique_players}, Official Factions: {official_faction_count}')
        return (stable_ready, play_count, unique_players, official_faction_count)

class Map(Post):
    clearings = models.IntegerField(default=12)
    fixed_clearings = models.BooleanField(default=False)
    default_landmark = models.ForeignKey(Landmark, on_delete=models.PROTECT, null=True, blank=True)
    def save(self, *args, **kwargs):
        self.component = 'Map'  # Set the component type
        self.sorting = 2
        if not self.picture:
            self.picture = 'default_images/default_map.png'
        super().save(*args, **kwargs)  # Call the parent save method

    def stable_check(self):
        plays = self.get_plays_queryset()
        official_faction_count = Faction.objects.filter(efforts__game__map=self, official=True).distinct().count()
        unique_players = plays.aggregate(
                    total_players=Count('efforts__player', distinct=True)
                )['total_players']
        
        play_count = plays.count()
 


        if play_count >= stable_game_count and self.status != 'Stable' and unique_players >= stable_player_count and official_faction_count >= stable_faction_count:
            stable_ready = True
        else:
            stable_ready = False
        print(f'Stable Ready: {stable_ready}, Plays: {play_count}, Players: {unique_players}, Official Factions: {official_faction_count}')

        return (stable_ready, play_count, unique_players, official_faction_count)




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
        if not self.picture:
            self.picture = animal_default_picture(self)
        elif self.picture == 'default_images/animals/default_animal.png':
            self.picture = animal_default_picture(self)
        super().save(*args, **kwargs)  # Call the parent save method

    
    def wins(self):
        # plays = self.get_plays_queryset()
        # wins = plays.filter(win=True, game__test_match=False)
        wins = self.efforts.filter(win=True, game__test_match=False)
        return wins.count() if wins else 0

    def get_wins_queryset(self):
        return self.efforts.all().filter(win=True, game__test_match=False)

    @property
    def winrate(self):
        points = 0
        wins = self.get_wins_queryset()

        for effort in wins:
            winners_count = effort.game.get_winners().count()
            if winners_count > 0:
                points += 1 / winners_count

        total_plays = self.get_plays_queryset().filter(game__test_match=False).count()
        return points / total_plays * 100 if total_plays > 0 else 0
    
    def stable_check(self):
        plays = self.get_plays_queryset()
        official_faction_count = Faction.objects.filter(efforts__game__efforts__vagabond=self, official=True).distinct().count()
        unique_players = plays.aggregate(
                    total_players=Count('player', distinct=True)
                )['total_players']
        

        play_count = plays.count()


        if play_count >= stable_game_count and self.status != 'Stable' and unique_players >= stable_player_count and official_faction_count >= stable_faction_count:
            stable_ready = True
        else:
            stable_ready = False
        print(f'Stable Ready: {stable_ready}, Plays: {play_count}, Players: {unique_players}, Official Factions: {official_faction_count}')
        return (stable_ready, play_count, unique_players, official_faction_count)

class Faction(Post):
    class TypeChoices(models.TextChoices):
        MILITANT = 'M'
        INSURGENT = 'I'
        CLOCKWORK = 'C'
    class StyleChoices(models.TextChoices):
        NONE = 'N'
        LOW = 'L'
        MODERATE = 'M'
        HIGH = 'H'                

    type = models.CharField(max_length=10, choices=TypeChoices.choices)
    reach = models.IntegerField(validators=[MinValueValidator(0), MaxValueValidator(10)], default=0)
    complexity = models.CharField(max_length=1, choices=StyleChoices.choices, default=StyleChoices.NONE)
    card_wealth = models.CharField(max_length=1, choices=StyleChoices.choices, default=StyleChoices.NONE)
    aggression = models.CharField(max_length=1, choices=StyleChoices.choices, default=StyleChoices.NONE)
    crafting_ability = models.CharField(max_length=1, choices=StyleChoices.choices, default=StyleChoices.NONE)
    color = models.CharField(
        max_length=7,
        blank=True,
        null=True,
        validators=[validate_hex_color],
        help_text="Enter a hex color code (e.g., #RRGGBB)."
    )

    def __add__(self, other):
        if isinstance(other, Faction):
            return self.reach + other.reach
        return NotImplemented
    

    def save(self, *args, **kwargs):
        
        if not self.card_image:  # Only set if it's not already defined
            if self.reach != 0:
                self.card_image = f'default_images/adset_cards/ADSET_{self.get_type_display()}_{self.reach}.png'
        if not self.small_icon:  # Only set if it's not already defined
            self.small_icon = 'default_images/default_faction_icon.png'
        if not self.picture:
            self.picture = animal_default_picture(self)
        elif self.picture == 'default_images/animals/default_animal.png': #Update animal if default was previously used.
            self.picture = animal_default_picture(self)

        if self.type == "C":
            self.component = 'Clockwork'
            self.sorting = 7
        else:
            self.component = 'Faction'  # Set the component type
            self.sorting = 1
        super().save(*args, **kwargs)  # Call the parent save method

    # def get_absolute_url(self):
    #     if self.component == 'Clockwork':
    #         return reverse('clockwork-detail', kwargs={'slug': self.slug})
    #     else:
    #         return reverse('faction-detail', kwargs={'slug': self.slug})
    
    def wins(self):
        plays = self.get_plays_queryset()
        wins = plays.filter(win=True, game__test_match=False)
        return wins.count() if plays else 0

    def get_wins_queryset(self):
        return self.efforts.all().filter(win=True, game__test_match=False)

    @property
    def winrate(self):
        points = 0
        wins = self.get_wins_queryset()
        # print(len(wins))
        for effort in wins:
            winners_count = effort.game.get_winners().count()
            if winners_count > 0:
                points += 1 / winners_count

        total_plays = self.get_plays_queryset().filter(game__test_match=False).count()
        return points / total_plays * 100 if total_plays > 0 else 0

    @classmethod
    def top_factions(cls, player_id=None, top_quantity=False, tournament=None, round=None, limit=5, game_threshold=10):
        """
        Get the top factions based on their win rate (default) or total efforts.
        If player_id is provided, get the top factions for that faction.
        Otherwise, get the top factions across all factions.
        The `limit` parameter controls how many factions to return.
        """
        # Start with the base queryset for factions
        queryset = cls.objects.all()

        # If a tournament is provided, filter efforts that are related to that tournament
        if tournament:
            queryset = queryset.filter(
                efforts__game__round__tournament=tournament  # Filter efforts linked to a specific tournament
            )

        # If a round is provided, filter efforts that are related to that round
        if round:
            queryset = queryset.filter(
                efforts__game__round=round  # Filter efforts linked to a specific round
            )
        # Now, annotate with the total efforts and win counts
        queryset = queryset.annotate(
            total_efforts=Count('efforts', filter=Q(efforts__player_id=player_id) if player_id else Q()),
            win_count=Count('efforts', filter=Q(efforts__win=True, efforts__player_id=player_id) if player_id else Q(efforts__win=True)),
            coalition_count=Count('efforts', filter=Q(efforts__win=True, efforts__game__coalition_win=True, efforts__player_id=player_id) if player_id else Q(efforts__win=True, efforts__game__coalition_win=True))
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
        plays = self.get_plays_queryset()
        official_faction_count = Faction.objects.filter(efforts__game__efforts__faction=self, official=True).distinct().count()
        unique_players = plays.aggregate(
                    total_players=Count('player', distinct=True)
                )['total_players']
        

        play_count = plays.count()
 


        if play_count >= stable_game_count and self.status != 'Stable' and unique_players >= stable_player_count and official_faction_count >= stable_faction_count:
            stable_ready = True
        else:
            stable_ready = False
        print(f'Stable Ready: {stable_ready}, Plays: {play_count}, Players: {unique_players}, Official Factions: {official_faction_count}')
        return (stable_ready, play_count, unique_players, official_faction_count)

    class Meta:
        ordering = ['-component', '-official', '-status', '-date_posted']


class Hireling(Post):
    class TypeChoices(models.TextChoices):
        PROMOTED = 'P'
        DEMOTED = 'D'

    type = models.CharField(max_length=1, choices=TypeChoices.choices)
    def save(self, *args, **kwargs):
        self.component = 'Hireling'  # Set the component type
        self.sorting = 6
        if not self.picture:
            self.picture = animal_default_picture(self)
        elif self.picture == 'default_images/animals/default_animal.png':
            self.picture = animal_default_picture(self)

        super().save(*args, **kwargs)  # Call the parent save method
    # def get_absolute_url(self):
    #     return reverse('hireling-detail', kwargs={'slug': self.slug})
    
# Game Pieces for Factions and Hirelings
class Piece(models.Model):
    class TypeChoices(models.TextChoices):
        WARRIOR = 'W'
        BUILDING = 'B'
        TOKEN = 'T'
        CARD = 'C'
        OTHER = 'O'
    name = models.CharField(max_length=30)
    quantity = models.IntegerField(validators=[MinValueValidator(1), MaxValueValidator(40)])
    description = models.TextField(null=True, blank=True)
    suited = models.BooleanField(default=False)
    parent = models.ForeignKey(Post, on_delete=models.CASCADE, related_name='pieces')
    type = models.CharField(max_length=1, choices=TypeChoices.choices)

    def __str__(self):
        return f"{self.name} (x{self.quantity})"

    def stable_check(self):
        plays = self.get_plays_queryset()
        official_faction_count = Faction.objects.filter(efforts__game__hirelings=self, official=True).distinct().count()
        unique_players = plays.aggregate(
                    total_players=Count('efforts__player', distinct=True)
                )['total_players']
        

        play_count = plays.count()



        if play_count >= stable_game_count and self.status != 'Stable' and unique_players >= stable_player_count and official_faction_count >= stable_faction_count:
            stable_ready = True
        else:
            stable_ready = False
        print(f'Stable Ready: {stable_ready}, Plays: {play_count}, Players: {unique_players}, Official Factions: {official_faction_count}')
        return (stable_ready, play_count, unique_players, official_faction_count)


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



## USING AWS S3
# def check_for_aws_image(folder, image_png_name):
#     s3_client = boto3.client(
#         's3',
#         aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
#         aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
#         region_name=settings.AWS_S3_REGION_NAME,
#     )
    
#     bucket_name = settings.AWS_STORAGE_BUCKET_NAME
#     folder_name = f'{folder}/'  # S3 Folder

#     # List objects in folder
#     response = s3_client.list_objects_v2(Bucket=bucket_name, Prefix=folder_name)

#     matching_images = []
#     # Check if 'Contents' key is in the response
#     if 'Contents' in response:
#         for obj in response['Contents']:
#             if obj['Key'].endswith(f'{image_png_name}.png'):
#                 matching_images.append(obj['Key']) 
#     else:
#         print('No objects found in the specified folder.')
#     if matching_images:
#         return random.choice(matching_images) 
#     return f'{folder}/default_{folder}.png'





class PNPAsset(models.Model):
    class CategoryChoices(models.TextChoices):
        FACTION = 'Faction'
        MAP = 'Map'
        DECK = 'Deck'
        VAGABOND = 'Vagabond'
        LANDMARK = 'Landmark'
        HIRELING = 'Hireling'
        OTHER = 'Other'
    class FileChoices(models.TextChoices):
        PDF = 'PDF', 'PDF'
        XCF = 'XCF', 'XCF'
        PNG = 'PNG', 'PNG'
        JPEG = 'JPEG', 'JPEG'
        DOC = 'DOC', 'DOC'
        OTHER = 'Other'

    date_updated = models.DateTimeField(default=timezone.now)
    title = models.CharField(max_length=50)
    link = models.URLField(max_length=300)
    file_type = models.CharField(choices=FileChoices, max_length=10, default="XCF")
    category = models.CharField(choices=CategoryChoices, max_length=15)
    shared_by = models.ForeignKey(Profile, on_delete=models.SET_NULL, related_name='assets', null=True, blank=True)
    pinned = models.BooleanField(default=False)

    class Meta:
        ordering = ['category', 'date_updated']






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

