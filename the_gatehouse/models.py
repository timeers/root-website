import os
import uuid
import calendar
import secrets
import hashlib

from urllib.parse import urlparse
from django.contrib.auth.models import User
from django.urls import reverse
from django.db import models
from PIL import Image
from django.db.models import Count, F, ExpressionWrapper, FloatField, Q, Case, When, Value
from django.db.models.functions import Cast
from django.apps import apps
from django.utils import timezone 
from django.core.exceptions import ValidationError
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy
from the_keep.utils import validate_hex_color, delete_old_image
from the_keep.services.upload_paths import avatar_upload_path, changelog_image_upload_path

# Component types eligible for the per-component Discord notification groups
# ("A new <X> is published" / "A <X> is marked Stable"). Each value equals the
# Post.component string on a saved post; a profile opts in by adding the value
# to its stable_notify / new_notify JSON list. Single source of truth for the
# model, the DiscordNotificationsForm, and the notifyservice broadcast lookups.
NOTIFY_COMPONENTS = [
    ("Faction", gettext_lazy("Faction")),
    ("Map", gettext_lazy("Map")),
    ("Deck", gettext_lazy("Deck")),
    ("Vagabond", gettext_lazy("Vagabond")),
    ("Hireling", gettext_lazy("Hireling")),
    ("Landmark", gettext_lazy("Landmark")),
    ("Clockwork", gettext_lazy("Clockwork")),
    ("Tweak", gettext_lazy("House Rule")),
]

class MessageChoices(models.TextChoices):
    DANGER = 'danger'
    WARNING = 'warning'
    SUCCESS = 'success'
    INFO = 'info'


class Language(models.Model):
    code = models.CharField(max_length=10, unique=True)  # 'en', 'fr', etc.
    name = models.CharField(max_length=50)
    
    LOCALE_MAP = {
        "en": "en-US",  # English (United States)
        "fr": "fr-FR",  # French (France)
        "es": "es-ES",  # Spanish (Spain)
        "nl": "nl-NL",  # Dutch (Netherlands)
        "pl": "pl-PL",  # Polish (Poland)
        "ru": "ru-RU",  # Russian (Russia)
        "de": "de-DE",  # German (Germany)

        # Future possible languages
        "pt": "pt-BR",  # Portuguese (Brazil)
        "it": "it-IT",  # Italian (Italy)
        "ja": "ja-JP",  # Japanese (Japan)
        "zh-hans": "zh-CN",    # Chinese Simplified (China)
        "zh-hant": "zh-TW",    # Chinese Traditional (Taiwan)
        "ko": "ko-KR",  # Korean (South Korea)
        "tr": "tr-TR",  # Turkish (Turkey)
    }

    class Meta:
        ordering = ['id']

    def __str__(self):
        return self.name
    
    @property
    def locale(self):
        # Return mapped locale or fallback to just the code itself
        return self.LOCALE_MAP.get(self.code, self.code)




ALLOWED_DISCORD_DOMAINS = {
    "discord.gg",
    "www.discord.gg",
    "discord.com",
    "www.discord.com",
}

def validate_discord_invite(url):
    parsed = urlparse(url)

    if parsed.scheme not in ("http", "https"):
        raise ValidationError("Invite must be a valid http or https URL.")

    if parsed.netloc not in ALLOWED_DISCORD_DOMAINS:
        raise ValidationError("Invite must be a Discord invite URL.")

    # Enforce invite path formats
    if not (
        parsed.path.startswith("/invite/")
        or parsed.netloc.endswith("discord.gg")
    ):
        raise ValidationError("Invite must be a valid Discord invite link.")


