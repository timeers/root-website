import re
import time
import json

from itertools import groupby
from django.shortcuts import render
from django.views.generic import DeleteView
from django.views.decorators.http import require_http_methods
from django.shortcuts import get_object_or_404, redirect
from django.forms.models import modelformset_factory
from django.http import HttpResponse, HttpResponseBadRequest, Http404, HttpResponseRedirect
from django.core.exceptions import ObjectDoesNotExist, PermissionDenied
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
from django.urls import reverse, reverse_lazy
from django.contrib.auth.decorators import login_required
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.db import IntegrityError, models

from django.db.models import Count, F, ExpressionWrapper, FloatField, IntegerField, Max, Q, Case, When, Value, ProtectedError, Prefetch, OuterRef, Subquery, Exists, BooleanField, CharField
from django.db.models.functions import Cast, Coalesce
from django.utils import timezone 
from django.utils.translation import get_language, gettext as _, gettext_lazy as _lazy
from urllib.parse import quote

from .models import (Game, Effort, TurnScore, ScoreCard, Round, Tournament, AssetModeChoices,
                     TournamentPlayer, PlayerGroup, Stage, StageParticipant, FormatChoices,
                     Match, MatchSeries, MatchSeat, CompetitionStatus, EditPermission)
from .services.grouping import GroupingService, build_opponent_history
from .forms import (GameCreateForm, GameCreateFormV2, EffortCreateForm,
                    TurnScoreCreateForm, ScoreCardCreateForm, AssignScorecardForm, AssignEffortForm,
                    RoundCreateForm, StageCreateForm,
                    TournamentDynamicCreateForm, TournamentDynamicUpdateForm,
                    TournamentPlayerSettingsForm, TournamentAssetSettingsForm,
                    )
from .filters import GameFilter, PlayerGameFilter, TournamentGameFilter

from .utils import get_single_round, get_single_stage

from the_keep.models import Post, Faction, Deck, Map, Vagabond, Hireling, Landmark, Tweak, StatusChoices, PostTranslation

from the_gatehouse.models import Profile, Language
from the_gatehouse.views import (player_required, admin_required, 
                                 admin_required_class_based_view, player_required_class_based_view,
                                 player_onboard_required, admin_onboard_required)
from the_gatehouse.forms import PlayerCreateForm
from the_gatehouse.tasks import send_rich_discord_message_task, send_discord_message_task
from the_gatehouse.utils import get_uuid, build_absolute_uri, get_int_param, NameConvention, generate_name
from the_gatehouse.services.context_service import get_theme, get_thematic_images

from the_tavern.forms import GameCommentCreateForm
from the_tavern.views import bookmark_toggle






# ============================
# Game Views
# ============================

#  A list of all the games. Most recent update first

def game_list_view(request):
    
    # Determine template
    t0 = time.perf_counter()
    if hasattr(request, 'htmx') and request.htmx:
        template_name = 'the_warroom/partials/game_list_home.html'
    else:
        if request.user.is_authenticated:
            send_discord_message_task.delay(
                f'[{request.user}]({build_absolute_uri(request, request.user.profile.get_absolute_url())}) ({request.user.profile.group}) viewing The Battlefield'
            )
        # else:
        #     send_discord_message_task.delay(f'{get_uuid(request)} viewing The Battlefield')
        template_name = 'the_warroom/games_home.html'
    
    t1 = time.perf_counter()
    # print(f"[TIMING] template names: {t1 - t0:.4f}s")
    
    # Build queryset
    t0 = time.perf_counter()
    opts = Game.with_efforts()
    if request.user.is_authenticated and not request.user.profile.weird:
        queryset = Game.objects.filter(official=True, final=True)
    else:
        queryset = Game.objects.filter(final=True)
    queryset = queryset.select_related(*opts['select']).prefetch_related(*opts['prefetch'])

    # Apply filters
    filterset = GameFilter(request.GET, queryset=queryset, user=request.user)
    games = filterset.qs.order_by('-date_posted')

    t1 = time.perf_counter()
    # print(f"[TIMING] queryset assembly: {t1 - t0:.4f}s")
    
    # Build context
    t10 = time.perf_counter()
    context = {}
    
    t0 = time.perf_counter()
    # print(f"[TIMING] context start: {t0 - t10:.4f}s")
    
    # Theme
    theme = get_theme(request)
    background_image, foreground_images, theme_artists, background_pattern = get_thematic_images(
        theme=theme, page='games'
    )
    
    context['background_image'] = background_image
    context['foreground_images'] = foreground_images
    context['background_pattern'] = background_pattern
    context['page_artists'] = theme_artists
    
    t1 = time.perf_counter()
    # print(f"[TIMING] context theme assembly: {t1 - t0:.4f}s")
    
    # Pagination
    paginate_by = settings.PAGE_SIZE
    paginator = Paginator(games, paginate_by)
    page_number = request.GET.get('page')
    
    try:
        page_obj = paginator.get_page(page_number)
    except EmptyPage:
        page_obj = paginator.page(paginator.num_pages)
    
    t12 = time.perf_counter()
    # print(f"[TIMING] context pagination: {t12 - t1:.4f}s")

    games_count = paginator.count
    
    t2 = time.perf_counter()
    # print(f"[TIMING] context games count: {t2 - t12:.4f}s")
    t3 = time.perf_counter()
    # print(f"[TIMING] context player leaderboard assembly: {t3 - t2:.4f}s")
    t4 = time.perf_counter()
    # print(f"[TIMING] context faction leaderboard assembly: {t4 - t3:.4f}s")
    # Leaderboard data
    context.update({
        'games': page_obj,
        'is_paginated': paginator.num_pages > 1,
        'page_obj': page_obj,
        'games_count': games_count,
        'form': filterset.form,
        'filterset': filterset,
    })
    
    t5 = time.perf_counter()
    # print(f"[TIMING] context update: {t5 - t4:.4f}s")

    response = render(request, template_name, context)
    t6 = time.perf_counter()

    # print(f"[TIMING] render: {t6 - t5:.4f}s")
    # print(f"[TIMING] total: {t6 - t0:.4f}s")
    
    return response



def leaderboard_view(request):
    

    try:
        leaderboard_threshold = int(request.GET.get("threshold", 0))
    except (TypeError, ValueError):
        leaderboard_threshold = 0
    try:
        leaderboard_places = int(request.GET.get("limit", 10))
    except (TypeError, ValueError):
        leaderboard_places = 10


    # Determine template
    if hasattr(request, 'htmx') and request.htmx:
        template_name = 'the_warroom/partials/leaderboard_list_home.html'
    else:
        if request.user.is_authenticated:
            send_discord_message_task.delay(
                f'[{request.user}]({build_absolute_uri(request, request.user.profile.get_absolute_url())}) ({request.user.profile.group}) viewing The Leaderboard'
            )
        # else:
        #     send_discord_message_task.delay(f'{get_uuid(request)} viewing The Leaderboard')
        template_name = 'the_warroom/leaderboard_home.html'
    
    # Build queryset
    opts = Game.with_efforts()
    if request.user.is_authenticated and not request.user.profile.weird:
        queryset = Game.objects.filter(official=True, final=True)
    else:
        queryset = Game.objects.filter(final=True)
    queryset = queryset.select_related(*opts['select']).prefetch_related(*opts['prefetch'])

    # Apply filters
    filterset = GameFilter(request.GET, queryset=queryset, user=request.user)
    games = filterset.qs.order_by('-date_posted')
    games_count = games.count()
    
    # Build context
    context = {}
    
    # Theme
    theme = get_theme(request)
    background_image, foreground_images, theme_artists, background_pattern = get_thematic_images(
        theme=theme, page='games'
    )
    
    context['background_image'] = background_image
    context['foreground_images'] = foreground_images
    context['background_pattern'] = background_pattern
    context['page_artists'] = theme_artists

    
    # Leaderboard thresholds
    efforts = Effort.objects.filter(game__in=games)
    if leaderboard_threshold == 0:
        if games_count > 5000:
            leaderboard_threshold = 25
        elif games_count > 2000:
            leaderboard_threshold = 15
        elif games_count > 1500:
            leaderboard_threshold = 10
        elif games_count > 1000:
            leaderboard_threshold = 5
        elif games_count > 500:
            leaderboard_threshold = 3
        else:
            leaderboard_threshold = 1
        
    top_players = Profile.leaderboard(
        limit=leaderboard_places, 
        effort_qs=efforts, 
        game_threshold=leaderboard_threshold, 
        as_json=False)
    most_players = Profile.leaderboard(
        limit=leaderboard_places, 
        effort_qs=efforts, 
        top_quantity=True, 
        game_threshold=leaderboard_threshold, 
        as_json=False)
    top_factions = Faction.leaderboard(
        limit=leaderboard_places, 
        effort_qs=efforts, 
        game_threshold=leaderboard_threshold, 
        as_json=False)
    most_factions = Faction.leaderboard(
        limit=leaderboard_places, 
        effort_qs=efforts, 
        top_quantity=True, 
        game_threshold=leaderboard_threshold, 
        as_json=False)
    # Leaderboard data
    context.update({
        'top_players': top_players,
        'most_players': most_players,
        'top_factions': top_factions,
        'most_factions': most_factions,
        'leaderboard_threshold': leaderboard_threshold,
        'leaderboard_places': leaderboard_places,
        'has_top_factions': bool(top_factions),
        'has_most_factions': bool(most_factions),
        'has_top_players': bool(top_players),
        'has_most_players': bool(most_players),
        'games_count': games_count,
        'form': filterset.form,
        'filterset': filterset,
    })
    

    response = render(request, template_name, context)
    
    return response


@player_required  # assuming you have an FBV decorator matching your CBV one
def player_game_list_view(request, slug=None):
    player = get_object_or_404(Profile, slug=slug) if slug else None

    queryset = Game.objects.filter(final=True)
    if request.user.is_authenticated and not request.user.profile.weird:
        queryset = queryset.filter(official=True)

    opts = Game.with_efforts()
    queryset = queryset.select_related(
        *opts['select'], 'undrafted_faction', 'undrafted_vagabond'
    ).prefetch_related(
        *opts['prefetch'], 'hirelings', 'landmarks', 'tweaks'
    ).distinct()

    filterset = PlayerGameFilter(request.GET, queryset=queryset, player=player)
    filtered_qs = filterset.qs

    paginator = Paginator(filtered_qs, settings.PAGE_SIZE)
    page_number = request.GET.get('page')

    try:
        page_obj = paginator.get_page(page_number)
    except EmptyPage:
        page_obj = paginator.page(paginator.num_pages)

    games_count = paginator.count
    efforts = Effort.objects.filter(game__in=filtered_qs)

    if games_count > 100:
        leaderboard_threshold = 10
    elif games_count > 50:
        leaderboard_threshold = 5
    elif games_count > 20:
        leaderboard_threshold = 3
    elif games_count > 10:
        leaderboard_threshold = 2
    else:
        leaderboard_threshold = 1

    # Theme
    theme = get_theme(request)
    background_image, foreground_images, theme_artists, background_pattern = get_thematic_images(
        theme=theme, page='games'
    )

    context = {
        'games': page_obj,
        'is_paginated': paginator.num_pages > 1,
        'page_obj': page_obj,
        'games_count': games_count,
        'form': filterset.form,
        'filterset': filterset,
        'leaderboard_threshold': leaderboard_threshold,
        'player_view': True,
        'player_slug': slug,
        'top_players': Profile.leaderboard(limit=10, effort_qs=efforts, game_threshold=leaderboard_threshold),
        'most_players': Profile.leaderboard(limit=10, effort_qs=efforts, top_quantity=True, game_threshold=leaderboard_threshold),
        'top_factions': Faction.leaderboard(limit=10, effort_qs=efforts, game_threshold=leaderboard_threshold),
        'most_factions': Faction.leaderboard(limit=10, effort_qs=efforts, top_quantity=True, game_threshold=leaderboard_threshold),
        'background_image': background_image,
        'foreground_images': foreground_images,
        'background_pattern': background_pattern,
        'page_artists': theme_artists,
    }


    if player:
        context['player'] = player

    template_name = 'the_warroom/partials/game_list_home.html' if getattr(request, 'htmx', False) else 'the_warroom/player_games.html'

    response = render(request, template_name, context)

    return response


@login_required
def my_submitted_games_view(request):
    player = request.user.profile

    queryset = Game.objects.filter(final=True, recorder=player)
    if not player.weird:
        queryset = queryset.filter(official=True)

    opts = Game.with_efforts()
    queryset = queryset.select_related(
        *opts['select'], 'undrafted_faction', 'undrafted_vagabond'
    ).prefetch_related(
        *opts['prefetch'], 'hirelings', 'landmarks', 'tweaks'
    ).distinct()

    filterset = GameFilter(request.GET, queryset=queryset, user=request.user)
    filtered_qs = filterset.qs.order_by('-date_posted')

    paginator = Paginator(filtered_qs, settings.PAGE_SIZE)
    page_number = request.GET.get('page')

    try:
        page_obj = paginator.get_page(page_number)
    except EmptyPage:
        page_obj = paginator.page(paginator.num_pages)

    games_count = paginator.count
    efforts = Effort.objects.filter(game__in=filtered_qs)

    if games_count > 100:
        leaderboard_threshold = 10
    elif games_count > 50:
        leaderboard_threshold = 5
    elif games_count > 20:
        leaderboard_threshold = 3
    elif games_count > 10:
        leaderboard_threshold = 2
    else:
        leaderboard_threshold = 1

    # Theme
    theme = get_theme(request)
    background_image, foreground_images, theme_artists, background_pattern = get_thematic_images(
        theme=theme, page='games'
    )

    context = {
        'games': page_obj,
        'is_paginated': paginator.num_pages > 1,
        'page_obj': page_obj,
        'games_count': games_count,
        'form': filterset.form,
        'filterset': filterset,
        'leaderboard_threshold': leaderboard_threshold,
        'player_view': False,
        'submitted_view': True,
        'player': player,
        'top_players': Profile.leaderboard(limit=10, effort_qs=efforts, game_threshold=leaderboard_threshold),
        'most_players': Profile.leaderboard(limit=10, effort_qs=efforts, top_quantity=True, game_threshold=leaderboard_threshold),
        'top_factions': Faction.leaderboard(limit=10, effort_qs=efforts, game_threshold=leaderboard_threshold),
        'most_factions': Faction.leaderboard(limit=10, effort_qs=efforts, top_quantity=True, game_threshold=leaderboard_threshold),
        'background_image': background_image,
        'foreground_images': foreground_images,
        'background_pattern': background_pattern,
        'page_artists': theme_artists,
    }

    template_name = 'the_warroom/partials/game_list_home.html' if getattr(request, 'htmx', False) else 'the_warroom/my_submitted_games.html'

    response = render(request, template_name, context)

    return response


def game_detail_view(request, id=None, league_id=None):
    import time as _time
    import logging as _logging
    # _vlog = _logging.getLogger(__name__)
    # _t0 = _time.time()

    language_code = get_language()

    if id:
        game = get_object_or_404(Game, id=id)
    elif league_id:
        game = get_object_or_404(Game, league_id=league_id)
    else:
        raise Http404('Game not found.')
    # _vlog.warning(f"[game_detail_view] get game: {_time.time()-_t0:.3f}s")

    participants = []
    efforts = []
    scorecard_count = 0
    show_detail = False

    for effort in game.efforts.all():
        participants.append(effort.player)
    if game.recorder:
        participants.append(game.recorder)
    # _vlog.warning(f"[game_detail_view] participants: {_time.time()-_t0:.3f}s")

    translations = PostTranslation.objects.filter(language__code=language_code)
    efforts = game.efforts.all().prefetch_related(
        'player', 'vagabond', 'scorecard',
        Prefetch('faction__translations', queryset=translations, to_attr='filtered_translations')
    )

    for effort in efforts:
        if hasattr(effort.faction, 'filtered_translations') and effort.faction.filtered_translations:
            effort.translated_faction_title = effort.faction.filtered_translations[0].translated_title
        else:
            effort.translated_faction_title = effort.faction.title
    # _vlog.warning(f"[game_detail_view] efforts prefetch: {_time.time()-_t0:.3f}s")

    scorecard_count = ScoreCard.objects.filter(final=True, effort__in=game.efforts.all()).distinct().count()
    if scorecard_count != 0:
        show_detail = True
    # _vlog.warning(f"[game_detail_view] scorecard_count: {_time.time()-_t0:.3f}s")

    if request.user.is_authenticated:
        for effort in efforts:
            effort.available_scorecard = effort.available_scorecard(request.user)
        if game.final and (request.user.profile in participants):
            show_detail = True
    # _vlog.warning(f"[game_detail_view] available_scorecard: {_time.time()-_t0:.3f}s")

    edit_permission = game.can_edit(request.user.profile) if request.user.is_authenticated else EditPermission(False)

    tournament_round = game.round
    all_players = Profile.objects.none()
    open_roster = True

    if edit_permission:
        if tournament_round:
            if tournament_round.get_tournament().open_roster:
                all_players = Profile.objects.all()
                open_roster = True
            else:
                all_players = tournament_round.current_player_queryset()
                open_roster = False
        else:
            all_players = Profile.objects.all()
            open_roster = True
    # _vlog.warning(f"[game_detail_view] all_players: {_time.time()-_t0:.3f}s")

    commentform = GameCommentCreateForm()
    context = {
        'game': game,
        'commentform': commentform,
        'participants': participants,
        'efforts': efforts,
        'scorecard_count': scorecard_count,
        'show_detail': show_detail,
        'can_edit': edit_permission,
        'edit_permission': edit_permission,
        'all_players': all_players,
        'open_roster': open_roster,
    }
    # _vlog.warning(f"[game_detail_view] before render: {_time.time()-_t0:.3f}s")
    result = render(request, "the_warroom/game_detail_page.html", context)
    # _vlog.warning(f"[game_detail_view] total: {_time.time()-_t0:.3f}s")
    return result

@player_onboard_required
def game_delete_view(request, id=None):
    try:
        obj = Game.objects.get(id=id)
    except:
        obj = None

    if obj is None:
        if request.htmx:
            return HttpResponse("Not Found")
        raise Http404
    
    profile = request.user.profile
    permission = obj.can_edit(profile)
    if not permission.allowed:
        messages.error(request, "You do not have permission to delete this game.")
        return redirect(obj.get_absolute_url())

    if request.method == "POST":
        ScoreCard.objects.filter(effort__game=obj).update(final=False)
        obj.delete()
        success_url = reverse('games-home')
        if request.htmx:
            headers = {
                'HX-Redirect': success_url,
            }
            return HttpResponse("Success", headers=headers)
        return redirect(success_url)
    context=  {
        'game': obj
    }
    return render(request, "the_warroom/game_delete.html", context)








# ============================
# Effort Views (Player details for a game)
# ============================

@admin_required
@require_http_methods(['DELETE'])
def effort_hx_delete(request, id):
    if not request.htmx:
        raise Http404("Not an HTMX request")
    effort = get_object_or_404(Effort, id=id)
    game = effort.game
    # Delete the effort
    effort.delete()
    remaining_efforts = game.get_efforts()
    context = {
        'game': game,
        'efforts': remaining_efforts,
               }
    # Return updated game data
    return render(request, 'the_warroom/partials/game_detail.html', context)

@admin_required
@require_http_methods(['DELETE'])
def game_hx_delete(request, id):
    if not request.htmx:
        raise Http404("Not an HTMX request")
    game = get_object_or_404(Game, id=id)
    game.delete()

    response = HttpResponse(status=204)
    response['HX-Trigger'] = 'delete-game'
    return response


@login_required
def game_detail_hx_view(request, id=None):
    if not request.htmx:
        raise Http404
    try:
        obj = Game.objects.get(id=id)
    except:
        obj = None
    if obj is None:
        return HttpResponse('Game Not Found')
    context=  {
        'game': obj
    }
    return render(request, "the_warroom/partials/game_detail.html", context)




@player_onboard_required
def manage_game(request, id=None):
    if id:
        obj = get_object_or_404(Game, id=id)
    else:
        obj = Game()  # Create a new Game instance but do not save it yet
    user = request.user

    initial_game_status = obj.final

    # For prepopulating the round
    round_id = request.GET.get('series-round')  # Gets the ?series-round=123 value as a string
    selected_round = None

    if round_id:
        try:
            selected_round = Round.objects.get(id=round_id)
        except Round.DoesNotExist:
            selected_round = None 


    if id:
        if obj.final and not user.profile.admin:

            messages.error(request, "Game cannot be edited.")
            return redirect(obj.get_absolute_url())


        elif not user.profile.admin and user.profile != obj.recorder:
            messages.error(request, "You do not have permission to edit this game.")
            return redirect(obj.get_absolute_url())


    player_form = PlayerCreateForm()

    # Default to 4 players
    if id:  # Only check for existing efforts if updating an existing game
        existing_efforts = obj.efforts.all()
        existing_count = existing_efforts.count()
    else:
        existing_count = 0  # New game has no existing efforts

    extra_forms = max(0, 4 - existing_count)

    EffortFormset = modelformset_factory(Effort, form=EffortCreateForm, extra=extra_forms)
    qs = obj.efforts.all() if id else Effort.objects.none()  # Only fetch existing efforts if updating
    formset = EffortFormset(request.POST or None, queryset=qs)


    # Pass the formset to the parent form by storing it in the parent form instance
    # Allowing validation based on the total form
    form = GameCreateForm(request.POST or None, instance=obj, user=user, effort_formset=formset, round=selected_round)
    form_count = extra_forms + existing_count
    context = {
        'form': form,
        'formset': formset,
        'object': obj,
        'form_count': form_count,
        'player_form': player_form,
    }


    # Handle form submission
    if request.method == 'POST':
        if form.is_valid() and formset.is_valid():
            parent = form.save(commit=False)
            # Check if game is final
            if request.POST.get('final') == 'False':
                parent.final = False  # Save as draft
            else:
                parent.final = True  # Finalize the game
                # send_discord_message_task.delay(f'{user} Recorded a Game')

            if not id:
                parent.recorder = request.user.profile  # Set the recorder
            # parent.date_posted = timezone.now()
            # print(parent.date_posted)
            parent.save()  # Save the new or updated Game instance
            form.save_m2m()
            seat = 0
            game_status = max(parent.map.status, parent.deck.status)
            for landmark in parent.landmarks.all():
                game_status = max(game_status, landmark.status)
            for hireling in parent.hirelings.all():
                game_status = max(game_status, hireling.status)
            for tweak in parent.tweaks.all():
                game_status = max(game_status, tweak.status)
            # roster = []
            for form in formset:
                if form.cleaned_data.get('delete'):  # Check if the delete checkbox is checked
                    # print('delete found')
                    if form.instance.id:
                        form.instance.delete()
                    # formset.forms.remove(form)  # Delete the associated Effort instance
                elif not form.cleaned_data.get('faction') and not form.cleaned_data.get('score') and not form.cleaned_data.get('player'):
                    # print('empty found')
                    if form.instance.id:
                        form.instance.delete()
                else:
                    child = form.save(commit=False)
                    if child.faction_id is not None:  # Only save if faction_id is present

                        game_status = max(game_status, child.faction.status)
                        if child.vagabond:
                            game_status = max(game_status, child.vagabond.status)
                        seat += 1
                        # Save current status of faction. Might be useful somewhere.

                        child.faction_status = child.faction.status

                        child.game = parent  # Link the effort to the game
                        child.seat = seat
                        child.save()
                    
            parent.status = StatusChoices(game_status)
            parent.save()
            context['message'] = "Game Saved"


            if parent.final:
                fields = []
                fields.append({
                        'name': 'Recorder:',
                        'value': user.profile.name
                    })
                if parent.nickname:
                    game_title = parent.nickname
                else:
                    game_title = f"{parent.platform} Game"
                if not initial_game_status and obj.final:
                    send_rich_discord_message_task.delay(f'[{game_title}](https://therootdatabase.com{parent.get_absolute_url()})', category='New Game', title=f'Game Recorded', fields=fields)


            return redirect(parent.get_absolute_url())
        else:
            context['message'] = 'Game not Saved. Please correct errors below.'
    if request.htmx:
        return render(request, 'the_warroom/partials/forms.html', context)
    
    return render(request, 'the_warroom/record_game.html', context)


# ────────────────────────────────────────────────────────────────
# Game Form v2 — Dual-mode (match + standalone) with HTMX partials
# ────────────────────────────────────────────────────────────────

def _get_match_profiles(match):
    """Return Profile queryset of players seated in this match's series."""
    return Profile.objects.filter(
        tournament_participations__stage_participations__matchseat__series=match.series
    )


def _is_group_moderator(profile, match):
    """True if profile is the group_moderator of this match's player group."""
    group = match.player_group
    return bool(group and group.group_moderator_id == profile.id)


def _can_record_match(profile, match):
    """Check if profile can record a game for a match."""
    tournament = match.round.get_tournament()
    if profile.admin:
        return True
    if tournament.has_permission(profile):
        return True
    # Group moderators can always record their group's scheduled games,
    # regardless of the tournament's recording_access tier.
    if _is_group_moderator(profile, match):
        return True
    if not tournament.players_can_record_matches():
        return False
    return _get_match_profiles(match).filter(pk=profile.pk).exists()



@player_onboard_required
def manage_game_v2(request, id=None):
    """Game recording form v2. Supports match mode (?match=<id>) and standalone."""
    user = request.user
    match = None
    match_mode = False

    # Determine mode
    match_id = request.GET.get('match') or request.POST.get('match_id')
    if match_id:
        match = get_object_or_404(Match, id=match_id)
        match_mode = True

    # Load or create game
    if id:
        obj = get_object_or_404(Game, id=id)
        # Auto-detect match mode from existing game
        if not match_mode:
            try:
                existing_match = obj.match
                if existing_match:
                    match = existing_match
                    match_mode = True
            except Match.DoesNotExist:
                pass
    else:
        obj = Game()

    initial_game_status = obj.final

    # Block game recording if bracket is not finalized
    if match_mode and match.round.bracket_status != Round.BracketStatusChoices.FINALIZED:
        messages.error(request, "The bracket must be finalized before games can be recorded.")
        return redirect('round-matches-page', match.round.stage.tournament.slug, match.round.stage.slug, match.round.slug)

    # Permission checks
    if match_mode and not id:
        if not _can_record_match(user.profile, match):
            messages.error(request, "You do not have permission to record this match.")
            return redirect('games-home')
        # If match already has a game, load it for editing in-place
        if match.game_id:
            obj = match.game
            id = obj.id

    if id:
        if not obj.can_edit(user.profile):
            messages.error(request, "You do not have permission to edit this game.")
            return redirect(obj.get_absolute_url())

    # Prepopulate round from query param (standalone mode)
    round_id = request.GET.get('series-round')
    selected_round = None
    if round_id:
        try:
            selected_round = Round.objects.get(id=round_id)
            # Block players if tournament restricts recording
            if selected_round.stage:
                _tournament = selected_round.stage.tournament
                if _tournament and not _tournament.players_can_record_standalone():
                    if not (user.profile.admin or _tournament.has_permission(user.profile)):
                        messages.error(request, "Only tournament moderators can record games for this series.")
                        return redirect('games-home')
        except Round.DoesNotExist:
            pass

    player_form = PlayerCreateForm()

    # Determine effort count
    if id:
        existing_count = obj.efforts.count()
    else:
        existing_count = 0

    if match_mode and not id:
        seat_count = MatchSeat.objects.filter(series=match.series).count()
        extra_forms = max(0, seat_count - existing_count)
    else:
        extra_forms = max(0, 4 - existing_count)

    # Build formset
    EffortFormset = modelformset_factory(Effort, form=EffortCreateForm, extra=extra_forms)
    qs = obj.efforts.all() if id else Effort.objects.none()
    formset = EffortFormset(request.POST or None, queryset=qs)

    # Pre-populate effort forms with match seat players (for new games in match mode)
    match_seats = []
    if match_mode:
        match_seats = list(
            MatchSeat.objects.filter(series=match.series)
            .select_related('stage_participant__tournament_player__profile')
            .order_by('seat_number')
        )
        # Restrict player dropdown to match participants only
        match_profiles = _get_match_profiles(match)
        for form in formset.forms:
            form.fields['player'].queryset = match_profiles
        # if not id and not request.POST:
        #     for i, seat in enumerate(match_seats):
        #         if i < len(formset.forms):
        #             profile_obj = seat.stage_participant.tournament_player.profile
        #             formset.forms[i].initial['player'] = profile_obj.pk

    # Build game form
    form = GameCreateFormV2(
        request.POST or None,
        instance=obj,
        user=user,
        effort_formset=formset,
        match=match,
        round=selected_round if not match_mode else None,
    )

    # Auto-fill nickname with match name in match mode
    if match_mode and not obj.pk:
        form.fields['nickname'].initial = (f'{match.round} {match.name}' or '')[:50]

    # Determine platform lock status for template rendering
    platform_locked = False
    locked_platform = None
    if match:
        tournament = match.round.get_tournament()
        if tournament.platform:
            platform_locked = True
            locked_platform = tournament.platform

    context = {
        'form': form,
        'formset': formset,
        'object': obj,
        'form_count': extra_forms + existing_count,
        'match': match,
        'match_mode': match_mode,
        'match_seats': match_seats,
        'player_form': player_form,
        'platform_locked': platform_locked,
        'locked_platform': locked_platform,
    }

    if request.method == 'POST':
        if form.is_valid() and formset.is_valid():
            import time as _time
            import logging as _logging
            _vlog = _logging.getLogger(__name__)
            _t0 = _time.time()

            parent = form.save(commit=False)

            # Set final status
            if request.POST.get('final') == 'False':
                parent.final = False
            else:
                parent.final = True

            if not id:
                parent.recorder = user.profile

            # For match mode, ensure round is set (disabled field not in cleaned_data)
            if match_mode and match:
                parent.round = match.round

            parent.save()
            form.save_m2m()
            _vlog.warning(f"[manage_game_v2] parent.save+m2m: {_time.time()-_t0:.3f}s")

            # Process seat ordering
            seat_order_str = request.POST.get('seat_order', '')
            seat_order = seat_order_str.split(',') if seat_order_str else None

            game_status = max(parent.map.status, parent.deck.status)
            for landmark in parent.landmarks.all():
                game_status = max(game_status, landmark.status)
            for hireling in parent.hirelings.all():
                game_status = max(game_status, hireling.status)
            for tweak in parent.tweaks.all():
                game_status = max(game_status, tweak.status)

            saved_efforts = []
            for idx, effort_form in enumerate(formset):
                if effort_form.cleaned_data.get('delete'):
                    if effort_form.instance.id:
                        effort_form.instance.delete()
                elif not effort_form.cleaned_data.get('faction') and not effort_form.cleaned_data.get('score') and not effort_form.cleaned_data.get('player'):
                    if effort_form.instance.id:
                        effort_form.instance.delete()
                else:
                    child = effort_form.save(commit=False)
                    if child.faction_id is not None:
                        game_status = max(game_status, child.faction.status)
                        if child.vagabond:
                            game_status = max(game_status, child.vagabond.status)
                        child.faction_status = child.faction.status
                        child.game = parent
                        saved_efforts.append((idx, child))

            # Assign seats based on seat_order or sequential
            if seat_order:
                form_index_to_seat = {}
                for seat_num, form_idx_str in enumerate(seat_order, start=1):
                    try:
                        form_index_to_seat[int(form_idx_str)] = seat_num
                    except (ValueError, TypeError):
                        pass
                for idx, child in saved_efforts:
                    child.seat = form_index_to_seat.get(idx, idx + 1)
                    child.save()
            else:
                for seat_num, (idx, child) in enumerate(saved_efforts, start=1):
                    child.seat = seat_num
                    child.save()
            _vlog.warning(f"[manage_game_v2] after effort saves: {_time.time()-_t0:.3f}s")

            parent.status = StatusChoices(game_status)
            parent.save()
            _vlog.warning(f"[manage_game_v2] after parent.save (status): {_time.time()-_t0:.3f}s")

            # Match linkage
            if match_mode and match:
                if not match.game_id or match.game_id != parent.id:
                    match.game = parent
                match.status = CompetitionStatus.COMPLETED if parent.final else CompetitionStatus.ACTIVE
                match.save()
                _vlog.warning(f"[manage_game_v2] after match.save: {_time.time()-_t0:.3f}s")

                # Trigger series/round completion logic
                if parent.final:
                    from the_warroom.services.bracket import BracketService
                    BracketService.on_game_complete(match)
                _vlog.warning(f"[manage_game_v2] after on_game_complete: {_time.time()-_t0:.3f}s")

            # ScoreCard consistency checks
            for idx, child in saved_efforts:
                try:
                    scorecard = child.scorecard
                except ScoreCard.DoesNotExist:
                    continue
                if scorecard is None:
                    continue
                # Faction mismatch → detach scorecard
                if child.faction_id != scorecard.faction_id:
                    scorecard.effort = None
                    scorecard.final = False
                    scorecard.save(update_fields=['effort', 'final'])
                    continue
                # Score or dominance mismatch → mark non-final
                score_mismatch = (child.score != scorecard.total_points)
                dominance_mismatch = (bool(child.dominance) != scorecard.dominance)
                if score_mismatch or dominance_mismatch:
                    scorecard.final = False
                    scorecard.save(update_fields=['final'])
            _vlog.warning(f"[manage_game_v2] after scorecard checks: {_time.time()-_t0:.3f}s")

            # Discord notification
            if parent.final:
                fields = []
                fields.append({
                    'name': 'Recorder:',
                    'value': user.profile.name
                })
                game_title = parent.nickname if parent.nickname else f"{parent.platform} Game"
                if not initial_game_status and obj.final:
                    send_rich_discord_message_task.delay(
                        f'[{game_title}](https://therootdatabase.com{parent.get_absolute_url()})',
                        category='New Game', title='Game Recorded', fields=fields
                    )
                    # DM opted-in players / component designers / tournament hosts
                    from the_gatehouse.services.notifyservice import notify_game_recorded
                    notify_game_recorded(parent)
            _vlog.warning(f"[manage_game_v2] total before redirect: {_time.time()-_t0:.3f}s")

            return redirect(parent.get_absolute_url())
        else:
            context['message'] = 'Game not Saved. Please correct errors below.'

    return render(request, 'the_warroom/record_game_v2.html', context)


