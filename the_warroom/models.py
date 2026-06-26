from dataclasses import dataclass

from django.db import models, transaction
from django.db.models import Q, Sum, Max, Prefetch, Count

from django.utils import timezone
from django.urls import reverse
from django.core.validators import MinValueValidator, MaxValueValidator
from django.core.exceptions import ValidationError

from the_gatehouse.models import Profile, DiscordGuild
from the_gatehouse.utils import NameConvention
from the_keep.models import Deck, Map, Faction, Landmark, Hireling, Vagabond, Tweak, StatusChoices
from the_keep.utils import delete_old_image


@dataclass
class EditPermission:
    allowed: bool
    reason: str = None
    def __bool__(self):
        return self.allowed
    @property
    def reason_label(self):
        labels = {
            'recorder': 'Recorder Actions',
            'participant': 'Match Participant Actions',
            'organizer': 'Organizer Actions',
            'admin': 'Admin Actions',
        }
        return labels.get(self.reason, '')

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

class VideoPlatformChoices(models.TextChoices):
    YOUTUBE = 'youtube', 'YouTube'
    TWITCH = 'twitch', 'Twitch'

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
            Q(end_date__isnull=True) | Q(end_date__gt=timezone.now().date())
        )

    def is_available(self):
        """Filter for available tournaments."""
        now = timezone.now().date()
        return self.exclude(
            Q(is_active=False) |
            Q(status=CompetitionStatus.COMPLETED) |
            Q(start_date__gt=now) |
            Q(end_date__lt=now)
        )

    def not_available(self):
        """Filter for unavailable tournaments."""
        now = timezone.now().date()
        return self.filter(
            Q(is_active=False) |
            Q(status=CompetitionStatus.COMPLETED) |
            Q(start_date__gt=now) |
            Q(end_date__lt=now)
        )


class RoundQuerySet(models.QuerySet):
    def open(self):
        """Return rounds that are still open (end_date is null or in the future)"""
        return self.filter(
            Q(end_date__isnull=True) | Q(end_date__gt=timezone.now().date())
        )

    def is_available(self):
        """Filter for available rounds (cascades from stage and tournament)."""
        now = timezone.now().date()
        return self.exclude(
            # Round's own fields
            Q(is_active=False) |
            Q(status=CompetitionStatus.COMPLETED) |
            Q(start_date__gt=now) |
            Q(end_date__lt=now) |
            # Parent stage
            Q(stage__is_active=False) |
            Q(stage__status=CompetitionStatus.COMPLETED) |
            Q(stage__start_date__gt=now) |
            Q(stage__end_date__lt=now) |
            # Parent tournament
            Q(stage__tournament__is_active=False) |
            Q(stage__tournament__status=CompetitionStatus.COMPLETED) |
            Q(stage__tournament__start_date__gt=now) |
            Q(stage__tournament__end_date__lt=now)
        )

    def not_available(self):
        """Filter for unavailable rounds."""
        now = timezone.now().date()
        return self.filter(
            Q(is_active=False) |
            Q(status=CompetitionStatus.COMPLETED) |
            Q(start_date__gt=now) |
            Q(end_date__lt=now)
        )

class CompetitionStatus(models.TextChoices):
    ACTIVE = "Active"
    PENDING = "Pending"
    COMPLETED = "Completed"

