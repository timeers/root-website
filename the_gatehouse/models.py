import os
from django.contrib.auth.models import User
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


class Profile(models.Model):
    class GroupChoices(models.TextChoices):
        OUTCAST = 'O'
        PLAYER = 'P'
        DESIGNER = 'D'
        ADMIN = 'A'
        BANNED = 'B'

    component = 'Profile'
    user = models.OneToOneField(User, on_delete=models.SET_NULL, null=True, blank=True)
    image = models.ImageField(default='default_images/default_user.png', upload_to='profile_pics')
    dwd = models.CharField(max_length=100, unique=True, blank=True, null=True)
    discord = models.CharField(max_length=100, unique=True, blank=True, null=True) #remove null and blank once allauth is added
    league = models.BooleanField(default=False)
    group = models.CharField(max_length=1, choices=GroupChoices.choices, default=GroupChoices.OUTCAST)
    in_weird_root = models.BooleanField(default=False)
    weird = models.BooleanField(default=False)
    display_name = models.CharField(max_length=100, unique=True, null=True, blank=True)
    slug = models.SlugField(unique=True, null=True, blank=True)
    bookmarks = models.ManyToManyField('self', through='PlayerBookmark')
    player_onboard = models.BooleanField(default=False)
    designer_onboard = models.BooleanField(default=False)
    admin_onboard = models.BooleanField(default=False)

    @property
    def name(self):
        if self.display_name:
            name = self.display_name
        elif self.discord:
            name = self.discord
        else:
            name = self.user.username
        return name
        

    def __str__(self):
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
                    print(f'Resized image saved at: {self.image.path}')
                else:
                    print(f'Original image saved at: {self.image.path}')
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
        if group == "D" or group == "A":
            return True
        else:
            return False

    @property
    def player(self):
        group = self.group
        if group == "D" or group == "A" or group == "P":
            return True
        else:
            return False
        

    def winrate(self, faction = None, deck = None, tournament = None, round = None):
        # efforts = self.efforts.all()  # Access the related Effort objects for this player
        efforts = self.efforts.filter(game__test_match=False)

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
            id__in=efforts.values_list('game', flat=True)
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
            id__in=efforts.values_list('game', flat=True)
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
            self.efforts.filter(win=True, game__test_match=False)
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
        queryset = cls.objects.all()

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
            win_count=Count('efforts', filter=Q(efforts__win=True, efforts__faction_id=faction_id) if faction_id else Q(efforts__win=True))
        )
        
        # Filter players who have enough efforts (before doing the annotation)

        queryset = queryset.filter(total_efforts__gte=game_threshold)

        # Annotate with win_rate after filtering
        queryset = queryset.annotate(
            win_rate=Case(
                When(total_efforts=0, then=Value(0)),
                default=ExpressionWrapper(
                    Cast(F('win_count'), FloatField()) / Cast(F('total_efforts'), FloatField()) * 100,  # Win rate as percentage
                    output_field=FloatField()
                ),
                output_field=FloatField()
            )
        )
        # Now we can order the queryset
        if top_quantity:
            # If top_quantity is True, order by total_efforts (most efforts) first
            return queryset.order_by('-total_efforts', '-win_rate')[:limit]
        else:
            # Otherwise, order by win_rate (highest win rate) first
            return queryset.order_by('-win_rate', '-total_efforts')[:limit]


    def faction_stats(self, most_wins=False, tournament=None, round=None, limit=15, game_threshold=1):
            from the_keep.models import Faction
            # Start with the base queryset for players
            queryset = Faction.objects.filter(efforts__player=self)
            print("got qs", most_wins, tournament, round, limit, game_threshold)
            # Now, annotate with the total efforts and win counts for player
            if round:
                queryset = queryset.annotate(
                    total_efforts=Count('efforts', filter=Q(efforts__player=self, efforts__game__round=round)),
                    win_count=Count('efforts', filter=Q(efforts__win=True, efforts__player=self, efforts__game__round=round))
                )
            elif tournament:
                queryset = queryset.annotate(
                    total_efforts=Count('efforts', filter=Q(efforts__player=self, efforts__game__round__tournament=tournament)),
                    win_count=Count('efforts', filter=Q(efforts__win=True, efforts__player=self, efforts__game__round__tournament=tournament))
                )
            else:
                queryset = queryset.annotate(
                    total_efforts=Count('efforts', filter=Q(efforts__player=self)),
                    win_count=Count('efforts', filter=Q(efforts__win=True, efforts__player=self))
                )

            # Filter factions who have enough efforts
            queryset = queryset.filter(total_efforts__gte=game_threshold)

            # Annotate with win_rate after filtering
            queryset = queryset.annotate(
                win_rate=Case(
                    When(total_efforts=0, then=Value(0)),
                    default=ExpressionWrapper(
                        Cast(F('win_count'), FloatField()) / Cast(F('total_efforts'), FloatField()) * 100,  # Win rate as percentage
                        output_field=FloatField()
                    ),
                    output_field=FloatField()
                )
            )

            for faction in queryset:
                print(f'Faction-{faction.title}, Wins-{faction.win_count}, Games-{faction.total_efforts}')

            # Now we can order the queryset
            if most_wins:
                # If most_wins is True, order by total_efforts (most efforts) first
                return queryset.order_by('-win_count', '-total_efforts', '-win_rate')[:limit]
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


def component_pre_save(sender, instance, *args, **kwargs):
    # print('pre_save')
    if instance.slug is None:
        slugify_instance_discord(instance, save=False)

pre_save.connect(component_pre_save, sender=Profile)



def component_post_save(sender, instance, created, *args, **kwargs):
    # print('post_save')
    if created:
        slugify_instance_discord(instance, save=True)

post_save.connect(component_post_save, sender=Profile)