from django.http import JsonResponse
from django.views.decorators.http import require_POST


@player_required
@require_POST
def add_player_to_effort(request):
    effort_id = request.POST.get('effort_id')
    player_id = request.POST.get('player_id')
    
    try:
        effort = Effort.objects.get(id=effort_id)
        player = Profile.objects.get(id=player_id)
        

        if effort.player:
            return JsonResponse({
                'success': False,
                'error': f'{effort.player.discord} is already recorded for seat {effort.seat}.'
            }, status=400)

        tournament_round = effort.game.round
        # Check tournament roster restrictions
        if tournament_round and not tournament_round.get_tournament().open_roster:
            allowed_players = tournament_round.current_player_queryset()
            
            if not allowed_players.filter(id=player.id).exists():
                
                return JsonResponse({
                    'success': False,
                    'error': f'{player.name} is not registered for {tournament_round}.'
                }, status=403)


        # Check if user has permission to edit this game
        if not (request.user.profile.admin or 
                effort.game.recorder == request.user.profile):
            return JsonResponse({
                'success': False,
                'error': 'You do not have permission to edit this game.'
            }, status=403)
        
        # Update the effort
        effort.player = player
        effort.save()
        
        return JsonResponse({
            'success': True,
            'player_discord': player.discord,
            'message': f'Player {player.discord} added to effort successfully!'
        })
        
    except Effort.DoesNotExist:
        return JsonResponse({
            'success': False,
            'error': 'Effort not found.'
        }, status=404)
    except Profile.DoesNotExist:
        return JsonResponse({
            'success': False,
            'error': 'Player not found.'
        }, status=404)
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=500)



@player_required
@bookmark_toggle(Game)
def bookmark_game(request, obj):
    return render(request, 'the_warroom/partials/bookmarks.html', {'game': obj})








# ============================
# Scorecard Views
# ============================

# Create and edit a scorecard
@player_onboard_required
def scorecard_old_manage_view(request, id=None):
    # existing_scorecard = False
    faction = request.GET.get('faction', None)
    effort_id = request.GET.get('effort', None)
    game_group = request.GET.get('game_group', None)
    # if faction:
    #     faction_name = Faction.objects.get(id=faction).title
    # else:
    #     faction_name = None
    faction_name = (
        Faction.objects.filter(id=faction)
        .values_list('title', flat=True)
        .first()
        if faction else None
    )

    game = None
    next_scorecard = None
    previous_scorecard = None
    try:
        effort = Effort.objects.get(id=effort_id)

        game = effort.game
        participants = []
        for participant_effort in game.efforts.all():
            participants.append(participant_effort.player)
        if game.recorder:
            participants.append(game.recorder)
        if not request.user.profile in participants:
            raise PermissionDenied() 

    except Effort.DoesNotExist:
        effort = None
        next_effort = None
        next_effort_scorecard = None
        previous_effort = None
        previous_effort_scorecard = None


    if id:
        obj = get_object_or_404(ScoreCard, id=id)
        # Check if the current user is the same as the recorder
        if obj.recorder != request.user.profile:
            raise PermissionDenied() 
        if obj.faction != None:
            faction = obj.faction.id
            # print(faction)
        if obj.effort != None:
            effort = obj.effort
            game = effort.game
        # existing_scorecard = True


        if not obj.effort:
            # Filter scorecards by category (or order) to group them together
            grouped_scorecards = ScoreCard.objects.filter(
                game_group=obj.game_group, effort=None, recorder=request.user.profile
                ).order_by('date_posted')

            scorecard_list = list(grouped_scorecards)
            current_index = scorecard_list.index(obj)

            # Get the next and previous scorecards
            next_scorecard = scorecard_list[(current_index + 1) % len(scorecard_list)]
            previous_scorecard = scorecard_list[(current_index - 1) % len(scorecard_list)]
            # Set next and previous to None if they are the same as the current scorecard
            if next_scorecard == obj:
                next_scorecard = None
            if previous_scorecard == obj:
                previous_scorecard = None


    else:
        obj = ScoreCard()  # Create a new ScoreCard instance but do not save it yet
        if game_group:
            grouped_scorecards = ScoreCard.objects.filter(
                game_group=game_group, effort=None, recorder=request.user.profile
                ).order_by('date_posted')
            # print(game_group)
            scorecard_list = list(grouped_scorecards)
            # print(scorecard_list)
            if scorecard_list:
                # Get the next scorecard (always the first one)
                next_scorecard = scorecard_list[0]
                # Get the previous scorecard (always the last one)
                previous_scorecard = scorecard_list[-1]



    if effort and game:
        game_efforts = Effort.objects.filter(game=game).order_by('seat')

        effort_list = list(game_efforts)
        current_index = effort_list.index(effort)

        # Get the next and previous scorecards
        next_effort = effort_list[(current_index + 1) % len(effort_list)]
        previous_effort = effort_list[(current_index - 1) % len(effort_list)]
        next_effort_scorecard = getattr(next_effort, 'scorecard', None)
        previous_effort_scorecard = getattr(previous_effort, 'scorecard', None)


    user = request.user

    

    if id:  # Only check for existing turns if updating an existing game score
        existing_turns = obj.turns.all()
        existing_count = existing_turns.count()
        extra_forms = 0
    else:
        existing_count = 0  # New game has no existing turns
        extra_forms = 1
    TurnFormset = modelformset_factory(TurnScore, form=TurnScoreCreateForm, extra=extra_forms)
    qs = obj.turns.all() if id else TurnScore.objects.none()  # Only fetch existing turns if updating
    formset = TurnFormset(request.POST or None, queryset=qs)
    form_count = extra_forms + existing_count        
    form = ScoreCardCreateForm(request.POST or None, instance=obj, user=user, faction=faction)
    if effort:
        score = effort.score
    else:
        score = None
    # print(obj.total_points)
    # print(obj.total_points)
    if not obj.total_generic_points and obj.id and obj.total_points and obj.total_points != 0:
        generic_view = False
    else:
        generic_view = True

    context = {
        'form': form,
        'formset': formset,
        'object': obj,
        'form_count': form_count,
        'faction': faction,
        'faction_name': faction_name,
        'score': score,
        'next_scorecard': next_scorecard,
        'previous_scorecard': previous_scorecard,
        'game_group': game_group,
        'generic_view': generic_view,
        'next_effort': next_effort,
        'next_effort_scorecard': next_effort_scorecard,
        'previous_effort': previous_effort,
        'previous_effort_scorecard': previous_effort_scorecard,
        'game': game
    }

    # Handle form submission
    if form.is_valid() and formset.is_valid():
        warning_message = False
        # print('valid form')
        # print(formset.cleaned_data)
        parent = form.save(commit=False)
        parent.recorder = request.user.profile  # Set the recorder
        parent.effort = effort
        parent.save()
        dominance = False

        # Calculate the total points from the formset
        total_points = 0
        for child_form in formset:
            # print(child_form.cleaned_data)

            turn_dominance = child_form.cleaned_data.get('dominance')
            if turn_dominance:
                dominance = True
            total_points += child_form.cleaned_data.get('total_points', 0)
        if effort:
            final_scorecard = True
        else:
            final_scorecard = False
        if effort and total_points != effort.score and effort.score:
            final_scorecard = False
            # If points don't match effort.score
            warning_message = True
            messages.info(request, f"Progress Saved. The recorded points, {total_points}, did not match the total game points, {effort.score}.")
            # context['message'] = f"Error: The recorded points ({total_points}) do not match the total game points ({effort.score})."
            # if existing_scorecard == False:
            #     parent.delete()
            # return render(request, 'the_warroom/record_scores.html', context)


        elif total_points < 0:
            final_scorecard = False
            # If points are negative
            warning_message = True
            messages.info(request, f"Progress Saved. The recorded points, {total_points}, were negative.")
            # context['message'] = f"Error: The recorded points ({total_points}) are negative."
            # if existing_scorecard == False:
            #     parent.delete()
            # return render(request, 'the_warroom/record_scores.html', context)

        elif effort and effort.score == 0 and total_points != 0 and not effort.dominance and not effort.coalition_with:
            final_scorecard = False
            warning_message = True
            messages.info(request, f"Progress Saved. The recorded points, {total_points}, did not match the total game points, {effort.score}.")


        if (effort and dominance and not effort.dominance and not effort.coalition_with) or (effort and not dominance and effort.dominance) or (effort and not dominance and effort.coalition_with):
            final_scorecard = False
            if dominance:
                warning_message = True
                messages.info(request, f"Progress Saved. Dominance not found in game data.")
                # context['message'] = f"Dominance Error: The original effort does not have a Dominance Played."
            else:
                warning_message = True
                messages.info(request, f"Progress Saved. Dominance missing.")
                # context['message'] = f"Dominance Error: The original effort has an active Dominance Played."

        
        for turn_form in formset:
            form_delete = turn_form.cleaned_data['delete']
            # print(form_delete)
            child = turn_form.save(commit=False)
            child.scorecard = parent  # Link the turn to the game
            child.save()

        if parent.dominance != dominance:
            parent.dominance = dominance
            # print("dominance", dominance)
            # parent.save()  # Save the new or updated Game Score instance

        if parent.final != final_scorecard:
            parent.final = final_scorecard
            # parent.save()  # Save with the new final status

        # Save and recalculate game points for each Turn
        parent.save(recalculate_game_points=True)

        if not warning_message:
            if final_scorecard:
                messages.success(request, f"Scores Saved for {parent.faction}")
            else:
                messages.info(request, f"Progress Saved for {parent.faction}")

        # Check if the "next" button was clicked
        if request.POST.get('next'):
            # Redirect to the update page of the next scorecard
            return redirect('update-old-scorecard', next_scorecard.id)
        # Check if the "previous" button was clicked
        if request.POST.get('previous'):
            # Redirect to the update page of the next scorecard
            return redirect('update-old-scorecard', previous_scorecard.id)
        # Check if the "add_player" button was clicked
        if request.POST.get('add_player'):
            # Redirect to the record page to record new
            game_group = parent.game_group
            encoded_game_group = quote(str(game_group))  # Encode the game_group value
            return redirect(f'{reverse("record-old-scorecard")}?game_group={encoded_game_group}')
        if request.POST.get('next-effort'):
            if next_effort_scorecard:
                return redirect('update-old-scorecard', next_effort_scorecard.id)
            else:
                url = reverse('record-old-scorecard')
                query_string = f'?faction={next_effort.faction.id}&effort={next_effort.id}'
                return redirect(f'{url}{query_string}')
        if request.POST.get('previous-effort'):
            if previous_effort_scorecard:
                return redirect('update-old-scorecard', previous_effort_scorecard.id)
            else:
                url = reverse('record-old-scorecard')
                query_string = f'?faction={previous_effort.faction.id}&effort={previous_effort.id}'
                return redirect(f'{url}{query_string}')
        context['message'] = "Scores Saved"
        return redirect(parent.get_absolute_url())
    return render(request, 'the_warroom/record_scores.html', context)


# Detail view of a scorecard

def scorecard_detail_view(request, id=None):
    try:
        obj = ScoreCard.objects.get(id=id)
    except ObjectDoesNotExist:
        obj = None
    language_code = get_language()
    language_object = Language.objects.filter(code=language_code).first()
    object_translation = obj.faction.translations.filter(language=language_object).first()
    object_title = object_translation.translated_title if object_translation and object_translation.translated_title else obj.faction.title

    # next_scorecard = None   
    # previous_scorecard = None
    # if not obj.effort:
    #     # Filter scorecards by category (or order) to group them together
    #     grouped_scorecards = ScoreCard.objects.filter(
    #         game_group=obj.game_group, effort=None, recorder=request.user.profile
    #         ).order_by('date_posted')

    #     scorecard_list = list(grouped_scorecards)
    #     current_index = scorecard_list.index(obj)

    #     # Get the next and previous scorecards
    #     next_scorecard = scorecard_list[(current_index + 1) % len(scorecard_list)]
    #     previous_scorecard = scorecard_list[(current_index - 1) % len(scorecard_list)]
    #     # Set next and previous to None if they are the same as the current scorecard
    #     if next_scorecard == obj:
    #         next_scorecard = None
    #     if previous_scorecard == obj:
    #         previous_scorecard = None

    context=  {
        'object': obj,
        # 'previous_scorecard': previous_scorecard,
        # 'next_scorecard': next_scorecard,
        'object_title': object_title,
    }

    return render(request, "the_warroom/score_detail.html", context)


# Choose from available scorecards to link to a game
@player_onboard_required
def scorecard_assign_view(request, id):
    effort_link = get_object_or_404(Effort, id=id)

    selected_scorecard = request.GET.get('scorecard')
    # print(selected_scorecard)
    game = effort_link.game
    participants = []
    for participant_effort in game.efforts.all():
        participants.append(participant_effort.player)
    if game.recorder:
        participants.append(game.recorder)
    if not request.user.profile in participants:
        raise PermissionDenied() 
    dominance = False
    if effort_link.dominance:
        dominance = True
    if request.method == 'POST':
        form = AssignScorecardForm(request.POST, user=request.user, faction=effort_link.faction, total_points=effort_link.score, selected_scorecard=selected_scorecard, dominance=dominance)
        if form.is_valid():
            scorecard = form.cleaned_data['scorecard']
            scorecard.effort = effort_link  # Assign the Effort to the selected Scorecard
            scorecard.final = True
            scorecard.save()  # Save the updated Scorecard
            return redirect('game-detail', id=effort_link.game.id)
    else:
        form = AssignScorecardForm(user=request.user, faction=effort_link.faction, total_points=effort_link.score, selected_scorecard=selected_scorecard, dominance=dominance)

    context = {
        'form': form, 
        'effort_link': effort_link,
        'game': game,
        }

    return render(request, 'the_warroom/assign_scorecard.html', context)

# Choose from available games to link a scorecard
@player_onboard_required
def effort_assign_view(request, id):

    scorecard = get_object_or_404(ScoreCard, id=id)

    if scorecard.effort:
        return redirect('detail-scorecard', id=scorecard.id)

    if not scorecard.dominance:
        available_efforts = Effort.objects.filter(
                Q(game__efforts__player=request.user.profile) |  # Player is linked to the game
                Q(game__recorder=request.user.profile),  # Recorder is the current user
                scorecard=None, faction=scorecard.faction,
                score=scorecard.total_points, game__final=True  # Effort has no associated scorecard
            ).prefetch_related(
                'game', 'game__deck', 'game__map', 'game__round', 'game__round__stage__tournament', 'game__round', 'game__landmarks', 'game__tweaks', 'game__hirelings',
                'game__efforts', 'game__efforts__faction', 'game__efforts__player', 'game__efforts__vagabond').distinct()
    else:
        available_efforts = Effort.objects.filter(
                Q(game__efforts__player=request.user.profile) |  # Player is linked to the game
                Q(game__recorder=request.user.profile),  # Recorder is the current user
                scorecard=None, faction=scorecard.faction, 
                dominance__isnull=False # Effort has no associated scorecard
            ).prefetch_related(
                'game', 'game__deck', 'game__map', 'game__round', 'game__round__stage__tournament', 'game__round', 'game__landmarks', 'game__tweaks', 'game__hirelings',
                'game__efforts', 'game__efforts__faction', 'game__efforts__player', 'game__efforts__vagabond').distinct()
        

    if request.method == 'POST':
        form = AssignEffortForm(request.POST, selected_efforts=available_efforts, user=request.user)
        # print('submitting form')
        if form.is_valid():
            # print('valid form')
            scorecard = scorecard
            scorecard.effort = form.cleaned_data['effort']  # Assign the Effort to the selected Scorecard
            scorecard.final = True
            scorecard.save()  # Save the updated Scorecard
            return redirect('game-detail', id=scorecard.effort.game.id)
    else:
        form = AssignEffortForm(selected_efforts=available_efforts, user=request.user)

    context = {
        'form': form, 
        'scorecard': scorecard,
        }

    return render(request, 'the_warroom/assign_effort.html', context)


@player_onboard_required
def scorecard_delete_view(request, id=None):
    try:
        obj = ScoreCard.objects.get(id=id, recorder=request.user.profile)
    except:
        obj = None
    if obj is None:
        if request.htmx:
            return HttpResponse("Not Found")
        raise Http404
    if request.method == "POST":
        if obj.effort:
            success_url = obj.effort.game.get_absolute_url()
        else:
            success_url = reverse('scorecard-home')
        obj.delete()
        if request.htmx:
            headers = {
                'HX-Redirect': success_url,
            }
            return HttpResponse("Success", headers=headers)
        return redirect(success_url)
    context=  {
        'object': obj
    }
    return render(request, "the_warroom/score_delete.html", context)


@player_required
def scorecard_list_view(request):
    active_profile = request.user.profile
    language_code = get_language()
    language_object = Language.objects.filter(code=language_code).first()
    complete_scorecards = ScoreCard.objects.filter(recorder=request.user.profile, final=True).prefetch_related('turns', 'faction', 'effort', 'effort__game', 'effort__player', 'effort__faction')


    complete_scorecards = complete_scorecards.annotate(
        selected_title=Coalesce(
            Subquery(
                PostTranslation.objects.filter(
                    post=OuterRef('faction__pk'),
                    language__code=language_object.code
                ).values('translated_title')[:1]
            ),
            F('faction__title')  # Fallback to the original faction title
        )
    )
    
    # Pagination (the rest of your pagination logic stays the same)
    paginator = Paginator(complete_scorecards, settings.PAGE_SIZE)
    page_number = request.GET.get('page')
    try:
        page_obj = paginator.get_page(page_number)
    except PageNotAnInteger:
        page_obj = paginator.get_page(1)
    except EmptyPage:
        page_obj = paginator.get_page(paginator.num_pages)

    context = {

        'complete_scorecards': page_obj,
        'active_profile': active_profile,

    }

    if request.htmx:
        return render(request, "the_warroom/partials/scorecard_list_partial.html", context)

    return render(request, 'the_warroom/scorecard_list.html', context)












# ============================
# Tournament Views
# ============================


def _build_used_asset_types(games_qs):
    """Build asset_types list from assets actually used in finalized games."""
    factions = Faction.objects.filter(efforts__game__in=games_qs).distinct()
    vagabonds = Vagabond.objects.filter(efforts__game__in=games_qs).distinct()
    maps = Map.objects.filter(games__in=games_qs).distinct()
    decks = Deck.objects.filter(games__in=games_qs).distinct()
    landmarks = Landmark.objects.filter(games__in=games_qs).distinct()
    tweaks = Tweak.objects.filter(games__in=games_qs).distinct()
    hirelings = Hireling.objects.filter(games__in=games_qs).distinct()

    return [
        ('faction', 'Factions', factions, 'bi-shield'),
        ('map', 'Maps', maps, 'bi-map'),
        ('deck', 'Decks', decks, 'bi-stack'),
        ('hireling', 'Hirelings', hirelings, 'bi-person-badge'),
        ('landmark', 'Landmarks', landmarks, 'bi-geo-alt'),
        ('tweak', 'House Rules', tweaks, 'bi-wrench'),
        ('vagabond', 'Vagabonds', vagabonds, 'bi-person-walking'),
    ]


# Roles a tournament manager can preview a page as, ordered high -> low privilege.
VIEW_AS_OPTIONS = (
    ('moderator', _lazy('Moderator')),
    ('player', _lazy('Registered Player')),
    ('logged_in', _lazy('Logged In')),
    ('logged_out', _lazy('Logged Out')),
)
VIEW_AS_ROLES = tuple(value for value, _ in VIEW_AS_OPTIONS)


def get_view_as(request, tournament):
    """Return the active 'view as' role for this tournament, or None.

    Only honored when the REAL user has manage permission on the tournament.
    Stored in session keyed by tournament id so it persists across tabs/stages/rounds.
    """
    if not request.user.is_authenticated:
        return None
    if not tournament.has_permission(request.user.profile):
        return None
    role = (request.session.get('view_as') or {}).get(str(tournament.id))
    return role if role in VIEW_AS_ROLES else None


def apply_view_as_can_manage(view_as, can_manage):
    """Downgrade `can_manage` to match an emulated role (display-only, downgrade-only).

    Only a moderator retains manage rights; all lower roles lose them. `playable_round`
    is recomputed separately via ``user_can_record_in_round(..., as_role=view_as)``.
    """
    if not view_as or view_as == 'moderator':
        return can_manage
    return False


# Preview roles that have no access to the manager-only settings hub (they would
# get a 403). Previewing as one of these shows a "no access" notice instead of cards.
VIEW_AS_NO_SETTINGS_ACCESS = ('player', 'logged_in', 'logged_out')


def settings_view_as_context(request, tournament, is_owner):
    """View-as context for the settings hub.

    Returns (context_dict, no_access, is_owner). The settings hub is manager-only,
    so previewing as a lower role renders a 'cannot view this page' notice; previewing
    as a moderator hides owner-only cards by forcing is_owner False.
    """
    view_as = get_view_as(request, tournament)
    no_access = view_as in VIEW_AS_NO_SETTINGS_ACCESS
    if view_as == 'moderator':
        is_owner = False
    ctx = {
        'can_view_as': request.user.is_authenticated and tournament.has_permission(request.user.profile),
        'view_as': view_as,
        'view_as_options': VIEW_AS_OPTIONS,
        'view_as_no_access': no_access,
    }
    return ctx, no_access, is_owner


@require_POST
def set_view_as(request, slug):
    """Set or clear the 'view as' preview role for a tournament (display-only)."""
    tournament = get_object_or_404(Tournament, slug=slug.lower())
    if not (request.user.is_authenticated and tournament.has_permission(request.user.profile)):
        raise PermissionDenied
    role = request.POST.get('role')
    store = request.session.get('view_as') or {}
    if role in VIEW_AS_ROLES:
        store[str(tournament.id)] = role
    else:
        # 'designer' / reset / anything invalid clears the override.
        store.pop(str(tournament.id), None)
    request.session['view_as'] = store
    request.session.modified = True
    return redirect(request.POST.get('next') or tournament.get_absolute_url())


def _tournament_base_context(request, tournament):
    """Shared context for all tournament pages."""
    view_as = get_view_as(request, tournament)
    playable_round = None
    if request.user.is_authenticated:
        active_rounds = Round.objects.filter(
            stage__tournament=tournament,
        ).is_available()
        if active_rounds.count() == 1:
            candidate = active_rounds.first()
            if user_can_record_in_round(candidate, request.user, as_role=view_as):
                playable_round = candidate

    can_manage = request.user.is_authenticated and tournament.has_permission(request.user.profile) and request.user.profile.player
    can_view_as = request.user.is_authenticated and tournament.has_permission(request.user.profile)
    can_manage = apply_view_as_can_manage(view_as, can_manage)
    has_bracket = Match.objects.filter(round__stage__tournament=tournament).exists()
    has_games = Game.objects.filter(round__stage__tournament=tournament).exists()
    has_players = TournamentPlayer.objects.filter(tournament=tournament).exists()

    from the_tavern.models import Survey
    has_surveys = Survey.objects.filter(series=tournament).exists()

    user_in_guild = (
        request.user.is_authenticated
        and tournament.guild
        and request.user.profile.guilds.filter(pk=tournament.guild_id).exists()
    )

    return {
        'tournament': tournament,
        'object': tournament,
        'playable_round': playable_round,
        'can_manage': can_manage,
        'can_view_as': can_view_as,
        'view_as': view_as,
        'view_as_options': VIEW_AS_OPTIONS,
        'has_bracket': has_bracket,
        'has_games': has_games,
        'has_players': has_players,
        'has_surveys': has_surveys,
        'tab_visible': {key: tournament.tab_visible(key) for key in Tournament.HIDEABLE_TABS},
        'user_in_guild': user_in_guild,
        'meta_title': tournament.name,
        'meta_description': tournament.description,
    }


def _stage_base_context(request, tournament, stage):
    """Shared context for all stage tab pages."""
    view_as = get_view_as(request, tournament)
    playable_round = None
    if request.user.is_authenticated:
        active_rounds = stage.rounds.is_available()
        if active_rounds.count() == 1:
            candidate = active_rounds.first()
            if user_can_record_in_round(candidate, request.user, as_role=view_as):
                playable_round = candidate

    can_manage = request.user.is_authenticated and tournament.has_permission(request.user.profile) and request.user.profile.player
    can_view_as = request.user.is_authenticated and tournament.has_permission(request.user.profile)
    can_manage = apply_view_as_can_manage(view_as, can_manage)
    has_bracket = Match.objects.filter(round__stage=stage).exists()
    has_games = Game.objects.filter(round__stage=stage).exists()
    has_players = StageParticipant.objects.filter(stage=stage).exists()

    from the_tavern.models import Survey
    has_surveys = Survey.objects.filter(series=tournament, stage=stage).exists()

    user_in_guild = (
        request.user.is_authenticated
        and tournament.guild
        and request.user.profile.guilds.filter(pk=tournament.guild_id).exists()
    )

    return {
        'tournament': tournament,
        'stage': stage,
        'object': stage,
        'playable_round': playable_round,
        'can_manage': can_manage,
        'can_view_as': can_view_as,
        'view_as': view_as,
        'view_as_options': VIEW_AS_OPTIONS,
        'has_bracket': has_bracket,
        'has_games': has_games,
        'has_players': has_players,
        'has_surveys': has_surveys,
        'tab_visible': {key: tournament.tab_visible(key) for key in Tournament.HIDEABLE_TABS},
        'user_in_guild': user_in_guild,
        'meta_title': f"{stage.name} - {tournament.name}",
        'meta_description': tournament.description or '',
    }


def _round_base_context(request, tournament, stage, round):
    """Shared context for all round tab pages."""
    view_as = get_view_as(request, tournament)
    playable_round = round if user_can_record_in_round(round, request.user, as_role=view_as) else None

    can_manage = request.user.is_authenticated and tournament.has_permission(request.user.profile) and request.user.profile.player
    can_view_as = request.user.is_authenticated and tournament.has_permission(request.user.profile)
    can_manage = apply_view_as_can_manage(view_as, can_manage)
    has_matches = MatchSeries.objects.filter(round=round).exists()
    has_games = Game.objects.filter(round=round).exists()
    has_players = StageParticipant.objects.filter(stage=stage).exists()

    is_bracket_finalized = round.bracket_status == Round.BracketStatusChoices.FINALIZED

    user_in_guild = (
        request.user.is_authenticated
        and tournament.guild
        and request.user.profile.guilds.filter(pk=tournament.guild_id).exists()
    )

    return {
        'tournament': tournament,
        'stage': stage,
        'round': round,
        'object': round,
        'playable_round': playable_round,
        'can_manage': can_manage,
        'can_view_as': can_view_as,
        'view_as': view_as,
        'view_as_options': VIEW_AS_OPTIONS,
        'has_matches': has_matches,
        'has_games': has_games,
        'has_players': has_players,
        'is_bracket_finalized': is_bracket_finalized,
        'tab_visible': {key: tournament.tab_visible(key) for key in Tournament.HIDEABLE_TABS},
        'user_in_guild': user_in_guild,
        'meta_title': f"{round.name} - {stage.name} - {tournament.name}",
        'meta_description': tournament.description or '',
    }


def tournament_overview_page(request, slug):
    tournament = get_object_or_404(Tournament, slug=slug.lower())

    context = _tournament_base_context(request, tournament)
    context['active_page'] = 'overview'

    if tournament.use_stages:
        children = Stage.objects.filter(tournament=tournament).select_related('tournament').annotate(
            annotated_game_count=Count(
                'rounds__games',
                filter=Q(rounds__games__final=True),
                distinct=True
            ),
            unique_players_count=Count(
                'rounds__games__efforts__player',
                distinct=True
            ),
            annotated_scheduled_count=Count(
                'rounds__series__matches',
                filter=Q(
                    rounds__bracket_status=Round.BracketStatusChoices.FINALIZED,
                    rounds__series__matches__scheduled_time__isnull=False,
                    rounds__series__matches__status__in=[CompetitionStatus.PENDING, CompetitionStatus.ACTIVE]
                ),
                distinct=True
            ),
            annotated_pending_count=Count(
                'rounds__series__matches',
                filter=Q(
                    rounds__bracket_status=Round.BracketStatusChoices.FINALIZED,
                    rounds__series__matches__scheduled_time__isnull=True,
                    rounds__series__matches__status__in=[CompetitionStatus.PENDING, CompetitionStatus.ACTIVE]
                ),
                distinct=True
            ),
        ).order_by('order')
        context['children'] = children
        context['children_type'] = 'stages'
    else:
        single_stage = get_single_stage(tournament)
        if single_stage:
            if single_stage.use_rounds:
                children = Round.objects.filter(stage=single_stage).select_related('stage__tournament').annotate(
                    annotated_game_count=Count(
                        'games',
                        filter=Q(games__final=True),
                        distinct=True
                    ),
                    unique_players_count=Count(
                        'games__efforts__player',
                        distinct=True
                    ),
                    annotated_scheduled_count=Count(
                        'series__matches',
                        filter=Q(
                            bracket_status=Round.BracketStatusChoices.FINALIZED,
                            series__matches__scheduled_time__isnull=False,
                            series__matches__status__in=[CompetitionStatus.PENDING, CompetitionStatus.ACTIVE]
                        ),
                        distinct=True
                    ),
                    annotated_pending_count=Count(
                        'series__matches',
                        filter=Q(
                            bracket_status=Round.BracketStatusChoices.FINALIZED,
                            series__matches__scheduled_time__isnull=True,
                            series__matches__status__in=[CompetitionStatus.PENDING, CompetitionStatus.ACTIVE]
                        ),
                        distinct=True
                    ),
                ).order_by('round_number')
                context['children'] = children
                context['children_type'] = 'rounds'
                context['single_stage'] = single_stage
            else:
                single_round = get_single_round(tournament, stage=single_stage)
                context['single_round'] = single_round
                context['single_stage'] = single_stage

    context['scheduled_count'] = Match.objects.filter(
        round__stage__tournament=tournament,
        round__bracket_status=Round.BracketStatusChoices.FINALIZED,
        scheduled_time__isnull=False,
        status__in=[CompetitionStatus.PENDING, CompetitionStatus.ACTIVE]
    ).count()
    context['pending_count'] = Match.objects.filter(
        round__stage__tournament=tournament,
        round__bracket_status=Round.BracketStatusChoices.FINALIZED,
        scheduled_time__isnull=True,
        status__in=[CompetitionStatus.PENDING, CompetitionStatus.ACTIVE]
    ).count()

    return render(request, 'the_warroom/tournament_overview.html', context)


