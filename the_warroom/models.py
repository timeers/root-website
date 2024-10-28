from django.contrib.auth.models import User
from django.db import models
from django.db.models import Q
from django.utils import timezone 
from django.core.validators import MinValueValidator
from the_gatehouse.models import Profile
from blog.models import Deck, Map, Faction, Landmark, Hireling, Vagabond


class Game(models.Model):
    ASYNC = 'A'
    LIVE = 'L'
    TYPE_CHOICES = [
        (ASYNC, 'Async'),
        (LIVE, 'Live'),
    ]
    TTS = 'TTS'
    HRF = 'HRF'
    DWD = 'DWD'
    IRL = 'IRL'
    ETC = 'ETC'
    PLATFORM_CHOICES = [
        (TTS, 'Tabletop Simulator'),
        (IRL, 'In Person'),
        (DWD, 'Root Digital'),
        (HRF, 'hrf.com'),
        (ETC, 'Other'),
    ]
    # Required
    type = models.CharField(max_length=1, choices=TYPE_CHOICES, default=LIVE)
    platform = models.CharField(max_length=3, choices=PLATFORM_CHOICES, default=DWD)
    deck = models.ForeignKey(Deck, on_delete=models.SET_NULL, null=True, blank=True)
    map = models.ForeignKey(Map, on_delete=models.SET_NULL, null=True, blank=True)

    league = models.BooleanField(default=False)
    
    # Optional
    landmarks = models.ManyToManyField(Landmark, blank=True)
    hirelings = models.ManyToManyField(Hireling, blank=True)
    undrafted = models.ForeignKey(Faction, on_delete=models.SET_NULL, null=True, blank=True)
    link = models.CharField(max_length=200, null=True, blank=True, unique=True)

    random_clearing = models.BooleanField(default=False)
    notes = models.TextField(null=True, blank=True)

    # Automatic
    date_posted = models.DateTimeField(default=timezone.now)
    recorder = models.ForeignKey(Profile, on_delete=models.SET_NULL, null=True, blank=True)


    # This probably isn't necessary....
    def get_efforts(self):
        return Effort.objects.filter(game=self)
    
    def get_winners(self):
        return Effort.objects.filter(game=self, win=True)
    
    def only_official_components(self):
        return not (
            Effort.objects.filter(Q(faction__official=False) | Q(vagabond__official=False), game=self).exists() or
            (self.deck and not self.deck.official) or
            (self.map and not self.map.official) or
            self.hirelings.filter(official=False).exists() or
            self.landmarks.filter(official=False).exists()
        )

class Effort(models.Model):
    seat = models.IntegerField(validators=[MinValueValidator(1)], null=True, blank=True)
    player = models.ForeignKey(Profile, on_delete=models.SET_NULL, null=True, blank=True)
    faction = models.ForeignKey(Faction, on_delete=models.SET_NULL, null=True, blank=True, related_name='faction_used')
    vagabond = models.ForeignKey(Vagabond, on_delete=models.SET_NULL, null=True, blank=True, default=None)
    coalition_with = models.ForeignKey(Faction, on_delete=models.SET_NULL, null=True, blank=True, related_name='coalition_with')
    dominance = models.BooleanField(default=False)
    clockwork = models.BooleanField(default=False)
    win = models.BooleanField()
    score = models.IntegerField(validators=[MinValueValidator(0)])
    game = models.ForeignKey(Game, on_delete=models.CASCADE, null=True, blank=True) #This should not be null. Remove null once done testing
    faction_status = models.CharField(max_length=15, null=True, blank=True) #This should not be null.
    notes = models.TextField(null=True, blank=True)
    date_posted = models.DateTimeField(default=timezone.now)

