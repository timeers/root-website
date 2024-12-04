from django.contrib.auth.models import User
from django.db import models
from django.db.models import Q
from django.utils import timezone 
from django.urls import reverse
from django.core.validators import MinValueValidator
from the_gatehouse.models import Profile
from the_keep.models import Deck, Map, Faction, Landmark, Hireling, Vagabond
from django.core.exceptions import ValidationError
from django.contrib.admin.views.decorators import staff_member_required


class GameQuerySet(models.QuerySet):
    def only_official_components(self):
        return self.exclude(
            Q(efforts__faction__official=False) |
            Q(efforts__vagabond__official=False) |
            Q(deck__official=False) |
            Q(map__official=False) |
            Q(hirelings__official=False) |
            Q(landmarks__official=False)
        )


class Tournament(models.Model):
    name = models.CharField(max_length=30)
    participants = models.ManyToManyField(Profile, blank=True)
    description = models.TextField(null=True, blank=True)
    active = models.BooleanField(default=True)

class Game(models.Model):
    class TypeChoices(models.TextChoices):
        ASYNC = 'Async'
        LIVE = 'Live'
    class PlatformChoices(models.TextChoices):
        TTS = 'Tabletop Simulator'
        # HRF = 'hrf.com'
        DWD = 'Root Digital'
        IRL = 'In Person'
        # ETC = 'Other'
    # Required
    type = models.CharField(max_length=5, choices=TypeChoices.choices, default=TypeChoices.LIVE)
    platform = models.CharField(max_length=20, choices=PlatformChoices.choices, default=PlatformChoices.TTS)
    deck = models.ForeignKey(Deck, on_delete=models.PROTECT, null=True, related_name='games')
    map = models.ForeignKey(Map, on_delete=models.PROTECT, null=True, related_name='games')

    league = models.BooleanField(default=False)
    tournament = models.ForeignKey(Tournament, on_delete=models.SET_NULL, null=True, blank=True)
    
    # Optional
    landmarks = models.ManyToManyField(Landmark, blank=True, related_name='games')
    hirelings = models.ManyToManyField(Hireling, blank=True, related_name='games')
    undrafted_faction = models.ForeignKey(Faction, on_delete=models.PROTECT, null=True, blank=True, default=None, related_name='undrafted_games')
    undrafted_vagabond = models.ForeignKey(Vagabond, on_delete=models.PROTECT, null=True, blank=True, default=None, related_name='undrafted_games')
    link = models.CharField(max_length=300, null=True, blank=True)

    random_clearing = models.BooleanField(default=False)
    notes = models.TextField(null=True, blank=True)

    # Automatic
    date_posted = models.DateTimeField(default=timezone.now)
    recorder = models.ForeignKey(Profile, on_delete=models.SET_NULL, null=True, blank=True, related_name='games_recorded')

    bookmarks = models.ManyToManyField(Profile, related_name='bookmarkedgames', through='GameBookmark')
    objects = GameQuerySet.as_manager()

    def __str__(self):
        return f'Game {self.id}'

    def get_efforts(self):
        return self.efforts.all()
    
    def get_winners(self):
        return self.get_efforts().filter(win=True)
    
    @property
    def only_official_components(self):
        return not (
            self.get_efforts().filter(Q(faction__official=False) | Q(vagabond__official=False)).exists() or
            (self.deck and not self.deck.official) or
            (self.map and not self.map.official) or
            self.hirelings.filter(official=False).exists() or
            self.landmarks.filter(official=False).exists()
        )
    
    def clean(self):
        # Check for duplicates among non-blank links
        if self.link:
            if Game.objects.exclude(id=self.id).filter(link=self.link).exists():
                raise ValidationError(f'The link "{self.link}" must be unique.')
    
    def get_absolute_url(self):
        return reverse("game-detail", kwargs={"id": self.id})
    
    def get_hx_url(self):
        return reverse("game-hx-detail", kwargs={"id": self.id})
    
    def get_edit_url(self):
        return reverse("game-update", kwargs={"id": self.id})
    
    def get_delete_url(self):
        return reverse("game-delete", kwargs={"id": self.id})
    
    class Meta:
        ordering = ['-date_posted']

class GameBookmark(models.Model):
    player = models.ForeignKey(Profile, on_delete=models.CASCADE)
    game = models.ForeignKey(Game, on_delete=models.CASCADE)
    public = models.BooleanField(default=False)
    date_posted = models.DateTimeField(default=timezone.now)
    def __str__(self):
        return f"{self.player.name}: {self.game.id}"


class Effort(models.Model):
    class DominanceChoices(models.TextChoices):
        MOUSE = 'Mouse'
        FOX = 'Fox'
        RABBIT = 'Rabbit'
        BIRD = 'Bird'
        DARK = 'Dark'
    seat = models.IntegerField(validators=[MinValueValidator(1)], null=True, blank=True)
    player = models.ForeignKey(Profile, on_delete=models.PROTECT, null=True, blank=True, related_name='efforts')
    faction = models.ForeignKey(Faction, on_delete=models.PROTECT, related_name='efforts')
    vagabond = models.ForeignKey(Vagabond, on_delete=models.PROTECT, null=True, blank=True, default=None, related_name='efforts')
    captains = models.ManyToManyField(Vagabond, blank=True, related_name='efforts_as_captain')
    coalition_with = models.ForeignKey(Faction, on_delete=models.PROTECT, null=True, blank=True, related_name='efforts_in_coalition')
    dominance = models.CharField(max_length=10, choices=DominanceChoices.choices, null=True, blank=True)
    clockwork = models.BooleanField(default=False)
    win = models.BooleanField(default=False)
    score = models.IntegerField(validators=[MinValueValidator(0)], null=True, blank=True)
    game = models.ForeignKey(Game, on_delete=models.CASCADE, related_name='efforts')
    faction_status = models.CharField(max_length=50, null=True, blank=True) #This should not be null.
    # notes = models.TextField(null=True, blank=True)
    date_posted = models.DateTimeField(default=timezone.now)

    def clean(self):
        super().clean()
        
        # Ensure no more than 3 captains are assigned
        # if self.captains.count() > 3:
        #     raise ValidationError({'captains': 'You cannot assign more than 3 Vagabonds as captains.'})

    def save(self, *args, **kwargs):
        self.full_clean()  # This ensures the clean() method is called before saving
        super().save(*args, **kwargs)

    def get_absolute_url(self):
        return self.game.get_absolute_url()

    def get_delete_url(self):
        kwargs = {
             "parent_id": self.game.id,
             "id": self.id,
        }
        return reverse("effort-delete", kwargs=kwargs)