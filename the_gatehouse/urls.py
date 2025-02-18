from django.urls import path
from the_keep.views import list_view
from .views import (profile, player_page_view, 
                    designer_component_view, post_bookmarks, game_bookmarks, player_games, 
                    onboard_user, player_stats, artist_component_view, manage_user,
                    ProfileListView, discord_feedback)

urlpatterns = [
    
    path('profile/<slug:slug>/', player_page_view, name='player-detail'),
    path('profile/<slug:slug>/manage/', manage_user, name='manage-user'),
    path('profile/<slug:slug>/stats/', player_stats, name='player-stats'),
    path('profile/<slug:slug>/creations/', list_view, name='player-creations'),
    path('profile/<slug:slug>/component-list/', designer_component_view, name='designer-components'),
    path('profile/<slug:slug>/artwork/', artist_component_view, name='artist-components'),
    path('profile/<slug:slug>/game-list/', player_games, name='player-games'),
    path('profile/<slug:slug>/post-bookmarks/', post_bookmarks, name='post-bookmarks'),
    path('profile/<slug:slug>/game-bookmarks/', game_bookmarks, name='game-bookmarks'),
    path('profile/', profile, name='profile'),
    path('profiles/', ProfileListView.as_view(), name='players-list'),
    path('feedback/', discord_feedback, name='discord-feedback')
]
