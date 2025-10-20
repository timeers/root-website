import os
import uuid
from django.contrib.auth.models import User
from io import BytesIO
# from the_warroom.models import Effort
from django.urls import reverse
from django.db.models.signals import pre_save, post_save
from django.db import models
from PIL import Image
from .utils import slugify_instance_discord
from django.db.models import Count, F, ExpressionWrapper, FloatField, Q, Case, When, Value
from django.db.models.functions import Cast
from django.apps import apps
from django.utils import timezone 
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
    
    class Meta:
        ordering = ['id']

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
    background_artist = models.ForeignKey('Profile', on_delete=models.SET_NULL, null=True, blank=True)
    image = models.ImageField(upload_to='background_images')
    theme = models.ForeignKey(Theme, on_delete=models.CASCADE)
    page = models.CharField(max_length=15 , default=PageChoices.LIBRARY, choices=PageChoices.choices)
    background_color = models.CharField(
        max_length=7,
        blank=True,
        null=True,
        validators=[validate_hex_color],
        help_text="Enter a hex color code (e.g., #RRGGBB)."
    )
    small_image = models.ImageField(upload_to='background_images', null=True, blank=True)
    date_modified = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name
    
    # def process_and_save_small_image(self, image_field_name):
    #     """
    #     Process the image field, resize it, and save it to the `small_image` field.
    #     This method can be used by any subclass of Post to reuse the image processing logic.
    #     """
    #     # Get the image from the specified field
    #     image = getattr(self, image_field_name)

    #     if image:
    #         # Open the image from the ImageFieldFile
    #         img = Image.open(image)
    #             # Convert the image to RGB if it's in a mode like 'P' (palette-based)
    #         # if img.mode != 'RGB':
    #         #     img = img.convert('RGB')
    #         # Create a copy of the image
    #         small_image_copy = img.copy()

    #         # Optionally, save the image to a new BytesIO buffer
    #         img_io = BytesIO()
    #         small_image_copy.save(img_io, format='WEBP', quality=80)  # Save as a WebP, or another format as needed
    #         img_io.seek(0)
    #         # Now you can assign the img_io to your model field or save it to a new ImageField
    #         # Generate a unique filename using UUID
    #         unique_filename = f"{uuid.uuid4().hex}.webp"

    #         # Save to small_image field with unique filename
    #         self.small_image.save(unique_filename, img_io, save=False)



    def alt(self):
        if self.background_artist:
            alt = f'{ self.name } by {self.background_artist.name}'
        else:
            alt = f'{ self.name }'
        return alt

        
    def save(self, *args, **kwargs):
        field_name = 'image'
        
        # Check if the instance already exists (i.e., is not a new object)
        if self.pk:
            try:
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
    foreground_artist = models.ForeignKey('Profile', on_delete=models.SET_NULL, null=True, blank=True)
    location = models.IntegerField(default=LocationChoices.CENTER, choices=LocationChoices.choices)
    image = models.ImageField(upload_to='foreground_images')
    
    theme = models.ForeignKey(Theme, on_delete=models.CASCADE)
    page = models.CharField(max_length=15 , default=PageChoices.LIBRARY, choices=PageChoices.choices)
    depth = models.IntegerField(default=-1)
    start_position = models.TextField(default='0vw')
    slide = models.TextField(default='0vw')
    speed = models.TextField(default='50vh')
    small_image = models.ImageField(upload_to='foreground_images', null=True, blank=True)
    date_modified = models.DateTimeField(auto_now=True)

    def style(self):
        return f'--offset-percent: { self.slide }; --slide-speed: { self.speed }; --z-depth: { self.depth }; --start-position: { self.start_position };'
    
    def __str__(self):
        return self.name

    # def process_and_save_small_image(self, image_field_name):
    #     """
    #     Process the image field, resize it, and save it to the `small_image` field.
    #     This method can be used by any subclass of Post to reuse the image processing logic.
    #     """
    #     # Get the image from the specified field
    #     image = getattr(self, image_field_name)
    #     print('processing')
    #     if image:
    #         print('found image')
    #         # Open the image from the ImageFieldFile
    #         img = Image.open(image)
    #             # Convert the image to RGB if it's in a mode like 'P' (palette-based)
    #         # if img.mode != 'RGB':
    #         #     img = img.convert('RGB')
    #         # Create a copy of the image
    #         small_image_copy = img.copy()
            
    #         # Optionally, save the image to a new BytesIO buffer
    #         img_io = BytesIO()
    #         small_image_copy.save(img_io, format='WEBP', quality=80)  # Save as a WebP, or another format as needed
    #         img_io.seek(0)
    #         # Now you can assign the img_io to your model field or save it to a new ImageField
    #         # Generate a unique filename using UUID
    #         unique_filename = f"{uuid.uuid4().hex}.webp"

    #         # Save to small_image field with unique filename
    #         self.small_image.save(unique_filename, img_io, save=False)


    def alt(self):
        if self.foreground_artist:
            alt = f'{ self.name } by {self.foreground_artist.name}'
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
        # from yourapp.models import Faction  # Lazy import to avoid circular imports
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