def tournament_leaderboard_page(request, slug):
    tournament = get_object_or_404(Tournament, slug=slug.lower())

    opts = Game.with_efforts()
    games_qs = Game.objects.filter(
        round__stage__tournament=tournament, final=True
    ).select_related(*opts['select']).prefetch_related(*opts['prefetch'])

    filterset = TournamentGameFilter(request.GET, queryset=games_qs, tournament=tournament)
    filtered_games = filterset.qs

    try:
        leaderboard_threshold = int(request.GET.get("threshold", tournament.game_threshold))
    except (TypeError, ValueError):
        leaderboard_threshold = tournament.game_threshold
    try:
        leaderboard_places = int(request.GET.get("limit", tournament.leaderboard_positions))
    except (TypeError, ValueError):
        leaderboard_places = tournament.leaderboard_positions

    efforts = Effort.objects.filter(game__in=filtered_games)

    faction_link = lambda f: reverse('tournament-component-leaderboard', kwargs={'tournament_slug': tournament.slug, 'post_slug': f.slug})
    player_link = lambda p: reverse('tournament-player-leaderboard', kwargs={'tournament_slug': tournament.slug, 'profile_slug': p.slug})
    top_players = Profile.leaderboard(limit=leaderboard_places, effort_qs=efforts, game_threshold=leaderboard_threshold, as_json=False, link_builder=player_link)
    most_players = Profile.leaderboard(limit=leaderboard_places, effort_qs=efforts, top_quantity=True, game_threshold=leaderboard_threshold, as_json=False, link_builder=player_link)
    top_factions = Faction.leaderboard(limit=leaderboard_places, effort_qs=efforts, game_threshold=leaderboard_threshold, as_json=False, link_builder=faction_link)
    most_factions = Faction.leaderboard(limit=leaderboard_places, effort_qs=efforts, top_quantity=True, game_threshold=leaderboard_threshold, as_json=False, link_builder=faction_link)

    if hasattr(request, 'htmx') and request.htmx:
        template_name = 'the_warroom/partials/leaderboard_list_home.html'
    else:
        template_name = 'the_warroom/tournament_leaderboard.html'

    context = _tournament_base_context(request, tournament)
    context.update({
        'active_page': 'leaderboard',
        'top_players': top_players,
        'most_players': most_players,
        'top_factions': top_factions,
        'most_factions': most_factions,
        'has_top_players': bool(top_players),
        'has_most_players': bool(most_players),
        'has_top_factions': bool(top_factions),
        'has_most_factions': bool(most_factions),
        'leaderboard_threshold': leaderboard_threshold,
        'leaderboard_places': leaderboard_places,
        'games_count': filtered_games.count(),
        'form': filterset.form,
        'filterset': filterset,
    })

    return render(request, template_name, context)


def tournament_games_page(request, slug):
    tournament = get_object_or_404(Tournament, slug=slug.lower())

    opts = Game.with_efforts()
    games_qs = Game.objects.filter(
        round__stage__tournament=tournament, final=True
    ).select_related(*opts['select']).prefetch_related(*opts['prefetch']).order_by('-date_posted')

    filterset = TournamentGameFilter(request.GET, queryset=games_qs, tournament=tournament)
    games = filterset.qs

    paginator = Paginator(games, settings.PAGE_SIZE)
    page_number = request.GET.get('page')
    try:
        page_obj = paginator.get_page(page_number)
    except EmptyPage:
        page_obj = paginator.page(paginator.num_pages)

    if hasattr(request, 'htmx') and request.htmx:
        template_name = 'the_warroom/partials/tournament_game_list.html'
    else:
        template_name = 'the_warroom/tournament_games.html'

    context = _tournament_base_context(request, tournament)
    context.update({
        'active_page': 'games',
        'games': page_obj,
        'page_obj': page_obj,
        'games_count': paginator.count,
        'form': filterset.form,
        'filterset': filterset,
        'pagination_url': reverse('tournament-games-page', args=[tournament.slug]),
    })

    return render(request, template_name, context)


def _tournament_roster_queryset(tournament):
    """Build annotated roster queryset for a tournament."""
    return Profile.objects.filter(
        Q(efforts__game__round__stage__tournament=tournament) |
        Q(tournament_participations__tournament=tournament)
    ).distinct().annotate(
        total_efforts=Count('efforts', distinct=True, filter=Q(
            efforts__game__round__stage__tournament=tournament,
            efforts__game__final=True
        )),
        win_count=Count('efforts', distinct=True, filter=Q(
            efforts__win=True,
            efforts__game__round__stage__tournament=tournament,
            efforts__game__final=True
        )),
        coalition_count=Count('efforts', distinct=True, filter=Q(
            efforts__win=True,
            efforts__game__coalition_win=True,
            efforts__game__round__stage__tournament=tournament,
            efforts__game__final=True
        ))
    ).annotate(
        win_rate=Case(
            When(total_efforts=0, then=Value(0)),
            default=ExpressionWrapper(
                (Cast(F('win_count'), FloatField()) - (Cast(F('coalition_count'), FloatField()) / 2)) / Cast(F('total_efforts'), FloatField()) * 100,
                output_field=FloatField()
            ),
            output_field=FloatField()
        )
    ).order_by('-total_efforts', '-win_count', '-win_rate', 'display_name')


def _stage_roster_queryset(stage):
    """Build annotated roster queryset for a stage."""
    stage_participant_ids = StageParticipant.objects.filter(
        stage=stage
    ).values_list('tournament_player__profile_id', flat=True)

    return Profile.objects.filter(
        Q(efforts__game__round__stage=stage) | Q(id__in=stage_participant_ids)
    ).distinct().annotate(
        total_efforts=Count('efforts', distinct=True, filter=Q(
            efforts__game__round__stage=stage, efforts__game__final=True
        )),
        win_count=Count('efforts', distinct=True, filter=Q(
            efforts__win=True, efforts__game__round__stage=stage, efforts__game__final=True
        )),
        coalition_count=Count('efforts', distinct=True, filter=Q(
            efforts__win=True, efforts__game__coalition_win=True,
            efforts__game__round__stage=stage, efforts__game__final=True
        ))
    ).annotate(
        win_rate=Case(
            When(total_efforts=0, then=Value(0)),
            default=ExpressionWrapper(
                (Cast(F('win_count'), FloatField()) - (Cast(F('coalition_count'), FloatField()) / 2)) / Cast(F('total_efforts'), FloatField()) * 100,
                output_field=FloatField()
            ),
            output_field=FloatField()
        )
    ).order_by('-total_efforts', '-win_count', '-win_rate', 'display_name')


def _round_roster_queryset(round):
    """Build annotated roster queryset for a round."""
    stage_participant_ids = StageParticipant.objects.filter(
        stage=round.stage
    ).values_list('tournament_player__profile_id', flat=True)

    return Profile.objects.filter(
        Q(efforts__game__round=round) | Q(id__in=stage_participant_ids)
    ).distinct().annotate(
        total_efforts=Count('efforts', distinct=True, filter=Q(
            efforts__game__round=round, efforts__game__final=True
        )),
        win_count=Count('efforts', distinct=True, filter=Q(
            efforts__win=True, efforts__game__round=round, efforts__game__final=True
        )),
        coalition_count=Count('efforts', distinct=True, filter=Q(
            efforts__win=True, efforts__game__coalition_win=True,
            efforts__game__round=round, efforts__game__final=True
        ))
    ).annotate(
        win_rate=Case(
            When(total_efforts=0, then=Value(0)),
            default=ExpressionWrapper(
                (Cast(F('win_count'), FloatField()) - (Cast(F('coalition_count'), FloatField()) / 2)) / Cast(F('total_efforts'), FloatField()) * 100,
                output_field=FloatField()
            ),
            output_field=FloatField()
        )
    ).order_by('-total_efforts', '-win_count', '-win_rate', 'display_name')


def tournament_roster_page(request, slug):
    tournament = get_object_or_404(Tournament, slug=slug.lower())

    players = _tournament_roster_queryset(tournament)

    paginator = Paginator(players, settings.PAGE_SIZE)
    page_number = request.GET.get('page')
    try:
        page_obj = paginator.get_page(page_number)
    except EmptyPage:
        page_obj = paginator.page(paginator.num_pages)

    if hasattr(request, 'htmx') and request.htmx:
        template_name = 'the_warroom/partials/tournament_roster_rows.html'
    else:
        template_name = 'the_warroom/tournament_roster.html'

    context = _tournament_base_context(request, tournament)
    context.update({
        'active_page': 'roster',
        'players': page_obj,
        'page_obj': page_obj,
        'players_count': paginator.count,
        'pagination_url': reverse('tournament-roster-page', args=[tournament.slug]),
    })

    return render(request, template_name, context)


def tournament_details_page(request, slug):
    tournament = get_object_or_404(
        Tournament.objects.prefetch_related('moderators'),
        slug=slug.lower()
    )

    context = _tournament_base_context(request, tournament)
    context['active_page'] = 'details'

    if tournament.show_assets:
        if tournament.asset_mode != AssetModeChoices.OPEN:
            assets = tournament.get_asset_querysets()
            context['asset_types'] = [
                ('faction', 'Factions', assets['factions'], 'bi-shield'),
                ('map', 'Maps', assets['maps'], 'bi-map'),
                ('deck', 'Decks', assets['decks'], 'bi-stack'),
                ('hireling', 'Hirelings', assets['hirelings'], 'bi-person-badge'),
                ('landmark', 'Landmarks', assets['landmarks'], 'bi-geo-alt'),
                ('tweak', 'House Rules', assets['tweaks'], 'bi-wrench'),
                ('vagabond', 'Vagabonds', assets['vagabonds'], 'bi-person-walking'),
            ]
        else:
            games_qs = Game.objects.filter(round__stage__tournament=tournament, final=True)
            if games_qs.exists():
                context['asset_types'] = _build_used_asset_types(games_qs)

    return render(request, 'the_warroom/tournament_details.html', context)



@player_required_class_based_view
class TournamentDeleteView(DeleteView):
    model = Tournament
    success_url = reverse_lazy('tournaments-home')  # Redirect to the tournament list or a suitable page

    def dispatch(self, request, *args, **kwargs):
        """Check if user has permission to delete this tournament."""
        tournament = self.get_object()
        user_profile = request.user.profile

        # Admin can always delete
        if user_profile.admin:
            return super().dispatch(request, *args, **kwargs)

        # Owner can delete only if no games have been recorded
        if tournament.designer == user_profile:
            # Check if any games exist in any round of any stage of this tournament
            has_games = Game.objects.filter(round__stage__tournament=tournament).exists()
            if has_games:
                messages.error(request, f"You cannot delete '{tournament.name}' because games have already been recorded. Contact an admin if you need to delete this tournament.")
                return redirect(tournament.get_absolute_url())
            return super().dispatch(request, *args, **kwargs)

        # Not admin or owner - no permission
        messages.error(request, "You do not have permission to delete this tournament.")
        raise PermissionDenied()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['tournament'] = self.object
        return context

    def post(self, request, *args, **kwargs):
        # print('Trying to delete')
        tournament = self.get_object()
        # print(tournament)
        name = tournament.name
        try:
            # Attempt to delete the tournament
            response = self.delete(request, *args, **kwargs)
            # Add success message upon successful deletion
            messages.success(request, f"The Tournament '{name}' was successfully deleted.")
            return response
        except ProtectedError:
            # Handle the case where the deletion fails due to foreign key protection
            messages.error(request, f"The Tournament '{name}' cannot be deleted because games have already been recorded.")
            # Redirect back to the tournament detail page
            return redirect(tournament.get_absolute_url())
        except IntegrityError:
            # Handle other integrity errors (if any)
            messages.error(request, "An error occurred while trying to delete this tournament.")
            return redirect(tournament.get_absolute_url())


class StageDeleteView(LoginRequiredMixin, UserPassesTestMixin, DeleteView):
    model = Stage

    def get_object(self, queryset=None):
        tournament_slug = self.kwargs.get('tournament_slug')
        stage_slug = self.kwargs.get('stage_slug')
        tournament = get_object_or_404(Tournament, slug=tournament_slug)
        return get_object_or_404(Stage, slug=stage_slug, tournament=tournament)

    def test_func(self):
        obj = self.get_object()
        profile = self.request.user.profile
        return profile.admin or profile == obj.tournament.designer

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['stage'] = self.object
        context['tournament'] = self.object.tournament
        return context

    def get_success_url(self):
        tournament = self.object.tournament
        return reverse_lazy('tournament-detail', kwargs={'slug': tournament.slug})

    def post(self, request, *args, **kwargs):
        stage = self.get_object()
        name = stage.name
        try:
            response = self.delete(request, *args, **kwargs)
            messages.success(request, f"'{name}' was successfully deleted.")
            return response
        except ProtectedError:
            messages.error(request, f"The Stage '{name}' cannot be deleted because games have already been recorded.")
            return redirect(stage.get_absolute_url())
        except IntegrityError:
            messages.error(request, "An error occurred while trying to delete this Stage.")
            return redirect(stage.get_absolute_url())


# ===== New Dynamic Tournament Views with HTMX Support =====

def set_default_tournament_assets(tournament, previous_asset_mode=None, previous_platform=None):
    """
    Helper function to set default assets for a tournament based on asset_mode and platform.
    Only sets defaults when:
    - asset_mode is not SELECT
    - Mode is switching from SELECT to another mode
    - Platform is changing to/from Root Digital

    Args:
        tournament: Tournament instance to update
        previous_asset_mode: Previous asset_mode value (for update operations)
        previous_platform: Previous platform value (for update operations)
    """
    from the_warroom.models import PlatformChoices

    # Don't update if asset_mode is SELECT
    if tournament.asset_mode == AssetModeChoices.SELECTED and tournament.platform != PlatformChoices.DWD:
        return

    # Check if we should update assets
    should_update = False

    # Initial creation (no previous values)
    if previous_asset_mode is None and previous_platform is None:
        should_update = True

    # Mode changed from SELECT to another mode
    elif previous_asset_mode == AssetModeChoices.SELECTED and tournament.asset_mode != AssetModeChoices.SELECTED:
        should_update = True

    # Platform changed to or from Root Digital
    elif previous_platform != tournament.platform and (
        previous_platform == PlatformChoices.DWD or tournament.platform == PlatformChoices.DWD
    ):
        should_update = True

    if not should_update:
        return

    # Determine filtering criteria based on platform
    is_root_digital = tournament.platform == PlatformChoices.DWD

    # Build querysets
    if is_root_digital:
        factions_qs = Faction.objects.filter(in_root_digital=True)
        maps_qs = Map.objects.filter(in_root_digital=True)
        decks_qs = Deck.objects.filter(in_root_digital=True)
        hirelings_qs = Hireling.objects.filter(in_root_digital=True)
        landmarks_qs = Landmark.objects.filter(in_root_digital=True)
        vagabonds_qs = Vagabond.objects.filter(in_root_digital=True)
    else:
        factions_qs = Faction.objects.filter(official=True)
        maps_qs = Map.objects.filter(official=True)
        decks_qs = Deck.objects.filter(official=True)
        hirelings_qs = Hireling.objects.filter(official=True)
        landmarks_qs = Landmark.objects.filter(official=True)
        vagabonds_qs = Vagabond.objects.filter(official=True)

    # Exclude clockwork factions if include_clockwork is False
    if not tournament.include_clockwork:
        factions_qs = factions_qs.exclude(type='C')

    # Set the assets
    tournament.factions.set(factions_qs)
    tournament.maps.set(maps_qs)
    tournament.decks.set(decks_qs)
    tournament.hirelings.set(hirelings_qs)
    tournament.landmarks.set(landmarks_qs)
    tournament.vagabonds.set(vagabonds_qs)


@player_onboard_required
def tournament_dynamic_create(request):
    """
    Create tournament with dynamic permissions.
    Non-admin: classification defaults to GROUP, designer auto-set, no guild.
    Admin: full control over all fields.
    """
    if request.method == 'POST':
        form = TournamentDynamicCreateForm(request.POST, request.FILES, user=request.user)

        if form.is_valid():
            tournament = form.save(commit=False)

            # Force values for non-admins as security check
            if not request.user.profile.admin:
                tournament.designer = request.user.profile

            # Clear default_format if classification is not Tournament
            if tournament.classification != Tournament.ClassificationTypes.TOURNAMENT:
                tournament.default_format = ''

            # Clear guild if classification is not League
            if tournament.classification != Tournament.ClassificationTypes.LEAGUE:
                tournament.guild = None

            # Stages/rounds are enabled later via the settings hub; a new
            # tournament starts flat with a single implicit Stage and Round.
            tournament.use_stages = False
            tournament.save()
            form.save_m2m()  # Save ManyToMany relationships

            # Save moderators
            moderator_ids = request.POST.getlist('moderators')
            if moderator_ids:
                valid_moderators = Profile.objects.filter(pk__in=moderator_ids)
                tournament.moderators.set(valid_moderators)

            # Set default assets based on asset_mode and platform
            set_default_tournament_assets(tournament)

            stage_name = 'Stage 1'
            round_name = 'Round 1'

            # Create the initial Stage and Round 1
            stage_kwargs = dict(
                tournament=tournament,
                name=stage_name,
                order=1,
            )
            if tournament.classification == Tournament.ClassificationTypes.TOURNAMENT:
                stage_kwargs['stage_format'] = tournament.default_format or FormatChoices.CUSTOM
            stage = Stage.objects.create(**stage_kwargs)
            Round.objects.create(
                stage=stage,
                name=round_name,
                round_number=1,
                start_date=tournament.start_date,
            )

            messages.success(request, f"Tournament '{tournament.name}' created successfully!")
            return redirect(tournament.get_absolute_url())
    else:
        form = TournamentDynamicCreateForm(user=request.user)

    context = {
        'form': form,
        'is_admin': request.user.profile.admin,
        'action': 'Create'
    }
    return render(request, 'the_warroom/tournament_dynamic_form.html', context)


@player_onboard_required
def tournament_dynamic_update(request, slug):
    """
    Update tournament with inline player/asset management.
    Non-admin: can update basic fields + manage players/assets.
    Admin: full control including classification, designer, guild.
    """
    tournament = get_object_or_404(Tournament, slug=slug)

    # Permission check — only designer and admin can edit the tournament itself
    if not (request.user.profile == tournament.designer or request.user.profile.admin):
        messages.error(request, f"You do not have permission to edit {tournament.name}.")
        raise PermissionDenied()

    if request.method == 'POST':
        form = TournamentDynamicUpdateForm(
            user=request.user,
            data=request.POST,
            files=request.FILES,
            instance=tournament
        )

        if form.is_valid():
            # Capture previous values before saving
            original = Tournament.objects.get(pk=tournament.pk)
            previous_asset_mode = original.asset_mode
            previous_platform = original.platform

            tournament = form.save(commit=False)

            # Security: prevent non-admins from changing restricted fields
            if not request.user.profile.admin:
                tournament.classification = original.classification
                tournament.designer = original.designer

            # Clear default_format if classification is not Tournament
            if tournament.classification != Tournament.ClassificationTypes.TOURNAMENT:
                tournament.default_format = ''

            # Guard: for non-League, only allow clearing the guild, not changing to a different one
            if tournament.classification != Tournament.ClassificationTypes.LEAGUE:
                if tournament.guild is not None and tournament.guild != original.guild:
                    tournament.guild = original.guild

            # use_stages / use_rounds are managed via the settings hub, not this
            # form, so there is nothing to lock here.
            tournament.save()
            form.save_m2m()

            # Set default assets if mode/platform changed appropriately
            set_default_tournament_assets(tournament, previous_asset_mode, previous_platform)

            # Save moderators
            moderator_ids = request.POST.getlist('moderators')
            valid_moderators = Profile.objects.filter(pk__in=moderator_ids)
            tournament.moderators.set(valid_moderators)

            messages.success(request, f"Tournament '{tournament.name}' updated successfully!")
            return redirect(tournament.get_absolute_url())
    else:
        form = TournamentDynamicUpdateForm(instance=tournament, user=request.user)

    context = {
        'form': form,
        'tournament': tournament,
        'is_admin': request.user.profile.admin,
        'action': 'Update',
        'moderators': tournament.moderators.all(),
    }
    return render(request, 'the_warroom/tournament_dynamic_form.html', context)


# ===== HTMX Endpoints for Player Management =====

@player_required
def tournament_search_players(request, slug):
    """HTMX endpoint: Search for players to add"""
    tournament = get_object_or_404(Tournament, slug=slug)

    # Permission check
    if not tournament.has_permission(request.user.profile):
        return HttpResponse("Permission denied", status=403)

    query = request.GET.get('q', '')

    # Get players not in tournament
    available_players = Profile.objects.exclude(
        tournament_participations__tournament=tournament
    )

    if query:
        available_players = available_players.filter(
            Q(discord__icontains=query) |
            Q(display_name__icontains=query)
        )

    available_players = available_players[:10]

    # Check if any profile exists with an exact name match
    has_exact_match = False
    if query:
        has_exact_match = Profile.objects.filter(
            Q(discord__iexact=query) | Q(display_name__iexact=query)
        ).exists()

    return render(request, 'the_warroom/partials/player_search_results.html', {
        'players': available_players,
        'tournament': tournament,
        'has_exact_match': has_exact_match,
    })


@player_onboard_required
def search_moderators(request):
    """HTMX endpoint: Search for players to add as moderators (works for both create and update)"""
    query = request.GET.get('q', '')
    exclude_ids = request.GET.get('exclude', '')
    designer_id = request.GET.get('designer', '')

    if not query:
        return render(request, 'the_warroom/partials/moderator_search_results.html', {
            'players': [],
        })

    candidates = Profile.objects.all()

    # Exclude already-selected moderators
    if exclude_ids:
        exclude_list = [int(pk) for pk in exclude_ids.split(',') if pk.strip().isdigit()]
        candidates = candidates.exclude(pk__in=exclude_list)

    # Exclude the designer
    if designer_id and designer_id.isdigit():
        candidates = candidates.exclude(pk=int(designer_id))

    candidates = candidates.filter(
        Q(discord__icontains=query) |
        Q(display_name__icontains=query)
    )[:10]

    return render(request, 'the_warroom/partials/moderator_search_results.html', {
        'players': candidates,
    })


@player_required
def tournament_move_player(request, slug):
    """JavaScript/AJAX endpoint: Move player between groups (players/waitlist/eliminated)"""
    if request.method != 'POST':
        return HttpResponse("Method not allowed", status=405)

    tournament = get_object_or_404(Tournament, slug=slug)

    # Permission check
    if not tournament.has_permission(request.user.profile):
        return HttpResponse("Permission denied", status=403)

    import json
    data = json.loads(request.body)
    player_id = data.get('player_id')
    from_group = data.get('from_group')
    to_group = data.get('to_group')

    player = get_object_or_404(Profile, id=player_id)

    status_map = {
        'players': TournamentPlayer.StatusChoices.REGISTERED,
        'waitlist': TournamentPlayer.StatusChoices.WAITLIST,
        'eliminated': TournamentPlayer.StatusChoices.ELIMINATED,
    }
    to_status = status_map.get(to_group)

    if not from_group:
        # Adding from search — create TournamentPlayer with desired status
        effective_status = to_status or TournamentPlayer.StatusChoices.REGISTERED
        tournament.add_player(player, status=effective_status)
    elif not to_group:
        # Trash button — remove from tournament entirely
        tournament.remove_player_from_tournament(player)
        return HttpResponse('')
    else:
        # Status transition between existing groups
        tournament.move_player(player, status_map.get(from_group), to_status)

    # Return updated player card
    return render(request, 'the_warroom/partials/player_card.html', {
        'player': player,
        'tournament': tournament,
        'group': to_group
    })


@player_required
def round_search_players(request, tournament_slug, stage_slug, round_slug):
    """HTMX endpoint: Search for players to add to round (only from tournament players, excluding eliminated)"""
    tournament = get_object_or_404(Tournament, slug=tournament_slug)
    stage = get_object_or_404(Stage, slug=stage_slug, tournament=tournament)
    round = get_object_or_404(Round, slug=round_slug, stage=stage)

    # Permission check
    if not tournament.has_permission(request.user.profile):
        return HttpResponse("Permission denied", status=403)

    query = request.GET.get('q', '')

    # Get all tournament players sorted by status then name
    status_order = Case(
        When(tournament_participations__status=TournamentPlayer.StatusChoices.REGISTERED, then=Value(1)),
        When(tournament_participations__status=TournamentPlayer.StatusChoices.WAITLIST, then=Value(2)),
        When(tournament_participations__status=TournamentPlayer.StatusChoices.ELIMINATED, then=Value(3)),
        default=Value(4),
        output_field=IntegerField(),
    )
    available_players = Profile.objects.filter(
        tournament_participations__tournament=tournament,
    ).annotate(
        tournament_status=F('tournament_participations__status'),
        status_order=status_order,
    ).order_by('status_order', 'display_name')

    if query:
        available_players = available_players.filter(
            Q(discord__icontains=query) |
            Q(display_name__icontains=query)
        )[:10]

    return render(request, 'the_warroom/partials/player_search_results.html', {
        'players': available_players,
        'tournament': tournament,
        'round': round
    })


@player_required
def round_move_player(request, tournament_slug, stage_slug, round_slug):
    """JavaScript/AJAX endpoint: Move player between groups for round"""
    if request.method != 'POST':
        return HttpResponse("Method not allowed", status=405)

    tournament = get_object_or_404(Tournament, slug=tournament_slug)
    stage = get_object_or_404(Stage, slug=stage_slug, tournament=tournament)
    round = get_object_or_404(Round, slug=round_slug, stage=stage)

    # Permission check
    if not tournament.has_permission(request.user.profile):
        return HttpResponse("Permission denied", status=403)

    data = json.loads(request.body)
    player_id = data.get('player_id')
    from_group = data.get('from_group')
    to_group = data.get('to_group')

    player = get_object_or_404(Profile, id=player_id)

    status_map = {
        'players': TournamentPlayer.StatusChoices.REGISTERED,
        'waitlist': TournamentPlayer.StatusChoices.WAITLIST,
        'eliminated': TournamentPlayer.StatusChoices.ELIMINATED,
    }
    to_status = status_map.get(to_group)

    if not from_group:
        # Adding from search results — add/update player in tournament with desired status
        effective_status = to_status or TournamentPlayer.StatusChoices.REGISTERED
        tournament.add_player(player, status=effective_status)
    elif not to_group:
        # Trash button — remove from tournament entirely
        tournament.remove_player_from_tournament(player)
        return HttpResponse('')
    else:
        # Status transition
        tournament.move_player(player, status_map.get(from_group), to_status)

    # Return updated player card
    return render(request, 'the_warroom/partials/player_card.html', {
        'player': player,
        'tournament': tournament,
        'round': round,
        'group': to_group
    })


# ===== HTMX Endpoints for Asset Management =====

@player_onboard_required
def tournament_search_assets(request, slug, asset_type):
    """HTMX endpoint: Search for assets to add"""
    tournament = get_object_or_404(Tournament, slug=slug)

    # Permission check
    if not tournament.has_permission(request.user.profile):
        return HttpResponse("Permission denied", status=403)

    query = request.GET.get('q', '')

    # Get the appropriate model
    asset_models = {
        'faction': Faction,
        'map': Map,
        'deck': Deck,
        'hireling': Hireling,
        'landmark': Landmark,
        'tweak': Tweak,
        'vagabond': Vagabond,
    }

    if asset_type not in asset_models:
        return HttpResponse("Invalid asset type", status=400)

    model = asset_models[asset_type]

    # Asset search only available in SELECTED mode
    if tournament.asset_mode != AssetModeChoices.SELECTED:
        return HttpResponse("Asset search only available in Selected mode", status=400)

    # In SELECTED mode, all assets are available for selection
    if tournament.platform == "Root Digital":
        queryset = model.objects.filter(in_root_digital=True)
    else:
        queryset = model.objects.all()

    # Exclude already added assets
    queryset = queryset.exclude(**{f'tournaments': tournament})

    # Apply search filter
    if query:
        queryset = queryset.filter(title__icontains=query)

    queryset = queryset[:10]

    return render(request, 'the_warroom/partials/asset_search_results.html', {
        'assets': queryset,
        'asset_type': asset_type,
        'tournament': tournament
    })


@player_onboard_required
def tournament_add_asset(request, slug, asset_type, asset_id):
    """HTMX endpoint: Add asset (faction/map/deck/etc) to tournament"""
    tournament = get_object_or_404(Tournament, slug=slug)

    # Permission check
    if not tournament.has_permission(request.user.profile):
        return HttpResponse("Permission denied", status=403)

    # Get the appropriate model and relationship
    asset_models = {
        'faction': (Faction, tournament.factions),
        'map': (Map, tournament.maps),
        'deck': (Deck, tournament.decks),
        'hireling': (Hireling, tournament.hirelings),
        'landmark': (Landmark, tournament.landmarks),
        'tweak': (Tweak, tournament.tweaks),
        'vagabond': (Vagabond, tournament.vagabonds),
    }

    if asset_type not in asset_models:
        return HttpResponse("Invalid asset type", status=400)

    model, relation = asset_models[asset_type]
    asset = get_object_or_404(model, id=asset_id)
    relation.add(asset)

    return render(request, 'the_warroom/partials/asset_item.html', {
        'asset': asset,
        'asset_type': asset_type,
        'tournament': tournament
    })


@player_onboard_required
def tournament_remove_asset(request, slug, asset_type, asset_id):
    """HTMX endpoint: Remove asset from tournament"""
    tournament = get_object_or_404(Tournament, slug=slug)

    # Permission check
    if not tournament.has_permission(request.user.profile):
        return HttpResponse("Permission denied", status=403)

    # Get the appropriate model and relationship
    asset_models = {
        'faction': (Faction, tournament.factions),
        'map': (Map, tournament.maps),
        'deck': (Deck, tournament.decks),
        'hireling': (Hireling, tournament.hirelings),
        'landmark': (Landmark, tournament.landmarks),
        'tweak': (Tweak, tournament.tweaks),
        'vagabond': (Vagabond, tournament.vagabonds),
    }

    if asset_type not in asset_models:
        return HttpResponse("Invalid asset type", status=400)

    model, relation = asset_models[asset_type]
    asset = get_object_or_404(model, id=asset_id)
    relation.remove(asset)

    # Return empty response (HTMX will remove the element)
    return HttpResponse('')


SERIES_SMALL_PAGE_SIZE = 5
SERIES_LARGE_PAGE_SIZE = 15

SERIES_PAGE_SIZES = {
    'my': SERIES_SMALL_PAGE_SIZE,
    'participating': SERIES_SMALL_PAGE_SIZE,
    'community': SERIES_SMALL_PAGE_SIZE,
    'public': SERIES_LARGE_PAGE_SIZE,
}

SERIES_CLASSIFICATIONS = [
    ('groups', Tournament.ClassificationTypes.GROUP),
    ('tournaments', Tournament.ClassificationTypes.TOURNAMENT),
    ('leagues', Tournament.ClassificationTypes.LEAGUE),
]

SERIES_SECTIONS = ['my', 'participating', 'community', 'public']


def _get_series_base_queryset(classification, profile=None):
    """Build an annotated Tournament queryset for a classification type."""
    qs = Tournament.objects.filter(classification=classification)

    now = timezone.now().date()
    qs = qs.annotate(
        annotated_game_count=Count(
            'stages__rounds__games',
            filter=Q(stages__rounds__games__final=True),
            distinct=True
        ),
        unique_players_count=Count(
            'stages__rounds__games__efforts__player',
            distinct=True
        ),
        annotated_scheduled_count=Count(
            'stages__rounds__series__matches',
            filter=Q(
                stages__rounds__series__matches__scheduled_time__isnull=False,
                stages__rounds__series__matches__status__in=[CompetitionStatus.PENDING, CompetitionStatus.ACTIVE]
            ),
            distinct=True
        ),
        annotated_pending_count=Count(
            'stages__rounds__series__matches',
            filter=Q(
                stages__rounds__bracket_status=Round.BracketStatusChoices.FINALIZED,
                stages__rounds__series__matches__scheduled_time__isnull=True,
                stages__rounds__series__matches__status__in=[CompetitionStatus.PENDING, CompetitionStatus.ACTIVE]
            ),
            distinct=True
        ),
        # Sort order: Active=0, Pending=1, Completed=2
        status_sort=Case(
            When(status=CompetitionStatus.ACTIVE, then=Value(0)),
            When(status=CompetitionStatus.PENDING, then=Value(1)),
            default=Value(2),
            output_field=IntegerField()
        ),
        # Availability sort: available tournaments first
        is_available_sort=Case(
            When(
                Q(is_active=True) &
                (Q(start_date__isnull=True) | Q(start_date__lte=now)) &
                (Q(end_date__isnull=True) | Q(end_date__gte=now)),
                then=Value(True)
            ),
            default=Value(False),
            output_field=BooleanField()
        ),
    )

    if profile:
        user_in_guild = Exists(
            Profile.guilds.through.objects.filter(
                profile_id=profile.pk,
                discordguild_id=OuterRef('guild_id')
            )
        )
        qs = qs.annotate(user_in_guild=user_in_guild)

    qs = qs.order_by('-is_available_sort', 'status_sort', '-start_date')
    qs = qs.select_related('guild', 'designer')

    return qs


