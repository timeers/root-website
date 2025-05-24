from django.contrib.auth.models import User
from django.db import models, transaction
from django.db.models import Q, Sum, F
from django.db.models.signals import pre_save, post_save
from django.utils import timezone 
from django.urls import reverse
from django.core.validators import MinValueValidator, MaxValueValidator
from the_gatehouse.models import Profile
from the_keep.models import Deck, Map, Faction, Landmark, Hireling, Vagabond, Tweak, StatusChoices
from django.core.exceptions import ValidationError
from django.contrib.admin.views.decorators import staff_member_required
from .utils import slugify_tournament_name, slugify_round_name

class PlatformChoices(models.TextChoices):
    TTS = 'Tabletop Simulator'
    # HRF = 'hrf.com' # :(
    DWD = 'Root Digital'
    IRL = 'In Person'
    # ETC = 'Other'

class GameQuerySet(models.QuerySet):
    def only_official_components(self):
        return self.filter(official=True)
        # return self.exclude(
        #     Q(efforts__faction__official=False) |
        #     Q(efforts__vagabond__official=False) |
        #     Q(deck__official=False) |
        #     Q(map__official=False) |
        #     Q(hirelings__official=False) |
        #     Q(landmarks__official=False)
        # )

# This is a Tournament (or Series). It can be a structured tournament or a loose grouping of playtests to show stats and leaderboards for specific games.
# Currently hidden on the site and not really being used.
class Tournament(models.Model):
    class CoalitionTypes(models.TextChoices):
        NONE = "None"
        ONE = "One"
        ALL = "All"
    type = "Tournament"
    name = models.CharField(max_length=30, unique=True)

    players = models.ManyToManyField(Profile, blank=True, related_name='current_tournaments')
    eliminated_players = models.ManyToManyField(Profile, blank=True, related_name='past_tournaments')
    factions = models.ManyToManyField(Faction, blank=True, related_name='tournaments')
    maps = models.ManyToManyField(Map, blank=True, related_name='tournaments')
    decks = models.ManyToManyField(Deck, blank=True, related_name='tournaments')
    hirelings = models.ManyToManyField(Hireling, blank=True, related_name='tournaments')
    landmarks = models.ManyToManyField(Landmark, blank=True, related_name='tournaments')
    tweaks = models.ManyToManyField(Tweak, blank=True, related_name='tournaments')
    vagabonds = models.ManyToManyField(Vagabond, blank=True, related_name='tournaments')

    max_players = models.IntegerField(default=4,validators=[MinValueValidator(2)])
    min_players = models.IntegerField(default=4,validators=[MinValueValidator(2)])

    # elimination = models.IntegerField(default=None, null=True, blank=True)
    platform = models.CharField(max_length=20, choices=PlatformChoices.choices, default=None, null=True, blank=True)
    link_required = models.BooleanField(default=False)

    game_threshold = models.IntegerField(default=0,validators=[MinValueValidator(0)])
    leaderboard_positions = models.IntegerField(default=15, validators=[MinValueValidator(3), MaxValueValidator(30)])

    include_fan_content = models.BooleanField(default=False)
    include_clockwork = models.BooleanField(default=False)
    teams = models.BooleanField(default=False)
    coalition_type = models.CharField(
        max_length=50,
        choices=CoalitionTypes.choices,
        default=CoalitionTypes.ONE
    )
    designer = models.ForeignKey(Profile, on_delete=models.SET_NULL, null=True, blank=True)
    description = models.TextField(null=True, blank=True)

    start_date = models.DateTimeField(default=timezone.now)
    end_date = models.DateTimeField(null=True, blank=True)
    
    slug = models.SlugField(unique=True, null=True, blank=True)

    open_roster = models.BooleanField(default=True)
    open_assets = models.BooleanField(default=True)

    def __str__(self):
        return self.name
    
    def get_absolute_url(self):
        return reverse('tournament-detail', kwargs={'tournament_slug': self.slug})
    
    def get_players_url(self):
        return reverse('tournament-players', kwargs={'tournament_slug': self.slug})
    
    def get_assets_url(self):
        return reverse('tournament-assets', kwargs={'tournament_slug': self.slug})
    
    def get_update_url(self):
        return reverse('tournament-update', kwargs={'slug': self.slug})
    

    def game_count(self):
        # Counts the number of games associated with this tournament
        return Game.objects.filter(round__tournament=self, final=True).count()
    
    def get_game_queryset(self):
        games = Game.objects.filter(round__tournament=self, final=True).all()
        return games


    def all_player_count(self):
        return Effort.objects.filter(game__round__tournament=self).values('player').distinct().count()
        # all_players = self.players.count() + self.eliminated_players.count()
        # return all_players


    def all_player_queryset(self):
        # Use union to combine the two querysets (must be the same model)
        players = self.players.all()
        eliminated_players = self.eliminated_players.all()

        # Combine the two querysets and return as a QuerySet
        all_players = players.union(eliminated_players)

        return all_players


