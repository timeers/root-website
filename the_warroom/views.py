from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from django.contrib.admin.views.decorators import staff_member_required
from django.views.generic import ListView
from django.views.decorators.http import require_http_methods
from django.shortcuts import get_object_or_404, redirect, HttpResponseRedirect
from django.forms.models import modelformset_factory
from django.http import HttpResponse, Http404, HttpResponseForbidden
from django.core.exceptions import ObjectDoesNotExist
from django.core.paginator import Paginator, EmptyPage
from django.urls import reverse
from django.conf import settings
from django.db.models import Q

from .models import Game, Effort, TurnScore, ScoreCard
from .forms import GameCreateForm, EffortCreateForm, TurnScoreCreateForm, ScoreCardCreateForm, AssignScorecardForm
from .filters import GameFilter

from the_gatehouse.models import Profile
from the_gatehouse.views import player_required, admin_required, designer_required
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
        print(first_part)

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
    form_count = extra_forms + existing_count
    context = {
        'form': form,
        'formset': formset,
        'object': obj,
        'form_count': form_count,
        'player_form': player_form,
    }

    print(f'Forms: {formset.total_form_count()}')
    # Handle form submission
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

        if seat == 0:
            parent.delete()
            context['message'] = "Game must include a player"
            return redirect('games-home')


        if len(roster) != len(set(roster)):
            parent.test_match = True
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

    if not form.is_valid():
        print("Form errors:", form.errors)
    if not formset.is_valid():
        print("Formset errors:", formset.errors)

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
                print('Delete')
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
            print("Found Dominance")
            dominance_efforts = unassigned_efforts.filter(
                faction=scorecard.faction,
                dominance__isnull=False
            )
            print(dominance_efforts)
            matching_efforts = matching_efforts | dominance_efforts
        # Add the matching efforts as a property on the scorecard
        scorecard.matching_efforts = matching_efforts.distinct()

    context = {
        'unassigned_scorecards': unassigned_scorecards,
        'complete_scorecards': complete_scorecards,
    }
    return render(request, 'the_warroom/scorecard_list.html', context)