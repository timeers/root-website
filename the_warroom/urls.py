from django.urls import path
# from .views import 
from .views import (GameListView, GameListViewHX,
                    game_detail_hx_view, game_detail_view, 
                    game_delete_view, effort_hx_delete, game_hx_delete,
                    bookmark_game, manage_game, scorecard_manage_view, scorecard_detail_view,
                    scorecard_assign_view, scorecard_delete_view, scorecard_list_view,
                    tournament_detail_view, round_detail_view,
                    TournamentCreateView, TournamentUpdateView, TournamentDeleteView, tournaments_home,
                    round_manage_view, tournament_manage_players, tournament_manage_assets,
                    round_manage_players, RoundDeleteView,
                    tournament_players_pagination, round_players_pagination, round_games_pagination)
from the_tavern.views import game_comment_delete

urlpatterns = [
    path('record/game/', manage_game, name='record-game'),
    path("game/<int:id>/delete/", game_delete_view, name='game-delete'),
    path("game/<int:id>/edit/", manage_game, name='game-update'),
    path("game/<int:id>/", game_detail_view, name='game-detail'),

    path('games/', GameListView.as_view(), name='games-home'),

    path('record/scorecard/', scorecard_manage_view, name='record-scorecard'),
    path("scorecard/<int:id>/", scorecard_detail_view, name='detail-scorecard'),
    path("scorecard/<int:id>/edit", scorecard_manage_view, name='update-scorecard'),
    path("scorecard/<int:id>/delete", scorecard_delete_view, name='delete-scorecard'),
    path('scorecard/assign/<int:id>/', scorecard_assign_view, name='assign-scorecard'),
    path('scorecard/list/', scorecard_list_view, name='list-scorecard'),

    path('new/tournament/', TournamentCreateView.as_view(), name='tournament-create'),
    path('tournament/<slug:tournament_slug>/', tournament_detail_view, name='tournament-detail'),
    path('tournament/<slug:slug>/update/', TournamentUpdateView.as_view(), name='tournament-update'),
    path('tournament/<slug:slug>/delete/', TournamentDeleteView.as_view(), name='tournament-delete'),
    path('tournament/<slug:tournament_slug>/manage-players/', tournament_manage_players, name='tournament-players'),
    path('tournament/<slug:tournament_slug>/manage-assets/', tournament_manage_assets, name='tournament-assets'),

    path('hx/tournament/<int:id>/player-list/', tournament_players_pagination, name='tournament-players-pagination'),
    path('hx/round/<int:id>/player-list/', round_players_pagination, name='round-players-pagination'),
    path('hx/round/<int:id>/game-list/', round_games_pagination, name='round-games-pagination'),

    path('tournament/<slug:tournament_slug>/new/round/', round_manage_view, name='round-create'),
    path('tournament/<slug:tournament_slug>/round/<slug:round_slug>/', round_detail_view, name='round-detail'),
    path('tournament/<slug:tournament_slug>/round/<slug:round_slug>/manage-players/', round_manage_players, name='round-players'),
    path('tournament/<slug:tournament_slug>/round/<slug:round_slug>/update/', round_manage_view, name='round-update'),
    path('tournament/<slug:tournament_slug>/round/<slug:round_slug>/delete/<int:pk>/', RoundDeleteView.as_view(), name='round-delete'),

    path('tournaments/', tournaments_home, name='tournaments-home'),

    path('hx/games/games-listview', GameListViewHX.as_view(), name='hx-game-list'),
    # path("hx/games/<int:id>/", game_detail_hx_view, name='game-hx-detail'),
    path("hx/games/<int:id>/bookmark/", bookmark_game, name='bookmark-game'),
    path("hx/games/effort/delete/<int:id>/", effort_hx_delete, name='effort-hx-delete'),
    path("hx/games/game/delete/<int:id>/", game_hx_delete, name='game-hx-delete'),

]
