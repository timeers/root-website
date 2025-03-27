import random
# import logging
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
from django.db import IntegrityError
from django.db.models import Count, F, ExpressionWrapper, FloatField, Q, Case, When, Value, ProtectedError, Prefetch
from django.db.models.functions import Cast
from django.utils import timezone 
from urllib.parse import quote

from .models import Game, Effort, TurnScore, ScoreCard, Round, Tournament
from .forms import (GameCreateForm, GameInfoUpdateForm, EffortCreateForm, 
                    TurnScoreCreateForm, ScoreCardCreateForm, AssignScorecardForm, AssignEffortForm,
                    TournamentCreateForm, RoundCreateForm, 
                    TournamentManagePlayersForm, TournamentManageAssetsForm,
                    RoundManagePlayersForm)
from .filters import GameFilter, PlayerGameFilter

from the_keep.models import Faction, Deck, Map, Vagabond, Hireling, Landmark, Tweak, StatusChoices

from the_gatehouse.models import Profile, BackgroundImage, ForegroundImage
from the_gatehouse.views import (player_required, admin_required, 
                                 admin_required_class_based_view, player_required_class_based_view,
                                 tester_required, player_onboard_required, admin_onboard_required)
from the_gatehouse.forms import PlayerCreateForm
from the_gatehouse.discordservice import send_discord_message
from the_gatehouse.utils import get_uuid

from the_tavern.forms import GameCommentCreateForm
from the_tavern.views import bookmark_toggle






# ============================
# Game Views
# ============================

#  A list of all the games. Most recent update first

class GameListView(ListView):
    # queryset = Game.objects.all().prefetch_related('efforts')
    model = Game
    # template_name = 'the_warroom/games_home.html' # <app>/<model>_<viewtype>.html
    context_object_name = 'games'
    ordering = ['-date_posted']
    paginate_by = settings.PAGE_SIZE

    def get_template_names(self):
        if self.request.htmx:
            return 'the_warroom/partials/game_list_home.html'
        
        if self.request.user.is_authenticated:
            send_discord_message(f'{self.request.user} on Game Page')
        else:
            send_discord_message(f'{get_uuid(self.request)} on Game Page')
        return 'the_warroom/games_home.html'
    
    def get_queryset(self):
        
        if self.request.user.is_authenticated:
            if self.request.user.profile.weird:
                queryset = Game.objects.filter(final=True).prefetch_related(
                    'efforts__player', 'efforts__faction', 'efforts__vagabond', 'round__tournament', 
                    'hirelings', 'landmarks', 'tweaks', 'map', 'deck', 'undrafted_faction', 'undrafted_vagabond'
                    )
                # queryset = super().get_queryset()
            else:
                queryset = Game.objects.filter(official=True, final=True).prefetch_related(
                    'efforts__player', 'efforts__faction', 'efforts__vagabond', 'round__tournament', 
                    'hirelings', 'landmarks', 'tweaks', 'map', 'deck', 'undrafted_faction', 'undrafted_vagabond'
                    )
                # queryset = super().get_queryset().only_official_components()
        else:
            queryset = Game.objects.filter(final=True).prefetch_related(
                'efforts__player', 'efforts__faction', 'efforts__vagabond', 'round__tournament', 
                'hirelings', 'landmarks', 'tweaks', 'map', 'deck', 'undrafted_faction', 'undrafted_vagabond'
                )
            # queryset = super().get_queryset()
        self.filterset = GameFilter(self.request.GET, queryset=queryset, user=self.request.user)

        # # Store the filtered queryset in an instance variable to avoid re-evaluating it
        self._cached_queryset = self.filterset.qs

        return self.filterset.qs
        
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        # Create a dictionary to collect context values
        context_data = {}

        if self.request.user.is_authenticated:
            profile = self.request.user.profile
            theme = self.request.user.profile.theme
        else:
            profile = None
            theme = None

        background_image = BackgroundImage.objects.filter(theme=theme, page="games").order_by('?').first()
        # foreground_images = ForegroundImage.objects.filter(theme=theme, page="games")
        all_foreground_images = ForegroundImage.objects.filter(theme=theme, page="games")
        # Group the images by location
        grouped_by_location = groupby(sorted(all_foreground_images, key=lambda x: x.location), key=lambda x: x.location)
        # Select a random image from each location
        foreground_images = [random.choice(list(group)) for _, group in grouped_by_location]
        context['background_image'] = background_image
        context['foreground_images'] = foreground_images



        in_progress = Game.objects.filter(final=False, recorder=profile)
        
        # Reuse the cached queryset here instead of calling get_queryset again
        games = self._cached_queryset  # Use the already-evaluated queryset
        # Get the total count of games
        games_count = games.count()
        efforts = Effort.objects.filter(game__in=games)
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

        # Get leaderboard data
        context_data.update({
            'top_players': Profile.leaderboard(limit=10, effort_qs=efforts, game_threshold=leaderboard_threshold),
            'most_players': Profile.leaderboard(limit=10, effort_qs=efforts, top_quantity=True, game_threshold=leaderboard_threshold),
            'top_factions': Faction.leaderboard(limit=10, effort_qs=efforts, game_threshold=leaderboard_threshold),
            'most_factions': Faction.leaderboard(limit=10, effort_qs=efforts, top_quantity=True, game_threshold=leaderboard_threshold),
            'leaderboard_threshold': leaderboard_threshold,
        })
        


        # Paginate games
        paginator = Paginator(games, self.paginate_by)  # Use the queryset directly
        page_number = self.request.GET.get('page')  # Get the page number from the request

        try:
            page_obj = paginator.get_page(page_number)  # Get the specific page of games
        except EmptyPage:
            page_obj = paginator.page(paginator.num_pages)  # Redirect to the last page if invalid
        
        # Add paginated data to the context dictionary
        context_data.update({
            'in_progress': in_progress,
            'games': page_obj,  # Pass the paginated page object to the context
            'is_paginated': paginator.num_pages > 1,  # Set is_paginated boolean
            'page_obj': page_obj,  # Pass the page_obj to the context
            'games_count': games_count,
            'form': self.filterset.form,
            'filterset': self.filterset,
        })

        # Update the main context with the collected context data
        context.update(context_data)

        return context

