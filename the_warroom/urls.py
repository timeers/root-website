from django.urls import path
# from .views import 
from .views import (GameListView,
                    game_detail_hx_view, game_detail_view, 
                    game_delete_view, game_effort_delete_view,
                    update_game, record_game)

urlpatterns = [
    path('', GameListView.as_view(), name='games-home'),
    path('record/', record_game, name='record-game'),

    path("hx/<int:id>/", game_detail_hx_view, name='game-hx-detail'),

    path("<int:parent_id>/effort/delete/", game_effort_delete_view, name='effort-delete'),
    path("<int:id>/delete/", game_delete_view, name='game-delete'),
    path("<int:id>/edit/", update_game, name='game-update'),
    path("<int:id>/", game_detail_view, name='game-detail'),

    # I don't think I ended up using this one
    # path('create-form/', record_effort, name='create-player'),
]
