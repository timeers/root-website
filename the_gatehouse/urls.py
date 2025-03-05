from django.urls import path
from the_keep.views import list_view
from .views import (user_settings, player_page_view, 
                    designer_component_view, post_bookmarks, game_bookmarks,
                    onboard_user, player_stats, artist_component_view, manage_user,
                    ProfileListView, user_bookmarks,
                    status_check, general_feedback, post_feedback, player_feedback, game_feedback, weird_root_invite)
from the_warroom.views import PlayerGameListView
urlpatterns = [

    path('status/', status_check, name='status_check'),

    path('profile/<slug:slug>/', player_page_view, name='player-detail'),

    path('profile/<slug:slug>/stats/', player_stats, name='player-stats'),
    path('profile/<slug:slug>/creations/', list_view, name='player-creations'),
    path('profile/<slug:slug>/component-list/', designer_component_view, name='designer-components'),
    path('profile/<slug:slug>/artwork/', artist_component_view, name='artist-components'),
    # path('profile/<slug:slug>/game-list/', player_games, name='player-games'),
    path('profile/<slug:slug>/games/', PlayerGameListView.as_view(), name='player-games'),
    path('profile/<slug:slug>/post-bookmarks/', post_bookmarks, name='post-bookmarks'),
    path('profile/<slug:slug>/game-bookmarks/', game_bookmarks, name='game-bookmarks'),
    path('settings/', user_settings, name='user-settings'),

    # Admin
    path('profile/<slug:slug>/manage/', manage_user, name='manage-user'),
    path('profiles/', ProfileListView.as_view(), name='players-list'),

    # Feedback
    path('feedback/', general_feedback, name='general-feedback'),
    path('feedback/post/<slug:slug>/', post_feedback, name='post-feedback'),
    path('feedback/profile/<slug:slug>/', player_feedback, name='player-feedback'),
    path('feedback/game/<int:id>/', game_feedback, name='game-feedback'),
    path('feedback/request-invite/<slug:slug>/', weird_root_invite, name='weird-root-invite'),

    path('bookmarks/', user_bookmarks, name='user-bookmarks'),

]
