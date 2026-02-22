import json

from datetime import datetime

from django.apps import apps
from django.core.exceptions import PermissionDenied
from django.core.cache import cache
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import connection, models
from django.db.models import Count, F, Q, Count, Avg, Exists, OuterRef, Case, When, Value, BooleanField, IntegerField
from django.http import JsonResponse, HttpResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from .forms import GameCommentCreateForm, PostCommentCreateForm
from .forms import SurveyResponseForm
from .models import (Survey, SurveyResponse, Question, QuestionTemplate, Choice, 
                     Answer, RankedAnswer, RankedPostAnswer, TA_DAY_CODES, LikertScale,
                     GameComment, PostComment)

from the_gatehouse.services.discordservice import send_new_survey_notification
from the_gatehouse.utils import build_absolute_uri, generate_name, NameConvention
from the_gatehouse.tasks import send_discord_message_task
from the_gatehouse.views import player_required, player_onboard_required, admin_onboard_required
from the_gatehouse.models import Profile, DiscordGuild

from the_warroom.models import Tournament, Round, Game, TournamentPlayer, PlayerGroup, Stage
from the_warroom.services.grouping import GroupingService

from the_keep.models import Post

with open('/etc/config.json') as config_file:
    config = json.load(config_file)

@login_required
def game_comment_sent(request, pk):
    game = get_object_or_404(Game, id=pk)

    if request.method == 'POST':
        form = GameCommentCreateForm(request.POST)
        if form.is_valid:
            comment = form.save(commit=False)
            comment.player = request.user.profile
            comment.game = game
            comment.save()
    return render(request, 'snippets/add_comment.html', {'comment': comment, 'object': game})

@login_required
@require_http_methods(['DELETE'])
def game_comment_delete(request, pk):
    comment = get_object_or_404(GameComment, id=pk, player=request.user.profile)
    comment.delete()

    response = HttpResponse(status=204)
    response['HX-Trigger'] = 'delete-comment'
    return response


@login_required
def post_comment_sent(request, pk):
    post = get_object_or_404(Post, id=pk)

    if request.method == 'POST':
        form = PostCommentCreateForm(request.POST)
        if form.is_valid:
            comment = form.save(commit=False)
            comment.player = request.user.profile
            comment.post = post
            comment.save()
    return render(request, 'snippets/add_comment.html', {'comment': comment, 'object': post})

# @login_required
# def post_comment_delete(request, pk):
#     comment = get_object_or_404(PostComment, id=pk, player=request.user.profile)
#     component = comment.post.component

#     if request.method == 'POST':
#         comment.delete()
#         messages.success(request, 'Comment deleted')
#         return redirect(f'{component.lower()}-detail', comment.post.slug)
#     return render(request, 'the_tavern/post_comment_delete.html', {'comment': comment})


@login_required
@require_http_methods(['DELETE'])
def post_comment_delete(request, pk):
    comment = get_object_or_404(PostComment, id=pk, player=request.user.profile)
    comment.delete()

    response = HttpResponse(status=204)
    response['HX-Trigger'] = 'delete-comment'
    return response

def bookmark_toggle(model):
    def inner_func(func):
        def wrapper(request, *args, **kwargs):
            obj = get_object_or_404(model, id=kwargs.get('id'))
            player_exists = obj.bookmarks.filter(discord=request.user.profile.discord).exists()
            if player_exists:
                obj.bookmarks.remove(request.user.profile)
            else:
                obj.bookmarks.add(request.user.profile)
            return func(request, obj)
        return wrapper
    return inner_func

@login_required
@bookmark_toggle(Profile)
def bookmark_player(request, obj):
    return render(request, 'the_gatehouse/partials/bookmarks.html', {'player': obj})

# Survey Views

# Survey Views
@login_required
def search_posts_for_survey(request):
    """AJAX endpoint for searching Posts to add as survey choices."""
    query = request.GET.get('q', '')
    component = request.GET.get('component', '')

    posts = Post.objects.filter(status__lte=4)

    if component:
        posts = posts.filter(component=component)
    if query:
        posts = posts.filter(title__icontains=query)

    # Order by official first, then alphabetically
    posts = posts.order_by('-official', 'title')[:30]

    data = [{
        'id': p.id,
        'title': p.title,
        'component': p.component,
        'official': p.official,
        'icon_url': p.small_icon.url if p.small_icon else None,
    } for p in posts]

    return JsonResponse({'posts': data})


@login_required
def get_tournament_rounds(request):
    """AJAX endpoint for fetching rounds of a tournament."""

    tournament_id = request.GET.get('tournament_id')

    if not tournament_id:
        return JsonResponse({'rounds': []})

    # Get open rounds for the tournament
    rounds = Round.objects.open().filter(
        tournament_id=tournament_id
    ).order_by('round_number')


    data = [{
        'id': r.id,
        'name': r.name or f"Round {r.round_number}",
        'round_number': r.round_number,
    } for r in rounds]

    return JsonResponse({'rounds': data})


@login_required
def get_tournament_stages(request):
    """AJAX endpoint for fetching stages of a tournament."""
    tournament_id = request.GET.get('tournament_id')

    if not tournament_id:
        return JsonResponse({'stages': []})

    stages = Stage.objects.filter(
        tournament_id=tournament_id
    ).order_by('order')

    data = [{'id': s.id, 'name': s.name} for s in stages]

    return JsonResponse({'stages': data})


@login_required
def search_players_for_survey(request):
    """AJAX endpoint for searching players to invite to a survey."""

    query = request.GET.get('q', '').strip()

    if len(query) < 2:
        return JsonResponse({'players': []})

    # Search by display_name or discord username
    players = Profile.objects.filter(
        Q(display_name__icontains=query) | Q(discord__icontains=query)
    )[:20]

    data = [{
        'id': p.id,
        'name': p.name,
        'discord': p.discord or '',
        'image_url': p.image.url if p.image else None,
    } for p in players]

    return JsonResponse({'players': data})


@login_required
def get_question_data(request, question_id):
    """AJAX endpoint for fetching full question data including choices."""

    try:
        question = Question.objects.get(id=question_id)
    except Question.DoesNotExist:
        return JsonResponse({'error': 'Question not found'}, status=404)

    # Check permissions - user must be admin or survey creator
    profile = request.user.profile
    if not profile.admin and question.survey.created_by != profile:
        return JsonResponse({'error': 'Permission denied'}, status=403)

    # Get choices with response info
    choices_with_responses = set()
    # Single selections
    single = set(Answer.objects.filter(
        response__survey=question.survey,
        selected_choice__isnull=False
    ).values_list('selected_choice_id', flat=True))
    # Multiple selections (M2M)
    multi = set(Answer.selected_choices.through.objects.filter(
        answer__response__survey=question.survey
    ).values_list('choice_id', flat=True))
    # Ranked answers
    ranked = set(RankedAnswer.objects.filter(
        answer__response__survey=question.survey
    ).values_list('choice_id', flat=True))
    choices_with_responses = single | multi | ranked

    # Build choices data
    choices = []
    for choice in question.choices.filter(is_hidden=False).order_by('order'):
        choice_data = {
            'id': choice.id,
            'text': choice.text,
            'order': choice.order,
            'has_responses': choice.id in choices_with_responses,
        }
        if choice.post:
            choice_data['post_id'] = choice.post.id
            choice_data['post_title'] = choice.post.title
            choice_data['post_icon_url'] = choice.post.small_icon.url if choice.post.small_icon else None
        choices.append(choice_data)

    # Build hidden choices data
    hidden_choices = []
    for choice in question.choices.filter(is_hidden=True).order_by('order'):
        hidden_choice_data = {
            'id': choice.id,
            'text': choice.text,
            'order': choice.order,
            'has_responses': choice.id in choices_with_responses,
        }
        if choice.post:
            hidden_choice_data['post_id'] = choice.post.id
            hidden_choice_data['post_title'] = choice.post.title
            hidden_choice_data['post_icon_url'] = choice.post.small_icon.url if choice.post.small_icon else None
        hidden_choices.append(hidden_choice_data)

    # Count responses for this question
    response_count = Answer.objects.filter(
        response__survey=question.survey,
        question=question
    ).count()

    data = {
        'id': question.id,
        'text': question.text,
        'type': question.question_type,
        'type_display': Question.QuestionType(question.question_type).label,
        'order': question.order,
        'required': question.required,
        'help_text': question.help_text or '',
        'choices': choices,
        'hidden_choices': hidden_choices,
        'likert_scale_id': question.likert_scale_id if question.likert_scale else None,
        'post_component': question.post_component or None,
        'post_selection_mode': question.post_selection_mode or None,
        'has_responses': response_count > 0,
        'response_count': response_count,
        'ta_enabled_days': question.ta_enabled_days if question.ta_enabled_days else TA_DAY_CODES,
    }

    return JsonResponse(data)


