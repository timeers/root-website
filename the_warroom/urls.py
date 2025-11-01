from django.urls import path
# from .views import 
from .views import (GameListView, GameUpdateView, #GameListViewHX,
                    game_detail_hx_view, game_detail_view, 
                    game_delete_view, effort_hx_delete, game_hx_delete,
                    bookmark_game, manage_game, scorecard_manage_view, scorecard_detail_view,
                    scorecard_assign_view, scorecard_delete_view, scorecard_list_view, effort_assign_view,
                    tournament_detail_view, round_detail_view,
                    TournamentCreateView, TournamentUpdateView, TournamentDeleteView, tournaments_home,
                    round_manage_view, tournament_manage_players, tournament_manage_assets,
                    round_manage_players, RoundDeleteView,
                    tournament_players_pagination, round_players_pagination, round_games_pagination,
                    in_progress_view, TournamentDesignerUpdateView, round_component_leaderboard_view)
from the_tavern.views import game_comment_delete

urlpatterns = [
    path('record/game/', manage_game, name='record-game'),
    path("rootleague/match/<int:league_id>/", game_detail_view, name='rdl-game-detail'),
    path("game/<int:id>/delete/", game_delete_view, name='game-delete'),
    path("game/<int:id>/edit/", manage_game, name='game-update'),
    path("game/<int:pk>/update/", GameUpdateView.as_view(), name='game-update-info'),
    path("game/<int:id>/", game_detail_view, name='game-detail'),
    

    path('battlefield/active/', in_progress_view, name='in-progress'),
    path('battlefield/', GameListView.as_view(), name='games-home'),
    path('warroom/', GameListView.as_view()),
    path('games/', GameListView.as_view()),

    path('record/scorecard/', scorecard_manage_view, name='record-scorecard'),
    path("scorecard/<int:id>/", scorecard_detail_view, name='detail-scorecard'),
    path("scorecard/<int:id>/edit", scorecard_manage_view, name='update-scorecard'),
    path("scorecard/<int:id>/delete", scorecard_delete_view, name='delete-scorecard'),
    path('scorecard/assign/<int:id>/', scorecard_assign_view, name='assign-scorecard'),
    path('scorecard/<int:id>/assign/', effort_assign_view, name='assign-effort'),
    path('scorecards/', scorecard_list_view, name='scorecard-home'),

    path('new/series/', TournamentCreateView.as_view(), name='tournament-create'),
    path('series/<slug:tournament_slug>/', tournament_detail_view, name='tournament-detail'),
    path('series/<slug:slug>/update/', TournamentUpdateView.as_view(), name='tournament-update'),
    path('series/<slug:slug>/host-edit/', TournamentDesignerUpdateView.as_view(), name='tournament-designer-update'),
    path('series/<slug:slug>/delete/', TournamentDeleteView.as_view(), name='tournament-delete'),
    path('series/<slug:tournament_slug>/manage-players/', tournament_manage_players, name='tournament-players'),
    path('series/<slug:tournament_slug>/manage-assets/', tournament_manage_assets, name='tournament-assets'),

    path('hx/series/<int:id>/player-list/', tournament_players_pagination, name='tournament-players-pagination'),
    path('hx/round/<int:id>/player-list/', round_players_pagination, name='round-players-pagination'),
    path('hx/round/<int:id>/game-list/', round_games_pagination, name='round-games-pagination'),

    path('series/<slug:tournament_slug>/new/round/', round_manage_view, name='round-create'),
    path('series/<slug:tournament_slug>/round/<slug:round_slug>/', round_detail_view, name='round-detail'),
    path('series/<slug:tournament_slug>/round/<slug:round_slug>/manage-players/', round_manage_players, name='round-players'),
    path('series/<slug:tournament_slug>/round/<slug:round_slug>/update/', round_manage_view, name='round-update'),
    path('series/<slug:tournament_slug>/round/<slug:round_slug>/delete/<int:pk>/', RoundDeleteView.as_view(), name='round-delete'),


    path('series/<slug:tournament_slug>/leaderboard/<slug:post_slug>/', round_component_leaderboard_view, name='tournament-leaderboard'),
    path('series/<slug:tournament_slug>/round/<slug:round_slug>/leaderboard/<slug:post_slug>/', round_component_leaderboard_view, name='round-leaderboard'),


    path('series/', tournaments_home, name='tournaments-home'),

    # path('hx/games/games-listview', GameListViewHX.as_view(), name='hx-game-list'),
    # path("hx/games/<int:id>/", game_detail_hx_view, name='game-hx-detail'),
    path("hx/games/<int:id>/bookmark/", bookmark_game, name='bookmark-game'),
    path("hx/games/effort/delete/<int:id>/", effort_hx_delete, name='effort-hx-delete'),
    path("hx/games/game/delete/<int:id>/", game_hx_delete, name='game-hx-delete'),

]