class DiscordGuild(models.Model):
    guild_id = models.CharField(max_length=32, unique=True)
    name = models.CharField(max_length=100)
    server_invite = models.URLField(
        max_length=1200, 
        null=True, 
        blank=True,
        validators=[validate_discord_invite],
        help_text="Discord server invite URL")
    request_message = models.TextField(blank=True, null=True)
    server_rules = models.TextField(blank=True, null=True)
    approval_message = models.TextField(blank=True, null=True)

    auto_approve_invite = models.BooleanField(default=False)

    # Whether OUR bot is a member of this guild. Maintained by the
    # sync_bot_guilds command/task. The bot can only DM users who share
    # a guild with it, so this gates DM reachability.
    bot_member = models.BooleanField(default=False, help_text="Whether the bot is a member of this guild.")

    # From Discord API
    actual_name = models.CharField(max_length=100, null=True, blank=True)
    description = models.TextField(blank=True, null=True, help_text="Server description from Discord")
    icon_hash = models.CharField(max_length=200, blank=True, null=True, help_text="Discord server icon hash")
    banner_hash = models.CharField(max_length=200, blank=True, null=True, help_text="Discord server banner hash")
    member_count = models.IntegerField(default=0, help_text="Approximate member count")
    online_count = models.IntegerField(default=0, help_text="Approximate online count")
    last_updated = models.DateTimeField(auto_now=True, help_text="Last time Discord info was refreshed")
    
    

    def __str__(self):
        return self.name

    def guild_name(self):
        if self.actual_name:
            return self.actual_name
        return self.name

    def get_invite_code(self):
        """Extract invite code from server_invite URL"""
        if self.server_invite:
            return self.server_invite.split('/')[-1].split('?')[0]  # Handle query params
        return None
    
    def get_icon_url(self):
        """Get full Discord icon URL"""
        if self.guild_id and self.icon_hash:
            return f"https://cdn.discordapp.com/icons/{self.guild_id}/{self.icon_hash}.png?size=256"
        return None
    
    def get_banner_url(self):
        """Get full Discord banner URL"""
        if self.guild_id and self.banner_hash:
            return f"https://cdn.discordapp.com/banners/{self.guild_id}/{self.banner_hash}.png?size=512"
        return None

    def get_discord_url(self):
        """Deep link that opens this server in Discord for a logged-in member
        (as opposed to server_invite, which is the join flow)."""
        if self.guild_id:
            return f"https://discord.com/channels/{self.guild_id}"
        return None

class Holiday(models.Model):
    name = models.CharField(max_length=100)
    start_date = models.DateField(default=timezone.now)
    end_date = models.DateField(default=timezone.now)
    start_day_of_year = models.PositiveSmallIntegerField(default=1)
    end_day_of_year = models.PositiveSmallIntegerField(default=1)
    date_modified = models.DateTimeField(auto_now=True)

    def save(self, *args, **kwargs):
        # Set the day of the year fields based on the datetime fields
        self.start_day_of_year = self.start_date.timetuple().tm_yday
        self.end_day_of_year = self.end_date.timetuple().tm_yday
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name
    
    class Meta:
        ordering = ['start_date', 'name', 'id']

class Theme(models.Model):
    name = models.CharField(max_length=100)
    theme_artists = models.ManyToManyField('Profile', blank=True, related_name='theme_artwork')
    theme_color = models.CharField(
        max_length=7,
        default='#5f788a',
        validators=[validate_hex_color],
        help_text="Enter a hex color code (e.g., #RRGGBB)."
    )
    background_color = models.CharField(
        max_length=7,
        default='#fafafa',
        validators=[validate_hex_color],
        help_text="Enter a hex color code (e.g., #RRGGBB)."
    )
    holiday = models.ForeignKey(Holiday, blank=True, null=True, on_delete=models.SET_NULL)
    public = models.BooleanField(default=False)
    active = models.BooleanField(default=True)
    backup_theme = models.ForeignKey('self', on_delete=models.SET_NULL, null=True, blank=True)
    date_modified = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name


    def get_artists(self, visited=None):
        """
        Collect all artists associated with this theme, its backgrounds,
        foregrounds, and directly assigned theme_artists.
        Recursively includes artists from any backup_theme, 
        avoiding infinite loops via a visited set.
        """
        if visited is None:
            visited = set()

        # Prevent circular reference recursion
        if self.id in visited:
            return []

        visited.add(self.id)
        artists = set()

        # Background artists
        for image in self.backgrounds.all():
            if image.artist:
                artists.add(image.artist)

        # Foreground artists
        for image in self.foregrounds.all():
            if image.artist:
                artists.add(image.artist)

        # Theme-specific artists
        for artist in self.theme_artists.all():
            artists.add(artist)

        # Recursively include artists from backup theme (if any)
        if self.backup_theme:
            artists.update(self.backup_theme.get_artists(visited=visited))

        return list(artists)




    class Meta:
        ordering = ['name', 'id']

class PageChoices(models.TextChoices):
    LIBRARY = 'library','Library'
    GAMES = 'games', 'Games'
    RESOURCES = 'resources', 'Resources'
    FEEDBACK = 'feedback', 'Feedback'
    ABOUT = 'about', 'About'
    SETTINGS = 'settings', 'Settings'
    LAWS = 'laws', 'Laws'
    FAQ = 'faq', 'FAQ'
    SURVEYS = 'surveys', 'Surveys'
    SERIES = 'series', 'Series'


