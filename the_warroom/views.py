from django.shortcuts import render
# from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.decorators import login_required
from django.contrib.admin.views.decorators import staff_member_required
from django.views.generic import ListView

from django.shortcuts import get_object_or_404, redirect, HttpResponseRedirect
from django.forms.models import modelformset_factory
from django.http import HttpResponse, Http404
from django.core.exceptions import ObjectDoesNotExist
from django.core.paginator import Paginator, EmptyPage
from django.urls import reverse
from django.conf import settings
from django.http import HttpResponse, JsonResponse
# from django.db import transaction
from django.db.models import Q
from .models import Game, Effort
from .forms import GameCreateForm, EffortCreateForm
from .filters import GameFilter
from the_gatehouse.models import Profile
# from the_keep.models import Post
from the_gatehouse.views import player_required
from the_tavern.forms import GameCommentCreateForm
from the_tavern.views import bookmark_toggle
from datetime import datetime



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
        self.filterset = GameFilter(self.request.GET, queryset=queryset)
        return self.filterset.qs
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        # Get the ordered queryset of games
        games = self.get_queryset()
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


# class GameCreateView(LoginRequiredMixin, CreateView):
#     model = Game
#     form_class = GameCreateForm

#     def form_valid(self, form):
#         form.instance.recorder = self.request.user
#         return super().form_valid(form)
# class EffortCreateView(LoginRequiredMixin, CreateView):
#     model = Effort
#     form_class = EffortCreateForm

#     def form_valid(self, form):
#         return super().form_valid(form)

@login_required
def game_detail_view(request, id=None):
    hx_url = reverse("game-hx-detail", kwargs={"id": id})
    participants = []
    try:
        obj = Game.objects.get(id=id)
        for effort in obj.efforts.all():
            participants.append(effort.player)
    except ObjectDoesNotExist:
        obj = None
    commentform = GameCommentCreateForm()
    context=  {
        'hx_url': hx_url,
        'game': obj,
        'commentform': commentform,
        'participants': participants
    }
    return render(request, "the_warroom/game_detail.html", context)

@staff_member_required
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


def delete_effort(request, id=None):
    # Check if the effort exists
    print(id)
    print('Test')
    if id:
        try:
            effort = Effort.objects.get(id=id)
            effort.delete()
            return JsonResponse({'status': 'success'})
        except Effort.DoesNotExist:
            return JsonResponse({'status': 'error', 'message': 'Effort not found'}, status=404)
    else:
        # Handle deletion for unsaved or temporary efforts
        return JsonResponse({'status': 'error', 'message': 'No effort to delete'}, status=400)


@staff_member_required
def game_effort_delete_view(request, parent_id=None, id=None):
    try:
        obj = Effort.objects.get(game__id=parent_id, id=id)
    except:
        obj = None

    if obj is None:
        if request.htmx:
            return HttpResponse("Not Found")
        raise Http404
    if request.method == "POST":
        obj.delete()
        success_url = reverse('games-detail', kwargs={'id': parent_id})
        if request.htmx:
            headers = {
                'HX-Redirect': success_url,
            }
            return HttpResponse("Success", headers=headers)
        return redirect(success_url)
    context=  {
        'object': obj
    }
    return render(request, "the_warroom/game_delete.html", context)

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

@login_required
def effort_update_hx_view(request, id=None):
    if not request.htmx:
        raise Http404("Not an HTMX request")
    
    try:
        obj = Effort.objects.get(id=id)
    except Effort.DoesNotExist:
        return HttpResponse('Effort Not Found', status=404)

    context = {
        'effort': obj
    }
    return render(request, "the_warroom/partials/effort_partial.html", context)



@player_required
def record_game(request, id=None):
    form = GameCreateForm(request.POST or None, user = request.user)
    context = {
        'form': form, 
    }
    if form.is_valid():
        obj = form.save(commit=False)
        obj.recorder = request.user.profile
        obj.save()
        form.save_m2m()
        return redirect(obj.get_absolute_url())
    return render(request, 'the_warroom/record_game.html', context)

@player_required
def manage_game(request, id=None):
    if id:
        obj = get_object_or_404(Game, id=id)
    else:
        obj = Game()  # Create a new Game instance but do not save it yet
    user = request.user
    form = GameCreateForm(request.POST or None, instance=obj, user=user)

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
        'form_count': form_count
    }

    
    # Handle form submission
    if form.is_valid() and formset.is_valid():
        parent = form.save(commit=False)
        parent.recorder = request.user.profile  # Set the recorder
        parent.save()  # Save the new or updated Game instance
        form.save_m2m()
        seat = 1
        for form in formset:

            child = form.save(commit=False)
            if child.faction_id is not None:  # Only save if faction_id is present
                if child.faction.status == "Stable":
                    status = "Stable"
                else:
                    status = f"{child.faction.status()} - {child.faction.date_updated.strftime('%Y-%m-%d')}"
                child.faction_status = status
                child.game = parent  # Link the effort to the game
                child.seat = seat
                child.save()
                seat += 1

        context['message'] = "Game Saved"
        return redirect(parent.get_absolute_url())

    if request.htmx:
        return render(request, 'the_warroom/partials/forms.html', context)
    
    return render(request, 'the_warroom/record_game.html', context)




def create_effort(request):
    if request.method == "POST":
        pass

    return render(request, 'the_warroom/partials/effort_partial.html', {'form': EffortCreateForm})

@login_required
@bookmark_toggle(Game)
def bookmark_game(request, object):
    return render(request, 'the_warroom/partials/bookmarks.html', {'game': object })


# Never used
# @login_required
# def record_effort(request):
#     if request.method == 'POST':
#         form = EffortCreateForm(request.POST or None)
#         if form.is_valid():
#             effort = form.save()
#             context = {'effort': effort}
#             return render(request, 'the_warroom/partials/effort.html', context)
#     return render(request, 'the_warroom/partials/effort_form.html', {'form': EffortCreateForm}) 