def _build_series_section_querysets(cls_value, profile, is_admin):
    """Build the 4 section querysets (my/participating/community/public) for a classification."""
    base_qs = _get_series_base_queryset(cls_value, profile)

    if profile:
        my_filter = Q(designer=profile) | Q(moderators=profile)
        participating_filter = Q(tournament_players__profile=profile)
        community_filter = Q(guild__in=profile.guilds.all())

        my_qs = base_qs.filter(my_filter).distinct()
        participating_qs = base_qs.filter(participating_filter).exclude(my_filter).distinct()
        community_qs = base_qs.filter(community_filter).exclude(my_filter).exclude(participating_filter).distinct()

        if is_admin:
            public_qs = base_qs.exclude(my_filter).exclude(participating_filter).exclude(community_filter).distinct()
        else:
            public_qs = base_qs.filter(publicly_visible=True).exclude(my_filter).exclude(participating_filter).exclude(community_filter).distinct()
    else:
        my_qs = Tournament.objects.none()
        participating_qs = Tournament.objects.none()
        community_qs = Tournament.objects.none()
        public_qs = base_qs.filter(publicly_visible=True)

    return {
        'my': my_qs,
        'participating': participating_qs,
        'community': community_qs,
        'public': public_qs,
    }


def tournaments_home(request):
    profile = request.user.profile if request.user.is_authenticated else None
    is_admin = profile and profile.admin

    load_type = request.GET.get('load_type', None)
    tab = request.GET.get('tab', None)

    # Handle HTMX tab lazy-load request — only build querysets for the requested classification
    if tab:
        cls_lookup = dict(SERIES_CLASSIFICATIONS)
        cls_value = cls_lookup.get(tab)
        if cls_value is None:
            return HttpResponseBadRequest()
        section_qs = _build_series_section_querysets(cls_value, profile, is_admin)
        context_data = {}
        for section in SERIES_SECTIONS:
            qs = section_qs[section]
            page_size = SERIES_PAGE_SIZES[section]
            results = list(qs[:page_size + 1])
            has_more = len(results) > page_size
            key_prefix = f'{tab}_{section}'
            context_data[key_prefix] = results[:page_size]
            context_data[f'has_more_{key_prefix}'] = has_more
            context_data[f'{key_prefix}_offset'] = page_size
        return render(request, 'the_warroom/partials/series_tab_content.html', {
            **context_data,
            'cls_key': tab,
            'cls_label': tab.rstrip('s').capitalize(),
            'my_series': context_data.get(f'{tab}_my', []),
            'participating_series': context_data.get(f'{tab}_participating', []),
            'community_series': context_data.get(f'{tab}_community', []),
            'public_series': context_data.get(f'{tab}_public', []),
            'has_more_my': context_data.get(f'has_more_{tab}_my', False),
            'has_more_participating': context_data.get(f'has_more_{tab}_participating', False),
            'has_more_community': context_data.get(f'has_more_{tab}_community', False),
            'has_more_public': context_data.get(f'has_more_{tab}_public', False),
            'my_offset': context_data.get(f'{tab}_my_offset', 0),
            'participating_offset': context_data.get(f'{tab}_participating_offset', 0),
            'community_offset': context_data.get(f'{tab}_community_offset', 0),
            'public_offset': context_data.get(f'{tab}_public_offset', 0),
            'profile': profile,
        })

    # Handle HTMX partial request — only build the one needed queryset
    if load_type:
        parts = load_type.rsplit('_', 1)
        cls_key, section = parts[0], parts[1]

        cls_lookup = dict(SERIES_CLASSIFICATIONS)
        cls_value = cls_lookup[cls_key]
        section_qs = _build_series_section_querysets(cls_value, profile, is_admin)

        offset_param = f'{load_type}_offset'
        offset = int(request.GET.get(offset_param, 0))
        page_size = SERIES_PAGE_SIZES[section]

        qs = section_qs[section]
        # Fetch one extra to check if there are more without a separate COUNT query
        results = list(qs[offset:offset + page_size + 1])
        has_more = len(results) > page_size
        selected = results[:page_size]
        next_offset = offset + page_size

        return render(request, 'the_warroom/partials/series_list_container.html', {
            'series_list': selected,
            'has_more': has_more,
            'offset': next_offset,
            'load_type': load_type,
            'profile': profile,
        })

    # Full page load — only build querysets for the default (Tournaments) tab.
    # Groups and Leagues are lazy-loaded via HTMX on first click.
    # Fetch page_size + 1 to determine has_more without separate COUNT queries.
    context_data = {}
    default_cls_key = 'tournaments'
    default_cls_value = dict(SERIES_CLASSIFICATIONS)[default_cls_key]
    section_qs = _build_series_section_querysets(default_cls_value, profile, is_admin)
    for section in SERIES_SECTIONS:
        qs = section_qs[section]
        page_size = SERIES_PAGE_SIZES[section]
        results = list(qs[:page_size + 1])
        has_more = len(results) > page_size

        key_prefix = f'{default_cls_key}_{section}'
        context_data[key_prefix] = results[:page_size]
        context_data[f'has_more_{key_prefix}'] = has_more
        context_data[f'{key_prefix}_offset'] = page_size

    # Theme
    theme = get_theme(request)
    background_image, foreground_images, theme_artists, background_pattern = get_thematic_images(
        theme=theme, page='games'
    )

    context = {
        **context_data,
        'profile': profile,
        'background_image': background_image,
        'foreground_images': foreground_images,
        'background_pattern': background_pattern,
        'page_artists': theme_artists,
    }
    return render(request, 'the_warroom/tournaments_home.html', context)


def about_series_view(request):
    return render(request, 'the_warroom/about_series.html', {
        'meta_title': 'About Series',
        'meta_description': 'Learn how Tournaments, Leagues, and Game Groups work on the Root Database - settings, registration, and permissions.',
    })


def about_games_view(request):
    return render(request, 'the_warroom/about_games.html', {
        'meta_title': 'About Games',
        'meta_description': 'Learn how to record games, track turn by turn scores, and understand scoring on the Root Database.',
    })











# ============================
# Round Views (Individual Round/Season of a Tournament)
# ============================
def user_can_access_round(tournament_round, user):
    from the_warroom.models import StageParticipant
    is_active = tournament_round.is_available()
    is_designer = tournament_round.stage.tournament.has_permission(user.profile)

    stage = tournament_round.stage
    if stage:
        # User must be an active (non-eliminated, non-waitlisted) stage participant
        is_player = StageParticipant.objects.filter(
            stage=stage,
            tournament_player__profile=user.profile,
            status=StageParticipant.ParticipantStatus.ACTIVE,
        ).exists()
        # If the stage has no participants yet, assume the round is open
        is_open = not StageParticipant.objects.filter(stage=stage).exists()
    else:
        is_player = False
        is_open = True

    return is_active and (is_designer or is_player or is_open)


def user_can_record_in_round(tournament_round, user, as_role=None):
    """Whether the user may record a game in this round.

    The round must be available. Moderators/designers/admins can always record.
    Otherwise, an active stage participant can record when the tournament's
    recording_access allows player recording (Scheduled Match Players or
    Registered Players) -- the recording_access tier governs this, replacing the
    old check for whether the round has a series.

    ``as_role`` lets a manager preview the result as a lower role (see VIEW_AS_ROLES):
    it suppresses the manager short-circuit so the answer matches what that role would
    actually see. It only ever restricts the answer, never expands it.
    """
    from the_warroom.models import StageParticipant
    if not user.is_authenticated or as_role == 'logged_out':
        return False
    if not tournament_round.is_available():
        return False

    stage = tournament_round.stage
    tournament = stage.tournament

    # Moderators/designers/admins can always record. A manager previewing as
    # 'moderator' keeps this, since a moderator could record here too.
    if as_role in (None, 'moderator') and tournament.has_permission(user.profile):
        return True

    # 'logged_out' is handled above; 'logged_in' has no tournament role.
    if as_role == 'logged_in':
        return False

    # 'player' preview: the New Game button records a standalone game, which only
    # REGISTERED access permits. Under SCHEDULED access players may only fill out a
    # scheduled match, not start a free game, so the button stays hidden.
    if as_role == 'player':
        return tournament.players_can_record_standalone()

    if not tournament.players_can_record_matches():
        return False

    return StageParticipant.objects.filter(
        stage=stage,
        tournament_player__profile=user.profile,
        status=StageParticipant.ParticipantStatus.ACTIVE,
    ).exists()


def round_overview_page(request, tournament_slug, round_slug, stage_slug=None):
    from the_warroom.utils import get_single_stage

    tournament = get_object_or_404(Tournament, slug=tournament_slug)

    if stage_slug:
        # Full 3-slug URL path
        stage = get_object_or_404(Stage, slug=stage_slug, tournament=tournament)
        round = get_object_or_404(Round, slug=round_slug, stage=stage)
    else:
        # Simplified 2-slug URL path
        stage = get_single_stage(tournament)
        if not stage:
            # Tournament uses stages - simplified URL not allowed
            return redirect(tournament.get_absolute_url())
        round = get_object_or_404(Round, slug=round_slug, stage=stage)

    context = _round_base_context(request, tournament, stage, round)
    context['active_page'] = 'overview'

    context['scheduled_count'] = Match.objects.filter(
        round=round,
        round__bracket_status=Round.BracketStatusChoices.FINALIZED,
        scheduled_time__isnull=False,
        status__in=[CompetitionStatus.PENDING, CompetitionStatus.ACTIVE]
    ).count()
    context['pending_count'] = Match.objects.filter(
        round=round,
        round__bracket_status=Round.BracketStatusChoices.FINALIZED,
        scheduled_time__isnull=True,
        status__in=[CompetitionStatus.PENDING, CompetitionStatus.ACTIVE]
    ).count()

    return render(request, 'the_warroom/round_overview.html', context)



def tournament_component_leaderboard(request, tournament_slug, post_slug, stage_slug=None, round_slug=None):
    from the_warroom.utils import get_single_stage

    threshold = get_int_param(request.GET.get('threshold', ''))
    limit = get_int_param(request.GET.get('limit', ''))
    if limit is not None:
        limit = min(limit, 50)

    # Get the tournament from slug
    tournament = get_object_or_404(Tournament, slug=tournament_slug.lower())
    if stage_slug:
        stage = get_object_or_404(Stage, slug=stage_slug.lower(), tournament=tournament)
    else:
        # Try to get single stage if round_slug is provided (simplified URL)
        if round_slug:
            stage = get_single_stage(tournament)
            if not stage:
                return redirect(tournament.get_absolute_url())
        else:
            stage = None

    if round_slug:
        round = get_object_or_404(Round, slug=round_slug.lower(), stage=stage)
    else:
        round = None

    post = get_object_or_404(Post, slug=post_slug)
    component_mapping = {
            "Map": Map,
            "Deck": Deck,
            "Landmark": Landmark,
            "Tweak": Tweak,
            "Hireling": Hireling,
            "Vagabond": Vagabond,
            "Faction": Faction,
            "Clockwork": Faction,
        }
    Klass = component_mapping.get(post.component)
    object = get_object_or_404(Klass, slug=post_slug)

    # Build game queryset scoped to tournament/stage/round
    game_filter = Q(final=True)
    if round:
        game_filter &= Q(round=round)
    elif stage:
        game_filter &= Q(round__stage=stage)
    else:
        game_filter &= Q(round__stage__tournament=tournament)

    # Add component-specific game filter
    component = object.component
    if component == "Map":
        game_filter &= Q(map=object)
    elif component == "Deck":
        game_filter &= Q(deck=object)
    elif component == "Landmark":
        game_filter &= Q(landmarks=object)
    elif component == "Tweak":
        game_filter &= Q(tweaks=object)
    elif component == "Hireling":
        game_filter &= Q(hirelings=object)
    elif component == "Vagabond":
        game_filter &= Q(efforts__vagabond=object)
    elif component == "Faction":
        game_filter &= Q(efforts__faction=object)

    opts = Game.with_efforts()
    games_qs = Game.objects.filter(game_filter).select_related(*opts['select']).prefetch_related(*opts['prefetch']).distinct()

    # Apply user filter
    filterset = TournamentGameFilter(request.GET, queryset=games_qs, tournament=tournament if not stage else None, stage=stage if not round else None, round=round)
    filtered_games = filterset.qs.order_by('-date_posted')

    # Build efforts from filtered games with component-specific effort filter
    effort_filter = Q(game__in=filtered_games, player__isnull=False)
    if component == "Vagabond":
        effort_filter &= Q(vagabond=object)
    elif component == "Faction":
        effort_filter &= Q(faction=object)

    efforts = Effort.objects.filter(effort_filter)

    if threshold is None:
        threshold = 1
    if limit is None:
        limit = round.get_leaderboard_positions() if round else stage.get_leaderboard_positions() if stage else tournament.leaderboard_positions

    if round:
        player_link = lambda p: reverse('round-player-leaderboard', kwargs={'tournament_slug': tournament.slug, 'stage_slug': stage.slug, 'round_slug': round.slug, 'profile_slug': p.slug})
    elif stage:
        player_link = lambda p: reverse('stage-player-leaderboard', kwargs={'tournament_slug': tournament.slug, 'stage_slug': stage.slug, 'profile_slug': p.slug})
    else:
        player_link = lambda p: reverse('tournament-player-leaderboard', kwargs={'tournament_slug': tournament.slug, 'profile_slug': p.slug})

    top_players = Profile.leaderboard(limit=limit, effort_qs=efforts, game_threshold=threshold, as_json=False, link_builder=player_link)
    most_players = Profile.leaderboard(limit=limit, effort_qs=efforts, top_quantity=True, game_threshold=threshold, as_json=False, link_builder=player_link)

    # Faction leaderboards (for non-faction components)
    top_factions = []
    most_factions = []
    if component != "Faction":
        if round:
            faction_link = lambda f: reverse('round-component-leaderboard', kwargs={'tournament_slug': tournament.slug, 'stage_slug': stage.slug, 'round_slug': round.slug, 'post_slug': f.slug})
        elif stage:
            faction_link = lambda f: reverse('stage-component-leaderboard', kwargs={'tournament_slug': tournament.slug, 'stage_slug': stage.slug, 'post_slug': f.slug})
        else:
            faction_link = lambda f: reverse('tournament-component-leaderboard', kwargs={'tournament_slug': tournament.slug, 'post_slug': f.slug})
        top_factions = Faction.leaderboard(limit=limit, effort_qs=efforts, game_threshold=threshold, as_json=False, link_builder=faction_link)
        most_factions = Faction.leaderboard(limit=limit, effort_qs=efforts, top_quantity=True, game_threshold=threshold, as_json=False, link_builder=faction_link)

    if round:
        meta_title = f'{tournament.name} - {stage.name} ({round.name})'
        if component == "Deck":
            title = f'{object.title} Deck - {tournament.name}: {stage.name} ({round.name})'
        elif component == "Map":
            title = f'{object.title} Map - {tournament.name}: {stage.name} ({round.name})'
        else:
            title = f'{object.title} - {tournament.name}: {stage.name} ({round.name})'
    elif stage:
        meta_title = f'{tournament.name} - {stage.name}'
        if component == "Deck":
            title = f'{object.title} Deck - {tournament.name} ({stage.name})'
        elif component == "Map":
            title = f'{object.title} Map - {tournament.name} ({stage.name})'
        else:
            title = f'{object.title} - {tournament.name} ({stage.name})'
    else:
        meta_title = f'{tournament.name}'
        if component == "Deck":
            title = f'{object.title} Deck - {tournament.name}'
        elif component == "Map":
            title = f'{object.title} Map - {tournament.name}'
        else:
            title = f'{object.title} - {tournament.name}'
    meta_description = f'Leaderboard for {object.title}'

    # Build filter URL for the current page
    if round:
        filter_url = reverse('round-component-leaderboard', kwargs={'tournament_slug': tournament.slug, 'stage_slug': stage.slug, 'round_slug': round.slug, 'post_slug': post_slug})
    elif stage:
        filter_url = reverse('stage-component-leaderboard', kwargs={'tournament_slug': tournament.slug, 'stage_slug': stage.slug, 'post_slug': post_slug})
    else:
        filter_url = reverse('tournament-component-leaderboard', kwargs={'tournament_slug': tournament.slug, 'post_slug': post_slug})

    # Paginate games
    games_count = filtered_games.count()
    paginator = Paginator(filtered_games, settings.PAGE_SIZE)
    page_number = request.GET.get('page')
    try:
        page_obj = paginator.page(page_number)
    except PageNotAnInteger:
        page_obj = paginator.page(1)
    except EmptyPage:
        page_obj = paginator.page(paginator.num_pages)

    # Build query string without 'page' for pagination links
    query_params = request.GET.copy()
    query_params.pop('page', None)
    query_string = query_params.urlencode()

    context = {
        'selected_tournament': tournament,
        'tournament': tournament,
        'selected_stage': stage,
        'tournament_round': round,
        'top_players': top_players,
        'most_players': most_players,
        'top_factions': top_factions,
        'most_factions': most_factions,
        'has_top_players': bool(top_players),
        'has_most_players': bool(most_players),
        'has_top_factions': bool(top_factions),
        'has_most_factions': bool(most_factions),
        'leaderboard_threshold': threshold,
        'leaderboard_places': limit,
        'games_count': games_count,
        'games': page_obj,
        'page_obj': page_obj,
        'query_string': query_string,
        'form': filterset.form,
        'filter_url': filter_url,
        'meta_title': meta_title,
        'meta_description': meta_description,
        'title': title,
        'post_name': object.title,
        'post_url': object.get_absolute_url(),
        'post_image': object.small_picture.url if object.small_picture else (object.picture.url if object.picture else None),
        'post_image_class': 'lg-faction-icon',
        'object_type': post.component,
    }

    return render(request, 'the_warroom/scoped_leaderboard.html', context)


def tournament_player_leaderboard(request, tournament_slug, profile_slug, stage_slug=None, round_slug=None):
    from the_warroom.utils import get_single_stage

    threshold = get_int_param(request.GET.get('threshold', ''))
    limit = get_int_param(request.GET.get('limit', ''))
    if limit is not None:
        limit = min(limit, 50)

    tournament = get_object_or_404(Tournament, slug=tournament_slug.lower())

    if stage_slug:
        stage = get_object_or_404(Stage, slug=stage_slug.lower(), tournament=tournament)
    else:
        # Try to get single stage if round_slug is provided (simplified URL)
        if round_slug:
            stage = get_single_stage(tournament)
            if not stage:
                return redirect(tournament.get_absolute_url())
        else:
            stage = None

    if round_slug:
        round = get_object_or_404(Round, slug=round_slug.lower(), stage=stage)
    else:
        round = None

    player = get_object_or_404(Profile, slug=profile_slug.lower())

    # Build game queryset scoped to tournament/stage/round for this player
    game_filter = Q(final=True, efforts__player=player)
    if round:
        game_filter &= Q(round=round)
    elif stage:
        game_filter &= Q(round__stage=stage)
    else:
        game_filter &= Q(round__stage__tournament=tournament)

    opts = Game.with_efforts()
    games_qs = Game.objects.filter(game_filter).select_related(*opts['select']).prefetch_related(*opts['prefetch']).distinct()

    # Apply user filter
    filterset = TournamentGameFilter(request.GET, queryset=games_qs, tournament=tournament if not stage else None, stage=stage if not round else None, round=round)
    filtered_games = filterset.qs.order_by('-date_posted')

    # Build efforts from filtered games for this player
    efforts = Effort.objects.filter(game__in=filtered_games, player=player)

    if threshold is None:
        threshold = 1
    if limit is None:
        limit = round.get_leaderboard_positions() if round else stage.get_leaderboard_positions() if stage else tournament.leaderboard_positions

    if round:
        faction_link = lambda f: reverse('round-component-leaderboard', kwargs={'tournament_slug': tournament.slug, 'stage_slug': stage.slug, 'round_slug': round.slug, 'post_slug': f.slug})
    elif stage:
        faction_link = lambda f: reverse('stage-component-leaderboard', kwargs={'tournament_slug': tournament.slug, 'stage_slug': stage.slug, 'post_slug': f.slug})
    else:
        faction_link = lambda f: reverse('tournament-component-leaderboard', kwargs={'tournament_slug': tournament.slug, 'post_slug': f.slug})

    top_factions = Faction.leaderboard(limit=limit, effort_qs=efforts, game_threshold=threshold, link_builder=faction_link)
    most_factions = Faction.leaderboard(limit=limit, effort_qs=efforts, top_quantity=True, game_threshold=threshold, link_builder=faction_link)

    if round:
        meta_title = f'{tournament.name} - {stage.name} ({round.name})'
        title = f'{player.display_name} - {tournament.name}: {stage.name} ({round.name})'
    elif stage:
        meta_title = f'{tournament.name} - {stage.name}'
        title = f'{player.display_name} - {tournament.name} ({stage.name})'
    else:
        meta_title = f'{tournament.name}'
        title = f'{player.display_name} - {tournament.name}'
    meta_description = f'Faction Leaderboard for {player.display_name}'

    # Build filter URL for the current page
    if round:
        filter_url = reverse('round-player-leaderboard', kwargs={'tournament_slug': tournament.slug, 'stage_slug': stage.slug, 'round_slug': round.slug, 'profile_slug': profile_slug})
    elif stage:
        filter_url = reverse('stage-player-leaderboard', kwargs={'tournament_slug': tournament.slug, 'stage_slug': stage.slug, 'profile_slug': profile_slug})
    else:
        filter_url = reverse('tournament-player-leaderboard', kwargs={'tournament_slug': tournament.slug, 'profile_slug': profile_slug})

    # Paginate games
    games_count = filtered_games.count()
    paginator = Paginator(filtered_games, settings.PAGE_SIZE)
    page_number = request.GET.get('page')
    try:
        page_obj = paginator.page(page_number)
    except PageNotAnInteger:
        page_obj = paginator.page(1)
    except EmptyPage:
        page_obj = paginator.page(paginator.num_pages)

    # Build query string without 'page' for pagination links
    query_params = request.GET.copy()
    query_params.pop('page', None)
    query_string = query_params.urlencode()

    context = {
        'selected_tournament': tournament,
        'tournament': tournament,
        'selected_stage': stage,
        'tournament_round': round,
        'top_factions': top_factions,
        'most_factions': most_factions,
        'has_top_factions': bool(top_factions),
        'has_most_factions': bool(most_factions),
        'has_top_players': False,
        'has_most_players': False,
        'leaderboard_threshold': threshold,
        'leaderboard_places': limit,
        'games_count': games_count,
        'games': page_obj,
        'page_obj': page_obj,
        'query_string': query_string,
        'form': filterset.form,
        'filter_url': filter_url,
        'meta_title': meta_title,
        'meta_description': meta_description,
        'title': title,
        'post_name': player.display_name,
        'post_url': player.get_absolute_url(),
        'post_image': player.image.url,
        'post_image_class': 'lg-avatar-icon',
        'object_type': 'Profile',
    }

    return render(request, 'the_warroom/scoped_leaderboard.html', context)


def stage_players_pagination(request, id):
    if not request.htmx:
        return HttpResponse(status=404)
    stage = get_object_or_404(Stage, id=id)

    stage_participant_ids = StageParticipant.objects.filter(stage=stage).values_list('tournament_player__profile_id', flat=True)
    players = Profile.objects.filter(
        Q(efforts__game__round__stage=stage) | Q(id__in=stage_participant_ids)
    ).distinct()

    players = players.annotate(
        total_efforts=Count('efforts', distinct=True, filter=Q(efforts__game__round__stage=stage, efforts__game__final=True)),
        win_count=Count('efforts', distinct=True, filter=Q(efforts__win=True, efforts__game__round__stage=stage, efforts__game__final=True)),
        coalition_count=Count('efforts', distinct=True, filter=Q(efforts__win=True, efforts__game__coalition_win=True, efforts__game__round__stage=stage, efforts__game__final=True))
    )
    players = players.annotate(
        win_rate=Case(
            When(total_efforts=0, then=Value(0)),
            default=ExpressionWrapper(
                (Cast(F('win_count'), FloatField()) - (Cast(F('coalition_count'), FloatField()) / 2)) / Cast(F('total_efforts'), FloatField()) * 100,
                output_field=FloatField()
            ),
            output_field=FloatField()
        ),
        tourney_points=Case(
            When(total_efforts=0, then=Value(0)),
            default=ExpressionWrapper(
                Cast(F('win_count'), FloatField()) - (Cast(F('coalition_count'), FloatField()) / 2),
                output_field=FloatField()
            ),
            output_field=FloatField()
        )
    ).order_by('-total_efforts', '-tourney_points', '-win_rate', 'display_name')

    paginator = Paginator(players, settings.PAGE_SIZE)
    page_number = request.GET.get('page', 1)
    try:
        page_obj = paginator.get_page(page_number)
    except EmptyPage:
        page_obj = paginator.page(paginator.num_pages)

    return render(request, 'the_warroom/partials/player_list.html', {'players': page_obj, 'object': stage})



def round_games_pagination(request, id):
    
    if not request.htmx:
        return HttpResponse(status=404)
    round = get_object_or_404(Round, id=id)

    games = round.games.filter(final=True)

    # Paginate games
    paginator = Paginator(games, settings.PAGE_SIZE)  # Use the queryset directly
    page_number = request.GET.get('page', 1)   # Get the page number from the request
 
    try:
        page_obj = paginator.get_page(page_number)  # Get the specific page of games
    except EmptyPage:
        page_obj = paginator.page(paginator.num_pages)  # Redirect to the last page if invalid
    


    return render(request, 'the_warroom/partials/round_game_list.html', {'games': page_obj, 'object': round})



@player_onboard_required
def round_manage_view(request, tournament_slug, stage_slug=None, round_slug=None):
    from the_warroom.utils import get_single_stage

    tournament = get_object_or_404(Tournament, slug=tournament_slug)

    if stage_slug:
        # Full 3-slug URL path
        stage = get_object_or_404(Stage, tournament=tournament, slug=stage_slug)
    else:
        # Simplified URL path
        if round_slug:
            # UPDATE mode with simplified URL
            stage = get_single_stage(tournament)
            if not stage:
                return redirect(tournament.get_absolute_url())
            # Get round to verify it exists
            round_instance_check = get_object_or_404(Round, slug=round_slug, stage=stage)
            stage = round_instance_check.stage
        else:
            # CREATE mode with simplified URL
            stage = get_single_stage(tournament)
            if not stage:
                return redirect(tournament.get_absolute_url())

    if not tournament.has_permission(request.user.profile):
        messages.error(request, "You do not have permission to view this page.")
        raise PermissionDenied()

    max_round_number = stage.rounds.aggregate(max_num=Max('round_number'))['max_num'] or 0
    current_round = max_round_number + 1

    round_instance = None
    # If round_slug is provided, update the existing round
    if round_slug:
        round_instance = get_object_or_404(Round, slug=round_slug, stage=stage)
        form = RoundCreateForm(request.POST or None, stage=stage, instance=round_instance, current_round=current_round)
    else:
        # Otherwise, create a new round
        form = RoundCreateForm(request.POST or None, stage=stage, current_round=current_round)

    if form.is_valid():
        round_instance = form.save(commit=False)
        if not round_instance.pk:
            round_instance.tournament = tournament
            round_instance.stage = stage
        if not round_instance.name:
            round_instance.name = f"Round {round_instance.round_number}"
        round_instance.save()
        return redirect(round_instance.get_absolute_url())

    # Existing round names in this stage for frontend duplicate validation
    existing_round_names = list(
        stage.rounds.exclude(name__isnull=True).exclude(name='').values_list('name', flat=True)
    )
    if round_instance:
        existing_round_names = [n for n in existing_round_names if n != round_instance.name]

    context = {
        'form': form,
        'tournament': tournament,
        'stage': stage,
        'round': round_instance,
        'existing_names_json': json.dumps(existing_round_names),
        }
    return render(request, 'the_warroom/round_form.html', context)


@player_onboard_required
def round_manage_players(request, tournament_slug, round_slug, stage_slug=None):
    """Redirect to stage player management — player roster is managed at the stage level."""
    from the_warroom.utils import get_single_stage

    tournament = get_object_or_404(Tournament, slug=tournament_slug)

    if stage_slug:
        return redirect('stage-manage-players', tournament_slug=tournament_slug, stage_slug=stage_slug)
    else:
        stage = get_single_stage(tournament)
        if not stage:
            return redirect(tournament.get_absolute_url())
        return redirect('stage-manage-players', tournament_slug=tournament_slug, stage_slug=stage.slug)


@player_required_class_based_view
class RoundDeleteView(LoginRequiredMixin, UserPassesTestMixin, DeleteView):
    model = Round

    def get_object(self, queryset=None):
        from the_warroom.utils import get_single_stage

        tournament_slug = self.kwargs.get('tournament_slug')
        stage_slug = self.kwargs.get('stage_slug')
        round_slug = self.kwargs.get('round_slug')

        tournament = get_object_or_404(Tournament, slug=tournament_slug)

        if stage_slug:
            # Full 3-slug URL path
            stage = get_object_or_404(Stage, slug=stage_slug, tournament=tournament)
        else:
            # Simplified 2-slug URL path
            stage = get_single_stage(tournament)
            if not stage:
                raise Http404("This tournament uses stages. Please use the full URL.")

        return get_object_or_404(Round, slug=round_slug, stage=stage)

    def test_func(self):
        obj = self.get_object()
        profile = self.request.user.profile
        # Only allow deletion for admins or the tournament designer (not moderators)
        return profile.admin or profile == obj.stage.tournament.designer

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['round'] = self.object
        context['stage'] = self.object.stage
        context['tournament'] = self.object.stage.tournament
        return context

    # Dynamically set the success URL based on the round's tournament
    def get_success_url(self):
        stage = self.object.stage
        tournament = stage.tournament
        if tournament.use_stages:
            return reverse_lazy('stage-details-page', kwargs={
                'tournament_slug': tournament.slug,
                'stage_slug': stage.slug,
            })
        return reverse_lazy('tournament-detail', kwargs={'slug': tournament.slug})
    
    def post(self, request, *args, **kwargs):
        # print('Trying to delete')
        round = self.get_object()
        # print(round)
        name = round.name
        try:
            # Attempt to delete the round
            response = self.delete(request, *args, **kwargs)
            # Add success message upon successful deletion
            messages.success(request, f"'{round}' was successfully deleted.")
            return response
        except ProtectedError:
            # Handle the case where the deletion fails due to foreign key protection
            messages.error(request, f"The Tournament Round '{name}' cannot be deleted because games have already been recorded.")
            # Redirect back to the round detail page
            return redirect(round.get_absolute_url())  # Make sure `round.get_absolute_url()` is correct
        except IntegrityError:
            # Handle other integrity errors (if any)
            messages.error(request, "An error occurred while trying to delete this Tournament Round.")
            return redirect(round.get_absolute_url())
        

@login_required
def in_progress_view(request):
    language_code = get_language()
    language_object = Language.objects.filter(code=language_code).first()
    user_profile = request.user.profile
    opts = Game.with_efforts()
    in_progress_games = Game.objects.filter(
        recorder=user_profile, final=False
    ).select_related(*opts['select']).prefetch_related(
        *opts['prefetch'], 'hirelings', 'landmarks', 'tweaks'
    )
    prefetch_scorecard = [
        'faction', 'effort__game'
    ]
    in_progress_scorecards = ScoreCard.objects.filter(recorder=user_profile, final=False).prefetch_related(*prefetch_scorecard)
    
    in_progress_scorecards = in_progress_scorecards.annotate(
        selected_title=Coalesce(
            Subquery(
                PostTranslation.objects.filter(
                    post=OuterRef('faction__pk'),
                    language__code=language_object.code
                ).values('translated_title')[:1]
            ),
            F('faction__title')  # Fallback to the original faction title
        )
    )

    # Theme
    theme = get_theme(request)
    background_image, foreground_images, theme_artists, background_pattern = get_thematic_images(
        theme=theme, page='games'
    )

    context = {
        'in_progress_games': in_progress_games,
        'in_progress_games_count': in_progress_games.count(),
        'in_progress_scorecards': in_progress_scorecards,
        'in_progress_scorecards_count': in_progress_scorecards.count(),
        'background_image': background_image,
        'foreground_images': foreground_images,
        'background_pattern': background_pattern,
        'page_artists': theme_artists,
    }

    return render(request, 'the_warroom/in_progress.html', context)


