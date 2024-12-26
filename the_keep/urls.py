from django.urls import path
from .views import (
    ExpansionDetailView,

    MapCreateView, DeckCreateView, HirelingCreateView, VagabondCreateView, 
    LandmarkCreateView, FactionCreateView, ExpansionCreateView, ClockworkCreateView,
    MapUpdateView, DeckUpdateView, HirelingUpdateView, VagabondUpdateView, 
    LandmarkUpdateView, FactionUpdateView, ExpansionUpdateView, ClockworkUpdateView,
    ExpansionDeleteView,
    PostDeleteView,
 

    # ComponentDetailListView,
    bookmark_post,
    list_view, activity_list,
    search_view,
    add_piece, delete_piece,
    ultimate_component_view,
)
from . import views

urlpatterns = [
    # path('', PostListView.as_view(), name='keep-home'),
    path("", list_view, name='keep-home'),
    # path("new/", activity_list, name='activity-list'),
 
 

    path("search/", search_view, name='search'),

    path('new/faction/', FactionCreateView.as_view(), name='faction-create'),
    path('new/clockwork/', ClockworkCreateView.as_view(), name='clockwork-create'),
    path('new/map/', MapCreateView.as_view(), name='map-create'),
    path('new/deck/', DeckCreateView.as_view(), name='deck-create'),
    path('new/hireling/', HirelingCreateView.as_view(), name='hireling-create'),
    path('new/landmark/', LandmarkCreateView.as_view(), name='landmark-create'),
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
    path('vagabond/<slug:slug>/', ultimate_component_view, name='vagabond-detail'),
    path('faction/<slug:slug>/', ultimate_component_view, name='faction-detail'),
    path('clockwork/<slug:slug>/', ultimate_component_view, name='clockwork-detail'),

    path('map/<slug:slug>/update/', MapUpdateView.as_view(), name='map-update'),
    path('deck/<slug:slug>/update/', DeckUpdateView.as_view(), name='deck-update'),
    path('hireling/<slug:slug>/update/', HirelingUpdateView.as_view(), name='hireling-update'),
    path('landmark/<slug:slug>/update/', LandmarkUpdateView.as_view(), name='landmark-update'),
    path('vagabond/<slug:slug>/update/', VagabondUpdateView.as_view(), name='vagabond-update'),
    path('faction/<slug:slug>/update/', FactionUpdateView.as_view(), name='faction-update'),
    path('clockwork/<slug:slug>/update/', ClockworkUpdateView.as_view(), name='clockwork-update'),

    path("post/<int:id>/bookmark/", bookmark_post, name='bookmark-post'),

    path('post/<int:pk>/delete/', PostDeleteView.as_view(), name='post-delete'),
    path('about/', views.about, name='keep-about'),
    path('piece/add/', add_piece, name='add-piece'),
    path('piece/update/<int:id>', add_piece, name='update-piece'),
    path('piece/delete/<int:id>', delete_piece, name='delete-piece'),
]
