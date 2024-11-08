
from django.db import models
from django.db.models import Q
from django.db.models.signals import pre_save, post_save
from django.utils import timezone 
from datetime import timedelta
from django.contrib.auth.models import User
from django.urls import reverse
from PIL import Image
from django.core.validators import MinValueValidator, MaxValueValidator
import json

from .utils import slugify_instance_title, default_picture
from the_gatehouse.models import Profile
# import random
# from django.utils.text import slugify

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
    designer = models.ForeignKey(Profile, on_delete=models.SET_NULL, null=True,)
    description = models.TextField(null=True, blank=True)
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
        return Faction.objects.filter(expansion=self)
    
    def get_absolute_url(self):
        return reverse('expansion-detail', kwargs={'slug': self.slug})


class Post(models.Model):
    COMPONENT_CHOICES = [
        ('Map', 'Map'),
        ('Deck', 'Deck'),
        ('Hireling', 'Hireling'),
        ('Vagabond', 'Vagabond'),
        ('Landmark', 'Landmark'),
        ('Faction', 'Faction'),
]
    title = models.CharField(max_length=35)
    slug = models.SlugField(unique=True, null=True, blank=True)
    expansion = models.ForeignKey(Expansion, on_delete=models.SET_NULL, null=True, blank=True)
    lore = models.TextField(null=True, blank=True)
    description = models.TextField(null=True, blank=True)
    date_posted = models.DateTimeField(default=timezone.now)
    date_updated = models.DateTimeField(default=timezone.now)
    designer = models.ForeignKey(Profile, on_delete=models.SET_NULL, null=True)
    artist = models.ForeignKey(Profile, on_delete=models.SET_NULL, null=True, related_name='artist_posts', blank=True)
    official = models.BooleanField(default=False)
    stable = models.BooleanField(default=False)
    bgg_link = models.CharField(max_length=200, null=True, blank=True)
    tts_link = models.CharField(max_length=200, null=True, blank=True)
    ww_link = models.CharField(max_length=200, null=True, blank=True)
    wr_link = models.CharField(max_length=200, null=True, blank=True)
    pnp_link = models.CharField(max_length=200, null=True, blank=True)
    change_log = models.TextField(default='[]') 
    component = models.CharField(max_length=10, choices=COMPONENT_CHOICES, null=True, blank=True)
    based_on = models.ForeignKey('self', on_delete=models.SET_NULL, null=True, blank=True)
    small_icon = models.ImageField(upload_to='small_component_icons', null=True, blank=True)
    picture = models.ImageField(upload_to='component_pictures', null=True, blank=True)

    objects = PostManager()

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)

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
    

    # This might need to be moved to each type of component? map-detail, deck-detail etc.
    def get_absolute_url(self):
        return reverse('post-detail', kwargs={'pk': self.pk})
    
    def get_plays_queryset(self):
        match self.component:
            case "Map" | "Deck" | "Landmark" | "Hireling":
                return self.game_set.all()
            case "Faction" | "Vagabond":
                return self.effort_set.all()
            case _:
                return Post.objects.none()  # or return an empty queryset
        

    def status(self):
        plays = self.get_plays_queryset()
        play_count = plays.count() if plays is not None else 0
        
        if play_count > 0:
            return "Stable" if self.stable else "Testing"
        else:
            # Check if date_updated is more than 1 year old
            if self.date_updated < timezone.now() - timedelta(days=365):
                return "Inactive"
            else:
                return "Development"

    def plays(self):
        plays = self.get_plays_queryset()
        return plays.count() if plays else 0

    def plays_since_update(self):
        plays = self.get_plays_queryset()
        if plays:
            new_plays = plays.filter(date_posted__gt=self.date_updated)
            return new_plays.count()
        return 0
    
    def stable_ready(self):
        plays = self.plays_since_update()
        stable = self.stable
        if plays > 9 and stable == False:
            return True
        return False
        

class Map(Post):
    clearings = models.IntegerField(default=12)
    def save(self, *args, **kwargs):
        self.component = 'Map'  # Set the component type
        super().save(*args, **kwargs)  # Call the parent save method
# This might need to be moved to each type of component? map-detail, deck-detail etc.
    def get_absolute_url(self):
        return reverse('map-detail', kwargs={'slug': self.slug})




