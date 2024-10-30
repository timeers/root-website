from django.urls import path
# from .views import 
from . import views
from .views import GameListView

urlpatterns = [
    path('games/', GameListView.as_view(), name='games-home'),
    path('record-game/', views.record_game, name='record_game'),
    path('create-form/', views.record_effort, name='create-player'),
]
