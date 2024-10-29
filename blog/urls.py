from django.urls import path
from .views import (
    PostListView, 
    PostDetailView, 
    PostCreateView, 
    PostUpdateView,

    MapDetailView, DeckDetailView, HirelingDetailView, VagabondDetailView, LandmarkDetailView, FactionDetailView,
    MapCreateView, DeckCreateView, HirelingCreateView, VagabondCreateView, LandmarkCreateView, FactionCreateView,
    MapUpdateView, DeckUpdateView, HirelingUpdateView, VagabondUpdateView, LandmarkUpdateView, FactionUpdateView,

    PostDeleteView,
    UserPostListView,
    SearchPostListView,
    ArtistPostListView
)
from . import views

urlpatterns = [
    path('', PostListView.as_view(), name='blog-home'),
    path('user/<str:username>/art/', ArtistPostListView.as_view(), name='artist-posts'),
    path('user/<str:username>/', UserPostListView.as_view(), name='user-posts'),
    path('posts/<str:search_term>/', SearchPostListView.as_view(), name='search-posts'),

    path('post/new/', PostCreateView.as_view(), name='post-create'),

    path('faction/new/', FactionCreateView.as_view(), name='faction-create'),
    path('map/new/', MapCreateView.as_view(), name='map-create'),
    path('deck/new/', DeckCreateView.as_view(), name='deck-create'),
    path('hireling/new/', HirelingCreateView.as_view(), name='hireling-create'),
    path('landmark/new/', LandmarkCreateView.as_view(), name='landmark-create'),
    path('vagabond/new/', VagabondCreateView.as_view(), name='vagabond-create'),

    path('post/<int:pk>/', PostDetailView.as_view(), name='post-detail'),

    path('map/<slug:slug>/', MapDetailView.as_view(), name='map-detail'),
    path('deck/<slug:slug>/', DeckDetailView.as_view(), name='deck-detail'),
    path('hireling/<slug:slug>/', HirelingDetailView.as_view(), name='hireling-detail'),
    path('landmark/<slug:slug>/', LandmarkDetailView.as_view(), name='landmark-detail'),
    path('vagabond/<slug:slug>/', VagabondDetailView.as_view(), name='vagabond-detail'),
    path('faction/<slug:slug>/', FactionDetailView.as_view(), name='faction-detail'),


    path('post/<int:pk>/update/', PostUpdateView.as_view(), name='post-update'),

    path('map/<slug:slug>/update/', MapUpdateView.as_view(), name='map-update'),
    path('deck/<slug:slug>/update/', DeckUpdateView.as_view(), name='deck-update'),
    path('hireling/<slug:slug>/update/', HirelingUpdateView.as_view(), name='hireling-update'),
    path('landmark/<slug:slug>/update/', LandmarkUpdateView.as_view(), name='landmark-update'),
    path('vagabond/<slug:slug>/update/', VagabondUpdateView.as_view(), name='vagabond-update'),
    path('faction/<slug:slug>/update/', FactionUpdateView.as_view(), name='faction-update'),


    path('post/<int:pk>/delete/', PostDeleteView.as_view(), name='post-delete'),
    path('about/', views.about, name='blog-about'),
    path('test/', views.test, name='blog-test'),
]
