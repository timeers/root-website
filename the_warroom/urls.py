from django.urls import path
# from .views import
from .views import (GameUpdateView, game_list_view, leaderboard_view,
                    game_detail_view,
                    game_delete_view, effort_hx_delete, game_hx_delete,
                    bookmark_game, manage_game, scorecard_detail_view,
                    scorecard_assign_view, scorecard_delete_view, scorecard_list_view, effort_assign_view,
                    scorecard_manage_view,
                    tournament_detail_view, round_detail_view,
                    TournamentDeleteView, tournaments_home,
                    tournament_dynamic_create, tournament_dynamic_update,
                    tournament_search_players, tournament_move_player,
                    tournament_search_assets, tournament_add_asset, tournament_remove_asset,
                    tournament_manage_players, tournament_manage_assets_v2,
                    tournament_settings_hub, round_settings_hub,
                    round_manage_view,
                    round_manage_players, round_search_players, round_move_player, RoundDeleteView,
                    tournament_players_pagination, round_players_pagination, round_games_pagination,
                    in_progress_view, round_component_leaderboard_view,
                    add_player_to_effort, my_submitted_games_view,
                    # Round grouping views
                    round_grouping_setup_view, round_grouping_status,
                    round_grouping_move_player, round_grouping_add_to_group,
                    round_grouping_remove_from_group, round_grouping_create_group,
                    round_grouping_delete_group, round_grouping_move_from_waitlist,
                    round_grouping_finalize)

