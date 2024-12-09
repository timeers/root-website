from django.urls import path
# from .views import 
from .views import (GameListView, GameListViewHX,
                    game_detail_hx_view, game_detail_view, 
                    game_delete_view, effort_hx_delete, game_hx_delete,
                    bookmark_game, manage_game, scorecard_manage_view, scorecard_detail_view,
                    scorecard_assign_view, scorecard_delete_view, scorecard_list_view)
from the_tavern.views import game_comment_delete

urlpatterns = [
    path('', GameListView.as_view(), name='games-home'),
    path('hx/games-listview', GameListViewHX.as_view(), name='hx-game-list'),
    path('record/', manage_game, name='record-game'),

    path("hx/<int:id>/", game_detail_hx_view, name='game-hx-detail'),
    path("hx/effort/delete/<int:id>/", effort_hx_delete, name='effort-hx-delete'),
    path("hx/game/delete/<int:id>/", game_hx_delete, name='game-hx-delete'),

    path("<int:id>/delete/", game_delete_view, name='game-delete'),
    path("<int:id>/edit/", manage_game, name='game-update'),
    path("<int:id>/", game_detail_view, name='game-detail'),
    path("<int:id>/bookmark/", bookmark_game, name='bookmark-game'),

    path('scorecard/record/', scorecard_manage_view, name='record-scorecard'),
    path("scorecard/<int:id>/", scorecard_detail_view, name='detail-scorecard'),
    path("scorecard/<int:id>/edit", scorecard_manage_view, name='update-scorecard'),
    path("scorecard/<int:id>/delete", scorecard_delete_view, name='delete-scorecard'),
    path('scorecard/assign/<int:id>/', scorecard_assign_view, name='assign-scorecard'),
    path('scorecard/list/', scorecard_list_view, name='list-scorecard'),
]
