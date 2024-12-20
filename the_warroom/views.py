from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from django.contrib.admin.views.decorators import staff_member_required
from django.views.generic import ListView, UpdateView, CreateView, DeleteView
from django.views.decorators.http import require_http_methods
from django.shortcuts import get_object_or_404, redirect, HttpResponseRedirect
from django.forms.models import modelformset_factory
from django.http import HttpResponse, Http404, HttpResponseForbidden
from django.core.exceptions import ObjectDoesNotExist
from django.core.paginator import Paginator, EmptyPage
from django.urls import reverse, reverse_lazy
from django.conf import settings
from django.db.models import Q
from django.utils import timezone 

from .models import Game, Effort, TurnScore, ScoreCard, Round, Tournament
from .forms import (GameCreateForm, EffortCreateForm, 
                    TurnScoreCreateForm, ScoreCardCreateForm, AssignScorecardForm, 
                    TournamentCreateForm, RoundCreateForm, 
                    TournamentManagePlayersForm, TournamentManageAssetsForm,
                    RoundManagePlayersForm)
from .filters import GameFilter

from the_keep.views import Faction, Deck, Map, Vagabond, Hireling, Landmark

from the_gatehouse.models import Profile
from the_gatehouse.views import player_required, admin_required, designer_required, admin_required_class_based_view
from the_gatehouse.forms import PlayerCreateForm

from the_tavern.forms import GameCommentCreateForm
from the_tavern.views import bookmark_toggle





#  A list of all the games. Most recent update first
class GameListView(ListView):
    queryset = Game.objects.all()
    model = Game
    # template_name = 'the_warroom/games_home.html' # <app>/<model>_<viewtype>.html
    context_object_name = 'games'
    ordering = ['-date_posted']
    paginate_by = settings.PAGE_SIZE

    def get_template_names(self):
        if self.request.htmx:
            return 'the_warroom/partials/game_list_home.html'
        return 'the_warroom/games_home.html'
    
    def get_queryset(self):
        if not self.request.user.is_authenticated:
            queryset = super().get_queryset().only_official_components()
        else:
            if self.request.user.profile.weird:
                queryset = super().get_queryset()
            else:
                queryset = super().get_queryset().only_official_components()
        self.filterset = GameFilter(self.request.GET, queryset=queryset, user=self.request.user)
        return self.filterset.qs
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        # Get the ordered queryset of games
        games = self.get_queryset()

        # Get the total count of games (total number of records in the queryset)
        games_count = games.count()

        # Paginate games
        paginator = Paginator(games, self.paginate_by)  # Use the queryset directly
        page_number = self.request.GET.get('page')  # Get the page number from the request

        try:
            page_obj = paginator.get_page(page_number)  # Get the specific page of games
        except EmptyPage:
            page_obj = paginator.page(paginator.num_pages)  # Redirect to the last page if invalid

        context['games'] = page_obj  # Pass the paginated page object to the context
        context['is_paginated'] = paginator.num_pages > 1  # Set is_paginated boolean
        context['page_obj'] = page_obj  # Pass the page_obj to the context

        context['form'] = self.filterset.form
        context['filterset'] = self.filterset

        context['games_count'] = games_count
        
        return context
    

# Not used currently.
class GameListViewHX(ListView):
    queryset = Game.objects.all()
    model = Game
    # template_name = 'the_warroom/games_home.html' # <app>/<model>_<viewtype>.html
    context_object_name = 'games'
    ordering = ['-date_posted']
    paginate_by = settings.PAGE_SIZE

    def get_template_names(self):
        if self.request.htmx:
            return 'the_warroom/partials/game_list_home.html'
        return 'the_warroom/games_home.html'
    
    def get_queryset(self):
        queryset = super().get_queryset()