# This is a round of a tournament, series or playtest. It allows for leaderboard splits and a set end date.
class Round(models.Model):
    type = "Round"
    tournament = models.ForeignKey(Tournament, on_delete=models.CASCADE, related_name='rounds')  # Link to the tournament
    round_number = models.PositiveIntegerField()  # Round number (e.g., 1, 2, 3, etc.)
    name = models.CharField(max_length=255, null=True, blank=True)  # Optional name, e.g., "Quarter-finals", "Finals"
    start_date = models.DateTimeField()
    game_threshold = models.IntegerField(default=0,validators=[MinValueValidator(0)])
    end_date = models.DateTimeField(null=True, blank=True)  # Can be null if round hasn't ended yet
    players = models.ManyToManyField(Profile, blank=True, related_name='rounds')
    description = models.TextField(null=True, blank=True)
    slug = models.SlugField(null=True, blank=True)

    def get_absolute_url(self):
        return reverse('round-detail', kwargs={'round_slug': self.slug, 'tournament_slug': self.tournament.slug})

    def get_players_url(self):
        return reverse('round-players', kwargs={'round_slug': self.slug, 'tournament_slug': self.tournament.slug})
    
    def get_assets_url(self):
        return reverse('tournament-assets', kwargs={'tournament_slug': self.tournament.slug})  

    def get_update_url(self):
        return reverse('round-update', kwargs={'round_slug': self.slug, 'tournament_slug': self.tournament.slug})
    
    def get_delete_url(self):
        return reverse('round-delete', kwargs={'round_slug': self.slug, 'tournament_slug': self.tournament.slug, 'pk': self.id})
    
    def current_player_queryset(self):
        if self.players.count() == 0:
            qs = self.tournament.players.all()
        else:
            qs = self.players.all()
        return qs

    def all_player_queryset(self):
        # Use union to combine the two querysets (must be the same model)
        players = self.current_player_queryset.all()
        eliminated_players = self.tournament.eliminated_players.all()
        # Combine the two querysets and return as a QuerySet
        all_players = players.union(eliminated_players)

        return all_players


    def __str__(self):
        return f"{self.tournament.name} - {self.name}"

    @property
    def all_player_count(self):
        return Effort.objects.filter(game__round=self).values('player').distinct().count()

        # if self.players.count() > 0:
        #     all_players = self.players.count()
        # else:
        #     all_players = self.tournament.all_player_count()
        # return all_players   
     
    def game_count(self):
        return Game.objects.filter(round=self, final=True).count()

    class Meta:
        ordering = ['-round_number']

# This is a game with the basic game attributes and a variable number of seats (Efforts) linked to it
class Game(models.Model):
    class TypeChoices(models.TextChoices):
        ASYNC = 'Async'
        LIVE = 'Live'
    component = 'Game'
    # Required
    type = models.CharField(max_length=5, choices=TypeChoices.choices, default=TypeChoices.LIVE)
    platform = models.CharField(max_length=20, choices=PlatformChoices.choices, default=PlatformChoices.TTS)
    deck = models.ForeignKey(Deck, on_delete=models.PROTECT, blank=True, null=True, related_name='games')
    map = models.ForeignKey(Map, on_delete=models.PROTECT,blank=True, null=True, related_name='games')

    round = models.ForeignKey(Round, on_delete=models.PROTECT, null=True, blank=True, related_name='games')
    
    # Optional
    landmarks = models.ManyToManyField(Landmark, blank=True, related_name='games')
    tweaks = models.ManyToManyField(Tweak, blank=True, related_name='games')
    hirelings = models.ManyToManyField(Hireling, blank=True, related_name='games')
    undrafted_faction = models.ForeignKey(Faction, on_delete=models.PROTECT, null=True, blank=True, default=None, related_name='undrafted_games')
    undrafted_vagabond = models.ForeignKey(Vagabond, on_delete=models.PROTECT, null=True, blank=True, default=None, related_name='undrafted_games')
    link = models.CharField(max_length=300, null=True, blank=True)
    nickname = models.CharField(max_length=50, null=True, blank=True)
    random_clearing = models.BooleanField(default=True)
    notes = models.TextField(null=True, blank=True)

    # Automatic
    date_posted = models.DateTimeField(default=timezone.now)
    recorder = models.ForeignKey(Profile, on_delete=models.SET_NULL, null=True, blank=True, related_name='games_recorded')

    test_match = models.BooleanField(default=False)
    coalition_win = models.BooleanField(default=False)
    coop = models.BooleanField(default=False)
    solo = models.BooleanField(default=False)
    official = models.BooleanField(default=True)
    final = models.BooleanField(default=False)
    status = models.CharField(max_length=15 , null=True, blank=True, choices=StatusChoices.choices)
    reach_value = models.IntegerField(null=True, blank=True)

    bookmarks = models.ManyToManyField(Profile, related_name='bookmarkedgames', through='GameBookmark')
    objects = GameQuerySet.as_manager()


    def __str__(self):
        return f'Game {self.id}'

    def get_efforts(self):
        return self.efforts.all()
    
    def get_winners(self):
        return self.get_efforts().filter(win=True)

    
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

