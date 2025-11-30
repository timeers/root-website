from django.urls import path
from the_keep.views import list_view
from .views import (user_settings, player_page_view, 
                    designer_component_view, post_bookmarks, game_bookmarks,
                    player_stats, artist_component_view, manage_user,
                    ProfileListView, user_bookmarks, french_root_invite, bug_report,
                    status_check, general_feedback, post_feedback, player_feedback, law_feedback, faq_feedback,
                    game_feedback, weird_root_invite, post_request, trigger_error, trigger_other_error, 
                    admin_dashboard, sync_discord_avatar, join_discord_server)
from the_warroom.views import player_game_list_view  #PlayerGameListView
urlpatterns = [
    # path("", list_view, name='home'),
    # path("home/", list_view),
    path('status/', status_check, name='status_check'),

    path('admin/', admin_dashboard, name='admin-dashboard'),

    path('profile/', player_page_view, name='profile'),
    path('profile/<slug:slug>/', player_page_view, name='player-detail'),

    path('profile/<slug:slug>/stats/', player_stats, name='player-stats'),
    path('profile/<slug:slug>/creations/', list_view, name='player-creations'),
    path('profile/<slug:slug>/component-list/', designer_component_view, name='designer-components'),
    path('profile/<slug:slug>/artwork/', artist_component_view, name='artist-components'),
    # path('profile/<slug:slug>/games/', PlayerGameListView.as_view(), name='player-games'),
    path('profile/<slug:slug>/games/', player_game_list_view, name='player-games'),
    path('profile/<slug:slug>/post-bookmarks/', post_bookmarks, name='post-bookmarks'),
    path('profile/<slug:slug>/game-bookmarks/', game_bookmarks, name='game-bookmarks'),
    path('settings/', user_settings, name='user-settings'),
    path('settings/sync-avatar/', sync_discord_avatar, name='sync-avatar'),

    # Admin
    path('profile/<slug:slug>/manage/', manage_user, name='manage-user'),
    path('profiles/', ProfileListView.as_view(), name='players-list'),

    # Feedback
    path('request/', post_request, name='post-request'),
    path('feedback/', general_feedback, name='general-feedback'),
    path('feedback/bug-report', bug_report, name='bug-report'),
    path('feedback/post/<slug:slug>/', post_feedback, name='post-feedback'),
    path('feedback/profile/<slug:slug>/', player_feedback, name='player-feedback'),
    path('feedback/game/<int:id>/', game_feedback, name='game-feedback'),
    path('feedback/law/<slug:slug>/<str:lang_code>/', law_feedback, name='law-feedback'),
    path('feedback/faq/', faq_feedback, name='faq-feedback'),
    path('feedback/faq/<slug:slug>/', faq_feedback, name='post-faq-feedback'),
    path('feedback/request-invite/', weird_root_invite, name='generic-weird-root-invite'),
    path('feedback/request-invite/<slug:slug>/', weird_root_invite, name='weird-root-invite'),
    path('feedback/french-invite/', french_root_invite, name='generic-french-root-invite'),
    path('feedback/french-invite/<slug:slug>/', french_root_invite, name='french-root-invite'),
    path('bookmarks/', user_bookmarks, name='user-bookmarks'),

    path('join-discord-server/', join_discord_server, name='join-discord-server'),

    # path('fake-error/', trigger_error),
    # path('test-error/', trigger_other_error),
]