urlpatterns = [
    path('record/game/', manage_game, name='record-game'),
    path("rootleague/match/<int:league_id>/", game_detail_view, name='rdl-game-detail'),
    path("game/<int:id>/delete/", game_delete_view, name='game-delete'),
    path("game/<int:id>/edit/", manage_game, name='game-update'),
    path("game/<int:pk>/update/", GameUpdateView.as_view(), name='game-update-info'),
    path("game/<int:id>/", game_detail_view, name='game-detail'),
    

    path('battlefield/active/', in_progress_view, name='in-progress'),
    path('battlefield/my-games/', my_submitted_games_view, name='my-submitted-games'),
    path('battlefield/', game_list_view, name='games-home'),
    path('warroom/', game_list_view),
    path('games/', game_list_view),

    path('leaderboard/', leaderboard_view, name='leaderboard-view'),

    path('record/scorecard/', scorecard_manage_view, name='record-scorecard'),
    path("scorecard/<int:id>/edit/", scorecard_manage_view, name='update-scorecard'),
    # path('record/old-scorecard/', scorecard_old_manage_view, name='record-old-scorecard'),
    path("scorecard/<int:id>/", scorecard_detail_view, name='detail-scorecard'),
    # path("scorecard/<int:id>/old-edit", scorecard_old_manage_view, name='update-old-scorecard'),
    path("scorecard/<int:id>/delete", scorecard_delete_view, name='delete-scorecard'),
    path('scorecard/assign/<int:id>/', scorecard_assign_view, name='assign-scorecard'),
    path('scorecard/<int:id>/assign/', effort_assign_view, name='assign-effort'),
    path('scorecards/', scorecard_list_view, name='scorecard-home'),

    # New dynamic tournament views with HTMX support
    path('new/series/', tournament_dynamic_create, name='tournament-dynamic-create'),
    path('series/<slug:slug>/update/', tournament_dynamic_update, name='tournament-dynamic-update'),

    # HTMX endpoints for player management
    path('series/<slug:slug>/players/search/', tournament_search_players, name='tournament-search-players'),
    path('series/<slug:slug>/players/move/', tournament_move_player, name='tournament-move-player'),

    # HTMX endpoints for asset management
    path('series/<slug:slug>/assets/<str:asset_type>/search/', tournament_search_assets, name='tournament-search-assets'),
    path('series/<slug:slug>/assets/<str:asset_type>/<int:asset_id>/add/', tournament_add_asset, name='tournament-add-asset'),
    path('series/<slug:slug>/assets/<str:asset_type>/<int:asset_id>/remove/', tournament_remove_asset, name='tournament-remove-asset'),

    # New dedicated management pages (v2)
    path('series/<slug:slug>/settings/', tournament_settings_hub, name='tournament-settings'),
    path('series/<slug:slug>/players/', tournament_manage_players, name='tournament-manage-players'),
    path('series/<slug:slug>/assets/', tournament_manage_assets_v2, name='tournament-manage-assets'),

    path('series/<slug:tournament_slug>/', tournament_detail_view, name='tournament-detail'),
    path('series/<slug:slug>/delete/', TournamentDeleteView.as_view(), name='tournament-delete'),

    path('hx/series/<int:id>/player-list/', tournament_players_pagination, name='tournament-players-pagination'),
    path('hx/round/<int:id>/player-list/', round_players_pagination, name='round-players-pagination'),
    path('hx/round/<int:id>/game-list/', round_games_pagination, name='round-games-pagination'),
    path('add-player-to-effort/', add_player_to_effort, name='add-player-to-effort'),

    path('series/<slug:tournament_slug>/new/round/', round_manage_view, name='round-create'),
    path('series/<slug:tournament_slug>/round/<slug:round_slug>/', round_detail_view, name='round-detail'),
    path('series/<slug:tournament_slug>/round/<slug:round_slug>/settings/', round_settings_hub, name='round-settings'),
    path('series/<slug:tournament_slug>/round/<slug:round_slug>/players/', round_manage_players, name='round-manage-players'),
    path('series/<slug:tournament_slug>/round/<slug:round_slug>/players/search/', round_search_players, name='round-search-players'),
    path('series/<slug:tournament_slug>/round/<slug:round_slug>/players/move/', round_move_player, name='round-move-player'),
    path('series/<slug:tournament_slug>/round/<slug:round_slug>/update/', round_manage_view, name='round-update'),
    path('series/<slug:tournament_slug>/round/<slug:round_slug>/delete/<int:pk>/', RoundDeleteView.as_view(), name='round-delete'),

    # Round grouping
    path('series/<slug:tournament_slug>/round/<slug:round_slug>/grouping/', round_grouping_setup_view, name='round-grouping-setup'),
    path('series/<slug:tournament_slug>/round/<slug:round_slug>/grouping/<int:session_id>/status/', round_grouping_status, name='round-grouping-status'),
    path('series/<slug:tournament_slug>/round/<slug:round_slug>/grouping/<int:session_id>/move-player/', round_grouping_move_player, name='round-grouping-move-player'),
    path('series/<slug:tournament_slug>/round/<slug:round_slug>/grouping/<int:session_id>/add-to-group/', round_grouping_add_to_group, name='round-grouping-add-to-group'),
    path('series/<slug:tournament_slug>/round/<slug:round_slug>/grouping/<int:session_id>/remove-from-group/', round_grouping_remove_from_group, name='round-grouping-remove-from-group'),
    path('series/<slug:tournament_slug>/round/<slug:round_slug>/grouping/<int:session_id>/create-group/', round_grouping_create_group, name='round-grouping-create-group'),
    path('series/<slug:tournament_slug>/round/<slug:round_slug>/grouping/<int:session_id>/delete-group/', round_grouping_delete_group, name='round-grouping-delete-group'),
    path('series/<slug:tournament_slug>/round/<slug:round_slug>/grouping/<int:session_id>/move-from-waitlist/', round_grouping_move_from_waitlist, name='round-grouping-move-from-waitlist'),
    path('series/<slug:tournament_slug>/round/<slug:round_slug>/grouping/<int:session_id>/finalize/', round_grouping_finalize, name='round-grouping-finalize'),

    path('series/<slug:tournament_slug>/leaderboard/<slug:post_slug>/', round_component_leaderboard_view, name='tournament-leaderboard'),
    path('series/<slug:tournament_slug>/round/<slug:round_slug>/leaderboard/<slug:post_slug>/', round_component_leaderboard_view, name='round-leaderboard'),


    path('series/', tournaments_home, name='tournaments-home'),

    # path('hx/games/games-listview', GameListViewHX.as_view(), name='hx-game-list'),
    # path("hx/games/<int:id>/", game_detail_hx_view, name='game-hx-detail'),
    path("hx/games/<int:id>/bookmark/", bookmark_game, name='bookmark-game'),
    path("hx/games/effort/delete/<int:id>/", effort_hx_delete, name='effort-hx-delete'),
    path("hx/games/game/delete/<int:id>/", game_hx_delete, name='game-hx-delete'),

]