####### Trying to use this one view to get game list data for anything.
        full_path = self.request.path
        # Get the first part of the path after the domain
        first_part = full_path.split('/')[1] if len(full_path.split('/')) > 1 else ''
        

        if first_part != 'games':
            # Get the slug from the URL (assuming your URL pattern captures a slug)
            slug = self.kwargs.get('slug')
            print(f'found slug {slug}')
            if slug:
                print(f'found slug {slug}')
                if first_part == 'profile':
                    player = get_object_or_404(Profile, slug=slug)
                    print(f'found player {player}')
                    queryset = queryset.filter(
                        Q(efforts__player=player)) # Filter by Profile Page
                # else:
                #     component = get_object_or_404(Post, slug=slug)
                #     queryset = queryset.filter(
                #         Q(efforts__faction=faction)  # Filter by any selected faction
                #     )



        self.filterset = GameFilter(self.request.GET, queryset=queryset)
        return self.filterset.qs
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        # Get the ordered queryset of games
        games = self.get_queryset()
        # Paginate games
        if games.exists():
            paginator = Paginator(games, self.paginate_by)
            page_number = self.request.GET.get('page')
            page_obj = paginator.get_page(page_number)
        else:
            page_obj = []  # Or handle as needed

        context['games'] = page_obj  # Pass the paginated page object to the context
        context['is_paginated'] = paginator.num_pages > 1  # Set is_paginated boolean
        context['page_obj'] = page_obj  # Pass the page_obj to the context

        context['form'] = self.filterset.form
        context['filterset'] = self.filterset
        
        return context


@player_required
def game_detail_view(request, id=None):
    hx_url = reverse("game-hx-detail", kwargs={"id": id})
    participants = []
    try:
        obj = Game.objects.get(id=id)
        for effort in obj.efforts.all():
            participants.append(effort.player)
    except ObjectDoesNotExist:
        obj = None

    # Add efforts directly to context to get the available_scorecard field
    efforts = obj.efforts.all()
    for effort in efforts:
        effort.available_scorecard = effort.available_scorecard(request.user)

    commentform = GameCommentCreateForm()
    context=  {
        'hx_url': hx_url,
        'game': obj,
        'commentform': commentform,
        'participants': participants,
        'efforts': efforts,
    }
    return render(request, "the_warroom/game_detail_page.html", context)

@admin_required
def game_delete_view(request, id=None):
    try:
        obj = Game.objects.get(id=id)
    except:
        obj = None

    if obj is None:
        if request.htmx:
            return HttpResponse("Not Found")
        raise Http404
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

# @login_required
# def effort_update_hx_view(request, id=None):
#     if not request.htmx:
#         raise Http404("Not an HTMX request")
    
#     try:
#         obj = Effort.objects.get(id=id)
#     except Effort.DoesNotExist:
#         return HttpResponse('Effort Not Found', status=404)

#     context = {
#         'effort': obj
#     }
#     return render(request, "the_warroom/partials/effort_partial.html", context)



@player_required
def manage_game(request, id=None):
    if id:
        obj = get_object_or_404(Game, id=id)
    else:
        obj = Game()  # Create a new Game instance but do not save it yet
    user = request.user
    form = GameCreateForm(request.POST or None, instance=obj, user=user)
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
            parent.recorder = request.user.profile  # Set the recorder
            parent.save()  # Save the new or updated Game instance
            form.save_m2m()
            seat = 0
            roster = []
            for form in formset:
                child = form.save(commit=False)
                if child.faction_id is not None:  # Only save if faction_id is present

                    seat += 1
                    # Save current status of faction. Might be useful somewhere.
                    if child.faction.status == "Stable":
                        status = "Stable"
                    else:
                        status = f"{child.faction.status()} - {child.faction.date_updated.strftime('%Y-%m-%d')}"
                    child.faction_status = status
                    child.game = parent  # Link the effort to the game
                    child.seat = seat
                    child.save()
                    
                    if child.player:
                        # Add player.id to the roster
                        roster.append(child.player.id)

            if len(roster) != len(set(roster)):
                parent.test_match = True
                parent.save()
            elif id and parent.test_match == True:
                parent.test_match = False
                parent.save()

            context['message'] = "Game Saved"
            return redirect(parent.get_absolute_url())

    if request.htmx:
        return render(request, 'the_warroom/partials/forms.html', context)
    
    return render(request, 'the_warroom/record_game.html', context)