ASSET_TYPES = {
    'maps': Map,
    'factions': Faction,
    'decks': Deck,
    'vagabonds': Vagabond,
    'landmarks': Landmark,
    'tweaks': Tweak,
    'hirelings': Hireling,
}

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
    class RecordingAccessTypes(models.TextChoices):
        MODERATORS = "moderators", "Moderators Only"
        SCHEDULED = "scheduled", "Scheduled Match Players"
        REGISTERED = "registered_players", "Registered Players"
        GUILD = "guild", "Guild Members"
    class RulesPlatformChoices(models.TextChoices):
        GOOGLE = 'google', 'Google Drive'
        DROPBOX = 'dropbox', 'Dropbox'

    # Nav tabs that moderators can hide (Overview is always shown). Drives both
    # the settings form and the per-tab visibility passed to the nav headers, so
    # adding a tab here makes it controllable everywhere without a migration.
    HIDEABLE_TABS = ['leaderboard', 'games', 'bracket', 'players', 'surveys', 'details']

    type = "Tournament"
    name = models.CharField(max_length=30, unique=True)
    designer = models.ForeignKey(
        Profile, 
        on_delete=models.SET_NULL, 
        null=True, blank=True, 
        related_name='hosted_tournaments',
        help_text='Owner can update the Series, add new rounds, manage player and assets.'
        )
    moderators = models.ManyToManyField(
        Profile,
        blank=True,
        related_name='moderated_tournaments',
        help_text='Moderators can manage players, stages, rounds, and surveys but cannot edit the Series itself.'
    )
    description = models.TextField(null=True, blank=True)
    rules = models.TextField(null=True, blank=True, help_text='Tournament rules that participants must agree to when registering.')
    rules_link = models.URLField(
        max_length=1000, null=True, blank=True,
        help_text='Optional link to an external rules document. Must be a Google Drive or Dropbox link.'
    )
    rules_platform = models.CharField(
        max_length=10, blank=True,
        choices=RulesPlatformChoices.choices,
        help_text='Auto-detected from rules_link URL.'
    )
    picture = models.ImageField(upload_to='tournaments', null=True, blank=True)

    classification = models.CharField(
        max_length=50,
        choices=ClassificationTypes.choices,
        default=ClassificationTypes.TOURNAMENT,
        help_text='Group: casual game group. League: semi-competitive. Tournament: structured and competitive.'
    )

    default_format = models.CharField(
        max_length=32,
        null=True,
        blank=True,
        choices=FormatChoices.choices,
    )

    # Access & Roster
    guild = models.ForeignKey(DiscordGuild, on_delete=models.SET_NULL, null=True, blank=True, related_name='tournaments', help_text='Link this League with a Guild to allow members to record games')
    open_roster = models.BooleanField(default=True, help_text='Allow any player to be added to a game. If disabled, only registered players will be available.')
    recording_access = models.CharField(
        max_length=20,
        choices=RecordingAccessTypes.choices,
        default=RecordingAccessTypes.MODERATORS,
        help_text=(
            'Who can record game results. Moderators Only: only admins, owner, and '
            'moderators. Scheduled Match Players: the above, plus players in a scheduled '
            'match can record that match. Registered Players: the above, plus registered '
            'players can record standalone games for rounds. Guild Members: the above, '
            'plus any member of the linked guild can record games for rounds.'
        ),
    )
    # Player management handled via TournamentPlayer
    # Use get_players_queryset(), get_waitlist_players_queryset(), get_eliminated_players_queryset()
    publicly_visible = models.BooleanField(default=False)

    # Asset Settings
    asset_mode = models.IntegerField(
        choices=AssetModeChoices.choices,
        default=AssetModeChoices.OPEN,
        help_text='Open: any asset. Official: all official assets. Selected: only chosen assets.'
    )
    include_clockwork = models.BooleanField(default=False)
    show_assets = models.BooleanField(
        default=True,
        help_text="Show the Allowed Assets section on detail pages."
    )

    # Nav tab visibility
    hidden_tabs = models.JSONField(
        default=list,
        blank=True,
        help_text=(
            "List of nav tab keys to hide (e.g. ['leaderboard','surveys']). "
            "Tabs not listed are shown. Overview is always shown."
        ),
    )

    factions = models.ManyToManyField(Faction, blank=True, related_name='tournaments')
    maps = models.ManyToManyField(Map, blank=True, related_name='tournaments')
    decks = models.ManyToManyField(Deck, blank=True, related_name='tournaments')
    hirelings = models.ManyToManyField(Hireling, blank=True, related_name='tournaments')
    landmarks = models.ManyToManyField(Landmark, blank=True, related_name='tournaments')
    tweaks = models.ManyToManyField(Tweak, blank=True, related_name='tournaments')
    vagabonds = models.ManyToManyField(Vagabond, blank=True, related_name='tournaments')

    enforce_player_count = models.BooleanField(default=False)
    max_players = models.PositiveSmallIntegerField(default=4,validators=[MinValueValidator(2)])
    min_players = models.PositiveSmallIntegerField(default=4,validators=[MinValueValidator(2)])

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

    start_date = models.DateField(
        null=True,
        blank=True,
    )
    end_date = models.DateField(
        null=True,
        blank=True,
        )

    slug = models.SlugField(unique=True, null=True, blank=True)

    use_stages = models.BooleanField(default=False, help_text='Enable if you want multiple stages.')

    is_active = models.BooleanField(
        default=True,
        help_text="Whether this tournament is currently active and visible to users"
    )

    objects = TournamentQuerySet.as_manager()

    status = models.CharField(
        max_length=16,
        choices=CompetitionStatus.choices,
        default=CompetitionStatus.PENDING
    )

    @property
    def is_open(self):
        """Check if tournament is currently open (end_date is null or in the future)"""
        if self.end_date is None:
            return True
        return timezone.now().date() < self.end_date

    def get_effective_status(self):
        """Return own status (tournament has no parent)."""
        return self.status

    def is_available(self):
        """Check if tournament is currently available."""
        if not self.is_active:
            return False
        if self.status == CompetitionStatus.COMPLETED:
            return False
        now = timezone.now().date()
        if self.start_date and now < self.start_date:
            return False
        if self.end_date and now > self.end_date:
            return False
        return True

    def has_started(self):
        """Check if tournament has started."""
        now = timezone.now().date()
        if self.start_date and now < self.start_date:
            return False
        return True

    def has_ended(self):
        """Check if tournament has ended."""
        now = timezone.now().date()
        if self.end_date and now > self.end_date:
            return True
        return False

    def has_permission(self, profile):
        """Check if profile is designer, moderator, or admin."""
        return profile.admin or profile == self.designer or self.moderators.filter(pk=profile.pk).exists()

    def players_can_record_matches(self):
        """Seated players may record their own scheduled match (SCHEDULED, REGISTERED, GUILD)."""
        return self.recording_access in (
            self.RecordingAccessTypes.SCHEDULED,
            self.RecordingAccessTypes.REGISTERED,
            self.RecordingAccessTypes.GUILD,
        )

    def players_can_record_standalone(self):
        """Registered players may record standalone games for rounds (REGISTERED, GUILD)."""
        return self.recording_access in (
            self.RecordingAccessTypes.REGISTERED,
            self.RecordingAccessTypes.GUILD,
        )

    def guild_members_can_record(self, profile):
        """Under GUILD access, any member of the linked guild may record games
        for this tournament's rounds, even if not a stage participant."""
        if self.recording_access != self.RecordingAccessTypes.GUILD:
            return False
        if not self.guild_id:
            return False
        return profile.guilds.filter(pk=self.guild_id).exists()

    def tab_visible(self, key):
        """Whether a nav tab is enabled (not in hidden_tabs). Unknown keys default visible."""
        return key not in (self.hidden_tabs or [])

    def __str__(self):
        return self.name
    
    def get_absolute_url(self):
        return reverse('tournament-detail', kwargs={'slug': self.slug})

    def get_settings_url(self):
        return reverse('tournament-settings', kwargs={'slug': self.slug})

    def get_schedule_url(self):
        return reverse('tournament-schedule-page', kwargs={'slug': self.slug})

    def get_edit_url(self):
        return reverse('tournament-dynamic-update', kwargs={'slug': self.slug})

    def get_players_url(self):
        return reverse('tournament-manage-players', kwargs={'slug': self.slug})

    def get_assets_url(self):
        return reverse('tournament-manage-assets', kwargs={'slug': self.slug})

    def get_update_url(self):
        return reverse('tournament-dynamic-update', kwargs={'slug': self.slug})

    def get_asset_querysets(self):
        """Return a dict of querysets for each asset type, filtered by asset_mode."""
        if self.asset_mode == AssetModeChoices.OPEN:
            assets = {key: model.objects.all() for key, model in ASSET_TYPES.items()}
        elif self.asset_mode == AssetModeChoices.OFFICIAL:
            assets = {key: model.objects.filter(official=True) for key, model in ASSET_TYPES.items()}
        else:
            # SELECTED mode
            assets = {key: getattr(self, key).all() for key in ASSET_TYPES}

        if self.platform == PlatformChoices.DWD:
            assets = {key: qs.filter(in_root_digital=True) for key, qs in assets.items()}

        if not self.include_clockwork:
            assets['factions'] = assets['factions'].exclude(component="Clockwork")

        return assets

    def game_count(self):
        # Counts the number of games associated with this tournament
        return Game.objects.filter(round__stage__tournament=self, final=True).count()

    def get_game_queryset(self):
        games = Game.objects.filter(round__stage__tournament=self, final=True).all()
        return games


    def all_player_count(self):
        return Effort.objects.filter(game__round__stage__tournament=self).values('player').distinct().count()


    def all_player_queryset(self):
        """All players in the tournament (active, waitlist, and eliminated)."""
        from the_gatehouse.models import Profile
        return Profile.objects.filter(
            tournament_participations__tournament=self
        ).distinct()

    def get_players_queryset(self):
        """Active players in the tournament (ACTIVE status)."""
        from the_gatehouse.models import Profile
        from the_warroom.models import TournamentPlayer
        return Profile.objects.filter(
            tournament_participations__tournament=self,
            tournament_participations__status=TournamentPlayer.StatusChoices.REGISTERED
        ).distinct()

    def get_waitlist_players_queryset(self):
        """Waitlisted players in the tournament."""
        from the_gatehouse.models import Profile
        from the_warroom.models import TournamentPlayer
        return Profile.objects.filter(
            tournament_participations__tournament=self,
            tournament_participations__status=TournamentPlayer.StatusChoices.WAITLIST
        ).distinct()

    def get_eliminated_players_queryset(self):
        """Eliminated players in the tournament."""
        from the_gatehouse.models import Profile
        from the_warroom.models import TournamentPlayer
        return Profile.objects.filter(
            tournament_participations__tournament=self,
            tournament_participations__status=TournamentPlayer.StatusChoices.ELIMINATED
        ).distinct()

    def move_player(self, profile, from_status, to_status):
        """Update a player's status in the tournament."""
        from the_warroom.models import TournamentPlayer
        tp = TournamentPlayer.objects.filter(
            tournament=self,
            profile=profile
        ).first()

        if tp is None:
            if from_status is not None:
                raise ValueError(f"Player {profile} not found in tournament")
            TournamentPlayer.objects.create(
                tournament=self,
                profile=profile,
                status=to_status,
            )
        else:
            if to_status is None:
                tp.delete()
            else:
                tp.set_status(to_status)
                tp.save(update_fields=['status', 'waitlist_position'])

    def remove_player_from_tournament(self, profile):
        """Remove a player completely from the tournament."""
        from the_warroom.models import TournamentPlayer
        TournamentPlayer.objects.filter(
            tournament=self,
            profile=profile
        ).delete()

    def add_player(self, profile, status=None):
        """Add a profile to this tournament. If status is REGISTERED, also propagates to all active/pending stages."""
        if status is None:
            status = TournamentPlayer.StatusChoices.REGISTERED
        tp, created = TournamentPlayer.objects.get_or_create(
            profile=profile,
            tournament=self,
            defaults={'status': status}
        )
        if not created:
            tp.set_status(status)
            tp.save(update_fields=['status', 'waitlist_position'])
        if status == TournamentPlayer.StatusChoices.REGISTERED or not self.use_stages:
            stages = self.stages.all() if not self.use_stages else self.stages.filter(status__in=[CompetitionStatus.PENDING, CompetitionStatus.ACTIVE])
            for stage in stages:
                StageParticipant.objects.get_or_create(
                    tournament_player=tp,
                    stage=stage,
                    defaults={'status': StageParticipant.ParticipantStatus.ACTIVE}
                )

    def _recalculate_status(self):
        """Recalculate status from is_active and dates."""
        now = timezone.now().date()
        if not self.is_active:
            has_games = Game.objects.filter(round__stage__tournament=self).exists()
            self.status = CompetitionStatus.COMPLETED if has_games else CompetitionStatus.PENDING
            return
        if self.end_date and self.end_date < now:
            self.status = CompetitionStatus.COMPLETED
        elif self.start_date and self.start_date > now:
            self.status = CompetitionStatus.PENDING
        else:
            self.status = CompetitionStatus.ACTIVE

    def _cascade_dates_to_children(self):
        """Clamp child stage dates and propagate to rounds."""
        for stage in self.stages.all():
            changed = False
            if self.end_date and stage.end_date and stage.end_date > self.end_date:
                stage.end_date = self.end_date
                changed = True
            if self.start_date and stage.start_date and stage.start_date < self.start_date:
                stage.start_date = self.start_date
                changed = True
            if changed:
                # Stage dates changed — save will recalculate status and cascade to rounds
                stage.save()
            else:
                # Stage dates unchanged but tournament dates changed —
                # still need to check rounds against tournament's range
                stage._cascade_dates_to_children()

    def save(self, *args, **kwargs):
        update_fields = kwargs.get('update_fields')
        if not update_fields or 'status' not in update_fields or 'is_active' in update_fields:
            self._recalculate_status()

        # Auto-detect the rules link platform for icon display
        if self.rules_link:
            if 'dropbox.com' in self.rules_link:
                self.rules_platform = self.RulesPlatformChoices.DROPBOX
            elif 'drive.google.com' in self.rules_link or 'docs.google.com' in self.rules_link:
                self.rules_platform = self.RulesPlatformChoices.GOOGLE
            else:
                self.rules_platform = ''
        else:
            self.rules_platform = ''

        # Track date changes for cascading to children
        old_start = None
        old_end = None

        # Check if the image field has changed (only works if the instance is already saved)
        if self.pk:  # If the object already exists in the database
            old_instance = Tournament.objects.get(pk=self.pk)
            old_start = old_instance.start_date
            old_end = old_instance.end_date
            # List of fields to check and delete old images if necessary
            field_name = 'picture'

            old_image = getattr(old_instance, field_name)
            new_image = getattr(self, field_name)
            if old_image != new_image:
                delete_old_image(old_image)

        super().save(*args, **kwargs)

        # Cascade date changes to child stages
        if self.pk and (old_start != self.start_date or old_end != self.end_date):
            self._cascade_dates_to_children()



