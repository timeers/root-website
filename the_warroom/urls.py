from django.urls import path
# from .views import 
from . import views

urlpatterns = [
    path('games/', views.GameListView.as_view(), name='games-home'),
    path('games/record/', views.record_game, name='record-game'),
    path("games/<int:id>/", views.game_detail_view, name='game-detail'),
    path("games/<int:id>/edit/", views.update_game, name='game-update'),
    path('create-form/', views.record_effort, name='create-player'),
]
