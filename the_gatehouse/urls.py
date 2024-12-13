from django.urls import path
from the_keep.views import list_view
from .views import profile, bookmark_player, player_page_view, designer_component_view, post_bookmarks, game_bookmarks

urlpatterns = [
    path("<int:id>/bookmark/", bookmark_player, name='bookmark-player'),
    path('<slug:slug>/', player_page_view, name='player-detail'),
    path('<slug:slug>/creations/', list_view, name='player-creations'),
    path('<slug:slug>/component-list/', designer_component_view, name='designer-components'),
    path('<slug:slug>/game-list/', player_page_view, name='player-games'),
    path('<slug:slug>/post-bookmarks/', post_bookmarks, name='post-bookmarks'),
    path('<slug:slug>/game-bookmarks/', game_bookmarks, name='game-bookmarks'),
    path('', profile, name='profile'),
]
