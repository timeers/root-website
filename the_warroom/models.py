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
    SINGLE_ELIM = 'Single Elimination'
    DOUBLE_ELIM = 'Double Elimination'
    SWISS = 'Swiss'
    ROUND_ROBIN = 'Round Robin'
    POOL_PLAY = 'Pool Play'
    CUSTOM = 'Custom'

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
    designer = models.ForeignKey(Profile, on_delete=models.SET_NULL, null=True, blank=True, related_name='hosted_tournaments')
    description = models.TextField(null=True, blank=True)
    picture = models.ImageField(upload_to='tournaments', null=True, blank=True)

    classification = models.CharField(
        max_length=50,
        choices=ClassificationTypes.choices,
        default=ClassificationTypes.TOURNAMENT
    )

    default_format = models.CharField(
        max_length=50, 
        choices=FormatChoices.choices, 
        default=FormatChoices.SINGLE_ELIM
    )

    # Access & Roster
    guild = models.ForeignKey(DiscordGuild, on_delete=models.SET_NULL, null=True, blank=True, related_name='tournaments', help_text='Link this Series with a Guild to allow members to record games')
    open_roster = models.BooleanField(default=True, help_text='Allow any player to be added to games in this Series')
    # Player management now handled via GroupingSession/SessionPlayer pattern
    # Use get_players_queryset(), get_waitlist_players_queryset(), get_eliminated_players_queryset()
    publicly_visible = models.BooleanField(default=False, help_text='Display this Series on the Series home page')

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
        session = self.master_session
        return Profile.objects.filter(
            session_participations__session=session
        ).distinct()

    # New master session pattern for player management
    @property
    def master_session(self):
        """Get or create the master player management session."""

        # Check cache first (within this instance)
        if not hasattr(self, '_master_session_cache'):
            session = self.grouping_sessions.filter(
                round__isnull=True,
                grouping_type='manual'
            ).first()

            if not session:
                # Import here to avoid circular imports
                from the_warroom.models import GroupingSession
                session = GroupingSession.objects.create(
                    tournament=self,
                    name=f"Master Session - {self.name}",
                    grouping_type='manual',
                    status='draft',
                    min_group_size=self.min_players,
                    max_group_size=self.max_players,
                )

            self._master_session_cache = session

        return self._master_session_cache

    def get_players_queryset(self):
        """Active players (UNGROUPED status in master session)."""
        from the_gatehouse.models import Profile
        session = self.master_session  # Auto-creates if doesn't exist
        return Profile.objects.filter(
            session_participations__session=session,
            session_participations__status='ungrouped'
        )

    def get_waitlist_players_queryset(self):
        """Waitlisted players (WAITLIST status in master session)."""
        from the_gatehouse.models import Profile
        session = self.master_session
        return Profile.objects.filter(
            session_participations__session=session,
            session_participations__status='waitlist'
        )

    def get_eliminated_players_queryset(self):
        """Eliminated players (ELIMINATED status in master session)."""
        from the_gatehouse.models import Profile
        session = self.master_session
        return Profile.objects.filter(
            session_participations__session=session,
            session_participations__status='eliminated'
        )

    def move_player(self, profile, from_status, to_status):
        """Move player between statuses (ungrouped/waitlist/eliminated)."""
        from the_warroom.models import SessionPlayer
        session = self.master_session

        if from_status is None:
            # Check if SessionPlayer already exists with any status
            existing_sp = SessionPlayer.objects.filter(
                session=session,
                profile=profile
            ).first()

            if existing_sp:
                # Player already exists with a different status - update it
                sp = existing_sp
                sp.status = to_status
            else:
                # Create new SessionPlayer
                sp = SessionPlayer.objects.create(
                    session=session,
                    profile=profile,
                    status=to_status,
                    added_via='manual'
                )
        else:
            try:
                sp = SessionPlayer.objects.get(
                    session=session,
                    profile=profile,
                    status=from_status
                )
                sp.status = to_status
            except SessionPlayer.DoesNotExist:
                # Handle case where player doesn't exist in this status
                raise ValueError(f"Player {profile} not found with status {from_status}")

        # If eliminating, remove from all round sessions
        if to_status == 'eliminated':
            for round_obj in self.rounds.all():
                # Find the round's master session and remove player from it
                round_session = round_obj.master_session
                if round_session:
                    SessionPlayer.objects.filter(
                        session=round_session,
                        profile=profile
                    ).delete()

        sp.save()
        session.recalculate_statistics()

    def add_player_to_tournament(self, profile, status='ungrouped'):
        """Add a player to the tournament."""
        from the_warroom.models import SessionPlayer
        session = self.master_session

        SessionPlayer.objects.update_or_create(
            session=session,
            profile=profile,
            defaults={
                'status': status,
                'added_via': 'manual'
            }
        )
        session.recalculate_statistics()

    def remove_player_from_tournament(self, profile):
        """Remove a player completely from the tournament."""
        from the_warroom.models import SessionPlayer
        session = self.master_session

        SessionPlayer.objects.filter(
            session=session,
            profile=profile
        ).delete()
        session.recalculate_statistics()

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

    round_number = models.PositiveIntegerField()  # Round number (e.g., 1, 2, 3, etc.)
    start_date = models.DateTimeField()
    end_date = models.DateTimeField(null=True, blank=True)  # Can be null if round hasn't ended yet
    game_threshold = models.IntegerField(default=0, validators=[MinValueValidator(0)])

    format = models.CharField(
        max_length=50, 
        choices=FormatChoices.choices, 
        blank=True,
        null=True,
        help_text="Leave blank to use tournament's default format"
    )

    # Player management now handled via GroupingSession/SessionPlayer pattern
    # Use get_players_queryset(), current_player_queryset(), or create_round_session()
    
    slug = models.SlugField(null=True, blank=True)

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
            return self.format or self.tournament.default_format

    def current_player_queryset(self):
        """Return players for this round, falling back to tournament if none."""
        # Check if round has its own master session
        session = self.master_session

        if session:
            # Round has its own player list via master session
            from the_gatehouse.models import Profile
            round_players = Profile.objects.filter(
                session_participations__session=session,
                session_participations__status='ungrouped'
            )
            if round_players.exists():
                return round_players

        # Fallback: inherit from tournament players
        return self.tournament.get_players_queryset()

    def all_player_queryset(self):
        """All players in the round (active, waitlist, and eliminated)."""
        from the_gatehouse.models import Profile
        session = self.master_session

        if session:
            # Round has its own session, use it
            return Profile.objects.filter(
                session_participations__session=session
            ).distinct()
        else:
            # No round session, inherit from tournament
            return self.tournament.all_player_queryset()

    # New master session pattern for round-specific player management
    @property
    def master_session(self):
        """Get round's master session if it exists (explicit creation only)."""
        if not hasattr(self, '_master_session_cache'):
            session = self.grouping_sessions.filter(grouping_type='manual').first()
            self._master_session_cache = session
        return self._master_session_cache

    def create_round_session(self):
        """Explicitly create a round-specific player session, copying from tournament."""
        from the_warroom.models import GroupingSession, SessionPlayer

        if self.master_session:
            return self.master_session

        # Create new session for this round
        session = GroupingSession.objects.create(
            tournament=self.tournament,
            tournament_round=self,
            name=f"Round Session - {self.name}",
            grouping_type='manual',
            status='draft',
        )

        # Copy ungrouped players from tournament's master session
        tournament_session = self.tournament.master_session
        for sp in tournament_session.session_players.filter(status='ungrouped'):
            SessionPlayer.objects.create(
                session=session,
                profile=sp.profile,
                status='ungrouped',
                added_via='manual'
            )

        session.recalculate_statistics()
        self._master_session_cache = session
        return session

    def get_players_queryset(self):
        """Players for this round."""
        session = self.master_session

        if session:
            from the_gatehouse.models import Profile
            return Profile.objects.filter(
                session_participations__session=session,
                session_participations__status='ungrouped'
            )
        else:
            # No round session, inherit from tournament
            return self.tournament.get_players_queryset()

    def get_waitlist_players_queryset(self):
        """Waitlisted players for this round."""
        session = self.master_session

        if session:
            from the_gatehouse.models import Profile
            return Profile.objects.filter(
                session_participations__session=session,
                session_participations__status='waitlist'
            )
        else:
            return self.tournament.get_waitlist_players_queryset()

    def get_eliminated_players_queryset(self):
        """Eliminated players for this round."""
        session = self.master_session

        if session:
            from the_gatehouse.models import Profile
            return Profile.objects.filter(
                session_participations__session=session,
                session_participations__status='eliminated'
            )
        else:
            return self.tournament.get_eliminated_players_queryset()

    def add_player_to_round(self, profile, status='ungrouped'):
        """Add a player to this specific round (creates round session if needed)."""
        from the_warroom.models import SessionPlayer

        # Ensure round has its own session
        if not self.master_session:
            self.create_round_session()

        session = self.master_session
        SessionPlayer.objects.update_or_create(
            session=session,
            profile=profile,
            defaults={
                'status': status,
                'added_via': 'manual'
            }
        )
        session.recalculate_statistics()

    def remove_player_from_round(self, profile):
        """Remove a player from this specific round."""
        from the_warroom.models import SessionPlayer

        session = self.master_session
        if session:
            SessionPlayer.objects.filter(
                session=session,
                profile=profile
            ).delete()
            session.recalculate_statistics()

    def move_player(self, profile, from_status, to_status):
        """Move player between statuses (ungrouped/waitlist/eliminated) for this round."""
        from the_warroom.models import SessionPlayer

        # Ensure round has master session
        if not self.master_session:
            self.create_round_session()

        session = self.master_session

        if from_status is None:
            # Check if SessionPlayer already exists with any status
            existing_sp = SessionPlayer.objects.filter(
                session=session,
                profile=profile
            ).first()

            if existing_sp:
                # Player already exists with a different status - update it
                sp = existing_sp
                sp.status = to_status
            else:
                # Create new SessionPlayer
                sp = SessionPlayer.objects.create(
                    session=session,
                    profile=profile,
                    status=to_status,
                    added_via='manual'
                )
        else:
            try:
                sp = SessionPlayer.objects.get(
                    session=session,
                    profile=profile,
                    status=from_status
                )
                sp.status = to_status
            except SessionPlayer.DoesNotExist:
                # Player might have been moved by another action, try to find by profile
                sp = SessionPlayer.objects.filter(
                    session=session,
                    profile=profile
                ).first()
                if sp:
                    sp.status = to_status
                else:
                    # Create new
                    sp = SessionPlayer.objects.create(
                        session=session,
                        profile=profile,
                        status=to_status,
                        added_via='manual'
                    )

        # Handle removal (to_status is None)
        if to_status is None:
            sp.delete()
        else:
            sp.save()

        session.recalculate_statistics()

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

    class StatusChoices(models.TextChoices):
        PROCESSING = 'processing', 'Processing'
        DRAFT = 'draft', 'Draft'
        FINALIZED = 'finalized', 'Finalized'
        ARCHIVED = 'archived', 'Archived'
        ERROR = 'error', 'Error'

    # Source (at least one should be set)
    survey = models.ForeignKey(
        'the_tavern.Survey',
        on_delete=models.CASCADE,
        null=True, blank=True,
        related_name='grouping_sessions',
        help_text="Source survey for availability data (optional)"
    )
    tournament = models.ForeignKey(
        Tournament,
        on_delete=models.CASCADE,
        null=True, blank=True,
        related_name='grouping_sessions'
    )
    tournament_round = models.ForeignKey(
        Round,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='grouping_sessions',
        help_text="Target round. If null when tournament set, a new round may be created."
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

    # Configuration (primarily for availability-based grouping)
    min_group_size = models.PositiveSmallIntegerField(
        default=4,
        validators=[MinValueValidator(1), MaxValueValidator(20)]
    )
    max_group_size = models.PositiveSmallIntegerField(
        default=4,
        validators=[MinValueValidator(1), MaxValueValidator(20)]
    )
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

    # Status
    status = models.CharField(
        max_length=20,
        choices=StatusChoices.choices,
        default=StatusChoices.DRAFT
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

    notes = models.TextField(blank=True, help_text="Admin notes about this grouping session")

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Grouping Session'
        verbose_name_plural = 'Grouping Sessions'

    def __str__(self):
        if self.name:
            return self.name
        source = self.survey.title if self.survey else (self.tournament.name if self.tournament else 'Unknown')
        return f"Grouping for {source} ({self.created_at.strftime('%Y-%m-%d')})"

    def clean(self):
        if self.min_group_size > self.max_group_size:
            raise ValidationError("Minimum group size cannot exceed maximum group size.")
        if not self.survey and not self.tournament and not self.round:
            raise ValidationError("At least one of survey, tournament, or round must be specified.")
        if self.round and self.tournament and self.round.tournament != self.tournament:
            raise ValidationError("Round must belong to the specified tournament.")

    def recalculate_statistics(self):
        """Update cached statistics from actual player data."""
        self.grouped_count = self.session_players.filter(status='grouped').count()
        self.ungrouped_count = self.session_players.filter(status='ungrouped').count()
        # Note: waitlist players are in session but not counted in total_players
        self.total_players = self.grouped_count + self.ungrouped_count
        self.save(update_fields=['grouped_count', 'ungrouped_count', 'total_players'])


class PlayerGroup(models.Model):
    """
    A group of players. For availability-based sessions, tracks overlap metrics.
    """
    session = models.ForeignKey(
        GroupingSession,
        on_delete=models.CASCADE,
        related_name='groups'
    )

    # Note: members are accessed via session_players relation with status='grouped'
    # The members property below provides backward-compatible access

    # Identification
    group_number = models.PositiveSmallIntegerField(
        default=1,
        help_text="Sequential number within the session"
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

    # Link to created game (after finalization)
    game = models.ForeignKey(
        Game,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='source_player_group',
        help_text="Game created from this group"
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['session', 'group_number']
        unique_together = ('session', 'group_number')
        verbose_name = 'Player Group'
        verbose_name_plural = 'Player Groups'

    def __str__(self):
        if self.name:
            return self.name
        return f"Group {self.group_number}"

    @property
    def members(self):
        """Return profiles of grouped players in this group."""
        return Profile.objects.filter(
            session_participations__group=self,
            session_participations__status='grouped'
        )

    @property
    def member_count(self):
        """Return count of grouped players in this group."""
        return self.session_players.filter(status='grouped').count()

    def recalculate_overlap(self):
        """
        Recalculate overlap metrics based on current members.
        Should be called after adding/removing members.
        Only meaningful for availability-based sessions.
        """
        if self.session.grouping_type != GroupingSession.GroupingTypeChoices.AVAILABILITY:
            return

        if not self.session.survey:
            self._clear_overlap_metrics()
            return

        # Get availability from session_players with status='grouped'
        grouped_players = self.session_players.filter(status='grouped').select_related('survey_response')

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
    Represents a player's participation in a grouping session.
    Can be grouped, ungrouped, or on the session waitlist.
    """
    class StatusChoices(models.TextChoices):
        GROUPED = 'grouped', 'In Group'
        UNGROUPED = 'ungrouped', 'Ungrouped'
        WAITLIST = 'waitlist', 'On Waitlist'
        ELIMINATED = 'eliminated', 'Eliminated'

    class AddedViaChoices(models.TextChoices):
        ALGORITHM = 'algorithm', 'Algorithm'
        MANUAL = 'manual', 'Manual'
        REASSIGNED = 'reassigned', 'Reassigned'

    class ReasonChoices(models.TextChoices):
        NO_COMPATIBLE = 'no_compatible', 'No compatible groups found'
        GROUPS_FULL = 'groups_full', 'All compatible groups at max size'
        LOW_AVAILABILITY = 'low_availability', 'Insufficient availability hours'
        WAITLIST = 'waitlist', 'On survey waitlist'
        PENDING = 'pending', 'Pending assignment'
        MANUAL = 'manual', 'Manually removed from groups'

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
    group = models.ForeignKey(
        PlayerGroup,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='session_players',
        help_text="The group this player is assigned to (null if ungrouped or waitlist)"
    )
    status = models.CharField(
        max_length=20,
        choices=StatusChoices.choices,
        default=StatusChoices.UNGROUPED
    )

    # Assignment metadata
    added_via = models.CharField(
        max_length=20,
        choices=AddedViaChoices.choices,
        default=AddedViaChoices.ALGORITHM
    )
    added_by = models.ForeignKey(
        Profile,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='+',
        help_text="Who added this player (for manual additions)"
    )
    added_at = models.DateTimeField(auto_now_add=True)

    # Ungrouped reason tracking
    reason = models.CharField(
        max_length=30,
        choices=ReasonChoices.choices,
        null=True, blank=True,
        help_text="Why this player is ungrouped (for display)"
    )

    # Best fit tracking (for ungrouped players)
    best_fit_group = models.ForeignKey(
        PlayerGroup,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='+',
        help_text="Group with best compatibility (even if not added)"
    )
    best_fit_overlap_hours = models.PositiveSmallIntegerField(
        default=0,
        help_text="Hours of overlap with best_fit_group"
    )

    # Cached availability (for display/manual matching)
    availability_hours = models.JSONField(
        default=list,
        help_text="This player's available hours (0-167)"
    )

    class Meta:
        unique_together = ('session', 'profile')
        ordering = ['added_at']
        verbose_name = 'Session Player'
        verbose_name_plural = 'Session Players'
        indexes = [
            models.Index(fields=['session', 'status']),
            models.Index(fields=['session', 'profile', 'status']),
            models.Index(fields=['profile', 'status']),
        ]

    def __str__(self):
        status_display = self.get_status_display()
        if self.group:
            return f"{self.profile.display_name} in {self.group} ({status_display})"
        return f"{self.profile.display_name} ({status_display})"


class PlayerGroupMembership(models.Model):
    """
    DEPRECATED: Use SessionPlayer instead.
    Through model for group membership, storing per-member metadata.
    Kept for data migration purposes - will be removed after migration.
    """
    class AddedViaChoices(models.TextChoices):
        ALGORITHM = 'algorithm', 'Algorithm'
        MANUAL = 'manual', 'Manual'
        REASSIGNED = 'reassigned', 'Reassigned'

    group = models.ForeignKey(
        PlayerGroup,
        on_delete=models.CASCADE,
        related_name='memberships'
    )
    profile = models.ForeignKey(
        Profile,
        on_delete=models.CASCADE,
        related_name='player_group_memberships'
    )
    survey_response = models.ForeignKey(
        'the_tavern.SurveyResponse',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='group_memberships',
        help_text="The response that provided this member's availability"
    )

    added_at = models.DateTimeField(auto_now_add=True)
    added_by = models.ForeignKey(
        Profile,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='added_group_members',
        help_text="Who added this member (for manual additions)"
    )
    added_via = models.CharField(
        max_length=20,
        choices=AddedViaChoices.choices,
        default=AddedViaChoices.ALGORITHM
    )

    class Meta:
        unique_together = ('group', 'profile')
        ordering = ['added_at']
        verbose_name = 'Group Membership'
        verbose_name_plural = 'Group Memberships'

    def __str__(self):
        return f"{self.profile.name} in {self.group}"


class UngroupedPlayer(models.Model):
    """
    DEPRECATED: Use SessionPlayer with status='ungrouped' or 'waitlist' instead.
    Players who couldn't be assigned to a group or are waiting assignment.
    Kept for data migration purposes - will be removed after migration.
    """
    class ReasonChoices(models.TextChoices):
        NO_COMPATIBLE = 'no_compatible', 'No compatible groups found'
        GROUPS_FULL = 'groups_full', 'All compatible groups at max size'
        LOW_AVAILABILITY = 'low_availability', 'Insufficient availability hours'
        WAITLIST = 'waitlist', 'On survey waitlist'
        PENDING = 'pending', 'Pending assignment'
        MANUAL = 'manual', 'Manually removed from groups'

    session = models.ForeignKey(
        GroupingSession,
        on_delete=models.CASCADE,
        related_name='ungrouped_players'
    )
    profile = models.ForeignKey(
        Profile,
        on_delete=models.CASCADE,
        related_name='ungrouped_sessions'
    )
    survey_response = models.ForeignKey(
        'the_tavern.SurveyResponse',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='ungrouped_records'
    )

    reason = models.CharField(
        max_length=30,
        choices=ReasonChoices.choices,
        default=ReasonChoices.PENDING
    )
    is_waitlist = models.BooleanField(
        default=False,
        help_text="True if player was on survey waitlist"
    )

    # Best fit tracking
    best_fit_group = models.ForeignKey(
        PlayerGroup,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='potential_members',
        help_text="Group with best compatibility (even if not added)"
    )
    best_fit_overlap_hours = models.PositiveSmallIntegerField(
        default=0,
        help_text="Hours of overlap with best_fit_group"
    )

    # Cached availability (for display/manual matching)
    availability_hours = models.JSONField(
        default=list,
        help_text="This player's available hours (0-167)"
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('session', 'profile')
        ordering = ['created_at']
        verbose_name = 'Ungrouped Player'
        verbose_name_plural = 'Ungrouped Players'

    def __str__(self):
        return f"{self.profile.name} (ungrouped - {self.get_reason_display()})"