def survey_list_view(request):
    """Display list of available surveys grouped by category."""

    # now = timezone.now()
    profile = request.user.profile if request.user.is_authenticated else None

    # Check for HTMX partial requests
    my_surveys_offset = int(request.GET.get('my_surveys_offset', 0))
    private_offset = int(request.GET.get('private_offset', 0))
    public_offset = int(request.GET.get('public_offset', 0))
    load_type = request.GET.get('load_type', None)

    if profile:

        # Check if banned
        is_banned = profile.group == Profile.GroupChoices.BANNED
        
        user_response_exists = Exists(
            SurveyResponse.objects.filter(
                survey=OuterRef('pk'),
                profile=profile
            )
        )
        
        # Check if user is invited
        user_invited = Exists(
            Survey.invited_players.through.objects.filter(
                survey_id=OuterRef('pk'),
                profile_id=profile.pk
            )
        )
        
        # Check if user is in guild
        user_in_guild = Exists(
            Profile.guilds.through.objects.filter(
                profile_id=profile.pk,
                discordguild_id=OuterRef('guild_id')
            )
        )
        
        # Check if user is in series (via TournamentPlayer)
        user_in_series = Exists(
            Survey.objects.filter(
                pk=OuterRef('pk'),
                series__tournament_players__profile=profile
            )
        )

        # Check if user is a participant in the linked stage
        user_in_stage = Exists(
            Survey.objects.filter(
                pk=OuterRef('pk'),
                stage__participants__tournament_player__profile=profile
            )
        )
        
        my_surveys = (
            Survey.objects.is_available()
            .filter(created_by=profile)
            .annotate(
                user_has_responded=user_response_exists,
                user_is_invited=user_invited,
                user_in_guild=user_in_guild,
                user_in_series=user_in_series,
                user_in_stage=user_in_stage,
                # Can respond if: multiple responses allowed OR hasn't responded yet
                can_respond=Q(allow_multiple_responses=True) | Q(user_has_responded=False),
                # Can take survey if: not banned AND can_respond AND (is_public OR has permission)
                can_take_survey=Case(
                    # Banned users can't take survey
                    When(Q(can_respond=False), then=Value(False)),
                    # Public surveys
                    When(Q(is_public=True), then=Value(True)),
                    # Invited players
                    When(Q(user_is_invited=True), then=Value(True)),
                    # Guild members
                    When(Q(user_in_guild=True), then=Value(True)),
                    # Stage (blocks if has stage but user not in it)
                    When(Q(stage__isnull=False, user_in_stage=True), then=Value(True)),
                    When(Q(stage__isnull=False, user_in_stage=False), then=Value(False)),
                    # Series (blocks if has series but user not in it)
                    When(Q(series__isnull=False, user_in_series=True), then=Value(True)),
                    When(Q(series__isnull=False, user_in_series=False), then=Value(False)),
                    default=Value(False),
                    output_field=models.BooleanField()
                ) if not is_banned else Value(False, output_field=models.BooleanField())
            )
            .prefetch_related('responses')
        )
        private_surveys = (
            Survey.objects.is_available()
            .user_has_invite(profile)
            .annotate(
                user_has_responded=user_response_exists,
                user_is_invited=user_invited,
                user_in_guild=user_in_guild,
                user_in_series=user_in_series,
                user_in_stage=user_in_stage,
                # Can respond if: multiple responses allowed OR hasn't responded yet
                can_respond=Q(allow_multiple_responses=True) | Q(user_has_responded=False),
                # Can take survey if: not banned AND can_respond AND (is_public OR has permission)
                can_take_survey=Case(
                    # Banned users can't take survey
                    When(Q(can_respond=False), then=Value(False)),
                    # Public surveys
                    When(Q(is_public=True), then=Value(True)),
                    # Invited players
                    When(Q(user_is_invited=True), then=Value(True)),
                    # Guild members
                    When(Q(user_in_guild=True), then=Value(True)),
                    # Stage (blocks if has stage but user not in it)
                    When(Q(stage__isnull=False, user_in_stage=True), then=Value(True)),
                    When(Q(stage__isnull=False, user_in_stage=False), then=Value(False)),
                    # Series (blocks if has series but user not in it)
                    When(Q(series__isnull=False, user_in_series=True), then=Value(True)),
                    When(Q(series__isnull=False, user_in_series=False), then=Value(False)),
                    default=Value(False),
                    output_field=models.BooleanField()
                ) if not is_banned else Value(False, output_field=models.BooleanField())
            )
            .prefetch_related('responses', 'guild', 'created_by')
        )
        public_surveys = (
            Survey.objects.is_available()
            .filter(is_public=True)
            .exclude(created_by=profile)
            .annotate(
                user_has_responded=user_response_exists,
                user_is_invited=user_invited,
                user_in_guild=user_in_guild,
                user_in_series=user_in_series,
                user_in_stage=user_in_stage,
                # Can respond if: multiple responses allowed OR hasn't responded yet
                can_respond=Q(allow_multiple_responses=True) | Q(user_has_responded=False),
                # Can take survey if: not banned AND can_respond AND (is_public OR has permission)
                can_take_survey=Case(
                    # Banned users can't take survey
                    When(Q(can_respond=False), then=Value(False)),
                    # Public surveys
                    When(Q(is_public=True), then=Value(True)),
                    # Invited players
                    When(Q(user_is_invited=True), then=Value(True)),
                    # Guild members
                    When(Q(user_in_guild=True), then=Value(True)),
                    # Stage (blocks if has stage but user not in it)
                    When(Q(stage__isnull=False, user_in_stage=True), then=Value(True)),
                    When(Q(stage__isnull=False, user_in_stage=False), then=Value(False)),
                    # Series (blocks if has series but user not in it)
                    When(Q(series__isnull=False, user_in_series=True), then=Value(True)),
                    When(Q(series__isnull=False, user_in_series=False), then=Value(False)),
                    default=Value(False),
                    output_field=models.BooleanField()
                ) if not is_banned else Value(False, output_field=models.BooleanField())
            )
            .prefetch_related('responses', 'created_by')
        )
    else:
        my_surveys = Survey.objects.none()
        private_surveys = Survey.objects.none()
        public_surveys = Survey.objects.is_available().prefetch_related('responses', 'created_by')

    total_my_surveys = my_surveys.count()
    total_private = private_surveys.count()
    total_public = private_surveys.count()

    current_url = request.path
    current_title = "Back to Surveys"

    # Handle HTMX partial requests for loading more surveys
    if load_type == 'my_surveys':
        selected_surveys = my_surveys[my_surveys_offset:my_surveys_offset + SURVEY_LIST_PAGE_SIZE]
        has_more = total_my_surveys > my_surveys_offset + SURVEY_LIST_PAGE_SIZE
        next_offset = my_surveys_offset + SURVEY_LIST_PAGE_SIZE
        return render(request, 'the_tavern/partials/survey_list_container.html', {
            'selected_surveys': selected_surveys,
            'has_more': has_more,
            'offset': next_offset,
            'load_type': load_type,
            'current_url': current_url,
            'current_title': current_title,
            'show_status': True,
        })

    if load_type == 'private':
        selected_surveys = private_surveys[private_offset:private_offset + SURVEY_LIST_PAGE_SIZE]
        has_more = total_private > private_offset + SURVEY_LIST_PAGE_SIZE
        next_offset = private_offset + SURVEY_LIST_PAGE_SIZE
        return render(request, 'the_tavern/partials/survey_list_container.html', {
            'selected_surveys': selected_surveys,
            'has_more': has_more,
            'offset': next_offset,
            'load_type': load_type,
            'current_url': current_url,
            'current_title': current_title,
            'show_status': True,
        })

    if load_type == 'public':
        selected_surveys = public_surveys[public_offset:public_offset + SURVEY_LIST_PAGE_SIZE]
        has_more = total_public > public_offset + SURVEY_LIST_PAGE_SIZE
        next_offset = public_offset + SURVEY_LIST_PAGE_SIZE
        return render(request, 'the_tavern/partials/survey_list_container.html', {
            'selected_surveys': selected_surveys,
            'has_more': has_more,
            'offset': next_offset,
            'load_type': load_type,
            'current_url': current_url,
            'current_title': current_title,
            'show_status': True,
        })


    meta_title = "Surveys"
    meta_description = "Explore community surveys about the board game Root - or create your own to gather feedback on factions, matchups, house rules, or scheduling availability."

    context = {
        'my_surveys': my_surveys[:SURVEY_LIST_PAGE_SIZE],
        'has_more_my_surveys': total_my_surveys > SURVEY_LIST_PAGE_SIZE,
        'my_surveys_offset': SURVEY_LIST_PAGE_SIZE,
        'total_my_surveys': total_my_surveys,

        'private_surveys': private_surveys[:SURVEY_LIST_PAGE_SIZE],
        'has_more_private': total_private > SURVEY_LIST_PAGE_SIZE,
        'private_offset': SURVEY_LIST_PAGE_SIZE,
        'total_private': total_private,

        'public_surveys': public_surveys[:SURVEY_LIST_PAGE_SIZE],
        'has_more_public': total_public > SURVEY_LIST_PAGE_SIZE,
        'public_offset': SURVEY_LIST_PAGE_SIZE,
        'total_public': total_public,

        'can_create_survey': profile.player if profile else False,
        'meta_title': meta_title,
        'meta_description': meta_description,
        'current_url': current_url,
        'current_title': current_title,

        'profile': profile,
        'show_status': True,
    }
    return render(request, 'the_tavern/survey_list.html', context)


SURVEY_LIST_PAGE_SIZE = 10


@login_required
def survey_history_view(request):
    """Display user's survey history: past responses and archived created surveys."""

    profile = request.user.profile
    now = timezone.now()

    # Check for HTMX partial requests
    responses_offset = int(request.GET.get('responses_offset', 0))
    archived_offset = int(request.GET.get('archived_offset', 0))
    public_offset = int(request.GET.get('public_offset', 0))
    load_type = request.GET.get('load_type', None)

    return_to = request.GET.get('return_to') or reverse('survey-list')
    return_title = request.GET.get('return_title') or 'Back to Surveys'
    current_url = request.path
    current_title = 'Back to History'

    # Get all surveys the user has responded to (excluding active ones they can still interact with)
    now = timezone.now()
    past_responses = SurveyResponse.objects.filter(
        profile=profile
    ).select_related(
        'survey', 'survey__created_by', 'survey__guild'
    ).filter(
        Q(survey__end_date__lt=now) | Q(survey__is_active=False)
    ).order_by('-submitted_at')
    total_past_responses = past_responses.count()

    # Get all surveys created by the user that are past or inactive
    archived_surveys = (
        Survey.objects
        .not_available()
        .filter(created_by=profile)
        .annotate(response_count=Count('responses'))
    )
    total_archived = archived_surveys.count()

    # Get all public closed surveys with public results    
    public_surveys = (
        Survey.objects
        .not_available()
        .can_see_results(profile)
        .exclude(created_by=profile)
        .annotate(response_count=Count('responses'))
    )
    total_public = public_surveys.count()

    # Handle HTMX partial requests for loading more items
    if load_type == 'responses':
        items = past_responses[responses_offset:responses_offset + SURVEY_LIST_PAGE_SIZE]
        has_more = len(past_responses) > responses_offset + SURVEY_LIST_PAGE_SIZE
        next_offset = responses_offset + SURVEY_LIST_PAGE_SIZE
        return render(request, 'the_tavern/partials/history_responses_list.html', {
            'past_responses': items,
            'has_more_responses': has_more,
            'responses_offset': next_offset,
            'return_to': return_to,
            'return_title': return_title,
            'current_url': current_url,
            'current_title': current_title,
        })

    if load_type == 'archived':
        items = archived_surveys[archived_offset:archived_offset + SURVEY_LIST_PAGE_SIZE]
        has_more = len(archived_surveys) > archived_offset + SURVEY_LIST_PAGE_SIZE
        next_offset = archived_offset + SURVEY_LIST_PAGE_SIZE
        return render(request, 'the_tavern/partials/history_archived_list.html', {
            'archived_surveys': items,
            'has_more_archived': has_more,
            'archived_offset': next_offset,
            'return_to': return_to,
            'return_title': return_title,
            'current_url': current_url,
            'current_title': current_title,
        })

    if load_type == 'public':
        items = public_surveys[public_offset:public_offset + SURVEY_LIST_PAGE_SIZE]
        has_more = total_public > public_offset + SURVEY_LIST_PAGE_SIZE

        next_offset = public_offset + SURVEY_LIST_PAGE_SIZE
        return render(request, 'the_tavern/partials/history_public_list.html', {
            'public_surveys': items,
            'has_more_public': has_more,
            'public_offset': next_offset,
            'return_to': return_to,
            'return_title': return_title,
            'current_url': current_url,
            'current_title': current_title,
        })

    # Full page load - slice to initial page size
    context = {
        'past_responses': past_responses[:SURVEY_LIST_PAGE_SIZE],
        'has_more_responses': total_past_responses > SURVEY_LIST_PAGE_SIZE,
        'responses_offset': SURVEY_LIST_PAGE_SIZE,
        'total_responses': total_past_responses,

        'archived_surveys': archived_surveys[:SURVEY_LIST_PAGE_SIZE],
        'has_more_archived': total_archived > SURVEY_LIST_PAGE_SIZE,
        'archived_offset': SURVEY_LIST_PAGE_SIZE,
        'total_archived': total_archived,

        'public_surveys': public_surveys[:SURVEY_LIST_PAGE_SIZE],
        'has_more_public': total_public > SURVEY_LIST_PAGE_SIZE,
        'public_offset': SURVEY_LIST_PAGE_SIZE,
        'total_public': total_public,

        'return_to': return_to,
        'return_title': return_title,
        'current_url': current_url,
        'current_title': current_title,
    }
    return render(request, 'the_tavern/survey_history.html', context)


@admin_onboard_required
def survey_admin_view(request):
    profile = request.user.profile
    if not profile.admin:
        raise PermissionDenied
    now = timezone.now()
    
    # Get search query and offset
    search_query = request.GET.get('q', '').strip()
    offset = int(request.GET.get('all_offset', 0))
    load_type = request.GET.get('load_type', None)

    surveys = Survey.objects.annotate(
        is_available_sort=Case(
            When(
                Q(is_active=True) &
                (Q(start_date__isnull=True) | Q(start_date__lte=now)) &
                (Q(end_date__isnull=True) | Q(end_date__gte=now)),
                then=Value(True)
            ),
            default=Value(False),
            output_field=models.BooleanField()
        )
    )
    
    # Filter by search query
    if search_query:
        surveys = surveys.filter(title__icontains=search_query)
    
    surveys = surveys.order_by('-is_available_sort', '-created_at', 'id')
    
    total = surveys.count()

    return_to = request.GET.get('return_to') or reverse('survey-list')
    return_title = request.GET.get('return_title') or 'Back to Surveys'
    current_url = request.path
    current_title = 'Back to Survey Admin'

    # Handle "Load more" request
    if load_type:
        selected_surveys = surveys[offset:offset + SURVEY_LIST_PAGE_SIZE]
        has_more = total > offset + SURVEY_LIST_PAGE_SIZE
        next_offset = offset + SURVEY_LIST_PAGE_SIZE
        return render(request, 'the_tavern/partials/survey_list_container.html', {
            'selected_surveys': selected_surveys,
            'has_more': has_more,
            'offset': next_offset,
            'load_type': load_type,
            'url': request.path,
            'search_query': search_query,
            'return_title': return_title,
            'return_to': return_to,
            'current_title': current_title,
            'current_url': current_url,
            'show_privacy_badge': True,
            'show_owner': True,
        })

    # Initial load
    selected_surveys = surveys[:SURVEY_LIST_PAGE_SIZE]
    has_more = total > SURVEY_LIST_PAGE_SIZE

    context = {
        'selected_surveys': selected_surveys,
        'has_more': has_more,
        'offset': SURVEY_LIST_PAGE_SIZE,
        'load_type': 'all',
        'title': 'Survey Admin',
        'search_query': search_query,
        'url': request.path,
        'total': total,
        'return_title': return_title,
        'return_to': return_to,
        'current_title': current_title,
        'current_url': current_url,
        'show_privacy_badge': True,
        'show_owner': True,
    }

    # Return partial for HTMX requests
    if request.htmx:
        return render(request, 'the_tavern/partials/survey_list_container.html', context)
    
    return render(request, 'the_tavern/survey_admin.html', context)


@player_onboard_required
def survey_take_view(request, slug):
    """Take a survey."""

    survey = get_object_or_404(Survey, slug=slug)
    profile = request.user.profile

    user_responses = survey.responses.filter(profile=profile)
    response_count = user_responses.count()
    first_response = user_responses.first()

    # Check if survey is available
    if not survey.is_available():
        if response_count == 1:
            messages.warning(request, _('This survey is no longer available.'))
            return redirect('survey-user-response', slug=survey.slug, response_id=first_response.id)
        elif response_count == 0:
            messages.warning(request, _('This survey is not currently available.'))
            return redirect('survey-detail', slug=survey.slug)
        else:
            messages.warning(request, _('This survey is no longer available.'))
            return redirect('survey-detail', slug=survey.slug)
            

    # Determine if user can edit their response
    if user_responses and not survey.allow_multiple_responses:
            messages.warning(request, _('You have already taken this survey.'))
            return redirect('survey-user-response', slug=survey.slug, response_id=first_response.id)

    # Check if there is any reason the user can't take the survey.
    if not survey.can_take_survey(profile):
        messages.error(request, f'You do not have access to take this survey')
        return redirect('survey-detail', slug=survey.slug)


    if request.method == 'POST':
        form = SurveyResponseForm(request.POST, survey=survey)

        if form.is_valid():
            # Get only visible questions for processing
            visible_questions = survey.questions.filter(is_hidden=False)

            # Additional validation for required multiple selection questions
            validation_errors = []
            for question in visible_questions:
                if question.required and question.question_type in ['MS', 'TA', 'DY']:
                    field_name = f'question_{question.id}'
                    answer_data = form.cleaned_data.get(field_name)
                    if not answer_data or len(answer_data) == 0:
                        validation_errors.append(f'{question.text}: Please select at least one option.')

            if validation_errors:
                for error in validation_errors:
                    messages.error(request, error)
                return render(request, 'the_tavern/take_survey.html', {
                    'survey': survey,
                    'form': form,
                    'visible_questions': visible_questions,
                })

            # Get timezone offset from form
            timezone_offset = request.POST.get('timezone_offset_hours', '').strip()
            try:
                timezone_offset_hours = float(timezone_offset) if timezone_offset else None
            except (ValueError, TypeError):
                timezone_offset_hours = None

            # Calculate response position
            response_position = survey.get_next_response_position()

            survey_response = SurveyResponse.objects.create(
                survey=survey,
                profile=request.user.profile,
                timezone_offset_hours=timezone_offset_hours,
                response_position=response_position
            )

            # Save answers for each question
            for question in visible_questions:
                field_name = f'question_{question.id}'
                answer_data = form.cleaned_data.get(field_name)

                if answer_data:
                    # Get or create answer (for editing vs new response)
                    answer = Answer.objects.create(
                        response=survey_response,
                        question=question
                    )

                    # Save based on question type
                    if question.question_type == 'MC':
                        # Single choice - handle Post-based questions
                        if question.uses_all_official_posts() and str(answer_data).startswith('post_'):
                            post_id = int(answer_data.replace('post_', ''))
                            answer.selected_post = Post.objects.get(id=post_id)
                            answer.save()
                        else:
                            choice = Choice.objects.get(id=int(answer_data))
                            answer.selected_choice = choice
                            # Also set selected_post if choice has a linked post
                            if choice.post:
                                answer.selected_post = choice.post
                            answer.save()

                    elif question.question_type == 'YN':
                        # Boolean - single choice
                        choice = Choice.objects.get(id=int(answer_data))
                        answer.selected_choice = choice
                        answer.save()

                    elif question.question_type == 'MS':
                        # Multiple choices - handle Post-based questions
                        answer.save()  # Save first to enable M2M
                        if question.uses_all_official_posts():
                            for item in answer_data:
                                if str(item).startswith('post_'):
                                    post_id = int(item.replace('post_', ''))
                                    answer.selected_posts.add(Post.objects.get(id=post_id))
                        else:
                            for choice_id in answer_data:
                                choice = Choice.objects.get(id=int(choice_id))
                                answer.selected_choices.add(choice)
                                # Also add to selected_posts if choice has a linked post
                                if choice.post:
                                    answer.selected_posts.add(choice.post)

                    elif question.question_type == 'TA' or question.question_type == 'DY':
                        # Time/Day availability - multiple choices
                        answer.save()  # Save first to enable M2M
                        for choice_id in answer_data:
                            choice = Choice.objects.get(id=int(choice_id))
                            answer.selected_choices.add(choice)

                    elif question.question_type == 'OE':
                        # Open ended
                        answer.text_answer = answer_data
                        answer.save()

                    elif question.question_type == 'LK':
                        # Likert/Rating
                        answer.numeric_answer = int(answer_data)
                        answer.save()

                    elif question.question_type == 'RK':
                        # Ranking - parse comma-separated IDs, handle Post-based questions
                        answer.save()  # Save first
                        if question.uses_all_official_posts():
                            # Post-based ranking
                            items = [x.strip() for x in answer_data.split(',') if x.strip()]
                            for rank, item in enumerate(items, start=1):
                                if item.startswith('post_'):
                                    try:
                                        post_id = int(item.replace('post_', ''))
                                        post = Post.objects.get(id=post_id)
                                        RankedPostAnswer.objects.create(
                                            answer=answer,
                                            post=post,
                                            rank=rank
                                        )
                                    except Post.DoesNotExist:
                                        pass
                        else:
                            # Regular choice-based ranking
                            choice_ids = [int(x.strip()) for x in answer_data.split(',') if x.strip().isdigit()]
                            for rank, choice_id in enumerate(choice_ids, start=1):
                                try:
                                    choice = question.choices.get(id=choice_id)
                                    RankedAnswer.objects.create(
                                        answer=answer,
                                        choice=choice,
                                        rank=rank
                                    )
                                except Choice.DoesNotExist:
                                    pass

                    elif question.question_type == 'DA':
                        # Date only
                        answer.date_answer = answer_data
                        answer.save()

                    elif question.question_type == 'TI':
                        # Time only
                        answer.time_answer = answer_data
                        answer.save()

                    elif question.question_type == 'DT':
                        # Date & Time - split into date and time parts
                        if isinstance(answer_data, datetime):
                            answer.date_answer = answer_data.date()
                            answer.time_answer = answer_data.time()
                        answer.save()

                    elif question.question_type == 'NU':
                        # Numeric
                        answer.numeric_answer = int(answer_data)
                        answer.save()

            send_discord_message_task.delay(f"[{request.user}]({build_absolute_uri(request, request.user.profile.get_absolute_url())}) ({request.user.profile.group}) took {survey.title}")
            messages.success(request, _('Thank you for completing the survey!'))
            # Calculate the quiz score if needed
            survey_response.calculate_score()
            # Redirect to results if allowed
            if survey.show_results_to_respondents:
                return redirect('survey-results', slug=survey.slug)

            return redirect('survey-detail', survey.slug)
    else:
        form = SurveyResponseForm(survey=survey, existing_response=None)

    # Get only visible (non-hidden) questions for display
    visible_questions = survey.questions.filter(is_hidden=False)

    return_to = request.GET.get('return_to') or reverse('survey-list')
    return_title = request.GET.get('return_title') or 'Back to Surveys'
    current_url = request.path
    current_title = 'Back to Take Survey'

    context = {
        'survey': survey,
        'form': form,
        'visible_questions': visible_questions,
        'return_title': return_title,
        'return_to': return_to,
        'return_title': return_title,
        'return_to': return_to,
    }
    return render(request, 'the_tavern/take_survey.html', context)


@player_required
def survey_user_response_view(request, slug, response_id):
    """Display survey and handle response submission."""

    survey = get_object_or_404(Survey, slug=slug)
    user_response = get_object_or_404(SurveyResponse, id=response_id, survey=survey)
    profile = request.user.profile

    if not user_response.can_view_response(profile):
        raise PermissionDenied

    can_edit_response = survey.can_edit_response(profile) and user_response.profile == profile
    can_see_results = survey.can_see_results(profile)

    can_edit_survey = survey.can_edit_survey(profile)
    can_take_survey = survey.can_take_survey(profile)

    is_response_waitlisted = survey.is_response_waitlisted(user_response)

    return_to = request.GET.get('return_to') or survey.get_absolute_url()
    return_title = request.GET.get('return_title') or 'Back to Survey'
    current_url = request.path
    current_title = 'Back to Response'
    view_detail_button = False

    if return_title == 'Back to History':
        view_detail_button = True

    # Show their previous response in read-only mode
    return render(request, 'the_tavern/view_survey_response.html', {
        'survey': survey,
        'response': user_response,
        'can_edit_response': can_edit_response,
        'can_see_results': can_see_results,
        'can_edit_survey': can_edit_survey,
        'can_take_survey': can_take_survey,
        'is_response_waitlisted': is_response_waitlisted,
        'return_title': return_title,
        'return_to': return_to,
        'current_title': current_title,
        'current_url': current_url,
        'view_detail_button': view_detail_button,
        'show_correct_answers': survey.show_correct_answers,
        'show_score_summary': survey.show_score_summary,
        'score_correct': user_response.score_correct,
        'score_total': user_response.score_total,
        'relative_score': user_response.relative_score,
    })


