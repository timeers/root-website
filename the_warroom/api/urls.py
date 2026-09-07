from django.urls import path
from .views import (ScoreCardDetailView, FactionAverageTurnScoreView, AverageTurnScoreView,
                    GameScorecardView, PlayerScorecardView, GameListView,
                    BoxScoreUploadView)

urlpatterns = [
    path('scorecard/detail/<int:pk>/', ScoreCardDetailView.as_view(), name='api-scorecard-detail'),
    path('scorecard/game/<int:pk>/', GameScorecardView.as_view(), name='scorecard-game'),
    path('scorecard/faction/<slug:slug>/', FactionAverageTurnScoreView.as_view(), name='api-scorecard-faction'),
    path('scorecard/average/', AverageTurnScoreView.as_view(), name='api-scorecard-all'),
    path('scorecard/player/<slug:slug>/', PlayerScorecardView.as_view(), name='api-scorecard-player'),
    path('games/', GameListView.as_view(), name='api-game-list'),
    # Write endpoint: a Tabletop Simulator object posting a box score with a
    # one-time token from /boxscore token.
    path('boxscore/upload/', BoxScoreUploadView.as_view(), name='api-boxscore-upload'),

]