@login_required
@bookmark_toggle(Game)
def bookmark_game(request, object):
    return render(request, 'the_warroom/partials/bookmarks.html', {'game': object })






# VIEW FOR RECORDING TURN BY TURN SCORES
@designer_required
def scorecard_manage_view(request, id=None):
    existing_scorecard = False
    faction = request.GET.get('faction', None)
    print(faction)
    effort_id = request.GET.get('effort', None)
    try:
        effort = Effort.objects.get(id=effort_id)

        game = effort.game
        participants = []
        for participant_effort in game.efforts.all():
            participants.append(participant_effort.player)
        if game.recorder:
            participants.append(game.recorder)
        if not request.user.profile in participants:
                    return HttpResponseForbidden("You do not have permission to edit this scorecard.")


    except Effort.DoesNotExist:
        effort = None

    if id:
        obj = get_object_or_404(ScoreCard, id=id)
        # Check if the current user is the same as the recorder
        if obj.recorder != request.user.profile:
            return HttpResponseForbidden("You do not have permission to edit this scorecard.")
        if obj.faction != None:
            faction = obj.faction.id
            print(faction)
        if obj.effort != None:
            effort = obj.effort
        existing_scorecard = True
    else:
        obj = ScoreCard()  # Create a new ScoreCard instance but do not save it yet
    user = request.user

    form = ScoreCardCreateForm(request.POST or None, instance=obj, user=user, faction=faction)

    if id:  # Only check for existing turns if updating an existing game score
        existing_turns = obj.turns.all()
        existing_count = existing_turns.count()
        extra_forms = 0
    else:
        existing_count = 0  # New game has no existing turns
        extra_forms = 4
    TurnFormset = modelformset_factory(TurnScore, form=TurnScoreCreateForm, extra=extra_forms)
    qs = obj.turns.all() if id else TurnScore.objects.none()  # Only fetch existing turns if updating
    formset = TurnFormset(request.POST or None, queryset=qs)
    form_count = extra_forms + existing_count        

    if effort:
        score = effort.score
    else:
        score = None


    context = {
        'form': form,
        'formset': formset,
        'object': obj,
        'form_count': form_count,
        'faction': faction,
        'score': score,
    }


    # Handle form submission
    if form.is_valid() and formset.is_valid():
        print(formset.cleaned_data)
        parent = form.save(commit=False)
        parent.recorder = request.user.profile  # Set the recorder
        parent.effort = effort
        parent.save()  # Save the new or updated Game Score instance


        # Calculate the total points from the formset
        total_points = 0
        for child_form in formset:
            # print(child_form.cleaned_data)
            if child_form.cleaned_data.get('DELETE'):
                # If the DELETE checkbox is checked, delete the object
                deleted_instance = child_form.instance
                deleted_instance.delete()
            else:
                total_points += (
                    child_form.cleaned_data.get('faction_points', 0) +
                    child_form.cleaned_data.get('crafting_points', 0) +
                    child_form.cleaned_data.get('battle_points', 0) +
                    child_form.cleaned_data.get('other_points', 0)
                )
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

        
        for turn_form in formset:
            child = turn_form.save(commit=False)
            child.scorecard = parent  # Link the turn to the game
            child.save()

        context['message'] = "Scores Saved"
        return redirect(parent.get_absolute_url())
    
    return render(request, 'the_warroom/record_scores.html', context)

@player_required
def scorecard_detail_view(request, id=None):
    try:
        obj = ScoreCard.objects.get(id=id)
    except ObjectDoesNotExist:
        obj = None
    context=  {
        'object': obj,
    }
    return render(request, "the_warroom/score_detail.html", context)