class BackgroundImage(models.Model):
    name = models.CharField(max_length=100)    
    # artist = models.CharField(max_length=100, blank=True, null=True)
    artist = models.ForeignKey('Profile', on_delete=models.SET_NULL, null=True, blank=True)
    image = models.ImageField(upload_to='background_images')
    pattern = models.ImageField(upload_to='background_patterns', null=True, blank=True)
    theme = models.ForeignKey(Theme, on_delete=models.CASCADE, related_name='backgrounds')
    page = models.CharField(max_length=15 , default=PageChoices.LIBRARY, choices=PageChoices.choices)
    background_color = models.CharField(
        max_length=7,
        blank=True,
        null=True,
        validators=[validate_hex_color],
        help_text="Enter a hex color code (e.g., #RRGGBB)."
    )
    small_image = models.ImageField(upload_to='background_images/small', null=True, blank=True)
    date_modified = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name
    



    def alt(self):
        if self.artist:
            alt = f'{ self.name } by {self.artist.name}'
        else:
            alt = f'{ self.name }'
        return alt

        
    def save(self, *args, **kwargs):
        
        
        # Check if the instance already exists (i.e., is not a new object)
        if self.pk:
            try:
                field_name = 'image'
                old_instance = BackgroundImage.objects.get(pk=self.pk)
                old_image = getattr(old_instance, field_name)
                new_image = getattr(self, field_name)
                
                # If the image has changed, delete the old one(s)
                if old_image and old_image != new_image:
                    delete_old_image(old_image)
                    delete_old_image(getattr(old_instance, 'small_image'))
            except BackgroundImage.DoesNotExist:
                # The object does not exist yet, nothing to delete
                pass
            try:
                field_name = 'pattern'
                old_instance = BackgroundImage.objects.get(pk=self.pk)
                old_image = getattr(old_instance, field_name)
                new_image = getattr(self, field_name)
                
                # If the image has changed, delete the old one(s)
                if old_image and old_image != new_image:
                    delete_old_image(old_image)

            except BackgroundImage.DoesNotExist:
                # The object does not exist yet, nothing to delete
                pass

        super().save(*args, **kwargs)


class ForegroundImage(models.Model):
    class LocationChoices(models.IntegerChoices):
        FAR_LEFT = 1, 'Far Left'
        LEFT = 3, 'Left'
        CENTER = 5, 'Center'
        RIGHT = 7, 'Right'
        FAR_RIGHT = 9, 'Far Right'
        TITLE = 100, 'Title'
        SECOND = 101, 'Second Title'
        THIRD = 102, 'Third Title'
    name = models.CharField(max_length=100)
    # artist = models.CharField(max_length=100, blank=True, null=True)
    artist = models.ForeignKey('Profile', on_delete=models.SET_NULL, null=True, blank=True)
    location = models.IntegerField(default=LocationChoices.CENTER, choices=LocationChoices.choices)
    image = models.ImageField(upload_to='foreground_images')
    
    theme = models.ForeignKey(Theme, on_delete=models.CASCADE, related_name='foregrounds')
    page = models.CharField(max_length=15 , default=PageChoices.LIBRARY, choices=PageChoices.choices)
    depth = models.IntegerField(default=-1)
    start_position = models.TextField(default='0vw')
    slide = models.TextField(default='0vw')
    speed = models.TextField(default='50vh')
    small_image = models.ImageField(upload_to='foreground_images/small', null=True, blank=True)
    date_modified = models.DateTimeField(auto_now=True)

    def style(self):
        return f'--offset-percent: { self.slide }; --slide-speed: { self.speed }; --z-depth: { self.depth }; --start-position: { self.start_position };'
    
    def __str__(self):
        return self.name


    def alt(self):
        if self.artist:
            alt = f'{ self.name } by {self.artist.name}'
        else:
            alt = f'{ self.name }'
        return alt
        
    def save(self, *args, **kwargs):
        field_name = 'image'
        
        # Check if the instance already exists (i.e., is not a new object)
        if self.pk:
            try:
                old_instance = ForegroundImage.objects.get(pk=self.pk)
                old_image = getattr(old_instance, field_name)
                new_image = getattr(self, field_name)
                
                # If the image has changed, delete the old one(s)
                if old_image and old_image != new_image:
                    delete_old_image(old_image)
                    delete_old_image(getattr(old_instance, 'small_image'))
            except ForegroundImage.DoesNotExist:
                # The object does not exist yet, nothing to delete
                pass

        super().save(*args, **kwargs)