@player_onboard_required
def survey_user_response_edit_view(request, slug, response_id):
    """Display survey and handle response submission."""

    is_editing = True
    survey = get_object_or_404(Survey, slug=slug)
    user_response = get_object_or_404(SurveyResponse, id=response_id, profile=request.user.profile, survey=survey)
    
    # Check if survey is available
    if not survey.is_available():
        messages.warning(request, _('This survey is no longer available.'))
        return redirect('survey-user-response', slug=slug, response_id=user_response.id)


    if not survey.can_edit_response(request.user.profile):
        # Show their previous response in read-only mode
        messages.warning(request, _('You cannot make changes to your response.'))
        return redirect('survey-user-response', slug=slug, response_id=user_response.id)

    if request.method == 'POST':
        form = SurveyResponseForm(request.POST, survey=survey, existing_response=user_response)

        if form.is_valid():
            # Additional validation for required multiple selection questions
            validation_errors = []
            for question in survey.questions.all():
                if question.required and question.question_type in ['MS', 'TA', 'DY']:
                    field_name = f'question_{question.id}'
                    answer_data = form.cleaned_data.get(field_name)
                    if not answer_data or len(answer_data) == 0:
                        validation_errors.append(f'{question.text}: Please select at least one option.')

            if validation_errors:
                for error in validation_errors:
                    messages.error(request, error)
                return render(request, 'the_tavern/take_survey.html', {
                    'survey': survey,
                    'form': form,
                    'is_editing': is_editing,
                })

            # Update timezone offset if provided
            timezone_offset = request.POST.get('timezone_offset_hours', '').strip()
            try:
                timezone_offset_hours = float(timezone_offset) if timezone_offset else None
                if timezone_offset_hours is not None:
                    user_response.timezone_offset_hours = timezone_offset_hours
                    user_response.save(update_fields=['timezone_offset_hours'])
            except (ValueError, TypeError):
                pass  # Keep existing timezone offset if invalid

            # Save answers for each question
            for question in survey.questions.all():
                field_name = f'question_{question.id}'
                answer_data = form.cleaned_data.get(field_name)

                if answer_data:
                    # Get or create answer (for editing vs new response)

                    answer, created = Answer.objects.get_or_create(
                        response=user_response,
                        question=question
                    )
                    # Clear previous values
                    answer.text_answer = None
                    answer.selected_choice = None
                    answer.selected_post = None
                    answer.numeric_answer = None
                    answer.date_answer = None
                    answer.time_answer = None
                    answer.selected_choices.clear()
                    answer.selected_posts.clear()
                    # Delete previous ranked answers
                    RankedAnswer.objects.filter(answer=answer).delete()
                    RankedPostAnswer.objects.filter(answer=answer).delete()


                    # Save based on question type
                    if question.question_type == 'MC':
                        # Single choice - handle Post-based questions
                        if question.uses_all_official_posts() and str(answer_data).startswith('post_'):
                            post_id = int(answer_data.replace('post_', ''))
                            answer.selected_post = Post.objects.get(id=post_id)
                            answer.save()
                        else:
                            choice = Choice.objects.get(id=int(answer_data))
                            answer.selected_choice = choice
                            # Also set selected_post if choice has a linked post
                            if choice.post:
                                answer.selected_post = choice.post
                            answer.save()

                    elif question.question_type == 'YN':
                        # Boolean - single choice
                        choice = Choice.objects.get(id=int(answer_data))
                        answer.selected_choice = choice
                        answer.save()

                    elif question.question_type == 'MS':
                        # Multiple choices - handle Post-based questions
                        answer.save()  # Save first to enable M2M
                        if question.uses_all_official_posts():
                            for item in answer_data:
                                if str(item).startswith('post_'):
                                    post_id = int(item.replace('post_', ''))
                                    answer.selected_posts.add(Post.objects.get(id=post_id))
                        else:
                            for choice_id in answer_data:
                                choice = Choice.objects.get(id=int(choice_id))
                                answer.selected_choices.add(choice)
                                # Also add to selected_posts if choice has a linked post
                                if choice.post:
                                    answer.selected_posts.add(choice.post)

                    elif question.question_type == 'TA' or question.question_type == 'DY':
                        # Time/Day availability - multiple choices
                        answer.save()  # Save first to enable M2M
                        for choice_id in answer_data:
                            choice = Choice.objects.get(id=int(choice_id))
                            answer.selected_choices.add(choice)

                    elif question.question_type == 'OE':
                        # Open ended
                        answer.text_answer = answer_data
                        answer.save()

                    elif question.question_type == 'LK':
                        # Likert/Rating
                        answer.numeric_answer = int(answer_data)
                        answer.save()

                    elif question.question_type == 'RK':
                        # Ranking - parse comma-separated IDs, handle Post-based questions
                        answer.save()  # Save first
                        if question.uses_all_official_posts():
                            # Post-based ranking
                            items = [x.strip() for x in answer_data.split(',') if x.strip()]
                            for rank, item in enumerate(items, start=1):
                                if item.startswith('post_'):
                                    try:
                                        post_id = int(item.replace('post_', ''))
                                        post = Post.objects.get(id=post_id)
                                        RankedPostAnswer.objects.create(
                                            answer=answer,
                                            post=post,
                                            rank=rank
                                        )
                                    except Post.DoesNotExist:
                                        pass
                        else:
                            # Regular choice-based ranking
                            choice_ids = [int(x.strip()) for x in answer_data.split(',') if x.strip().isdigit()]
                            for rank, choice_id in enumerate(choice_ids, start=1):
                                try:
                                    choice = question.choices.get(id=choice_id)
                                    RankedAnswer.objects.create(
                                        answer=answer,
                                        choice=choice,
                                        rank=rank
                                    )
                                except Choice.DoesNotExist:
                                    pass

                    elif question.question_type == 'DA':
                        # Date only
                        answer.date_answer = answer_data
                        answer.save()

                    elif question.question_type == 'TI':
                        # Time only
                        answer.time_answer = answer_data
                        answer.save()

                    elif question.question_type == 'DT':
                        # Date & Time - split into date and time parts
                        if isinstance(answer_data, datetime):
                            answer.date_answer = answer_data.date()
                            answer.time_answer = answer_data.time()
                        answer.save()

                    elif question.question_type == 'NU':
                        # Numeric
                        answer.numeric_answer = int(answer_data)
                        answer.save()

            # Update the response's updated_at timestamp
            user_response.save()

            messages.success(request, _('Your survey response has been updated!'))

            # Re-calculate the quiz score if needed
            user_response.calculate_score()

            # Redirect to results if allowed
            if survey.show_results_to_respondents:
                return redirect('survey-results', slug=survey.slug)

            return redirect('survey-user-response', slug=slug, response_id=user_response.id)
    else:
        form = SurveyResponseForm(survey=survey, existing_response=user_response if is_editing else None)

    visible_questions = survey.questions.filter(is_hidden=False)

    context = {
        'survey': survey,
        'form': form,
        'is_editing': is_editing,
        'visible_questions': visible_questions,
    }
    return render(request, 'the_tavern/take_survey.html', context)

def survey_detail_view(request, slug):
    """Display the current user's responses to a survey."""

    survey = get_object_or_404(Survey, slug=slug)

    if request.user.is_authenticated:
        profile = request.user.profile
    else:
        profile = None

    survey_question_count = survey.question_count()
    survey_user_response_count = survey.response_count()

    user_responses = survey.responses.filter(profile=profile)

    can_edit_response = survey.can_edit_response(profile)
    can_edit_survey = survey.can_edit_survey(profile)
    can_see_results = survey.can_see_results(profile)
    can_take_survey = survey.can_take_survey(profile)
    can_view_survey = survey.can_view_survey(profile)

    return_to = request.GET.get('return_to') or reverse('survey-list')
    return_title = request.GET.get('return_title') or 'Back to Surveys'
    current_url = request.path
    current_title = 'Back to Survey'

    meta_title = survey.title
    survey_description = f' | {survey.description}' if survey.description else ''
    meta_description = f"A { 'public' if survey.is_public else 'private' } survey by {survey.created_by.name if survey.created_by else "Anonymous"}{survey_description}"


    context = {
        'responses': user_responses,
        'survey': survey,
        'can_edit_response': can_edit_response,
        'can_edit_survey': can_edit_survey,
        'can_take_survey': can_take_survey,
        'can_view_survey': can_view_survey,
        'can_see_results': can_see_results,
        'survey_question_count': survey_question_count,
        'survey_user_response_count': survey_user_response_count,
        'return_to': return_to,
        'return_title': return_title,
        'current_url': current_url,
        'current_title': current_title,
        'meta_title': meta_title,
        'meta_description': meta_description,
    }

    return render(request, 'the_tavern/survey_detail.html', context)



@player_onboard_required
def survey_results_view(request, slug):
    """Display aggregated survey results (admin only, or respondents if allowed)."""

    survey = get_object_or_404(Survey, slug=slug)
    profile = request.user.profile

    # Check permissions
    if not request.user.profile.admin and not request.user.profile == survey.created_by:
        if not survey.can_see_results(profile):
            raise PermissionDenied
        if not survey.show_results_on_close and not survey.has_user_responded(request.user.profile):
            messages.warning(request, _('You must complete the survey to view results.'))
            return redirect('survey-take', slug=survey.slug)

    # Gather results for each question
    questions_with_results = []
    for question in survey.questions.all():
        question_data = {
            'question': question,
            'total_responses': question.answer_set.count(),
            'results': [],
            'has_correct_answer': question.has_correct_answer(),
            'correct_answer_display': question.get_correct_answer_display(),
            'correct_choice_id': question.correct_choice_id,
            'correct_choice_ids': list(question.correct_choices.values_list('id', flat=True)),
            'correct_post_id': question.correct_post_id,
            'correct_post_ids': list(question.correct_posts.values_list('id', flat=True)),
            'correct_numeric': question.correct_numeric,
            'correct_ranking': question.correct_ranking,
            'correct_ranking_posts': question.correct_ranking_posts,
        }

        if question.question_type in ['MC', 'YN', 'MS', 'TA', 'DY']:
            # Choice-based questions (including Time Availability)
            results_list = []

            # For post-based questions, use selected_post/selected_posts instead of choices
            if question.post_component:
                if question.question_type == 'MS':
                    # Multiple selection - aggregate from selected_posts
                    post_results = Post.objects.filter(
                        multiple_post_answers__question=question,
                        multiple_post_answers__response__survey=survey
                    ).annotate(count=Count('multiple_post_answers')).values('title', 'count')
                    for result in post_results:
                        percentage = (result['count'] / question_data['total_responses'] * 100) if question_data['total_responses'] > 0 else 0
                        results_list.append({
                            'choice': result['title'],
                            'count': result['count'],
                            'percentage': round(percentage, 1)
                        })
                else:
                    # Single selection (MC) - aggregate from selected_post
                    post_results = question.answer_set.filter(
                        response__survey=survey,
                        selected_post__isnull=False
                    ).values('selected_post__title').annotate(count=Count('id'))
                    for result in post_results:
                        percentage = (result['count'] / question_data['total_responses'] * 100) if question_data['total_responses'] > 0 else 0
                        results_list.append({
                            'choice': result['selected_post__title'],
                            'count': result['count'],
                            'percentage': round(percentage, 1)
                        })
            else:
                # Standard choice-based questions
                for choice in question.choices.all():
                    if question.question_type in ['MS', 'TA', 'DY']:
                        count = choice.multiple_answers.filter(response__survey=survey).count()
                    else:
                        count = choice.single_answers.filter(response__survey=survey).count()

                    percentage = (count / question_data['total_responses'] * 100) if question_data['total_responses'] > 0 else 0
                    results_list.append({
                        'choice': choice.text,
                        'count': count,
                        'percentage': round(percentage, 1)
                    })

            # Sort by count (descending) for better visualization
            question_data['results'] = sorted(results_list, key=lambda x: x['count'], reverse=True)

        elif question.question_type == 'LK':
            # Numeric questions - calculate average and distribution
            answers = question.answer_set.filter(response__survey=survey, numeric_answer__isnull=False)
            numeric_values = answers.values_list('numeric_answer', flat=True)

            if numeric_values:
                avg = sum(numeric_values) / len(numeric_values)
                question_data['average'] = round(avg, 2)

                # Distribution
                distribution = answers.values('numeric_answer').annotate(count=Count('id')).order_by('numeric_answer')
                question_data['results'] = [
                    {
                        'value': item['numeric_answer'],
                        'count': item['count'],
                        'percentage': round(item['count'] / len(numeric_values) * 100, 1)
                    }
                    for item in distribution
                ]

        elif question.question_type == 'NU':
            # Numeric questions - calculate statistics and distribution
            answers = question.answer_set.filter(response__survey=survey, numeric_answer__isnull=False)
            numeric_values = list(answers.values_list('numeric_answer', flat=True))

            if numeric_values:
                # Summary statistics
                question_data['average'] = round(sum(numeric_values) / len(numeric_values), 2)
                sorted_values = sorted(numeric_values)
                mid = len(sorted_values) // 2
                if len(sorted_values) % 2 == 0:
                    question_data['median'] = (sorted_values[mid - 1] + sorted_values[mid]) / 2
                else:
                    question_data['median'] = sorted_values[mid]
                question_data['min_value'] = min(numeric_values)
                question_data['max_value'] = max(numeric_values)

                # Distribution (grouped by value)
                distribution = answers.values('numeric_answer').annotate(count=Count('id')).order_by('numeric_answer')
                question_data['results'] = [
                    {
                        'value': item['numeric_answer'],
                        'count': item['count'],
                        'percentage': round(item['count'] / len(numeric_values) * 100, 1)
                    }
                    for item in distribution
                ]

                # Raw values for collapsible section (limit to first 100 for performance)
                question_data['raw_values'] = numeric_values[:100]
                question_data['has_more_values'] = len(numeric_values) > 100

        elif question.question_type == 'OE':
            # Open-ended - show all text responses
            answers = question.answer_set.filter(response__survey=survey, text_answer__isnull=False)
            question_data['results'] = [
                {'text': answer.text_answer}
                for answer in answers
            ]

        elif question.question_type == 'RK':
            # Ranking - show average rank for each choice
            ranking_data = {}

            if question.post_component:
                # Post-based ranking - use RankedPostAnswer
                ranked_posts = RankedPostAnswer.objects.filter(
                    answer__question=question,
                    answer__response__survey=survey
                ).values('post__title').annotate(
                    avg_rank=Avg('rank'),
                    count=Count('id')
                )
                for result in ranked_posts:
                    ranking_data[result['post__title']] = {
                        'avg_rank': round(result['avg_rank'], 2),
                        'count': result['count']
                    }
            else:
                # Standard choice-based ranking
                for choice in question.choices.all():
                    ranks = choice.rankedanswer_set.filter(answer__response__survey=survey).values_list('rank', flat=True)
                    if ranks:
                        avg_rank = sum(ranks) / len(ranks)
                        ranking_data[choice.text] = {
                            'avg_rank': round(avg_rank, 2),
                            'count': len(ranks)
                        }

            question_data['results'] = sorted(ranking_data.items(), key=lambda x: x[1]['avg_rank'])

        elif question.question_type in ['DA', 'TI', 'DT']:
            # Date/Time questions - show all responses
            answers = question.answer_set.filter(response__survey=survey)
            for answer in answers:
                if question.question_type == 'DA' and answer.date_answer:
                    question_data['results'].append({'date': answer.date_answer})
                elif question.question_type == 'TI' and answer.time_answer:
                    question_data['results'].append({'date': answer.time_answer})
                elif question.question_type == 'DT' and answer.date_answer and answer.time_answer:
                    dt = datetime.combine(answer.date_answer, answer.time_answer)
                    question_data['results'].append({'date': dt})

        questions_with_results.append(question_data)

    required_summary = None
    optional_summary = None
    required_percent = None
    optional_percent = None
    if survey.show_score_summary and survey.is_quiz:
        survey_stats = survey.get_score_stats()

        required_summary = f"{survey_stats['avg_correct_required']} / {survey_stats['avg_total_required']}"
        required_percent = f"({survey_stats['avg_score_required']}%)"
        if survey_stats['avg_total_optional']:
            optional_summary = f"{survey_stats['avg_correct_optional']} / {survey_stats['avg_total_optional']}"
            optional_percent = f"({survey_stats['avg_score_optional']}%)"


    return_to = request.GET.get('return_to') or survey.get_absolute_url()
    return_title = request.GET.get('return_title') or 'Back to Survey'
    current_url = request.path
    current_title = 'Back to Results'

    context = {
        'survey': survey,
        'total_responses': survey.response_count(),
        'questions_with_results': questions_with_results,
        'return_title': return_title,
        'return_to': return_to,
        'current_url': current_url,
        'current_title': current_title,

        'required_summary': required_summary,
        'required_percent': required_percent,
        'optional_summary': optional_summary,
        'optional_percent': optional_percent,
    }
    return render(request, 'the_tavern/survey_results.html', context)


