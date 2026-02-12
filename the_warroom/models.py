from django.db import models
from django.db.models import Q, Sum

from django.utils import timezone 
from django.urls import reverse
from django.core.validators import MinValueValidator, MaxValueValidator
from django.core.exceptions import ValidationError

from the_gatehouse.models import Profile, DiscordGuild
from the_gatehouse.utils import NameConvention
from the_keep.models import Deck, Map, Faction, Landmark, Hireling, Vagabond, Tweak, StatusChoices
from the_keep.utils import delete_old_image

class PlatformChoices(models.TextChoices):
    TTS = 'Tabletop Simulator'
    # HRF = 'hrf.com' # :(
    DWD = 'Root Digital'
    IRL = 'In Person'
    # ETC = 'Other'

class AssetModeChoices(models.IntegerChoices):
    OPEN = 1, 'Open Assets'           # Any asset can be used
    OFFICIAL = 2, 'Official Only'      # All official content included automatically
    SELECTED = 3, 'Selected Only'      # Only specifically selected assets


class FormatChoices(models.TextChoices):
    SINGLE_ELIM = 'Single Elimination', 'Single Elimination'
    DOUBLE_ELIM = 'Double Elimination', 'Double Elimination'
    SWISS = 'Swiss', 'Swiss'
    ROUND_ROBIN = 'Round Robin', 'Round Robin'
    POOL_PLAY = 'Pool Play', 'Pool Play'
    CUSTOM = 'Custom', 'Custom'

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


class TournamentQuerySet(models.QuerySet):
    def open(self):
        """Return tournaments that are still open (end_date is null or in the future)"""
        return self.filter(
            Q(end_date__isnull=True) | Q(end_date__gt=timezone.now())
        )


class RoundQuerySet(models.QuerySet):
    def open(self):
        """Return rounds that are still open (end_date is null or in the future)"""
        return self.filter(
            Q(end_date__isnull=True) | Q(end_date__gt=timezone.now())
        )


