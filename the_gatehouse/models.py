import os
from django.contrib.auth.models import User
# from the_warroom.models import Effort
from django.urls import reverse
from django.db.models.signals import pre_save, post_save
from django.db import models
from PIL import Image
from .utils import slugify_instance_discord
from django.db.models import Count
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
    weird = models.BooleanField(default=False)
    display_name = models.CharField(max_length=100, unique=True, null=True, blank=True)
    slug = models.SlugField(unique=True, null=True, blank=True)
    bookmarks = models.ManyToManyField('self', through='PlayerBookmark')

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
        

    def winrate(self, faction = None):
        efforts = self.efforts.all()  # Access the related Effort objects for this player

        if faction:
            efforts = efforts.filter(faction=faction)

        all_games = efforts.count()
        wins = efforts.filter(win=True)
        points = 0
        for effort in wins:
            points += (1 / effort.game.get_winners().count())        
        if all_games > 0:
            return points / all_games * 100  # Calculate winrate
        return 0
    
    def get_games_queryset(self):
        Game = apps.get_model('the_warroom', 'Game')
        efforts = self.efforts.all()
        games = Game.objects.filter(id__in=[effort.game.id for effort in efforts]).order_by('-date_posted')
        return games


    def games_played(self, faction = None):
        efforts = self.efforts.all()  # Access the related Effort objects for this player
        if faction:
            efforts = efforts.filter(faction=faction)
        all_games = efforts.count()
        return all_games


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
            self.efforts.filter(win=True)
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