@login_required
def save_question_template(request):
    """AJAX endpoint to save a question as a template"""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Invalid request method'})
    
    try:
        data = json.loads(request.body)
        profile = request.user.profile

        # Create the template
        # Build template kwargs - only include ta_enabled_days if provided
        template_kwargs = {
            'name': data['name'],
            'text': data['text'],
            'question_type': data['question_type'],
            'required': data.get('required', True),
            'help_text': data.get('help_text', ''),
            'created_by': profile,
            'is_public': data.get('is_public', False) and profile.group == 'A',  # Only admins can make public
            'choices_data': data.get('choices_data', []),
            'post_component': data.get('post_component'),
            'post_selection_mode': data.get('post_selection_mode'),
            'likert_scale_id': data.get('likert_scale_id'),
        }

        # Only include ta_enabled_days if provided (to allow model default to work)
        if data.get('ta_enabled_days'):
            template_kwargs['ta_enabled_days'] = data['ta_enabled_days']

        template = QuestionTemplate.objects.create(**template_kwargs)

        # Add post choices if individual mode
        if data.get('post_choices'):
            posts = Post.objects.filter(id__in=data['post_choices'])
            template.post_choices.set(posts)

        return JsonResponse({
            'success': True,
            'template_id': template.id,
            'template_name': template.name,
            'template_type': template.question_type,
            'template_type_display': template.get_question_type_display(),
            'is_public': template.is_public,
        })
        
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})


@login_required
def get_question_template(request, template_id):
    """AJAX endpoint to fetch full question template data for loading"""

    profile = request.user.profile

    try:
        # User can only access public templates or their own
        template = QuestionTemplate.objects.get(
            Q(is_public=True) | Q(created_by=profile),
            id=template_id
        )
    except QuestionTemplate.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Template not found'})

    # Build the template data
    data = {
        'success': True,
        'text': template.text,
        'type': template.question_type,
        'required': template.required,
        'help_text': template.help_text,
    }

    if template.question_type == 'LK' and template.likert_scale:
        data['likert_scale_id'] = template.likert_scale_id

    # Handle Post-based templates
    if template.post_component:
        data['post_component'] = template.post_component
        data['post_selection_mode'] = template.post_selection_mode
        if template.post_selection_mode == 'individual':
            # Return full post data for individual mode
            post_choices = []
            for post in template.post_choices.all():
                post_choices.append({
                    'id': post.id,
                    'title': post.title,
                    'icon_url': post.small_icon.url if post.small_icon else None,
                })
            data['post_choices'] = post_choices
    elif template.question_type in ['MC', 'MS', 'YN', 'RK'] and template.choices_data:
        data['choices'] = template.choices_data

    # Handle Time Availability templates
    if template.question_type == 'TA' and template.ta_enabled_days:
        data['ta_enabled_days'] = template.ta_enabled_days

    return JsonResponse(data)


@login_required
def delete_question_template(request, template_id):
    """AJAX endpoint to delete a question template"""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Invalid request method'})

    profile = request.user.profile

    try:
        template = QuestionTemplate.objects.get(id=template_id)
    except QuestionTemplate.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Template not found'})

    # Only allow deletion by the creator or admins
    if template.created_by != profile and not profile.admin:
        return JsonResponse({'success': False, 'error': 'You do not have permission to delete this template'})

    template.delete()
    return JsonResponse({'success': True})


@login_required
def survey_duplicate_view(request, slug):
    """Duplicate an existing survey."""

    original_survey = get_object_or_404(Survey, slug=slug)

    profile = request.user.profile
    if not profile.admin and not original_survey.created_by == profile:
        raise PermissionDenied

    try:
        # Store original survey ID and ManyToMany data
        original_survey_id = original_survey.id
        invited_players = list(original_survey.invited_players.all())

        # Create a copy of the survey
        original_survey.pk = None
        original_survey.id = None
        original_survey.slug = None  # Will be auto-generated
        original_survey.title = f"{original_survey.title} (Copy)"
        original_survey.is_active = False
        original_survey.start_date = None
        original_survey.end_date = None
        original_survey.created_by = request.user.profile
        original_survey.save()

        new_survey = original_survey

        # Set ManyToMany field after save
        new_survey.invited_players.set(invited_players)

        # Fetch original questions from the database
        original_questions = Question.objects.filter(
            survey_id=original_survey_id,
            is_hidden=False
        )

        # Copy all non-hidden questions
        for original_question in original_questions:
            original_question_id = original_question.id

            # Store correct answer data before copying
            original_correct_choice_id = original_question.correct_choice_id
            original_correct_choice_ids = list(original_question.correct_choices.values_list('id', flat=True))
            original_correct_ranking = original_question.correct_ranking
            original_correct_post_id = original_question.correct_post_id
            original_correct_post_ids = list(original_question.correct_posts.values_list('id', flat=True))
            original_correct_ranking_posts = original_question.correct_ranking_posts
            original_correct_numeric = original_question.correct_numeric

            # Fetch original choices for this question
            original_choices = Choice.objects.filter(
                question_id=original_question_id,
                is_hidden=False
            )

            # Copy the question
            original_question.pk = None
            original_question.id = None
            original_question.survey = new_survey
            # Clear correct_choice FK before save to avoid referencing old choice
            original_question.correct_choice = None
            original_question.save()

            new_question = original_question

            # Build mapping of old choice IDs to new choice IDs
            choice_id_map = {}

            # Copy all non-hidden choices for this question
            # Skip TIME_AVAILABILITY and DAY_AVAILABILITY - these are auto-created by post_save signal
            if original_question.question_type not in ['TA', 'DY']:
                for original_choice in original_choices:
                    old_choice_id = original_choice.id
                    original_choice.pk = None
                    original_choice.id = None
                    original_choice.question = new_question
                    original_choice.save()
                    choice_id_map[old_choice_id] = original_choice.id

            # Restore correct answer fields with mapped IDs
            if original_correct_choice_id and original_correct_choice_id in choice_id_map:
                new_question.correct_choice_id = choice_id_map[original_correct_choice_id]

            if original_correct_choice_ids:
                new_correct_choice_ids = [choice_id_map[cid] for cid in original_correct_choice_ids if cid in choice_id_map]
                new_question.correct_choices.set(new_correct_choice_ids)

            if original_correct_ranking:
                new_question.correct_ranking = [choice_id_map[cid] for cid in original_correct_ranking if cid in choice_id_map]

            # Post-based correct answers don't need mapping - copy directly
            new_question.correct_post_id = original_correct_post_id
            if original_correct_post_ids:
                new_question.correct_posts.set(original_correct_post_ids)
            new_question.correct_ranking_posts = original_correct_ranking_posts
            new_question.correct_numeric = original_correct_numeric
            new_question.save()

        messages.success(request, f'"{new_survey.title}" created. Mark as active to publish.')
        return redirect('survey-edit', slug=new_survey.slug)

    except Exception as e:
        messages.error(request, f'Error duplicating survey: {str(e)}')
        return redirect('survey-list')


@player_required
def survey_preview_view(request, slug):
    """Preview a survey without submitting responses."""

    survey = get_object_or_404(Survey, slug=slug)

    profile = request.user.profile


    if not survey.can_view_survey(profile):
        messages.warning(request, _('You do not have permission to view this survey.'))
        return redirect('survey-detail', slug=survey.slug)

    can_edit_survey = survey.can_edit_survey(profile)
    can_take_survey = survey.can_take_survey(profile)
    can_see_results = survey.can_see_results(profile)

    return_to = request.GET.get('return_to') or survey.get_absolute_url()
    return_title = request.GET.get('return_title') or 'Back to Survey'
    current_url = request.path
    current_title = 'Back to Preview'

    # Create a read-only form for preview
    form = SurveyResponseForm(survey=survey)

    # Get only visible (non-hidden) questions for display
    visible_questions = survey.questions.filter(is_hidden=False)

    context = {
        'survey': survey,
        'form': form,
        'is_preview': True,
        'can_edit_survey': can_edit_survey,
        'can_take_survey': can_take_survey,
        'can_see_results': can_see_results,
        'return_to': return_to,
        'return_title': return_title,
        'current_url': current_url,
        'current_title': current_title,
        'visible_questions': visible_questions,
    }
    return render(request, 'the_tavern/survey_preview.html', context)


