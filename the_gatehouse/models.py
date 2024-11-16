import os
from django.contrib.auth.models import User
# from the_warroom.models import Effort
from django.urls import reverse
from django.db.models.signals import pre_save, post_save
from django.db import models
from PIL import Image
from .utils import slugify_instance_discord


class Profile(models.Model):
    component = 'Profile'
    user = models.OneToOneField(User, on_delete=models.SET_NULL, null=True, blank=True)
    image = models.ImageField(default='default_images/default_user.png', upload_to='profile_pics')
    dwd = models.CharField(max_length=100, unique=True, blank=True, null=True)
    discord = models.CharField(max_length=100, unique=True, blank=True, null=True) #remove null and blank once allauth is added
    league = models.BooleanField(default=False)
    creative = models.BooleanField(default=False)
    display_name = models.CharField(max_length=100, unique=True, null=True, blank=True)
    slug = models.SlugField(unique=True, null=True, blank=True)

    def absorbed_by(self, profile):
        if self.user is None:
            efforts = self.efforts.all() 
            print('Transferring Games')
            for effort in efforts:
                effort.player = profile
                effort.save()
                print(f'Gameplay {effort.id} transferred from {self} to {profile}')
            designer_posts = self.posts.all()
            print('Transferring Posts')
            for post in designer_posts:
                post.designer = profile
                post.save()
                print(f'Component {post} transferred from {self} to {profile}')
            artist_posts = self.artist_posts.all()
            print('Transferring Art')
            for art in artist_posts:
                art.artist = profile
                art.save()
                print(f'Art for {art} transferred from {self} to {profile}')

    def __str__(self):
        return self.discord
    
    #  No longer used as files are stored in S3
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
    
    def get_absolute_url(self):
        return reverse('player-detail', kwargs={'slug': self.slug})
    



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
