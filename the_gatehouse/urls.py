from django.urls import path
from the_keep.views import list_view
from .views import profile, bookmark_player, player_page_view

urlpatterns = [
    path("<int:id>/bookmark/", bookmark_player, name='bookmark-player'),
    path('<slug:slug>/', player_page_view, name='player-detail'),
    path('<slug:slug>/creations/', list_view, name='player-creations'),
    path('', profile, name='profile'),
]