@player_onboard_required
def survey_responses_view(request, slug):
    """View all responses for a survey (admin and survey creator only)."""

    survey = get_object_or_404(Survey, slug=slug)
    profile = request.user.profile

    can_edit_survey = survey.can_edit_survey(profile)
    can_take_survey = survey.can_take_survey(profile)
    can_see_results = survey.can_see_results(profile)

    # Only admin or survey creator can view all responses
    if not can_edit_survey:
        raise PermissionDenied

    # Annotate waitlist status
    responses = SurveyResponse.objects.filter(survey=survey).select_related('profile').annotate(
        is_waitlisted=Case(
            When(
                Q(survey__has_waitlist=True) &
                Q(survey__waitlist_threshold__isnull=False) &
                Q(response_position__gt=F('survey__waitlist_threshold')),
                then=Value(True)
            ),
            default=Value(False),
            output_field=BooleanField()
        ),
        waitlist_number=Case(
            When(
                Q(survey__has_waitlist=True) &
                Q(survey__waitlist_threshold__isnull=False) &
                Q(response_position__gt=F('survey__waitlist_threshold')),
                then=F('response_position') - F('survey__waitlist_threshold')
            ),
            default=Value(None),
            output_field=IntegerField()
        )
    ).order_by('response_position')

    invited_players = survey.invited_players.all()
    unresponded_players = None
    if survey.invited_players.exists():
        responded_players = Profile.objects.filter(
            survey_responses__survey=survey
        ).distinct()
        unresponded_players = invited_players.exclude(
            id__in=responded_players.values_list('id', flat=True)
        )


    return_to = request.GET.get('return_to') or survey.get_absolute_url()
    return_title = request.GET.get('return_title') or 'Back to Survey'
    current_url = request.path
    current_title = 'Back to Responses'

    context = {
        'survey': survey,
        'responses': responses,
        'can_edit_survey': can_edit_survey,
        'can_take_survey': can_take_survey,
        'can_see_results': can_see_results,
        'return_to': return_to,
        'return_title': return_title,
        'current_url': current_url,
        'current_title': current_title,
        'unresponded_players': unresponded_players,
    }
    return render(request, 'the_tavern/survey_responses.html', context)


@player_required
def survey_response_move_to_waitlist(request, slug, response_id):
    """Move a response to waitlist by shifting it to the end and compacting positions."""
    from django.db.models import Max, F

    survey = get_object_or_404(Survey, slug=slug)
    profile = request.user.profile

    # Only admin or survey creator can manage responses
    if not survey.can_edit_survey(profile):
        return JsonResponse({'success': False, 'error': 'Permission denied'}, status=403)

    if not survey.has_waitlist or not survey.waitlist_threshold:
        return JsonResponse({'success': False, 'error': 'Survey does not have waitlist enabled'}, status=400)

    response = get_object_or_404(SurveyResponse, id=response_id, survey=survey)

    # Check if response is already on waitlist
    if response.response_position > survey.waitlist_threshold:
        return JsonResponse({'success': False, 'error': 'Response is already on waitlist'}, status=400)

    old_position = response.response_position

    # Shift all responses after this one up by 1 (fill the gap)
    survey.responses.filter(
        response_position__gt=old_position
    ).update(
        response_position=F('response_position') - 1
    )

    # Calculate new position (end of the list)
    max_position = survey.responses.exclude(id=response_id).aggregate(
        Max('response_position')
    )['response_position__max'] or 0
    response.response_position = max_position + 1
    response.save()

    Survey.objects.filter(pk=survey.pk).update(waitlist_threshold=F('waitlist_threshold') - 1)
    
    return JsonResponse({'success': True})


@player_required
def survey_response_move_to_accepted(request, slug, response_id):
    """Move a response from waitlist to accepted by inserting at threshold position."""
    from django.db.models import F

    survey = get_object_or_404(Survey, slug=slug)
    profile = request.user.profile

    # Only admin or survey creator can manage responses
    if not survey.can_edit_survey(profile):
        return JsonResponse({'success': False, 'error': 'Permission denied'}, status=403)

    if not survey.has_waitlist or not survey.waitlist_threshold:
        return JsonResponse({'success': False, 'error': 'Survey does not have waitlist enabled'}, status=400)

    response = get_object_or_404(SurveyResponse, id=response_id, survey=survey)

    # Check if response is already accepted
    if response.response_position <= survey.waitlist_threshold:
        return JsonResponse({'success': False, 'error': 'Response is already accepted'}, status=400)

    old_position = response.response_position

    # Step 1 & 2: Shift responses between threshold and selected response back 1 position
    survey.responses.filter(
        response_position__gt=survey.waitlist_threshold,
        response_position__lt=old_position
    ).exclude(id=response_id).update(
        response_position=F('response_position') + 1
    )

    # Step 3: Insert this response at the threshold position
    response.response_position = survey.waitlist_threshold + 1
    response.save()

    Survey.objects.filter(pk=survey.pk).update(waitlist_threshold=F('waitlist_threshold') + 1)

    return JsonResponse({'success': True})


@player_onboard_required
def survey_response_delete(request, slug, response_id):
    """Delete a survey response and shift remaining positions up."""
    from django.db.models import F

    survey = get_object_or_404(Survey, slug=slug)
    profile = request.user.profile

    # Only admin or survey creator can delete responses
    if not survey.can_edit_survey(profile):
        return JsonResponse({'success': False, 'error': 'Permission denied'}, status=403)

    response = get_object_or_404(SurveyResponse, id=response_id, survey=survey)
    deleted_position = response.response_position

    # Delete the response
    response.delete()

    # Shift all responses after the deleted one up by 1 (fill the gap)
    survey.responses.filter(
        response_position__gt=deleted_position
    ).update(
        response_position=F('response_position') - 1
    )

    return JsonResponse({'success': True})


