import os
import uuid
from django.contrib.auth.models import User
from io import BytesIO
# from the_warroom.models import Effort
from django.urls import reverse
from django.db.models.signals import pre_save, post_save
from django.db import models
from PIL import Image
from django.db.models import Count, F, ExpressionWrapper, FloatField, Q, Case, When, Value
from django.db.models.functions import Cast
from django.apps import apps
from django.utils import timezone 
from django.core.exceptions import ValidationError
from django.utils.translation import gettext as _
from the_keep.utils import validate_hex_color, delete_old_image

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


class DiscordGuild(models.Model):
    guild_id = models.CharField(max_length=32, unique=True)
    name = models.CharField(max_length=100)

    def __str__(self):
        return self.name

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
    image = models.ImageField(default='default_images/default_user.png', upload_to='profile_pics')
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
    credit_link = models.CharField(max_length=400, null=True, blank=True)
    date_modified = models.DateTimeField(auto_now=True)
    guilds = models.ManyToManyField(DiscordGuild, related_name="members")

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
    def leaderboard(cls, effort_qs, top_quantity=False, limit=5, game_threshold=10):
        """
        Get the players with the highest winrate (or most wins for top_quantity) from the effort_qs
        The limit is how many players will be displayed.
        The game theshold is how many games a player needs to play to qualify.
        """
        # Start with the base queryset for profiles
        queryset = cls.objects.all()

        # If a tournament is provided, filter efforts that are related to that tournament

        queryset = queryset.filter(efforts__in=effort_qs)
        queryset = queryset.annotate(
            total_efforts=Count('efforts', filter=Q(efforts__in=effort_qs, efforts__game__final=True, efforts__game__test_match=False)),
            win_count=Count('efforts', filter=Q(efforts__in=effort_qs, efforts__win=True, efforts__game__final=True, efforts__game__test_match=False)),
            coalition_count=Count('efforts', filter=Q(efforts__in=effort_qs, efforts__win=True, efforts__game__coalition_win=True, efforts__game__final=True, efforts__game__test_match=False))
        )

        # Now, annotate with the total efforts and win counts
        queryset = queryset.annotate(
            total_efforts=Count('efforts', filter=Q(efforts__game__final=True, efforts__game__test_match=False)),
            win_count=Count('efforts', filter=Q(efforts__win=True, efforts__game__final=True, efforts__game__test_match=False)),
            coalition_count=Count('efforts', filter=Q(efforts__win=True, efforts__game__coalition_win=True, efforts__game__final=True, efforts__game__test_match=False))
        )
        
        # Filter factions who have enough efforts (before doing the annotation)

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
        # Now we can order the queryset
        if top_quantity:
            # If top_quantity is True, order by total_efforts (most efforts) first
            return queryset.order_by('-tourney_points', '-win_rate')[:limit]
        else:
            # Otherwise, order by win_rate (highest win rate) first
            return queryset.order_by('-win_rate', '-total_efforts')[:limit]




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
    rdb_feedback_invite = models.CharField(max_length=100, null=True, blank=True)
    date_modified = models.DateTimeField(auto_now=True)
    last_law_check = models.DateTimeField(null=True, blank=True)

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
    


# # Surveys
# class Survey(models.Model):
#     title = models.CharField(max_length=200)
#     description = models.TextField(blank=True)
#     is_active = models.BooleanField(default=True)
#     created_at = models.DateTimeField(auto_now_add=True)
#     start_date = models.DateTimeField(null=True, blank=True)
#     end_date = models.DateTimeField(null=True, blank=True)
#     is_public = models.BooleanField(default=True)


# class LikertScale(models.Model):
#     min_value = models.IntegerField(default=1)
#     max_value = models.IntegerField(default=5)
#     labels = models.JSONField(default=dict)

# # Each Survey is made up of one or more questions
# # The Type determines what kind of question it is
# class Question(models.Model):
#     class QuestionType(models.TextChoices):
#         MULTIPLE_CHOICE = 'MC', 'Multiple Choice'
#         MULTIPLE_SELECTION = 'MS', 'Multiple Selection'
#         OPEN_ENDED = 'OE', 'Open Ended'
#         RANKING = 'RK', 'Ranking'
#         RATING = 'RT', 'Rating'
#         LIKERT = 'LK', 'Likert Scale'
#         BOOLEAN = 'YN', 'Yes/No'
#         DATE = 'DT', 'Date/Time'