# This is a Tournament (or Series). It can be a structured tournament or a loose grouping of playtests to show stats and leaderboards for specific games.
# Currently hidden on the site and not really being used.
class Tournament(models.Model):
    class CoalitionTypes(models.TextChoices):
        NONE = "None"
        ONE = "One"
        ALL = "All"
    class ClassificationTypes(models.TextChoices):
        LEAGUE = "League"
        TOURNAMENT = "Tournament"
        GROUP = "Game Group"
    type = "Tournament"
    name = models.CharField(max_length=30, unique=True)
    designer = models.ForeignKey(
        Profile, 
        on_delete=models.SET_NULL, 
        null=True, blank=True, 
        related_name='hosted_tournaments',
        help_text='Owner can update the Series, add new rounds, manage player and assets.'
        )
    description = models.TextField(null=True, blank=True)
    picture = models.ImageField(upload_to='tournaments', null=True, blank=True)

    classification = models.CharField(
        max_length=50,
        choices=ClassificationTypes.choices,
        default=ClassificationTypes.TOURNAMENT,
        help_text='Group: casual game group. League: semi-competitive. Tournament: structured and competitive.'
    )

    default_format = models.CharField(
        max_length=50,
        choices=FormatChoices.choices,
        blank=True,
        default=''
    )

    # Access & Roster
    guild = models.ForeignKey(DiscordGuild, on_delete=models.SET_NULL, null=True, blank=True, related_name='tournaments', help_text='Link this Series with a Guild to allow members to record games')
    open_roster = models.BooleanField(default=True, help_text='Allow any player to be added to games in this Series')
    # Player management now handled via GroupingSession/SessionPlayer pattern
    # Use get_players_queryset(), get_waitlist_players_queryset(), get_eliminated_players_queryset()
    publicly_visible = models.BooleanField(default=False)

    # Asset Settings
    asset_mode = models.IntegerField(
        choices=AssetModeChoices.choices,
        default=AssetModeChoices.OPEN,
        help_text='Open: any asset. Official: all official assets. Selected: only chosen assets.'
    )
    include_clockwork = models.BooleanField(default=False)
    
    factions = models.ManyToManyField(Faction, blank=True, related_name='tournaments')
    maps = models.ManyToManyField(Map, blank=True, related_name='tournaments')
    decks = models.ManyToManyField(Deck, blank=True, related_name='tournaments')
    hirelings = models.ManyToManyField(Hireling, blank=True, related_name='tournaments')
    landmarks = models.ManyToManyField(Landmark, blank=True, related_name='tournaments')
    tweaks = models.ManyToManyField(Tweak, blank=True, related_name='tournaments')
    vagabonds = models.ManyToManyField(Vagabond, blank=True, related_name='tournaments')

    enforce_player_count = models.BooleanField(default=False)
    max_players = models.IntegerField(default=4,validators=[MinValueValidator(2)])
    min_players = models.IntegerField(default=4,validators=[MinValueValidator(2)])

    # Submission Settings
    platform = models.CharField(max_length=20, choices=PlatformChoices.choices, default=None, null=True, blank=True)
    link_required = models.BooleanField(default=False)

    # Leaderboard Settings
    game_threshold = models.IntegerField(default=0,validators=[MinValueValidator(0)])
    leaderboard_positions = models.IntegerField(default=15, validators=[MinValueValidator(3), MaxValueValidator(30)])

    teams = models.BooleanField(default=False)
    coalition_type = models.CharField(
        max_length=50,
        choices=CoalitionTypes.choices,
        default=CoalitionTypes.ONE
    )

    start_date = models.DateTimeField(default=timezone.now)
    end_date = models.DateTimeField(null=True, blank=True)

    slug = models.SlugField(unique=True, null=True, blank=True)
    objects = TournamentQuerySet.as_manager()

    @property
    def is_open(self):
        """Check if tournament is currently open (end_date is null or in the future)"""
        if self.end_date is None:
            return True
        return timezone.now() < self.end_date

    def __str__(self):
        return self.name
    
    def get_absolute_url(self):
        return reverse('tournament-detail', kwargs={'tournament_slug': self.slug})

    def get_settings_url(self):
        return reverse('tournament-settings', kwargs={'slug': self.slug})

    def get_edit_url(self):
        return reverse('tournament-dynamic-update', kwargs={'slug': self.slug})

    def get_players_url(self):
        return reverse('tournament-manage-players', kwargs={'slug': self.slug})

    def get_assets_url(self):
        return reverse('tournament-manage-assets', kwargs={'slug': self.slug})

    def get_update_url(self):
        return reverse('tournament-dynamic-update', kwargs={'slug': self.slug})


    def game_count(self):
        # Counts the number of games associated with this tournament
        return Game.objects.filter(round__tournament=self, final=True).count()
    
    def get_game_queryset(self):
        games = Game.objects.filter(round__tournament=self, final=True).all()
        return games


    def all_player_count(self):
        return Effort.objects.filter(game__round__tournament=self).values('player').distinct().count()


    def all_player_queryset(self):
        """All players in the tournament (active, waitlist, and eliminated)."""
        from the_gatehouse.models import Profile
        return Profile.objects.filter(
            session_participations__session__tournament=self
        ).distinct()

    @property
    def master_session(self):
        """Get or create the tournament-level grouping session that owns all SessionPlayers."""
        if not hasattr(self, '_master_session_cache'):
            session = self.grouping_sessions.order_by('created_at').first()

            if not session:
                from the_warroom.models import GroupingSession
                session = GroupingSession.objects.create(
                    tournament=self,
                    name=f"Session - {self.name}",
                    grouping_type='manual',
                    status='draft',
                )

            self._master_session_cache = session

        return self._master_session_cache

    def get_players_queryset(self):
        """Active players in the tournament (ACTIVE status)."""
        from the_gatehouse.models import Profile
        from the_warroom.models import SessionPlayer
        return Profile.objects.filter(
            session_participations__session__tournament=self,
            session_participations__status=SessionPlayer.StatusChoices.ACTIVE
        ).distinct()

    def get_waitlist_players_queryset(self):
        """Waitlisted players in the tournament."""
        from the_gatehouse.models import Profile
        from the_warroom.models import SessionPlayer
        return Profile.objects.filter(
            session_participations__session__tournament=self,
            session_participations__status=SessionPlayer.StatusChoices.WAITLIST
        ).distinct()

    def get_eliminated_players_queryset(self):
        """Eliminated players in the tournament."""
        from the_gatehouse.models import Profile
        from the_warroom.models import SessionPlayer
        return Profile.objects.filter(
            session_participations__session__tournament=self,
            session_participations__status=SessionPlayer.StatusChoices.ELIMINATED
        ).distinct()

    def move_player(self, profile, from_status, to_status):
        """Update a player's status in the tournament session."""
        from the_warroom.models import SessionPlayer
        sp = SessionPlayer.objects.filter(
            session__tournament=self,
            profile=profile
        ).first()

        if sp is None:
            if from_status is not None:
                raise ValueError(f"Player {profile} not found in tournament")
            sp = SessionPlayer.objects.create(
                session=self.master_session,
                profile=profile,
                status=to_status,
            )
        else:
            sp.status = to_status
            sp.save(update_fields=['status'])

    def add_player_to_tournament(self, profile, status=None):
        """Add a player to the tournament session."""
        from the_warroom.models import SessionPlayer
        if status is None:
            status = SessionPlayer.StatusChoices.ACTIVE
        SessionPlayer.objects.update_or_create(
            session=self.master_session,
            profile=profile,
            defaults={'status': status}
        )

    def remove_player_from_tournament(self, profile):
        """Remove a player completely from the tournament."""
        from the_warroom.models import SessionPlayer
        SessionPlayer.objects.filter(
            session__tournament=self,
            profile=profile
        ).delete()

    def save(self, *args, **kwargs):

        # Check if the image field has changed (only works if the instance is already saved)
        if self.pk:  # If the object already exists in the database
            old_instance = Tournament.objects.get(pk=self.pk)
            # List of fields to check and delete old images if necessary
            field_name = 'picture'

            old_image = getattr(old_instance, field_name)
            new_image = getattr(self, field_name)
            if old_image != new_image:
                delete_old_image(old_image)
        
        super().save(*args, **kwargs)