@player_required_class_based_view  
class PlayerGameListView(ListView):
    # queryset = Game.objects.all().prefetch_related('efforts')
    model = Game
    # template_name = 'the_warroom/games_home.html' # <app>/<model>_<viewtype>.html
    context_object_name = 'games'
    ordering = ['-date_posted']
    paginate_by = settings.PAGE_SIZE

    def get_template_names(self):
        if self.request.htmx:
            return 'the_warroom/partials/game_list_home.html'
        
        return 'the_warroom/player_games.html'
    
    def get_queryset(self):
        # Fetch player based on slug if present in URL
        player_slug = self.kwargs.get('slug')
        player = None
        if player_slug:
            # Use get_object_or_404 to ensure player exists
            player = get_object_or_404(Profile, slug=player_slug)
        if self.request.user.is_authenticated:
            if self.request.user.profile.weird:
                queryset = Game.objects.filter(final=True).prefetch_related(
                    'efforts__player', 'efforts__faction', 'efforts__vagabond', 'round__tournament', 
                    'hirelings', 'landmarks', 'tweaks', 'map', 'deck', 'undrafted_faction', 'undrafted_vagabond'
                    ).distinct()
                # queryset = super().get_queryset()
            else:
                queryset = Game.objects.filter(official=True, final=True).prefetch_related(
                    'efforts__player', 'efforts__faction', 'efforts__vagabond', 'round__tournament', 
                    'hirelings', 'landmarks', 'tweaks', 'map', 'deck', 'undrafted_faction', 'undrafted_vagabond'
                    ).distinct()
                # queryset = super().get_queryset().only_official_components()
        else:
            queryset = Game.objects.filter(final=True).prefetch_related(
                'efforts__player', 'efforts__faction', 'efforts__vagabond', 'round__tournament', 
                'hirelings', 'landmarks', 'tweaks', 'map', 'deck', 'undrafted_faction', 'undrafted_vagabond'
                ).distinct()
            # queryset = super().get_queryset()
        self.filterset = PlayerGameFilter(self.request.GET, queryset=queryset, player=player)

        # # Store the filtered queryset in an instance variable to avoid re-evaluating it
        self._cached_queryset = self.filterset.qs
        self._player = player 

        return self.filterset.qs
        
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        # Create a dictionary to collect context values
        context_data = {}
    
        
        # Reuse the cached queryset here instead of calling get_queryset again
        games = self._cached_queryset  # Use the already-evaluated queryset
        # Get the total count of games
        games_count = games.count()
        efforts = Effort.objects.filter(game__in=games)
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

        # Get leaderboard data
        context_data.update({
            'top_players': Profile.leaderboard(limit=10, effort_qs=efforts, game_threshold=leaderboard_threshold),
            'most_players': Profile.leaderboard(limit=10, effort_qs=efforts, top_quantity=True, game_threshold=leaderboard_threshold),
            'top_factions': Faction.leaderboard(limit=10, effort_qs=efforts, game_threshold=leaderboard_threshold),
            'most_factions': Faction.leaderboard(limit=10, effort_qs=efforts, top_quantity=True, game_threshold=leaderboard_threshold),
            'leaderboard_threshold': leaderboard_threshold,
        })
        
        # Use the player stored in self._player
        player = self._player
        # Only pass player to context if it exists
        if player:
            context_data['player'] = player  # Add the player to context

        # Paginate games
        paginator = Paginator(games, self.paginate_by)  # Use the queryset directly
        page_number = self.request.GET.get('page')  # Get the page number from the request

        try:
            page_obj = paginator.get_page(page_number)  # Get the specific page of games
        except EmptyPage:
            page_obj = paginator.page(paginator.num_pages)  # Redirect to the last page if invalid
        
        # Add paginated data to the context dictionary
        context_data.update({
            'games': page_obj,  # Pass the paginated page object to the context
            'is_paginated': paginator.num_pages > 1,  # Set is_paginated boolean
            'page_obj': page_obj,  # Pass the page_obj to the context
            'games_count': games_count,
            'form': self.filterset.form,
            'filterset': self.filterset,
        })

        # Update the main context with the collected context data
        context.update(context_data)

        return context




