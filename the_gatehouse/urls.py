from django.urls import path
from the_keep.views import list_view
from .views import (profile, player_page_view, 
                    designer_component_view, post_bookmarks, game_bookmarks, game_list, onboard_user, player_stats, artist_component_view)

urlpatterns = [
    
    path('<slug:slug>/', player_page_view, name='player-detail'),
    path('<slug:slug>/stats/', player_stats, name='player-stats'),
    path('<slug:slug>/creations/', list_view, name='player-creations'),
    path('<slug:slug>/component-list/', designer_component_view, name='designer-components'),
    path('<slug:slug>/artwork/', artist_component_view, name='artist-components'),
    path('<slug:slug>/game-list/', game_list, name='player-games'),
    path('<slug:slug>/post-bookmarks/', post_bookmarks, name='post-bookmarks'),
    path('<slug:slug>/game-bookmarks/', game_bookmarks, name='game-bookmarks'),
    path('', profile, name='profile'),
]