@designer_required
def scorecard_assign_view(request, id):
    effort = get_object_or_404(Effort, id=id)

    selected_scorecard = request.GET.get('scorecard')
    print(selected_scorecard)
    game = effort.game
    participants = []
    for participant_effort in game.efforts.all():
        participants.append(participant_effort.player)
    if game.recorder:
        participants.append(game.recorder)
    if not request.user.profile in participants:
                return HttpResponseForbidden("You do not have permission to edit this scorecard.")
    dominance = False
    if effort.dominance:
        dominance = True
    if request.method == 'POST':
        form = AssignScorecardForm(request.POST, user=request.user, faction=effort.faction, total_points=effort.score, selected_scorecard=selected_scorecard, dominance=dominance)
        if form.is_valid():
            scorecard = form.cleaned_data['scorecard']
            scorecard.effort = effort  # Assign the Effort to the selected Scorecard
            scorecard.save()  # Save the updated Scorecard
            return redirect('game-detail', id=effort.game.id)
    else:
        form = AssignScorecardForm(user=request.user, faction=effort.faction, total_points=effort.score, selected_scorecard=selected_scorecard, dominance=dominance)

    context = {
        'form': form, 
        'effort': effort,
        'game': game,
        }

    return render(request, 'the_warroom/assign_scorecard.html', context)

@designer_required
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
            success_url = reverse('list-scorecard')
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

@designer_required
def scorecard_list_view(request):
    unassigned_scorecards = ScoreCard.objects.filter(
            Q(recorder=request.user.profile) & 
            Q(effort=None)
        )
    complete_scorecards = ScoreCard.objects.filter(
            recorder=request.user.profile).exclude(effort=None)
    # Scorecards that need to be assigned
    unassigned_efforts = Effort.objects.filter(
        Q(game__efforts__player=request.user.profile) |  # Player is linked to the game
        Q(game__recorder=request.user.profile),  # Recorder is the current user
        scorecard=None  # Effort has no associated scorecard
    ).distinct()
    for scorecard in unassigned_scorecards:
        # # Find matching efforts based on faction and total_points
        matching_efforts = unassigned_efforts.filter(
            faction=scorecard.faction,
            score=scorecard.total_points
        )
        dominance = False
        for turn in scorecard.turns.all():
            if turn.dominance:
                dominance = True
        if dominance:
            dominance_efforts = unassigned_efforts.filter(
                faction=scorecard.faction,
                dominance__isnull=False
            )
            # print(dominance_efforts)
            matching_efforts = matching_efforts | dominance_efforts
        # Add the matching efforts as a property on the scorecard
        scorecard.matching_efforts = matching_efforts.distinct()

    # Pagination
    # paginator = Paginator(complete_scorecards, settings.PAGE_SIZE)  # Show 10 posts per page
    paginator = Paginator(complete_scorecards, 5) 
    # Get the current page number from the request (default to 1)
    page_number = request.GET.get('page')  # e.g., ?page=2
    page_obj = paginator.get_page(page_number)  # Get the page object for the current page


    context = {
        'unassigned_scorecards': unassigned_scorecards,
        # 'complete_scorecards': complete_scorecards,
        'complete_scorecards': page_obj,
    }

    if request.htmx:
        return render(request, "the_warroom/partials/scorecard_list_partial.html", context)   

    return render(request, 'the_warroom/scorecard_list.html', context)