class Stage(models.Model):
    type = "Stage"

    class GroupingTypeChoices(models.TextChoices):
        AVAILABILITY = 'availability', 'Availability-based'
        MANUAL = 'manual', 'Manual'
        RANDOM = 'random', 'Random'
        SWISS = 'swiss', 'Avoid Repeats (Swiss)'

    tournament = models.ForeignKey(Tournament, on_delete=models.CASCADE, related_name='stages')

    name = models.CharField(max_length=100)
    order = models.PositiveIntegerField()

    use_rounds = models.BooleanField(default=False, help_text='Enable if this stage has multiple rounds.')

    stage_format = models.CharField(
        max_length=32,
        choices=FormatChoices.choices,
        null=True, blank=True,
        help_text="Leave blank to use tournament's default format"
    )

    winners_advance_to = models.ForeignKey(
        'self', null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='incoming_winners',
    )
    losers_advance_to = models.ForeignKey(
        'self', null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='incoming_losers',
    )
    advancement_count = models.PositiveSmallIntegerField(
        null=True, blank=True,
        help_text="For league-style stages: number of top-ranked players who advance."
    )

    status = models.CharField(
        max_length=16,
        choices=CompetitionStatus.choices,
        default=CompetitionStatus.PENDING
    )

    max_players = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
    )
    min_players = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
    )

    config = models.JSONField(default=dict, blank=True)

    # Grouping configuration
    grouping_type = models.CharField(
        max_length=20,
        choices=GroupingTypeChoices.choices,
        default=GroupingTypeChoices.AVAILABILITY
    )
    naming_convention = models.CharField(
        max_length=100,
        choices=NameConvention.choices,
        default=NameConvention.NUMERIC,
        help_text="Convention to use for group names"
    )
    include_waitlist = models.BooleanField(
        default=False,
        help_text="Whether to include waitlisted players in grouping"
    )

    # Leaderboard settings (inherit from Tournament if blank)
    game_threshold = models.IntegerField(
        null=True, blank=True, validators=[MinValueValidator(0)],

    )
    leaderboard_positions = models.IntegerField(
        null=True, blank=True, validators=[MinValueValidator(3), MaxValueValidator(30)],

    )

    # Grouping stats
    grouped_count = models.PositiveIntegerField(default=0)
    ungrouped_count = models.PositiveIntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)

    slug = models.SlugField(null=True, blank=True)

    start_date = models.DateField(
        null=True,
        blank=True,
    )
    end_date = models.DateField(null=True, blank=True)

    is_active = models.BooleanField(
        default=True,
        help_text="Whether this stage is currently active and visible to users"
    )

    def get_game_threshold(self):
        return self.game_threshold if self.game_threshold is not None else self.tournament.game_threshold

    def get_leaderboard_positions(self):
        return self.leaderboard_positions if self.leaderboard_positions is not None else self.tournament.leaderboard_positions

    def get_effective_status(self):
        """Return status considering parent tournament's status."""
        parent_status = self.tournament.status if self.tournament else None
        if parent_status == CompetitionStatus.COMPLETED:
            return CompetitionStatus.COMPLETED
        if parent_status == CompetitionStatus.PENDING:
            return CompetitionStatus.PENDING
        return self.status

    def is_available(self):
        """Check if stage is available (cascades from tournament)."""
        # Check parent tournament first
        if self.tournament and not self.tournament.is_available():
            return False

        # Then check self
        if not self.is_active:
            return False
        if self.status == CompetitionStatus.COMPLETED:
            return False
        now = timezone.now().date()
        if self.start_date and now < self.start_date:
            return False
        if self.end_date and now > self.end_date:
            return False
        return True

    def has_started(self):
        """Check if stage has started."""
        now = timezone.now().date()
        if self.start_date and now < self.start_date:
            return False
        return True

    def has_ended(self):
        """Check if stage has ended."""
        now = timezone.now().date()
        if self.end_date and now > self.end_date:
            return True
        return False

    def _recalculate_status(self):
        """Recalculate status from is_active and dates."""
        now = timezone.now().date()
        if not self.is_active:
            has_games = Game.objects.filter(round__stage=self).exists()
            self.status = CompetitionStatus.COMPLETED if has_games else CompetitionStatus.PENDING
            return
        if self.end_date and self.end_date < now:
            self.status = CompetitionStatus.COMPLETED
        elif self.start_date and self.start_date > now:
            self.status = CompetitionStatus.PENDING
        else:
            self.status = CompetitionStatus.ACTIVE

    def _cascade_dates_to_children(self):
        """Clamp child round dates to fit within stage and tournament date range."""
        # Effective boundaries: tighter of stage and tournament dates
        effective_end = self.end_date
        if self.tournament and self.tournament.end_date:
            if effective_end is None or self.tournament.end_date < effective_end:
                effective_end = self.tournament.end_date
        effective_start = self.start_date
        if self.tournament and self.tournament.start_date:
            if effective_start is None or self.tournament.start_date > effective_start:
                effective_start = self.tournament.start_date

        for round_obj in self.rounds.all():
            changed = False
            if effective_end and round_obj.end_date and round_obj.end_date > effective_end:
                round_obj.end_date = effective_end
                changed = True
            if effective_start and round_obj.start_date and round_obj.start_date < effective_start:
                round_obj.start_date = effective_start
                changed = True
            if changed:
                round_obj.save()

    def get_min_players_display(self):
        """Get the minimum players per game for this stage"""
        if not self.tournament.enforce_player_count:
            return None
        return self.min_players or self.tournament.min_players or None

    def get_max_players_display(self):
        """Get the maximum players per game for this stage"""
        if not self.tournament.enforce_player_count:
            return None
        return self.max_players or self.tournament.max_players or None


    def save(self, *args, **kwargs):
        update_fields = kwargs.get('update_fields')
        if not update_fields or 'status' not in update_fields or 'is_active' in update_fields:
            self._recalculate_status()

        # Track date changes for cascading to children
        old_start = None
        old_end = None
        if self.pk:
            old = Stage.objects.filter(pk=self.pk).values('start_date', 'end_date').first()
            if old:
                old_start = old['start_date']
                old_end = old['end_date']

        super().save(*args, **kwargs)

        # Cascade date changes to child rounds
        if self.pk and (old_start != self.start_date or old_end != self.end_date):
            self._cascade_dates_to_children()

    def __str__(self):
        return f"{self.tournament.name} - {self.name}"

    def get_absolute_url(self):
        if not self.tournament.use_stages:
            return self.tournament.get_absolute_url()
        return reverse('stage-overview', kwargs={'tournament_slug': self.tournament.slug, 'stage_slug': self.slug})

    def get_settings_url(self):
        return reverse('stage-settings', kwargs={'tournament_slug': self.tournament.slug, 'stage_slug': self.slug})

    def get_edit_url(self):
        return reverse('stage-update', kwargs={'tournament_slug': self.tournament.slug, 'stage_slug': self.slug})

    def get_players_url(self):
        return reverse('stage-manage-players', kwargs={'tournament_slug': self.tournament.slug, 'stage_slug': self.slug})

    def get_create_round_url(self):
        return reverse('round-create', kwargs={'tournament_slug': self.tournament.slug, 'stage_slug': self.slug})

    def get_schedule_url(self):
        return reverse('stage-schedule-page', kwargs={'tournament_slug': self.tournament.slug, 'stage_slug': self.slug})

    def add_player(self, profile):
        """Add a profile to this stage, creating a TournamentPlayer if needed."""
        tp, _ = TournamentPlayer.objects.get_or_create(
            profile=profile,
            tournament=self.tournament,
            defaults={'status': TournamentPlayer.StatusChoices.REGISTERED}
        )
        StageParticipant.objects.get_or_create(
            tournament_player=tp,
            stage=self,
            defaults={'status': StageParticipant.ParticipantStatus.ACTIVE}
        )

    def get_format(self):
        """Get the effective format for this stage"""
        return self.stage_format or self.tournament.default_format

    def game_count(self):
        return Game.objects.filter(round__stage=self, final=True).count()

    @property
    def all_player_count(self):
        return Effort.objects.filter(game__round__stage=self).values('player').distinct().count()

    def clean(self):
        """Validate stage dates against tournament dates."""
        super().clean()

        if not self.tournament_id:
            return

        # Only validate if parent has dates
        if self.start_date and self.tournament.start_date:
            if self.start_date < self.tournament.start_date:
                raise ValidationError({
                    'start_date': f'Stage cannot start before tournament ({self.tournament.start_date})'
                })

        if self.end_date and self.tournament.end_date:
            if self.end_date > self.tournament.end_date:
                raise ValidationError({
                    'end_date': f'Stage cannot end after tournament ({self.tournament.end_date})'
                })