class Deck(Post):

    card_total = models.IntegerField()
    def save(self, *args, **kwargs):
        self.component = 'Deck'  # Set the component type
        super().save(*args, **kwargs)  # Call the parent save method
    def get_absolute_url(self):
        return reverse('deck-detail', kwargs={'slug': self.slug})

class Landmark(Post):

    card_text = models.TextField()
    def save(self, *args, **kwargs):
        self.component = 'Landmark'  # Set the component type
        super().save(*args, **kwargs)  # Call the parent save method
    def get_absolute_url(self):
        return reverse('landmark-detail', kwargs={'slug': self.slug})


class Vagabond(Post):

    animal = models.CharField(max_length=15)
    ability_item = models.CharField(max_length=150, null=True, blank=True)
    ability = models.CharField(max_length=150)
    ability_description = models.TextField(null=True, blank=True)
    starting_coins = models.IntegerField(default=0, validators=[MinValueValidator(0),MaxValueValidator(4)])
    starting_boots = models.IntegerField(default=0, validators=[MinValueValidator(0),MaxValueValidator(4)])
    starting_bag = models.IntegerField(default=0, validators=[MinValueValidator(0),MaxValueValidator(4)])
    starting_tea = models.IntegerField(default=0, validators=[MinValueValidator(0),MaxValueValidator(4)])
    starting_hammer = models.IntegerField(default=0, validators=[MinValueValidator(0),MaxValueValidator(4)])
    starting_crossbow = models.IntegerField(default=0, validators=[MinValueValidator(0),MaxValueValidator(4)])
    starting_torch = models.IntegerField(default=1, validators=[MinValueValidator(0),MaxValueValidator(2)])
    def save(self, *args, **kwargs):
        self.component = 'Vagabond'  # Set the component type
        if not self.picture:
            self.picture = default_picture(self)
        super().save(*args, **kwargs)  # Call the parent save method






    def get_absolute_url(self):
        return reverse('vagabond-detail', kwargs={'slug': self.slug})
    
    def wins(self):
        plays = self.get_plays_queryset()
        wins = plays.filter(win=True)
        return wins.count() if plays else 0

    def get_wins_queryset(self):
        return self.effort_set.all().filter(win=True)

    @property
    def winrate(self):
        points = 0
        wins = self.get_wins_queryset()

        for effort in wins:
            winners_count = effort.game.get_winners().count()
            if winners_count > 0:
                points += 1 / winners_count

        total_plays = self.plays()
        return points / total_plays * 100 if total_plays > 0 else 0


class Faction(Post):
    MILITANT = 'M'
    INSURGENT = 'I'
    TYPE_CHOICES = [
        (MILITANT, 'Militant'),
        (INSURGENT, 'Insurgent'),
    ]

    NONE = 'N'
    LOW = 'L'
    MODERATE = 'M'
    HIGH = 'H'
    STYLE_CHOICES = [
        (NONE, 'N'),
        (LOW, 'L'),
        (MODERATE, 'M'),
        (HIGH, 'H'),
    ]

    animal = models.CharField(max_length=15)
    type = models.CharField(max_length=1, choices=TYPE_CHOICES)
    reach = models.IntegerField(validators=[MinValueValidator(0), MaxValueValidator(10)])
    complexity = models.CharField(max_length=1, choices=STYLE_CHOICES)
    card_wealth = models.CharField(max_length=1, choices=STYLE_CHOICES)
    aggression = models.CharField(max_length=1, choices=STYLE_CHOICES)
    crafting_ability = models.CharField(max_length=1, choices=STYLE_CHOICES)
    adset_card = models.ImageField(default=None, upload_to='adset_cards', blank=True, null=True)
    clockwork = models.BooleanField(default=False)

    def __add__(self, other):
        if isinstance(other, Faction):
            return self.reach + other.reach
        return NotImplemented
    

    def save(self, *args, **kwargs):
        if not self.adset_card:  # Only set if it's not already defined
            if self.type == self.INSURGENT:
                self.adset_card = 'ADSET_Insurgent.png'
            elif self.type == self.MILITANT:
                self.adset_card = 'ADSET_Militant.png'
        if not self.small_icon:  # Only set if it's not already defined
            self.small_icon = 'default_faction_icon.png'
        if not self.picture:
            self.picture = default_picture(self)
                
        self.component = 'Faction'  # Set the component type
        super().save(*args, **kwargs)  # Call the parent save method


        # img = Image.open(self.small_icon.path)
        # # Determine the largest dimension
        # max_size = 40
        # if img.height > max_size or img.width > max_size:
        #     # print('resizing image')

        #     # Calculate the new size while maintaining the aspect ratio
        #     if img.width > img.height:
        #         ratio = max_size / img.width
        #         new_size = (max_size, int(img.height * ratio))
        #     else:
        #         ratio = max_size / img.height
        #         new_size = (int(img.width * ratio), max_size)

        #     img = img.resize(new_size, Image.LANCZOS)
        #     img.save(self.small_icon.path)
        #     # print(f'Resized image saved at: {self.small_icon.path}')
        # else:
        #     # print('original image')
        #     img.save(self.small_icon.path)
        #     # print(f'Original image saved at: {self.small_icon.path}')

    def get_absolute_url(self):
        return reverse('faction-detail', kwargs={'slug': self.slug})
    
    def wins(self):
        plays = self.get_plays_queryset()
        wins = plays.filter(win=True)
        return wins.count() if plays else 0

    def get_wins_queryset(self):
        return self.effort_set.all().filter(win=True)

    @property
    def winrate(self):
        points = 0
        wins = self.get_wins_queryset()

        for effort in wins:
            winners_count = effort.game.get_winners().count()
            if winners_count > 0:
                points += 1 / winners_count

        total_plays = self.plays()
        return points / total_plays * 100 if total_plays > 0 else 0