def tournament_detail_view(request, tournament_slug):
    # Get the tournament from slug
    tournament = get_object_or_404(Tournament, slug=tournament_slug.lower())

    past_rounds = Round.objects.filter(tournament=tournament, end_date__lt=timezone.now())
    active_rounds = Round.objects.filter(tournament=tournament, start_date__lt=timezone.now())
    future_rounds = Round.objects.filter(tournament=tournament, start_date__gt=timezone.now())

    top_players = []
    most_players = []
    top_players = Profile.top_players(limit=5, tournament=tournament, game_threshold=tournament.game_threshold)
    most_players = Profile.top_players(limit=5, tournament=tournament, top_quantity=True, game_threshold=tournament.game_threshold)
    top_factions = []
    most_factions = []
    top_factions = Faction.top_factions(limit=5, tournament=tournament, game_threshold=tournament.game_threshold)
    most_factions = Faction.top_factions(limit=5, tournament=tournament, top_quantity=True, game_threshold=tournament.game_threshold)

    context = {
        'object': tournament,
        'active': active_rounds,
        'future': future_rounds,
        'past': past_rounds,
        'top_players': top_players,
        'most_players': most_players,
        'top_factions': top_factions,
        'most_factions': most_factions,
    }
    
    if request.htmx:
        return render(request, 'the_warroom/tournament_round_detail.html', context)
    return render(request, 'the_warroom/tournament_overview.html', context)

def round_detail_view(request, tournament_slug, round_slug):

    # Get the tournament from slug
    tournament = get_object_or_404(Tournament, slug=tournament_slug.lower())
    # Fetch the round using its slug, and filter it by the related tournament
    round = get_object_or_404(Round, slug=round_slug.lower(), tournament=tournament)

    
    threshold = round.game_threshold


    top_players = []
    most_players = []
    top_players = Profile.top_players(limit=5, round=round, game_threshold=threshold)
    most_players = Profile.top_players(limit=5, round=round, top_quantity=True, game_threshold=threshold)
    top_factions = []
    most_factions = []
    top_factions = Faction.top_factions(limit=5, round=round, game_threshold=threshold)
    most_factions = Faction.top_factions(limit=5, round=round, top_quantity=True, game_threshold=threshold)


    context = {
        'tournament': tournament,
        'object': round,
        'top_players': top_players,
        'most_players': most_players,
        'top_factions': top_factions,
        'most_factions': most_factions,
    }
    
    return render(request, 'the_warroom/tournament_round_detail.html', context)


@admin_required_class_based_view  
class TournamentUpdateView(UpdateView):
    model = Tournament
    form_class = TournamentCreateForm

@admin_required_class_based_view  
class TournamentCreateView(CreateView):
    model = Tournament
    form_class = TournamentCreateForm

@admin_required_class_based_view  
class TournamentDeleteView(DeleteView):
    model = Tournament
    success_url = reverse_lazy('tournaments-home')  # Redirect to the tournament list or a suitable page


@admin_required
def round_manage_view(request, tournament_slug, round_slug=None):
    tournament = get_object_or_404(Tournament, slug=tournament_slug)
    current_round = 1
    for tournament_round in tournament.round_set.all():
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
        form.save()
        return redirect('tournament-detail', tournament_slug=tournament_slug)
    context = {
        'form': form,
        'tournament': tournament,
        'round': round_instance,
        }
    return render(request, 'the_warroom/tournament_round_form.html', context)



@admin_required
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



@admin_required
def tournament_manage_assets(request, tournament_slug):
    # Fetch the tournament object
    tournament = get_object_or_404(Tournament, slug=tournament_slug)

    # Initialize the querysets based on whether fan content is included
    if tournament.include_fan_content:
        available_factions = Faction.objects.exclude(tournaments__id=tournament.id)
        available_decks = Deck.objects.exclude(tournaments__id=tournament.id)
        available_maps = Map.objects.exclude(tournaments__id=tournament.id)
        available_landmarks = Landmark.objects.exclude(tournaments__id=tournament.id)
        available_hirelings = Hireling.objects.exclude(tournaments__id=tournament.id)
        available_vagabonds = Vagabond.objects.exclude(tournaments__id=tournament.id)
    else:
        available_factions = Faction.objects.exclude(tournaments__id=tournament.id).filter(official=True)
        available_decks = Deck.objects.exclude(tournaments__id=tournament.id).filter(official=True)
        available_maps = Map.objects.exclude(tournaments__id=tournament.id).filter(official=True)
        available_landmarks = Landmark.objects.exclude(tournaments__id=tournament.id).filter(official=True)
        available_hirelings = Hireling.objects.exclude(tournaments__id=tournament.id).filter(official=True)
        available_vagabonds = Vagabond.objects.exclude(tournaments__id=tournament.id).filter(official=True)

    # Assets already in the tournament
    tournament_factions = Faction.objects.filter(tournaments__id=tournament.id)
    tournament_decks = Deck.objects.filter(tournaments__id=tournament.id)
    tournament_maps = Map.objects.filter(tournaments__id=tournament.id)
    tournament_landmarks = Landmark.objects.filter(tournaments__id=tournament.id)
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
    }

    return render(request, 'the_warroom/tournament_manage_assets.html', context)

