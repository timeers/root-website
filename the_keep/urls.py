from django.urls import path
from .views import (
    PostListView, 
    PostUpdateView,
    ExpansionDetailView,

    MapCreateView, DeckCreateView, HirelingCreateView, VagabondCreateView, LandmarkCreateView, FactionCreateView,
    MapUpdateView, DeckUpdateView, HirelingUpdateView, VagabondUpdateView, LandmarkUpdateView, FactionUpdateView,

    PostDeleteView,
    UserPostListView,
    ArtistPostListView, 

    ComponentDetailListView,
    bookmark_post,
    list_view,
    search_view,
    # component_detail_view,
)
from . import views

urlpatterns = [
    # path('', PostListView.as_view(), name='keep-home'),
    path("", list_view, name='keep-home'),
    path('user/<slug:slug>/art/', ArtistPostListView.as_view(), name='artist-posts'),
    path('user/<slug:slug>/', UserPostListView.as_view(), name='user-posts'),

    path("search/", search_view, name='search'),

    path('faction/new/', FactionCreateView.as_view(), name='faction-create'),
    path('map/new/', MapCreateView.as_view(), name='map-create'),
    path('deck/new/', DeckCreateView.as_view(), name='deck-create'),
    path('hireling/new/', HirelingCreateView.as_view(), name='hireling-create'),
    path('landmark/new/', LandmarkCreateView.as_view(), name='landmark-create'),
    path('vagabond/new/', VagabondCreateView.as_view(), name='vagabond-create'),
    
    path('expansion/<slug:slug>/', ExpansionDetailView.as_view(), name='expansion-detail'),

    path('map/<slug:slug>/', ComponentDetailListView.as_view(), name='map-detail'),
    path('deck/<slug:slug>/', ComponentDetailListView.as_view(), name='deck-detail'),
    path('hireling/<slug:slug>/', ComponentDetailListView.as_view(), name='hireling-detail'),
    path('landmark/<slug:slug>/', ComponentDetailListView.as_view(), name='landmark-detail'),
    path('vagabond/<slug:slug>/', ComponentDetailListView.as_view(), name='vagabond-detail'),
    path('faction/<slug:slug>/', ComponentDetailListView.as_view(), name='faction-detail'),

    path('post/<int:pk>/update/', PostUpdateView.as_view(), name='post-update'),

    path('map/<slug:slug>/update/', MapUpdateView.as_view(), name='map-update'),
    path('deck/<slug:slug>/update/', DeckUpdateView.as_view(), name='deck-update'),
    path('hireling/<slug:slug>/update/', HirelingUpdateView.as_view(), name='hireling-update'),
    path('landmark/<slug:slug>/update/', LandmarkUpdateView.as_view(), name='landmark-update'),
    path('vagabond/<slug:slug>/update/', VagabondUpdateView.as_view(), name='vagabond-update'),
    path('faction/<slug:slug>/update/', FactionUpdateView.as_view(), name='faction-update'),

    path("post/<int:id>/bookmark/", bookmark_post, name='bookmark-post'),

    path('post/<int:pk>/delete/', PostDeleteView.as_view(), name='post-delete'),
    path('about/', views.about, name='keep-about'),
]