# This represents one seat of a game. A faction is required but a player is not.
class Effort(models.Model):
    class DominanceChoices(models.TextChoices):
        MOUSE = 'Mouse'
        FOX = 'Fox'
        RABBIT = 'Rabbit'
        BIRD = 'Bird'
        DARK = 'Dark'
        FROG = 'Frog'
        BEAR = 'Mountain King'
    class StatusChoices(models.TextChoices):
        ACTIVE = 'Active'
        ELIMINATED = 'Eliminated'

    seat = models.IntegerField(validators=[MinValueValidator(1)], null=True, blank=True)
    player = models.ForeignKey(Profile, on_delete=models.PROTECT, null=True, blank=True, related_name='efforts')
    faction = models.ForeignKey(Faction, on_delete=models.PROTECT, related_name='efforts', blank=True, null=True)
    vagabond = models.ForeignKey(Vagabond, on_delete=models.PROTECT, null=True, blank=True, default=None, related_name='efforts')
    captains = models.ForeignKey(Vagabond, on_delete=models.PROTECT, null=True, blank=True, default=None, related_name='efforts_as_captain')
    coalition_with = models.ForeignKey(Faction, on_delete=models.PROTECT, null=True, blank=True, related_name='efforts_in_coalition')
    dominance = models.CharField(max_length=20, choices=DominanceChoices.choices, null=True, blank=True)
    win = models.BooleanField(default=False)
    score = models.IntegerField(validators=[MinValueValidator(0)], null=True, blank=True)
    game = models.ForeignKey(Game, on_delete=models.CASCADE, related_name='efforts')
    faction_status = models.CharField(max_length=50, null=True, blank=True) #This should not be null.
    # notes = models.TextField(null=True, blank=True)
    date_posted = models.DateTimeField(default=timezone.now)
    player_status = models.CharField(max_length=50, choices=StatusChoices.choices, default=StatusChoices.ACTIVE)


    def save(self, *args, **kwargs):
        self.full_clean()  # This ensures the clean() method is called before saving
        super().save(*args, **kwargs)
        update_game = False
        if self.coalition_with and self.win:
            self.game.coalition_win = True
            update_game = True
        if update_game:
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

        if not scorecards.exists() and self.dominance:
            # Query dominance scorecards if no regular scorecards were found and dominance is True
            dominance_scorecards = ScoreCard.objects.filter(
                Q(recorder=user.profile) &
                Q(effort=None) &
                Q(faction=self.faction) &
                Q(turns__dominance=True)
            )
            return dominance_scorecards.exists()

        # Return True if regular scorecards exist
        return scorecards.exists()

    
    class Meta:
        ordering = ['game', 'seat']    

