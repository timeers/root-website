import time
import json

from itertools import groupby
from django.shortcuts import render
from django.views.generic import ListView, UpdateView, CreateView, DeleteView
from django.views.decorators.http import require_http_methods
from django.shortcuts import get_object_or_404, redirect
from django.forms.models import modelformset_factory
from django.http import HttpResponse, Http404
from django.core.exceptions import ObjectDoesNotExist, PermissionDenied
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
from django.urls import reverse, reverse_lazy
from django.contrib.auth.decorators import login_required
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.db import IntegrityError, models

from django.db.models import Count, F, ExpressionWrapper, FloatField, IntegerField, Max, Q, Case, When, Value, ProtectedError, Prefetch, OuterRef, Subquery
from django.db.models.functions import Cast, Coalesce
from django.utils import timezone 
from django.utils.translation import get_language
from urllib.parse import quote

from .models import (Game, Effort, TurnScore, ScoreCard, Round, Tournament, AssetModeChoices,
                     TournamentPlayer, PlayerGroup, Stage, StageParticipant, FormatChoices,
                     Match, MatchSeries, MatchSeat, CompetitionStatus)
from .services.grouping import GroupingService, build_opponent_history
from .forms import (GameCreateForm, GameCreateFormV2, GameInfoUpdateForm, EffortCreateForm,
                    TurnScoreCreateForm, ScoreCardCreateForm, AssignScorecardForm, AssignEffortForm,
                    RoundCreateForm, StageCreateForm,
                    TournamentDynamicCreateForm, TournamentDynamicUpdateForm,
                    TournamentManageAssetsForm,
                    TournamentPlayerSettingsForm, TournamentAssetSettingsForm,
                    RoundManagePlayersForm)
from .filters import GameFilter, PlayerGameFilter, TournamentGameFilter

from the_keep.models import Post, Faction, Deck, Map, Vagabond, Hireling, Landmark, Tweak, StatusChoices, PostTranslation