# This is a round of a tournament, series or playtest. It allows for leaderboard splits and a set end date.
class Round(models.Model):
    type = "Round"
    name = models.CharField(max_length=255, null=True, blank=True)  # Optional name, e.g., "Quarter-finals", "Finals"
    description = models.TextField(null=True, blank=True)
    tournament = models.ForeignKey(Tournament, on_delete=models.CASCADE, related_name='rounds')  # Link to the tournament
    
    round_specific_format = models.CharField(
        max_length=50, 
        choices=FormatChoices.choices, 
        blank=True,
        null=True,
        help_text="Leave blank to use tournament's default format"
    )

    round_number = models.PositiveIntegerField()  # Round number (e.g., 1, 2, 3, etc.)
    start_date = models.DateTimeField()
    end_date = models.DateTimeField(null=True, blank=True)  # Can be null if round hasn't ended yet

    game_threshold = models.IntegerField(
        null=True, blank=True, validators=[MinValueValidator(0)],
        help_text="Leave blank to inherit from series"
        )
    leaderboard_positions = models.IntegerField(
        null=True, blank=True, validators=[MinValueValidator(3), MaxValueValidator(30)],
        help_text="Leave blank to inherit from series"
        )

    max_players = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
    )
    min_players = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
    )

    # Round roster — SessionPlayers from the tournament's GroupingSession
    roster = models.ManyToManyField(
        'SessionPlayer',
        blank=True,
        related_name='rounds',
        help_text="Players participating in this round (from the tournament's GroupingSession)"
    )

    slug = models.SlugField(null=True, blank=True)

    # Grouping lifecycle — scoped to this round, independent of other rounds using the same session
    class GroupingStatusChoices(models.TextChoices):
        PROCESSING = 'processing', 'Processing'
        DRAFT = 'draft', 'Draft'
        FINALIZED = 'finalized', 'Finalized'
        ERROR = 'error', 'Error'

    grouping_status = models.CharField(
        max_length=20,
        choices=GroupingStatusChoices.choices,
        default=GroupingStatusChoices.DRAFT,
        help_text="Status of the grouping process for this round"
    )
    grouping_notes = models.TextField(
        blank=True,
        help_text="Notes or error messages from the grouping process"
    )

    objects = RoundQuerySet.as_manager()

    @property
    def is_open(self):
        """Check if round is currently open (end_date is null or in the future)"""
        if self.end_date is None:
            return True
        return timezone.now() < self.end_date

    def get_absolute_url(self):
        return reverse('round-detail', kwargs={'round_slug': self.slug, 'tournament_slug': self.tournament.slug})

    def get_settings_url(self):
        return reverse('round-settings', kwargs={'round_slug': self.slug, 'tournament_slug': self.tournament.slug})

    def get_edit_url(self):
        return reverse('round-update', kwargs={'round_slug': self.slug, 'tournament_slug': self.tournament.slug})

    def get_players_url(self):
        return reverse('round-manage-players', kwargs={'round_slug': self.slug, 'tournament_slug': self.tournament.slug})

    def get_assets_url(self):
        return reverse('tournament-manage-assets', kwargs={'tournament_slug': self.tournament.slug})

    def get_update_url(self):
        return reverse('round-update', kwargs={'round_slug': self.slug, 'tournament_slug': self.tournament.slug})

    def get_delete_url(self):
        return reverse('round-delete', kwargs={'round_slug': self.slug, 'tournament_slug': self.tournament.slug, 'pk': self.id})

    def get_format(self):
            """Get the effective format for this round"""
            return self.round_specific_format or self.tournament.default_format

    def get_min_players(self):
            """Get the minimum players per game for this round"""
            return self.min_players or self.tournament.min_players

    def get_max_players(self):
            """Get the maximum players per game for this round"""
            return self.max_players or self.tournament.max_players

    def current_player_queryset(self):
        """Return active players in this round's roster."""
        return self.roster.filter(status=SessionPlayer.StatusChoices.ACTIVE)

    def all_player_queryset(self):
        """All players in the round (any status)."""
        return self.roster.all()

    def get_players_queryset(self):
        """Active players in this round."""
        return self.roster.filter(status=SessionPlayer.StatusChoices.ACTIVE)

    def get_active_players_queryset(self):
        """Active players in this round who are not yet assigned to a group."""
        return self.roster.filter(
            status=SessionPlayer.StatusChoices.ACTIVE
        ).exclude(
            player_groups__round=self
        )

    def get_waitlist_players_queryset(self):
        """Waitlisted players for this round."""
        return self.roster.filter(status=SessionPlayer.StatusChoices.WAITLIST)

    def get_eliminated_players_queryset(self):
        """Eliminated players for this round."""
        return self.roster.filter(status=SessionPlayer.StatusChoices.ELIMINATED)

    def add_player_to_round(self, profile):
        """Add a player to this round's roster, creating their tournament SessionPlayer if needed."""
        sp = SessionPlayer.objects.filter(
            session__tournament=self.tournament,
            profile=profile
        ).first()
        if not sp:
            sp = SessionPlayer.objects.create(
                session=self.tournament.master_session,
                profile=profile,
                status=SessionPlayer.StatusChoices.ACTIVE,
            )
        self.roster.add(sp)

    def remove_player_from_round(self, profile):
        """Remove a player from this round's roster (does not delete their SessionPlayer)."""
        sp = SessionPlayer.objects.filter(
            session__tournament=self.tournament,
            profile=profile
        ).first()
        if sp:
            self.roster.remove(sp)

    def move_player(self, profile, from_status, to_status):
        """Update a player's status on their tournament-level SessionPlayer."""
        sp = SessionPlayer.objects.filter(
            session__tournament=self.tournament,
            profile=profile
        ).first()
        if sp:
            if to_status is None:
                sp.delete()
            else:
                sp.status = to_status
                sp.save(update_fields=['status'])

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


