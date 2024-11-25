from django.db import models
from the_keep.models import Post
from the_warroom.models import Game
from the_gatehouse.models import Profile
from django.utils import timezone 

#  I think I want to use Comments as a private notepad. I'm leaning towards only one comment per game/post.
#  Discussions should be kept in Discord on the linked threads.
class Comment(models.Model):
    public = models.BooleanField(default=False)
    body = models.CharField(max_length=300)
    date_posted = models.DateTimeField(default=timezone.now)
    class Meta:
        abstract = True

class PostComment(Comment):
    type = "post"
    player = models.ForeignKey(Profile, on_delete=models.CASCADE, related_name='post_comments')
    post = models.ForeignKey(Post, on_delete=models.CASCADE, related_name='comments')
    def __str__(self):
        return f"{self.player.name}: {self.body[:30]}"

class GameComment(Comment):
    type = "game"
    player = models.ForeignKey(Profile, on_delete=models.CASCADE, related_name='game_comments')
    game = models.ForeignKey(Game, on_delete=models.CASCADE, related_name='comments')
    class Meta:
        ordering = ['-date_posted']
    def __str__(self):
        return f"{self.player.name}: {self.body[:30]}"







