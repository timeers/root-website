
from django.db import models
from django.db.models.signals import pre_save, post_save
from django.utils import timezone 
from django.contrib.auth.models import User
from django.urls import reverse

from django.core.validators import MinValueValidator, MaxValueValidator
import json

from .utils import slugify_instance_title
# import random
# from django.utils.text import slugify

# from .utils import slugify_instance_title 

class Expansion(models.Model):
    name = models.CharField(max_length=100)
    designer = models.ForeignKey(User, on_delete=models.SET_NULL, null=True,)
    description = models.TextField(null=True, blank=True)
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


class Post(models.Model):
    COMPONENT_CHOICES = [
        ('Map', 'Map'),
        ('Deck', 'Deck'),
        ('Hireling', 'Hireling'),
        ('Vagabond', 'Vagabond'),
        ('Landmark', 'Landmark'),
        ('Faction', 'Faction'),
]
    title = models.CharField(max_length=30)
    slug = models.SlugField(unique=True, null=True, blank=True)
    expansion = models.ForeignKey(Expansion, on_delete=models.SET_NULL, null=True, blank=True)
    description = models.TextField(null=True, blank=True)
    date_posted = models.DateTimeField(default=timezone.now)
    date_updated = models.DateTimeField(default=timezone.now)
    designer = models.ForeignKey(User, on_delete=models.SET_NULL, null=True,)
    artist = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='artist_posts', blank=True)
    official = models.BooleanField(default=False)
    stable = models.BooleanField(default=False)
    bgg_link = models.CharField(max_length=200, null=True, blank=True)
    tts_link = models.CharField(max_length=200, null=True, blank=True)
    ww_link = models.CharField(max_length=200, null=True, blank=True)
    wr_link = models.CharField(max_length=200, null=True, blank=True)
    pnp_link = models.CharField(max_length=200, null=True, blank=True)
    change_log = models.TextField(default='[]') 
    component = models.CharField(max_length=10, choices=COMPONENT_CHOICES, null=True, blank=True)

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
    ability = models.CharField(max_length=150)
    starting_coins = models.IntegerField(default=0, validators=[MinValueValidator(0),MaxValueValidator(4)])
    starting_boots = models.IntegerField(default=0, validators=[MinValueValidator(0),MaxValueValidator(4)])
    starting_bag = models.IntegerField(default=0, validators=[MinValueValidator(0),MaxValueValidator(4)])
    starting_tea = models.IntegerField(default=0, validators=[MinValueValidator(0),MaxValueValidator(4)])
    starting_hammer = models.IntegerField(default=0, validators=[MinValueValidator(0),MaxValueValidator(4)])
    starting_crossbow = models.IntegerField(default=0, validators=[MinValueValidator(0),MaxValueValidator(4)])
    starting_torch = models.IntegerField(default=1, validators=[MinValueValidator(0),MaxValueValidator(2)])
    starting_other = models.IntegerField(default=0, validators=[MinValueValidator(0),MaxValueValidator(5)])
    def save(self, *args, **kwargs):
        self.component = 'Vagabond'  # Set the component type
        super().save(*args, **kwargs)  # Call the parent save method
    def get_absolute_url(self):
        return reverse('vagabond-detail', kwargs={'slug': self.slug})
    
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
    reach = models.IntegerField(validators=[MinValueValidator(1), MaxValueValidator(10)])
    complexity = models.CharField(max_length=1, choices=STYLE_CHOICES)
    card_wealth = models.CharField(max_length=1, choices=STYLE_CHOICES)
    aggression = models.CharField(max_length=1, choices=STYLE_CHOICES)
    crafting_ability = models.CharField(max_length=1, choices=STYLE_CHOICES)
    based_on = models.ForeignKey('self', on_delete=models.SET_NULL, null=True, blank=True)

    def __add__(self, other):
        if isinstance(other, Faction):
            return self.reach + other.reach
        return NotImplemented
    def save(self, *args, **kwargs):
        self.component = 'Faction'  # Set the component type
        super().save(*args, **kwargs)  # Call the parent save method
    def get_absolute_url(self):
        return reverse('faction-detail', kwargs={'slug': self.slug})
    
class Hireling(Post):
    PROMOTED = 'P'
    DEMOTED = 'D'
    TYPE_CHOICES = [
        (PROMOTED, 'Promoted'),
        (DEMOTED, 'Demoted'),
    ]
    animal = models.CharField(max_length=15)
    type = models.CharField(max_length=1, choices=TYPE_CHOICES)
    based_on = models.ForeignKey(Faction, on_delete=models.SET_NULL, null=True, blank=True)
    def save(self, *args, **kwargs):
        self.component = 'Hireling'  # Set the component type
        super().save(*args, **kwargs)  # Call the parent save method
    def get_absolute_url(self):
        return reverse('hireling-detail', kwargs={'slug': self.slug})
    


def component_pre_save(sender, instance, *args, **kwargs):
    print('pre_save')
    if instance.slug is None:
        slugify_instance_title(instance, save=False)

pre_save.connect(component_pre_save, sender=Map)
pre_save.connect(component_pre_save, sender=Deck)
pre_save.connect(component_pre_save, sender=Faction)
pre_save.connect(component_pre_save, sender=Vagabond)
pre_save.connect(component_pre_save, sender=Hireling)
pre_save.connect(component_pre_save, sender=Landmark)


def component_post_save(sender, instance, created, *args, **kwargs):
    print('post_save')
    if created:
        slugify_instance_title(instance, save=True)

post_save.connect(component_post_save, sender=Map)
post_save.connect(component_post_save, sender=Deck)
post_save.connect(component_post_save, sender=Faction)
post_save.connect(component_post_save, sender=Vagabond)
post_save.connect(component_post_save, sender=Hireling)
post_save.connect(component_post_save, sender=Landmark)

