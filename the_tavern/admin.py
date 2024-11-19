from django.contrib import admin
from .models import Bookmark, Comment


class BookmarkAdmin(admin.ModelAdmin):
    list_display = ('id', 'player', 'post', 'game', 'public')
    search_fields = ['post__title', 'player__discord', 'player__dwd', 'player__display_name']

class CommentAdmin(admin.ModelAdmin):
    list_display = ('id', 'player', 'post', 'game', 'public')
    search_fields = ['post__title', 'player__discord', 'player__dwd', 'player__display_name', 'body']

# Register your models here.
admin.site.register(Bookmark, BookmarkAdmin)
admin.site.register(Comment, CommentAdmin)