class Profile(models.Model):
    class GroupChoices(models.TextChoices):
        OUTCAST = 'O'
        PLAYER = 'P'
        EDITOR = 'E'
        DESIGNER = 'D'
        ADMIN = 'A'
        BANNED = 'B'
    class StatusChoices(models.TextChoices):
        STABLE = '1','Stable'
        TESTING = '2', 'Testing'
        DEVELOPMENT = '3', 'Development'
        INACTIVE = '4', 'Inactive'
        ABANDONED = '5', 'Abandoned'

    component = 'Profile'

    user = models.OneToOneField(User, on_delete=models.SET_NULL, null=True, blank=True)
    # theme = models.CharField(max_length=20 , default=Theme.LIGHT, choices=Theme.choices, null=True, blank=True)
    theme = models.ForeignKey(Theme, on_delete=models.SET_NULL, null=True, blank=True)
    image = models.ImageField(default='default_images/default_user.png', upload_to=avatar_upload_path)
    last_avatar_sync = models.DateTimeField(null=True, blank=True)

    dwd = models.CharField(max_length=100, unique=True, blank=True, null=True)
    discord = models.CharField(max_length=100, unique=True, blank=True, null=True)
    league = models.BooleanField(default=False)
    group = models.CharField(max_length=1, choices=GroupChoices.choices, default=GroupChoices.OUTCAST)
    tester = models.BooleanField(default=False)
    weird = models.BooleanField(default=True)
    in_weird_root = models.BooleanField(default=False)
    in_woodland_warriors = models.BooleanField(default=False)
    in_french_root = models.BooleanField(default=False)
    view_status = models.CharField(max_length=15 , default=StatusChoices.INACTIVE, choices=StatusChoices.choices)
    language = models.ForeignKey(Language, on_delete=models.SET_NULL, null=True, blank=True)

    display_name = models.CharField(max_length=100, null=True, blank=True)
    slug = models.SlugField(unique=True, null=True, blank=True)
    bookmarks = models.ManyToManyField('self', through='PlayerBookmark')
    player_onboard = models.BooleanField(default=False)
    editor_onboard = models.BooleanField(default=False)
    designer_onboard = models.BooleanField(default=False)
    admin_onboard = models.BooleanField(default=False)
    forge_onboard = models.BooleanField(default=False)
    trusted_tournament_host = models.BooleanField(default=False)
    admin_nominated = models.ForeignKey('self', on_delete=models.SET_NULL, null=True, blank=True, related_name='nominated_by')
    admin_dismiss = models.ForeignKey('self', on_delete=models.SET_NULL, null=True, blank=True, related_name='dismissed_by')
    credit_link = models.CharField(max_length=400, null=True, blank=True, help_text="User's external link to their other endeavors.")
    date_modified = models.DateTimeField(auto_now=True)
    guilds = models.ManyToManyField(DiscordGuild, related_name="members", help_text="User's known Root Guilds.", blank=True)
    # Discord DM notification preferences (all opt-in). The bot can only DM
    # users who share a guild with it; see Profile.can_receive_dms.
    notify_survey_response = models.BooleanField(default=False)
    notify_game_recorded = models.BooleanField(default=False)
    notify_tournament_game_recorded = models.BooleanField(default=False)
    notify_post_game_recorded = models.BooleanField(default=False)
    notify_post_approved = models.BooleanField(default=False)
    # Per-component broadcast opt-ins: each holds a list of Post.component values
    # (see NOTIFY_COMPONENTS) the user wants a DM for. stable_notify -> a component
    # of that type is marked Stable; new_notify -> a new component of that type is
    # published. Empty list = opted out of the whole group.
    stable_notify = models.JSONField(default=list, blank=True)
    new_notify = models.JSONField(default=list, blank=True)
    discord_id = models.CharField(max_length=32, blank=True, null=True, unique=True, help_text="User's Discord ID number.")
    # Cached leaderboard inputs (coalition formula), maintained by
    # calculate_and_cache_winrate via Effort/Game signals. Let the default
    # /leaderboard/ board be a plain indexed query with no aggregation.
    cached_winrate = models.FloatField(null=True, blank=True, db_index=True)
    cached_plays = models.IntegerField(null=True, blank=True, db_index=True)
    cached_tourney_points = models.FloatField(null=True, blank=True)
    # Only a hash of the API key is stored, never the key itself (like GitHub/Discord
    # tokens). The raw key is shown once at generation and is not retrievable afterwards;
    # a DB leak therefore does not expose usable keys. API keys are high-entropy random
    # tokens, so a fast SHA-256 is sufficient (unlike low-entropy passwords).
    api_key_hash = models.CharField(max_length=64, unique=True, null=True, blank=True, db_index=True, help_text="SHA-256 hash of the user's game data API key.")
    api_key_created = models.DateTimeField(null=True, blank=True, help_text="When the current API key was generated.")

    @staticmethod
    def hash_api_key(raw_key):
        """Return the SHA-256 hex digest used to store/look up an API key."""
        return hashlib.sha256(raw_key.encode()).hexdigest()

    def generate_api_key(self):
        """Generate (or regenerate) this profile's API key.

        Stores only the hash and returns the raw key. The raw value is returned exactly
        once here and cannot be recovered later; callers must surface it to the user
        immediately.
        """
        raw_key = secrets.token_urlsafe(32)
        self.api_key_hash = self.hash_api_key(raw_key)
        self.api_key_created = timezone.now()
        self.save(update_fields=['api_key_hash', 'api_key_created'])
        return raw_key

    @property
    def name(self):
        if self.display_name:
            name = self.display_name
        elif self.discord:
            name = self.discord
        else:
            name = "Anonymous"
            # name = self.user.username
        return name
    
    @property
    def active_posts(self):
        return self.posts.filter(status__lte=4).count()

    def __str__(self):
        if self.name.lower() == self.discord:
            return self.name
        else:
            return f'{self.name} ({self.discord})'

    @property
    def can_receive_dms(self):
        """
        True if the bot shares a guild with this user, which is Discord's
        requirement for the bot to be able to DM them. Relies on the
        bot_member flag kept current by sync_bot_guilds.
        """
        return self.guilds.filter(bot_member=True).exists()

    def save(self, *args, **kwargs):
        # Check for blank display names
        if not self.display_name: 
            self.display_name = self.discord # set to discord if blank


        field_name = 'image'
        
        # Check if the instance already exists (i.e., is not a new object)
        if self.pk:
            try:
                old_instance = Profile.objects.get(pk=self.pk)
                old_image = getattr(old_instance, field_name)
                new_image = getattr(self, field_name)
                
                # If the image has changed, delete the old one(s)
                if old_image and old_image != new_image:
                    delete_old_image(old_image)
            except Profile.DoesNotExist:
                # The object does not exist yet, nothing to delete
                pass

        super().save(*args, **kwargs)
        self._resize_image()

    def _resize_image(self):
        """Helper method to resize the image if necessary."""
        try:
            # Check if the image exists and is a valid file
            if self.image and os.path.exists(self.image.path):
                img = Image.open(self.image.path)

                max_size = 125
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
                    img.save(self.image.path)
                    # print(f'Resized image saved at: {self.image.path}')
                # else:
                    # print(f'Original image saved at: {self.image.path}')
        except Exception as e:
            print(f"Error resizing image: {e}")

    #     img = Image.open(self.image.path)

    #     if img.height > 300 or img.width > 300:
    #         output_size = (300, 300)
    #         img.thumbnail(output_size)
    #         img.save(self.image.path)

    @property
    def outcast(self):
        group = self.group
        if group == "O":
            return True
        else:
            return False

    @property
    def banned(self):
        group = self.group
        if group == "B":
            return True
        else:
            return False

    @property
    def admin(self):
        group = self.group
        if group == "A":
            return True
        else:
            return False

    @property
    def designer(self):
        group = self.group
        if group == "A" or group == "D":
            return True
        else:
            return False

    @property
    def editor(self):
        group = self.group
        if group == "A" or group == "D" or group == "E":
            return True
        else:
            return False

    @property
    def player(self):
        group = self.group
        if group == "A" or group == "D" or group == "E" or group == "P":
            return True
        else:
            return False
        

    def winrate(self, faction = None, deck = None, tournament = None, round = None):
        # Coalition-based leaderboard formula (coalition wins count as half),
        # the site's single source of truth. See filtered_winrate().
        from the_warroom.models import filtered_winrate
        return filtered_winrate(
            player=self, faction=faction, deck=deck, tournament=tournament
        )['win_rate']
    
    def get_games_queryset(self, faction=None):
        # Get the model for Game
        Game = apps.get_model('the_warroom', 'Game')
        
        # Start with the Effort queryset
        efforts = self.efforts.all()
        
        # Apply the faction filter if provided
        if faction:
            efforts = efforts.filter(faction=faction)

        # Filter for distinct games linked to these efforts
        games = Game.objects.filter(
            id__in=efforts.values_list('game', flat=True),
            final=True
        ).distinct().order_by('-date_posted')

        return games


    def games_played(self, faction=None):
        Game = apps.get_model('the_warroom', 'Game')
        # Access the related Effort objects for this player
        efforts = self.efforts.all()
        
        # Apply the faction filter if provided
        if faction:
            efforts = efforts.filter(faction=faction)
        
        # Count the distinct games linked to the efforts
        distinct_game_count = Game.objects.filter(
            id__in=efforts.values_list('game', flat=True),
            final=True
        ).distinct().count()

        return distinct_game_count
    
    def games_won(self, faction=None):
        Game = apps.get_model('the_warroom', 'Game')
        # Access the related Effort objects for this player
        efforts = self.efforts.all()
        
        # Apply the faction filter if provided
        if faction:
            efforts = efforts.filter(faction=faction, win=True)
        
        # Count the distinct games linked to the efforts
        distinct_game_count = Game.objects.filter(
            id__in=efforts.values_list('game', flat=True),
            final=True
        ).distinct().count()

        return distinct_game_count



    def get_absolute_url(self):
        return reverse('player-detail', kwargs={'slug': self.slug})
    

    def most_used_faction(self):
        from the_keep.models import Faction
        
        most_used = (
            self.efforts.values('faction')
            .annotate(faction_count=Count('faction'))
            .order_by('-faction_count')
        ).first()  # Get the top result

        if most_used:
            faction_id = most_used['faction']
            try:
                # Fetch and return the faction instance
                return Faction.objects.get(pk=faction_id)
            except Faction.DoesNotExist:
                return None  # Handle case where faction doesn't exist
        return None  # Handle case where there are no efforts

    def most_successful_faction(self):
        # Lazy import to avoid circular imports
        from the_keep.models import Faction

        # Aggregate wins by faction
        wins_by_faction = (
            self.efforts.filter(win=True, game__test_match=False, game__final=True)
            .values('faction')  # Assuming 'faction' is the field name
            .annotate(win_count=Count('id'))  # Count wins
            .order_by('-win_count')  # Order by count descending
        )

        # Get the faction with the most wins
        most_successful = wins_by_faction.first()

        if most_successful:
            # Return the corresponding Faction object
            return Faction.objects.get(id=most_successful['faction'])

        return None  # No wins found
    
    class Meta:
        ordering = ['display_name']

    @classmethod
    def top_players(cls, faction_id=None, top_quantity=False, tournament=None, round=None, limit=5, game_threshold=10):
        """
        Get the top players based on their win rate (default) or total efforts.
        If faction_id is provided, get the top players for that faction.
        Otherwise, get the top players across all factions.
        The `limit` parameter controls how many players to return.
        """
        from the_warroom.models import (effort_counts_for_round_q,
                                         effort_counts_for_tournament_q)

        # Start with the base queryset for players
        queryset = cls.objects.filter(efforts__game__final=True, efforts__game__test_match=False)

        # If a tournament is provided, filter efforts that are related to that tournament
        # (via the game's primary round OR an extra round it counts toward)
        if tournament:
            queryset = queryset.filter(
                effort_counts_for_tournament_q(tournament, prefix='efforts__game')
            ).distinct()

        # If a round is provided, filter efforts that are related to that round
        if round:
            queryset = queryset.filter(
                effort_counts_for_round_q(round, prefix='efforts__game')
            ).distinct()
        # Now, annotate with the total efforts and win counts
        queryset = queryset.annotate(
            total_efforts=Count('efforts', filter=Q(efforts__faction_id=faction_id) if faction_id else Q()),
            win_count=Count('efforts', filter=Q(efforts__win=True, efforts__faction_id=faction_id) if faction_id else Q(efforts__win=True)),
            coalition_count=Count('efforts', filter=Q(efforts__win=True, efforts__game__coalition_win=True, efforts__faction_id=faction_id) if faction_id else Q(efforts__win=True, efforts__game__coalition_win=True))
        )
        
        # Filter players who have enough efforts (before doing the annotation)

        queryset = queryset.filter(total_efforts__gte=game_threshold)

        # Annotate with win_rate after filtering
        queryset = queryset.annotate(
            win_rate=Case(
                When(total_efforts=0, then=Value(0)),
                default=ExpressionWrapper(
                    (Cast(F('win_count'), FloatField()) - (Cast(F('coalition_count'), FloatField()) / 2 )) / Cast(F('total_efforts'), FloatField()) * 100,  # Win rate as percentage
                    output_field=FloatField()
                ),
                output_field=FloatField()
            ),
            tourney_points=Case(
                When(total_efforts=0, then=Value(0)),
                default=ExpressionWrapper(
                    Cast(F('win_count'), FloatField()) - (Cast(F('coalition_count'), FloatField()) / 2 ),  # Tourney Points
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



    @classmethod
    def leaderboard(cls, effort_qs, top_quantity=False, limit=5, game_threshold=10, as_json=False, link_builder=None):
        """
        Get the players with the highest winrate (or most wins for top_quantity) from the effort_qs
        The limit is how many players will be displayed.
        The game theshold is how many games a player needs to play to qualify.
        link_builder: optional callable(profile) -> str URL. Defaults to player-detail.
        """
        # Start with the base queryset for profiles
        queryset = cls.objects.filter(efforts__in=effort_qs)

        # Now, annotate with the total efforts and win counts
        queryset = queryset.annotate(
            total_efforts=Count('efforts', filter=Q(efforts__game__final=True, efforts__game__test_match=False)),
            win_count=Count('efforts', filter=Q(efforts__win=True, efforts__game__final=True, efforts__game__test_match=False)),
            coalition_count=Count('efforts', filter=Q(efforts__win=True, efforts__game__coalition_win=True, efforts__game__final=True, efforts__game__test_match=False))
        )
        
        # Filter players who have enough efforts (before doing the annotation)
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

        # Order the queryset
        if top_quantity:
            queryset = queryset.order_by('-tourney_points', '-win_rate')
        else:
            queryset = queryset.order_by('-win_rate', '-total_efforts')

        queryset = queryset[:limit]

        # Materialize queryset and set leaderboard_link on each profile
        results = list(queryset)
        for profile in results:
            if link_builder:
                profile.leaderboard_link = link_builder(profile)
            else:
                profile.leaderboard_link = reverse('player-detail', kwargs={'slug': profile.slug})

        # Return as JSON if requested
        if as_json:
            return [
                {
                    'title': profile.display_name or profile.discord,
                    'win_rate': round(profile.win_rate, 2),
                    'tourney_points': round(profile.tourney_points, 2),
                    'total_efforts': profile.total_efforts,
                    'url': profile.get_absolute_url(),
                    'slug': profile.slug,
                    'image_url': profile.image.url if profile.image else None,
                }
                for profile in results
            ]

        return results




class PlayerBookmark(models.Model):
    player = models.ForeignKey(Profile, on_delete=models.CASCADE)
    friend = models.ForeignKey(Profile, on_delete=models.CASCADE, related_name='followers')
    public = models.BooleanField(default=False)
    date_posted = models.DateTimeField(default=timezone.now)
    def __str__(self):
        return f"{self.player.name} > {self.friend.name}"


def get_first_theme():
    # This will return the first Theme object, or None if no Theme objects exist
    return Theme.objects.first()

class Website(models.Model):
    site_title = models.CharField(max_length=255, default="Root Database")
    default_theme = models.ForeignKey(Theme, on_delete=models.SET_NULL, null=True, blank=True)
    game_threshold = models.IntegerField(default=10)
    player_threshold = models.IntegerField(default=5)
    global_message = models.CharField(max_length=400, null=True, blank=True)
    message_type = models.CharField(max_length=15 , default=MessageChoices.INFO, choices=MessageChoices.choices)
    woodland_warriors_invite = models.CharField(max_length=100, null=True, blank=True)
    french_root_invite = models.CharField(max_length=100, null=True, blank=True)
    weird_root_invite = models.CharField(max_length=100, null=True, blank=True)
    rdb_feedback_invite = models.CharField(max_length=100, null=True, blank=True)
    date_modified = models.DateTimeField(auto_now=True)
    last_law_check = models.DateTimeField(null=True, blank=True)
    primary_discord_guild = models.ForeignKey(DiscordGuild, on_delete=models.SET_NULL, null=True, blank=True)

    @classmethod
    def get_singular_instance(cls):
        # This will return the first instance or create one if none exists
        obj, created = cls.objects.get_or_create(pk=1)  # You could use any constant key, like '1'
        return obj
    
    def __str__(self):
        return "Website Configuration"
    
class DailyUserVisit(models.Model):
    profile = models.ForeignKey(Profile, on_delete=models.CASCADE)
    date = models.DateField(default=timezone.localdate)

    class Meta:
        unique_together = ('profile', 'date')
        verbose_name = 'Daily User Visit'
        verbose_name_plural = 'Daily User Visits'

    def __str__(self):
        return f"{self.profile.discord} - {self.date}"
    


class Changelog(models.Model):
    version = models.CharField(max_length=50, unique=True) 
    title = models.CharField(max_length=200, blank=True)
    date = models.DateField(default=timezone.now)
    description = models.TextField(blank=True)
    image = models.ImageField(upload_to=changelog_image_upload_path, blank=True, null=True)
    source_hash = models.CharField(max_length=65, blank=True, editable=False)
    slug = models.SlugField(unique=True, null=True, blank=True)

    class Meta:
        ordering = ['-date', 'id']
    
    def __str__(self):
        return f"{self.version} - {self.date}"

    def save(self, *args, **kwargs):
        field_name = 'image'
        
        # Check if the instance already exists (i.e., is not a new object)
        if self.pk:
            try:
                old_instance = Changelog.objects.get(pk=self.pk)
                old_image = getattr(old_instance, field_name)
                new_image = getattr(self, field_name)
                
                # If the image has changed, delete the old one(s)
                if old_image and old_image != new_image:
                    delete_old_image(old_image)
            except Changelog.DoesNotExist:
                # The object does not exist yet, nothing to delete
                pass

        super().save(*args, **kwargs)


class ChangelogEntry(models.Model):
    CATEGORY_CHOICES = [
        ('feature', 'New Feature'),
        ('improvement', 'Improvement'),
        ('bugfix', 'Bug Fix'),
        ('breaking', 'Breaking Change'),
        ('issues', 'Known Issue'),
    ]

    CATEGORY_ORDER = {
        'feature': 0,
        'improvement': 1,
        'bugfix': 2,
        'breaking': 3,
        'issues': 4,
    }

    changelog = models.ForeignKey(Changelog, related_name='entries', on_delete=models.CASCADE)
    category = models.CharField(max_length=20, choices=CATEGORY_CHOICES)
    category_order = models.PositiveSmallIntegerField(editable=False)
    description = models.TextField()
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['category_order', 'order', 'id']

    
    def __str__(self):
        return f"{self.get_category_display()}: {self.description[:50]}"
    
    def save(self, *args, **kwargs):
        self.category_order = self.CATEGORY_ORDER.get(self.category, 99)
        super().save(*args, **kwargs)


class DiscordGuildJoinRequest(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        WITHDRAWN = "withdrawn", "Withdrawn"
        COMPLETED = "completed", "Completed"

    profile = models.ForeignKey(
        Profile,
        on_delete=models.CASCADE,
        related_name="guild_join_requests",
    )
    guild = models.ForeignKey(
        DiscordGuild,
        on_delete=models.CASCADE,
        related_name="join_requests",
    )

    request_message = models.TextField()
    agreement_message = models.TextField()
    acknowledgement = models.BooleanField(default=False)
    

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
    )

    moderator_message = models.TextField(
        blank=True,
        null=True,
        help_text="Optional message shown to the user when approved/rejected"
    )
    moderator_note = models.TextField(
        blank=True,
        null=True,
        help_text="Internal note visible only to moderators"
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("profile", "guild")
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.profile} → {self.guild} ({self.status})"


    def clean(self):
        if not self.acknowledgement:
            raise ValidationError("You must acknowledge the server rules.")
        
    def approve(self):
        if self.status != self.Status.PENDING:
            raise ValueError("Only pending requests can be approved.")
        self.status = self.Status.APPROVED
        self.save(update_fields=["status"])

    def reject(self):
        if self.status != self.Status.PENDING:
            raise ValueError("Only pending requests can be rejected.")
        self.status = self.Status.REJECTED
        self.save(update_fields=["status"])

    def complete(self):
        if self.status != self.Status.APPROVED:
            raise ValueError("Only approved requests can be completed.")
        self.status = self.Status.COMPLETED
        self.save(update_fields=["status"])



class UserNotification(models.Model):
    """
    Persistent notification system for users.
    Stores dismissible notifications that appear in the message bar.
    """
    profile = models.ForeignKey(Profile, on_delete=models.CASCADE, related_name='notifications')
    sender = models.ForeignKey(Profile, on_delete=models.SET_NULL, null=True, blank=True, related_name='sent_notifications')
    message = models.TextField()
    message_type = models.CharField(
        max_length=20,
        choices=MessageChoices.choices,
        default=MessageChoices.INFO
    )
    created_at = models.DateTimeField(auto_now_add=True)
    is_dismissed = models.BooleanField(default=False)
    dismissed_at = models.DateTimeField(null=True, blank=True)

    # Optional: Link to related object
    related_post_id = models.IntegerField(null=True, blank=True, help_text="ID of related Post if applicable")
    related_url = models.CharField(max_length=500, null=True, blank=True, help_text="URL to navigate to")

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['profile', 'is_dismissed']),
        ]

    def __str__(self):
        return f"Notification for {self.profile.name}: {self.message[:50]}"

    def dismiss(self):
        """Mark notification as dismissed"""
        self.is_dismissed = True
        self.dismissed_at = timezone.now()
        self.save()

    @classmethod
    def create_notification(cls, profile, message, message_type=MessageChoices.INFO, related_post=None, related_url=None, sender=None):
        """
        Helper method to create a notification.

        Args:
            profile: Profile object
            message: Notification message text
            message_type: Type of message (success, warning, danger, info)
            related_post: Optional Post object to link to
            related_url: Optional URL to link to
            sender: Optional Profile of the admin/user sending the notification
        """
        notification = cls.objects.create(
            profile=profile,
            sender=sender,
            message=message,
            message_type=message_type,
            related_post_id=related_post.id if related_post else None,
            related_url=related_url
        )
        return notification


# Stub function for old migrations that reference survey models
# Survey models have been moved to the_tavern app
def get_default_ta_days():
    """Returns default enabled days for TIME_AVAILABILITY questions (all 7 days)"""
    return ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday']