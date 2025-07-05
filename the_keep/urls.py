from django.urls import path
from .views import (
    MapCreateView, DeckCreateView, HirelingCreateView, VagabondCreateView, 
    LandmarkCreateView, FactionCreateView, ExpansionCreateView, ClockworkCreateView,
    MapUpdateView, DeckUpdateView, HirelingUpdateView, VagabondUpdateView, 
    LandmarkUpdateView, FactionUpdateView, ExpansionUpdateView, ClockworkUpdateView,
    ExpansionDeleteView,
    PostDeleteView,
    TweakCreateView, TweakUpdateView,

    PNPAssetCreateView, PNPAssetListView, PNPAssetUpdateView, PNPAssetDeleteView, PNPAssetDetailView,

    # ComponentDetailListView,
    bookmark_post,
    list_view,
    search_view,
    add_piece, delete_piece,
    ultimate_component_view,
    component_games,
    new_components,

    confirm_stable,
    confirm_testing,
    pin_asset,
    universal_search,
    # color_match_view,
    color_group_view,
    animal_match_view,
    status_check,
    translations_view, create_post_translation,
    expansion_detail_view
)
from .api_views import search_posts
from . import views

urlpatterns = [
    path('', views.home, name='site-home'),
    path('home/', views.home),
    path('about/', views.about, name='keep-about'),

    # Laws
    path('law-of-root/', views.law_table_of_contents),
    path('law-of-root/<str:lang_code>/', views.law_table_of_contents, name='law-of-root'),
    path('law-of-root/<str:lang_code>/update/', views.update_official_laws, name='update-law-of-root'),
    path('law/<slug:slug>/add/', views.create_law_group, name='post-law-group-create'),
    path('law/<slug:slug>/copy/', views.copy_law_group_view, name='copy-first-law'),
    path('law/<slug:slug>/edit/', views.edit_law_group, name='edit-law-group'),
    path('law/<slug:slug>/<str:lang_code>/', views.law_group_view, name='law-view'),
    path('law/<slug:slug>/<str:lang_code>/edit/', views.law_group_edit_view, name='edit-law-view'),
    path('law/<slug:slug>/<str:lang_code>/copy/', views.copy_law_group_view, name='copy-law-group'),
    path('law/<slug:slug>/<str:lang_code>/delete/', views.delete_law_group, name='delete-law-group'),
    path('law/<slug:group_slug>/<str:lang_code>/compare/', views.upload_and_compare_yaml_view, name='compare-law-group'),

    path('export-laws/<slug:group_slug>/<str:lang_code>/', views.export_laws_yaml_view, name='export-laws-yaml'),
    
    # AJAX Law Views
    path('ajax/law/add/', views.add_law_ajax, name='add-law-ajax'),
    path('ajax/law/move/<int:law_id>/<str:direction>/', views.move_law_ajax, name='move-law-ajax'),
    path('ajax/law/edit/', views.edit_law_ajax, name='edit-law-ajax'),
    path('ajax/law/edit-description/', views.edit_law_description_ajax, name='edit-law-description-ajax'),
    path('ajax/law/delete/', views.delete_law_ajax, name='delete-law-ajax'),

    # FAQs
    path('faq/', views.faq_search, name='faq'),
    path('faq/<str:lang_code>/', views.faq_search, name='lang-faq'),
    path('faq/add/<str:lang_code>/', views.FAQCreateView.as_view(), name='faq-add'),
    path('faq/<slug:slug>/<str:lang_code>/add/', views.FAQCreateView.as_view(), name='post-faq-add'),
    path('faq/<slug:slug>/<str:lang_code>/', views.faq_search, name='faq-view'),
    path('edit/faq/<int:pk>/', views.FAQUpdateView.as_view(), name='faq-edit'),
    path('delete/faq/<int:pk>/', views.FAQDeleteView.as_view(), name='faq-delete'),

    path("archive/", list_view, name='archive-home'),
 
    # used for search
    path("hx/search/", search_view, name='search'),
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
    
    # path('expansion/<slug:slug>/', ExpansionDetailView.as_view(), name='expansion-detail'),
    path('expansion/<slug:slug>/', expansion_detail_view, name='expansion-detail'),
    # path('expansion/<slug:slug>/factions/', ExpansionDetailView.as_view(), name='expansion-factions'),
    path('expansion/<slug:slug>/update/', ExpansionUpdateView.as_view(), name='expansion-update'),
    path('expansion/<slug:slug>/delete/', ExpansionDeleteView.as_view(), name='expansion-delete'),
    # path('expansion/<slug:expansion_slug>/law/', views.law_hierarchy_view, name='expansion-law'),


    
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
    path("stable/<slug:slug>/", confirm_stable, name='confirm-stable'),
    path("testing/<slug:slug>/", confirm_testing, name='confirm-testing'),
    path('color/<str:color_name>/', color_group_view, name='color-group'),
    path("animals/<slug:slug>/", animal_match_view, name='animal-match'),
    path("status/<slug:slug>/", status_check, name='status-check'),
    path("translations/<slug:slug>/", translations_view, name='post-translations'),
    path("translations/<slug:slug>/new/", create_post_translation, name='translation-create'),
    path("translations/<slug:slug>/update/<str:lang>/", create_post_translation, name='translation-update'),

    path('post/<int:pk>/delete/', PostDeleteView.as_view(), name='post-delete'),


    path('piece/add/', add_piece, name='add-piece'),
    path('piece/update/<int:id>', add_piece, name='update-piece'),
    path('piece/delete/<int:id>', delete_piece, name='delete-piece'),

    path('workshop/', PNPAssetListView.as_view(), name='asset-list'),
    path('resources/', PNPAssetListView.as_view()),
    path('resources/new/', PNPAssetCreateView.as_view(), name='asset-new'),
    path('resources/<int:pk>/', PNPAssetDetailView.as_view(), name='asset-detail'),
    path('resources/<int:pk>/update/', PNPAssetUpdateView.as_view(), name='asset-update'),
    path('resources/<int:pk>/delete/', PNPAssetDeleteView.as_view(), name='asset-delete'),
    path('resources/player/<slug:slug>/', PNPAssetListView.as_view(), name='asset-player'),
    path('resources/pin/<int:id>/', pin_asset, name='pin-asset')
]
