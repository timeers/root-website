from django.contrib import admin
from .models import PostComment, GameComment


class PostCommentAdmin(admin.ModelAdmin):
    list_display = ('__str__', 'post', 'post__component', 'public')
    search_fields = ['post__title', 'player__discord', 'player__dwd', 'player__display_name', 'body']

class GameCommentAdmin(admin.ModelAdmin):
    list_display = ('__str__', 'public')
    search_fields = ['player__discord', 'player__dwd', 'player__display_name', 'body']    

# Register your models here.


admin.site.register(PostComment, PostCommentAdmin)
admin.site.register(GameComment, GameCommentAdmin)