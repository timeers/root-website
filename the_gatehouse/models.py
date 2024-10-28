from django.contrib.auth.models import User
# from the_warroom.models import Effort

from django.db import models
from PIL import Image


class Profile(models.Model):
    user = models.OneToOneField(User, on_delete=models.SET_NULL, null=True, blank=True)
    image = models.ImageField(default='default.png', upload_to='profile_pics')
    dwd = models.CharField(max_length=100, unique=True, blank=True, null=True)
    discord = models.CharField(max_length=100, unique=True, blank=True, null=True) #remove null and blank once allauth is added
    league = models.BooleanField(default=False)

    def absorbed_by(self, player):
        if self.user is None:
            games = self.gameplay_set.all() 
            print('Transferring Games')
            for play in games:
                play.player = player
                play.save()
                print(f'Gameplay {play.id} transferred from {self} to {player}')

    def __str__(self):
        return f'{self.discord}'
    
    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)

        img = Image.open(self.image.path)

        if img.height > 300 or img.width > 300:
            output_size = (300, 300)
            img.thumbnail(output_size)
            img.save(self.image.path)

    def winrate(self, faction = None):
        efforts = self.effort_set.all()  # Access the related Effort objects for this player

        if faction:
            efforts = efforts.filter(faction=faction)

        all_games = efforts.count()
        wins = efforts.filter(win=True)
        points = 0
        for effort in wins:
            points += (1 / effort.game.get_winners().count())        
        if all_games > 0:
            return points / all_games  # Calculate winrate
        return 0