class Match(models.Model):
    """A single match slot in a tournament bracket. Can be created as an empty shell and
    have a PlayerGroup and Game assigned later."""
    round = models.ForeignKey(Round, on_delete=models.CASCADE, related_name='matches')
    name = models.CharField(max_length=100, null=True, blank=True)
    match_number = models.PositiveSmallIntegerField(null=True, blank=True)
    player_group = models.OneToOneField(
        'PlayerGroup', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='match',
        help_text="Group assigned to this match slot"
    )
    game = models.OneToOneField(
        'Game', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='match',
        help_text="Game played for this match"
    )

    class Meta:
        ordering = ['round', 'match_number']
        unique_together = [('round', 'match_number')]

    def save(self, *args, **kwargs):
        if self.match_number is None:
            last = Match.objects.filter(round=self.round).order_by('match_number').last()
            self.match_number = (last.match_number + 1) if last and last.match_number is not None else 1
        super().save(*args, **kwargs)

    def clean(self):
        # Ensure game belongs to the same round as the match
        if self.game_id and self.game.round_id != self.round_id:
            raise ValidationError("The assigned game must belong to the same round as the match.")

    @property
    def winners(self):
        """Players who won the game linked to this match."""
        if not self.game_id:
            return Profile.objects.none()
        return Profile.objects.filter(efforts__game_id=self.game_id, efforts__win=True)

    def __str__(self):
        return self.name or f"Match {self.match_number} ({self.round})"


