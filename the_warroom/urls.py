from django.urls import path
# from .views import 
from .views import (GameListView, GameListViewHX,
                    game_detail_hx_view, game_detail_view, 
                    game_delete_view, game_effort_delete_view,
                    bookmark_game, manage_game, delete_effort)
from the_tavern.views import game_comment_delete

urlpatterns = [
    path('', GameListView.as_view(), name='games-home'),
    path('hx/games-listview', GameListViewHX.as_view(), name='hx-game-list'),
    path('record/', manage_game, name='record-game'),

    path("hx/<int:id>/", game_detail_hx_view, name='game-hx-detail'),
    path("effort/hx/<int:id>/", delete_effort, name='effort-delete'),
    # path("hx/effort/<int:id>/", effort_update_hx_view, name='effort-hx-update'),
    # path("create-effort/", create_effort, name='create-effort'),

    path("<int:parent_id>/effort/delete/", game_effort_delete_view, name='effort-delete'),
    path("<int:id>/delete/", game_delete_view, name='game-delete'),
    path("<int:id>/edit/", manage_game, name='game-update'),
    path("<int:id>/", game_detail_view, name='game-detail'),
    path("<int:id>/bookmark/", bookmark_game, name='bookmark-game'),
]