def promote_n_waitlist_players(tournament, n):
    """
    Promote the top `n` players from the waitlist to ACTIVE and
    create StageParticipants for all non-completed stages.
    Returns the list of TournamentPlayers promoted.
    """
    promoted_players = []

    with transaction.atomic():
        waitlist_players = TournamentPlayer.objects.filter(
            tournament=tournament,
            status=TournamentPlayer.StatusChoices.WAITLIST
        ).order_by('waitlist_position')[:n]

        for tp in waitlist_players:
            tp.status = TournamentPlayer.StatusChoices.REGISTERED
            tp.waitlist_position = None
            tp.save()

            stages = tournament.stages.filter(status__in=[CompetitionStatus.PENDING, CompetitionStatus.ACTIVE])
            for stage in stages:
                StageParticipant.objects.get_or_create(
                    tournament_player=tp,
                    stage=stage,
                    defaults={'status': StageParticipant.ParticipantStatus.ACTIVE}
                )

            promoted_players.append(tp)

        remaining_waitlist = TournamentPlayer.objects.filter(
            tournament=tournament,
            status=TournamentPlayer.StatusChoices.WAITLIST
        ).order_by('waitlist_position')

        for i, tp in enumerate(remaining_waitlist, start=1):
            tp.waitlist_position = i
            tp.save()

    return promoted_players


def add_player_to_waitlist(profile, tournament):
    tp, _ = TournamentPlayer.objects.get_or_create(
        profile=profile,
        tournament=tournament,
        defaults={'status': TournamentPlayer.StatusChoices.WAITLIST}
    )

    tp.set_status(TournamentPlayer.StatusChoices.WAITLIST)
    tp.save()
    return tp



