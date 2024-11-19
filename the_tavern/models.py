from django.db import models
from the_keep.models import Post
from the_warroom.models import Game
from the_gatehouse.models import Profile
from django.utils import timezone 

#  I think I want to use Comments as a private notepad. I'm leaning towards only one comment per game/post.
#  Discussions should be kept in Discord on the linked threads.
class Comment(models.Model):
    player = models.ForeignKey(Profile, on_delete=models.CASCADE, related_name='comments')
    post = models.ForeignKey(Post, on_delete=models.CASCADE, related_name='comments', null=True, blank=True)
    game = models.ForeignKey(Game, on_delete=models.CASCADE, related_name='comments', null=True, blank=True)
    public = models.BooleanField(default=False)
    body = models.TextField()
    date_posted = models.DateTimeField(default=timezone.now)

# Bookmarks can be a way for a user to favorite certain things to find them easily. I don't want these to be public.
# But maybe sort by number of favorites to bring popular components to the top?
class Bookmark(models.Model):
    player = models.ForeignKey(Profile, on_delete=models.CASCADE, related_name='bookmarks')
    post = models.ForeignKey(Post, on_delete=models.CASCADE, related_name='bookmarks', null=True, blank=True)
    game = models.ForeignKey(Game, on_delete=models.CASCADE, related_name='bookmarks', null=True, blank=True)
    public = models.BooleanField(default=False)
    date_posted = models.DateTimeField(default=timezone.now)