class MatchAdvancement(models.Model):
    """Defines how players advance from one match to the next match or into a new round."""
    class PositionChoices(models.TextChoices):
        WINNER = 'winner', 'Winner'
        LOSER = 'loser', 'Loser'

    from_match = models.ForeignKey(Match, on_delete=models.CASCADE, related_name='advancements')
    position = models.CharField(max_length=10, choices=PositionChoices.choices)

    # Exactly one of these must be set (XOR — enforced in clean())
    to_match = models.ForeignKey(
        Match, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='incoming_advancements'
    )
    to_round = models.ForeignKey(
        Round, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='incoming_advancements'
    )

    class Meta:
        unique_together = [('from_match', 'position')]

    def clean(self):
        if self.to_match_id and self.to_round_id:
            raise ValidationError("Only one of to_match or to_round can be set, not both.")
        if not self.to_match_id and not self.to_round_id:
            raise ValidationError("One of to_match or to_round must be set.")

    def __str__(self):
        if self.to_match_id:
            dest = f"Match {self.to_match}"
        elif self.to_round_id:
            dest = f"Round {self.to_round}"
        else:
            dest = "No destination"
        return f"{self.from_match} {self.position} → {dest}"


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

    round = models.ForeignKey(Round, on_delete=models.SET_NULL, null=True, blank=True, related_name='games')
    
    # Optional
    landmarks = models.ManyToManyField(Landmark, blank=True, related_name='games')
    tweaks = models.ManyToManyField(Tweak, blank=True, related_name='games')
    hirelings = models.ManyToManyField(Hireling, blank=True, related_name='games')
    undrafted_faction = models.ForeignKey(Faction, on_delete=models.PROTECT, null=True, blank=True, default=None, related_name='undrafted_games')
    undrafted_vagabond = models.ForeignKey(Vagabond, on_delete=models.PROTECT, null=True, blank=True, default=None, related_name='undrafted_games')
    link = models.URLField(max_length=1000, null=True, blank=True)
    nickname = models.CharField(max_length=50, null=True, blank=True)
    random_clearing = models.BooleanField(default=True)
    notes = models.TextField(null=True, blank=True)
    league_id = models.CharField(max_length=300, null=True, blank=True)


    # Automatic
    date_posted = models.DateTimeField(default=timezone.now)
    date_modified = models.DateTimeField(auto_now=True)
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
        if self.league_id:
            if Game.objects.exclude(id=self.id).filter(league_id=self.league_id).exists():
                raise ValidationError(f'The league id "{self.league_id}" must be unique.')
    
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
    date_modified = models.DateTimeField(auto_now=True)
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
    effort = models.OneToOneField(Effort, related_name='scorecard', on_delete=models.SET_NULL, null=True, blank=True)
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
        if self.dominance:
            # If self.dominance is True, filter for efforts where dominance is not null
            available_efforts = Effort.objects.filter(
                Q(game__efforts__player=self.recorder) |  # Player is linked to the game
                Q(game__recorder=self.recorder),  # Recorder is the current user
                scorecard=None, faction=self.faction,  # Effort has no associated scorecard
                dominance__isnull=False
            ).exists()
            # print(f'Faction: {self.faction} - Dominance')
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
           
        return available_efforts

    
    def __str__(self):
        return f"{self.faction} - {self.total_points} points"
    
    def get_absolute_url(self):
        return reverse("detail-scorecard", kwargs={"id": self.id})
    class Meta:
        ordering = ['-date_posted']

    def save(self, *args, recalculate_game_points=False, **kwargs):
        super().save(*args, **kwargs)
        
        # Recalculate if explicitly requested
        if recalculate_game_points:
            self.recalculate_all_game_points()
    
    def recalculate_all_game_points(self):
        """Recalculate game_points for all turns"""
        turns = self.turns.all().order_by('turn_number')
        running_total = 0
        updates = []
        
        for turn in turns:
            running_total += turn.total_points
            if turn.game_points_total != running_total:
                turn.game_points_total = running_total
                updates.append(turn)
        
        if updates:
            TurnScore.objects.bulk_update(updates, ['game_points_total'])


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
    game_points_total = models.IntegerField(default=0)


    def __str__(self):
            return f"Turn {self.turn_number} - Total Points: {self.total_points}"
    class Meta:
        unique_together = ('scorecard', 'turn_number')  # Ensure each game has only one entry per turn_number
        ordering = ['scorecard', 'turn_number']