# @player_onboard_required
def game_detail_view(request, id=None):
    # hx_url = reverse("game-hx-detail", kwargs={"id": id})
    participants = []
    efforts = []
    scorecard_count = 0
    show_detail = False
    try:
        obj = Game.objects.get(id=id)
        for effort in obj.efforts.all():
            participants.append(effort.player)
        if obj.recorder:
            participants.append(obj.recorder)
        # Add efforts directly to context to get the available_scorecard field
        efforts = obj.efforts.all().prefetch_related('faction', 'player', 'vagabond', 'scorecard')
        # Count the total number of scorecards linked to efforts
        scorecard_count = ScoreCard.objects.filter(effort__in=obj.efforts.all()).distinct().count()
        if request.user.is_authenticated:
            for effort in efforts:
                effort.available_scorecard = effort.available_scorecard(request.user)

            if obj.final and (request.user.profile in participants or scorecard_count != 0):
                show_detail = True

    except ObjectDoesNotExist:
        obj = None


    commentform = GameCommentCreateForm()
    context=  {
        # 'hx_url': hx_url,
        'game': obj,
        'commentform': commentform,
        'participants': participants,
        'efforts': efforts,
        'scorecard_count': scorecard_count,
        'show_detail': show_detail,
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

    if id:
        if obj.final and not user.profile.admin:

            messages.error(request, "Game cannot be edited.")
            return redirect(obj.get_absolute_url())


        elif not user.profile.admin and user.profile != obj.recorder:
            messages.error(request, "You do not have permission to edit this game.")
            return redirect(obj.get_absolute_url())

    # Don't think the form needs to be initiated here
    # form = GameCreateForm(request.POST or None, instance=obj, user=user)
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
    form = GameCreateForm(request.POST or None, instance=obj, user=user, effort_formset=formset)
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
                send_discord_message(f'{user} Recorded a Game')
            if not parent.recorder:
                parent.recorder = request.user.profile  # Set the recorder
            parent.date_posted = timezone.now()
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
            return redirect(parent.get_absolute_url())
        else:
            context['message'] = 'Game not Saved. Please correct errors below.'
    if request.htmx:
        return render(request, 'the_warroom/partials/forms.html', context)
    
    return render(request, 'the_warroom/record_game.html', context)


@player_required
@bookmark_toggle(Game)
def bookmark_game(request, object):
    return render(request, 'the_warroom/partials/bookmarks.html', {'game': object })












# ============================
# Scorecard Views
# ============================

# Create and edit a scorecard
@player_onboard_required
def scorecard_manage_view(request, id=None):
    existing_scorecard = False
    faction = request.GET.get('faction', None)
    effort_id = request.GET.get('effort', None)
    game_group = request.GET.get('game_group', None)
    if faction:
        faction_name = Faction.objects.get(id=faction).title
    else:
        faction_name = None

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
        existing_scorecard = True


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

    if not obj.total_generic_points and obj.id and obj.total_points:
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
    }

    # Handle form submission
    if form.is_valid() and formset.is_valid():
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
            # total_points += (
            #     child_form.cleaned_data.get('faction_points', 0) +
            #     child_form.cleaned_data.get('crafting_points', 0) +
            #     child_form.cleaned_data.get('battle_points', 0) +
            #     child_form.cleaned_data.get('other_points', 0) + 
            #     child_form.cleaned_data.get('generic_points', 0) 
            # )
            total_points += child_form.cleaned_data.get('total_points', 0)
        if effort and total_points != effort.score and effort.score:
            # If points don't match effort.score
            context['message'] = f"Error: The recorded points ({total_points}) do not match the total game points ({effort.score})."
            if existing_scorecard == False:
                parent.delete()
            return render(request, 'the_warroom/record_scores.html', context)

        elif total_points < 0:
            # If points are negative
            context['message'] = f"Error: The recorded points ({total_points}) are negative."
            if existing_scorecard == False:
                parent.delete()
            return render(request, 'the_warroom/record_scores.html', context)

        if (effort and dominance and not effort.dominance and not effort.coalition_with) or (effort and not dominance and effort.dominance) or (effort and not dominance and effort.coalition_with):
            if dominance:
                context['message'] = f"Dominance Error: The original effort does not have a Dominance Played."
            else:
                context['message'] = f"Dominance Error: The original effort has an active Dominance Played."
            if existing_scorecard == False:
                parent.delete()
            return render(request, 'the_warroom/record_scores.html', context)

        
        for turn_form in formset:
            child = turn_form.save(commit=False)
            child.scorecard = parent  # Link the turn to the game
            child.save()

        if parent.dominance != dominance:
            parent.dominance = dominance
            # print("dominance", dominance)
            parent.save()  # Save the new or updated Game Score instance

        # Check if the "next" button was clicked
        if request.POST.get('next'):
            # Redirect to the update page of the next scorecard
            return redirect('update-scorecard', next_scorecard.id)
        # Check if the "previous" button was clicked
        if request.POST.get('previous'):
            # Redirect to the update page of the next scorecard
            return redirect('update-scorecard', previous_scorecard.id)
        # Check if the "add_player" button was clicked
        if request.POST.get('add_player'):
            # Redirect to the record page to record new
            game_group = parent.game_group
            encoded_game_group = quote(str(game_group))  # Encode the game_group value
            return redirect(f'{reverse("record-scorecard")}?game_group={encoded_game_group}')


        context['message'] = "Scores Saved"
        return redirect(parent.get_absolute_url())
    return render(request, 'the_warroom/record_scores.html', context)


