from django.urls import path
from .views import ScoreCardDetailView, FactionAverageTurnScoreView, AverageTurnScoreView, GameScorecardView, PlayerScorecardView

urlpatterns = [
    path('scorecard/detail/<int:pk>/', ScoreCardDetailView.as_view(), name='api-scorecard-detail'),
    path('scorecard/game/<int:pk>/', GameScorecardView.as_view(), name='scorecard-game'),
    path('scorecard/faction/<slug:slug>/', FactionAverageTurnScoreView.as_view(), name='api-scorecard-faction'),
    path('scorecard/average/', AverageTurnScoreView.as_view(), name='api-scorecard-all'),
    path('scorecard/player/<slug:slug>/', PlayerScorecardView.as_view(), name='api-scorecard-player'),

]