# ============================================================================
# Player Grouping Models
# ============================================================================

class GroupingSession(models.Model):
    """
    Represents a single grouping operation. Can be created from a survey
    (for availability-based grouping) or directly from tournament/round players.
    """
    class GroupingTypeChoices(models.TextChoices):
        AVAILABILITY = 'availability', 'Availability-based'
        MANUAL = 'manual', 'Manual'
        RANDOM = 'random', 'Random'

    class AlgorithmChoices(models.TextChoices):
        GREEDY = 'greedy', 'Optimized'
        RANDOM = 'random', 'Random Assignment'

    # Required tournament owner (non-null after data migration)
    tournament = models.ForeignKey(
        Tournament,
        on_delete=models.CASCADE,
        null=True,
        related_name='grouping_sessions'
    )
    # Optional survey reference (availability data source)
    survey = models.ForeignKey(
        'the_tavern.Survey',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='grouping_sessions',
        help_text="Survey that seeded this session's availability data (optional)"
    )

    # Identification
    name = models.CharField(max_length=100, blank=True, help_text="Session name for identification")
    grouping_type = models.CharField(
        max_length=20,
        choices=GroupingTypeChoices.choices,
        default=GroupingTypeChoices.AVAILABILITY
    )
    naming_convention = models.CharField(
        max_length=100, 
        help_text="Convention to user for group names",
        choices=NameConvention.choices,
        default=NameConvention.NUMERIC
        )
    algorithm = models.CharField(
        max_length=20,
        choices=AlgorithmChoices.choices,
        default=AlgorithmChoices.GREEDY,
        help_text="Algorithm to use for availability-based grouping"
    )

    # Configuration (for availability-based grouping)
    min_consecutive_hours = models.PositiveSmallIntegerField(
        default=4,
        validators=[MinValueValidator(1), MaxValueValidator(24)],
        help_text="Minimum consecutive overlapping hours required"
    )
    min_days_with_overlap = models.PositiveSmallIntegerField(
        default=2,
        validators=[MinValueValidator(1), MaxValueValidator(7)],
        help_text="Minimum number of days with qualifying time blocks"
    )
    include_waitlist = models.BooleanField(
        default=False,
        help_text="Whether to include waitlisted survey responses in grouping"
    )

    # Metadata
    created_by = models.ForeignKey(
        Profile,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='created_grouping_sessions'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # Cached statistics
    total_players = models.PositiveIntegerField(default=0)
    grouped_count = models.PositiveIntegerField(default=0)
    ungrouped_count = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Grouping Session'
        verbose_name_plural = 'Grouping Sessions'

    def __str__(self):
        if self.name:
            return self.name
        source = self.survey.title if self.survey else self.tournament.name
        return f"Grouping for {source} ({self.created_at.strftime('%Y-%m-%d')})"

    def recalculate_statistics(self):
        """Update cached statistics from actual player data."""
        self.grouped_count = self.session_players.filter(
            player_groups__isnull=False
        ).distinct().count()
        self.ungrouped_count = self.session_players.filter(status='active').count()
        self.total_players = self.session_players.filter(
            status__in=['active', 'waitlist']
        ).count()
        self.save(update_fields=['grouped_count', 'ungrouped_count', 'total_players'])


class PlayerGroup(models.Model):
    """
    A group of players for a specific round. Tracks availability overlap metrics.
    """
    round = models.ForeignKey(
        Round,
        on_delete=models.CASCADE,
        null=True,
        related_name='player_groups',
        help_text="The round this group belongs to (populated by data migration)"
    )
    session = models.ForeignKey(
        GroupingSession,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='groups',
        help_text="Grouping session that generated this group (for availability data)"
    )

    # Members (M2M to SessionPlayer)
    session_players = models.ManyToManyField(
        'SessionPlayer',
        blank=True,
        related_name='player_groups',
        help_text="Players assigned to this group"
    )
    best_fit_players = models.ManyToManyField(
        'SessionPlayer',
        blank=True,
        related_name='best_fit_groups',
        help_text="Players not in the group with good availability overlap"
    )

    # Group-level metadata
    created_by = models.ForeignKey(
        Profile,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='created_player_groups'
    )
    created_via = models.CharField(
        max_length=20,
        choices=GroupingSession.AlgorithmChoices.choices,
        blank=True,
        help_text="How this group was created (algorithm or manual)"
    )

    # Identification
    group_number = models.PositiveSmallIntegerField(
        default=1,
        help_text="Sequential number within the round"
    )
    name = models.CharField(
        max_length=100,
        blank=True,
        help_text="Optional custom name for this group"
    )

    # Availability metrics (for availability-based sessions)
    all_hours = models.JSONField(
        default=list,
        help_text="List of hour-of-week integers (0-167) where any members are available"
    )
    overlap_hours = models.JSONField(
        default=list,
        help_text="List of hour-of-week integers (0-167) where all members overlap"
    )
    total_overlap_hours = models.PositiveSmallIntegerField(
        default=0,
        help_text="Total number of overlapping hours"
    )
    best_consecutive_block = models.PositiveSmallIntegerField(
        default=0,
        help_text="Length of longest consecutive overlapping block"
    )
    days_with_overlap = models.JSONField(
        default=list,
        help_text="List of day indices (0-6) that have overlapping hours"
    )


    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['round', 'group_number']
        unique_together = ('round', 'group_number')
        verbose_name = 'Player Group'
        verbose_name_plural = 'Player Groups'

    def __str__(self):
        if self.name:
            return self.name
        return f"Group {self.group_number}"

    @property
    def members(self):
        """Return profiles of players in this group."""
        return Profile.objects.filter(player_groups=self)

    @property
    def member_count(self):
        """Return count of players in this group."""
        return self.session_players.count()

    def recalculate_overlap(self):
        """
        Recalculate overlap metrics based on current members.
        Should be called after adding/removing members.
        Only meaningful for availability-based sessions.
        """
        if not self.session_id:
            return

        if self.session.grouping_type != GroupingSession.GroupingTypeChoices.AVAILABILITY:
            return

        if not self.session.survey:
            self._clear_overlap_metrics()
            return

        grouped_players = self.session_players.all().select_related('survey_response')

        if not grouped_players.exists():
            self._clear_overlap_metrics()
            return

        # Collect availability for each member
        availability_sets = []
        for player in grouped_players:
            if player.availability_hours:
                availability_sets.append(set(player.availability_hours))
            elif player.survey_response:
                hours = player.survey_response.get_combined_availability_hours()
                if hours:
                    availability_sets.append(hours)

        if not availability_sets:
            self._clear_overlap_metrics()
            return

        # Calculate intersection of all availability (hours where ALL members overlap)
        overlap = availability_sets[0]
        for avail_set in availability_sets[1:]:
            overlap = overlap.intersection(avail_set)

        # Calculate union of all availability (hours where ANY member is available)
        all_hours = set()
        for avail_set in availability_sets:
            all_hours = all_hours.union(avail_set)

        self.all_hours = sorted(list(all_hours))
        self.overlap_hours = sorted(list(overlap))
        self.total_overlap_hours = len(overlap)
        self.best_consecutive_block = self._calculate_best_consecutive(overlap)
        self.days_with_overlap = self._calculate_days_with_overlap(overlap)
        self.save(update_fields=[
            'all_hours', 'overlap_hours', 'total_overlap_hours',
            'best_consecutive_block', 'days_with_overlap'
        ])

    def _clear_overlap_metrics(self):
        self.all_hours = []
        self.overlap_hours = []
        self.total_overlap_hours = 0
        self.best_consecutive_block = 0
        self.days_with_overlap = []
        self.save(update_fields=[
            'all_hours', 'overlap_hours', 'total_overlap_hours',
            'best_consecutive_block', 'days_with_overlap'
        ])

    def _calculate_best_consecutive(self, hours_set):
        """Find longest consecutive run in hour-of-week set."""
        if not hours_set:
            return 0
        sorted_hours = sorted(hours_set)
        max_consecutive = 1
        current = 1
        for i in range(1, len(sorted_hours)):
            if sorted_hours[i] == sorted_hours[i - 1] + 1:
                current += 1
                max_consecutive = max(max_consecutive, current)
            else:
                current = 1
        return max_consecutive

    def _calculate_days_with_overlap(self, hours_set):
        """Determine which days have overlapping availability."""
        days = set()
        for hour in hours_set:
            day_index = hour // 24
            days.add(day_index)
        return sorted(list(days))

    def get_overlap_display(self):
        """Return human-readable overlap summary."""
        if not self.total_overlap_hours:
            return "No overlapping hours"

        day_names = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
        days_display = ', '.join(day_names[d] for d in self.days_with_overlap if d < 7)

        return f"{self.total_overlap_hours}h across {len(self.days_with_overlap)} days ({days_display}), best block: {self.best_consecutive_block}h"


class SessionPlayer(models.Model):
    """
    Represents a player's participation in a tournament's grouping session.
    Status is tournament-scoped: active players can be rostered into any round.
    Whether a player is grouped in a specific round is derived from round.player_groups.
    """
    class StatusChoices(models.TextChoices):
        ACTIVE = 'active', 'Active'
        WAITLIST = 'waitlist', 'On Waitlist'
        ELIMINATED = 'eliminated', 'Eliminated'

    session = models.ForeignKey(
        GroupingSession,
        on_delete=models.CASCADE,
        related_name='session_players'
    )
    profile = models.ForeignKey(
        Profile,
        on_delete=models.CASCADE,
        related_name='session_participations'
    )
    survey_response = models.ForeignKey(
        'the_tavern.SurveyResponse',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='session_players',
        help_text="The response that provided this player's availability"
    )
    status = models.CharField(
        max_length=20,
        choices=StatusChoices.choices,
        default=StatusChoices.ACTIVE
    )

    # Cached availability (for display/manual matching)
    availability_hours = models.JSONField(
        default=list,
        help_text="This player's available hours (0-167)"
    )

    class Meta:
        unique_together = ('session', 'profile')
        ordering = ['profile__display_name']
        verbose_name = 'Session Player'
        verbose_name_plural = 'Session Players'
        indexes = [
            models.Index(fields=['session', 'status']),
            models.Index(fields=['profile', 'status']),
        ]

    def __str__(self):
        return f"{self.profile.display_name} ({self.get_status_display()})"