# Detail view of a scorecard
@player_onboard_required
def scorecard_detail_view(request, id=None):
    try:
        obj = ScoreCard.objects.get(id=id)
    except ObjectDoesNotExist:
        obj = None

    next_scorecard = None   
    previous_scorecard = None
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

    context=  {
        'object': obj,
        'previous_scorecard': previous_scorecard,
        'next_scorecard': next_scorecard,
    }

    return render(request, "the_warroom/score_detail.html", context)


# Choose from available scorecards to link to a game
@player_required
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
@player_required
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
        print('submitting form')
        if form.is_valid():
            print('valid form')
            scorecard = scorecard
            scorecard.effort = form.cleaned_data['effort']  # Assign the Effort to the selected Scorecard
            scorecard.save()  # Save the updated Scorecard
            return redirect('game-detail', id=scorecard.effort.game.id)
        else:
                print("Form errors:", form.errors)  # Print the errors for debugging
                # You can also print individual field errors if you need to inspect them specifically
                for field, errors in form.errors.items():
                    print(f"Errors for {field}: {errors}")
    else:
        form = AssignEffortForm(selected_efforts=available_efforts, user=request.user)

    context = {
        'form': form, 
        'scorecard': scorecard,
        }

    return render(request, 'the_warroom/assign_effort.html', context)


@player_required
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

    all_scorecards = ScoreCard.objects.filter(recorder=request.user.profile).prefetch_related('turns', 'faction', 'effort')

    # QuerySets
    unassigned_scorecards = all_scorecards.filter(effort=None)
    unassigned_count = unassigned_scorecards.count()
    # if unassigned_count > 10:
    #     unassigned_scorecards = unassigned_scorecards[:10]
    complete_scorecards = all_scorecards.exclude(effort=None).prefetch_related(
        'effort__game', 'effort__player', 'effort__faction')

    
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
        'unassigned_scorecards': unassigned_scorecards,
        'complete_scorecards': page_obj,
        'active_profile': active_profile,
        'unassigned_count': unassigned_count,
    }

    if request.htmx:
        return render(request, "the_warroom/partials/scorecard_list_partial.html", context)

    return render(request, 'the_warroom/scorecard_list.html', context)