@player_required
def tournaments_home(request):
    scheduled_tournaments = Tournament.objects.filter(start_date__gt=timezone.now())
    concluded_tournaments = Tournament.objects.filter(end_date__lt=timezone.now())
    ongoing_tournaments = Tournament.objects.filter(
        Q(end_date__gt=timezone.now()) | Q(end_date__isnull=True), 
        start_date__lt=timezone.now()
    )
    all_tournaments = Tournament.objects.all()

    context = {
        'scheduled': scheduled_tournaments,
        'concluded': concluded_tournaments,
        'ongoing': ongoing_tournaments,
        'all': all_tournaments,
    }
    return render(request, 'the_warroom/tournaments_home.html', context)



@admin_required
def round_manage_players(request, round_slug, tournament_slug):
    # Fetch the tournament object
    selected_round = get_object_or_404(Round, slug=round_slug)
    tournament = selected_round.tournament

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
        'round': selected_round,
        'available_players_count': available_players.count(),
        'current_players_count': current_players.count(),
    }

    return render(request, 'the_warroom/tournament_manage_players.html', context)

@login_required
def player_stats(request, slug):
    tournament_slug = request.GET.get('tournament_slug')
    round_slug = request.GET.get('round_slug')
    player = get_object_or_404(Profile, slug=slug)
    tournament = None
    round = None
    if tournament_slug:
        tournament = get_object_or_404(Tournament, slug=tournament_slug)
        if round_slug:
            round = get_object_or_404(Round, tournament=tournament, slug=round_slug)
    
    if round:
        efforts = Effort.objects.filter(player=player, game__round=round)
    elif tournament:
        efforts = Effort.objects.filter(player=player, game__round__tournament=tournament)
    else:
        efforts = Effort.objects.filter(player=player, game__test_match=False)

    if not tournament and not round:
        if efforts.count() > 100:
            game_threshold = 10
        elif efforts.count() > 50:
            game_threshold = 5
        elif efforts.count() > 20:
            game_threshold = 2
        else:
            game_threshold = 1

    print(f"{player.name} Game Threshold {game_threshold}")


    win_games = 0
    all_games = efforts.count()
    coalition_games = 0
    for effort in efforts:
        if effort.win:
            if effort.coalition_with:
                coalition_games += 1
            else:
                win_games += 1
    
    win_points = win_games + (coalition_games * .5)
    win_rate = win_points / all_games if all_games > 0 else 0


    top_factions = player.faction_stats(tournament=tournament, round=round, game_threshold=game_threshold)
    most_factions = player.faction_stats(most_wins=True, tournament=tournament, round=round)

    context = {
        'selected_tournament': tournament,
        'tournament_round': round,
        'top_factions': top_factions,
        'most_factions': most_factions,
        'all_games': all_games,
        'win_points': win_points,
        'win_rate': win_rate,
    }
    if request.htmx:
        return render(request, 'the_warroom/partials/player_stats.html', context)

    return render(request, 'the_warroom/player_tournament_stats.html', context)