@player_onboard_required
def survey_edit_view(request, slug):
    """Edit an existing survey."""

    def get_questions_with_responses(survey):
        """Return set of question IDs that have answers"""
        return set(Answer.objects.filter(
            response__survey=survey
        ).values_list('question_id', flat=True).distinct())

    def get_choices_with_responses(survey):
        """Return set of choice IDs that have been selected"""
        # Single selections
        single = set(Answer.objects.filter(
            response__survey=survey,
            selected_choice__isnull=False
        ).values_list('selected_choice_id', flat=True))

        # Multiple selections (M2M)
        multi = set(Answer.selected_choices.through.objects.filter(
            answer__response__survey=survey
        ).values_list('choice_id', flat=True))

        # Ranked answers
        ranked = set(RankedAnswer.objects.filter(
            answer__response__survey=survey
        ).values_list('choice_id', flat=True))

        return single | multi | ranked

    survey = get_object_or_404(Survey, slug=slug)

    profile = request.user.profile
    if not profile.admin and not survey.created_by == profile:
        raise PermissionDenied

    if request.method == 'POST':
        try:
            # Get survey data
            title = request.POST.get('title', '').strip()[:200]

            # Validate that title is not empty
            if not title:
                messages.error(request, 'Survey title is required.')
                return redirect('survey-edit', slug=slug)

            description = request.POST.get('description', '')
            is_active = request.POST.get('is_active') == 'on'
            is_private = request.POST.get('is_private') == 'on'
            is_public = not is_private
            allow_multiple_responses = request.POST.get('allow_multiple_responses') == 'on'
            allow_edit_responses = request.POST.get('allow_edit_responses') == 'on'
            show_results_to_respondents = request.POST.get('show_results_to_respondents') == 'on'
            show_results_on_close = request.POST.get('show_results_on_close') == 'on'
            is_quiz = request.POST.get('is_quiz') == 'on'
            has_waitlist = request.POST.get('has_waitlist') == 'on'

            # Get waitlist threshold (validated client-side)
            waitlist_threshold = None
            if has_waitlist:
                threshold_str = request.POST.get('waitlist_threshold', '').strip()
                if threshold_str:
                    try:
                        waitlist_threshold = int(threshold_str)
                    except ValueError:
                        waitlist_threshold = None

            # Get associations
            guild_id = request.POST.get('guild') or None
            series_id = request.POST.get('series') or None
            stage_id = request.POST.get('stage') or None
            post_id = request.POST.get('post') or None

            # Get date fields
            start_date = request.POST.get('start_date')
            end_date = request.POST.get('end_date')

            # Convert to datetime objects if provided
            start_date_obj = None
            end_date_obj = None

            if start_date:
                try:
                    start_date_obj = datetime.fromisoformat(start_date)
                except ValueError:
                    pass

            if end_date:
                try:
                    end_date_obj = datetime.fromisoformat(end_date)
                except ValueError:
                    pass

            # Get invited players
            invited_players_str = request.POST.get('invited_players', '')
            invited_player_ids = [int(id) for id in invited_players_str.split(',') if id.strip()]

            # Update survey
            survey.title = title
            survey.description = description
            survey.is_active = is_active
            survey.is_public = is_public
            survey.allow_multiple_responses = allow_multiple_responses
            survey.allow_edit_responses = allow_edit_responses
            survey.show_results_to_respondents = show_results_to_respondents
            survey.show_results_on_close = show_results_on_close
            survey.is_quiz = is_quiz
            survey.has_waitlist = has_waitlist
            survey.waitlist_threshold = waitlist_threshold
            survey.start_date = start_date_obj
            survey.end_date = end_date_obj

            # Update associations
            survey.guild_id = guild_id
            survey.series_id = series_id
            survey.stage_id = stage_id
            survey.post_id = post_id

            # If survey is now public, clear access restrictions
            if is_public:
                survey.guild = None
                survey.save()
                survey.invited_players.clear()
            else:
                survey.save()
                # Only update invited players if not public
                survey.invited_players.set(Profile.objects.filter(id__in=invited_player_ids))

            # Get questions data from JSON
            questions_json = request.POST.get('questions_data')
            if questions_json:
                questions_data = json.loads(questions_json)

                # Track existing question IDs to know which to delete
                existing_question_ids = set(survey.questions.values_list('id', flat=True))
                updated_question_ids = set()

                for q_data in questions_data:
                    if q_data.get('id'):
                        # Update existing question (or restore if hidden)
                        question = Question.objects.get(id=q_data['id'], survey=survey)
                        question.text = q_data['text']
                        question.question_type = q_data['type']
                        question.order = q_data['order']
                        question.required = q_data.get('required', True)
                        question.help_text = q_data.get('help_text', '')
                        question.is_hidden = False  # Unhide if restoring

                        # Update likert scale if needed
                        if q_data['type'] == 'LK' and q_data.get('likert_scale_id'):
                            question.likert_scale_id = q_data['likert_scale_id']
                        else:
                            question.likert_scale = None

                        # Update Post-based question fields
                        if q_data.get('post_component'):
                            question.post_component = q_data['post_component']
                            question.post_selection_mode = q_data.get('post_selection_mode', 'all_official')
                        else:
                            question.post_component = None
                            question.post_selection_mode = None

                        # Update Time Availability enabled days
                        if q_data['type'] == 'TA':
                            question.ta_enabled_days = q_data.get('ta_enabled_days') or TA_DAY_CODES

                        question.save()
                        updated_question_ids.add(question.id)

                        # Handle Post-based questions
                        if q_data.get('post_component') and q_data.get('post_selection_mode') == 'individual':
                            # Clear existing choices
                            question.choices.all().delete()
                            # Create choices linked to Posts
                            if q_data.get('post_choices'):
                                for idx, post_id in enumerate(q_data['post_choices']):
                                    Choice.objects.create(
                                        question=question,
                                        post_id=post_id,
                                        order=idx
                                    )
                            question.save()
                        # Update choices for choice-based questions (non-Post)
                        elif q_data['type'] in ['MC', 'MS', 'YN', 'RK'] and q_data.get('choices'):
                            # Track existing choice IDs (include hidden ones for restore)
                            existing_choice_ids = set(question.choices.values_list('id', flat=True))
                            updated_choice_ids = set()

                            for idx, choice_data in enumerate(q_data['choices']):
                                if isinstance(choice_data, dict) and choice_data.get('id'):
                                    # Update existing choice (or restore if hidden)
                                    choice = Choice.objects.get(id=choice_data['id'], question=question)
                                    choice.text = choice_data['text']
                                    choice.order = idx
                                    choice.is_hidden = False  # Unhide if restoring
                                    choice.save()
                                    updated_choice_ids.add(choice.id)
                                else:
                                    # Create new choice
                                    choice_text = choice_data['text'] if isinstance(choice_data, dict) else choice_data
                                    if choice_text.strip():
                                        Choice.objects.create(
                                            question=question,
                                            text=choice_text,
                                            order=idx
                                        )

                            # Delete or hide choices that were removed
                            choices_to_delete = existing_choice_ids - updated_choice_ids
                            if choices_to_delete:
                                choices_with_responses = get_choices_with_responses(survey)
                                for c_id in choices_to_delete:
                                    if c_id in choices_with_responses:
                                        # Choice has responses - hide instead of delete
                                        Choice.objects.filter(id=c_id).update(is_hidden=True)
                                    else:
                                        # Safe to delete
                                        Choice.objects.filter(id=c_id).delete()

                        elif q_data['type'] == 'TA' or q_data['type'] == 'DY':
                            # TIME_AVAILABILITY - keep existing UTC hour choices, don't delete them
                            pass
                        else:
                            # Not a choice-based question, delete all choices
                            question.choices.all().delete()

                    else:
                        # Create new question
                        question = Question.objects.create(
                            survey=survey,
                            text=q_data['text'],
                            question_type=q_data['type'],
                            order=q_data['order'],
                            required=q_data.get('required', True),
                            help_text=q_data.get('help_text', '')
                        )

                        # Add likert scale if needed
                        if q_data['type'] == 'LK' and q_data.get('likert_scale_id'):
                            question.likert_scale_id = q_data['likert_scale_id']
                            question.save()

                        # Handle Time Availability enabled days
                        if q_data['type'] == 'TA':
                            question.ta_enabled_days = q_data.get('ta_enabled_days') or TA_DAY_CODES
                            question.save()

                        # Track created choices for mapping correct answers
                        created_choices = []

                        # Handle Post-based questions
                        if q_data.get('post_component'):
                            question.post_component = q_data['post_component']
                            question.post_selection_mode = q_data.get('post_selection_mode', 'all_official')
                            question.save()

                            # For individual mode, create choices linked to Posts
                            if q_data.get('post_selection_mode') == 'individual' and q_data.get('post_choices'):
                                for idx, post_id in enumerate(q_data['post_choices']):
                                    choice = Choice.objects.create(
                                        question=question,
                                        post_id=post_id,
                                        order=idx
                                    )
                                    created_choices.append(choice)
                            question.save()

                        # Add choices for choice-based questions (non-Post)
                        elif q_data['type'] in ['MC', 'MS', 'YN', 'RK'] and q_data.get('choices'):
                            for idx, choice_data in enumerate(q_data['choices']):
                                choice_text = choice_data['text'] if isinstance(choice_data, dict) else choice_data
                                if choice_text.strip():
                                    choice = Choice.objects.create(
                                        question=question,
                                        text=choice_text,
                                        order=idx
                                    )
                                    created_choices.append(choice)

                        updated_question_ids.add(question.id)

                # Handle explicit deletions (user chose to delete questions with responses)
                explicit_deletes = request.POST.get('questions_to_delete', '')
                explicit_delete_ids = set()
                if explicit_deletes:
                    explicit_delete_ids = set(int(x) for x in explicit_deletes.split(',') if x.strip())
                    # Delete these questions and their answers (cascade will handle answers)
                    deleted_count = Question.objects.filter(id__in=explicit_delete_ids, survey=survey).delete()[0]
                    if deleted_count > 0:
                        messages.warning(request, f'{deleted_count} question(s) and their responses were permanently deleted.')

                # Delete or hide questions that were removed (but not explicitly deleted)
                questions_to_delete = existing_question_ids - updated_question_ids - explicit_delete_ids
                if questions_to_delete:
                    questions_with_responses = get_questions_with_responses(survey)
                    hidden_count = 0
                    for q_id in questions_to_delete:
                        if q_id in questions_with_responses:
                            # Question has responses - hide instead of delete
                            Question.objects.filter(id=q_id).update(is_hidden=True)
                            hidden_count += 1
                        else:
                            # Safe to delete
                            Question.objects.filter(id=q_id).delete()

                    if hidden_count > 0:
                        messages.info(request, f'{hidden_count} question(s) with existing responses were hidden instead of deleted to preserve data.')

            messages.success(request, f'Survey "{title}" updated successfully!')
            send_new_survey_notification(survey=survey, profile=profile, type="Edited")
            # Check if we should redirect to preview
            if request.POST.get('redirect_to_preview') == 'true':
                return redirect('survey-preview', slug=survey.slug)

            return redirect('survey-detail', slug=survey.slug)

        except Exception as e:
            messages.error(request, f'Error updating survey: {str(e)}')
            return redirect('survey-edit', slug=survey.slug)

    # GET request - show form with existing data
    likert_scales = LikertScale.objects.all()

    # Get available question templates (public ones or user's own)
    question_templates = QuestionTemplate.objects.filter(
        Q(is_public=True) | Q(created_by=profile)
    ).order_by('name')

    # Get user's guilds and hosted tournaments for dropdowns
    user_guilds = profile.guilds.all().exclude(guild_id=config['WW_GUILD_ID'])
    if survey.guild:
        user_guilds = user_guilds | DiscordGuild.objects.filter(pk=survey.guild.pk)

    if profile.admin:
        user_tournaments = Tournament.objects.open()
    else:
        user_tournaments = Tournament.objects.open().filter(
            Q(designer=profile) | Q(moderators=profile)
        ).distinct()

    # Get public posts that the user designed
    user_posts = Post.objects.filter(designer=profile, status__lte=4).distinct()

    # Get response metadata for questions and choices
    questions_with_responses = get_questions_with_responses(survey)
    choices_with_responses = get_choices_with_responses(survey)

    # Prepare existing questions data for JavaScript (only non-hidden)
    existing_questions = []
    for question in survey.questions.filter(is_hidden=False):
        question_response_count = Answer.objects.filter(
            response__survey=survey, question=question
        ).count()

        q_data = {
            'id': question.id,
            'text': question.text,
            'type': question.question_type,
            'order': question.order,
            'required': question.required,
            'help_text': question.help_text or '',
            'choices': [],
            'likert_scale_id': question.likert_scale_id if question.likert_scale else None,
            'post_component': question.post_component or None,
            'post_selection_mode': question.post_selection_mode or None,
            'post_choices': [],
            'has_responses': question.id in questions_with_responses,
            'response_count': question_response_count,
            'ta_enabled_days': question.ta_enabled_days if question.ta_enabled_days else TA_DAY_CODES,
        }

        # Add choices if applicable (only non-hidden)
        if question.question_type in ['MC', 'MS', 'YN', 'RK', 'TA', 'DY']:
            for choice in question.choices.filter(is_hidden=False):
                choice_data = {
                    'id': choice.id,
                    'text': choice.text,
                    'order': choice.order,
                    'has_responses': choice.id in choices_with_responses,
                }
                # If choice is linked to a Post, include Post data
                if choice.post:
                    choice_data['post_id'] = choice.post.id
                    choice_data['post_title'] = choice.post.title
                    choice_data['post_icon_url'] = choice.post.small_icon.url if choice.post.small_icon else None
                    # Add full post object to post_choices for JavaScript
                    q_data['post_choices'].append({
                        'id': choice.post.id,
                        'title': choice.post.title,
                        'icon_url': choice.post.small_icon.url if choice.post.small_icon else None
                    })
                q_data['choices'].append(choice_data)

            # Add hidden choices separately for restore functionality
            q_data['hidden_choices'] = []
            for choice in question.choices.filter(is_hidden=True):
                hidden_choice_data = {
                    'id': choice.id,
                    'text': choice.text,
                    'order': choice.order,
                    'has_responses': choice.id in choices_with_responses,
                }
                if choice.post:
                    hidden_choice_data['post_id'] = choice.post.id
                    hidden_choice_data['post_title'] = choice.post.title
                    hidden_choice_data['post_icon_url'] = choice.post.small_icon.url if choice.post.small_icon else None
                q_data['hidden_choices'].append(hidden_choice_data)

        existing_questions.append(q_data)

    # Prepare hidden questions data for restore functionality
    hidden_questions = []
    for question in survey.questions.filter(is_hidden=True):
        question_response_count = Answer.objects.filter(
            response__survey=survey, question=question
        ).count()
        hq_data = {
            'id': question.id,
            'text': question.text,
            'type': question.question_type,
            'type_display': Question.QuestionType(question.question_type).label,
            'response_count': question_response_count,
        }
        hidden_questions.append(hq_data)

    context = {
        'survey': survey,
        'likert_scales': likert_scales,
        'question_templates': question_templates,
        'existing_questions': json.dumps(existing_questions),
        'hidden_questions': json.dumps(hidden_questions),
        'is_edit_mode': True,
        'response_count': survey.responses.count(),
        'user_guilds': user_guilds,
        'user_tournaments': user_tournaments,
        'user_posts': user_posts,
    }
    return render(request, 'the_tavern/survey_form.html', context)


@login_required
def survey_delete_view(request, slug):
    """Delete a survey and all its responses."""

    survey = get_object_or_404(Survey, slug=slug)

    profile = request.user.profile
    if not profile.admin and not survey.created_by == profile:
        raise PermissionDenied

    if request.method == 'POST':
        survey_title = survey.title
        survey.delete()
        messages.success(request, f'Survey "{survey_title}" has been permanently deleted.')
        return redirect('survey-list')

    # GET request - show confirmation (handled via modal in edit page)
    return redirect('survey-edit', slug=slug)