from the_gatehouse.models import Profile, BackgroundImage, ForegroundImage, Language
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
    if request.user.is_authenticated and not request.user.profile.weird:
        queryset = Game.objects.filter(official=True, final=True).prefetch_related(
            'efforts__player', 'efforts__faction', 'efforts__vagabond', 'round__tournament', 
            'map', 'deck',
            # 'hirelings', 'landmarks', 'tweaks', 
        )
    else:
        queryset = Game.objects.filter(final=True).prefetch_related(
            'efforts__player', 'efforts__faction', 'efforts__vagabond', 'round__tournament', 
            'map', 'deck',
            # 'hirelings', 'landmarks', 'tweaks', 
        )
    
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
    if request.user.is_authenticated and not request.user.profile.weird:
        queryset = Game.objects.filter(official=True, final=True).prefetch_related(
            'efforts__player', 'efforts__faction', 'efforts__vagabond', 'round__tournament', 
            'map', 'deck',
            # 'hirelings', 'landmarks', 'tweaks', 
        )
    else:
        queryset = Game.objects.filter(final=True).prefetch_related(
            'efforts__player', 'efforts__faction', 'efforts__vagabond', 'round__tournament', 
            'map', 'deck',
            # 'hirelings', 'landmarks', 'tweaks', 
        )
    
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

    queryset = queryset.prefetch_related(
        'efforts__player', 'efforts__faction', 'efforts__vagabond',
        'round__tournament', 'hirelings', 'landmarks', 'tweaks',
        'map', 'deck', 'undrafted_faction', 'undrafted_vagabond'
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

    queryset = queryset.prefetch_related(
        'efforts__player', 'efforts__faction', 'efforts__vagabond',
        'round__tournament', 'hirelings', 'landmarks', 'tweaks',
        'map', 'deck', 'undrafted_faction', 'undrafted_vagabond'
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
    }

    template_name = 'the_warroom/partials/game_list_home.html' if getattr(request, 'htmx', False) else 'the_warroom/my_submitted_games.html'

    response = render(request, template_name, context)

    return response


# @player_onboard_required
def game_detail_view(request, id=None, league_id=None):
    language_code = get_language()

    if id:
        game = get_object_or_404(Game, id=id)
    elif league_id:
        game = get_object_or_404(Game, league_id=league_id)
    else:
        raise Http404('Game not found.')

    participants = []
    efforts = []
    scorecard_count = 0
    show_detail = False

    for effort in game.efforts.all():
        participants.append(effort.player)
    if game.recorder:
        participants.append(game.recorder)

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

    scorecard_count = ScoreCard.objects.filter(final=True, effort__in=game.efforts.all()).distinct().count()
    if scorecard_count != 0:
        show_detail = True

    if request.user.is_authenticated:
        for effort in efforts:
            effort.available_scorecard = effort.available_scorecard(request.user)
        if game.final and (request.user.profile in participants):
            show_detail = True

    tournament_round = game.round

    if tournament_round:
        if tournament_round.get_tournament().open_roster:
            # Open roster - all players
            all_players = Profile.objects.all()
            open_roster = True
        else:
            # Closed roster - use round's player queryset
            all_players = tournament_round.current_player_queryset()
            open_roster = False
    else:
        # No tournament
        all_players = Profile.objects.all()
        open_roster = True

    can_edit = (
        request.user.is_authenticated and 
        (request.user.profile.admin or game.recorder == request.user.profile)
    )

    commentform = GameCommentCreateForm()
    context = {
        'game': game,
        'commentform': commentform,
        'participants': participants,
        'efforts': efforts,
        'scorecard_count': scorecard_count,
        'show_detail': show_detail,
        'can_edit': can_edit,
        'all_players': all_players,
        'open_roster': open_roster,
    }
    return render(request, "the_warroom/game_detail_page.html", context)

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
    if obj.final and not profile.admin:
        messages.error(request, "Game cannot be deleted.")
        return redirect(obj.get_absolute_url())
    elif not profile.admin and profile != obj.recorder:
        messages.error(request, "You do not have permission to delete this game.")
        return redirect(obj.get_absolute_url())    

    if request.method == "POST":
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



class GameUpdateView(LoginRequiredMixin, UserPassesTestMixin, UpdateView):
    model = Game
    form_class = GameInfoUpdateForm
    template_name = 'the_warroom/game_update.html'  # Customize this as needed
    context_object_name = 'game'  # Optional, if you want to use it in your template
    
    def get_object(self, queryset=None):
        """
        Override get_object to ensure the logged-in user is the recorder of the game.
        """
        game = super().get_object(queryset)
        
        return game
    
    def test_func(self):
        obj = self.get_object()
        # Only allow access if the logged-in user is the designer of the object
        return self.request.user.profile == obj.recorder


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


def _can_record_match(profile, match):
    """Check if profile can record a game for a match."""
    tournament = match.round.get_tournament()
    if profile.admin:
        return True
    if tournament.has_permission(profile):
        return True
    return _get_match_profiles(match).filter(pk=profile.pk).exists()


def _can_edit_game(profile, game):
    """Check if profile can edit an existing game."""
    if profile.admin:
        return True
    # Match-linked game: tournament staff always, participants if non-final
    try:
        match = game.match
        if match:
            tournament = match.round.get_tournament()
            if tournament.has_permission(profile):
                return True
            if not game.final:
                return _get_match_profiles(match).filter(pk=profile.pk).exists()
            return False
    except Match.DoesNotExist:
        pass
    # Round-linked game: recorder or tournament staff
    if game.round:
        tournament = game.round.get_tournament()
        if profile == game.recorder or tournament.has_permission(profile):
            return True
        return False
    # Standalone game: recorder only
    return profile == game.recorder


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
        if not _can_edit_game(user.profile, obj):
            messages.error(request, "You do not have permission to edit this game.")
            return redirect(obj.get_absolute_url())

    # Prepopulate round from query param (standalone mode)
    round_id = request.GET.get('series-round')
    selected_round = None
    if round_id:
        try:
            selected_round = Round.objects.get(id=round_id)
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
        if not id and not request.POST:
            for i, seat in enumerate(match_seats):
                if i < len(formset.forms):
                    profile_obj = seat.stage_participant.tournament_player.profile
                    formset.forms[i].initial['player'] = profile_obj.pk

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
        form.fields['nickname'].initial = (match.name or '')[:50]

    # Determine platform lock status for template rendering
    platform_locked = False
    locked_platform = None
    link_required = False
    if match:
        tournament = match.round.get_tournament()
        if tournament.platform:
            platform_locked = True
            locked_platform = tournament.platform
        link_required = tournament.link_required
    elif obj and obj.pk and obj.round:
        tournament = obj.round.get_tournament()
        if tournament.platform:
            platform_locked = True
            locked_platform = tournament.platform
        link_required = tournament.link_required

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
        'link_required': link_required,
    }

    if request.method == 'POST':
        if form.is_valid() and formset.is_valid():
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

            parent.status = StatusChoices(game_status)
            parent.save()

            # Match linkage
            if match_mode and match:
                if not match.game_id or match.game_id != parent.id:
                    match.game = parent
                match.status = CompetitionStatus.COMPLETED if parent.final else CompetitionStatus.ACTIVE
                match.save()

                # Trigger series/round completion logic
                if parent.final:
                    from the_warroom.services.bracket import BracketService
                    BracketService.on_game_complete(match)

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
                'game', 'game__deck', 'game__map', 'game__round', 'game__round__tournament', 'game__round', 'game__landmarks', 'game__tweaks', 'game__hirelings',
                'game__efforts', 'game__efforts__faction', 'game__efforts__player', 'game__efforts__vagabond').distinct()
    else:
        available_efforts = Effort.objects.filter(
                Q(game__efforts__player=request.user.profile) |  # Player is linked to the game
                Q(game__recorder=request.user.profile),  # Recorder is the current user
                scorecard=None, faction=scorecard.faction, 
                dominance__isnull=False # Effort has no associated scorecard
            ).prefetch_related(
                'game', 'game__deck', 'game__map', 'game__round', 'game__round__tournament', 'game__round', 'game__landmarks', 'game__tweaks', 'game__hirelings',
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


def _tournament_base_context(request, tournament):
    """Shared context for all tournament pages."""
    if request.user.is_authenticated:
        active_rounds = Round.objects.filter(
            stage__tournament=tournament,
        ).filter(
            Q(end_date__gt=timezone.now()) | Q(end_date__isnull=True),
            start_date__lt=timezone.now()
        )
        playable_rounds = active_rounds.filter(
            Q(stage__participants__tournament_player__profile=request.user.profile) |
            Q(tournament__designer=request.user.profile) |
            Q(tournament__moderators=request.user.profile)
        ).distinct()
        playable_round = playable_rounds.last()
    else:
        playable_round = None

    can_manage = request.user.is_authenticated and tournament.has_permission(request.user.profile)

    return {
        'tournament': tournament,
        'object': tournament,
        'playable_round': playable_round,
        'can_manage': can_manage,
        'meta_title': tournament.name,
        'meta_description': tournament.description,
    }


def _stage_base_context(request, tournament, stage):
    """Shared context for all stage tab pages."""
    playable_round = None
    if request.user.is_authenticated:
        active_rounds = stage.rounds.filter(
            Q(end_date__gt=timezone.now()) | Q(end_date__isnull=True),
            start_date__lt=timezone.now()
        )
        for r in active_rounds:
            if user_can_access_round(r, request.user):
                playable_round = r
                break

    can_manage = request.user.is_authenticated and tournament.has_permission(request.user.profile)
    has_bracket = Match.objects.filter(round__stage=stage).exists()

    return {
        'tournament': tournament,
        'stage': stage,
        'object': stage,
        'playable_round': playable_round,
        'can_manage': can_manage,
        'has_bracket': has_bracket,
        'meta_title': f"{stage.name} - {tournament.name}",
        'meta_description': tournament.description or '',
    }


def _round_base_context(request, tournament, stage, round):
    """Shared context for all round tab pages."""
    playable_round = None
    if request.user.is_authenticated:
        if user_can_access_round(round, request.user):
            playable_round = round

    can_manage = request.user.is_authenticated and tournament.has_permission(request.user.profile)
    has_matches = MatchSeries.objects.filter(round=round).exists()

    return {
        'tournament': tournament,
        'stage': stage,
        'round': round,
        'object': round,
        'playable_round': playable_round,
        'can_manage': can_manage,
        'has_matches': has_matches,
        'meta_title': f"{round.name} - {stage.name} - {tournament.name}",
        'meta_description': tournament.description or '',
    }


def tournament_detail_view(request, slug):
    tournament = get_object_or_404(Tournament, slug=slug.lower())

    stages = Stage.objects.filter(tournament=tournament).prefetch_related(
        Prefetch('rounds', queryset=Round.objects.order_by('round_number'))
    ).order_by('order')

    context = _tournament_base_context(request, tournament)
    context['stages'] = stages
    context['active_page'] = 'overview'

    return render(request, 'the_warroom/tournament_overview.html', context)


def tournament_leaderboard_page(request, slug):
    tournament = get_object_or_404(Tournament, slug=slug.lower())

    games_qs = Game.objects.filter(
        round__stage__tournament=tournament, final=True
    ).prefetch_related(
        'efforts__player', 'efforts__faction', 'efforts__vagabond', 'map', 'deck'
    )

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

    top_players = Profile.leaderboard(limit=leaderboard_places, effort_qs=efforts, game_threshold=leaderboard_threshold, as_json=False)
    most_players = Profile.leaderboard(limit=leaderboard_places, effort_qs=efforts, top_quantity=True, game_threshold=leaderboard_threshold, as_json=False)
    top_factions = Faction.leaderboard(limit=leaderboard_places, effort_qs=efforts, game_threshold=leaderboard_threshold, as_json=False)
    most_factions = Faction.leaderboard(limit=leaderboard_places, effort_qs=efforts, top_quantity=True, game_threshold=leaderboard_threshold, as_json=False)

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

    games_qs = Game.objects.filter(
        round__stage__tournament=tournament, final=True
    ).prefetch_related(
        'efforts__player', 'efforts__faction', 'efforts__vagabond',
        'round__stage', 'map', 'deck'
    ).order_by('-date_posted')

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

    return render(request, 'the_warroom/tournament_details.html', context)


def tournament_players_pagination(request, id):
    if not request.htmx:
        return HttpResponse(status=404)
    tournament = get_object_or_404(Tournament, id=id)

    players = Profile.objects.filter(Q(efforts__game__round__stage__tournament=tournament)|Q(tournament_participations__tournament=tournament))

    players = players.annotate(
        total_efforts=Count('efforts', distinct=True, filter=Q(efforts__game__round__stage__tournament=tournament, efforts__game__final=True)),
        win_count=Count('efforts', distinct=True, filter=Q(efforts__win=True, efforts__game__round__stage__tournament=tournament, efforts__game__final=True)),
        coalition_count=Count('efforts', distinct=True, filter=Q(efforts__win=True, efforts__game__coalition_win=True, efforts__game__round__stage__tournament=tournament, efforts__game__final=True))
    )
    # Annotate with win_rate after filtering
    players = players.annotate(
        win_rate=Case(
            When(total_efforts=0, then=Value(0)),
            default=ExpressionWrapper(
                (Cast(F('win_count'), FloatField()) - (Cast(F('coalition_count'), FloatField()) / 2)) / Cast(F('total_efforts'), FloatField()) * 100,  # Win rate as percentage
                output_field=FloatField()
            ),
            output_field=FloatField()
        ),
        tourney_points=Case(
            When(total_efforts=0, then=Value(0)),
            default=ExpressionWrapper(
                Cast(F('win_count'), FloatField()) - (Cast(F('coalition_count'), FloatField()) / 2),  # Points - 0.5 for coalitions
                output_field=FloatField()
            ),
            output_field=FloatField()
        )
    ).order_by('-total_efforts', '-tourney_points', '-win_rate', 'display_name')

    # Paginate players
    paginator = Paginator(players, settings.PAGE_SIZE)  # Use the queryset directly
    page_number = request.GET.get('page', 1)   # Get the page number from the request

    try:
        page_obj = paginator.get_page(page_number)  # Get the specific page of players
    except EmptyPage:
        page_obj = paginator.page(paginator.num_pages)  # Redirect to the last page if invalid

    return render(request, 'the_warroom/partials/player_list.html', {'players': page_obj, 'object': tournament})

    
@admin_required_class_based_view  
class TournamentDeleteView(DeleteView):
    model = Tournament
    success_url = reverse_lazy('tournaments-home')  # Redirect to the tournament list or a suitable page

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
                tournament.classification = Tournament.ClassificationTypes.GROUP
                tournament.designer = request.user.profile
                tournament.guild = None

            # Clear default_format if classification is not Tournament
            if tournament.classification != Tournament.ClassificationTypes.TOURNAMENT:
                tournament.default_format = ''

            use_stages = form.cleaned_data.get('use_stages', False)
            use_rounds = form.cleaned_data.get('use_rounds', False)
            tournament.use_stages = use_stages
            tournament.use_rounds = use_rounds
            tournament.save()
            form.save_m2m()  # Save ManyToMany relationships

            # Set default assets based on asset_mode and platform
            set_default_tournament_assets(tournament)

            # Determine names for auto-created Stage and Round
            stage_name = form.cleaned_data.get('stage_name') or 'Stage 1' if use_stages else 'Stage 1'
            round_name = (form.cleaned_data.get('round_name') or 'Round 1') if (use_stages and use_rounds) else 'Round 1'

            # Create the initial Stage and Round 1
            stage_kwargs = dict(
                tournament=tournament,
                name=stage_name,
                order=1,
            )
            if tournament.classification == Tournament.ClassificationTypes.TOURNAMENT:
                stage_kwargs['stage_format'] = tournament.default_format or FormatChoices.CUSTOM
                stage_kwargs['advancement_type'] = Stage.StageAdvancementType.ROUND_BATCH
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
                tournament.guild = original.guild

            # Clear default_format if classification is not Tournament
            if tournament.classification != Tournament.ClassificationTypes.TOURNAMENT:
                tournament.default_format = ''

            # Enforce locking: cannot unset use_stages if 2+ stages exist
            stage_count = original.stages.count()
            if stage_count >= 2:
                tournament.use_stages = True

            # Enforce locking: cannot unset use_rounds if any stage has 2+ rounds
            max_round_count = max((s.rounds.count() for s in original.stages.all()), default=0)
            if max_round_count >= 2:
                tournament.use_rounds = True

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

    stage_count = tournament.stages.count()
    max_round_count = max((s.rounds.count() for s in tournament.stages.all()), default=0)
    context = {
        'form': form,
        'tournament': tournament,
        'is_admin': request.user.profile.admin,
        'action': 'Update',
        'use_stages_locked': stage_count >= 2,
        'use_rounds_locked': max_round_count >= 2,
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


@player_required
def tournament_search_moderators(request, slug):
    """HTMX endpoint: Search for players to add as moderators"""
    tournament = get_object_or_404(Tournament, slug=slug)

    # Only designer and admin can manage moderators
    if not (request.user.profile == tournament.designer or request.user.profile.admin):
        return HttpResponse("Permission denied", status=403)

    query = request.GET.get('q', '')

    # Get profiles not already moderators and not the designer
    candidates = Profile.objects.exclude(
        pk__in=tournament.moderators.values_list('pk', flat=True)
    )
    if tournament.designer:
        candidates = candidates.exclude(pk=tournament.designer.pk)

    if query:
        candidates = candidates.filter(
            Q(discord__icontains=query) |
            Q(display_name__icontains=query)
        )
    else:
        candidates = candidates.none()

    candidates = candidates[:10]

    return render(request, 'the_warroom/partials/moderator_search_results.html', {
        'players': candidates,
        'tournament': tournament,
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
        if effective_status == TournamentPlayer.StatusChoices.REGISTERED:
            tournament.add_player(player)
        else:
            tournament.add_player_to_tournament(player, status=effective_status)
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
    round = get_object_or_404(Round, slug=round_slug, tournament=tournament)

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
    round = get_object_or_404(Round, slug=round_slug, tournament=tournament)

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
        if effective_status == TournamentPlayer.StatusChoices.REGISTERED:
            tournament.add_player(player)
        else:
            tournament.add_player_to_tournament(player, status=effective_status)
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


def tournaments_home(request):
    scheduled_tournaments = Tournament.objects.filter(start_date__gt=timezone.now())
    scheduled_tournaments = scheduled_tournaments.annotate(
    unique_players_count=Count('rounds__games__efforts__player', distinct=True)
    )


    concluded_tournaments = Tournament.objects.filter(end_date__lt=timezone.now())
    concluded_tournaments = concluded_tournaments.annotate(
    unique_players_count=Count('rounds__games__efforts__player', distinct=True)
    )

    ongoing_tournaments = Tournament.objects.filter(
        Q(end_date__gt=timezone.now()) | Q(end_date__isnull=True),
        start_date__lt=timezone.now()
    )
    ongoing_tournaments = ongoing_tournaments.annotate(
    unique_players_count=Count('rounds__games__efforts__player', distinct=True)
    )

    # all_tournaments = Tournament.objects.all()

    # Get user's tournaments (where they are designer or have played)
    user_tournaments = None
    if request.user.is_authenticated:
        user_tournaments = Tournament.objects.filter(
            Q(designer=request.user.profile) |
            Q(moderators=request.user.profile) |
            Q(rounds__games__efforts__player=request.user.profile)
        ).distinct().annotate(
            unique_players_count=Count('rounds__games__efforts__player', distinct=True)
        )
    print(user_tournaments)
    # Theme
    theme = get_theme(request)
    background_image, foreground_images, theme_artists, background_pattern = get_thematic_images(
        theme=theme, page='games'
    )

    context = {
        'scheduled': scheduled_tournaments,
        'concluded': concluded_tournaments,
        'ongoing': ongoing_tournaments,
        'user_tournaments': user_tournaments,
        # 'all': all_tournaments,
        'background_image': background_image,
        'foreground_images': foreground_images,
        'background_pattern': background_pattern,
    }
    return render(request, 'the_warroom/tournaments_home.html', context)













# ============================
# Round Views (Individual Round/Season of a Tournament)
# ============================
def user_can_access_round(tournament_round, user):
    from the_warroom.models import StageParticipant
    now = timezone.now()
    is_active = (tournament_round.start_date < now) and (tournament_round.end_date is None or tournament_round.end_date > now)
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



def round_detail_view(request, tournament_slug, stage_slug, round_slug):
    tournament = get_object_or_404(Tournament, slug=tournament_slug.lower())
    stage = get_object_or_404(Stage, slug=stage_slug, tournament=tournament)
    round = get_object_or_404(Round, slug=round_slug.lower(), stage=stage, tournament=tournament)

    context = _round_base_context(request, tournament, stage, round)
    context['active_page'] = 'overview'

    return render(request, 'the_warroom/round_overview.html', context)



def round_component_leaderboard_view(request, tournament_slug, post_slug, round_slug=None):

    threshold = get_int_param(request.GET.get('threshold', ''))
    limit = get_int_param(request.GET.get('limit', ''))
    if limit is not None:
        limit = min(limit, 50)


    # Get the tournament from slug
    tournament = get_object_or_404(Tournament, slug=tournament_slug.lower())
    # Fetch the round using its slug, and filter it by the related tournament
    if round_slug:
        round = get_object_or_404(Round, slug=round_slug.lower(), tournament=tournament)
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

    # Base filter: filter by round or tournament
    base_filter = Q()
    if round:
        base_filter &= Q(game__round=round)
    else:
        base_filter &= Q(game__round__tournament=tournament)

    # Add component-specific filter
    component = object.component
    if component == "Map":
        base_filter &= Q(game__map=object)
    elif component == "Deck":
        base_filter &= Q(game__deck=object)
    elif component == "Vagabond":
        base_filter &= Q(vagabond=object)
    elif component == "Faction":
        base_filter &= Q(faction=object)

    # Always exclude efforts with no player
    base_filter &= Q(player__isnull=False)

    efforts = Effort.objects.filter(base_filter)

    
    if not threshold:
        threshold = round.get_game_threshold() if round else tournament.game_threshold
    if not limit:
        limit = round.get_leaderboard_positions() if round else tournament.leaderboard_positions

    top_players = []
    most_players = []
    top_players = Profile.leaderboard(limit=limit, effort_qs=efforts, game_threshold=threshold)
    most_players = Profile.leaderboard(limit=limit, effort_qs=efforts, top_quantity=True, game_threshold=threshold)
    if round:
        meta_title = f'{tournament.name} - {round.name}'
        if object.component == "Deck":
            title = f'{object.title} Deck - {tournament.name} ({round.name})'
        elif object.component == "Map":
            title = f'{object.title} Map - {tournament.name} ({round.name})'
        else:
            title = f'{object.title} - {tournament.name} ({round.name})'
    else:
        meta_title = f'{tournament.name}'
        if object.component == "Deck":
            title = f'{object.title} Deck - {tournament.name}'
        elif object.component == "Map":
            title = f'{object.title} Map - {tournament.name}'
        else:
            title = f'{object.title} - {tournament.name}'
    meta_description = f'Player Leaderboard for {object.title}'


    context = {
        'selected_tournament': tournament,
        'tournament_round': round,
        'top_players': top_players,
        'most_players': most_players,
        'leaderboard_threshold': threshold,
        'meta_title': meta_title,
        'meta_description': meta_description,
        'title': title,
        'post_name': object.title,
    }
    
    return render(request, 'the_warroom/tournament_leaderboard.html', context)




def round_players_pagination(request, id):
    if not request.htmx:
        return HttpResponse(status=404)
    round = get_object_or_404(Round, id=id)

    if round.stage:
        stage_player_ids = StageParticipant.objects.filter(
            stage=round.stage
        ).values_list('tournament_player__profile_id', flat=True)
        players = Profile.objects.filter(
            Q(efforts__game__round=round) | Q(id__in=stage_player_ids)
        ).distinct()
    else:
        players = Profile.objects.filter(
            Q(efforts__game__round=round) |
            Q(tournament_participations__tournament=round.get_tournament())
        ).distinct()

    players = players.annotate(
        total_efforts=Count('efforts', distinct=True, filter=Q(efforts__game__round=round, efforts__game__final=True)),
        win_count=Count('efforts', distinct=True, filter=Q(efforts__win=True, efforts__game__round=round, efforts__game__final=True)),
        coalition_count=Count('efforts', distinct=True, filter=Q(efforts__win=True, efforts__game__coalition_win=True, efforts__game__round=round, efforts__game__final=True))
    )

    # Annotate with win_rate after filtering
    players = players.annotate(
        win_rate=Case(
            When(total_efforts=0, then=Value(0)),
            default=ExpressionWrapper(
                (Cast(F('win_count'), FloatField()) - (Cast(F('coalition_count'), FloatField()) / 2)) / Cast(F('total_efforts'), FloatField()) * 100,  # Win rate as percentage
                output_field=FloatField()
            ),
            output_field=FloatField()
        ),
        tourney_points=Case(
            When(total_efforts=0, then=Value(0)),
            default=ExpressionWrapper(
                Cast(F('win_count'), FloatField()) - (Cast(F('coalition_count'), FloatField()) / 2),  # Points - 0.5 for coalitions
                output_field=FloatField()
            ),
            output_field=FloatField()
        )
    ).order_by('-total_efforts', '-tourney_points', '-win_rate', 'display_name')



    # Paginate players
    paginator = Paginator(players, settings.PAGE_SIZE)  # Use the queryset directly
    page_number = request.GET.get('page', 1)   # Get the page number from the request

    try:
        page_obj = paginator.get_page(page_number)  # Get the specific page of players
    except EmptyPage:
        page_obj = paginator.page(paginator.num_pages)  # Redirect to the last page if invalid

    return render(request, 'the_warroom/partials/player_list.html', {'players': page_obj, 'object': round})


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
def round_manage_view(request, tournament_slug, stage_slug, round_slug=None):
    tournament = get_object_or_404(Tournament, slug=tournament_slug)
    stage = get_object_or_404(Stage, tournament=tournament, slug=stage_slug)

    if not tournament.has_permission(request.user.profile):
        messages.error(request, "You do not have permission to view this page.")
        raise PermissionDenied() 
    
    max_round_number = stage.rounds.aggregate(max_num=Max('round_number'))['max_num'] or 0
    current_round = max_round_number + 1
    
    round_instance = None
    # If round_slug is provided, update the existing round
    if round_slug:
        round_instance = get_object_or_404(Round, slug=round_slug, stage=stage, tournament=tournament)
        form = RoundCreateForm(request.POST or None, tournament=tournament, stage=stage, instance=round_instance, current_round=current_round)
    else:
        # Otherwise, create a new round
        form = RoundCreateForm(request.POST or None, tournament=tournament, stage=stage, current_round=current_round)

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
def round_manage_players(request, tournament_slug, stage_slug, round_slug):
    """Redirect to stage player management — player roster is managed at the stage level."""
    return redirect('stage-manage-players', tournament_slug=tournament_slug, stage_slug=stage_slug)


@player_required_class_based_view
class RoundDeleteView(LoginRequiredMixin, UserPassesTestMixin, DeleteView):
    model = Round

    def test_func(self):
        obj = self.get_object()
        profile = self.request.user.profile
        # Only allow deletion for admins or the tournament designer (not moderators)
        return profile.admin or profile == obj.tournament.designer
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['round'] = self.object
        context['tournament'] = self.object.tournament
        return context

    # Dynamically set the success URL based on the round's tournament
    def get_success_url(self):
        # Redirect to the tournament detail page using the tournament slug
        tournament_slug = self.object.tournament.slug
        return reverse_lazy('tournament-detail', kwargs={'slug': tournament_slug})
    
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
        

@player_onboard_required
def in_progress_view(request):
    language_code = get_language()
    language_object = Language.objects.filter(code=language_code).first()
    user_profile = request.user.profile
    prefetch_game = [
        'efforts__player', 'efforts__faction', 'efforts__vagabond', 'round__tournament', 
        'hirelings', 'landmarks', 'tweaks', 'map', 'deck'
    ]
    in_progress_games = Game.objects.filter(recorder=user_profile, final=False).prefetch_related(*prefetch_game)
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
            form = TournamentPlayerSettingsForm(request.POST, instance=tournament)
            if form.is_valid():
                form.save()
                messages.success(request, "Player settings updated.")
                return redirect('tournament-manage-players', slug=slug)
        else:
            form = TournamentPlayerSettingsForm(instance=tournament)

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
def tournament_manage_assets_v2(request, slug):
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
    return render(request, 'the_warroom/tournament_manage_assets_v2.html', context)


@player_onboard_required
def tournament_settings_hub(request, slug):
    """Settings hub page with links to all tournament management tools."""
    from the_tavern.models import Survey
    tournament = get_object_or_404(Tournament, slug=slug)

    # Permission check
    if not tournament.has_permission(request.user.profile):
        raise PermissionDenied()

    survey_count = Survey.objects.filter(series=tournament).count()

    profile = request.user.profile
    is_owner = profile.admin or profile == tournament.designer

    context = {
        'object_type': 'Series',
        'tournament': tournament,
        'object': tournament,  # For template title
        'survey_count': survey_count,
        'can_manage': is_owner,
        'is_owner': is_owner,
    }
    return render(request, 'the_warroom/settings_hub.html', context)


@player_onboard_required
def round_settings_hub(request, tournament_slug, stage_slug, round_slug):
    """Settings hub page with links to all round management tools."""
    tournament = get_object_or_404(Tournament, slug=tournament_slug)
    round = get_object_or_404(Round, slug=round_slug, tournament=tournament)

    # Permission check
    if not tournament.has_permission(request.user.profile):
        raise PermissionDenied()

    from the_tavern.models import Survey
    stage = round.stage
    survey_count = Survey.objects.filter(series=tournament, stage=stage).count() if stage else Survey.objects.filter(series=tournament).count()
    profile = request.user.profile
    is_owner = profile.admin or profile == tournament.designer

    context = {
        'tournament': tournament,
        'round': round,
        'stage': stage,
        'object': round,  # For template title
        'object_type': 'Round',
        'survey_count': survey_count,
        'can_manage': True,  # Already permission-gated above
        'is_owner': is_owner,
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
        form = StageCreateForm(request.POST or None, tournament=tournament, instance=stage_instance)
    else:
        form = StageCreateForm(request.POST or None, tournament=tournament)

    if form.is_valid():
        stage_instance = form.save(commit=False)
        if not stage_instance.pk:
            stage_instance.tournament = tournament
        stage_instance.save()

        # Save use_rounds to the tournament (locked if any stage has 2+ rounds)
        max_round_count = max((s.rounds.count() for s in tournament.stages.all()), default=0)
        if max_round_count < 2:
            tournament.use_rounds = form.cleaned_data.get('use_rounds', tournament.use_rounds)
            tournament.save(update_fields=['use_rounds'])

        return redirect(stage_instance.get_absolute_url())

    max_round_count = max((s.rounds.count() for s in tournament.stages.all()), default=0)

    # Existing stage names in this tournament for frontend duplicate validation
    existing_stage_names = list(
        tournament.stages.values_list('name', flat=True)
    )
    if stage_instance:
        existing_stage_names = [n for n in existing_stage_names if n != stage_instance.name]

    context = {
        'form': form,
        'tournament': tournament,
        'stage': stage_instance,
        'use_rounds_locked': max_round_count >= 2,
        'existing_names_json': json.dumps(existing_stage_names),
    }
    return render(request, 'the_warroom/stage_form.html', context)


@player_onboard_required
def stage_detail_view(request, tournament_slug, stage_slug):
    """Stage overview — lists rounds belonging to this stage."""
    tournament = get_object_or_404(Tournament, slug=tournament_slug)
    stage = get_object_or_404(Stage, slug=stage_slug, tournament=tournament)
    rounds = stage.rounds.all().order_by('round_number')

    context = _stage_base_context(request, tournament, stage)
    context.update({
        'rounds': rounds,
        'active_page': 'overview',
    })
    return render(request, 'the_warroom/stage_overview.html', context)


def stage_leaderboard_page(request, tournament_slug, stage_slug):
    tournament = get_object_or_404(Tournament, slug=tournament_slug)
    stage = get_object_or_404(Stage, slug=stage_slug, tournament=tournament)

    games_qs = Game.objects.filter(
        round__stage=stage, final=True
    ).prefetch_related(
        'efforts__player', 'efforts__faction', 'efforts__vagabond', 'map', 'deck'
    )

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

    top_players = Profile.leaderboard(limit=leaderboard_places, effort_qs=efforts, game_threshold=leaderboard_threshold, as_json=False)
    most_players = Profile.leaderboard(limit=leaderboard_places, effort_qs=efforts, top_quantity=True, game_threshold=leaderboard_threshold, as_json=False)
    top_factions = Faction.leaderboard(limit=leaderboard_places, effort_qs=efforts, game_threshold=leaderboard_threshold, as_json=False)
    most_factions = Faction.leaderboard(limit=leaderboard_places, effort_qs=efforts, top_quantity=True, game_threshold=leaderboard_threshold, as_json=False)

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

    games_qs = Game.objects.filter(
        round__stage=stage, final=True
    ).prefetch_related(
        'efforts__player', 'efforts__faction', 'efforts__vagabond',
        'round__stage', 'map', 'deck'
    ).order_by('-date_posted')

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
    return render(request, 'the_warroom/stage_details.html', context)


def stage_bracket_page(request, tournament_slug, stage_slug):
    tournament = get_object_or_404(Tournament, slug=tournament_slug)
    stage = get_object_or_404(Stage, slug=stage_slug, tournament=tournament)

    rounds = stage.rounds.all().order_by('round_number')
    rounds_with_matches = rounds.prefetch_related(
        'matches__series__player_group__tournament_players__profile',
        'matches__advancements__to_round',
        'series__player_group',
    )

    context = _stage_base_context(request, tournament, stage)
    context.update({
        'active_page': 'bracket',
        'rounds_with_matches': rounds_with_matches,
    })
    return render(request, 'the_warroom/stage_bracket.html', context)


# ── Round tab pages ──────────────────────────────────────────────────────────

def round_leaderboard_page(request, tournament_slug, stage_slug, round_slug):
    tournament = get_object_or_404(Tournament, slug=tournament_slug)
    stage = get_object_or_404(Stage, slug=stage_slug, tournament=tournament)
    round = get_object_or_404(Round, slug=round_slug, stage=stage, tournament=tournament)

    games_qs = Game.objects.filter(
        round=round, final=True
    ).prefetch_related(
        'efforts__player', 'efforts__faction', 'efforts__vagabond', 'map', 'deck'
    )

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

    top_players = Profile.leaderboard(limit=leaderboard_places, effort_qs=efforts, game_threshold=leaderboard_threshold, as_json=False)
    most_players = Profile.leaderboard(limit=leaderboard_places, effort_qs=efforts, top_quantity=True, game_threshold=leaderboard_threshold, as_json=False)
    top_factions = Faction.leaderboard(limit=leaderboard_places, effort_qs=efforts, game_threshold=leaderboard_threshold, as_json=False)
    most_factions = Faction.leaderboard(limit=leaderboard_places, effort_qs=efforts, top_quantity=True, game_threshold=leaderboard_threshold, as_json=False)

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


def round_games_page(request, tournament_slug, stage_slug, round_slug):
    tournament = get_object_or_404(Tournament, slug=tournament_slug)
    stage = get_object_or_404(Stage, slug=stage_slug, tournament=tournament)
    round = get_object_or_404(Round, slug=round_slug, stage=stage, tournament=tournament)

    games_qs = Game.objects.filter(
        round=round, final=True
    ).prefetch_related(
        'efforts__player', 'efforts__faction', 'efforts__vagabond',
        'round__stage', 'map', 'deck'
    ).order_by('-date_posted')

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


def round_roster_page(request, tournament_slug, stage_slug, round_slug):
    tournament = get_object_or_404(Tournament, slug=tournament_slug)
    stage = get_object_or_404(Stage, slug=stage_slug, tournament=tournament)
    round = get_object_or_404(Round, slug=round_slug, stage=stage, tournament=tournament)

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


def round_details_page(request, tournament_slug, stage_slug, round_slug):
    tournament = get_object_or_404(Tournament, slug=tournament_slug)
    stage = get_object_or_404(Stage, slug=stage_slug, tournament=tournament)
    round = get_object_or_404(Round, slug=round_slug, stage=stage, tournament=tournament)

    context = _round_base_context(request, tournament, stage, round)
    context['active_page'] = 'details'
    return render(request, 'the_warroom/round_details.html', context)


def round_matches_page(request, tournament_slug, stage_slug, round_slug):
    tournament = get_object_or_404(Tournament, slug=tournament_slug)
    stage = get_object_or_404(Stage, slug=stage_slug, tournament=tournament)
    round = get_object_or_404(Round, slug=round_slug, stage=stage, tournament=tournament)

    match_series = MatchSeries.objects.filter(round=round).select_related(
        'player_group',
    ).prefetch_related(
        'winners__tournament_player__profile',
        'matches__game',
        'player_group__tournament_players__profile',
    ).order_by('id')

    recordable_match_ids = set()
    if request.user.is_authenticated:
        profile = request.user.profile
        if profile.admin or tournament.has_permission(profile):
            recordable_match_ids = set(
                Match.objects.filter(round=round).values_list('id', flat=True)
            )
        else:
            participant_series_ids = MatchSeat.objects.filter(
                series__round=round,
                stage_participant__tournament_player__profile=profile
            ).values_list('series_id', flat=True)
            recordable_match_ids = set(
                Match.objects.filter(
                    round=round, series_id__in=participant_series_ids
                ).values_list('id', flat=True)
            )

    context = _round_base_context(request, tournament, stage, round)
    context.update({
        'active_page': 'matches',
        'match_series': match_series,
        'recordable_match_ids': recordable_match_ids,
    })

    return render(request, 'the_warroom/round_matches.html', context)


@player_onboard_required
def stage_settings_hub(request, tournament_slug, stage_slug):
    """Settings hub page with links to all stage management tools."""
    from the_tavern.models import Survey
    tournament = get_object_or_404(Tournament, slug=tournament_slug)
    stage = get_object_or_404(Stage, slug=stage_slug, tournament=tournament)

    if not tournament.has_permission(request.user.profile):
        raise PermissionDenied()

    survey_count = Survey.objects.filter(series=tournament, stage=stage).count()
    profile = request.user.profile
    is_owner = profile.admin or profile == tournament.designer

    context = {
        'tournament': tournament,
        'stage': stage,
        'object': stage,
        'object_type': 'Stage',
        'survey_count': survey_count,
        'can_manage': True,  # Already permission-gated above
        'is_owner': is_owner,
    }
    return render(request, 'the_warroom/settings_hub.html', context)


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

    context = {
        'tournament': tournament,
        'stage': stage,
        'object': stage,
        'participants': participants,
        'eliminated_participants': eliminated_participants,
        'withdrawn_participants': withdrawn_participants,
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

    available_players = available_players[:10]

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


@player_onboard_required
def tournament_bracket_view(request, slug):
    """Read-only bracket overview — shows all stages and their bracket status."""
    tournament = get_object_or_404(Tournament, slug=slug)

    if not tournament.has_permission(request.user.profile):
        raise PermissionDenied()

    stages = tournament.stages.order_by('order').prefetch_related(
        Prefetch(
            'rounds',
            queryset=Round.objects.order_by('round_number').prefetch_related(
                'matches__series__player_group__tournament_players__profile',
                'matches__series__winners__tournament_player__profile',
            ),
        ),
    )

    has_bracket = Match.objects.filter(round__stage__tournament=tournament).exists()

    context = {
        'tournament': tournament,
        'stages': stages,
        'has_bracket': has_bracket,
    }
    return render(request, 'the_warroom/tournament_bracket.html', context)


# Round Grouping Views

@login_required
def round_grouping_setup_view(request, tournament_slug, stage_slug, round_slug):
    """
    Round grouping view - shows setup form and organize interface.
    Each round is linked to a Stage which holds the grouping configuration.
    """
    tournament = get_object_or_404(Tournament, slug=tournament_slug)
    round = get_object_or_404(Round, slug=round_slug, tournament=tournament)
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

        grouped_ids = set(
            TournamentPlayer.objects.filter(
                player_groups__round=round
            ).values_list('id', flat=True)
        )
        ungrouped_players = active_players_qs.exclude(id__in=grouped_ids).select_related('profile')

    return_to = round.get_absolute_url()

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
        'return_to': return_to,
        'naming_conventions': NameConvention.choices,
        'session': stage,
        'base_url': reverse('round-grouping-setup', kwargs={
            'tournament_slug': tournament.slug,
            'stage_slug': stage.slug,
            'round_slug': round.slug,
        }) if stage else '',
        # Bracket config context
        'round_format': round.get_format(),
        'has_matches': round.matches.exists(),
        'match_count': round.matches.count(),
        'bracket_series': MatchSeries.objects.filter(round=round).select_related('player_group').order_by('id') if is_finalized else [],
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
    return render(request, 'the_warroom/round_grouping.html', context)


# Round Grouping AJAX Views

@login_required
@require_http_methods(['GET'])
def round_grouping_status(request, tournament_slug, stage_slug, round_slug, session_id):
    """Check session status (for HTMX polling)."""
    tournament = get_object_or_404(Tournament, slug=tournament_slug)
    round = get_object_or_404(Round, slug=round_slug, tournament=tournament)
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
def round_grouping_move_player(request, tournament_slug, stage_slug, round_slug, session_id):
    """Move a player from one group to another."""
    from django.http import JsonResponse

    tournament = get_object_or_404(Tournament, slug=tournament_slug)
    round = get_object_or_404(Round, slug=round_slug, tournament=tournament)
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
            }
        to_group.refresh_from_db()
        response['to_group'] = {
            'id': to_group.id,
            'member_count': to_group.member_count,
            'total_overlap_hours': to_group.total_overlap_hours,
            'best_consecutive_block': to_group.best_consecutive_block,
        }
        return JsonResponse(response)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=400)


@login_required
@require_http_methods(['POST'])
def round_grouping_add_to_group(request, tournament_slug, stage_slug, round_slug, session_id):
    """Add an ungrouped or waitlisted player to a group."""
    from django.http import JsonResponse

    tournament = get_object_or_404(Tournament, slug=tournament_slug)
    round = get_object_or_404(Round, slug=round_slug, tournament=tournament)
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

        GroupingService.assign_player_to_group(tournament_player, group, round=round, moved_by=profile)

        group.refresh_from_db()
        return JsonResponse({
            'success': True,
            'group': {
                'id': group.id,
                'member_count': group.member_count,
                'total_overlap_hours': group.total_overlap_hours,
                'best_consecutive_block': group.best_consecutive_block,
            }
        })
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=400)


@login_required
@require_http_methods(['POST'])
def round_grouping_remove_from_group(request, tournament_slug, stage_slug, round_slug, session_id):
    """Remove a player from a group and return them to ungrouped status."""
    from django.http import JsonResponse

    tournament = get_object_or_404(Tournament, slug=tournament_slug)
    round = get_object_or_404(Round, slug=round_slug, tournament=tournament)
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
        old_group = get_object_or_404(PlayerGroup, id=group_id, round=round) if group_id else None

        GroupingService.remove_from_group(tournament_player, round=round)

        result = {'success': True}
        if old_group:
            old_group.refresh_from_db()
            result['from_group'] = {
                'id': old_group.id,
                'member_count': old_group.member_count,
                'total_overlap_hours': old_group.total_overlap_hours,
                'best_consecutive_block': old_group.best_consecutive_block,
            }

        return JsonResponse(result)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=400)


@login_required
@require_http_methods(['POST'])
def round_grouping_create_group(request, tournament_slug, stage_slug, round_slug, session_id):
    """Create a new empty group for a round."""
    from django.http import JsonResponse

    tournament = get_object_or_404(Tournament, slug=tournament_slug)
    round = get_object_or_404(Round, slug=round_slug, tournament=tournament)
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

        # If player_id provided, add that player to the new group
        if player_id:
            tournament_player = TournamentPlayer.objects.filter(
                tournament=stage.tournament,
                id=player_id
            ).first()
            if tournament_player:
                GroupingService.assign_player_to_group(tournament_player, new_group, round=round, moved_by=profile)

        return JsonResponse({
            'success': True,
            'group': {
                'id': new_group.id,
                'group_number': new_group.group_number,
                'member_count': new_group.member_count,
            }
        })
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=400)


@login_required
@require_http_methods(['POST'])
def round_grouping_delete_group(request, tournament_slug, stage_slug, round_slug, session_id):
    """Delete an empty group."""
    from django.http import JsonResponse

    tournament = get_object_or_404(Tournament, slug=tournament_slug)
    round = get_object_or_404(Round, slug=round_slug, tournament=tournament)
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


@login_required
@require_http_methods(['POST'])
def round_grouping_finalize(request, tournament_slug, stage_slug, round_slug, session_id):
    """Finalize the grouping session."""
    from django.http import JsonResponse

    tournament = get_object_or_404(Tournament, slug=tournament_slug)
    round = get_object_or_404(Round, slug=round_slug, tournament=tournament)
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

    tournament = get_object_or_404(Tournament, slug=tournament_slug)
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

    losers_stage = None
    losers_stage_id = data.get('losers_stage_id')
    losers_stage_name = data.get('losers_stage_name', '').strip()

    if losers_stage_id:
        losers_stage = get_object_or_404(Stage, id=losers_stage_id, tournament=tournament)
    elif losers_stage_name:
        max_order = tournament.stages.aggregate(max_order=models.Max('order'))['max_order'] or 0
        losers_stage = Stage.objects.create(
            tournament=tournament,
            name=losers_stage_name,
            order=max_order + 1,
            stage_format=round.get_format(),
            status='Pending',
        )

    try:
        warnings = BracketService.generate_round_bracket(round, best_of=best_of, losers_stage=losers_stage, create_byes=create_byes)
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

    tournament = get_object_or_404(Tournament, slug=tournament_slug)
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