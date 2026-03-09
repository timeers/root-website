from django.urls import path
from .views import (
                    survey_list_view, survey_history_view, survey_results_view, survey_take_view, survey_user_response_view, survey_user_response_edit_view, survey_detail_view,
                    survey_create_view, survey_edit_view, survey_delete_view, survey_preview_view, survey_duplicate_view, search_posts_for_survey, search_players_for_survey, get_tournament_rounds, get_tournament_stages,
                    get_question_data, survey_responses_view, save_question_template, get_question_template, delete_question_template, survey_admin_view, survey_quiz_settings_view,
                    survey_response_move_to_waitlist, survey_response_move_to_accepted, survey_response_delete,
                    survey_send_availability,
                    tournament_surveys_view,
                    about_surveys_view,
                    )

urlpatterns = [
   
    # Tournament/Stage scoped survey list
    path('series/<slug:tournament_slug>/surveys/', tournament_surveys_view, name='tournament-surveys'),
    path('series/<slug:tournament_slug>/stage/<slug:stage_slug>/surveys/', tournament_surveys_view, name='stage-surveys'),

    # Survey URLs
    path('surveys/', survey_list_view, name='survey-list'),
    path('surveys/history/', survey_history_view, name='survey-history'),
    path('surveys/create/', survey_create_view, name='survey-create'),
    path('surveys/admin/', survey_admin_view, name='survey-admin'),
    path('about/surveys/', about_surveys_view, name='about-surveys'),

    path('surveys/api/save-question-template/', save_question_template, name='save-question-template'),
    path('surveys/api/get-question-template/<int:template_id>/', get_question_template, name='get-question-template'),
    path('surveys/api/delete-question-template/<int:template_id>/', delete_question_template, name='delete-question-template'),
    path('surveys/api/search-posts/', search_posts_for_survey, name='search-posts-for-survey'),
    path('surveys/api/search-players/', search_players_for_survey, name='search-players-for-survey'),
    path('surveys/api/tournament-rounds/', get_tournament_rounds, name='get-tournament-rounds'),
    path('surveys/api/tournament-stages/', get_tournament_stages, name='get-tournament-stages'),
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
    path('surveys/<slug:slug>/send-availability/', survey_send_availability, name='survey-send-availability'),

    path('surveys/<slug:slug>/response/<int:response_id>/', survey_user_response_view, name='survey-user-response'),
    path('surveys/<slug:slug>/response/<int:response_id>/edit/', survey_user_response_edit_view, name='survey-edit-response'),

    # Response management actions
    path('surveys/<slug:slug>/response/<int:response_id>/move-to-waitlist/', survey_response_move_to_waitlist, name='survey-response-move-waitlist'),
    path('surveys/<slug:slug>/response/<int:response_id>/move-to-accepted/', survey_response_move_to_accepted, name='survey-response-move-accepted'),
    path('surveys/<slug:slug>/response/<int:response_id>/delete/', survey_response_delete, name='survey-response-delete'),


]
