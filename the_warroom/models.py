from django.contrib.auth.models import User
from django.db import models, transaction
from django.db.models import Q, Sum, F
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
    test_match = models.BooleanField(default=False)
    coalition_win = models.BooleanField(default=False)
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


    def save(self, *args, **kwargs):
        self.full_clean()  # This ensures the clean() method is called before saving
        super().save(*args, **kwargs)
        if self.coalition_with and self.win:
            self.game.coalition_win = True
            self.game.save()

    def get_absolute_url(self):
        return self.game.get_absolute_url()

    def get_delete_url(self):
        kwargs = {
             "parent_id": self.game.id,
             "id": self.id,
        }
        return reverse("effort-delete", kwargs=kwargs)

    def available_scorecard(self, user):
        # Use Q to filter for scorecards made by the user, with no linked effort, matching faction and score
        scorecards = ScoreCard.objects.filter(
            Q(recorder=user.profile) &
            Q(effort=None) &
            Q(faction=self.faction) &
            Q(total_points=self.score)
        )
        # If there is a queryset return True
        return scorecards.exists()

    
    class Meta:
        ordering = ['game', 'seat']    


class ScoreCard(models.Model):
    effort = models.OneToOneField(Effort, related_name='scorecard', on_delete=models.CASCADE, null=True, blank=True)
    faction = models.ForeignKey(Faction, related_name='scorecards', on_delete=models.CASCADE)
    recorder = models.ForeignKey(Profile, on_delete=models.SET_NULL, null=True, blank=True)
    description = models.TextField(blank=True)
    date_posted = models.DateTimeField(default=timezone.now)

    total_points = models.IntegerField(default=0)
    total_battle_points = models.IntegerField(default=0)
    total_crafting_points = models.IntegerField(default=0)
    total_faction_points = models.IntegerField(default=0)
    total_other_points = models.IntegerField(default=0)


    def __str__(self):
        if self.description:
            if len(self.description) > 10:
                description = f'{self.description[:10]}...'
            else:
                description = self.description
        else:
            description = 'No Note'
        return f"{self.turns.count()} Turn - {self.total_points} Point Game - {self.date_posted.strftime('%Y-%m-%d')} - {description}"
    def get_absolute_url(self):
        return reverse("detail-scorecard", kwargs={"id": self.id})

class TurnScore(models.Model):
    scorecard = models.ForeignKey(ScoreCard, related_name='turns', on_delete=models.CASCADE, null=True, blank=True)
    turn_number = models.IntegerField(default=0, validators=[MinValueValidator(1)])
    battle_points = models.IntegerField(default=0)
    crafting_points = models.IntegerField(default=0)
    faction_points = models.IntegerField(default=0)
    other_points = models.IntegerField(default=0)
    total_points = models.IntegerField(default=0)
    dominance = models.BooleanField(default=False)

    def __str__(self):
            return f"Turn {self.turn_number} - Total Points: {self.total_points}"
    class Meta:
        unique_together = ('scorecard', 'turn_number')  # Ensure each game has only one entry per turn_number
        ordering = ['scorecard', 'turn_number']   


    def save(self, *args, **kwargs):
        with transaction.atomic():
            self.total_points = (self.battle_points + self.crafting_points + self.faction_points + self.other_points)
            super().save(*args, **kwargs)
            if self.scorecard:
                scorecard = self.scorecard
                scorecard.total_points = scorecard.turns.aggregate(Sum('total_points'))['total_points__sum'] or 0
                scorecard.total_battle_points = scorecard.turns.aggregate(Sum('battle_points'))['battle_points__sum'] or 0
                scorecard.total_crafting_points = scorecard.turns.aggregate(Sum('crafting_points'))['crafting_points__sum'] or 0
                scorecard.total_faction_points = scorecard.turns.aggregate(Sum('faction_points'))['faction_points__sum'] or 0
                scorecard.total_other_points = scorecard.turns.aggregate(Sum('other_points'))['other_points__sum'] or 0
                scorecard.save()