# ============================
# Tournament Views
# ============================

@player_required
def tournament_detail_view(request, tournament_slug):
    # Get the tournament from slug
    tournament = get_object_or_404(Tournament, slug=tournament_slug.lower())
    all_rounds = Round.objects.filter(tournament=tournament).annotate(
        unique_players_count=Count('games__efforts__player', distinct=True)
        ).order_by('-round_number')

    past_rounds = all_rounds.filter(end_date__lt=timezone.now())


    active_rounds = all_rounds.filter(
        Q(end_date__gt=timezone.now()) | Q(end_date__isnull=True),
        start_date__lt=timezone.now()
        )
    future_rounds = all_rounds.filter(start_date__gt=timezone.now())
   
    efforts = Effort.objects.filter(game__round__tournament=tournament)

    top_players = []
    most_players = []
    top_factions = []
    most_factions = []
    leaderboard_threshold = tournament.game_threshold
    # 4 queries
    top_players = Profile.leaderboard(limit=tournament.leaderboard_positions, effort_qs=efforts, game_threshold=leaderboard_threshold)
    most_players = Profile.leaderboard(limit=tournament.leaderboard_positions, effort_qs=efforts, top_quantity=True, game_threshold=leaderboard_threshold)
    top_factions = Faction.leaderboard(limit=20, effort_qs=efforts, game_threshold=leaderboard_threshold)
    most_factions = Faction.leaderboard(limit=20, effort_qs=efforts, top_quantity=True, game_threshold=leaderboard_threshold)

    context = {
        'object': tournament,
        'active': active_rounds,
        'future': future_rounds,
        'past': past_rounds,
        'top_players': top_players,
        'most_players': most_players,
        'top_factions': top_factions,
        'most_factions': most_factions,
        'leaderboard_threshold': leaderboard_threshold,
        # 'players': players,
        # 'games': games,
    }
    
    if request.htmx:
        return render(request, 'the_warroom/partials/tournament_round_detail.html', context)
    return render(request, 'the_warroom/tournament_overview.html', context)