# ============================================================================
# Scorecard Management V2
# ============================================================================

@player_onboard_required
def scorecard_manage_view(request, id=None):
    """
    Improved scorecard management view with:
    - TurnScore deletion capability
    - Better UI for seat/faction selection
    - Dynamic point totals
    - Clearer navigation between seats
    """
    faction = request.GET.get('faction', None)
    effort_id = request.GET.get('effort', None)
    game_group = request.GET.get('game_group', None)

    faction_name = None
    faction_color = None
    faction_color_rgb = None
    faction_obj = None

    if faction:
        faction_obj = Faction.objects.filter(id=faction).first()
        if faction_obj:
            faction_name = faction_obj.title
            faction_color = faction_obj.color
            if faction_obj.color_r is not None:
                faction_color_rgb = f"{faction_obj.color_r}, {faction_obj.color_g}, {faction_obj.color_b}"

    game = None
    next_scorecard = None
    previous_scorecard = None
    next_effort = None
    previous_effort = None
    next_effort_scorecard = None
    previous_effort_scorecard = None
    all_game_efforts = []

    try:
        effort = Effort.objects.get(id=effort_id)
        game = effort.game
        participants = []
        for participant_effort in game.efforts.all():
            participants.append(participant_effort.player)
        if game.recorder:
            participants.append(game.recorder)
        if not request.user.profile in participants:
            raise PermissionDenied()
    except Effort.DoesNotExist:
        effort = None

    if id:
        obj = get_object_or_404(ScoreCard, id=id)
        if obj.recorder != request.user.profile:
            raise PermissionDenied()
        if obj.faction != None:
            faction_obj = obj.faction
            faction = obj.faction.id
            faction_name = obj.faction.title
            faction_color = obj.faction.color
            if obj.faction.color_r is not None:
                faction_color_rgb = f"{obj.faction.color_r}, {obj.faction.color_g}, {obj.faction.color_b}"
        if obj.effort != None:
            effort = obj.effort
            game = effort.game

        if not obj.effort:
            grouped_scorecards = ScoreCard.objects.filter(
                game_group=obj.game_group, effort=None, recorder=request.user.profile
            ).order_by('date_posted')

            scorecard_list = list(grouped_scorecards)
            current_index = scorecard_list.index(obj)

            next_scorecard = scorecard_list[(current_index + 1) % len(scorecard_list)]
            previous_scorecard = scorecard_list[(current_index - 1) % len(scorecard_list)]
            if next_scorecard == obj:
                next_scorecard = None
            if previous_scorecard == obj:
                previous_scorecard = None
    else:
        obj = ScoreCard()
        if game_group:
            grouped_scorecards = ScoreCard.objects.filter(
                game_group=game_group, effort=None, recorder=request.user.profile
            ).order_by('date_posted')
            scorecard_list = list(grouped_scorecards)
            if scorecard_list:
                next_scorecard = scorecard_list[0]
                previous_scorecard = scorecard_list[-1]

    if effort and game:
        game_efforts = Effort.objects.filter(game=game).order_by('seat')
        effort_list = list(game_efforts)
        current_index = effort_list.index(effort)

        # Get all efforts for the game (for seat selector)
        all_game_efforts = [
            {
                'effort': e,
                'scorecard': getattr(e, 'scorecard', None),
                'is_current': e == effort,
                'image_url': e.faction.small_icon.url,
            }
            for e in effort_list
        ]

        next_effort = effort_list[(current_index + 1) % len(effort_list)]
        previous_effort = effort_list[(current_index - 1) % len(effort_list)]
        next_effort_scorecard = getattr(next_effort, 'scorecard', None)
        previous_effort_scorecard = getattr(previous_effort, 'scorecard', None)

    user = request.user

    # Handle TurnScore deletion via HTMX
    if request.method == 'DELETE':
        turn_id = request.GET.get('turn_id')
        if turn_id and id:
            try:
                turn = TurnScore.objects.get(id=turn_id, scorecard=obj)
                turn_number = turn.turn_number
                turn.delete()

                # Renumber remaining turns
                remaining_turns = obj.turns.filter(turn_number__gt=turn_number).order_by('turn_number')
                for t in remaining_turns:
                    t.turn_number -= 1
                    t.save()

                # Recalculate totals
                obj.save(recalculate_game_points=True)

                # Return success response for HTMX
                return HttpResponse(status=200, headers={'HX-Trigger': 'turnDeleted'})
            except TurnScore.DoesNotExist:
                return HttpResponse(status=404)
        return HttpResponse(status=400)

    if id:
        existing_turns = obj.turns.all()
        existing_count = existing_turns.count()
        extra_forms = 0
    else:
        existing_count = 0
        extra_forms = 1

    TurnFormset = modelformset_factory(TurnScore, form=TurnScoreCreateForm, extra=extra_forms, can_delete=True)
    qs = obj.turns.all() if id else TurnScore.objects.none()
    formset = TurnFormset(request.POST or None, queryset=qs)
    form_count = extra_forms + existing_count
    form = ScoreCardCreateForm(request.POST or None, instance=obj, user=user, faction=faction)

    if effort:
        score = effort.score
    else:
        score = None

    if not obj.total_generic_points and obj.id and obj.total_points and obj.total_points != 0:
        generic_view = False
    else:
        generic_view = True

    context = {
        'form': form,
        'formset': formset,
        'object': obj,
        'form_count': form_count,
        'faction': faction,
        'faction_obj': faction_obj,
        'faction_name': faction_name,
        'faction_color': faction_color,
        'faction_color_rgb': faction_color_rgb,
        'score': score,
        'next_scorecard': next_scorecard,
        'previous_scorecard': previous_scorecard,
        'game_group': game_group,
        'generic_view': generic_view,
        'next_effort': next_effort,
        'next_effort_scorecard': next_effort_scorecard,
        'previous_effort': previous_effort,
        'previous_effort_scorecard': previous_effort_scorecard,
        'game': game,
        'effort': effort,
        'all_game_efforts': all_game_efforts,
    }

    if form.is_valid() and formset.is_valid():
        # warning_message = False
        parent = form.save(commit=False)
        parent.recorder = request.user.profile
        parent.effort = effort
        parent.save()
        dominance = False

        total_points = 0

        for turn_form in formset:
            turn_dominance = turn_form.cleaned_data.get('dominance')
            if turn_dominance:
                dominance = True
            total_points += turn_form.cleaned_data.get('total_points', 0)

        if effort:
            final_scorecard = True
        else:
            final_scorecard = False

        if effort and total_points != effort.score and effort.score:
            final_scorecard = False

        elif total_points < 0:
            final_scorecard = False

        elif effort and effort.score == 0 and total_points != 0 and not effort.dominance and not effort.coalition_with:
            final_scorecard = False

        if (effort and dominance and not effort.dominance and not effort.coalition_with) or (effort and not dominance and effort.dominance) or (effort and not dominance and effort.coalition_with):
            final_scorecard = False

        for turn_form in formset:
            child = turn_form.save(commit=False)
            child.scorecard = parent
            child.save()

        if parent.dominance != dominance:
            parent.dominance = dominance

        if parent.final != final_scorecard:
            parent.final = final_scorecard

        parent.save(recalculate_game_points=True)

        # Renumber turns after deletions
        turns = parent.turns.all().order_by('turn_number')
        for i, turn in enumerate(turns, start=1):
            if turn.turn_number != i:
                turn.turn_number = i
                turn.save()

        if request.POST.get('next'):
            return redirect('update-scorecard', next_scorecard.id)
        if request.POST.get('previous'):
            return redirect('update-scorecard', previous_scorecard.id)
        if request.POST.get('add_player'):
            game_group = parent.game_group
            encoded_game_group = quote(str(game_group))
            return redirect(f'{reverse("record-scorecard")}?game_group={encoded_game_group}')
        if request.POST.get('next-effort'):
            if next_effort_scorecard:
                return redirect('update-scorecard', next_effort_scorecard.id)
            else:
                url = reverse('record-scorecard')
                query_string = f'?faction={next_effort.faction.id}&effort={next_effort.id}'
                return redirect(f'{url}{query_string}')
        if request.POST.get('previous-effort'):
            if previous_effort_scorecard:
                return redirect('update-scorecard', previous_effort_scorecard.id)
            else:
                url = reverse('record-scorecard')
                query_string = f'?faction={previous_effort.faction.id}&effort={previous_effort.id}'
                return redirect(f'{url}{query_string}')

        # Handle seat navigator buttons (goto_scorecard and goto_effort)
        goto_scorecard_id = request.POST.get('goto_scorecard')
        if goto_scorecard_id:
            return redirect('update-scorecard', int(goto_scorecard_id))

        goto_effort_id = request.POST.get('goto_effort')
        if goto_effort_id:
            try:
                target_effort = Effort.objects.get(id=goto_effort_id)
                url = reverse('record-scorecard')
                query_string = f'?faction={target_effort.faction.id}&effort={target_effort.id}'
                return redirect(f'{url}{query_string}')
            except Effort.DoesNotExist:
                pass

        context['message'] = "Scores Saved"
        return redirect(parent.get_absolute_url())

    return render(request, 'the_warroom/record_scores_v2.html', context)


# ============================
# Tournament Management Views (v2)
# ============================

@player_onboard_required
def tournament_manage_players(request, slug):
    """Dedicated player management page with settings + HTMX player groups."""
    tournament = get_object_or_404(Tournament, slug=slug)

    # Permission check
    if not tournament.has_permission(request.user.profile):
        raise PermissionDenied()

    profile = request.user.profile
    is_owner = profile.admin or profile == tournament.designer

    form = None
    if is_owner:
        if request.method == 'POST':
            form = TournamentPlayerSettingsForm(request.POST, instance=tournament, user=request.user)
            if form.is_valid():
                form.save()
                messages.success(request, "Player settings updated.")
                return redirect('tournament-manage-players', slug=slug)
        else:
            form = TournamentPlayerSettingsForm(instance=tournament, user=request.user)

    context = {
        'tournament': tournament,
        'object': tournament,  # For template title
        'form': form,
        'is_owner': is_owner,
        'players': tournament.get_players_queryset(),
        'waitlist_players': tournament.get_waitlist_players_queryset(),
        'eliminated_players': tournament.get_eliminated_players_queryset(),
    }
    return render(request, 'the_warroom/tournament_manage_players.html', context)


@player_onboard_required
def tournament_manage_assets(request, slug):
    """Dedicated asset management page with settings + HTMX asset categories."""
    tournament = get_object_or_404(Tournament, slug=slug)

    # Permission check
    if not tournament.has_permission(request.user.profile):
        raise PermissionDenied()

    if request.method == 'POST':
        form = TournamentAssetSettingsForm(request.POST, instance=tournament)
        if form.is_valid():
            original = Tournament.objects.get(pk=tournament.pk)
            previous_asset_mode = original.asset_mode
            previous_platform = original.platform
            form.save()
            set_default_tournament_assets(tournament, previous_asset_mode, previous_platform)
            messages.success(request, "Asset settings updated.")
            return redirect('tournament-manage-assets', slug=slug)
    else:
        form = TournamentAssetSettingsForm(instance=tournament)

    # Determine UI visibility based on asset mode
    show_asset_management = tournament.asset_mode == AssetModeChoices.SELECTED

    context = {
        'tournament': tournament,
        'form': form,
        'show_asset_management': show_asset_management,
        'asset_mode_choices': AssetModeChoices,
        'asset_types': [
            ('faction', 'Factions', tournament.factions.all(), 'bi-shield'),
            ('map', 'Maps', tournament.maps.all(), 'bi-map'),
            ('deck', 'Decks', tournament.decks.all(), 'bi-stack'),
            ('hireling', 'Hirelings', tournament.hirelings.all(), 'bi-person-badge'),
            ('landmark', 'Landmarks', tournament.landmarks.all(), 'bi-geo-alt'),
            ('tweak', 'House Rules', tournament.tweaks.all(), 'bi-wrench'),
            ('vagabond', 'Vagabonds', tournament.vagabonds.all(), 'bi-person-walking'),
        ],
    }
    return render(request, 'the_warroom/tournament_manage_assets.html', context)


def get_status_info(entity, entity_type, tournament=None, stage=None):
    """Calculate status display information for Tournament/Stage/Round settings hub."""
    now = timezone.now().date()

    if entity_type == 'tournament':
        t = entity.classification
        if entity.is_active:
            if entity.status == CompetitionStatus.COMPLETED:
                return {'alert_type': 'secondary', 'status_word': 'Completed', 'alert_message': f'{t} is completed', 'icon': 'bi-check-circle-fill'}
            if entity.end_date and entity.end_date < now:
                return {'alert_type': 'secondary', 'status_word': 'Completed', 'alert_message': f'{t} has ended', 'icon': 'bi-clock-history'}
            if entity.start_date and entity.start_date > now:
                return {'alert_type': 'info', 'status_word': 'Scheduled', 'alert_message': f'{t} is active but has not started yet', 'icon': 'bi-info-circle-fill'}
            return {'alert_type': 'success', 'status_word': 'Active', 'alert_message': f'{t} is active and available', 'icon': 'bi-check-circle-fill'}
        return {'alert_type': 'danger', 'status_word': 'Inactive', 'alert_message': f'{t} is inactive and not available', 'icon': 'bi-x-circle-fill'}

    elif entity_type == 'stage':
        if entity.is_active:
            if entity.status == CompetitionStatus.COMPLETED:
                return {'alert_type': 'secondary', 'status_word': 'Completed', 'alert_message': 'Stage is completed', 'icon': 'bi-check-circle-fill'}
            if not tournament.is_active:
                return {'alert_type': 'warning', 'status_word': 'Inactive', 'alert_message': 'Stage is active but tournament is inactive', 'icon': 'bi-exclamation-triangle-fill'}
            if (entity.end_date and entity.end_date < now) or (tournament.end_date and tournament.end_date < now):
                return {'alert_type': 'secondary', 'status_word': 'Completed', 'alert_message': 'Stage has ended', 'icon': 'bi-clock-history'}
            if (entity.start_date and entity.start_date > now) or (tournament.start_date and tournament.start_date > now):
                return {'alert_type': 'info', 'status_word': 'Scheduled', 'alert_message': 'Stage is active but has not started yet', 'icon': 'bi-info-circle-fill'}
            return {'alert_type': 'success', 'status_word': 'Active', 'alert_message': 'Stage is active and available', 'icon': 'bi-check-circle-fill'}
        return {'alert_type': 'danger', 'status_word': 'Inactive', 'alert_message': 'Stage is inactive', 'icon': 'bi-x-circle-fill'}

    elif entity_type == 'round':
        if entity.is_active:
            if entity.status == CompetitionStatus.COMPLETED:
                return {'alert_type': 'secondary', 'status_word': 'Completed', 'alert_message': 'Round is completed', 'icon': 'bi-check-circle-fill'}
            if not tournament.is_active or not stage.is_active:
                return {'alert_type': 'warning', 'status_word': 'Inactive', 'alert_message': 'Round is active but parent is inactive', 'icon': 'bi-exclamation-triangle-fill'}
            if (entity.end_date and entity.end_date < now) or (stage.end_date and stage.end_date < now) or (tournament.end_date and tournament.end_date < now):
                return {'alert_type': 'secondary', 'status_word': 'Completed', 'alert_message': 'Round has ended', 'icon': 'bi-clock-history'}
            if (entity.start_date and entity.start_date > now) or (stage.start_date and stage.start_date > now) or (tournament.start_date and tournament.start_date > now):
                return {'alert_type': 'info', 'status_word': 'Scheduled', 'alert_message': 'Round is active but has not started yet', 'icon': 'bi-info-circle-fill'}
            return {'alert_type': 'success', 'status_word': 'Active', 'alert_message': 'Round is active and available', 'icon': 'bi-check-circle-fill'}
        return {'alert_type': 'danger', 'status_word': 'Inactive', 'alert_message': 'Round is inactive', 'icon': 'bi-x-circle-fill'}


def build_status_hierarchy(entity_type, tournament, stage=None, round_obj=None):
    """Build ordered list of status info dicts for hierarchical display."""
    hierarchy = []

    t_info = get_status_info(tournament, 'tournament')
    t_info['label'] = tournament.name
    t_info['is_current'] = (entity_type == 'tournament')
    hierarchy.append(t_info)

    if entity_type in ('stage', 'round') and stage and tournament.use_stages:
        s_info = get_status_info(stage, 'stage', tournament=tournament)
        s_info['label'] = stage.name
        s_info['is_current'] = (entity_type == 'stage')
        hierarchy.append(s_info)

    if entity_type == 'round' and round_obj:
        r_info = get_status_info(round_obj, 'round', tournament=tournament, stage=stage)
        r_info['label'] = round_obj.name or 'Round'
        r_info['is_current'] = True
        hierarchy.append(r_info)

    return hierarchy


def _schedule_round_manage_url(tournament, round):
    """Where the 'Manage Scheduled Matches' link points for a single round,
    resolved by use_stages × the round's stage use_rounds (the 2x2 routing):
      (stages, rounds)  -> round-matches-page (full, with stage)
      (no stages, rounds) -> round-matches-simple (omits stage)
      (rounds off)      -> stage-matches-page (the stage's flat matches view)
      (no stages, rounds off) -> tournament-bracket-page (delegates to matches)
    """
    stage = round.stage
    if stage.use_rounds:
        if tournament.use_stages:
            return reverse('round-matches-page', kwargs={
                'tournament_slug': tournament.slug,
                'stage_slug': stage.slug,
                'round_slug': round.slug,
            })
        return reverse('round-matches-simple', kwargs={
            'tournament_slug': tournament.slug,
            'round_slug': round.slug,
        })
    if tournament.use_stages:
        return reverse('stage-matches-page', kwargs={
            'tournament_slug': tournament.slug,
            'stage_slug': stage.slug,
        })
    return reverse('tournament-bracket-page', kwargs={'slug': tournament.slug})


def _schedule_matches_context(tournament, rounds):
    """Context for the 'Schedule Matches' settings-hub card (League / Game Group).

    `rounds` is an iterable of the schedulable rounds in scope for a hub section.
    Builds one entry per round with its enabled state and the URLs its row needs.
    Tournament-classification series use the grouping pipeline instead, so this
    returns empty for them.
    """
    rounds = [r for r in (rounds or []) if r]
    if tournament.classification == Tournament.ClassificationTypes.TOURNAMENT or not rounds:
        return {'schedule_rounds': [], 'schedule_round': None, 'matches_enabled': False}

    schedule_rounds = []
    for r in rounds:
        schedule_rounds.append({
            'round': r,
            'matches_enabled': r.bracket_status == Round.BracketStatusChoices.FINALIZED,
            'manage_url': _schedule_round_manage_url(tournament, r),
            'enable_url': reverse('round-enable-matches', kwargs={
                'tournament_slug': tournament.slug,
                'stage_slug': r.stage.slug,
                'round_slug': r.slug,
            }),
        })

    first = schedule_rounds[0]
    return {
        'schedule_rounds': schedule_rounds,
        # Back-compat single-round values (used by the `{% if schedule_round %}` gate).
        'schedule_round': first['round'],
        'matches_enabled': first['matches_enabled'],
    }


def _grouping_step(round):
    """Map a round's grouping/bracket status to a settings-hub step label."""
    if not round:
        return None
    if round.bracket_status == Round.BracketStatusChoices.FINALIZED:
        return 'matches_finalized'
    if round.matches.exists():
        return 'matches_draft'
    if round.grouping_status == Round.GroupingStatusChoices.FINALIZED:
        return 'groups_finalized'
    if round.player_groups.exists():
        return 'groups_draft'
    return 'no_groups'


def _settings_level_context(level, tournament, stage=None, round=None, *, is_owner):
    """Build the card-relevant flags for ONE settings level (a hub section).

    `level` is 'round' | 'stage' | 'series'. Returns a dict consumed by
    partials/settings_cards.html, namespaced so a section can render without
    depending on the page's "current object".
    """
    from the_tavern.models import Survey
    from the_warroom.utils import get_single_stage

    ctx = {
        'level': level,
        'tournament': tournament,
        'stage': stage,
        'round': round,
        'is_owner': is_owner,
    }

    if level == 'series':
        default_stage = get_single_stage(tournament)
        default_round = default_stage.rounds.first() if default_stage else None
        tournament_stages = tournament.stages.all().order_by('order')
        default_stage_rounds = (
            default_stage.rounds.all().order_by('round_number') if default_stage else None
        )
        # When the tournament has no stages and its default stage has no rounds,
        # the series hub stands in for the (hidden) default stage — surface the
        # Player Grouping card the same way the stage hub does.
        grouping_step = None
        if (
            default_stage is not None
            and not default_stage.use_rounds
            and tournament.classification == "Tournament"
        ):
            grouping_step = _grouping_step(default_round)
        ctx.update({
            'heading': f'{tournament.name} Settings',
            'can_manage': is_owner,
            'survey_count': Survey.objects.filter(series=tournament).count(),
            'has_games': Game.objects.filter(round__stage__tournament=tournament).exists(),
            'tournament_stages': tournament_stages,
            'use_stages_locked': tournament_stages.count() >= 2,
            'default_stage': default_stage,
            'default_stage_rounds': default_stage_rounds,
            'grouping_step': grouping_step,
        })
        # Series-level scheduling only for flat (no-stages) series; multi-stage
        # series schedule per stage on each stage hub instead.
        series_rounds = list(default_stage_rounds) if (default_stage and not tournament.use_stages) else []
        ctx.update(_schedule_matches_context(tournament, series_rounds))

    elif level == 'stage':
        has_match_series = MatchSeries.objects.filter(round__stage=stage).exists()
        has_game_data = Effort.objects.filter(
            game__round__stage=stage, game__test_match=False, game__final=True
        ).exists()
        grouping_step = None
        hidden_round = None
        if not stage.use_rounds and tournament.classification == "Tournament":
            hidden_round = stage.rounds.first()
            grouping_step = _grouping_step(hidden_round)
        stage_rounds = stage.rounds.all().order_by('round_number')
        ctx.update({
            'heading': f'{stage.name} Settings',
            'can_manage': True,  # permission-gated by the calling view
            'survey_count': Survey.objects.filter(series=tournament, stage=stage).count(),
            'has_games': Game.objects.filter(round__stage=stage).exists(),
            'has_advancement_data': has_match_series or has_game_data,
            'grouping_step': grouping_step,
            'hidden_round': hidden_round,
            'stage_rounds': stage_rounds,
            'use_rounds_locked': stage_rounds.count() >= 2,
            # Only consumed by the series-level grouping card, but must exist so
            # the shared footer's `stage|default:section.default_stage` resolves.
            'default_stage': stage,
        })
        # Stage-level scheduling: this stage's rounds (the single hidden default
        # round when use_rounds is off).
        ctx.update(_schedule_matches_context(tournament, stage_rounds))

    else:  # round
        ctx.update({
            'heading': f'{round.name} Settings',
            'can_manage': True,  # permission-gated by the calling view
            'survey_count': Survey.objects.filter(series=tournament, stage=stage).count(),
            'has_games': Game.objects.filter(round__stage=stage).exists(),
            'grouping_step': _grouping_step(round),
        })
        ctx.update(_schedule_matches_context(tournament, [round]))

    return ctx


def _mark_deepest_schedule_section(sections):
    """Flag only the deepest section so the Schedule Matches card renders once
    per hub (not duplicated in every stacked parent section). Sections are built
    deepest-first, so the first one is the deepest."""
    for i, section in enumerate(sections):
        section['show_schedule_card'] = (i == 0)
    return sections


@player_onboard_required
def tournament_settings_hub(request, slug):
    """Settings hub page with links to all tournament management tools."""
    tournament = get_object_or_404(Tournament, slug=slug)

    # Permission check
    if not tournament.has_permission(request.user.profile):
        raise PermissionDenied()

    profile = request.user.profile
    is_owner = profile.admin or profile == tournament.designer

    view_as_ctx, no_access, is_owner = settings_view_as_context(request, tournament, is_owner)

    sections = [] if no_access else [_settings_level_context('series', tournament, is_owner=is_owner)]
    _mark_deepest_schedule_section(sections)

    context = {
        'object_type': 'Series',
        'object': tournament,  # For template title
        'tournament': tournament,
        'sections': sections,
        'status_hierarchy': build_status_hierarchy('tournament', tournament),
        **view_as_ctx,
    }
    return render(request, 'the_warroom/settings_hub.html', context)


@player_onboard_required
def round_settings_hub(request, tournament_slug, round_slug, stage_slug=None):
    """Settings hub page with links to all round management tools."""
    from the_warroom.utils import get_single_stage

    tournament = get_object_or_404(Tournament, slug=tournament_slug)

    if stage_slug:
        stage = get_object_or_404(Stage, slug=stage_slug, tournament=tournament)
        round = get_object_or_404(Round, slug=round_slug, stage=stage)
    else:
        stage = get_single_stage(tournament)
        if not stage:
            return redirect(tournament.get_absolute_url())
        round = get_object_or_404(Round, slug=round_slug, stage=stage)

    # Permission check
    if not tournament.has_permission(request.user.profile):
        raise PermissionDenied()

    stage = round.stage
    profile = request.user.profile
    is_owner = profile.admin or profile == tournament.designer

    view_as_ctx, no_access, is_owner = settings_view_as_context(request, tournament, is_owner)

    # Stacked sections, deepest object first. The Stage section only appears when
    # stages are enabled (otherwise the stage is the hidden default stage).
    sections = []
    if not no_access:
        sections = [_settings_level_context('round', tournament, stage=stage, round=round, is_owner=is_owner)]
        if tournament.use_stages:
            sections.append(_settings_level_context('stage', tournament, stage=stage, is_owner=is_owner))
        sections.append(_settings_level_context('series', tournament, is_owner=is_owner))
    _mark_deepest_schedule_section(sections)

    context = {
        'object_type': 'Round',
        'object': round,  # For template title
        'tournament': tournament,
        'round': round,
        'stage': stage,
        'sections': sections,
        'status_hierarchy': build_status_hierarchy('round', tournament, stage=stage, round_obj=round),
        **view_as_ctx,
    }
    return render(request, 'the_warroom/settings_hub.html', context)


# ===== Stage Views =====

@player_onboard_required
def stage_manage_view(request, tournament_slug, stage_slug=None):
    """Create or update a Stage."""
    tournament = get_object_or_404(Tournament, slug=tournament_slug)

    if not tournament.has_permission(request.user.profile):
        raise PermissionDenied()

    stage_instance = None
    if stage_slug:
        stage_instance = get_object_or_404(Stage, slug=stage_slug, tournament=tournament)

    is_creating = stage_slug is None

    # Pre-process POST: create any "new stage" entries and replace sentinel values with real IDs
    post_data = request.POST.copy() if request.method == 'POST' else None
    if post_data:
        for which in ('winners', 'losers'):
            field_name = f'{which}_advance_to'
            if post_data.get(field_name) == f'new_{which}':
                new_name = post_data.get(f'new_{which}_stage_name', '').strip()
                if new_name:
                    max_order = tournament.stages.aggregate(Max('order'))['order__max'] or 0
                    new_stage = Stage.objects.create(
                        tournament=tournament,
                        name=new_name,
                        order=max_order + 1,
                    )
                    round_name = post_data.get(f'new_{which}_round_name', '').strip() or 'Round 1'
                    Round.objects.create(
                        stage=new_stage,
                        name=round_name,
                        round_number=1,
                    )
                    post_data[field_name] = str(new_stage.pk)
                else:
                    # No name provided — clear the sentinel so the field validates as blank
                    post_data[field_name] = ''

    if stage_slug:
        form = StageCreateForm(post_data, tournament=tournament, instance=stage_instance)
    else:
        form = StageCreateForm(post_data, tournament=tournament)

    if request.method == 'POST' and form.is_valid():
        stage_instance = form.save(commit=False)
        if not stage_instance.pk:
            stage_instance.tournament = tournament
        stage_instance.save()

        # Auto-create the default first Round when creating a new stage. The
        # round is always created; the user can rename it or add more rounds
        # later from the stage settings page after enabling rounds.
        if is_creating:
            Round.objects.create(
                stage=stage_instance,
                name='Round 1',
                round_number=1,
                start_date=stage_instance.start_date,
            )

        return redirect(stage_instance.get_absolute_url())

    # Existing stage names in this tournament for frontend duplicate validation
    existing_stage_names = list(
        tournament.stages.values_list('name', flat=True)
    )
    if stage_instance:
        existing_stage_names = [n for n in existing_stage_names if n != stage_instance.name]

    current_format = (stage_instance.stage_format if stage_instance else None) or tournament.default_format
    BRACKET_FORMATS = {FormatChoices.SINGLE_ELIM, FormatChoices.DOUBLE_ELIM}
    context = {
        'form': form,
        'tournament': tournament,
        'stage': stage_instance,
        'is_creating': is_creating,
        'existing_names_json': json.dumps(existing_stage_names),
        'is_bracket_format': current_format in BRACKET_FORMATS,
        'is_double_elim': current_format == FormatChoices.DOUBLE_ELIM,
    }
    return render(request, 'the_warroom/stage_form.html', context)


def stage_overview_page(request, tournament_slug, stage_slug):
    """Stage overview — lists rounds belonging to this stage."""
    tournament = get_object_or_404(Tournament, slug=tournament_slug)
    stage = get_object_or_404(Stage, slug=stage_slug, tournament=tournament)

    context = _stage_base_context(request, tournament, stage)
    context['active_page'] = 'overview'

    if stage.use_rounds:
        children = Round.objects.filter(stage=stage).select_related('stage__tournament').annotate(
            annotated_game_count=Count(
                'games',
                filter=Q(games__final=True),
                distinct=True
            ),
            unique_players_count=Count(
                'games__efforts__player',
                distinct=True
            ),
            annotated_scheduled_count=Count(
                'series__matches',
                filter=Q(
                    bracket_status=Round.BracketStatusChoices.FINALIZED,
                    series__matches__scheduled_time__isnull=False,
                    series__matches__status__in=[CompetitionStatus.PENDING, CompetitionStatus.ACTIVE]
                ),
                distinct=True
            ),
            annotated_pending_count=Count(
                'series__matches',
                filter=Q(
                    bracket_status=Round.BracketStatusChoices.FINALIZED,
                    series__matches__scheduled_time__isnull=True,
                    series__matches__status__in=[CompetitionStatus.PENDING, CompetitionStatus.ACTIVE]
                ),
                distinct=True
            ),
        ).order_by('round_number')
        context['children'] = children
        context['children_type'] = 'rounds'
    else:
        single_round = get_single_round(tournament, stage=stage)
        context['single_round'] = single_round

    context['scheduled_count'] = Match.objects.filter(
        round__stage=stage,
        round__bracket_status=Round.BracketStatusChoices.FINALIZED,
        scheduled_time__isnull=False,
        status__in=[CompetitionStatus.PENDING, CompetitionStatus.ACTIVE]
    ).count()
    context['pending_count'] = Match.objects.filter(
        round__stage=stage,
        round__bracket_status=Round.BracketStatusChoices.FINALIZED,
        scheduled_time__isnull=True,
        status__in=[CompetitionStatus.PENDING, CompetitionStatus.ACTIVE]
    ).count()

    return render(request, 'the_warroom/stage_overview.html', context)


