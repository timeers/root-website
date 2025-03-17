from django.urls import path
from .views import (
    ExpansionDetailView,

    MapCreateView, DeckCreateView, HirelingCreateView, VagabondCreateView, 
    LandmarkCreateView, FactionCreateView, ExpansionCreateView, ClockworkCreateView,
    MapUpdateView, DeckUpdateView, HirelingUpdateView, VagabondUpdateView, 
    LandmarkUpdateView, FactionUpdateView, ExpansionUpdateView, ClockworkUpdateView,
    ExpansionDeleteView,
    PostDeleteView,
    TweakCreateView, TweakUpdateView,

    PNPAssetCreateView, PNPAssetListView, PNPAssetUpdateView, PNPAssetDeleteView,

    # ComponentDetailListView,
    bookmark_post,
    list_view, activity_list,
    search_view,
    add_piece, delete_piece,
    ultimate_component_view,
    component_games,
    new_components,

    confirm_stable,
    confirm_testing,
    pin_asset,
    universal_search,
)
from .api_views import search_posts
from . import views

urlpatterns = [
    path("", list_view, name='keep-home'),
    path("home/", list_view, name='keep-home'),
    # path("new/", activity_list, name='activity-list'),
 
    path("search/", search_view, name='search'),
    path('universal-search/', universal_search, name='universal-search'),
    path('api/search/', search_posts, name='api-search-posts'),

    path('new/', new_components, name='new-components'),
    path('new/faction/', FactionCreateView.as_view(), name='faction-create'),
    path('new/clockwork/', ClockworkCreateView.as_view(), name='clockwork-create'),
    path('new/map/', MapCreateView.as_view(), name='map-create'),
    path('new/deck/', DeckCreateView.as_view(), name='deck-create'),
    path('new/hireling/', HirelingCreateView.as_view(), name='hireling-create'),
    path('new/landmark/', LandmarkCreateView.as_view(), name='landmark-create'),
    path('new/tweak/', TweakCreateView.as_view(), name='tweak-create'),
    path('new/vagabond/', VagabondCreateView.as_view(), name='vagabond-create'),
    path('new/expansion/', ExpansionCreateView.as_view(), name='expansion-create'),
    
    path('expansion/<slug:slug>/', ExpansionDetailView.as_view(), name='expansion-detail'),
    path('expansion/<slug:slug>/factions/', ExpansionDetailView.as_view(), name='expansion-factions'),
    path('expansion/<slug:slug>/update/', ExpansionUpdateView.as_view(), name='expansion-update'),
    path('expansion/<slug:slug>/delete/', ExpansionDeleteView.as_view(), name='expansion-delete'),

    # path('old/faction/<slug:slug>/', ComponentDetailListView.as_view(), name='faction-old'),
    
    path('map/<slug:slug>/', ultimate_component_view, name='map-detail'),
    path('deck/<slug:slug>/', ultimate_component_view, name='deck-detail'),
    path('hireling/<slug:slug>/', ultimate_component_view, name='hireling-detail'),
    path('landmark/<slug:slug>/', ultimate_component_view, name='landmark-detail'),
    path('tweak/<slug:slug>/', ultimate_component_view, name='tweak-detail'),
    path('vagabond/<slug:slug>/', ultimate_component_view, name='vagabond-detail'),
    path('faction/<slug:slug>/', ultimate_component_view, name='faction-detail'),
    path('clockwork/<slug:slug>/', ultimate_component_view, name='clockwork-detail'),
    # Games
    path('map/<slug:slug>/games/', component_games, name='map-games'),
    path('deck/<slug:slug>/games/', component_games, name='deck-games'),
    path('hireling/<slug:slug>/games/', component_games, name='hireling-games'),
    path('landmark/<slug:slug>/games/', component_games, name='landmark-games'),
    path('tweak/<slug:slug>/games/', component_games, name='tweak-games'),
    path('vagabond/<slug:slug>/games/', component_games, name='vagabond-games'),
    path('faction/<slug:slug>/games/', component_games, name='faction-games'),
    path('clockwork/<slug:slug>/games/', component_games, name='clockwork-games'),

    path('map/<slug:slug>/update/', MapUpdateView.as_view(), name='map-update'),
    path('deck/<slug:slug>/update/', DeckUpdateView.as_view(), name='deck-update'),
    path('hireling/<slug:slug>/update/', HirelingUpdateView.as_view(), name='hireling-update'),
    path('landmark/<slug:slug>/update/', LandmarkUpdateView.as_view(), name='landmark-update'),
    path('tweak/<slug:slug>/update/', TweakUpdateView.as_view(), name='tweak-update'),
    path('vagabond/<slug:slug>/update/', VagabondUpdateView.as_view(), name='vagabond-update'),
    path('faction/<slug:slug>/update/', FactionUpdateView.as_view(), name='faction-update'),
    path('clockwork/<slug:slug>/update/', ClockworkUpdateView.as_view(), name='clockwork-update'),

    path("post/<int:id>/bookmark/", bookmark_post, name='bookmark-post'),
    path("post/<slug:slug>/stable/", confirm_stable, name='confirm-stable'),
    path("post/<slug:slug>/testing/", confirm_testing, name='confirm-testing'),

    path('post/<int:pk>/delete/', PostDeleteView.as_view(), name='post-delete'),
    path('about/', views.about, name='keep-about'),
    path('newhome/', views.home, name='site-home'),

    path('piece/add/', add_piece, name='add-piece'),
    path('piece/update/<int:id>', add_piece, name='update-piece'),
    path('piece/delete/<int:id>', delete_piece, name='delete-piece'),

    path('resources/', PNPAssetListView.as_view(), name='asset-list'),
    path('resources/new/', PNPAssetCreateView.as_view(), name='asset-new'),
    path('resources/update/<int:pk>/', PNPAssetUpdateView.as_view(), name='asset-update'),
    path('resources/delete/<int:pk>/', PNPAssetDeleteView.as_view(), name='asset-delete'),
    path('resources/player/<slug:slug>/', PNPAssetListView.as_view(), name='asset-player'),
    path('resources/pin/<int:id>/', pin_asset, name='pin-asset')
]