@player_onboard_required
def survey_create_view(request):
    """Create a new survey with questions and choices."""

    profile = request.user.profile

    if request.method == 'POST':
        try:
            # Get survey data
            title = request.POST.get('title', '').strip()[:200]

            # Validate that title is not empty
            if not title:
                messages.error(request, 'Survey title is required.')
                return redirect('survey-create')

            description = request.POST.get('description', '')
            is_active = request.POST.get('is_active') == 'on'
            allow_multiple_responses = request.POST.get('allow_multiple_responses') == 'on'
            allow_edit_responses = request.POST.get('allow_edit_responses') == 'on'
            show_results_to_respondents = request.POST.get('show_results_to_respondents') == 'on'
            show_results_on_close = request.POST.get('show_results_on_close') == 'on'
            is_quiz = request.POST.get('is_quiz') == 'on'
            has_waitlist = request.POST.get('has_waitlist') == 'on'

            # Get waitlist threshold (validated client-side)
            waitlist_threshold = None
            if has_waitlist:
                threshold_str = request.POST.get('waitlist_threshold', '').strip()
                if threshold_str:
                    try:
                        waitlist_threshold = int(threshold_str)
                    except ValueError:
                        waitlist_threshold = None

            # Get date fields
            start_date = request.POST.get('start_date')
            end_date = request.POST.get('end_date')

            # Convert to datetime objects if provided
            start_date_obj = None
            end_date_obj = None

            if start_date:
                try:
                    start_date_obj = datetime.fromisoformat(start_date)
                except ValueError:
                    pass

            if end_date:
                try:
                    end_date_obj = datetime.fromisoformat(end_date)
                except ValueError:
                    pass

            # Get optional association fields
            guild_id = request.POST.get('guild') or None
            series_id = request.POST.get('series') or None
            stage_id = request.POST.get('stage') or None
            post_id = request.POST.get('post') or None
            is_private = request.POST.get('is_private') == 'on'
            is_public = not is_private

            # Get invited players
            invited_players_str = request.POST.get('invited_players', '')
            invited_player_ids = [int(id) for id in invited_players_str.split(',') if id.strip()]

            # Create survey
            survey = Survey.objects.create(
                title=title,
                description=description,
                is_active=is_active,
                is_public=is_public,
                allow_multiple_responses=allow_multiple_responses,
                allow_edit_responses=allow_edit_responses,
                show_results_to_respondents=show_results_to_respondents,
                show_results_on_close=show_results_on_close,
                is_quiz=is_quiz,
                has_waitlist=has_waitlist,
                waitlist_threshold=waitlist_threshold,
                start_date=start_date_obj,
                end_date=end_date_obj,
                created_by=profile,
                guild_id=guild_id,
                series_id=series_id,
                stage_id=stage_id,
                post_id=post_id,
            )

            # Add invited players
            if invited_player_ids:
                survey.invited_players.set(Profile.objects.filter(id__in=invited_player_ids))

            # Get questions data from JSON
            questions_json = request.POST.get('questions_data')
            if questions_json:
                questions_data = json.loads(questions_json)

                for q_data in questions_data:
                    # Create question
                    question = Question.objects.create(
                        survey=survey,
                        text=q_data['text'],
                        question_type=q_data['type'],
                        order=q_data['order'],
                        required=q_data.get('required', True),
                        help_text=q_data.get('help_text', '')
                    )

                    # Add likert scale if needed
                    if q_data['type'] == 'LK' and q_data.get('likert_scale_id'):
                        question.likert_scale_id = q_data['likert_scale_id']
                        question.save()

                    # Handle Time Availability enabled days
                    if q_data['type'] == 'TA':
                        question.ta_enabled_days = q_data.get('ta_enabled_days') or TA_DAY_CODES
                        question.save()

                    # Track created choices for mapping correct answers
                    created_choices = []

                    # Handle Post-based questions
                    if q_data.get('post_component'):
                        question.post_component = q_data['post_component']
                        question.post_selection_mode = q_data.get('post_selection_mode', 'all_official')
                        question.save()

                        # For individual mode, create choices linked to Posts
                        if q_data.get('post_selection_mode') == 'individual' and q_data.get('post_choices'):
                            for idx, post_id in enumerate(q_data['post_choices']):
                                choice = Choice.objects.create(
                                    question=question,
                                    post_id=post_id,
                                    order=idx
                                )
                                created_choices.append(choice)

                    # Add choices for choice-based questions (non-Post)
                    elif q_data['type'] in ['MC', 'MS', 'YN', 'RK'] and q_data.get('choices'):
                        for idx, choice_data in enumerate(q_data['choices']):
                            choice_text = choice_data['text'] if isinstance(choice_data, dict) else choice_data
                            if choice_text.strip():
                                choice = Choice.objects.create(
                                    question=question,
                                    text=choice_text,
                                    order=idx
                                )
                                created_choices.append(choice)

            messages.success(request, f'Survey "{title}" created successfully!')
            send_new_survey_notification(survey=survey, profile=profile, type="New")
            return redirect('survey-preview', slug=survey.slug)

        except Exception as e:
            messages.error(request, f'Error creating survey: {str(e)}')
            return redirect('survey-create')

    # GET request - show form
    likert_scales = LikertScale.objects.all()

    # Get available question templates (public ones or user's own)
    question_templates = QuestionTemplate.objects.filter(
        Q(is_public=True) | Q(created_by=profile)
    ).order_by('name')

    # Convert templates to JSON for JavaScript
    templates_json = []
    for template in question_templates:
        template_data = template.to_question_data()
        templates_json.append({
            'id': template.id,
            'name': template.name,
            'data': template_data
        })

    # Get user's guilds and hosted tournaments for dropdowns
    if profile.admin:
        user_tournaments = Tournament.objects.open()
        user_guilds = DiscordGuild.objects.all()
    else:
        user_tournaments = Tournament.objects.open().filter(
            Q(designer=profile) | Q(moderators=profile)
        ).distinct()
        user_guilds = profile.guilds.all().exclude(guild_id=config['WW_GUILD_ID'])

    # Get public posts that the user designed
    user_posts = Post.objects.filter(designer=profile, status__lte=4).distinct()

    # Handle pre-fill from query params (e.g. coming from the settings hub)
    prefill_series_id = None
    prefill_stage_id = None
    series_param = request.GET.get('series')
    stage_param = request.GET.get('stage')
    if series_param:
        try:
            prefill_tournament = Tournament.objects.get(id=series_param)
            if profile.admin or prefill_tournament in user_tournaments:
                prefill_series_id = prefill_tournament.id
                if stage_param:
                    try:
                        prefill_stage = Stage.objects.get(id=stage_param, tournament=prefill_tournament)
                        prefill_stage_id = prefill_stage.id
                    except Stage.DoesNotExist:
                        pass
        except Tournament.DoesNotExist:
            pass

    context = {
        'likert_scales': likert_scales,
        'question_templates': question_templates,
        'user_guilds': user_guilds,
        'user_tournaments': user_tournaments,
        'user_posts': user_posts,
        'is_edit_mode': False,
        'prefill_series_id': prefill_series_id,
        'prefill_stage_id': prefill_stage_id,
    }
    return render(request, 'the_tavern/survey_form.html', context)


@login_required
def tournament_surveys_view(request, tournament_slug, stage_slug=None):
    """List surveys associated with a tournament or a specific stage."""
    tournament = get_object_or_404(Tournament, slug=tournament_slug)
    profile = request.user.profile

    if not tournament.has_permission(profile):
        raise PermissionDenied

    stage = None
    surveys = Survey.objects.filter(series=tournament)
    if stage_slug:
        stage = get_object_or_404(Stage, slug=stage_slug, tournament=tournament)
        surveys = surveys.filter(stage=stage)

    if stage:
        create_url = reverse('survey-create') + f'?series={tournament.id}&stage={stage.id}'
    else:
        create_url = reverse('survey-create') + f'?series={tournament.id}'

    context = {
        'tournament': tournament,
        'stage': stage,
        'surveys': surveys,
        'create_url': create_url,
        'current_title': stage.name if stage else tournament.name,
        'current_url': request.path,
    }
    return render(request, 'the_tavern/tournament_surveys.html', context)


@login_required
def survey_quiz_settings_view(request, slug):
    """Configure correct answers for a quiz survey."""

    survey = get_object_or_404(Survey, slug=slug)
    profile = request.user.profile

    # Only the creator or admin can edit quiz settings
    if not profile.admin and survey.created_by != profile:
        raise PermissionDenied

    # Survey must have quiz mode enabled
    if not survey.is_quiz:
        messages.error(request, _('Quiz mode is not enabled for this survey.'))
        return redirect('survey-detail', slug=survey.slug)

    visible_questions = survey.questions.filter(is_hidden=False).prefetch_related('choices')

    if request.method == 'POST':
        # Handle quiz settings form
        show_correct_answers = request.POST.get('show_correct_answers') == 'on'
        show_score_summary = request.POST.get('show_score_summary') == 'on'
        allow_multiple_responses = request.POST.get('allow_multiple_responses') == 'on'
        allow_edit_responses = request.POST.get('allow_edit_responses') == 'on'

        survey.show_correct_answers = show_correct_answers
        survey.show_score_summary = show_score_summary
        survey.allow_multiple_responses = allow_multiple_responses
        survey.allow_edit_responses = allow_edit_responses
        survey.save()

        # Process correct answers for each question
        for question in visible_questions:
            # Skip question types that don't support correct answers
            if question.question_type not in ['MC', 'MS', 'YN', 'NU', 'LK', 'RK']:
                continue

            field_name = f'correct_{question.id}'

            if question.question_type in ['MC', 'YN']:
                # Single choice
                answer_data = request.POST.get(field_name)
                question.correct_choice = None
                question.correct_post = None

                if answer_data:
                    if answer_data.startswith('post_'):
                        post_id = int(answer_data.replace('post_', ''))
                        question.correct_post_id = post_id
                    else:
                        question.correct_choice_id = int(answer_data)
                question.save()

            elif question.question_type == 'MS':
                # Multiple selection - all selected must match exactly
                answer_data = request.POST.getlist(field_name)
                question.correct_choices.clear()
                question.correct_posts.clear()

                choice_ids = []
                post_ids = []
                for val in answer_data:
                    if val.startswith('post_'):
                        post_ids.append(int(val.replace('post_', '')))
                    else:
                        choice_ids.append(int(val))

                if choice_ids:
                    question.correct_choices.set(Choice.objects.filter(id__in=choice_ids))
                if post_ids:
                    question.correct_posts.set(Post.objects.filter(id__in=post_ids))

            elif question.question_type in ['NU', 'LK']:
                # Numeric/Scale
                answer_data = request.POST.get(field_name)
                if answer_data:
                    try:
                        question.correct_numeric = int(answer_data)
                    except ValueError:
                        question.correct_numeric = None
                else:
                    question.correct_numeric = None
                question.save()

            elif question.question_type == 'RK':
                # Ranking - save the order
                ranking_data = request.POST.get(f'{field_name}_order')
                question.correct_ranking = None
                question.correct_ranking_posts = None

                if ranking_data:
                    try:
                        order = json.loads(ranking_data)
                        # Separate choice IDs from post IDs
                        choice_order = []
                        post_order = []
                        for item in order:
                            if str(item).startswith('post_'):
                                post_order.append(int(str(item).replace('post_', '')))
                            else:
                                choice_order.append(int(item))

                        if choice_order:
                            question.correct_ranking = choice_order
                        if post_order:
                            question.correct_ranking_posts = post_order
                    except (json.JSONDecodeError, ValueError):
                        pass
                question.save()

        messages.success(request, _('Quiz settings saved successfully.'))
        return redirect('survey-detail', slug=survey.slug)

    # Build questions data for template
    questions_data = []
    for question in visible_questions:
        q_data = {
            'question': question,
            'supports_correct_answer': question.question_type in ['MC', 'MS', 'YN', 'NU', 'LK', 'RK'],
            'required': question.required,
        }

        # Get current correct answer
        if question.question_type in ['MC', 'YN']:
            q_data['current_correct_choice_id'] = question.correct_choice_id
            q_data['current_correct_post_id'] = question.correct_post_id
        elif question.question_type == 'MS':
            q_data['current_correct_choice_ids'] = list(question.correct_choices.values_list('id', flat=True))
            q_data['current_correct_post_ids'] = list(question.correct_posts.values_list('id', flat=True))
        elif question.question_type in ['NU', 'LK']:
            q_data['current_correct_numeric'] = question.correct_numeric
        elif question.question_type == 'RK':
            q_data['current_correct_ranking'] = question.correct_ranking or []
            q_data['current_correct_ranking_posts'] = question.correct_ranking_posts or []

        questions_data.append(q_data)

    return_to = request.GET.get('return_to') or survey.get_absolute_url()
    return_title = request.GET.get('return_title') or 'Back to Survey'
    current_url = request.path
    current_title = 'Back to Quiz Settings'

    context = {
        'survey': survey,
        'questions_data': questions_data,
        'can_edit_survey': True,
        'can_take_survey': False,
        'can_see_results': survey.can_see_results(profile),
        'return_to': return_to,
        'return_title': return_title,
        'current_url': current_url,
        'current_title': current_title,
    }
    return render(request, 'the_tavern/survey_quiz_settings.html', context)


# ============================================================================
# Survey Availability Send to Round or Tournament
# ============================================================================


@player_onboard_required
def survey_send_availability(request, slug):
    """
    Send survey respondents to a specific Stage's roster.
    Lets the user select from the survey's linked tournament's stages
    and adds accepted respondents to that stage's roster.
    """
    from the_warroom.services.grouping import GroupingService

    survey = get_object_or_404(Survey, slug=slug)
    profile = request.user.profile

    if not profile.admin and survey.created_by != profile:
        raise PermissionDenied

    if not survey.series:
        messages.error(request, 'This survey is not linked to a tournament series.')
        return redirect('survey-detail', slug=survey.slug)

    tournament = survey.series
    stages = tournament.stages.order_by('order')

    if request.method == 'POST':
        stage_id = request.POST.get('stage_id')

        # Sync survey responses to TournamentPlayer records
        GroupingService.sync_survey_responses_to_tournament(tournament, survey)

        if stage_id == 'none' or not stage_id:
            # Just sync to tournament — no stage
            count = TournamentPlayer.objects.filter(tournament=tournament).count()
            messages.success(request, f'Synced {count} player(s) to {tournament.name}.')
            return redirect('tournament-manage-players', slug=tournament.slug)

        target_stage = get_object_or_404(Stage, id=stage_id, tournament=tournament)

        registered_players = TournamentPlayer.objects.filter(
            tournament=tournament,
            status=TournamentPlayer.StatusChoices.REGISTERED,
        )
        for tp in registered_players:
            target_stage.add_player(tp.profile)

        added_count = registered_players.count()
        messages.success(request, f'Synced {added_count} player(s) to {target_stage.name}.')
        return redirect('stage-manage-players', tournament_slug=tournament.slug, stage_slug=target_stage.slug)


    return_to = request.GET.get('return_to') or survey.get_absolute_url()
    return_title = request.GET.get('return_title') or 'Back to Survey'
    current_url = request.path
    current_title = 'Back to Quiz Settings'

    context = {
        'survey': survey,
        'tournament': tournament,
        'stages': stages,
        'designated_stage': survey.stage,
        'return_to': return_to,
        'return_title': return_title,
        'current_url': current_url,
        'current_title': current_title,
    }
    return render(request, 'the_tavern/survey_send_availability.html', context)