def stage_leaderboard_page(request, tournament_slug, stage_slug):
    tournament = get_object_or_404(Tournament, slug=tournament_slug)
    stage = get_object_or_404(Stage, slug=stage_slug, tournament=tournament)

    opts = Game.with_efforts()
    games_qs = Game.objects.filter(
        round__stage=stage, final=True
    ).select_related(*opts['select']).prefetch_related(*opts['prefetch'])

    filterset = TournamentGameFilter(request.GET, queryset=games_qs, stage=stage)
    filtered_games = filterset.qs

    try:
        leaderboard_threshold = int(request.GET.get("threshold", stage.get_game_threshold()))
    except (TypeError, ValueError):
        leaderboard_threshold = stage.get_game_threshold()
    try:
        leaderboard_places = int(request.GET.get("limit", stage.get_leaderboard_positions()))
    except (TypeError, ValueError):
        leaderboard_places = stage.get_leaderboard_positions()

    efforts = Effort.objects.filter(game__in=filtered_games)

    faction_link = lambda f: reverse('stage-component-leaderboard', kwargs={'tournament_slug': tournament.slug, 'stage_slug': stage.slug, 'post_slug': f.slug})
    player_link = lambda p: reverse('stage-player-leaderboard', kwargs={'tournament_slug': tournament.slug, 'stage_slug': stage.slug, 'profile_slug': p.slug})
    top_players = Profile.leaderboard(limit=leaderboard_places, effort_qs=efforts, game_threshold=leaderboard_threshold, as_json=False, link_builder=player_link)
    most_players = Profile.leaderboard(limit=leaderboard_places, effort_qs=efforts, top_quantity=True, game_threshold=leaderboard_threshold, as_json=False, link_builder=player_link)
    top_factions = Faction.leaderboard(limit=leaderboard_places, effort_qs=efforts, game_threshold=leaderboard_threshold, as_json=False, link_builder=faction_link)
    most_factions = Faction.leaderboard(limit=leaderboard_places, effort_qs=efforts, top_quantity=True, game_threshold=leaderboard_threshold, as_json=False, link_builder=faction_link)

    if hasattr(request, 'htmx') and request.htmx:
        template_name = 'the_warroom/partials/leaderboard_list_home.html'
    else:
        template_name = 'the_warroom/stage_leaderboard.html'

    context = _stage_base_context(request, tournament, stage)
    context.update({
        'active_page': 'leaderboard',
        'top_players': top_players,
        'most_players': most_players,
        'top_factions': top_factions,
        'most_factions': most_factions,
        'has_top_players': bool(top_players),
        'has_most_players': bool(most_players),
        'has_top_factions': bool(top_factions),
        'has_most_factions': bool(most_factions),
        'leaderboard_threshold': leaderboard_threshold,
        'leaderboard_places': leaderboard_places,
        'games_count': filtered_games.count(),
        'form': filterset.form,
        'filterset': filterset,
    })

    return render(request, template_name, context)


def stage_games_page(request, tournament_slug, stage_slug):
    tournament = get_object_or_404(Tournament, slug=tournament_slug)
    stage = get_object_or_404(Stage, slug=stage_slug, tournament=tournament)

    opts = Game.with_efforts()
    games_qs = Game.objects.filter(
        round__stage=stage, final=True
    ).select_related(*opts['select']).prefetch_related(*opts['prefetch']).order_by('-date_posted')

    filterset = TournamentGameFilter(request.GET, queryset=games_qs, stage=stage)
    games = filterset.qs

    paginator = Paginator(games, settings.PAGE_SIZE)
    page_number = request.GET.get('page')
    try:
        page_obj = paginator.get_page(page_number)
    except EmptyPage:
        page_obj = paginator.page(paginator.num_pages)

    if hasattr(request, 'htmx') and request.htmx:
        template_name = 'the_warroom/partials/tournament_game_list.html'
    else:
        template_name = 'the_warroom/stage_games.html'

    context = _stage_base_context(request, tournament, stage)
    context.update({
        'active_page': 'games',
        'games': page_obj,
        'page_obj': page_obj,
        'games_count': paginator.count,
        'form': filterset.form,
        'filterset': filterset,
        'pagination_url': reverse('stage-games-page', args=[tournament.slug, stage.slug]),
    })

    return render(request, template_name, context)


def stage_roster_page(request, tournament_slug, stage_slug):
    tournament = get_object_or_404(Tournament, slug=tournament_slug)
    stage = get_object_or_404(Stage, slug=stage_slug, tournament=tournament)

    players = _stage_roster_queryset(stage)

    paginator = Paginator(players, settings.PAGE_SIZE)
    page_number = request.GET.get('page')
    try:
        page_obj = paginator.get_page(page_number)
    except EmptyPage:
        page_obj = paginator.page(paginator.num_pages)

    if hasattr(request, 'htmx') and request.htmx:
        template_name = 'the_warroom/partials/tournament_roster_rows.html'
    else:
        template_name = 'the_warroom/stage_roster.html'

    context = _stage_base_context(request, tournament, stage)
    context.update({
        'active_page': 'roster',
        'players': page_obj,
        'page_obj': page_obj,
        'players_count': paginator.count,
        'pagination_url': reverse('stage-roster-page', args=[tournament.slug, stage.slug]),
    })

    return render(request, template_name, context)


def stage_details_page(request, tournament_slug, stage_slug):
    tournament = get_object_or_404(Tournament, slug=tournament_slug)
    stage = get_object_or_404(Stage, slug=stage_slug, tournament=tournament)

    context = _stage_base_context(request, tournament, stage)
    context['active_page'] = 'details'

    context['min_players'] = stage.get_min_players_display()
    context['max_players'] = stage.get_max_players_display()

    if tournament.show_assets:
        if tournament.asset_mode != AssetModeChoices.OPEN:
            assets = tournament.get_asset_querysets()
            context['asset_types'] = [
                ('faction', 'Factions', assets['factions'], 'bi-shield'),
                ('map', 'Maps', assets['maps'], 'bi-map'),
                ('deck', 'Decks', assets['decks'], 'bi-stack'),
                ('hireling', 'Hirelings', assets['hirelings'], 'bi-person-badge'),
                ('landmark', 'Landmarks', assets['landmarks'], 'bi-geo-alt'),
                ('tweak', 'House Rules', assets['tweaks'], 'bi-wrench'),
                ('vagabond', 'Vagabonds', assets['vagabonds'], 'bi-person-walking'),
            ]
        else:
            games_qs = Game.objects.filter(round__stage=stage, final=True)
            if games_qs.exists():
                context['asset_types'] = _build_used_asset_types(games_qs)

    return render(request, 'the_warroom/stage_details.html', context)


def stage_bracket_page(request, tournament_slug, stage_slug):
    tournament = get_object_or_404(Tournament, slug=tournament_slug)
    stage = get_object_or_404(Stage, slug=stage_slug, tournament=tournament)

    rounds = stage.rounds.all().order_by('round_number')
    rounds_with_matches = rounds.prefetch_related(
        'matches__series__player_group',
        'matches__series__matches',
        'matches__series__matchseat_set__stage_participant__tournament_player__profile',
        'matches__series__winners__tournament_player__profile',
        'matches__game__efforts__faction',
        'matches__game__efforts__player',
        'series__player_group',
    )
    has_bracket = Match.objects.filter(round__stage=stage).exists()
    context = _stage_base_context(request, tournament, stage)
    context.update({
        'active_page': 'bracket',
        'has_bracket': has_bracket,
        'rounds_with_matches': rounds_with_matches,
    })
    return render(request, 'the_warroom/stage_bracket.html', context)


def _recordable_match_ids(round, user, tournament, view_as=None):
    """Match ids the user may record in this round, and the series they're seated in.

    Returns (recordable_match_ids set, is_participant_series set).

    Gating mirrors the record endpoint: managers can record any match; otherwise a
    seated participant can record their own match when recording_access allows player
    recording (SCHEDULED or REGISTERED); group moderators can always record their
    group's matches. ``view_as`` lets a manager preview this as a lower role -- it
    suppresses the manager short-circuit so the result matches what that role sees
    (and only ever restricts it, never expands it).
    """
    recordable_match_ids = set()
    is_participant_series = set()
    if not user.is_authenticated or view_as in ('logged_in', 'logged_out'):
        return recordable_match_ids, is_participant_series

    profile = user.profile
    is_manager = profile.admin or tournament.has_permission(profile)
    # Previewing as a lower role drops manager privileges.
    if is_manager and view_as in (None, 'moderator'):
        recordable_match_ids = set(
            Match.objects.filter(round=round).values_list('id', flat=True)
        )
    elif tournament.players_can_record_matches():
        participant_series_ids = MatchSeat.objects.filter(
            series__round=round,
            stage_participant__tournament_player__profile=profile
        ).values_list('series_id', flat=True)
        recordable_match_ids = set(
            Match.objects.filter(
                round=round, series_id__in=participant_series_ids
            ).values_list('id', flat=True)
        )
    # Group moderators can always record their group's matches.
    recordable_match_ids |= set(
        Match.objects.filter(
            round=round, series__player_group__group_moderator=profile
        ).values_list('id', flat=True)
    )
    is_participant_series = set(MatchSeat.objects.filter(
        series__round=round,
        stage_participant__tournament_player__profile=profile
    ).values_list('series_id', flat=True))
    return recordable_match_ids, is_participant_series


def _stage_matches_context(request, tournament, stage):
    """Build the context for the scheduled-matches view of a stage's single
    (default) round. Shared by the stage matches page and the tournament
    bracket page when the tournament has no stages and the stage has no rounds."""
    round = stage.rounds.first()
    match_series = []
    recordable_match_ids = set()
    is_participant_series = set()
    is_bracket_finalized = False

    if round:
        is_bracket_finalized = round.bracket_status == Round.BracketStatusChoices.FINALIZED
        match_series = MatchSeries.objects.filter(round=round).select_related(
            'player_group',
        ).prefetch_related(
            'winners__tournament_player__profile',
            'matches__game',
            'matchseat_set__stage_participant__tournament_player__profile',
        ).order_by('id')

        recordable_match_ids, is_participant_series = _recordable_match_ids(
            round, request.user, tournament, view_as=get_view_as(request, tournament)
        )

    context = _stage_base_context(request, tournament, stage)
    context.update({
        'active_page': 'bracket',
        'match_series': match_series,
        'recordable_match_ids': recordable_match_ids,
        'is_participant_series': is_participant_series,
        'is_bracket_finalized': is_bracket_finalized,
        'page_name': stage.name,
        'nav_partial': 'the_warroom/partials/stage_nav_header.html',
    })

    if context.get('can_manage') and is_bracket_finalized and round:
        context.update(_build_series_edit_context(tournament, stage, round))
        context['create_series_url'] = reverse('round-create-series', kwargs={
            'tournament_slug': tournament.slug,
            'stage_slug': stage.slug,
            'round_slug': round.slug,
        })
        context['delete_series_url'] = reverse('round-delete-series', kwargs={
            'tournament_slug': tournament.slug,
            'stage_slug': stage.slug,
            'round_slug': round.slug,
        })

    return context


def stage_matches_page(request, tournament_slug, stage_slug):
    tournament = get_object_or_404(Tournament, slug=tournament_slug)
    stage = get_object_or_404(Stage, slug=stage_slug, tournament=tournament)

    context = _stage_matches_context(request, tournament, stage)
    return render(request, 'the_warroom/matches.html', context)


# ── Round tab pages ──────────────────────────────────────────────────────────

def round_leaderboard_page(request, tournament_slug, round_slug, stage_slug=None):
    from the_warroom.utils import get_single_stage

    tournament = get_object_or_404(Tournament, slug=tournament_slug)

    if stage_slug:
        stage = get_object_or_404(Stage, slug=stage_slug, tournament=tournament)
        round = get_object_or_404(Round, slug=round_slug, stage=stage)
    else:
        stage = get_single_stage(tournament)
        if not stage:
            return redirect(tournament.get_absolute_url())
        round = get_object_or_404(Round, slug=round_slug, stage=stage)

    opts = Game.with_efforts()
    games_qs = Game.objects.filter(
        round=round, final=True
    ).select_related(*opts['select']).prefetch_related(*opts['prefetch'])

    filterset = TournamentGameFilter(request.GET, queryset=games_qs, round=round)
    filtered_games = filterset.qs

    try:
        leaderboard_threshold = int(request.GET.get("threshold", round.get_game_threshold()))
    except (TypeError, ValueError):
        leaderboard_threshold = round.get_game_threshold()
    try:
        leaderboard_places = int(request.GET.get("limit", round.get_leaderboard_positions()))
    except (TypeError, ValueError):
        leaderboard_places = round.get_leaderboard_positions()

    efforts = Effort.objects.filter(game__in=filtered_games)

    faction_link = lambda f: reverse('round-component-leaderboard', kwargs={'tournament_slug': tournament.slug, 'stage_slug': stage.slug, 'round_slug': round.slug, 'post_slug': f.slug})
    player_link = lambda p: reverse('round-player-leaderboard', kwargs={'tournament_slug': tournament.slug, 'stage_slug': stage.slug, 'round_slug': round.slug, 'profile_slug': p.slug})
    top_players = Profile.leaderboard(limit=leaderboard_places, effort_qs=efforts, game_threshold=leaderboard_threshold, as_json=False, link_builder=player_link)
    most_players = Profile.leaderboard(limit=leaderboard_places, effort_qs=efforts, top_quantity=True, game_threshold=leaderboard_threshold, as_json=False, link_builder=player_link)
    top_factions = Faction.leaderboard(limit=leaderboard_places, effort_qs=efforts, game_threshold=leaderboard_threshold, as_json=False, link_builder=faction_link)
    most_factions = Faction.leaderboard(limit=leaderboard_places, effort_qs=efforts, top_quantity=True, game_threshold=leaderboard_threshold, as_json=False, link_builder=faction_link)

    if hasattr(request, 'htmx') and request.htmx:
        template_name = 'the_warroom/partials/leaderboard_list_home.html'
    else:
        template_name = 'the_warroom/round_leaderboard.html'

    context = _round_base_context(request, tournament, stage, round)
    context.update({
        'active_page': 'leaderboard',
        'top_players': top_players,
        'most_players': most_players,
        'top_factions': top_factions,
        'most_factions': most_factions,
        'has_top_players': bool(top_players),
        'has_most_players': bool(most_players),
        'has_top_factions': bool(top_factions),
        'has_most_factions': bool(most_factions),
        'leaderboard_threshold': leaderboard_threshold,
        'leaderboard_places': leaderboard_places,
        'games_count': filtered_games.count(),
        'form': filterset.form,
        'filterset': filterset,
    })

    return render(request, template_name, context)


def round_games_page(request, tournament_slug, round_slug, stage_slug=None):
    from the_warroom.utils import get_single_stage

    tournament = get_object_or_404(Tournament, slug=tournament_slug)

    if stage_slug:
        stage = get_object_or_404(Stage, slug=stage_slug, tournament=tournament)
        round = get_object_or_404(Round, slug=round_slug, stage=stage)
    else:
        stage = get_single_stage(tournament)
        if not stage:
            return redirect(tournament.get_absolute_url())
        round = get_object_or_404(Round, slug=round_slug, stage=stage)

    opts = Game.with_efforts()
    games_qs = Game.objects.filter(
        round=round, final=True
    ).select_related(*opts['select']).prefetch_related(*opts['prefetch']).order_by('-date_posted')

    filterset = TournamentGameFilter(request.GET, queryset=games_qs, round=round)
    games = filterset.qs

    paginator = Paginator(games, settings.PAGE_SIZE)
    page_number = request.GET.get('page')
    try:
        page_obj = paginator.get_page(page_number)
    except EmptyPage:
        page_obj = paginator.page(paginator.num_pages)

    if hasattr(request, 'htmx') and request.htmx:
        template_name = 'the_warroom/partials/tournament_game_list.html'
    else:
        template_name = 'the_warroom/round_games.html'

    context = _round_base_context(request, tournament, stage, round)
    context.update({
        'active_page': 'games',
        'games': page_obj,
        'page_obj': page_obj,
        'games_count': paginator.count,
        'form': filterset.form,
        'filterset': filterset,
        'pagination_url': reverse('round-games-page', args=[tournament.slug, stage.slug, round.slug]),
    })

    return render(request, template_name, context)


def round_roster_page(request, tournament_slug, round_slug, stage_slug=None):
    from the_warroom.utils import get_single_stage

    tournament = get_object_or_404(Tournament, slug=tournament_slug)

    if stage_slug:
        stage = get_object_or_404(Stage, slug=stage_slug, tournament=tournament)
        round = get_object_or_404(Round, slug=round_slug, stage=stage)
    else:
        stage = get_single_stage(tournament)
        if not stage:
            return redirect(tournament.get_absolute_url())
        round = get_object_or_404(Round, slug=round_slug, stage=stage)

    players = _round_roster_queryset(round)

    paginator = Paginator(players, settings.PAGE_SIZE)
    page_number = request.GET.get('page')
    try:
        page_obj = paginator.get_page(page_number)
    except EmptyPage:
        page_obj = paginator.page(paginator.num_pages)

    if hasattr(request, 'htmx') and request.htmx:
        template_name = 'the_warroom/partials/tournament_roster_rows.html'
    else:
        template_name = 'the_warroom/round_roster.html'

    context = _round_base_context(request, tournament, stage, round)
    context.update({
        'active_page': 'roster',
        'players': page_obj,
        'page_obj': page_obj,
        'players_count': paginator.count,
        'pagination_url': reverse('round-roster-page', args=[tournament.slug, stage.slug, round.slug]),
    })

    return render(request, template_name, context)


def round_details_page(request, tournament_slug, round_slug, stage_slug=None):
    from the_warroom.utils import get_single_stage

    tournament = get_object_or_404(Tournament, slug=tournament_slug)

    if stage_slug:
        stage = get_object_or_404(Stage, slug=stage_slug, tournament=tournament)
        round = get_object_or_404(Round, slug=round_slug, stage=stage)
    else:
        stage = get_single_stage(tournament)
        if not stage:
            return redirect(tournament.get_absolute_url())
        round = get_object_or_404(Round, slug=round_slug, stage=stage)

    context = _round_base_context(request, tournament, stage, round)
    context['active_page'] = 'details'

    context['min_players'] = round.get_min_players_display()
    context['max_players'] = round.get_max_players_display()

    if tournament.show_assets:
        if tournament.asset_mode != AssetModeChoices.OPEN:
            assets = tournament.get_asset_querysets()
            context['asset_types'] = [
                ('faction', 'Factions', assets['factions'], 'bi-shield'),
                ('map', 'Maps', assets['maps'], 'bi-map'),
                ('deck', 'Decks', assets['decks'], 'bi-stack'),
                ('hireling', 'Hirelings', assets['hirelings'], 'bi-person-badge'),
                ('landmark', 'Landmarks', assets['landmarks'], 'bi-geo-alt'),
                ('tweak', 'House Rules', assets['tweaks'], 'bi-wrench'),
                ('vagabond', 'Vagabonds', assets['vagabonds'], 'bi-person-walking'),
            ]
        else:
            games_qs = Game.objects.filter(round=round, final=True)
            if games_qs.exists():
                context['asset_types'] = _build_used_asset_types(games_qs)

    return render(request, 'the_warroom/round_details.html', context)


def _attach_series_effort_grid(series):
    """Attach per-player faction-icon data to a prefetched MatchSeries.

    For each game in the series (match_number order), a player's effort contributes
    one faction icon. The icons are shown to the right of each player's row.

    Sets:
      seat.effort_cells (on each MatchSeat) -> [(game_url, effort_or_None), ...]
        aligned to the series' games, for the participant in that seat.
      series.extra_player_rows -> [{'profile', 'cells': [...]}, ...]
        for players who have an effort in a linked game but are NOT a listed
        participant (no seat); rendered after the seat list, fainter.

    Reads only prefetched data (matches__game__efforts__faction/player,
    matchseat_set__...__profile) so it adds no queries and works in both the
    full-page and edit re-render paths.
    """
    games = [m.game for m in series.matches.all() if m.game]   # match_number order = game order

    # per game: player_id -> effort (faction efforts only)
    per_game_efforts = [
        {e.player_id: e for e in game.efforts.all() if e.player_id and e.faction_id}
        for game in games
    ]

    def cells_for(profile_id):
        return [
            (game.get_absolute_url(), per_game_efforts[i].get(profile_id))
            for i, game in enumerate(games)
        ]

    seen = set()
    for seat in series.matchseat_set.all():
        profile_id = seat.stage_participant.tournament_player.profile_id
        seen.add(profile_id)
        seat.effort_cells = cells_for(profile_id)

    # non-participant effort players (have an effort but no seat), in game then seat order
    extra_rows = []
    for game in games:
        for effort in game.efforts.all():
            p = effort.player
            if p and effort.faction_id and p.id not in seen:
                seen.add(p.id)
                extra_rows.append({'profile': p, 'cells': cells_for(p.id)})
    series.extra_player_rows = extra_rows
    series.num_games = len(games)
    series.has_effort_grid = bool(games)


def round_matches_page(request, tournament_slug, round_slug, stage_slug=None):
    from the_warroom.utils import get_single_stage

    tournament = get_object_or_404(Tournament, slug=tournament_slug)

    if stage_slug:
        stage = get_object_or_404(Stage, slug=stage_slug, tournament=tournament)
        round = get_object_or_404(Round, slug=round_slug, stage=stage)
    else:
        stage = get_single_stage(tournament)
        if not stage:
            return redirect(tournament.get_absolute_url())
        round = get_object_or_404(Round, slug=round_slug, stage=stage)

    match_series = MatchSeries.objects.filter(round=round).select_related(
        'player_group',
    ).prefetch_related(
        'winners__tournament_player__profile',
        'matches__game',
        'matches__game__efforts__faction',
        'matches__game__efforts__player',
        'matchseat_set__stage_participant__tournament_player__profile',
    ).order_by('id')

    for series in match_series:
        _attach_series_effort_grid(series)

    recordable_match_ids, is_participant_series = _recordable_match_ids(
        round, request.user, tournament, view_as=get_view_as(request, tournament)
    )

    context = _round_base_context(request, tournament, stage, round)
    context.update({
        'active_page': 'bracket',
        'match_series': match_series,
        'recordable_match_ids': recordable_match_ids,
        'is_participant_series': is_participant_series,
    })

    # Add series edit modal context for managers
    if context.get('can_manage') and context.get('is_bracket_finalized'):
        context.update(_build_series_edit_context(tournament, stage, round))
        context['create_series_url'] = reverse('round-create-series', kwargs={
            'tournament_slug': tournament.slug,
            'stage_slug': stage.slug,
            'round_slug': round.slug,
        })
        context['delete_series_url'] = reverse('round-delete-series', kwargs={
            'tournament_slug': tournament.slug,
            'stage_slug': stage.slug,
            'round_slug': round.slug,
        })

    context.update({
        'page_name': round.name,
        'nav_partial': 'the_warroom/partials/round_nav_header.html',
    })
    return render(request, 'the_warroom/matches.html', context)


@player_onboard_required
def stage_settings_hub(request, tournament_slug, stage_slug):
    """Settings hub page with links to all stage management tools."""
    tournament = get_object_or_404(Tournament, slug=tournament_slug)
    stage = get_object_or_404(Stage, slug=stage_slug, tournament=tournament)

    if not tournament.has_permission(request.user.profile):
        raise PermissionDenied()

    profile = request.user.profile
    is_owner = profile.admin or profile == tournament.designer

    view_as_ctx, no_access, is_owner = settings_view_as_context(request, tournament, is_owner)

    sections = [] if no_access else [
        _settings_level_context('stage', tournament, stage=stage, is_owner=is_owner),
        _settings_level_context('series', tournament, is_owner=is_owner),
    ]
    _mark_deepest_schedule_section(sections)

    context = {
        'object_type': 'Stage',
        'object': stage,
        'tournament': tournament,
        'stage': stage,
        'sections': sections,
        'status_hierarchy': build_status_hierarchy('stage', tournament, stage=stage),
        **view_as_ctx,
    }
    return render(request, 'the_warroom/settings_hub.html', context)


@login_required
def stage_grouping_setup_view(request, tournament_slug, stage_slug):
    """Grouping entry point for stages with use_rounds=False."""
    tournament = get_object_or_404(Tournament, slug=tournament_slug)
    stage = get_object_or_404(Stage, slug=stage_slug, tournament=tournament)

    if not tournament.has_permission(request.user.profile):
        raise PermissionDenied()

    if stage.use_rounds:
        return redirect('stage-settings-hub', tournament_slug=tournament_slug, stage_slug=stage_slug)

    round = stage.rounds.first()
    if not round:
        return redirect('stage-settings-hub', tournament_slug=tournament_slug, stage_slug=stage_slug)

    return round_grouping_setup_view(request, tournament_slug, round.slug, stage_slug=stage_slug)


@player_onboard_required
def stage_manage_players(request, tournament_slug, stage_slug):
    """Stage player management — manage StageParticipants."""
    tournament = get_object_or_404(Tournament, slug=tournament_slug)
    stage = get_object_or_404(Stage, slug=stage_slug, tournament=tournament)

    if not tournament.has_permission(request.user.profile):
        raise PermissionDenied()

    participants = StageParticipant.objects.filter(
        stage=stage, status=StageParticipant.ParticipantStatus.ACTIVE
    ).select_related('tournament_player__profile')
    eliminated_participants = StageParticipant.objects.filter(
        stage=stage, status=StageParticipant.ParticipantStatus.ELIMINATED
    ).select_related('tournament_player__profile')
    withdrawn_participants = StageParticipant.objects.filter(
        stage=stage, status=StageParticipant.ParticipantStatus.WITHDRAWN
    ).select_related('tournament_player__profile')
    advanced_participants = StageParticipant.objects.filter(
        stage=stage, status=StageParticipant.ParticipantStatus.ADVANCED
    ).select_related('tournament_player__profile')

    context = {
        'tournament': tournament,
        'stage': stage,
        'object': stage,
        'participants': participants,
        'eliminated_participants': eliminated_participants,
        'withdrawn_participants': withdrawn_participants,
        'advanced_participants': advanced_participants,
    }
    return render(request, 'the_warroom/stage_manage_players.html', context)


@player_required
def stage_search_players(request, tournament_slug, stage_slug):
    """HTMX endpoint: Search for tournament players not yet in this stage."""
    tournament = get_object_or_404(Tournament, slug=tournament_slug)
    stage = get_object_or_404(Stage, slug=stage_slug, tournament=tournament)

    if not tournament.has_permission(request.user.profile):
        return HttpResponse("Permission denied", status=403)

    query = request.GET.get('q', '')

    # Get tournament players not already a StageParticipant in this stage
    existing_participant_profile_ids = StageParticipant.objects.filter(
        stage=stage
    ).values_list('tournament_player__profile_id', flat=True)

    status_order = Case(
        When(tournament_participations__status=TournamentPlayer.StatusChoices.REGISTERED, then=Value(1)),
        When(tournament_participations__status=TournamentPlayer.StatusChoices.WAITLIST, then=Value(2)),
        When(tournament_participations__status=TournamentPlayer.StatusChoices.ELIMINATED, then=Value(3)),
        default=Value(4),
        output_field=IntegerField(),
    )
    available_players = Profile.objects.filter(
        tournament_participations__tournament=tournament,
    ).exclude(id__in=existing_participant_profile_ids).annotate(
        tournament_status=F('tournament_participations__status'),
        status_order=status_order,
    ).order_by('status_order', 'display_name')

    if query:
        available_players = available_players.filter(
            Q(discord__icontains=query) |
            Q(display_name__icontains=query)
        )

    available_players = available_players[:50]

    unregistered_players = []
    if query:
        unregistered_players = Profile.objects.exclude(
            tournament_participations__tournament=tournament
        ).filter(
            Q(discord__icontains=query) | Q(display_name__icontains=query)
        )[:5]

    # Check if any profile exists with an exact name match
    has_exact_match = False
    if query:
        has_exact_match = Profile.objects.filter(
            Q(discord__iexact=query) | Q(display_name__iexact=query)
        ).exists()

    return render(request, 'the_warroom/partials/player_search_results.html', {
        'players': available_players,
        'unregistered_players': unregistered_players,
        'tournament': tournament,
        'stage': stage,
        'has_exact_match': has_exact_match,
    })


@player_required
def stage_move_player(request, tournament_slug, stage_slug):
    """JavaScript/AJAX endpoint: Move a player between stage participant groups."""
    if request.method != 'POST':
        return HttpResponse("Method not allowed", status=405)

    tournament = get_object_or_404(Tournament, slug=tournament_slug)
    stage = get_object_or_404(Stage, slug=stage_slug, tournament=tournament)

    if not tournament.has_permission(request.user.profile):
        return HttpResponse("Permission denied", status=403)

    data = json.loads(request.body)
    player_id = data.get('player_id')
    from_group = data.get('from_group')
    to_group = data.get('to_group')
    unregistered = data.get('unregistered', False)

    player = get_object_or_404(Profile, id=player_id)

    status_map = {
        'players': StageParticipant.ParticipantStatus.ACTIVE,
        'eliminated': StageParticipant.ParticipantStatus.ELIMINATED,
        'withdrawn': StageParticipant.ParticipantStatus.WITHDRAWN,
        'advanced': StageParticipant.ParticipantStatus.ADVANCED,
    }

    if not from_group:
        if unregistered:
            # Player not in tournament — create TournamentPlayer (REGISTERED) + StageParticipant (ACTIVE)
            stage.add_player(player)
            return render(request, 'the_warroom/partials/stage_player_card.html', {
                'player': player,
                'tournament': tournament,
                'stage': stage,
                'group': 'players',
            })

        # Adding from search — mirror TournamentPlayer status into StageParticipant
        tournament_player = get_object_or_404(TournamentPlayer, tournament=tournament, profile=player)
        tournament_status_map = {
            TournamentPlayer.StatusChoices.REGISTERED: StageParticipant.ParticipantStatus.ACTIVE,
            TournamentPlayer.StatusChoices.WAITLIST: StageParticipant.ParticipantStatus.ACTIVE,
            TournamentPlayer.StatusChoices.ELIMINATED: StageParticipant.ParticipantStatus.ELIMINATED,
        }
        to_status = tournament_status_map.get(tournament_player.status, StageParticipant.ParticipantStatus.ACTIVE)
        sp, _ = StageParticipant.objects.get_or_create(
            stage=stage,
            tournament_player=tournament_player,
            defaults={'status': to_status}
        )
        # to_group drives which column the card renders into in the template
        to_group = {
            StageParticipant.ParticipantStatus.ACTIVE: 'players',
            StageParticipant.ParticipantStatus.ELIMINATED: 'eliminated',
        }.get(to_status, 'players')
    elif not to_group:
        # Remove from stage entirely
        tournament_player = get_object_or_404(TournamentPlayer, tournament=tournament, profile=player)
        StageParticipant.objects.filter(stage=stage, tournament_player=tournament_player).delete()
        return HttpResponse('')
    else:
        # Status transition
        tournament_player = get_object_or_404(TournamentPlayer, tournament=tournament, profile=player)
        to_status = status_map.get(to_group)
        StageParticipant.objects.filter(
            stage=stage, tournament_player=tournament_player
        ).update(status=to_status)

    return render(request, 'the_warroom/partials/stage_player_card.html', {
        'player': player,
        'tournament': tournament,
        'stage': stage,
        'group': to_group,
    })


def tournament_bracket_page(request, slug):
    """Read-only bracket overview — shows all stages and their bracket status.

    When the tournament has no stages and its single default stage has no
    rounds, there is nothing to lay out as a bracket — instead show the fuller,
    editable scheduled-matches view (the same one the stage matches page uses)
    so moderators can add and edit matches directly."""
    tournament = get_object_or_404(Tournament, slug=slug)

    if not tournament.use_stages:
        single_stage = get_single_stage(tournament)
        if single_stage and not single_stage.use_rounds:
            context = _stage_matches_context(request, tournament, single_stage)
            context.update({
                'active_page': 'bracket',
                'nav_partial': 'the_warroom/partials/tournament_nav_header.html',
                'breadcrumb_page': _('Bracket'),
                # This is the tournament-level bracket URL; the single stage is
                # hidden from the user, so show the series name, not the stage's.
                'page_name': tournament.name,
            })
            return render(request, 'the_warroom/matches.html', context)

    stages = tournament.stages.order_by('order').prefetch_related(
        Prefetch(
            'rounds',
            queryset=Round.objects.order_by('round_number').prefetch_related(
                'matches__series__player_group',
                'matches__series__matches',
                'matches__series__matchseat_set__stage_participant__tournament_player__profile',
                'matches__series__winners__tournament_player__profile',
                'matches__game__efforts__faction',
                'matches__game__efforts__player',
            ),
        ),
    )

    context = _tournament_base_context(request, tournament)
    context.update({
        'active_page': 'bracket',
        'stages': stages,
    })
    return render(request, 'the_warroom/tournament_bracket.html', context)