# This is a round of a tournament, series or playtest. It allows for leaderboard splits and a set end date.
class Round(models.Model):
    type = "Round"
    name = models.CharField(max_length=255, null=True, blank=True)  # Optional name, e.g., "Quarter-finals", "Finals"
    description = models.TextField(null=True, blank=True)
    tournament = models.ForeignKey(Tournament, on_delete=models.CASCADE, related_name='rounds', null=True, blank=True)  # Link to the tournament
    stage = models.ForeignKey(Stage, on_delete=models.CASCADE, related_name='rounds', null=True, blank=True)  # Link to the stage

    round_specific_format = models.CharField(
        max_length=50, 
        choices=FormatChoices.choices, 
        blank=True,
        null=True,
        help_text="Leave blank to use tournament's default format"
    )

    round_number = models.PositiveIntegerField()  # Round number (e.g., 1, 2, 3, etc.)
    start_date = models.DateField(
        null=True,
        blank=True,
    )
    end_date = models.DateField(null=True, blank=True)

    game_threshold = models.IntegerField(
        null=True, blank=True, validators=[MinValueValidator(0)],
        )
    leaderboard_positions = models.IntegerField(
        null=True, blank=True, validators=[MinValueValidator(3), MaxValueValidator(30)],
        )

    max_players = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
    )
    min_players = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
    )

    # Grouping lifecycle — scoped to this round, independent of other rounds using the same session
    class GroupingStatusChoices(models.TextChoices):
        PROCESSING = 'processing', 'Processing'
        DRAFT = 'draft', 'Draft'
        FINALIZED = 'finalized', 'Finalized'
        ERROR = 'error', 'Error'

    status = models.CharField(
        max_length=16,
        choices=CompetitionStatus.choices,
        default=CompetitionStatus.PENDING
    )

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

    # Bracket lifecycle
    class BracketStatusChoices(models.TextChoices):
        DRAFT = 'draft', 'Draft'
        FINALIZED = 'finalized', 'Finalized'

    bracket_status = models.CharField(
        max_length=20,
        choices=BracketStatusChoices.choices,
        default=BracketStatusChoices.DRAFT,
        help_text="Status of the bracket for this round"
    )

    slug = models.SlugField(null=True, blank=True)

    is_active = models.BooleanField(
        default=True,
        help_text="Whether this round is currently active and visible to users"
    )

    objects = RoundQuerySet.as_manager()

    @property
    def is_open(self):
        """Check if round is currently open (end_date is null or in the future)"""
        if self.end_date is None:
            return True
        return timezone.now().date() < self.end_date

    def get_effective_status(self):
        """Return status considering parent stage and tournament statuses."""
        if self.stage:
            parent_status = self.stage.get_effective_status()
            if parent_status == CompetitionStatus.COMPLETED:
                return CompetitionStatus.COMPLETED
            if parent_status == CompetitionStatus.PENDING:
                return CompetitionStatus.PENDING
        return self.status

    def is_available(self):
        """Check if round is available (cascades from stage and tournament)."""
        # Check parent stage first
        if self.stage and not self.stage.is_available():
            return False

        # Then check self
        if not self.is_active:
            return False
        if self.status == CompetitionStatus.COMPLETED:
            return False
        now = timezone.now().date()
        if self.start_date and now < self.start_date:
            return False
        if self.end_date and now > self.end_date:
            return False
        return True

    def has_started(self):
        """Check if round has started."""
        now = timezone.now().date()
        if self.start_date and now < self.start_date:
            return False
        return True

    def has_ended(self):
        """Check if round has ended."""
        now = timezone.now().date()
        if self.end_date and now > self.end_date:
            return True
        return False

    def _recalculate_status(self):
        """Recalculate status from is_active and dates."""
        now = timezone.now().date()
        if not self.is_active:
            has_games = Game.objects.filter(round=self).exists()
            self.status = CompetitionStatus.COMPLETED if has_games else CompetitionStatus.PENDING
            return
        if self.end_date and self.end_date < now:
            self.status = CompetitionStatus.COMPLETED
        elif self.start_date and self.start_date > now:
            self.status = CompetitionStatus.PENDING
        else:
            self.status = CompetitionStatus.ACTIVE

    def save(self, *args, **kwargs):
        update_fields = kwargs.get('update_fields')
        if not update_fields or 'status' not in update_fields or 'is_active' in update_fields:
            self._recalculate_status()
        super().save(*args, **kwargs)

    def get_tournament(self):
        """Return the tournament for this round, preferring stage.tournament over direct FK."""
        if self.stage:
            return self.stage.tournament
        return self.tournament

    @property
    def is_bracket_finalized(self):
        """True once this round's bracket is finalized and its matches are live.
        Mirrors the gate used on the matches page (_stage_matches_context)."""
        return self.bracket_status == self.BracketStatusChoices.FINALIZED

    def _stage_slug(self):
        return self.stage.slug if self.stage else None

    def get_absolute_url(self):
        if not self.stage:
            tournament = self.get_tournament()
            if tournament:
                return tournament.get_absolute_url()
            return '#'

        tournament = self.stage.tournament

        # No stages and no rounds — link to tournament
        if not tournament.use_stages and not self.stage.use_rounds:
            return tournament.get_absolute_url()

        # No rounds — link to stage
        if not self.stage.use_rounds:
            return self.stage.get_absolute_url()

        # No stages (simple round mode) — use simplified URL
        if not tournament.use_stages:
            return reverse('round-overview-simple', kwargs={
                'tournament_slug': tournament.slug,
                'round_slug': self.slug
            })

        # Full hierarchical URL
        return reverse('round-overview', kwargs={
            'tournament_slug': tournament.slug,
            'stage_slug': self.stage.slug,
            'round_slug': self.slug
        })

    def get_settings_url(self):
        tournament = self.stage.tournament
        if not tournament.use_stages:
            return reverse('round-settings-simple', kwargs={
                'tournament_slug': tournament.slug,
                'round_slug': self.slug
            })
        return reverse('round-settings', kwargs={
            'tournament_slug': tournament.slug,
            'stage_slug': self.stage.slug,
            'round_slug': self.slug
        })

    def get_edit_url(self):
        tournament = self.stage.tournament
        if not tournament.use_stages:
            return reverse('round-update-simple', kwargs={
                'tournament_slug': tournament.slug,
                'round_slug': self.slug
            })
        return reverse('round-update', kwargs={
            'tournament_slug': tournament.slug,
            'stage_slug': self.stage.slug,
            'round_slug': self.slug
        })

    def get_players_url(self):
        tournament = self.stage.tournament
        if not tournament.use_stages:
            return reverse('round-manage-players-simple', kwargs={
                'tournament_slug': tournament.slug,
                'round_slug': self.slug
            })
        return reverse('round-manage-players', kwargs={
            'tournament_slug': tournament.slug,
            'stage_slug': self.stage.slug,
            'round_slug': self.slug
        })

    def get_assets_url(self):
        return reverse('tournament-manage-assets', kwargs={'tournament_slug': self.stage.tournament.slug})

    def get_update_url(self):
        tournament = self.stage.tournament
        if not tournament.use_stages:
            return reverse('round-update-simple', kwargs={
                'tournament_slug': tournament.slug,
                'round_slug': self.slug
            })
        return reverse('round-update', kwargs={
            'tournament_slug': tournament.slug,
            'stage_slug': self.stage.slug,
            'round_slug': self.slug
        })

    def get_delete_url(self):
        tournament = self.stage.tournament
        if not tournament.use_stages:
            return reverse('round-delete-simple', kwargs={
                'tournament_slug': tournament.slug,
                'round_slug': self.slug,
                'pk': self.id
            })
        return reverse('round-delete', kwargs={
            'tournament_slug': tournament.slug,
            'stage_slug': self.stage.slug,
            'round_slug': self.slug,
            'pk': self.id
        })

    def get_leaderboard_url(self):
        tournament = self.stage.tournament
        if not tournament.use_stages:
            return reverse('round-leaderboard-simple', kwargs={
                'tournament_slug': tournament.slug,
                'round_slug': self.slug
            })
        return reverse('round-leaderboard-page', kwargs={
            'tournament_slug': tournament.slug,
            'stage_slug': self.stage.slug,
            'round_slug': self.slug
        })

    def get_games_url(self):
        tournament = self.stage.tournament
        if not tournament.use_stages:
            return reverse('round-games-simple', kwargs={
                'tournament_slug': tournament.slug,
                'round_slug': self.slug
            })
        return reverse('round-games-page', kwargs={
            'tournament_slug': tournament.slug,
            'stage_slug': self.stage.slug,
            'round_slug': self.slug
        })

    def get_roster_url(self):
        tournament = self.stage.tournament
        if not tournament.use_stages:
            return reverse('round-roster-simple', kwargs={
                'tournament_slug': tournament.slug,
                'round_slug': self.slug
            })
        return reverse('round-roster-page', kwargs={
            'tournament_slug': tournament.slug,
            'stage_slug': self.stage.slug,
            'round_slug': self.slug
        })

    def get_details_url(self):
        tournament = self.stage.tournament
        if not tournament.use_stages:
            return reverse('round-details-simple', kwargs={
                'tournament_slug': tournament.slug,
                'round_slug': self.slug
            })
        return reverse('round-details-page', kwargs={
            'tournament_slug': tournament.slug,
            'stage_slug': self.stage.slug,
            'round_slug': self.slug
        })

    def get_matches_url(self):
        tournament = self.stage.tournament
        if not tournament.use_stages:
            return reverse('round-matches-simple', kwargs={
                'tournament_slug': tournament.slug,
                'round_slug': self.slug
            })
        return reverse('round-matches-page', kwargs={
            'tournament_slug': tournament.slug,
            'stage_slug': self.stage.slug,
            'round_slug': self.slug
        })

    def get_schedule_url(self):
        tournament = self.stage.tournament
        if not tournament.use_stages:
            return reverse('round-schedule-simple', kwargs={
                'tournament_slug': tournament.slug,
                'round_slug': self.slug
            })
        return reverse('round-schedule-page', kwargs={
            'tournament_slug': tournament.slug,
            'stage_slug': self.stage.slug,
            'round_slug': self.slug
        })

    def get_format(self):
        """Get the effective format for this round"""
        if self.round_specific_format:
            return self.round_specific_format
        if self.stage:
            return self.stage.get_format()
        if self.tournament:
            return self.tournament.default_format
        return None

    def get_game_threshold(self):
        if self.game_threshold is not None:
            return self.game_threshold
        if self.stage:
            return self.stage.get_game_threshold()
        return self.tournament.game_threshold

    def get_leaderboard_positions(self):
        if self.leaderboard_positions is not None:
            return self.leaderboard_positions
        if self.stage:
            return self.stage.get_leaderboard_positions()
        return self.tournament.leaderboard_positions

    def clean(self):
        """Validate round dates against stage dates."""
        super().clean()

        if not self.stage:
            return

        # Only validate if parent has dates
        if self.start_date and self.stage.start_date:
            if self.start_date < self.stage.start_date:
                raise ValidationError({
                    'start_date': f'Round cannot start before stage ({self.stage.start_date})'
                })

        if self.end_date and self.stage.end_date:
            if self.end_date > self.stage.end_date:
                raise ValidationError({
                    'end_date': f'Round cannot end after stage ({self.stage.end_date})'
                })

    def get_min_players(self):
        """Get the minimum players per game for this round"""
        return self.min_players or self.stage.min_players or self.stage.tournament.min_players or 4

    def get_max_players(self):
        """Get the maximum players per game for this round"""
        return self.max_players or self.stage.max_players or self.stage.tournament.max_players or 4

    def get_min_players_display(self):
        """Get the minimum players per game for this stage"""
        if not self.stage.tournament.enforce_player_count:
            return None
        return self.min_players or self.stage.min_players or self.stage.tournament.min_players or None

    def get_max_players_display(self):
        """Get the maximum players per game for this stage"""
        if not self.stage.tournament.enforce_player_count:
            return None
        return self.max_players or self.stage.max_players or self.stage.tournament.max_players or None
    
    def current_player_queryset(self):
        """Return Profiles eligible to play in this round.
        If open_roster: all profiles. Otherwise: stage participants (ACTIVE),
        falling back to tournament-level registered players."""
        tournament = self.get_tournament()
        if tournament.open_roster:
            return Profile.objects.all()
        if self.stage:
            stage_players = Profile.objects.filter(
                tournament_participations__stage_participations__stage=self.stage,
                tournament_participations__stage_participations__status=StageParticipant.ParticipantStatus.ACTIVE,
            )
            if stage_players.exists():
                return stage_players
        return Profile.objects.filter(
            tournament_participations__tournament=tournament,
            tournament_participations__status=TournamentPlayer.StatusChoices.REGISTERED,
        )

    def __str__(self):

        if self.stage:
            tournament = self.stage.tournament
            if self.stage.use_rounds and tournament.use_stages:
                return f"{self.stage.tournament.name} - {self.stage.name} - {self.name}"
            elif self.stage.use_rounds:
                return f'{tournament.name} - {self.name}'
            elif tournament.use_stages:
                return f'{tournament.name} - {self.stage.name}'
            else:
                return tournament.name
        else:
            return self.name

    @property
    def all_player_count(self):
        return Effort.objects.filter(game__round=self).values('player').distinct().count() 
     
    def game_count(self):
        return Game.objects.filter(round=self, final=True).count()

    class Meta:
        ordering = ['-round_number']


