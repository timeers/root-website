from django.urls import path
from .views import (
                    survey_list_view, survey_history_view, survey_results_view, survey_take_view, survey_user_response_view, survey_user_response_edit_view, survey_detail_view,
                    survey_create_view, survey_edit_view, survey_delete_view, survey_preview_view, survey_duplicate_view, search_posts_for_survey, search_players_for_survey, get_tournament_rounds,
                    get_question_data, survey_responses_view, save_question_template, get_question_template, delete_question_template, survey_admin_view, survey_quiz_settings_view,
                    survey_response_move_to_waitlist, survey_response_move_to_accepted, survey_response_delete,
                    # Grouping views
                    survey_grouping_setup_view,
                    grouping_status, grouping_move_player, grouping_add_to_group, grouping_create_group,
                    grouping_delete_group, grouping_regenerate, grouping_add_survey_response, grouping_finalize,
                    grouping_move_to_waitlist, grouping_move_from_waitlist, grouping_remove_from_group
                    )

urlpatterns = [
   
    # Survey URLs
    path('surveys/', survey_list_view, name='survey-list'),
    path('surveys/history/', survey_history_view, name='survey-history'),
    path('surveys/create/', survey_create_view, name='survey-create'),
    path('surveys/admin/', survey_admin_view, name='survey-admin'),

    path('surveys/api/save-question-template/', save_question_template, name='save-question-template'),
    path('surveys/api/get-question-template/<int:template_id>/', get_question_template, name='get-question-template'),
    path('surveys/api/delete-question-template/<int:template_id>/', delete_question_template, name='delete-question-template'),
    path('surveys/api/search-posts/', search_posts_for_survey, name='search-posts-for-survey'),
    path('surveys/api/search-players/', search_players_for_survey, name='search-players-for-survey'),
    path('surveys/api/tournament-rounds/', get_tournament_rounds, name='get-tournament-rounds'),
    path('surveys/api/question/<int:question_id>/', get_question_data, name='get-question-data'),

    path('surveys/<slug:slug>/', survey_detail_view, name='survey-detail'),
    path('surveys/<slug:slug>/take/', survey_take_view, name='survey-take'),
    path('surveys/<slug:slug>/edit/', survey_edit_view, name='survey-edit'),
    path('surveys/<slug:slug>/delete/', survey_delete_view, name='survey-delete'),
    path('surveys/<slug:slug>/preview/', survey_preview_view, name='survey-preview'),
    path('surveys/<slug:slug>/duplicate/', survey_duplicate_view, name='survey-duplicate'),
    path('surveys/<slug:slug>/results/', survey_results_view, name='survey-results'),
    path('surveys/<slug:slug>/responses/', survey_responses_view, name='survey-responses'),
    path('surveys/<slug:slug>/quiz/', survey_quiz_settings_view, name='survey-quiz-settings'),

    path('surveys/<slug:slug>/response/<int:response_id>/', survey_user_response_view, name='survey-user-response'),
    path('surveys/<slug:slug>/response/<int:response_id>/edit/', survey_user_response_edit_view, name='survey-edit-response'),

    # Response management actions
    path('surveys/<slug:slug>/response/<int:response_id>/move-to-waitlist/', survey_response_move_to_waitlist, name='survey-response-move-waitlist'),
    path('surveys/<slug:slug>/response/<int:response_id>/move-to-accepted/', survey_response_move_to_accepted, name='survey-response-move-accepted'),
    path('surveys/<slug:slug>/response/<int:response_id>/delete/', survey_response_delete, name='survey-response-delete'),

    # Grouping URLs
    path('surveys/<slug:slug>/grouping/', survey_grouping_setup_view, name='survey-grouping-setup'),

    # Grouping API endpoints
    path('surveys/<slug:slug>/grouping/<int:session_id>/status/', grouping_status, name='grouping-status'),
    path('surveys/<slug:slug>/grouping/<int:session_id>/move-player/', grouping_move_player, name='grouping-move-player'),
    path('surveys/<slug:slug>/grouping/<int:session_id>/add-to-group/', grouping_add_to_group, name='grouping-add-to-group'),
    path('surveys/<slug:slug>/grouping/<int:session_id>/create-group/', grouping_create_group, name='grouping-create-group'),
    path('surveys/<slug:slug>/grouping/<int:session_id>/delete-group/', grouping_delete_group, name='grouping-delete-group'),
    path('surveys/<slug:slug>/grouping/<int:session_id>/regenerate/', grouping_regenerate, name='grouping-regenerate'),
    path('surveys/<slug:slug>/grouping/<int:session_id>/add-survey-response/', grouping_add_survey_response, name='grouping-add-survey-response'),
    path('surveys/<slug:slug>/grouping/<int:session_id>/move-to-waitlist/', grouping_move_to_waitlist, name='grouping-move-to-waitlist'),
    path('surveys/<slug:slug>/grouping/<int:session_id>/move-from-waitlist/', grouping_move_from_waitlist, name='grouping-move-from-waitlist'),
    path('surveys/<slug:slug>/grouping/<int:session_id>/remove-from-group/', grouping_remove_from_group, name='grouping-remove-from-group'),
    path('surveys/<slug:slug>/grouping/<int:session_id>/finalize/', grouping_finalize, name='grouping-finalize'),

]