# This is a collection of Turns that makes up the detailed point breakdown of a game. It should be linked to an effort and is marked as final when the total score matches with the effort's score.
class ScoreCard(models.Model):
    effort = models.OneToOneField(Effort, related_name='scorecard', on_delete=models.CASCADE, null=True, blank=True)
    faction = models.ForeignKey(Faction, related_name='scorecards', on_delete=models.CASCADE)
    recorder = models.ForeignKey(Profile, on_delete=models.SET_NULL, null=True, blank=True)
    description = models.TextField(blank=True)
    date_posted = models.DateTimeField(default=timezone.now)
    game_group = models.CharField(max_length=100, null=True, blank=True)

    total_points = models.IntegerField(default=0)
    total_battle_points = models.IntegerField(default=0)
    total_crafting_points = models.IntegerField(default=0)
    total_faction_points = models.IntegerField(default=0)
    total_other_points = models.IntegerField(default=0)
    total_generic_points = models.IntegerField(default=0)
    dominance = models.BooleanField(default=False)
    final = models.BooleanField(default=False)


    def efforts_available(self):
        if self.effort:
            return False
        print(self)
        if self.dominance:
            print('dominance')
            # If self.dominance is True, filter for efforts where dominance is not null
            available_efforts = Effort.objects.filter(
                Q(game__efforts__player=self.recorder) |  # Player is linked to the game
                Q(game__recorder=self.recorder),  # Recorder is the current user
                scorecard=None, faction=self.faction,  # Effort has no associated scorecard
                dominance__isnull=False
            ).exists()
            print(f'Faction: {self.faction} - Dominance')
        else:
            # If self.dominance is False, filter for efforts with no dominance and score matching
            available_efforts = Effort.objects.filter(
                Q(game__efforts__player=self.recorder) |  # Player is linked to the game
                Q(game__recorder=self.recorder),  # Recorder is the current user
                scorecard=None, faction=self.faction,
                score=self.total_points,  # Effort has no associated scorecard
                game__final=True
            ).exists()
            available_test_efforts = Effort.objects.filter(
                Q(game__efforts__player=self.recorder) |  # Player is linked to the game
                Q(game__recorder=self.recorder),  # Recorder is the current user
                scorecard=None, faction=self.faction,
                score=self.total_points,  # Effort has no associated scorecard
                game__final=True
            )
            print(available_test_efforts)
            for effort in available_test_efforts:
                print(effort.game.get_absolute_url())
            print(f'Faction: {self.faction} - No Dominance')



        print(f'Faction: {self.faction} - Match: {available_efforts}')
        
        return available_efforts

    
    def __str__(self):
        return f"{self.faction} - {self.total_points} points"
    
    def get_absolute_url(self):
        return reverse("detail-scorecard", kwargs={"id": self.id})
    class Meta:
        ordering = ['-date_posted']

# This is the poorly named segment of a Scorecard that contains the point breakdown for each Turn
class TurnScore(models.Model):
    scorecard = models.ForeignKey(ScoreCard, related_name='turns', on_delete=models.CASCADE, null=True, blank=True)
    turn_number = models.IntegerField(default=0, validators=[MinValueValidator(1)])
    battle_points = models.IntegerField(default=0)
    crafting_points = models.IntegerField(default=0)
    faction_points = models.IntegerField(default=0)
    other_points = models.IntegerField(default=0)
    generic_points = models.IntegerField(default=0)
    total_points = models.IntegerField(default=0)
    dominance = models.BooleanField(default=False)
    # game_points = models.IntegerField(default=0)


    def __str__(self):
            return f"Turn {self.turn_number} - Total Points: {self.total_points}"
    class Meta:
        unique_together = ('scorecard', 'turn_number')  # Ensure each game has only one entry per turn_number
        ordering = ['scorecard', 'turn_number']   

    # Calculate the cumulative game points up to the current turn
    def game_points(self):
        # Filter turns in the same scorecard with turn_number <= current turn's turn_number
        total_turns = self.scorecard.turns.filter(turn_number__lte=self.turn_number)
        
        # Sum up the total_points for those turns
        total_game_points = total_turns.aggregate(Sum('total_points'))['total_points__sum'] or 0
        
        return total_game_points

  


def tournament_pre_save(sender, instance, *args, **kwargs):
    if instance.slug is None:
        slugify_tournament_name(instance, save=False)

def round_pre_save(sender, instance, *args, **kwargs):
    if instance.slug is None:
        slugify_round_name(instance, save=False)


pre_save.connect(tournament_pre_save, sender=Tournament)
pre_save.connect(round_pre_save, sender=Round)


def tournament_post_save(sender, instance, created, *args, **kwargs):
    if created:
        slugify_tournament_name(instance, save=True)

def round_post_save(sender, instance, created, *args, **kwargs):
    if created:
        slugify_round_name(instance, save=True)

post_save.connect(tournament_post_save, sender=Tournament)
post_save.connect(round_post_save, sender=Round)