class MatchSeries(models.Model):
    """
    A Series groups multiple Matches in a Round, typically used for
    best-of-X or repeated games where the outcome of the Match Series
    determines advancement.
    """
 
    # Core relations
    round = models.ForeignKey(
        Round,
        on_delete=models.CASCADE,
        related_name="series"
    )
 
    # Optional metadata
    name = models.CharField(max_length=100, blank=True, null=True)
    description = models.TextField(blank=True, null=True)
 
    status = models.CharField(
        max_length=16,
        choices=CompetitionStatus.choices,
        default=CompetitionStatus.PENDING
    )
 
    # Players competing in this series
    player_group = models.OneToOneField(
        'PlayerGroup',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='series',
        help_text="Group of players competing in this series"
    )

    # Optional fields for best-of-X style series
    number_of_games = models.PositiveIntegerField(default=1)
    winners = models.ManyToManyField(
        "StageParticipant",
        blank=True,
        related_name="won_series"
    )
    is_bye = models.BooleanField(default=False, help_text="True if this series is a bye (player advances without playing)")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
 
    class Meta:
        ordering = ["round", "id"]
 
    def __str__(self):
        return self.name or f"Series {self.id} in Round {self.round.round_number}"
 
    def is_complete(self):
        """Return True if series has winners or all matches are complete."""
        if self.winners.exists():
            return True
        # Alternatively, check if all matches are complete
        return all(match.status == CompetitionStatus.COMPLETED for match in self.matches.all())