@player_required
def tournament_players_pagination(request, id):
    if not request.htmx:
        return HttpResponse(status=404)
    tournament = get_object_or_404(Tournament, id=id)

    players = Profile.objects.filter(Q(efforts__game__round__tournament=tournament)|Q(current_tournaments=tournament))
    
    players = players.annotate(
        total_efforts=Count('efforts', filter=Q(efforts__game__round__tournament=tournament)),
        win_count=Count('efforts', filter=Q(efforts__win=True, efforts__game__round__tournament=tournament)),
        coalition_count=Count('efforts', filter=Q(efforts__win=True, efforts__game__coalition_win=True, efforts__game__round__tournament=tournament))
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
class TournamentUpdateView(UpdateView):
    model = Tournament
    form_class = TournamentCreateForm

@admin_required_class_based_view  
class TournamentCreateView(CreateView):
    model = Tournament
    form_class = TournamentCreateForm

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user  # Pass the current user to the form
        return kwargs
    
@admin_required_class_based_view  
class TournamentDeleteView(DeleteView):
    model = Tournament
    success_url = reverse_lazy('tournaments-home')  # Redirect to the tournament list or a suitable page

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
            return redirect(tournament.get_absolute_url())  # Make sure `tournament.get_absolute_url()` is correct
        except IntegrityError:
            # Handle other integrity errors (if any)
            messages.error(request, "An error occurred while trying to delete this tournament.")
            return redirect(tournament.get_absolute_url())



@admin_onboard_required
def tournament_manage_players(request, tournament_slug):
    # Fetch the tournament object
    tournament = get_object_or_404(Tournament, slug=tournament_slug)

    # Initialize the querysets based on whether fan content is included
    available_players = Profile.objects.exclude(current_tournaments=tournament)

    # Assets already in the tournament
    current_players = Profile.objects.filter(current_tournaments=tournament)


    # Initialize the form and pass the querysets to it
    form = TournamentManagePlayersForm(request.POST or None,
        tournament=tournament,
        available_players_query=available_players,
        current_players_query=current_players,
    )

    # Handle form submission
    if request.method == 'POST':
        # form = TournamentManageAssetsForm(request.POST)

        if form.is_valid():
            
            # Save the changes made to players
            form.save()
            # Redirect or show a success message
            return redirect(tournament.get_absolute_url())  # or any other success URL
        else:
            # If form is invalid, print the errors for debugging
            print("Error")
            print(form.errors)
            print(form.data)
    # Render the template with the form
    context = {
        'form': form,
        'tournament': tournament,
        'available_players_count': available_players.count(),
        'current_players_count': current_players.count(),
    }

    return render(request, 'the_warroom/tournament_manage_players.html', context)



@admin_onboard_required
def tournament_manage_assets(request, tournament_slug):
    # Fetch the tournament object
    tournament = get_object_or_404(Tournament, slug=tournament_slug)

    # Initialize the querysets based on whether fan content is included
    if tournament.include_fan_content:
        if tournament.include_clockwork:
            available_factions = Faction.objects.exclude(tournaments__id=tournament.id)
        else:
            available_factions = Faction.objects.exclude(tournaments__id=tournament.id, type="C")
        available_decks = Deck.objects.exclude(tournaments__id=tournament.id)
        available_maps = Map.objects.exclude(tournaments__id=tournament.id)
        available_landmarks = Landmark.objects.exclude(tournaments__id=tournament.id)
        available_tweaks = Tweak.objects.exclude(tournaments__id=tournament.id)
        available_hirelings = Hireling.objects.exclude(tournaments__id=tournament.id)
        available_vagabonds = Vagabond.objects.exclude(tournaments__id=tournament.id)
    else:
        if tournament.include_clockwork:
            available_factions = Faction.objects.exclude(tournaments__id=tournament.id).filter(official=True)
        else:
            available_factions = Faction.objects.exclude(tournaments__id=tournament.id, type="C").filter(official=True)
        available_decks = Deck.objects.exclude(tournaments__id=tournament.id).filter(official=True)
        available_maps = Map.objects.exclude(tournaments__id=tournament.id).filter(official=True)
        available_landmarks = Landmark.objects.exclude(tournaments__id=tournament.id).filter(official=True)
        available_tweaks = Tweak.objects.exclude(tournaments__id=tournament.id).filter(official=True)
        available_hirelings = Hireling.objects.exclude(tournaments__id=tournament.id).filter(official=True)
        available_vagabonds = Vagabond.objects.exclude(tournaments__id=tournament.id).filter(official=True)

    # Assets already in the tournament
    tournament_factions = Faction.objects.filter(tournaments__id=tournament.id)
    tournament_decks = Deck.objects.filter(tournaments__id=tournament.id)
    tournament_maps = Map.objects.filter(tournaments__id=tournament.id)
    tournament_landmarks = Landmark.objects.filter(tournaments__id=tournament.id)
    tournament_tweaks = Tweak.objects.filter(tournaments__id=tournament.id)
    tournament_hirelings = Hireling.objects.filter(tournaments__id=tournament.id)
    tournament_vagabonds = Vagabond.objects.filter(tournaments__id=tournament.id)

    # Initialize the form and pass the querysets to it
    form = TournamentManageAssetsForm(request.POST or None,
        tournament=tournament,
        available_factions_query=available_factions,
        tournament_factions_query=tournament_factions,
        available_decks_query=available_decks,
        tournament_decks_query=tournament_decks,
        available_maps_query=available_maps,
        tournament_maps_query=tournament_maps,
        available_landmarks_query=available_landmarks,
        tournament_landmarks_query=tournament_landmarks,
        available_tweaks_query=available_tweaks,
        tournament_tweaks_query=tournament_tweaks,
        available_hirelings_query=available_hirelings,
        tournament_hirelings_query=tournament_hirelings,
        available_vagabonds_query=available_vagabonds,
        tournament_vagabonds_query=tournament_vagabonds
    )

    # Handle form submission
    if request.method == 'POST':
        # form = TournamentManageAssetsForm(request.POST)
        
        if form.is_valid():
            
            # Save the changes made to assets (factions, decks, maps, etc.)
            form.save()
            # form.save_m2m()
            # Redirect or show a success message
            return redirect(tournament.get_absolute_url())  # or any other success URL
        else:
            # If form is invalid, print the errors for debugging
            print("Error")
            print(form.errors)
            print(form.data)
    # Render the template with the form
    context = {
        'form': form,
        'tournament': tournament,
        'available_factions_count': available_factions.count(),
        'available_decks_count': available_decks.count(),
        'available_maps_count': available_maps.count(),
        'available_vagabonds_count': available_vagabonds.count(),
        'available_hirelings_count': available_hirelings.count(),
        'available_landmarks_count': available_landmarks.count(),
        'available_tweaks_count': available_tweaks.count(),
    }

    return render(request, 'the_warroom/tournament_manage_assets.html', context)

@player_onboard_required
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

    context = {
        'scheduled': scheduled_tournaments,
        'concluded': concluded_tournaments,
        'ongoing': ongoing_tournaments,
        # 'all': all_tournaments,
    }
    return render(request, 'the_warroom/tournaments_home.html', context)













# ============================
# Round Views (Individual Round/Season of a Tournament)
# ============================


@player_onboard_required
def round_detail_view(request, tournament_slug, round_slug):

    # Get the tournament from slug
    tournament = get_object_or_404(Tournament, slug=tournament_slug.lower())
    # Fetch the round using its slug, and filter it by the related tournament
    round = get_object_or_404(Round, slug=round_slug.lower(), tournament=tournament)

    efforts = Effort.objects.filter(game__round=round)
    
    threshold = round.game_threshold


    top_players = []
    most_players = []
    top_factions = []
    most_factions = []

    top_players = Profile.leaderboard(limit=tournament.leaderboard_positions, effort_qs=efforts, game_threshold=threshold)
    most_players = Profile.leaderboard(limit=tournament.leaderboard_positions, effort_qs=efforts, top_quantity=True, game_threshold=threshold)
    top_factions = Faction.leaderboard(limit=20, effort_qs=efforts, game_threshold=threshold)
    most_factions = Faction.leaderboard(limit=20, effort_qs=efforts, top_quantity=True, game_threshold=threshold)

    # players = round.current_player_queryset()
    # players = players.annotate(
    #     total_efforts=Count('efforts', filter=Q(efforts__game__round=round)),
    #     win_count=Count('efforts', filter=Q(efforts__win=True, efforts__game__round=round)),
    #     coalition_count=Count('efforts', filter=Q(efforts__win=True, efforts__game__coalition_win=True, efforts__game__round=round))
    # )

    # # Annotate with win_rate after filtering
    # players = players.annotate(
    #     win_rate=Case(
    #         When(total_efforts=0, then=Value(0)),
    #         default=ExpressionWrapper(
    #             (Cast(F('win_count'), FloatField()) - (Cast(F('coalition_count'), FloatField()) / 2)) / Cast(F('total_efforts'), FloatField()) * 100,  # Win rate as percentage
    #             output_field=FloatField()
    #         ),
    #         output_field=FloatField()
    #     ),
    #     tourney_points=Case(
    #         When(total_efforts=0, then=Value(0)),
    #         default=ExpressionWrapper(
    #             Cast(F('win_count'), FloatField()) - (Cast(F('coalition_count'), FloatField()) / 2),  # Points - 0.5 for coalitions
    #             output_field=FloatField()
    #         ),
    #         output_field=FloatField()
    #     )
    # ).order_by('-total_efforts', '-tourney_points', '-win_rate', 'display_name')



    # games = round.games.all()

    context = {
        'tournament': tournament,
        'object': round,
        'top_players': top_players,
        'most_players': most_players,
        'top_factions': top_factions,
        'most_factions': most_factions,
        'leaderboard_threshold': threshold,
        # 'players': players,
        # 'games': games,
    }
    
    return render(request, 'the_warroom/tournament_overview.html', context)



@player_required
def round_players_pagination(request, id):
    if not request.htmx:
        return HttpResponse(status=404)
    round = get_object_or_404(Round, id=id)

    if round.players.count() == 0:
        players = Profile.objects.filter(Q(efforts__game__round=round)|Q(current_tournaments=round.tournament))
    else:
        players = Profile.objects.filter(Q(efforts__game__round=round)|Q(rounds=round))

    # participants = Profile.objects.filter(efforts__game__round=round)
    # roster = round.current_player_queryset()
    # players = participants.union(roster)
    print(players)


    # print(players.count())
    # print("Players")
    players = players.annotate(
        total_efforts=Count('efforts', filter=Q(efforts__game__round=round)),
        win_count=Count('efforts', filter=Q(efforts__win=True, efforts__game__round=round)),
        coalition_count=Count('efforts', filter=Q(efforts__win=True, efforts__game__coalition_win=True, efforts__game__round=round))
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




@player_required
def round_games_pagination(request, id):
    
    if not request.htmx:
        return HttpResponse(status=404)
    round = get_object_or_404(Round, id=id)

    games = round.games.all()

    # Paginate games
    paginator = Paginator(games, settings.PAGE_SIZE)  # Use the queryset directly
    page_number = request.GET.get('page', 1)   # Get the page number from the request
 
    try:
        page_obj = paginator.get_page(page_number)  # Get the specific page of games
    except EmptyPage:
        page_obj = paginator.page(paginator.num_pages)  # Redirect to the last page if invalid
    


    return render(request, 'the_warroom/partials/round_game_list.html', {'games': page_obj, 'object': round})



@player_onboard_required
def round_manage_view(request, tournament_slug, round_slug=None):
    tournament = get_object_or_404(Tournament, slug=tournament_slug)

    if not request.user.profile == tournament.designer and not request.user.profile.admin:
        messages.error(request, "You do not have permission to view this page.")
        raise PermissionDenied() 
    
    current_round = 1
    for tournament_round in tournament.rounds.all():
        current_round = max(tournament_round.round_number, current_round)
    
    round_instance = None
    # If round_slug is provided, update the existing round
    if round_slug:
        round_instance = get_object_or_404(Round, slug=round_slug, tournament=tournament)
        form = RoundCreateForm(request.POST or None, tournament=tournament, instance=round_instance, current_round=current_round)
    else:
        # Otherwise, create a new round
        form = RoundCreateForm(request.POST or None, tournament=tournament, current_round=current_round)

    if form.is_valid():
        round_instance = form.save()  # Save the round instance
        return redirect(round_instance.get_absolute_url())  # Redirect to the round's absolute URL
    context = {
        'form': form,
        'tournament': tournament,
        'round': round_instance,
        }
    return render(request, 'the_warroom/tournament_round_form.html', context)


@admin_onboard_required
def round_manage_players(request, round_slug, tournament_slug):
    # Fetch the tournament object
    selected_round = get_object_or_404(Round, slug=round_slug)
    tournament = selected_round.tournament

    if tournament.players.count() == 0:
        return redirect('tournament-players', tournament_slug=tournament.slug)

    round_number = selected_round.round_number

    # QS of players in tournament that are not in this round.
    available_players = Profile.objects.exclude(rounds=selected_round).filter(current_tournaments=tournament)

    # Find players already in the same tournament round.
    other_rounds = Round.objects.filter(tournament=tournament, round_number=round_number).exclude(id=selected_round.id)
    taken_players = []
    if other_rounds:
        for round in other_rounds:
            taken_players = taken_players + list(round.players.all())
    exclude_ids = [obj.id for obj in taken_players] 
    # Remove players from the available list if they are already in another concurrent round
    available_players = available_players.exclude(id__in=exclude_ids)

    # Players already in this round that can be removed.
    current_players = Profile.objects.filter(rounds=selected_round)


    # Initialize the form and pass the querysets to it
    form = RoundManagePlayersForm(request.POST or None,
        round=selected_round,
        available_players_query=available_players,
        current_players_query=current_players,
    )

    # Handle form submission
    if request.method == 'POST':
        # form = TournamentManageAssetsForm(request.POST)
        if form.is_valid():
            # Save the changes made to players
            form.save()
            # Redirect or show a success message
            return redirect(selected_round.get_absolute_url())  # or any other success URL
        else:
            # If form is invalid, print the errors for debugging
            print("Error")
            print(form.errors)
            print(form.data)
    # Render the template with the form
    context = {
        'form': form,
        'tournament': tournament,
        'round': selected_round,
        'available_players_count': available_players.count(),
        'current_players_count': current_players.count(),
    }

    return render(request, 'the_warroom/tournament_manage_players.html', context)


@player_required_class_based_view
class RoundDeleteView(LoginRequiredMixin, UserPassesTestMixin, DeleteView):
    model = Round

    def test_func(self):
        obj = self.get_object()
        # Only allow access if the logged-in user is the designer of the object
        return self.request.user.profile == obj.tournament.designer or self.request.user.profile.admin
    
    # Dynamically set the success URL based on the round's tournament
    def get_success_url(self):
        # Redirect to the tournament detail page using the tournament slug
        tournament_slug = self.object.tournament.slug
        return reverse_lazy('tournament-detail', kwargs={'tournament_slug': tournament_slug})
    
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