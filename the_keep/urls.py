from django.urls import path
from .api_views import search_posts
from . import views

urlpatterns = [
    path('', views.home, name='site-home'),
    path('home/', views.home),
    path('about/', views.about, name='keep-about'),

    # Laws
    path('law-of-root/', views.law_table_of_contents, name='law-home'),
    path('law-of-root/update/', views.manage_law_updates, name='manage-law-updates'),
    path('law-of-root/refresh/', views.check_for_law_updates, name='check-for-law-updates'),
    path('law-of-root/preview/<int:rule_id>/', views.law_preview, name='law-preview'),
    path("activate-rule/", views.activate_rule, name="activate-rule"),
    path("archive-rule/", views.archive_rule, name="archive-rule"),
    path('law-of-root/<str:lang_code>/', views.law_table_of_contents, name='law-of-root'),
    path('law-of-root/<str:lang_code>/update/', views.update_official_laws, name='update-law-of-root'),
    path('law/<slug:slug>/add/', views.create_law_group, name='post-law-group-create'),
    path('law/<slug:slug>/copy/', views.copy_law_group_view, name='copy-first-law'),
    path('law/<slug:slug>/<str:lang_code>/', views.law_group_view, name='law-view'),
    path('law/<slug:slug>/<str:lang_code>/update/', views.update_law_group, name='update-law-group'),
    path('law/<slug:slug>/<str:lang_code>/edit/', views.law_group_edit_view, name='edit-law-view'),
    path('law/<slug:slug>/<str:lang_code>/copy/', views.copy_law_group_view, name='copy-law-group'),
    path('law/<slug:slug>/<str:lang_code>/delete/', views.delete_law_group, name='delete-law-group'),
    path('law/<slug:group_slug>/<str:lang_code>/compare/', views.upload_and_compare_yaml_view, name='compare-law-group'),

    path('export-laws/<slug:group_slug>/<str:lang_code>/', views.export_laws_yaml_view, name='export-laws-yaml'),
    path('download-laws/<int:rule_id>/', views.download_rule, name='download-rule'),


    # AJAX Law Views
    path('ajax/law/add/', views.add_law_ajax, name='add-law-ajax'),
    path('ajax/law/move/<int:law_id>/<str:direction>/', views.move_law_ajax, name='move-law-ajax'),
    path('ajax/law/edit/', views.edit_law_ajax, name='edit-law-ajax'),
    path('ajax/law/edit-description/', views.edit_law_description_ajax, name='edit-law-description-ajax'),
    path('ajax/law/delete/', views.delete_law_ajax, name='delete-law-ajax'),

    # FAQs
    path('faq/', views.faq_home, name='faq-home'),
    path('faq/<str:lang_code>/', views.faq_home, name='faq-home-lang'),
    path('website/faq/', views.faq_search, name='website-faq'),
    path('website/faq/<str:lang_code>/', views.faq_search, name='lang-faq'),
    path('website/faq/add/<str:lang_code>/', views.FAQCreateView.as_view(), name='faq-add'),
    path('faq/<slug:slug>/<str:lang_code>/add/', views.FAQCreateView.as_view(), name='post-faq-add'),
    path('faq/<slug:slug>/<str:lang_code>/', views.faq_search, name='faq-view'),
    path('edit/faq/<int:pk>/', views.FAQUpdateView.as_view(), name='faq-edit'),
    path('delete/faq/<int:pk>/', views.FAQDeleteView.as_view(), name='faq-delete'),

    path("archive/", views.list_view, name='archive-home'),

    path("search/<str:component_type>/", views.advanced_search, name='advanced-search'),
 
    # used for search
    path("hx/search/", views.search_view, name='search'),
    path('universal-search/', views.universal_search, name='universal-search'),
    path('api/search/', search_posts, name='api-search-posts'),

    path('new/', views.new_components, name='new-components'),
    path('new/faction/', views.FactionCreateView.as_view(), name='faction-create'),
    path('new/clockwork/', views.ClockworkCreateView.as_view(), name='clockwork-create'),
    path('new/map/', views.MapCreateView.as_view(), name='map-create'),
    path('new/deck/', views.DeckCreateView.as_view(), name='deck-create'),
    path('new/hireling/', views.HirelingCreateView.as_view(), name='hireling-create'),
    path('new/landmark/', views.LandmarkCreateView.as_view(), name='landmark-create'),
    path('new/tweak/', views.TweakCreateView.as_view(), name='tweak-create'),
    path('new/vagabond/', views.VagabondCreateView.as_view(), name='vagabond-create'),
    path('new/expansion/', views.ExpansionCreateView.as_view(), name='expansion-create'),
    

    path('expansion/<slug:slug>/', views.expansion_detail_view, name='expansion-detail'),
    path('expansion/<slug:expansion_slug>/faq/<str:lang_code>/', views.faq_home, name='expansion-faq'),
    path('expansion/<slug:expansion_slug>/law/<str:lang_code>/', views.expansion_law_group, name='expansion-law'),
    path('expansion/<slug:slug>/update/', views.ExpansionUpdateView.as_view(), name='expansion-update'),
    path('expansion/<slug:slug>/delete/', views.ExpansionDeleteView.as_view(), name='expansion-delete'),
    # path('expansion/<slug:expansion_slug>/law/', views.law_hierarchy_view, name='expansion-law'),


    
    path('map/<slug:slug>/', views.ultimate_component_view, name='map-detail'),
    path('deck/<slug:slug>/', views.ultimate_component_view, name='deck-detail'),
    path('hireling/<slug:slug>/', views.ultimate_component_view, name='hireling-detail'),
    path('landmark/<slug:slug>/', views.ultimate_component_view, name='landmark-detail'),
    path('tweak/<slug:slug>/', views.ultimate_component_view, name='tweak-detail'),
    path('vagabond/<slug:slug>/', views.ultimate_component_view, name='vagabond-detail'),
    path('faction/<slug:slug>/', views.ultimate_component_view, name='faction-detail'),
    path('clockwork/<slug:slug>/', views.ultimate_component_view, name='clockwork-detail'),
    # Games
    path('map/<slug:slug>/games/', views.component_games, name='map-games'),
    path('deck/<slug:slug>/games/', views.component_games, name='deck-games'),
    path('hireling/<slug:slug>/games/', views.component_games, name='hireling-games'),
    path('landmark/<slug:slug>/games/', views.component_games, name='landmark-games'),
    path('tweak/<slug:slug>/games/', views.component_games, name='tweak-games'),
    path('vagabond/<slug:slug>/games/', views.component_games, name='vagabond-games'),
    path('faction/<slug:slug>/games/', views.component_games, name='faction-games'),
    path('clockwork/<slug:slug>/games/', views.component_games, name='clockwork-games'),

    path('map/<slug:slug>/update/', views.MapUpdateView.as_view(), name='map-update'),
    path('deck/<slug:slug>/update/', views.DeckUpdateView.as_view(), name='deck-update'),
    path('hireling/<slug:slug>/update/', views.HirelingUpdateView.as_view(), name='hireling-update'),
    path('landmark/<slug:slug>/update/', views.LandmarkUpdateView.as_view(), name='landmark-update'),
    path('tweak/<slug:slug>/update/', views.TweakUpdateView.as_view(), name='tweak-update'),
    path('vagabond/<slug:slug>/update/', views.VagabondUpdateView.as_view(), name='vagabond-update'),
    path('faction/<slug:slug>/update/', views.FactionUpdateView.as_view(), name='faction-update'),
    path('clockwork/<slug:slug>/update/', views.ClockworkUpdateView.as_view(), name='clockwork-update'),

    path("post/<int:id>/bookmark/", views.bookmark_post, name='bookmark-post'),
    path("stable/<slug:slug>/", views.confirm_stable, name='confirm-stable'),
    path("testing/<slug:slug>/", views.confirm_testing, name='confirm-testing'),
    path('color/<str:color_name>/', views.color_group_view, name='color-group'),
    path("animals/<slug:slug>/", views.animal_match_view, name='animal-match'),
    path("status/<slug:slug>/", views.status_check, name='status-check'),
    path("translations/<slug:slug>/", views.translations_view, name='post-translations'),
    path("translations/<slug:slug>/new/", views.create_post_translation, name='translation-create'),
    path("translations/<slug:slug>/update/<str:lang>/", views.create_post_translation, name='translation-update'),

    path('post/<int:pk>/delete/', views.PostDeleteView.as_view(), name='post-delete'),


    path('piece/add/', views.add_piece, name='add-piece'),
    path('piece/update/<int:id>', views.add_piece, name='update-piece'),
    path('piece/delete/<int:id>', views.delete_piece, name='delete-piece'),

    path('workshop/', views.PNPAssetListView.as_view(), name='asset-list'),
    path('resources/', views.PNPAssetListView.as_view()),
    path('resources/new/', views.PNPAssetCreateView.as_view(), name='asset-new'),
    path('resources/<int:pk>/', views.PNPAssetDetailView.as_view(), name='asset-detail'),
    path('resources/<int:pk>/update/', views.PNPAssetUpdateView.as_view(), name='asset-update'),
    path('resources/<int:pk>/delete/', views.PNPAssetDeleteView.as_view(), name='asset-delete'),
    path('resources/player/<slug:slug>/', views.PNPAssetListView.as_view(), name='asset-player'),
    path('resources/pin/<int:id>/', views.pin_asset, name='pin-asset')
]