class Match(models.Model):
    """A single match slot in a tournament bracket. Always belongs to a MatchSeries —
    even standalone matches use a series with number_of_games=1."""
    round = models.ForeignKey(Round, on_delete=models.CASCADE, related_name='matches')
    name = models.CharField(max_length=100, null=True, blank=True)
    match_number = models.PositiveSmallIntegerField(null=True, blank=True)
    series = models.ForeignKey(MatchSeries, on_delete=models.CASCADE, related_name='matches')
    game = models.OneToOneField(
        'Game', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='match',
        help_text="Game played for this match"
    )

    status = models.CharField(
        max_length=16,
        choices=CompetitionStatus.choices,
        default=CompetitionStatus.PENDING
    )
    scheduled_time = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['round', 'match_number']
        unique_together = [('round', 'match_number')]

    def save(self, *args, **kwargs):
        if self.match_number is None:
            last = Match.objects.filter(round=self.round).order_by('match_number').last()
            self.match_number = (last.match_number + 1) if last and last.match_number is not None else 1
        if not self.name and self.series_id and self.series.player_group_id:
            group_name = self.series.player_group.name
            if self.series.number_of_games > 1:
                series_position = Match.objects.filter(series=self.series).count() + 1
                self.name = f"{group_name} Game {series_position}"
            else:
                self.name = group_name
        super().save(*args, **kwargs)

    def clean(self):
        # Ensure game belongs to the same round as the match
        if self.game_id and self.game.round_id != self.round_id:
            raise ValidationError("The assigned game must belong to the same round as the match.")

    @property
    def display_status(self):
        """Status for display purposes. A match that hasn't started but has a
        scheduled time reads as 'Scheduled' rather than the stored 'Pending'."""
        if self.status == CompetitionStatus.PENDING and self.scheduled_time:
            return "Scheduled"
        return self.status

    @property
    def series_position(self):
        """This match's 1-based game number within its multi-game series, or
        None for single-game series. Reads from ``series.matches`` so a
        prefetched set is reused instead of issuing a query."""
        if not self.series_id or self.series.number_of_games <= 1:
            return None
        siblings = sorted(
            self.series.matches.all(),
            key=lambda m: (m.match_number is None, m.match_number),
        )
        for index, sibling in enumerate(siblings, start=1):
            if sibling.pk == self.pk:
                return index
        return None

    @property
    def winners(self):
        """Players who won the game linked to this match."""
        if not self.game_id:
            return Profile.objects.none()
        return Profile.objects.filter(efforts__game_id=self.game_id, efforts__win=True)

    @property
    def player_group(self):
        """Delegates to the series' player group."""
        return self.series.player_group

    @property
    def stage(self):
        """Convenience accessor — equivalent to match.round.stage."""
        return self.round.stage

    def get_matches_url(self):
        """URL of the page that lists this match, accounting for the variable
        tournament layout: a stage may skip rounds, and a tournament may skip
        stages. Mirrors the branching in Round.get_absolute_url(); extends
        Round.get_matches_url() with the stage-level (no-rounds) case."""
        round = self.round
        tournament = round.get_tournament()
        stage = round.stage

        # Stage without rounds — matches live at the stage level.
        if stage and not stage.use_rounds:
            return reverse('stage-matches-page', kwargs={
                'tournament_slug': tournament.slug,
                'stage_slug': stage.slug,
            })
        # Tournament without stages — simplified round URL (no stage_slug).
        if not tournament.use_stages:
            return reverse('round-matches-simple', kwargs={
                'tournament_slug': tournament.slug,
                'round_slug': round.slug,
            })
        # Full hierarchy.
        return reverse('round-matches-page', kwargs={
            'tournament_slug': tournament.slug,
            'stage_slug': stage.slug,
            'round_slug': round.slug,
        })

    def __str__(self):
        return self.name or f"Match {self.id}"


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
    video_link = models.URLField(
        max_length=500,
        blank=True,
        help_text="Video stream/recording URL for this group"
    )
    video_platform = models.CharField(
        max_length=10,
        blank=True,
        choices=VideoPlatformChoices.choices,
        help_text="Auto-detected from video_link URL"
    )

    nickname = models.CharField(max_length=50, null=True, blank=True)
    random_clearing = models.BooleanField(default=True)
    notes = models.TextField(null=True, blank=True)
    league_id = models.CharField(max_length=300, null=True, blank=True)


    # Automatic
    date_posted = models.DateTimeField(default=timezone.now, db_index=True)
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

    @staticmethod
    def with_efforts():
        """Standard select/prefetch for game list views."""
        return {
            'select': ['deck', 'map', 'round__stage__tournament'],
            'prefetch': [
                Prefetch(
                    'efforts',
                    queryset=Effort.objects.select_related(
                        'player', 'faction', 'vagabond', 'coalition_with'
                    )
                ),
            ],
        }

    def save(self, *args, **kwargs):
        if self.video_link:
            if 'twitch.tv' in self.video_link:
                self.video_platform = VideoPlatformChoices.TWITCH
            elif 'youtube.com' in self.video_link or 'youtu.be' in self.video_link:
                self.video_platform = VideoPlatformChoices.YOUTUBE
            else:
                self.video_platform = ''
        else:
            self.video_platform = ''
        super().save(*args, **kwargs)

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
        return reverse("game-update-v2", kwargs={"id": self.id})
    
    def get_delete_url(self):
        return reverse("game-delete", kwargs={"id": self.id})

    def get_tournament(self):
        """Return the tournament this game belongs to, if any."""
        if self.round:
            return self.round.get_tournament()
        return None

    def can_edit(self, profile):
        """Check if profile can edit this game. Returns EditPermission(allowed, reason).
        Checks from lowest to highest permission level."""
        has_match = hasattr(self, 'match') and self.match is not None
        # Recorder: any non-final game, or finalized non-match game
        if profile == self.recorder:
            if not self.final or not has_match:
                return EditPermission(True, 'recorder')
        # Match participants can edit non-final match games (unless tournament restricts recording)
        if has_match and not self.final:
            tournament = self.get_tournament()
            # Group moderators can always edit their group's non-final match games,
            # regardless of the tournament's recording_access tier.
            group = self.match.player_group
            if group and group.group_moderator_id == profile.id:
                return EditPermission(True, 'group_moderator')
            # Guild members can edit non-final match games under GUILD access,
            # even if they are not seated in the match.
            if tournament and tournament.guild_members_can_record(profile):
                return EditPermission(True, 'guild_member')
            if not tournament or tournament.players_can_record_matches():
                if Profile.objects.filter(
                    tournament_participations__stage_participations__matchseat__series=self.match.series,
                    pk=profile.pk
                ).exists():
                    return EditPermission(True, 'participant')
        # Admin: check before organizer so site admins are labeled "admin"
        # rather than "organizer" (Tournament.has_permission also passes for
        # admins, which would otherwise shadow this reason).
        if profile.admin:
            return EditPermission(True, 'admin')
        # Tournament organizer/moderator
        tournament = self.get_tournament()
        if tournament and tournament.has_permission(profile):
            return EditPermission(True, 'organizer')
        return EditPermission(False)

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
    # class StatusChoices(models.TextChoices):
    #     ACTIVE = 'Active'
    #     ELIMINATED = 'Eliminated'

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
    # player_status = models.CharField(max_length=50, choices=StatusChoices.choices, default=StatusChoices.ACTIVE)


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


