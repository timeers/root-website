import os
import uuid
import calendar

from urllib.parse import urlparse
from django.contrib.auth.models import User
from io import BytesIO
# from the_warroom.models import Effort
from django.core.validators import MaxValueValidator, MinValueValidator
from django.urls import reverse
from django.db.models.signals import pre_save, post_save
from django.dispatch import receiver
from django.db import models
from PIL import Image
from django.db.models import Count, F, ExpressionWrapper, FloatField, Q, Case, When, Value
from django.db.models.functions import Cast
from django.apps import apps
from django.utils import timezone 
from django.core.exceptions import ValidationError
from django.utils.translation import gettext as _
from the_keep.utils import validate_hex_color, delete_old_image
from the_keep.services.upload_paths import avatar_upload_path, changelog_image_upload_path

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
    auto_approve_invite = models.BooleanField(default=False)

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
    admin_nominated = models.ForeignKey('self', on_delete=models.SET_NULL, null=True, blank=True, related_name='nominated_by')
    admin_dismiss = models.ForeignKey('self', on_delete=models.SET_NULL, null=True, blank=True, related_name='dismissed_by')
    credit_link = models.CharField(max_length=400, null=True, blank=True, help_text="User's external link to their other endeavors.")
    date_modified = models.DateTimeField(auto_now=True)
    guilds = models.ManyToManyField(DiscordGuild, related_name="members", help_text="User's known Root Guilds.", blank=True)
    discord_id = models.CharField(max_length=32, blank=True, null=True, unique=True, help_text="User's Discord ID number.")

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
        # efforts = self.efforts.all()  # Access the related Effort objects for this player
        efforts = self.efforts.filter(game__test_match=False, game__final=True)

        if faction:
            efforts = efforts.filter(faction=faction)

        if deck:
            efforts = efforts.filter(game__deck=deck)

        all_games = efforts.count()
        wins = efforts.filter(win=True)
        points = 0
        for effort in wins:
            points += (1 / effort.game.get_winners().count())        
        if all_games > 0:
            return points / all_games * 100  # Calculate winrate
        return 0
    
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
        # Start with the base queryset for players
        queryset = cls.objects.filter(efforts__game__final=True, efforts__game__test_match=False)

        # If a tournament is provided, filter efforts that are related to that tournament
        if tournament:
            queryset = queryset.filter(
                efforts__game__round__tournament=tournament  # Filter efforts linked to a specific tournament
            )

        # If a round is provided, filter efforts that are related to that round
        if round:
            queryset = queryset.filter(
                efforts__game__round=round  # Filter efforts linked to a specific round
            )
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
    def leaderboard(cls, effort_qs, top_quantity=False, limit=5, game_threshold=10, as_json=False):
        """
        Get the players with the highest winrate (or most wins for top_quantity) from the effort_qs
        The limit is how many players will be displayed.
        The game theshold is how many games a player needs to play to qualify.
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
                for profile in queryset
            ]
                
        return queryset




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



# Surveys
class Survey(models.Model):
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    slug = models.SlugField(unique=True, max_length=250, null=True, blank=True)
    
    # Survey Owner Objects
    post = models.ForeignKey('the_keep.Post', on_delete=models.SET_NULL, null=True, blank=True, related_name='surveys', help_text="The Post that this survey is about.")
    series = models.ForeignKey('the_warroom.Tournament', on_delete=models.SET_NULL, null=True, blank=True, related_name='surveys', help_text="The Tournament or Series that this survey is about.")
    series_round = models.ForeignKey('the_warroom.Round', on_delete=models.SET_NULL, null=True, blank=True, related_name='surveys', help_text="The Tournament Round that this survey is about.")

    is_public = models.BooleanField(default=True, help_text="If False, only certain players can access.")    
    invited_players = models.ManyToManyField(Profile, blank=True, related_name='survey_invites', help_text="Players invited to take this survey.")
    guild = models.ForeignKey(DiscordGuild, on_delete=models.SET_NULL, null=True, blank=True, related_name='surveys', help_text="Players in this guild will be able to take this survey.")

    is_active = models.BooleanField(default=True, help_text="Whether this survey is currently accepting responses")
    created_at = models.DateTimeField(auto_now_add=True)
    
    start_date = models.DateTimeField(null=True, blank=True, help_text="When survey becomes available")
    end_date = models.DateTimeField(null=True, blank=True, help_text="When survey closes")
    
    allow_multiple_responses = models.BooleanField(default=False, help_text="Allow players to submit multiple times")
    allow_edit_responses = models.BooleanField(default=False, help_text="Allow players to edit their responses while survey is open")
    show_results_to_respondents = models.BooleanField(default=False, help_text="Allow respondents to view results")
    created_by = models.ForeignKey(Profile, on_delete=models.SET_NULL, null=True, blank=True, related_name='created_surveys')

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Survey'
        verbose_name_plural = 'Surveys'

    def __str__(self):
        return self.title

    def get_absolute_url(self):
        return reverse('survey-detail', kwargs={'slug': self.slug})

    def is_available(self):
        """Check if survey is currently available to take"""
        now = timezone.now()
        if not self.is_active:
            return False
        if self.start_date and now < self.start_date:
            return False
        if self.end_date and now > self.end_date:
            return False
        return True

    def question_count(self):
        """Get total number of questions"""
        return self.questions.count()

    def response_count(self):
        """Get total number of responses"""
        return self.responses.count()

    def has_user_responded(self, user_profile):
        """Check if a specific user has already responded"""
        if not user_profile:
            return False
        return self.responses.filter(user=user_profile).exists()

    def can_edit_response(self, user_profile):
        """Check if a user can edit their response"""
        if not user_profile or not self.allow_edit_responses:
            return False
        if not self.is_available():
            return False
        return self.has_user_responded(user_profile)

    def can_edit_survey(self, user_profile):
        """Check if a user can edit the survey"""
        if not user_profile:
            return False
        return user_profile.admin or user_profile == self.created_by


    def can_take_survey(self, user_profile):
        """Check if a user can take the survey"""
        if not user_profile:
            return False

        # Block banned users
        if user_profile.group == Profile.GroupChoices.BANNED:
            return False

        # Survey must be open
        if not self.is_available():
            return False

        # Respect multiple response rules
        if self.has_user_responded(user_profile) and not self.allow_multiple_responses:
            return False

        # Public survey
        if self.is_public:
            return True

        # Explicit invite
        if self.invited_players.filter(pk=user_profile.pk).exists():
            return True

        # Guild-based access
        if self.guild and user_profile.guilds.filter(pk=self.guild.pk).exists():
            return True

        return False

    def can_see_results(self, user_profile):
        if not user_profile:
            return False
        # Admin and creator can see results
        if user_profile.admin or user_profile == self.created_by:
            return True
        if self.has_user_responded(user_profile) and self.show_results_to_respondents:
            return True
        return False

class LikertScale(models.Model):
    name = models.CharField(max_length=100, help_text="Name for this scale (e.g., '5-point Agreement')")
    min_value = models.IntegerField(default=1, validators=[MinValueValidator(0)], help_text="Minimum value (up to -10)")
    max_value = models.IntegerField(default=5, validators=[MaxValueValidator(10)], help_text="Maximum value (up to 10)")
    min_label = models.CharField(max_length=50, default="Strongly Disagree", blank=False, help_text="Label for minimum value")
    max_label = models.CharField(max_length=50, default="Strongly Agree", blank=False, help_text="Label for maximum value")
    labels = models.JSONField(default=dict, null=True, blank=True, help_text='Optional labels for each value (e.g., {"1": "Poor", "5": "Excellent"})')

    class Meta:
        ordering = ['name']
        verbose_name = 'Scale'
        verbose_name_plural = 'Scales'

    def __str__(self):
        return f"{self.name} ({self.min_value}-{self.max_value})"

    def clean(self):
        """Validate that required fields are filled"""
        from django.core.exceptions import ValidationError

        # Validate required labels
        if not self.min_label or not self.min_label.strip():
            raise ValidationError({'min_label': 'Minimum label is required.'})
        if not self.max_label or not self.max_label.strip():
            raise ValidationError({'max_label': 'Maximum label is required.'})

        # Validate min/max values
        if self.min_value >= self.max_value:
            raise ValidationError({'max_value': 'Maximum value must be greater than minimum value.'})

        # Validate labels JSON field if provided
        if self.labels:
            if not isinstance(self.labels, dict):
                raise ValidationError({'labels': 'Labels must be a dictionary/object.'})

            # Validate that keys are integers within the scale range
            for key, value in self.labels.items():
                try:
                    key_int = int(key)
                    if key_int < self.min_value or key_int > self.max_value:
                        raise ValidationError({
                            'labels': f'Label key {key} is outside the scale range ({self.min_value}-{self.max_value}).'
                        })
                except (ValueError, TypeError):
                    raise ValidationError({'labels': f'Label key "{key}" must be a number.'})

    def get_display_labels(self):
        if self.labels:
            return {
                value: _(label)
                for value, label in self.labels.items()
            }

        return {
            self.min_value: _(self.min_label),
            self.max_value: _(self.max_label),
        }


# Each Survey is made up of one or more questions
# The Type determines what kind of question it is
class Question(models.Model):
    class QuestionType(models.TextChoices):
        MULTIPLE_CHOICE = 'MC', 'Multiple Choice'
        MULTIPLE_SELECTION = 'MS', 'Multiple Selection'
        OPEN_ENDED = 'OE', 'Open Ended'
        RANKING = 'RK', 'Ranking'
        SCALE = 'LK', 'Scale'
        BOOLEAN = 'YN', 'Yes/No'
        DATE = 'DA', 'Date'
        TIME = 'TI', 'Time'
        DATETIME = 'DT', 'Date & Time'
        TIME_AVAILABILITY = 'TA', 'Time Availability'
        DAY_AVAILABILITY = 'DY', 'Day Availability'

    survey = models.ForeignKey(Survey, on_delete=models.CASCADE, related_name='questions')
    text = models.TextField(help_text="The question text")
    likert_scale = models.ForeignKey(LikertScale, null=True, blank=True, on_delete=models.SET_NULL, help_text="Required for Likert Scale questions")
    question_type = models.CharField(max_length=2, choices=QuestionType.choices)
    order = models.PositiveIntegerField(default=0, help_text="Display order in survey")
    required = models.BooleanField(default=True, help_text="Is this question required?")
    help_text = models.CharField(max_length=300, blank=True, help_text="Optional help text shown to users")

    # Post-based choices configuration
    class PostSelectionMode(models.TextChoices):
        ALL_OFFICIAL = 'all_official', 'All Official'
        INDIVIDUAL = 'individual', 'Select Individual'

    post_component = models.CharField(
        max_length=20,
        null=True, blank=True,
        help_text="Component type for Post-based choices (e.g., Faction, Map)"
    )
    post_selection_mode = models.CharField(
        max_length=20,
        choices=PostSelectionMode.choices,
        null=True, blank=True,
        help_text="How Posts are selected as choices"
    )

    class Meta:
        ordering = ['survey', 'order', 'id']
        verbose_name = 'Question'
        verbose_name_plural = 'Questions'

    def __str__(self):
        return f"{self.survey.title} - Q{self.order}: {self.text[:50]}"

    def create_utc_hour_choices(self):
        """Create 24 UTC hour choices for TIME_AVAILABILITY questions"""
        if self.question_type != self.QuestionType.TIME_AVAILABILITY:
            return

        # Only create if choices don't already exist
        if self.choices.exists():
            return

        # Create choices for hours 0-23 (UTC)
        for hour in range(24):
            Choice.objects.create(
                question=self,
                text=str(hour),
                order=hour
            )


    def create_day_choices(self):
        """Create 7 day choices for DAY_AVAILABILITY questions"""
        if self.question_type != self.QuestionType.DAY_AVAILABILITY:
            return

        # Only create if choices don't already exist
        if self.choices.exists():
            return

        for order, day_name in enumerate(calendar.day_name):
            Choice.objects.create(
                question=self,
                text=day_name,   # "Monday", "Tuesday", ...
                order=order
            )

    def clean(self):
        """Validate that question type matches required fields"""
        super().clean()
        if self.question_type == self.QuestionType.SCALE and not self.likert_scale:
            raise ValidationError("Scale questions require a Likert Scale to be selected.")

        # Only validate choices if the question has been saved (has a primary key)
        # Skip validation for "All Official" mode since choices are loaded dynamically
        if self.pk and not self.uses_all_official_posts():
            if self.question_type in [self.QuestionType.MULTIPLE_CHOICE, self.QuestionType.MULTIPLE_SELECTION,
                                      self.QuestionType.BOOLEAN, self.QuestionType.RANKING,
                                      self.QuestionType.TIME_AVAILABILITY, self.QuestionType.DAY_AVAILABILITY]:
                if not self.choices.exists():
                    raise ValidationError(f"{self.get_question_type_display()} questions require at least one choice.")

    def is_post_based(self):
        """Check if this question uses Posts as choices"""
        return bool(self.post_component)

    def uses_all_official_posts(self):
        """Check if dynamically loading all official Posts"""
        return self.post_component and self.post_selection_mode == self.PostSelectionMode.ALL_OFFICIAL

    def get_post_choices(self):
        """Get Posts for 'all_official' mode"""
        if not self.uses_all_official_posts():
            return None
        Post = apps.get_model('the_keep', 'Post')
        return Post.objects.filter(
            component=self.post_component,
            official=True,
            status__lte=4
        ).order_by('title')


# For multiple choice questions they will have multiple choices
class Choice(models.Model):
    question = models.ForeignKey(Question, on_delete=models.CASCADE, related_name='choices')
    text = models.CharField(max_length=200, blank=True)  # blank=True since Post can provide text
    order = models.PositiveIntegerField(default=0)
    post = models.ForeignKey(
        'the_keep.Post', on_delete=models.CASCADE,
        null=True, blank=True, related_name='survey_choices',
        help_text="Link to a Post - if set, displays Post title/icon"
    )

    class Meta:
        ordering = ['question', 'order', 'id']
        verbose_name = 'Choice'
        verbose_name_plural = 'Choices'

    def __str__(self):
        return f"{self.question.text[:30]} - {self.get_display_text()}"

    def get_display_text(self):
        """Return the text to display for this choice"""
        return self.post.title if self.post else self.text

    def clean(self):
        """Validate that either text or post is provided"""
        super().clean()
        if not self.text and not self.post:
            raise ValidationError("Choice must have either text or a linked Post.")


# A user's response to a survey is stored here
class SurveyResponse(models.Model):
    survey = models.ForeignKey(Survey, on_delete=models.CASCADE, related_name='responses')
    user = models.ForeignKey(Profile, on_delete=models.SET_NULL, null=True, blank=True, related_name='survey_responses')
    submitted_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-submitted_at']
        verbose_name = 'Survey Response'
        verbose_name_plural = 'Survey Responses'
        indexes = [
            models.Index(fields=['survey', 'user']),
        ]

    def __str__(self):
        user_display = self.user.name if self.user else "Anonymous"
        return f"{user_display} → {self.survey.title}"

    def can_view_response(self, user_profile):
        if not user_profile:
            return False
        # Admin, survey owner or respondent can view
        if user_profile == self.user or user_profile.admin or user_profile == self.survey.created_by:
            return True
        return False

# An answer to a question that is linked to a user's response
class Answer(models.Model):
    response = models.ForeignKey(SurveyResponse, on_delete=models.CASCADE, related_name='answers')
    question = models.ForeignKey(Question, on_delete=models.CASCADE)

    # For open-ended
    text_answer = models.TextField(blank=True, null=True)

    # For multiple choice (single selection)
    selected_choice = models.ForeignKey(Choice, on_delete=models.SET_NULL, null=True, blank=True, related_name='single_answers')

    # For multiple selection
    selected_choices = models.ManyToManyField(Choice, blank=True, related_name='multiple_answers')

    # For date/time questions
    date_answer = models.DateField(blank=True, null=True, help_text="For date questions and date part of datetime questions")
    time_answer = models.TimeField(blank=True, null=True, help_text="For time questions and time part of datetime questions")

    # For rating/likert (stored as integer value)
    numeric_answer = models.IntegerField(blank=True, null=True, help_text="For rating and likert scale questions")

    # For Post-based answers - store Post directly for easier querying/reporting
    selected_post = models.ForeignKey(
        'the_keep.Post', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='single_post_answers',
        help_text="For Post-based single selection questions"
    )
    selected_posts = models.ManyToManyField(
        'the_keep.Post', blank=True, related_name='multiple_post_answers',
        help_text="For Post-based multiple selection questions"
    )

    class Meta:
        ordering = ['response', 'question__order']
        verbose_name = 'Answer'
        verbose_name_plural = 'Answers'
        unique_together = ('response', 'question')

    def clean(self):
        """Validate that answer matches question type"""
        qtype = self.question.question_type

        # MULTIPLE CHOICE
        if qtype == Question.QuestionType.MULTIPLE_CHOICE:
            if self.question.required and not self.selected_choice:
                raise ValidationError("Multiple choice question requires a selected choice.")
            if self.selected_choices.exists():
                raise ValidationError("Only one choice allowed for multiple choice questions.")

        # MULTIPLE SELECTION
        elif qtype == Question.QuestionType.MULTIPLE_SELECTION:
            # Check will happen after save for M2M fields
            if self.selected_choice:
                raise ValidationError("Use 'selected_choices' only for multiple selection.")

        # TIME AVAILABILITY - same as multiple selection
        elif qtype == Question.QuestionType.TIME_AVAILABILITY:
            # Check will happen after save for M2M fields
            if self.selected_choice:
                raise ValidationError("Use 'selected_choices' only for time availability.")
            
        # DAY AVAILABILITY - same as multiple selection
        elif qtype == Question.QuestionType.DAY_AVAILABILITY:
            # Check will happen after save for M2M fields
            if self.selected_choice:
                raise ValidationError("Use 'selected_choices' only for day availability.")

        # OPEN ENDED
        elif qtype == Question.QuestionType.OPEN_ENDED:
            if self.question.required and not self.text_answer:
                raise ValidationError("Open-ended question requires a text answer.")
            if self.selected_choice or self.selected_choices.exists():
                raise ValidationError("Open-ended questions should not have choices selected.")

        # BOOLEAN (YES/NO)
        elif qtype == Question.QuestionType.BOOLEAN:
            if self.question.required and not self.selected_choice:
                raise ValidationError("Yes/No question requires a choice.")
            if self.selected_choice:
                valid = self.question.choices.filter(pk=self.selected_choice.pk).exists()
                if not valid:
                    raise ValidationError("Selected choice is not valid for this question.")

        # SCALE - store in numeric_answer
        elif qtype == Question.QuestionType.SCALE:
            if self.question.required and self.numeric_answer is None:
                raise ValidationError(f"{self.question.get_question_type_display()} question requires a numeric answer.")
            if self.numeric_answer is not None and self.question.likert_scale:
                if not (self.question.likert_scale.min_value <= self.numeric_answer <= self.question.likert_scale.max_value):
                    raise ValidationError(f"Answer must be between {self.question.likert_scale.min_value} and {self.question.likert_scale.max_value}.")

        # RANKING - uses RankedAnswer model
        elif qtype == Question.QuestionType.RANKING:
            # Validation happens in RankedAnswer
            if self.selected_choice or self.selected_choices.exists() or self.text_answer:
                raise ValidationError("Use the RankedAnswer model to store ranking answers.")

        # DATE
        elif qtype == Question.QuestionType.DATE:
            if self.question.required and not self.date_answer:
                raise ValidationError("A valid date must be provided.")
            if self.selected_choice or self.selected_choices.exists() or self.text_answer or self.time_answer:
                raise ValidationError("Only a date answer is allowed.")

        # TIME
        elif qtype == Question.QuestionType.TIME:
            if self.question.required and not self.time_answer:
                raise ValidationError("A valid time must be provided.")
            if self.selected_choice or self.selected_choices.exists() or self.text_answer or self.date_answer:
                raise ValidationError("Only a time answer is allowed.")

        # DATETIME
        elif qtype == Question.QuestionType.DATETIME:
            if self.question.required and (not self.date_answer or not self.time_answer):
                raise ValidationError("Both date and time must be provided.")
            if self.selected_choice or self.selected_choices.exists() or self.text_answer:
                raise ValidationError("Only date and time answers are allowed.")

        # Fallback
        else:
            raise ValidationError("Unsupported question type.")

    def __str__(self):
        return f"Answer to '{self.question.text[:50]}'"

    def get_display_value(self):
        """Return a human-readable version of the answer"""
        qtype = self.question.question_type

        # Handle Post-based questions
        if self.question.is_post_based():
            if qtype == Question.QuestionType.MULTIPLE_CHOICE:
                if self.selected_post:
                    return self.selected_post.title
                elif self.selected_choice:
                    return self.selected_choice.get_display_text()
                return "No answer"
            elif qtype == Question.QuestionType.MULTIPLE_SELECTION:
                posts = self.selected_posts.all()
                if posts:
                    return ", ".join([p.title for p in posts])
                choices = self.selected_choices.all()
                if choices:
                    return ", ".join([c.get_display_text() for c in choices])
                return "No answer"
            elif qtype == Question.QuestionType.RANKING:
                ranked_posts = self.ranked_post_items.order_by('rank')
                if ranked_posts:
                    return ", ".join([f"{r.rank}. {r.post.title}" for r in ranked_posts])
                ranked = self.ranked_items.order_by('rank')
                if ranked:
                    return ", ".join([f"{r.rank}. {r.choice.get_display_text()}" for r in ranked])
                return "No answer"

        if qtype == Question.QuestionType.MULTIPLE_CHOICE:
            return self.selected_choice.get_display_text() if self.selected_choice else "No answer"
        elif qtype == Question.QuestionType.MULTIPLE_SELECTION:
            choices = self.selected_choices.all()
            return ", ".join([c.get_display_text() for c in choices]) if choices else "No answer"
        elif qtype == Question.QuestionType.TIME_AVAILABILITY:
            choices = self.selected_choices.all().order_by('text')
            return ", ".join([f"{c.text}:00 UTC" for c in choices]) if choices else "No answer"
        elif qtype == Question.QuestionType.DAY_AVAILABILITY:
            choices = self.selected_choices.all().order_by('text')
            return ", ".join([c.text for c in choices]) if choices else "No answer"
        elif qtype == Question.QuestionType.OPEN_ENDED:
            return self.text_answer or "No answer"
        elif qtype == Question.QuestionType.BOOLEAN:
            return self.selected_choice.get_display_text() if self.selected_choice else "No answer"
        elif qtype == Question.QuestionType.SCALE:
            return str(self.numeric_answer) if self.numeric_answer is not None else "No answer"
        elif qtype == Question.QuestionType.RANKING:
            ranked = self.ranked_items.order_by('rank')
            return ", ".join([f"{r.rank}. {r.choice.get_display_text()}" for r in ranked]) if ranked else "No answer"
        elif qtype == Question.QuestionType.DATE:
            return str(self.date_answer) if self.date_answer else "No answer"
        elif qtype == Question.QuestionType.TIME:
            return str(self.time_answer) if self.time_answer else "No answer"
        elif qtype == Question.QuestionType.DATETIME:
            if self.date_answer and self.time_answer:
                from datetime import datetime
                dt = datetime.combine(self.date_answer, self.time_answer)
                return dt.strftime("%B %d, %Y %I:%M %p")
            return "No answer"
        return "No answer"


class RankedAnswer(models.Model):
    """For ranking questions - stores the rank order of choices"""
    answer = models.ForeignKey(Answer, on_delete=models.CASCADE, related_name='ranked_items')
    choice = models.ForeignKey(Choice, on_delete=models.CASCADE)
    rank = models.PositiveIntegerField(help_text="Position in ranking (1 = first choice)")

    class Meta:
        ordering = ['answer', 'rank']
        unique_together = ('answer', 'rank')
        verbose_name = 'Ranked Answer'
        verbose_name_plural = 'Ranked Answers'

    def __str__(self):
        return f"Rank {self.rank}: {self.choice.get_display_text()}"


class RankedPostAnswer(models.Model):
    """For ranking Post-based questions - stores the rank order of Posts"""
    answer = models.ForeignKey(Answer, on_delete=models.CASCADE, related_name='ranked_post_items')
    post = models.ForeignKey('the_keep.Post', on_delete=models.CASCADE)
    rank = models.PositiveIntegerField(help_text="Position in ranking (1 = first choice)")

    class Meta:
        ordering = ['answer', 'rank']
        unique_together = ('answer', 'rank')
        verbose_name = 'Ranked Post Answer'
        verbose_name_plural = 'Ranked Post Answers'

    def __str__(self):
        return f"Rank {self.rank}: {self.post.title}"


# Question Templates - Reusable questions
class QuestionTemplate(models.Model):
    """Template for reusable survey questions"""
    name = models.CharField(max_length=200, help_text="Template name (e.g., 'Age Question', 'Satisfaction Scale')")
    text = models.TextField(help_text="The question text")
    question_type = models.CharField(max_length=2, choices=Question.QuestionType.choices)
    likert_scale = models.ForeignKey(LikertScale, null=True, blank=True, on_delete=models.SET_NULL)
    help_text = models.CharField(max_length=300, blank=True)
    required = models.BooleanField(default=True)
    choices_data = models.JSONField(default=list, blank=True, help_text="List of choice texts for choice-based questions")
    created_by = models.ForeignKey(Profile, on_delete=models.SET_NULL, null=True, blank=True, related_name='question_templates')
    created_at = models.DateTimeField(auto_now_add=True)
    is_public = models.BooleanField(default=False, help_text="Make available to all users")

    # Post-based choices configuration
    post_component = models.CharField(
        max_length=20,
        null=True, blank=True,
        help_text="Component type for Post-based choices"
    )
    post_selection_mode = models.CharField(
        max_length=20,
        choices=Question.PostSelectionMode.choices,
        null=True, blank=True,
        help_text="How Posts are selected as choices"
    )
    post_choices = models.ManyToManyField(
        'the_keep.Post', blank=True, related_name='question_templates',
        help_text="Pre-selected Posts for 'individual' mode templates"
    )

    class Meta:
        ordering = ['name']
        verbose_name = 'Question Template'
        verbose_name_plural = 'Question Templates'

    def __str__(self):
        return self.name

    def to_question_data(self):
        """Convert template to question data format used in create/edit forms"""
        data = {
            'text': self.text,
            'type': self.question_type,
            'required': self.required,
            'help_text': self.help_text,
        }

        if self.question_type == 'LK' and self.likert_scale:
            data['likert_scale_id'] = self.likert_scale_id

        # Handle Post-based templates
        if self.post_component:
            data['post_component'] = self.post_component
            data['post_selection_mode'] = self.post_selection_mode
            if self.post_selection_mode == Question.PostSelectionMode.INDIVIDUAL:
                data['post_choices'] = list(self.post_choices.values_list('id', flat=True))
        elif self.question_type in ['MC', 'MS', 'YN', 'RK'] and self.choices_data:
            data['choices'] = self.choices_data

        return data


class UserNotification(models.Model):
    """
    Persistent notification system for users.
    Stores dismissible notifications that appear in the message bar.
    """
    profile = models.ForeignKey(Profile, on_delete=models.CASCADE, related_name='notifications')
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
    def create_notification(cls, profile, message, message_type=MessageChoices.INFO, related_post=None, related_url=None):
        """
        Helper method to create a notification.

        Args:
            profile: Profile object
            message: Notification message text
            message_type: Type of message (success, warning, danger, info)
            related_post: Optional Post object to link to
            related_url: Optional URL to link to
        """
        notification = cls.objects.create(
            profile=profile,
            message=message,
            message_type=message_type,
            related_post_id=related_post.id if related_post else None,
            related_url=related_url
        )
        return notification


# Signal to auto-create UTC hour choices for TIME_AVAILABILITY questions
@receiver(post_save, sender=Question)
def create_time_availability_choices(sender, instance, created, **kwargs):
    """Automatically create 24 UTC hour choices for TIME_AVAILABILITY questions"""
    if instance.question_type == Question.QuestionType.TIME_AVAILABILITY:
        instance.create_utc_hour_choices()
    """Automatically create weekday choices for DAY_AVAILABILITY questions"""
    if instance.question_type == Question.QuestionType.DAY_AVAILABILITY:
        instance.create_day_choices()