#     survey = models.ForeignKey(Survey, on_delete=models.CASCADE, related_name='questions')
#     text = models.TextField()
#     likert_scale = models.ForeignKey(LikertScale, null=True, blank=True, on_delete=models.SET_NULL)
#     question_type = models.CharField(max_length=2, choices=QuestionType.choices)
#     order = models.PositiveIntegerField(default=0)
#     required = models.BooleanField(default=True)
#     help_text = models.CharField(max_length=300, blank=True)


# # For multiple choice questions they will have multiple choices
# class Choice(models.Model):
#     question = models.ForeignKey(Question, on_delete=models.CASCADE, related_name='choices')
#     text = models.CharField(max_length=200)
#     order = models.PositiveIntegerField(default=0)

# # A user's response to a survey is stored here
# class SurveyResponse(models.Model):
#     survey = models.ForeignKey(Survey, on_delete=models.CASCADE, related_name='responses')
#     user = models.ForeignKey(Profile, on_delete=models.SET_NULL, null=True, blank=True)
#     submitted_at = models.DateTimeField(auto_now_add=True)
#     class Meta:
#         # Prevent duplicate submissions
#         unique_together = ('survey', 'user')

#     def __str__(self):
#         return f"{self.user} → {self.survey.title}"


# # An anser to a question that is linked to a user's response
# class Answer(models.Model):
#     response = models.ForeignKey(SurveyResponse, on_delete=models.CASCADE, related_name='answers')
#     question = models.ForeignKey(Question, on_delete=models.CASCADE)

#     # For open-ended
#     text_answer = models.TextField(blank=True, null=True)

#     # For multiple choice
#     selected_choice = models.ForeignKey(Choice, on_delete=models.SET_NULL, null=True, blank=True)
#     selected_choices = models.ManyToManyField(Choice, blank=True)

#     # For date questions
#     date_answer = models.DateTimeField(blank=True, null=True)

#     def clean(self):
#         qtype = self.question.question_type

#         # MULTIPLE CHOICE
#         if qtype == Question.QuestionType.MULTIPLE_CHOICE:
#             if not self.selected_choice:
#                 raise ValidationError("Multiple choice question requires a selected choice.")
#             if self.selected_choices.exists():
#                 raise ValidationError("Only one choice allowed for multiple choice questions.")

#         # MULTIPLE SELECTION
#         elif qtype == Question.QuestionType.MULTIPLE_SELECTION:
#             if not self.pk:
#                 # Need to save instance first to access selected_choices M2M
#                 super().save()
#             if self.selected_choices.count() == 0:
#                 raise ValidationError("You must select at least one option for multiple selection questions.")
#             if self.selected_choice:
#                 raise ValidationError("Use 'selected_choices' only for multiple selection.")

#         # OPEN ENDED
#         elif qtype == Question.QuestionType.OPEN_ENDED:
#             if not self.text_answer:
#                 raise ValidationError("Open-ended question requires a text answer.")
#             if self.selected_choice or self.selected_choices.exists():
#                 raise ValidationError("Open-ended questions should not have choices selected.")

#         # BOOLEAN (YES/NO)
#         elif qtype == Question.QuestionType.BOOLEAN:
#             if not self.selected_choice:
#                 raise ValidationError("Yes/No question requires a choice.")
#             valid = self.question.choices.filter(pk=self.selected_choice.pk).exists()
#             if not valid:
#                 raise ValidationError("Selected choice is not valid for this question.")

#         # RANKING, LIKERT, RATING
#         elif qtype in [Question.QuestionType.RANKING, Question.QuestionType.LIKERT, Question.QuestionType.RATING]:
#             # Enforce that answers are in `RankedAnswer`, not here
#             if self.selected_choice or self.selected_choices.exists() or self.text_answer:
#                 raise ValidationError("Use the associated ranking models to store ranking/likert/rating answers.")

#         # DATE
#         elif qtype == Question.QuestionType.DATE:
#             if not self.date_answer:
#                 raise ValidationError("A valid date/time must be provided.")
#             if self.selected_choice or self.selected_choices.exists() or self.text_answer:
#                 raise ValidationError("Only a date/time answer is allowed.")


#         # Fallback
#         else:
#             raise ValidationError("Unsupported question type.")

#     def __str__(self):
#         return f"Answer to '{self.question.text}'"


# class RankedAnswer(models.Model):
#     answer = models.ForeignKey(Answer, on_delete=models.CASCADE, related_name='ranked_items')
#     choice = models.ForeignKey(Choice, on_delete=models.CASCADE)
#     rank = models.PositiveIntegerField()
#     class Meta:
#         unique_together = ('answer', 'rank')