def filtered_winrate(player=None, faction=None, tournament=None, platform=None):
    """Win rate over Efforts, filtered by any combination of player, faction,
    tournament (series), and platform. Mirrors the leaderboard formula
    (coalition wins count as half) in a single aggregate query so the result
    matches the site's leaderboards. Returns {total, win_points, win_rate}."""
    qs = Effort.objects.filter(game__final=True, game__test_match=False)
    if player:
        qs = qs.filter(player=player)
    if faction:
        qs = qs.filter(faction=faction)
    if platform:
        qs = qs.filter(game__platform=platform)
    if tournament:
        qs = qs.filter(
            Q(game__round__stage__tournament=tournament)
            | Q(game__round__tournament=tournament)
        )
    agg = qs.aggregate(
        total=Count('id'),
        wins=Count('id', filter=Q(win=True)),
        coalition=Count('id', filter=Q(win=True, game__coalition_win=True)),
    )
    total = agg['total'] or 0
    win_points = (agg['wins'] or 0) - (agg['coalition'] or 0) / 2
    win_rate = (win_points / total * 100) if total else 0.0
    return {'total': total, 'win_points': win_points, 'win_rate': win_rate}


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

class PlayerGroup(models.Model):
    """
    A group of players for a specific round. Tracks availability overlap metrics.
    """
    round = models.ForeignKey(
        Round,
        on_delete=models.CASCADE,
        related_name='player_groups',
        help_text="The round this group belongs to"
    )

    # Members (M2M to TournamentPlayer)
    tournament_players = models.ManyToManyField(
        'TournamentPlayer',
        blank=True,
        related_name='player_groups',
        help_text="Players assigned to this group"
    )
    best_fit_players = models.ManyToManyField(
        'TournamentPlayer',
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
        choices=Stage.GroupingTypeChoices.choices,
        blank=True,
        help_text="How this group was created"
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
    discord_thread = models.URLField(
        max_length=500,
        blank=True,
        help_text="Discord thread URL for this group"
    )
    video_link = models.URLField(
        max_length=500,
        blank=True,
        help_text="Video stream/recording URL for this group"
    )
    group_moderator = models.ForeignKey(
        Profile,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='moderated_player_groups',
        help_text="Profile who can record scheduled games for this group, even if not a tournament moderator or group member."
    )



    video_platform = models.CharField(
        max_length=10,
        blank=True,
        choices=VideoPlatformChoices.choices,
        help_text="Auto-detected from video_link URL"
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

    def save(self, *args, **kwargs):
        if self.video_link:
            if 'twitch.tv' in self.video_link:
                self.video_platform = VideoPlatformChoices.TWITCH
            elif 'youtube.com' in self.video_link or 'youtu.be' in self.video_link:
                self.video_platform = VideoPlatformChoices.YOUTUBE
            else:
                self.video_platform = ''
        else:
            self.video_platform = ''
        super().save(*args, **kwargs)

    @property
    def members(self):
        """Return profiles of players in this group."""
        return Profile.objects.filter(
            tournament_participations__player_groups=self
        )

    @property
    def member_count(self):
        """Return count of players in this group."""
        return self.tournament_players.count()

    def recalculate_overlap(self):
        """
        Recalculate overlap metrics based on current members' availability_hours.
        Should be called after adding/removing members.
        Only meaningful for availability-based grouping.
        """
        if self.round.stage.grouping_type != Stage.GroupingTypeChoices.AVAILABILITY:
            self._clear_overlap_metrics()
            return

        grouped_players = self.tournament_players.all().select_related('survey_response')

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


class TournamentPlayer(models.Model):

    class StatusChoices(models.TextChoices):
        REGISTERED = 'registered', 'Registered'
        WAITLIST = 'waitlist', 'On Waitlist'
        ELIMINATED = 'eliminated', 'Eliminated'

    profile = models.ForeignKey(Profile, on_delete=models.CASCADE, related_name='tournament_participations')
    tournament = models.ForeignKey(Tournament, on_delete=models.CASCADE, related_name='tournament_players')
    survey_response = models.ForeignKey(
        'the_tavern.SurveyResponse',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='tournament_players',
        help_text="The response that provided this player's availability"
    )
    status = models.CharField(
        max_length=16,
        choices=StatusChoices.choices,
        default=StatusChoices.REGISTERED
    )
    availability_hours = models.JSONField(
        default=list,
        help_text="This player's available hours (0-167)"
    )

    waitlist_position = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        unique_together = ('tournament', 'profile')
        ordering = ['profile__display_name']
        verbose_name = 'Tournament Player'
        verbose_name_plural = 'Tournament Players'
        indexes = [
            models.Index(fields=['tournament', 'status']),
            models.Index(fields=['profile', 'status']),
        ]

    def __str__(self):
        return f"{self.profile.display_name} ({self.get_status_display()})"

    def set_status(self, status):
        """Set status and manage waitlist_position consistently."""
        self.status = status
        if status == self.StatusChoices.WAITLIST and self.waitlist_position is None:
            max_pos = TournamentPlayer.objects.filter(
                tournament=self.tournament,
                status=self.StatusChoices.WAITLIST,
            ).aggregate(Max('waitlist_position'))['waitlist_position__max'] or 0
            self.waitlist_position = max_pos + 1
        elif status != self.StatusChoices.WAITLIST:
            self.waitlist_position = None

class StageParticipant(models.Model):
    class ParticipantStatus(models.TextChoices):
        ACTIVE = 'active', 'Active'
        ELIMINATED = 'eliminated', 'Eliminated'
        WITHDRAWN = 'withdrawn', 'Withdrawn'
        ADVANCED = 'advanced', 'Advanced'
    stage = models.ForeignKey(Stage, on_delete=models.CASCADE, related_name='participants')
    tournament_player = models.ForeignKey(TournamentPlayer, on_delete=models.CASCADE, related_name='stage_participations')
    seed = models.IntegerField(null=True, blank=True)
    status = models.CharField(
        max_length=50,
        choices=ParticipantStatus.choices,
        default=ParticipantStatus.ACTIVE
    )

class MatchSeat(models.Model):
    series = models.ForeignKey(MatchSeries, on_delete=models.CASCADE)
    stage_participant = models.ForeignKey(StageParticipant, on_delete=models.CASCADE)
    seat_number = models.IntegerField(null=True, blank=True)