class Hireling(Post):
    PROMOTED = 'P'
    DEMOTED = 'D'
    TYPE_CHOICES = [
        (PROMOTED, 'Promoted'),
        (DEMOTED, 'Demoted'),
    ]
    animal = models.CharField(max_length=15)
    type = models.CharField(max_length=1, choices=TYPE_CHOICES)
    def save(self, *args, **kwargs):
        self.component = 'Hireling'  # Set the component type
        if not self.picture:
            self.picture = default_picture(self)
        super().save(*args, **kwargs)  # Call the parent save method
    def get_absolute_url(self):
        return reverse('hireling-detail', kwargs={'slug': self.slug})
    
# Game Pieces for Factions and Hirelings
class Piece(models.Model):
    name = models.CharField(max_length=30)
    quantity = models.IntegerField(validators=[MinValueValidator(1), MaxValueValidator(40)])
    description = models.TextField(null=True, blank=True)
    suited = models.BooleanField(default=False)
    faction = models.ForeignKey(Faction, on_delete=models.CASCADE, null=True, blank=True, related_name='%(class)s')
    vagabond = models.ForeignKey(Vagabond, on_delete=models.CASCADE, null=True, blank=True, related_name='%(class)s')
    hireling = models.ForeignKey(Hireling, on_delete=models.CASCADE, null=True, blank=True, related_name='%(class)s')

    class Meta:
        abstract = True  # This ensures that Piece won't create a table

    def __str__(self):
        return f"{self.name} (x{self.quantity})"

class Warrior(Piece):
    pass

class Building(Piece):
    pass

class Token(Piece):
    pass

class Card(Piece):
    pass

class OtherPiece(Piece):
    pass





def component_pre_save(sender, instance, *args, **kwargs):
    # print('pre_save')
    if instance.slug is None:
        slugify_instance_title(instance, save=False)

pre_save.connect(component_pre_save, sender=Map)
pre_save.connect(component_pre_save, sender=Deck)
pre_save.connect(component_pre_save, sender=Faction)
pre_save.connect(component_pre_save, sender=Vagabond)
pre_save.connect(component_pre_save, sender=Hireling)
pre_save.connect(component_pre_save, sender=Landmark)
pre_save.connect(component_pre_save, sender=Expansion)


def component_post_save(sender, instance, created, *args, **kwargs):
    # print('post_save')
    if created:
        slugify_instance_title(instance, save=True)

post_save.connect(component_post_save, sender=Map)
post_save.connect(component_post_save, sender=Deck)
post_save.connect(component_post_save, sender=Faction)
post_save.connect(component_post_save, sender=Vagabond)
post_save.connect(component_post_save, sender=Hireling)
post_save.connect(component_post_save, sender=Landmark)
post_save.connect(component_post_save, sender=Expansion)