# Round Grouping Views

@login_required
def round_grouping_setup_view(request, tournament_slug, round_slug, stage_slug=None):
    """
    Round grouping view - shows setup form and organize interface.
    Each round is linked to a Stage which holds the grouping configuration.
    """
    from the_warroom.utils import get_single_stage

    tournament = get_object_or_404(Tournament, slug=tournament_slug, classification=Tournament.ClassificationTypes.TOURNAMENT)

    if stage_slug:
        stage = get_object_or_404(Stage, slug=stage_slug, tournament=tournament)
        round = get_object_or_404(Round, slug=round_slug, stage=stage)
    else:
        stage = get_single_stage(tournament)
        if not stage:
            return redirect(tournament.get_absolute_url())
        round = get_object_or_404(Round, slug=round_slug, stage=stage)
    profile = request.user.profile

    # Only tournament creator or admin can access grouping
    if not tournament.has_permission(profile):
        raise PermissionDenied

    # Get the stage for this round (grouping config lives on Stage)
    stage = round.stage

    # Get active players via StageParticipants (mirrors grouping service logic)
    if stage:
        active_tp_ids = StageParticipant.objects.filter(
            stage=stage,
            status=StageParticipant.ParticipantStatus.ACTIVE
        ).values_list('tournament_player_id', flat=True)
        active_players_qs = TournamentPlayer.objects.filter(id__in=active_tp_ids)
    else:
        active_players_qs = TournamentPlayer.objects.filter(
            tournament=tournament,
            status=TournamentPlayer.StatusChoices.REGISTERED
        )

    # Check for availability data on active players
    has_availability = active_players_qs.exclude(availability_hours=[]).exists()

    # Calculate stats
    total_players = active_players_qs.count()
    group_count = round.player_groups.count()

    # Handle POST actions
    if request.method == 'POST':
        action = request.POST.get('action', 'generate')

        # Reset session - clear bracket data and groups for this round
        if action == 'reset_session':
            if round.grouping_status == Round.GroupingStatusChoices.FINALIZED:
                messages.error(request, 'Cannot reset a finalized grouping.')
                return redirect('round-grouping-setup', tournament_slug=tournament.slug, stage_slug=stage.slug, round_slug=round.slug)
            Match.objects.filter(round=round).delete()
            MatchSeries.objects.filter(round=round).delete()
            round.player_groups.all().delete()
            round.grouping_status = Round.GroupingStatusChoices.DRAFT
            round.grouping_notes = ''
            round.bracket_status = Round.BracketStatusChoices.DRAFT
            round.save(update_fields=['grouping_status', 'grouping_notes', 'bracket_status'])
            messages.success(request, 'Groups reset successfully.')
            return redirect('round-grouping-setup', tournament_slug=tournament.slug, stage_slug=stage.slug, round_slug=round.slug)

        # Skip grouping - finalize grouping + bracket with no groups, go straight to manual matches
        elif action == 'skip_grouping':
            if (round.grouping_status == Round.GroupingStatusChoices.FINALIZED
                    or round.bracket_status == Round.BracketStatusChoices.FINALIZED):
                messages.error(request, 'Cannot skip grouping once grouping or the bracket is finalized.')
                return redirect('round-grouping-setup', tournament_slug=tournament.slug, stage_slug=stage.slug, round_slug=round.slug)
            # Clear any draft groups/series/matches so we start clean
            Match.objects.filter(round=round).delete()
            MatchSeries.objects.filter(round=round).delete()
            round.player_groups.all().delete()
            round.grouping_status = Round.GroupingStatusChoices.FINALIZED
            round.grouping_notes = ''
            round.bracket_status = Round.BracketStatusChoices.FINALIZED
            round.save(update_fields=['grouping_status', 'grouping_notes', 'bracket_status'])
            messages.success(request, 'Grouping skipped. You can now add matches manually.')
            return redirect('round-matches-page', tournament_slug=tournament.slug, stage_slug=stage.slug, round_slug=round.slug)

        # Generate/regenerate groups with new settings
        elif action == 'generate':
            grouping_type = request.POST.get('grouping_type', 'random')
            naming_convention = request.POST.get('naming_convention', 'numeric')

            # Can't regenerate if finalized
            if round.grouping_status == Round.GroupingStatusChoices.FINALIZED:
                messages.error(request, 'Cannot modify a finalized grouping. Reset it first.')
                return redirect('round-grouping-setup', tournament_slug=tournament.slug, stage_slug=stage.slug, round_slug=round.slug)

            # Update stage grouping config if a stage exists
            if stage:
                stage.grouping_type = grouping_type
                stage.naming_convention = naming_convention
                stage.save(update_fields=['grouping_type', 'naming_convention'])

            # Clear existing groups for this round only
            round.player_groups.all().delete()
            round.grouping_status = Round.GroupingStatusChoices.PROCESSING
            round.grouping_notes = ''
            round.save(update_fields=['grouping_status', 'grouping_notes'])

            # Queue grouping based on type
            if grouping_type == 'availability' and stage:
                from the_tavern.tasks import generate_grouping_async
                generate_grouping_async.delay(stage.id, round.id)
            elif grouping_type == 'random' and stage:
                GroupingService.generate_random_groups(stage, round)
                round.grouping_status = Round.GroupingStatusChoices.DRAFT
                round.save(update_fields=['grouping_status'])
            elif grouping_type == 'swiss' and stage:
                GroupingService.generate_swiss_groups(stage, round)
                round.grouping_status = Round.GroupingStatusChoices.DRAFT
                round.save(update_fields=['grouping_status'])
            elif grouping_type == 'manual' and stage:
                GroupingService.generate_manual_groups(stage, round)
                round.grouping_status = Round.GroupingStatusChoices.DRAFT
                round.save(update_fields=['grouping_status'])
            else:
                round.grouping_status = Round.GroupingStatusChoices.DRAFT
                round.save(update_fields=['grouping_status'])

            return redirect('round-grouping-setup', tournament_slug=tournament.slug, stage_slug=stage.slug, round_slug=round.slug)

    # Build context for template
    groups = []
    ungrouped_players = []
    is_processing = round.grouping_status == Round.GroupingStatusChoices.PROCESSING
    is_finalized = round.grouping_status == Round.GroupingStatusChoices.FINALIZED
    has_error = round.grouping_status == Round.GroupingStatusChoices.ERROR

    if not is_processing:
        groups = round.player_groups.prefetch_related(
            'tournament_players__profile'
        ).order_by('group_number')

        # Annotate each group with conflict_count and conflict_description (repeat matchups from prior rounds)
        if stage:
            _history = build_opponent_history(stage, round)
            groups = list(groups)
            # Build a flat id→display_name map across all groups (prefetch already loaded)
            _all_tps = {tp.id: tp.profile.display_name for _group in groups for tp in _group.tournament_players.all()}
            for _group in groups:
                _members = list(_group.tournament_players.values_list('id', flat=True))
                _count = 0
                _pairs = []
                for _i, _a in enumerate(_members):
                    for _b in _members[_i + 1:]:
                        _times = _history.get(_a, {}).get(_b, 0)
                        if _times > 0:
                            _count += _times
                            _name_a = _all_tps.get(_a, str(_a))
                            _name_b = _all_tps.get(_b, str(_b))
                            _pairs.append(f"{_name_a} & {_name_b}")
                _group.conflict_count = _count
                _group.conflict_description = ', '.join(_pairs)
        else:
            groups = list(groups)
            for _group in groups:
                _group.conflict_count = 0
                _group.conflict_description = ''

        # Annotate groups with has_series for edit modal match-clearing warnings
        _groups_with_series = set(
            MatchSeries.objects.filter(
                player_group__in=[g.id for g in groups]
            ).values_list('player_group_id', flat=True)
        )
        for _group in groups:
            _group.has_series = _group.id in _groups_with_series

        grouped_ids = set(
            TournamentPlayer.objects.filter(
                player_groups__round=round
            ).values_list('id', flat=True)
        )
        ungrouped_players = active_players_qs.exclude(id__in=grouped_ids).select_related('profile')

    context = {
        'tournament': tournament,
        'round': round,
        'stage': stage,
        'groups': groups,
        'ungrouped_players': ungrouped_players,
        'ungrouped_count': ungrouped_players.count() if hasattr(ungrouped_players, 'filter') else len(ungrouped_players),
        'total_players': total_players,
        'group_count': group_count,
        'has_availability': has_availability,
        'show_availability_options': has_availability,
        'is_swiss': stage and stage.grouping_type == Stage.GroupingTypeChoices.SWISS,
        'is_processing': is_processing,
        'is_finalized': is_finalized,
        'has_error': has_error,
        'naming_conventions': NameConvention.choices,
        'base_url': reverse('round-grouping-setup', kwargs={
            'tournament_slug': tournament.slug,
            'stage_slug': stage.slug,
            'round_slug': round.slug,
        }) if stage else '',
        # Bracket config context
        'round_format': round.get_format(),
        'has_matches': round.matches.exists(),
        'match_count': round.matches.count(),
        'bracket_series': MatchSeries.objects.filter(round=round).select_related('player_group').prefetch_related('matchseat_set__stage_participant__tournament_player__profile').order_by('id') if is_finalized else [],
        'other_stages': tournament.stages.exclude(id=stage.id).order_by('order') if stage else Stage.objects.none(),
        'best_of_choices': [1, 3, 5, 7],
        'bracket_url': reverse('round-generate-bracket', kwargs={
            'tournament_slug': tournament.slug,
            'stage_slug': stage.slug,
            'round_slug': round.slug,
        }) if stage else '',
        'is_bracket_finalized': round.bracket_status == Round.BracketStatusChoices.FINALIZED,
        'bracket_finalize_url': reverse('round-finalize-bracket', kwargs={
            'tournament_slug': tournament.slug,
            'stage_slug': stage.slug,
            'round_slug': round.slug,
        }) if stage else '',
    }
    # Add series edit modal context when bracket is finalized
    if round.bracket_status == Round.BracketStatusChoices.FINALIZED and stage:
        context.update(_build_series_edit_context(tournament, stage, round))
        context['create_series_url'] = reverse('round-create-series', kwargs={
            'tournament_slug': tournament.slug,
            'stage_slug': stage.slug,
            'round_slug': round.slug,
        })
        context['delete_series_url'] = reverse('round-delete-series', kwargs={
            'tournament_slug': tournament.slug,
            'stage_slug': stage.slug,
            'round_slug': round.slug,
        })
    return render(request, 'the_warroom/round_grouping.html', context)


# Round Grouping AJAX Views

@login_required
@require_http_methods(['GET'])
def round_grouping_status(request, tournament_slug, stage_slug, round_slug, session_id):
    """Check session status (for HTMX polling)."""
    tournament = get_object_or_404(Tournament, slug=tournament_slug, classification=Tournament.ClassificationTypes.TOURNAMENT)
    stage = get_object_or_404(Stage, slug=stage_slug, tournament=tournament)
    round = get_object_or_404(Round, slug=round_slug, stage=stage)
    profile = request.user.profile

    if not tournament.has_permission(profile):
        raise PermissionDenied

    stage = get_object_or_404(Stage, id=session_id)

    # If still processing, return empty 200 — hx-swap="none" means the spinner is untouched
    if round.grouping_status == Round.GroupingStatusChoices.PROCESSING:
        return HttpResponse(status=200)

    # If done, trigger a full page refresh via HX-Refresh header
    response = HttpResponse(status=200)
    response['HX-Refresh'] = 'true'
    return response


@login_required
@require_http_methods(['POST'])
def round_grouping_reset(request, tournament_slug, stage_slug, round_slug, session_id):
    """Reset a stuck/errored grouping status back to DRAFT."""
    tournament = get_object_or_404(Tournament, slug=tournament_slug, classification=Tournament.ClassificationTypes.TOURNAMENT)
    stage = get_object_or_404(Stage, slug=stage_slug, tournament=tournament)
    round = get_object_or_404(Round, slug=round_slug, stage=stage)
    profile = request.user.profile

    if not tournament.has_permission(profile):
        raise PermissionDenied

    if round.grouping_status in (Round.GroupingStatusChoices.PROCESSING, Round.GroupingStatusChoices.ERROR):
        round.grouping_status = Round.GroupingStatusChoices.DRAFT
        round.grouping_notes = ''
        round.save(update_fields=['grouping_status', 'grouping_notes'])

    return HttpResponseRedirect(request.META.get('HTTP_REFERER', request.path))


@login_required
@require_http_methods(['POST'])
def round_grouping_move_player(request, tournament_slug, stage_slug, round_slug, session_id):
    """Move a player from one group to another."""
    from django.http import JsonResponse

    tournament = get_object_or_404(Tournament, slug=tournament_slug, classification=Tournament.ClassificationTypes.TOURNAMENT)
    stage = get_object_or_404(Stage, slug=stage_slug, tournament=tournament)
    round = get_object_or_404(Round, slug=round_slug, stage=stage)
    profile = request.user.profile

    if not tournament.has_permission(profile):
        raise PermissionDenied

    stage = get_object_or_404(Stage, id=session_id)

    if round.grouping_status != Round.GroupingStatusChoices.DRAFT:
        return JsonResponse({'error': 'Round grouping is not editable'}, status=400)

    try:
        data = json.loads(request.body)
        player_id = data.get('player_id')
        from_group_id = data.get('from_group_id')
        to_group_id = data.get('to_group_id')

        tournament_player = get_object_or_404(TournamentPlayer, id=player_id, tournament=stage.tournament)
        from_group = get_object_or_404(PlayerGroup, id=from_group_id, round=round) if from_group_id else None
        to_group = get_object_or_404(PlayerGroup, id=to_group_id, round=round)

        GroupingService.assign_player_to_group(tournament_player, to_group, round=round, moved_by=profile)

        response = {'success': True}
        if from_group:
            from_group.refresh_from_db()
            response['from_group'] = {
                'id': from_group.id,
                'member_count': from_group.member_count,
                'total_overlap_hours': from_group.total_overlap_hours,
                'best_consecutive_block': from_group.best_consecutive_block,
                'all_hours': from_group.all_hours or [],
                'overlap_hours': from_group.overlap_hours or [],
            }
        to_group.refresh_from_db()
        response['to_group'] = {
            'id': to_group.id,
            'member_count': to_group.member_count,
            'total_overlap_hours': to_group.total_overlap_hours,
            'best_consecutive_block': to_group.best_consecutive_block,
            'all_hours': to_group.all_hours or [],
            'overlap_hours': to_group.overlap_hours or [],
        }
        return JsonResponse(response)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=400)


def _get_group_data(group, history):
    """Build group stats dict including conflict data from opponent history."""
    group.refresh_from_db()
    members = list(group.tournament_players.select_related('profile').all())
    member_ids = [tp.id for tp in members]
    conflict_count = 0
    conflict_pairs = []
    for i, a in enumerate(member_ids):
        for b in member_ids[i + 1:]:
            times = history.get(a, {}).get(b, 0)
            if times > 0:
                conflict_count += times
                name_a = next(tp.profile.display_name for tp in members if tp.id == a)
                name_b = next(tp.profile.display_name for tp in members if tp.id == b)
                conflict_pairs.append(f"{name_a} & {name_b}")
    return {
        'id': group.id,
        'name': group.name,
        'discord_thread': group.discord_thread,
        'video_link': group.video_link,
        'member_count': len(members),
        'total_overlap_hours': group.total_overlap_hours,
        'best_consecutive_block': group.best_consecutive_block,
        'all_hours': group.all_hours or [],
        'overlap_hours': group.overlap_hours or [],
        'conflict_count': conflict_count,
        'conflict_description': ', '.join(conflict_pairs),
    }


def _get_ungrouped_players(stage, round):
    """Return ungrouped active players queryset for a round."""
    if stage:
        active_tp_ids = StageParticipant.objects.filter(
            stage=stage,
            status=StageParticipant.ParticipantStatus.ACTIVE
        ).values_list('tournament_player_id', flat=True)
        active_players_qs = TournamentPlayer.objects.filter(id__in=active_tp_ids)
    else:
        active_players_qs = TournamentPlayer.objects.filter(
            tournament=round.stage.tournament if round.stage else round.tournament,
            status=TournamentPlayer.StatusChoices.REGISTERED
        )
    grouped_ids = set(
        TournamentPlayer.objects.filter(
            player_groups__round=round
        ).values_list('id', flat=True)
    )
    return active_players_qs.exclude(id__in=grouped_ids).select_related('profile')


def _get_ungrouped_count(stage, round):
    """Count ungrouped active players for a round."""
    return _get_ungrouped_players(stage, round).count()


@login_required
@require_http_methods(['POST'])
def round_grouping_add_to_group(request, tournament_slug, stage_slug, round_slug, session_id):
    """Add an ungrouped or waitlisted player to a group."""
    from django.http import JsonResponse

    tournament = get_object_or_404(Tournament, slug=tournament_slug, classification=Tournament.ClassificationTypes.TOURNAMENT)
    stage = get_object_or_404(Stage, slug=stage_slug, tournament=tournament)
    round = get_object_or_404(Round, slug=round_slug, stage=stage)
    profile = request.user.profile

    if not tournament.has_permission(profile):
        raise PermissionDenied

    stage = get_object_or_404(Stage, id=session_id)

    if round.grouping_status != Round.GroupingStatusChoices.DRAFT:
        return JsonResponse({'error': 'Round grouping is not editable'}, status=400)

    try:
        data = json.loads(request.body)
        player_id = data.get('player_id')
        group_id = data.get('group_id')

        tournament_player = get_object_or_404(TournamentPlayer, id=player_id, tournament=stage.tournament)
        group = get_object_or_404(PlayerGroup, id=group_id, round=round)

        # Capture old group before assignment (for group→group moves)
        old_groups = list(tournament_player.player_groups.filter(round=round))
        old_group = old_groups[0] if old_groups else None

        GroupingService.assign_player_to_group(tournament_player, group, round=round, moved_by=profile)

        history = build_opponent_history(stage, round) if stage else {}
        result = {
            'success': True,
            'to_group': _get_group_data(group, history),
            'ungrouped_count': _get_ungrouped_count(stage, round),
        }
        if old_group and old_group.id != group.id:
            result['from_group'] = _get_group_data(old_group, history)

        return JsonResponse(result)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=400)


@login_required
@require_http_methods(['POST'])
def round_grouping_remove_from_group(request, tournament_slug, stage_slug, round_slug, session_id):
    """Remove a player from a group and return them to ungrouped status."""
    from django.http import JsonResponse

    tournament = get_object_or_404(Tournament, slug=tournament_slug, classification=Tournament.ClassificationTypes.TOURNAMENT)
    stage = get_object_or_404(Stage, slug=stage_slug, tournament=tournament)
    round = get_object_or_404(Round, slug=round_slug, stage=stage)
    profile = request.user.profile

    if not tournament.has_permission(profile):
        raise PermissionDenied

    stage = get_object_or_404(Stage, id=session_id)

    if round.grouping_status != Round.GroupingStatusChoices.DRAFT:
        return JsonResponse({'error': 'Round grouping is not editable'}, status=400)

    try:
        data = json.loads(request.body)
        player_id = data.get('player_id')
        group_id = data.get('group_id')

        tournament_player = get_object_or_404(TournamentPlayer, id=player_id, tournament=stage.tournament)
        # Look up old group from DB if not provided by client
        if group_id:
            old_group = get_object_or_404(PlayerGroup, id=group_id, round=round)
        else:
            old_groups = list(tournament_player.player_groups.filter(round=round))
            old_group = old_groups[0] if old_groups else None

        GroupingService.remove_from_group(tournament_player, round=round)

        history = build_opponent_history(stage, round) if stage else {}
        result = {'success': True, 'ungrouped_count': _get_ungrouped_count(stage, round)}
        if old_group:
            result['from_group'] = _get_group_data(old_group, history)

        return JsonResponse(result)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=400)


@login_required
@require_http_methods(['POST'])
def round_grouping_create_group(request, tournament_slug, stage_slug, round_slug, session_id):
    """Create a new empty group for a round."""
    from django.http import JsonResponse

    tournament = get_object_or_404(Tournament, slug=tournament_slug, classification=Tournament.ClassificationTypes.TOURNAMENT)
    stage = get_object_or_404(Stage, slug=stage_slug, tournament=tournament)
    round = get_object_or_404(Round, slug=round_slug, stage=stage)
    profile = request.user.profile

    if not tournament.has_permission(profile):
        raise PermissionDenied

    stage = get_object_or_404(Stage, id=session_id)

    if round.grouping_status != Round.GroupingStatusChoices.DRAFT:
        return JsonResponse({'error': 'Round grouping is not editable'}, status=400)

    try:
        data = json.loads(request.body)
        player_id = data.get('player_id')  # Optional: TournamentPlayer id to add to new group

        # Get next group number
        max_group_num = round.player_groups.aggregate(models.Max('group_number'))['group_number__max'] or 0
        new_group_num = max_group_num + 1

        # Create new group
        new_group = PlayerGroup.objects.create(
            round=round,
            group_number=new_group_num,
            name=generate_name(new_group_num, NameConvention(stage.naming_convention)),
            created_by=profile,
        )

        # If player_id provided, capture old group then add to new group
        old_group = None
        if player_id:
            tournament_player = TournamentPlayer.objects.filter(
                tournament=stage.tournament,
                id=player_id
            ).first()
            if tournament_player:
                old_groups = list(tournament_player.player_groups.filter(round=round))
                old_group = old_groups[0] if old_groups else None
                GroupingService.assign_player_to_group(tournament_player, new_group, round=round, moved_by=profile)

        history = build_opponent_history(stage, round) if stage else {}
        result = {
            'success': True,
            'group': _get_group_data(new_group, history),
            'ungrouped_count': _get_ungrouped_count(stage, round),
        }
        result['group']['group_number'] = new_group.group_number
        result['group']['name'] = new_group.name
        if old_group:
            result['from_group'] = _get_group_data(old_group, history)

        return JsonResponse(result)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=400)


@login_required
@require_http_methods(['POST'])
def round_grouping_delete_group(request, tournament_slug, stage_slug, round_slug, session_id):
    """Delete an empty group."""
    from django.http import JsonResponse

    tournament = get_object_or_404(Tournament, slug=tournament_slug, classification=Tournament.ClassificationTypes.TOURNAMENT)
    stage = get_object_or_404(Stage, slug=stage_slug, tournament=tournament)
    round = get_object_or_404(Round, slug=round_slug, stage=stage)
    profile = request.user.profile

    if not tournament.has_permission(profile):
        raise PermissionDenied

    stage = get_object_or_404(Stage, id=session_id)

    if round.grouping_status != Round.GroupingStatusChoices.DRAFT:
        return JsonResponse({'error': 'Round grouping is not editable'}, status=400)

    try:
        data = json.loads(request.body)
        group_id = data.get('group_id')

        group = get_object_or_404(PlayerGroup, id=group_id, round=round)

        if group.tournament_players.exists():
            return JsonResponse({'error': 'Cannot delete group with members'}, status=400)

        group.delete()

        return JsonResponse({'success': True})
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=400)


DISCORD_URL_PATTERN = re.compile(r'^https://(discord\.com|discordapp\.com)/')
VIDEO_URL_PATTERN = re.compile(r'^https://(www\.)?(youtube\.com|youtu\.be|twitch\.tv)/')


@login_required
@require_http_methods(['POST'])
def round_grouping_edit_group(request, tournament_slug, stage_slug, round_slug, session_id):
    """Edit group metadata (name, discord_thread, video_link) and optionally update participants."""
    from django.http import JsonResponse

    tournament = get_object_or_404(Tournament, slug=tournament_slug, classification=Tournament.ClassificationTypes.TOURNAMENT)
    stage = get_object_or_404(Stage, slug=stage_slug, tournament=tournament)
    round = get_object_or_404(Round, slug=round_slug, stage=stage)
    profile = request.user.profile

    if not tournament.has_permission(profile):
        raise PermissionDenied

    stage = get_object_or_404(Stage, id=session_id)

    try:
        data = json.loads(request.body)
        group_id = data.get('group_id')
        group = get_object_or_404(PlayerGroup, id=group_id, round=round)

        # Validate and update metadata
        name = data.get('name', '').strip()
        discord_thread = data.get('discord_thread', '').strip()
        video_link = data.get('video_link', '').strip()

        if discord_thread and not DISCORD_URL_PATTERN.match(discord_thread):
            return JsonResponse({'error': 'Discord thread must be a discord.com link'}, status=400)

        if video_link and not VIDEO_URL_PATTERN.match(video_link):
            return JsonResponse({'error': 'Video link must be a YouTube or Twitch link'}, status=400)

        group.name = name
        group.discord_thread = discord_thread
        group.video_link = video_link
        group.save(update_fields=['name', 'discord_thread', 'video_link'])

        history = build_opponent_history(stage, round) if stage else {}
        participants_changed = False
        matches_cleared = False
        affected_groups = []

        # Handle participant changes if provided
        if 'participants' in data:
            if round.bracket_status == Round.BracketStatusChoices.FINALIZED:
                return JsonResponse({'error': 'Cannot change participants after bracket is finalized'}, status=400)

            new_participant_ids = set(data['participants'])
            current_ids = set(group.tournament_players.values_list('id', flat=True))

            if new_participant_ids != current_ids:
                participants_changed = True

                # Clear all matches for the entire round
                has_matches = Match.objects.filter(round=round).exists()
                if has_matches:
                    Match.objects.filter(round=round).delete()
                    MatchSeries.objects.filter(round=round).delete()
                    round.bracket_status = Round.BracketStatusChoices.DRAFT
                    round.save(update_fields=['bracket_status'])
                    matches_cleared = True

                removed_ids = current_ids - new_participant_ids
                added_ids = new_participant_ids - current_ids

                # Track groups that lose players
                affected_group_ids = set()

                # Remove players from this group
                for tp_id in removed_ids:
                    tp = TournamentPlayer.objects.filter(id=tp_id, tournament=stage.tournament).first()
                    if tp:
                        group.tournament_players.remove(tp)

                # Add players to this group
                for tp_id in added_ids:
                    tp = TournamentPlayer.objects.filter(id=tp_id, tournament=stage.tournament).first()
                    if not tp:
                        return JsonResponse({'error': f'Player {tp_id} not found', 'reload': True}, status=400)

                    # Check if player is in another group for this round
                    old_groups = list(tp.player_groups.filter(round=round))
                    for old_group in old_groups:
                        if old_group.id != group.id:
                            affected_group_ids.add(old_group.id)
                            old_group.tournament_players.remove(tp)
                            old_group.recalculate_overlap()

                    group.tournament_players.add(tp)

                group.recalculate_overlap()

                # Build affected groups data
                for ag_id in affected_group_ids:
                    try:
                        ag = PlayerGroup.objects.get(id=ag_id)
                        ag_data = _get_group_data(ag, history)
                        ag_data['members'] = [
                            {
                                'id': tp.id,
                                'display_name': tp.profile.display_name,
                                'image_url': tp.profile.image.url if tp.profile.image else '',
                                'availability_hours': tp.availability_hours or [],
                            }
                            for tp in ag.tournament_players.select_related('profile').all()
                        ]
                        affected_groups.append(ag_data)
                    except PlayerGroup.DoesNotExist:
                        pass

        # Build main group response
        group.refresh_from_db()
        group_data = _get_group_data(group, history)
        group_data['members'] = [
            {
                'id': tp.id,
                'display_name': tp.profile.display_name,
                'image_url': tp.profile.image.url if tp.profile.image else '',
                'availability_hours': tp.availability_hours or [],
            }
            for tp in group.tournament_players.select_related('profile').all()
        ]

        result = {
            'success': True,
            'group': group_data,
            'affected_groups': affected_groups,
            'participants_changed': participants_changed,
            'matches_cleared': matches_cleared,
        }

        if participants_changed:
            # Return full ungrouped players list for DOM rebuild
            ungrouped_qs = _get_ungrouped_players(stage, round)
            result['ungrouped_players'] = [
                {
                    'id': tp.id,
                    'display_name': tp.profile.display_name,
                    'image_url': tp.profile.image.url if tp.profile.image else '',
                    'availability_hours': tp.availability_hours or [],
                }
                for tp in ungrouped_qs
            ]
            result['ungrouped_count'] = len(result['ungrouped_players'])
        else:
            result['ungrouped_count'] = _get_ungrouped_count(stage, round)

        return JsonResponse(result)
    except Exception as e:
        return JsonResponse({'error': str(e), 'reload': True}, status=400)


@login_required
@require_http_methods(['POST'])
def round_grouping_finalize(request, tournament_slug, stage_slug, round_slug, session_id):
    """Finalize the grouping session."""
    from django.http import JsonResponse

    tournament = get_object_or_404(Tournament, slug=tournament_slug, classification=Tournament.ClassificationTypes.TOURNAMENT)
    stage = get_object_or_404(Stage, slug=stage_slug, tournament=tournament)
    round = get_object_or_404(Round, slug=round_slug, stage=stage)
    profile = request.user.profile

    if not tournament.has_permission(profile):
        raise PermissionDenied

    stage = get_object_or_404(Stage, id=session_id)

    if round.grouping_status != Round.GroupingStatusChoices.DRAFT:
        return JsonResponse({'error': 'Round grouping cannot be finalized'}, status=400)

    try:
        GroupingService.finalize_round_grouping(round)

        return JsonResponse({
            'success': True,
            'status': 'finalized',
        })
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=400)


@login_required
@require_http_methods(["POST"])
def round_generate_bracket(request, tournament_slug, stage_slug, round_slug):
    """Generate bracket (MatchSeries + Matches) for a finalized round."""
    from django.http import JsonResponse
    from .services.bracket import BracketService

    tournament = get_object_or_404(Tournament, slug=tournament_slug, classification=Tournament.ClassificationTypes.TOURNAMENT)
    stage = get_object_or_404(Stage, slug=stage_slug, tournament=tournament)
    round = get_object_or_404(Round, slug=round_slug, stage=stage)

    if not tournament.has_permission(request.user.profile):
        raise PermissionDenied

    if round.bracket_status == Round.BracketStatusChoices.FINALIZED:
        return JsonResponse({'error': 'Bracket has been finalized and cannot be regenerated.'}, status=400)

    try:
        data = json.loads(request.body) if request.body else {}
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    best_of = int(data.get('best_of', 1))
    if best_of not in (1, 3, 5, 7):
        return JsonResponse({'error': 'best_of must be 1, 3, 5, or 7'}, status=400)

    create_byes = bool(data.get('create_byes', False))

    try:
        warnings = BracketService.generate_round_bracket(
            round, best_of=best_of, create_byes=create_byes,
        )
        return JsonResponse({
            'success': True,
            'warnings': warnings,
            'match_count': round.matches.count(),
        })
    except ValueError as e:
        return JsonResponse({'error': str(e)}, status=400)


@login_required
@require_http_methods(["POST"])
def round_finalize_bracket(request, tournament_slug, stage_slug, round_slug):
    """Finalize the bracket so it can no longer be regenerated."""
    from django.http import JsonResponse

    tournament = get_object_or_404(Tournament, slug=tournament_slug, classification=Tournament.ClassificationTypes.TOURNAMENT)
    stage = get_object_or_404(Stage, slug=stage_slug, tournament=tournament)
    round = get_object_or_404(Round, slug=round_slug, stage=stage)

    if not tournament.has_permission(request.user.profile):
        raise PermissionDenied

    if round.bracket_status != Round.BracketStatusChoices.DRAFT:
        return JsonResponse({'error': 'Bracket is not in draft status.'}, status=400)

    if not round.matches.exists():
        return JsonResponse({'error': 'No bracket to finalize. Generate a bracket first.'}, status=400)

    round.bracket_status = Round.BracketStatusChoices.FINALIZED
    round.save(update_fields=['bracket_status'])

    return JsonResponse({'success': True, 'status': 'finalized'})


@login_required
@require_http_methods(["POST"])
def round_enable_matches(request, tournament_slug, stage_slug, round_slug):
    """Enable manual match scheduling for a round without going through grouping.

    Used by League and Game Group tournaments (any non-Tournament classification)
    so moderators can add matches directly. Finalizes grouping + bracket on the
    round and redirects to the matches page where the manual tools live.
    """
    tournament = get_object_or_404(Tournament, slug=tournament_slug)
    stage = get_object_or_404(Stage, slug=stage_slug, tournament=tournament)
    round = get_object_or_404(Round, slug=round_slug, stage=stage)

    if not tournament.has_permission(request.user.profile):
        raise PermissionDenied

    # Idempotent: if already enabled, just go to the matches page
    if round.bracket_status != Round.BracketStatusChoices.FINALIZED:
        round.grouping_status = Round.GroupingStatusChoices.FINALIZED
        round.bracket_status = Round.BracketStatusChoices.FINALIZED
        round.save(update_fields=['grouping_status', 'bracket_status'])
        messages.success(request, 'Match scheduling enabled. You can now add matches.')

    return redirect('round-matches-page', tournament_slug=tournament.slug, stage_slug=stage.slug, round_slug=round.slug)


def _build_series_edit_context(tournament, stage, round):
    """Build context variables needed for the series edit modal on both pages."""
    edit_url = reverse('round-edit-series', kwargs={
        'tournament_slug': tournament.slug,
        'stage_slug': stage.slug,
        'round_slug': round.slug,
    })

    # Build series data map (keyed by series id)
    all_series = MatchSeries.objects.filter(round=round).select_related(
        'player_group', 'player_group__group_moderator'
    ).prefetch_related(
        'matchseat_set__stage_participant__tournament_player__profile',
        'matches__game',
    ).order_by('id')

    series_map = {}
    for s in all_series:
        group = s.player_group
        seats = []
        for seat in s.matchseat_set.all():
            sp = seat.stage_participant
            tp = sp.tournament_player
            seats.append({
                'id': seat.id,
                'participant_id': sp.id,
                'display_name': tp.profile.display_name,
                'image_url': tp.profile.image.url if tp.profile.image else '',
                'status': sp.status,
            })
        matches = []
        for i, m in enumerate(s.matches.all(), 1):
            matches.append({
                'id': m.id,
                'name': m.name or f'Game {i}',
                'match_number': m.match_number,
                'scheduled_time': m.scheduled_time.isoformat() if m.scheduled_time else None,
                'has_game': m.game_id is not None,
                'game_url': m.game.get_absolute_url() if m.game_id else '',
                'game_final': m.game.final if m.game_id else False,
                'status': m.status,
            })
        mod = group.group_moderator if group else None
        series_map[s.id] = {
            'id': s.id,
            'name': group.name if group else (s.name or ''),
            'player_group_id': group.id if group else None,
            'discord_thread': group.discord_thread if group else '',
            'video_link': group.video_link if group else '',
            'group_moderator_id': mod.id if mod else None,
            'group_moderator_name': mod.display_name if mod else '',
            'group_moderator_image': (mod.image.url if mod.image else '') if mod else '',
            'number_of_games': s.number_of_games,
            'is_bye': s.is_bye,
            'status': s.status,
            'seats': seats,
            'matches': matches,
        }

    # Build stage participants list
    participants = StageParticipant.objects.filter(stage=stage).select_related(
        'tournament_player__profile'
    ).order_by('tournament_player__profile__display_name')
    participants_list = [
        {
            'id': sp.id,
            'display_name': sp.tournament_player.profile.display_name,
            'image_url': sp.tournament_player.profile.image.url if sp.tournament_player.profile.image else '',
            'status': sp.status,
        }
        for sp in participants
    ]

    return {
        'edit_series_url': edit_url,
        'record_game_url': reverse('record-game-v2'),
        'series_data_json': json.dumps(series_map),
        'stage_participants_json': json.dumps(participants_list),
    }


def _build_series_response(series):
    """Build JSON-serializable dict for a MatchSeries including seats and matches."""
    series.refresh_from_db()
    seats = MatchSeat.objects.filter(series=series).select_related(
        'stage_participant__tournament_player__profile'
    ).order_by('seat_number')
    matches = Match.objects.filter(series=series).select_related('game').order_by('match_number')

    group = series.player_group
    mod = group.group_moderator if group else None
    return {
        'id': series.id,
        'name': group.name if group else (series.name or ''),
        'player_group_id': group.id if group else None,
        'discord_thread': group.discord_thread if group else '',
        'video_link': group.video_link if group else '',
        'group_moderator_id': mod.id if mod else None,
        'group_moderator_name': mod.display_name if mod else '',
        'group_moderator_image': (mod.image.url if mod.image else '') if mod else '',
        'number_of_games': series.number_of_games,
        'is_bye': series.is_bye,
        'status': series.status,
        'seats': [
            {
                'id': seat.id,
                'participant_id': seat.stage_participant_id,
                'display_name': seat.stage_participant.tournament_player.profile.display_name,
                'image_url': seat.stage_participant.tournament_player.profile.image.url if seat.stage_participant.tournament_player.profile.image else '',
                'status': seat.stage_participant.status,
            }
            for seat in seats
        ],
        'matches': [
            {
                'id': m.id,
                'name': m.name or f'Game {i}',
                'match_number': m.match_number,
                'scheduled_time': m.scheduled_time.isoformat() if m.scheduled_time else None,
                'has_game': m.game_id is not None,
                'game_url': m.game.get_absolute_url() if m.game_id else '',
                'game_final': m.game.final if m.game_id else False,
                'status': m.status,
            }
            for i, m in enumerate(matches, 1)
        ],
    }


@login_required
@require_http_methods(['POST'])
def round_edit_series(request, tournament_slug, stage_slug, round_slug):
    """Edit a MatchSeries: group metadata, match scheduled times, add/remove matches and seats."""
    from django.http import JsonResponse
    from datetime import datetime

    tournament = get_object_or_404(Tournament, slug=tournament_slug)
    stage = get_object_or_404(Stage, slug=stage_slug, tournament=tournament)
    round = get_object_or_404(Round, slug=round_slug, stage=stage)

    if not tournament.has_permission(request.user.profile):
        raise PermissionDenied

    try:
        data = json.loads(request.body)
        series_id = data.get('series_id')
        series = get_object_or_404(MatchSeries, id=series_id, round=round)

        # --- PlayerGroup metadata ---
        group = series.player_group
        if group:
            name = data.get('name', '').strip()
            discord_thread = data.get('discord_thread', '').strip()
            video_link = data.get('video_link', '').strip()

            if discord_thread and not DISCORD_URL_PATTERN.match(discord_thread):
                return JsonResponse({'error': 'Discord thread must be a discord.com link'}, status=400)
            if video_link and not VIDEO_URL_PATTERN.match(video_link):
                return JsonResponse({'error': 'Video link must be a YouTube or Twitch link'}, status=400)

            # Group moderator (single Profile, optional). 'group_moderator_id'
            # absent = leave unchanged; null/empty = clear.
            update_fields = ['name', 'discord_thread', 'video_link', 'video_platform']
            if 'group_moderator_id' in data:
                mod_id = data.get('group_moderator_id')
                if mod_id:
                    mod_profile = Profile.objects.filter(pk=mod_id).first()
                    if not mod_profile:
                        return JsonResponse({'error': 'Selected group moderator not found'}, status=400)
                    group.group_moderator = mod_profile
                else:
                    group.group_moderator = None
                update_fields.append('group_moderator')

            group.name = name
            group.discord_thread = discord_thread
            group.video_link = video_link
            group.save(update_fields=update_fields)

        # --- Match scheduled_time updates ---
        for match_data in data.get('matches', []):
            match_id = match_data.get('id')
            if match_id is None:
                continue
            match = Match.objects.filter(id=match_id, series=series).first()
            if not match:
                continue
            scheduled_str = match_data.get('scheduled_time')
            if scheduled_str:
                try:
                    match.scheduled_time = datetime.fromisoformat(scheduled_str.replace('Z', '+00:00'))
                except (ValueError, TypeError):
                    return JsonResponse({'error': f'Invalid date format for match {match_id}'}, status=400)
            else:
                match.scheduled_time = None
            match.save(update_fields=['scheduled_time'])

        # --- Delete matches ---
        for match_id in data.get('delete_match_ids', []):
            match = Match.objects.filter(id=match_id, series=series).first()
            if not match:
                continue
            if match.game_id is not None:
                return JsonResponse({'error': f'Cannot delete match "{match.name}" — it has a linked game'}, status=400)
            match.delete()

        # --- Add matches ---
        add_matches = data.get('add_matches', [])
        if isinstance(add_matches, int):
            # Legacy format: just a count
            for _ in range(add_matches):
                Match.objects.create(round=round, series=series)
        else:
            # New format: list of {scheduled_time} dicts
            for m_data in add_matches:
                scheduled_time = None
                st_str = m_data.get('scheduled_time')
                if st_str:
                    try:
                        scheduled_time = datetime.fromisoformat(st_str.replace('Z', '+00:00'))
                    except (ValueError, TypeError):
                        pass
                Match.objects.create(round=round, series=series, scheduled_time=scheduled_time)

        # --- Remove seats ---
        for seat_id in data.get('remove_seat_ids', []):
            MatchSeat.objects.filter(id=seat_id, series=series).delete()

        # --- Add seats ---
        for sp_id in data.get('add_seat_participant_ids', []):
            sp = StageParticipant.objects.filter(id=sp_id, stage=stage).first()
            if not sp:
                return JsonResponse({'error': f'Stage participant {sp_id} not found'}, status=400)
            if MatchSeat.objects.filter(series=series, stage_participant=sp).exists():
                continue
            last_seat = MatchSeat.objects.filter(series=series).order_by('-seat_number').first()
            next_num = (last_seat.seat_number + 1) if last_seat and last_seat.seat_number is not None else 1
            MatchSeat.objects.create(series=series, stage_participant=sp, seat_number=next_num)

        # --- Update number_of_games and regenerate match names ---
        new_count = series.matches.count()
        if new_count != series.number_of_games:
            series.number_of_games = new_count
            series.save(update_fields=['number_of_games'])

        # Regenerate match names from group name
        if group:
            for i, m in enumerate(series.matches.order_by('match_number'), 1):
                new_name = group.name if new_count == 1 else f"{group.name} Game {i}"
                if m.name != new_name:
                    m.name = new_name
                    m.save(update_fields=['name'])

        # --- Recalculate series status ---
        matches_qs = series.matches.all()
        if not matches_qs.exists():
            new_status = CompetitionStatus.PENDING
        elif all(m.status == CompetitionStatus.COMPLETED for m in matches_qs):
            new_status = CompetitionStatus.COMPLETED
        elif any(m.game_id is not None for m in matches_qs):
            new_status = CompetitionStatus.ACTIVE
        else:
            new_status = CompetitionStatus.PENDING
        if series.status != new_status:
            series.status = new_status
            series.save(update_fields=['status'])

        # --- Return HTML partial if card_type provided ---
        card_type = data.get('card_type')
        if card_type:
            from django.template.loader import render_to_string
            series_fresh = MatchSeries.objects.select_related(
                'player_group',
            ).prefetch_related(
                'winners__tournament_player__profile',
                'matches__game',
                'matches__game__efforts__faction',
                'matches__game__efforts__player',
                'matchseat_set__stage_participant__tournament_player__profile',
            ).get(pk=series.pk)
            _attach_series_effort_grid(series_fresh)

            if card_type == 'matches':
                profile = request.user.profile
                if profile.admin or tournament.has_permission(profile):
                    recordable_match_ids = set(
                        Match.objects.filter(round=round).values_list('id', flat=True)
                    )
                elif tournament.players_can_record_matches():
                    participant_series_ids = MatchSeat.objects.filter(
                        series__round=round,
                        stage_participant__tournament_player__profile=profile
                    ).values_list('series_id', flat=True)
                    recordable_match_ids = set(
                        Match.objects.filter(
                            round=round, series_id__in=participant_series_ids
                        ).values_list('id', flat=True)
                    )
                else:
                    recordable_match_ids = set()
                # Group moderators can always record their group's matches.
                recordable_match_ids |= set(
                    Match.objects.filter(
                        round=round, series__player_group__group_moderator=profile
                    ).values_list('id', flat=True)
                )
                series_index = data.get('series_index', 1)
                is_participant_series = set(MatchSeat.objects.filter(
                    series__round=round,
                    stage_participant__tournament_player__profile=profile
                ).values_list('series_id', flat=True))
                html = render_to_string('the_warroom/partials/series_card_matches.html', {
                    'series': series_fresh,
                    'recordable_match_ids': recordable_match_ids,
                    'can_manage': True,
                    'series_index': series_index,
                    'is_participant_series': is_participant_series,
                }, request=request)
            else:  # bracket
                html = render_to_string('the_warroom/partials/series_card_bracket.html', {
                    'series': series_fresh,
                    'is_bracket_finalized': True,
                }, request=request)

            return JsonResponse({'success': True, 'html': html, 'series_id': series.id, 'series': _build_series_response(series_fresh)})

        return JsonResponse({
            'success': True,
            'series': _build_series_response(series),
        })

    except Exception as e:
        return JsonResponse({'error': str(e)}, status=400)


@login_required
@require_http_methods(['POST'])
def round_create_series(request, tournament_slug, stage_slug, round_slug):
    """Create a new MatchSeries with a backing PlayerGroup and one default Match."""
    tournament = get_object_or_404(Tournament, slug=tournament_slug)
    stage = get_object_or_404(Stage, slug=stage_slug, tournament=tournament)
    round = get_object_or_404(Round, slug=round_slug, stage=stage)

    if not tournament.has_permission(request.user.profile):
        raise PermissionDenied

    if round.bracket_status != Round.BracketStatusChoices.FINALIZED:
        return JsonResponse({'error': 'Bracket must be finalized before adding series.'}, status=400)

    try:
        max_group_num = round.player_groups.aggregate(
            models.Max('group_number')
        )['group_number__max'] or 0
        new_group_num = max_group_num + 1

        group = PlayerGroup.objects.create(
            round=round,
            group_number=new_group_num,
            name=generate_name(new_group_num, NameConvention(stage.naming_convention)),
            created_by=request.user.profile,
        )

        series = MatchSeries.objects.create(
            round=round,
            player_group=group,
            number_of_games=1,
        )

        Match.objects.create(round=round, series=series)

        return JsonResponse({
            'success': True,
            'series': _build_series_response(series),
        })

    except Exception as e:
        return JsonResponse({'error': str(e)}, status=400)


@login_required
@require_http_methods(['POST'])
def round_delete_series(request, tournament_slug, stage_slug, round_slug):
    """Delete a MatchSeries and its backing PlayerGroup."""
    tournament = get_object_or_404(Tournament, slug=tournament_slug)
    stage = get_object_or_404(Stage, slug=stage_slug, tournament=tournament)
    round = get_object_or_404(Round, slug=round_slug, stage=stage)

    if not tournament.has_permission(request.user.profile):
        raise PermissionDenied

    try:
        data = json.loads(request.body)
        series_id = data.get('series_id')
        series = get_object_or_404(MatchSeries, id=series_id, round=round)

        # Block deletion if any match has a linked game
        if series.matches.filter(game__isnull=False).exists():
            return JsonResponse({'error': 'Cannot delete a series that has recorded games.'}, status=400)

        # Delete the backing PlayerGroup (if any) before the series
        group = series.player_group
        series.delete()  # Cascades to Match, MatchSeat
        if group:
            group.delete()

        return JsonResponse({'success': True, 'deleted_id': series_id})

    except Exception as e:
        return JsonResponse({'error': str(e)}, status=400)


# ===== Stage Advancement Views =====

def _effort_display(effort):
    """Return display dict for an effort in the advancement template."""
    if not effort:
        return None
    faction_icon_url = effort.faction.small_icon.url if effort.faction and effort.faction.small_icon else None
    faction_name = str(effort.faction) if effort.faction else None
    if effort.dominance:
        result_icon_url = f'/static/images/dominance/{effort.dominance}_Icon.png'
        score_display = None
    elif effort.coalition_with and effort.coalition_with.small_icon:
        result_icon_url = effort.coalition_with.small_icon.url
        score_display = None
    else:
        result_icon_url = None
        score_display = effort.score if effort.score is not None else '0'
    return {
        'faction_icon_url': faction_icon_url,
        'faction_name': faction_name,
        'result_icon_url': result_icon_url,
        'score_display': score_display,
        'win': effort.win,
    }


def _build_game_only_advancement_context(stage):
    """
    Build eligible_players list from raw game efforts when no MatchSeries exist.
    Uses final, non-test games linked to rounds in this stage.
    Returns dict compatible with stage_advancement_page context.
    """
    from collections import defaultdict

    efforts = Effort.objects.filter(
        game__round__stage=stage,
        game__test_match=False,
        game__final=True,
    ).select_related(
        'game__round', 'player', 'faction', 'coalition_with'
    ).order_by('game__date_posted')

    efforts_by_profile = defaultdict(list)
    for effort in efforts:
        if effort.player:
            efforts_by_profile[effort.player].append(effort)

    BRACKET_FORMATS = {FormatChoices.SINGLE_ELIM, FormatChoices.DOUBLE_ELIM}
    stage_format = stage.get_format()
    is_double_elim = stage_format == FormatChoices.DOUBLE_ELIM
    advancement_count = stage.advancement_count

    eligible_players_raw = []

    for sp in stage.participants.filter(
        status=StageParticipant.ParticipantStatus.ACTIVE
    ).select_related('tournament_player__profile'):
        profile = sp.tournament_player.profile
        player_efforts = efforts_by_profile.get(profile, [])

        rounds_seen = {}
        for effort in player_efforts:
            rnd = effort.game.round
            if rnd not in rounds_seen:
                rounds_seen[rnd] = []
            rounds_seen[rnd].append(effort)

        series_data = []
        for rnd, rnd_efforts in rounds_seen.items():
            match_data = [{'match': None, 'effort': e, 'game_url': reverse('game-detail', args=[e.game.id]), 'display': _effort_display(e)} for e in rnd_efforts]
            wins = sum(1 for e in rnd_efforts if e.win)

            class RoundProxy:
                def __init__(self, r):
                    self.round = r
                    self.is_bye = False

            series_data.append({
                'series': RoundProxy(rnd),
                'match_data': match_data,
                'is_winner': wins > 0,
            })

        total_games = len(player_efforts)
        wins = sum(1 for e in player_efforts if e.win)
        win_rate = (wins / total_games) if total_games > 0 else 0.0
        all_bye = total_games == 0

        eligible_players_raw.append({
            'sp': sp,
            'series_data': series_data,
            'all_bye': all_bye,
            'win_rate': win_rate,
            'non_bye_count': total_games,
        })

    eligible_players_raw.sort(
        key=lambda p: (p['all_bye'], p['win_rate'], p['non_bye_count']),
        reverse=True,
    )

    eligible_players = []
    for rank_idx, player_data in enumerate(eligible_players_raw):
        sp = player_data['sp']
        all_bye = player_data['all_bye']
        series_data = player_data['series_data']

        if advancement_count is not None:
            default_action = 'advance' if rank_idx < advancement_count else 'eliminate'
        elif all_bye:
            default_action = 'advance'
        elif stage_format in BRACKET_FORMATS:
            lost_any = any(not sd['is_winner'] for sd in series_data)
            if stage_format == FormatChoices.SINGLE_ELIM and lost_any:
                default_action = 'eliminate'
            elif is_double_elim and lost_any:
                default_action = 'losers'
            else:
                default_action = 'advance'
        else:
            default_action = 'skip'

        eligible_players.append({
            'sp': sp,
            'series_data': series_data,
            'all_bye': all_bye,
            'default_action': default_action,
            'rank': rank_idx + 1,
            'win_rate': player_data['win_rate'],
        })

    return {
        'eligible_players': eligible_players,
        'incomplete_players': [],
        'has_incomplete_warning': False,
        'incomplete_total': 0,
    }


@login_required
def stage_advancement_page(request, tournament_slug, stage_slug):
    """Moderator page to review and approve player advancement after rounds complete."""
    tournament = get_object_or_404(Tournament, slug=tournament_slug)
    stage = get_object_or_404(Stage, slug=stage_slug, tournament=tournament)

    if not tournament.has_permission(request.user.profile):
        raise PermissionDenied()

    has_match_series = MatchSeries.objects.filter(round__stage=stage).exists()

    # All stages later in this tournament (fallback advancement targets)
    available_stages = list(Stage.objects.filter(
        tournament=tournament,
        order__gt=stage.order,
    ).order_by('order'))

    BRACKET_FORMATS = {FormatChoices.SINGLE_ELIM, FormatChoices.DOUBLE_ELIM}
    stage_format = stage.get_format()
    is_double_elim = stage_format == FormatChoices.DOUBLE_ELIM
    advancement_count = stage.advancement_count

    if has_match_series:
        all_stage_series = list(MatchSeries.objects.filter(round__stage=stage).prefetch_related(
            'winners', 'matches__game__efforts__faction', 'player_group__tournament_players',
            'matchseat_set__stage_participant'
        ))

        eligible_players_raw = []
        incomplete_players = []

        for sp in stage.participants.filter(
            status=StageParticipant.ParticipantStatus.ACTIVE
        ).select_related('tournament_player__profile'):
            player_series = [
                s for s in all_stage_series
                if (s.player_group and sp.tournament_player in s.player_group.tournament_players.all())
                or any(seat.stage_participant_id == sp.id for seat in s.matchseat_set.all())
            ]

            # Players with no series at all are eligible to advance by default (rank top)
            if not player_series:
                eligible_players_raw.append({
                    'sp': sp,
                    'series_data': [],
                    'all_bye': True,
                    'win_rate': 1.0,
                    'non_bye_count': 0,
                })
                continue

            all_bye = all(s.is_bye for s in player_series)
            incomplete = [s for s in player_series if not s.is_complete()]
            if incomplete:
                incomplete_players.append({'sp': sp, 'incomplete_count': len(incomplete)})
                continue

            # Build series data for template
            series_data = []
            for series in player_series:
                match_data = []
                for match in series.matches.all():
                    effort = None
                    if match.game:
                        effort = match.game.efforts.select_related('faction', 'coalition_with').filter(player=sp.tournament_player.profile).first()
                    game_url = reverse('game-detail', args=[match.game.id]) if match.game else None
                    match_data.append({'match': match, 'effort': effort, 'game_url': game_url, 'display': _effort_display(effort)})
                series_data.append({
                    'series': series,
                    'match_data': match_data,
                    'is_winner': sp in series.winners.all(),
                })

            # Compute win rate for ranking
            non_bye_series = [s for s in player_series if not s.is_bye]
            if all_bye or not non_bye_series:
                win_rate = 1.0
                non_bye_count = 0
            else:
                wins = sum(1 for sd in series_data if not sd['series'].is_bye and sd['is_winner'])
                win_rate = wins / len(non_bye_series)
                non_bye_count = len(non_bye_series)

            eligible_players_raw.append({
                'sp': sp,
                'series_data': series_data,
                'all_bye': all_bye,
                'win_rate': win_rate,
                'non_bye_count': non_bye_count,
            })

        # Sort: bye players first (highest rank), then by win rate descending, then by games played descending
        eligible_players_raw.sort(key=lambda p: (p['all_bye'], p['win_rate'], p['non_bye_count']), reverse=True)

        eligible_players = []
        for rank_idx, player_data in enumerate(eligible_players_raw):
            sp = player_data['sp']
            all_bye = player_data['all_bye']
            series_data = player_data['series_data']

            if advancement_count is not None:
                default_action = 'advance' if rank_idx < advancement_count else 'eliminate'
            elif all_bye:
                default_action = 'advance'
            elif stage_format in BRACKET_FORMATS:
                lost_any = any(not sd['is_winner'] for sd in series_data)
                if stage_format == FormatChoices.SINGLE_ELIM and lost_any:
                    default_action = 'eliminate'
                elif is_double_elim and lost_any:
                    default_action = 'losers'
                else:
                    default_action = 'advance'
            else:
                default_action = 'skip'  # league-style: moderator decides

            eligible_players.append({
                'sp': sp,
                'series_data': series_data,
                'all_bye': all_bye,
                'default_action': default_action,
                'rank': rank_idx + 1,
                'win_rate': player_data['win_rate'],
            })

        has_incomplete_warning = bool(incomplete_players) and advancement_count is not None
        player_context = {
            'eligible_players': eligible_players,
            'incomplete_players': incomplete_players,
            'has_incomplete_warning': has_incomplete_warning,
            'incomplete_total': len(incomplete_players),
        }
    else:
        player_context = _build_game_only_advancement_context(stage)

    already_advanced = list(stage.participants.filter(
        status=StageParticipant.ParticipantStatus.ADVANCED
    ).select_related('tournament_player__profile'))

    ctx = _stage_base_context(request, tournament, stage)
    ctx.update({
        'active_page': 'advancement',
        'already_advanced': already_advanced,
        'available_stages': available_stages,
        'is_bracket': stage_format in BRACKET_FORMATS,
        'is_double_elim': is_double_elim,
        'stage_format': stage_format,
        'advancement_count': advancement_count,
        'all_stages': list(Stage.objects.filter(tournament=tournament).exclude(id=stage.id).order_by('order')),
        'format_choices': FormatChoices.choices,
    })
    ctx.update(player_context)
    return render(request, 'the_warroom/stage_advancement.html', ctx)


@login_required
@require_http_methods(["POST"])
def stage_advancement_submit(request, tournament_slug, stage_slug):
    """Process moderator advancement decisions for eligible players."""
    tournament = get_object_or_404(Tournament, slug=tournament_slug)
    stage = get_object_or_404(Stage, slug=stage_slug, tournament=tournament)

    if not tournament.has_permission(request.user.profile):
        raise PermissionDenied()

    try:
        data = json.loads(request.body)
        decisions = data.get('decisions', [])
    except (json.JSONDecodeError, AttributeError):
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    errors = []
    for decision in decisions:
        sp_id = decision.get('sp_id')
        action = decision.get('action')

        sp = StageParticipant.objects.filter(id=sp_id, stage=stage).select_related('tournament_player').first()
        if not sp:
            errors.append(f"StageParticipant {sp_id} not found.")
            continue

        if action == 'eliminate':
            sp.status = StageParticipant.ParticipantStatus.ELIMINATED
            sp.save(update_fields=['status'])
            tp = sp.tournament_player
            tp.set_status(TournamentPlayer.StatusChoices.ELIMINATED)
            tp.save()
        elif action == 'advance':
            to_stage_id = decision.get('to_stage_id')
            if not to_stage_id:
                errors.append(f"No to_stage_id for sp {sp_id}.")
                continue
            to_stage = Stage.objects.filter(id=to_stage_id, tournament=tournament).first()
            if not to_stage:
                errors.append(f"Stage {to_stage_id} not found.")
                continue
            StageParticipant.objects.get_or_create(
                stage=to_stage,
                tournament_player=sp.tournament_player,
                defaults={'status': StageParticipant.ParticipantStatus.ACTIVE},
            )
            sp.status = StageParticipant.ParticipantStatus.ADVANCED
            sp.save(update_fields=['status'])
        else:
            errors.append(f"Unknown action '{action}' for sp {sp_id}.")

    if errors:
        return JsonResponse({'success': False, 'errors': errors}, status=400)

    return JsonResponse({'success': True})


@login_required
@require_http_methods(["POST"])
def stage_create_quick(request, tournament_slug):
    """Quick-create a Stage (and first Round) from the advancement page modal."""
    tournament = get_object_or_404(Tournament, slug=tournament_slug)

    if not tournament.has_permission(request.user.profile):
        raise PermissionDenied()

    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, AttributeError):
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    stage_name = (data.get('stage_name') or '').strip()
    round_name = (data.get('round_name') or 'Round 1').strip() or 'Round 1'

    if not stage_name:
        return JsonResponse({'error': 'Stage name is required.'}, status=400)

    max_order = tournament.stages.aggregate(Max('order'))['order__max'] or 0
    stage = Stage.objects.create(
        tournament=tournament,
        name=stage_name,
        order=max_order + 1,
    )

    Round.objects.create(
        stage=stage,
        name=round_name,
        round_number=1,
    )

    return JsonResponse({'success': True, 'stage_id': stage.id, 'stage_name': stage.name})


@login_required
@require_http_methods(["POST"])
def tournament_enable_stages(request, tournament_slug):
    """Enable stages on a tournament and return the rendered 'Stages' card."""
    tournament = get_object_or_404(Tournament, slug=tournament_slug)

    if not tournament.has_permission(request.user.profile):
        raise PermissionDenied()

    if not tournament.use_stages:
        tournament.use_stages = True
        tournament.save(update_fields=['use_stages'])

    return render(request, 'the_warroom/partials/stages_card.html', {
        'tournament': tournament,
        'tournament_stages': tournament.stages.all().order_by('order'),
    })


@login_required
@require_http_methods(["POST"])
def stage_enable_rounds(request, tournament_slug, stage_slug):
    """Enable rounds on a stage and return the rendered 'Rounds' card."""
    tournament = get_object_or_404(Tournament, slug=tournament_slug)
    stage = get_object_or_404(Stage, slug=stage_slug, tournament=tournament)

    if not tournament.has_permission(request.user.profile):
        raise PermissionDenied()

    if not stage.use_rounds:
        stage.use_rounds = True
        stage.save(update_fields=['use_rounds'])

    return render(request, 'the_warroom/partials/rounds_card.html', {
        'tournament': tournament,
        'stage': stage,
        'stage_rounds': stage.rounds.all().order_by('round_number'),
    })


@login_required
@require_http_methods(["POST"])
def tournament_disable_stages(request, tournament_slug):
    """Disable stages on a tournament and return the rendered 'Enable Stages' card.

    Only allowed when at most one stage exists; the remaining stage becomes the
    hidden default stage and keeps its rounds/games.
    """
    tournament = get_object_or_404(Tournament, slug=tournament_slug)

    if not tournament.has_permission(request.user.profile):
        raise PermissionDenied()

    if tournament.stages.count() > 1:
        return HttpResponseBadRequest("Cannot disable stages while more than one stage exists.")

    if tournament.use_stages:
        tournament.use_stages = False
        tournament.save(update_fields=['use_stages'])

    return render(request, 'the_warroom/partials/enable_stages_card.html', {
        'tournament': tournament,
    })


@login_required
@require_http_methods(["POST"])
def stage_disable_rounds(request, tournament_slug, stage_slug):
    """Disable rounds on a stage and return the rendered 'Enable Rounds' card.

    Only allowed when at most one round exists; the remaining round becomes the
    hidden default round and keeps its games.
    """
    tournament = get_object_or_404(Tournament, slug=tournament_slug)
    stage = get_object_or_404(Stage, slug=stage_slug, tournament=tournament)

    if not tournament.has_permission(request.user.profile):
        raise PermissionDenied()

    if stage.rounds.count() > 1:
        return HttpResponseBadRequest("Cannot disable rounds while more than one round exists.")

    if stage.use_rounds:
        stage.use_rounds = False
        stage.save(update_fields=['use_rounds'])

    return render(request, 'the_warroom/partials/enable_rounds_card.html', {
        'tournament': tournament,
        'stage': stage,
    })


@login_required
@require_http_methods(["POST"])
def stage_advancement_config(request, tournament_slug, stage_slug):
    """Update stage-level advancement configuration."""
    tournament = get_object_or_404(Tournament, slug=tournament_slug)
    stage = get_object_or_404(Stage, slug=stage_slug, tournament=tournament)

    if not tournament.has_permission(request.user.profile):
        raise PermissionDenied()

    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, AttributeError):
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    update_fields = []

    stage_format = data.get('stage_format')
    if 'stage_format' in data:
        stage.stage_format = stage_format if stage_format else None
        update_fields.append('stage_format')

    if 'winners_advance_to_id' in data:
        wid = data.get('winners_advance_to_id')
        if wid:
            winners_stage = Stage.objects.filter(id=wid, tournament=tournament).first()
            if not winners_stage:
                return JsonResponse({'error': f'Stage {wid} not found.'}, status=400)
            stage.winners_advance_to = winners_stage
        else:
            stage.winners_advance_to = None
        update_fields.append('winners_advance_to')

    if 'losers_advance_to_id' in data:
        lid = data.get('losers_advance_to_id')
        if lid:
            losers_stage = Stage.objects.filter(id=lid, tournament=tournament).first()
            if not losers_stage:
                return JsonResponse({'error': f'Stage {lid} not found.'}, status=400)
            stage.losers_advance_to = losers_stage
        else:
            stage.losers_advance_to = None
        update_fields.append('losers_advance_to')

    if 'advancement_count' in data:
        cnt = data.get('advancement_count')
        stage.advancement_count = int(cnt) if cnt is not None and cnt != '' else None
        update_fields.append('advancement_count')

    if update_fields:
        stage.save(update_fields=update_fields)

    return JsonResponse({'success': True})