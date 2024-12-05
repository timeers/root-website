from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.http import HttpResponseForbidden, JsonResponse
from django.contrib.auth.decorators import login_required
from django.utils.decorators import method_decorator
from django.views.generic import DetailView, ListView
from django.contrib.auth.mixins import LoginRequiredMixin
from functools import wraps
from .forms import UserRegisterForm, UserUpdateForm, ProfileUpdateForm, PlayerCreateForm
from .models import Profile
from the_tavern.views import bookmark_toggle
from django.core.paginator import Paginator, EmptyPage
from django.conf import settings
from the_warroom.filters import GameFilter



def register(request):
    if request.user.is_authenticated:
        return redirect('profile')
    if request.method == 'POST':
        form = UserRegisterForm(request.POST)
        if form.is_valid():
            form.save()
            username = form.cleaned_data.get('username')
            messages.success(request, f'Account created for {username}!')
            return redirect('login')
    else:
        form = UserRegisterForm()
    return render(request, 'the_gatehouse/register.html', {'form': form})

@login_required
def profile(request):
    if request.method == 'POST':
        # u_form = UserUpdateForm(request.POST, instance=request.user)
        p_form = ProfileUpdateForm(request.POST, 
                                   request.FILES, 
                                   instance=request.user.profile)
        # if u_form.is_valid() and p_form.is_valid():
        if p_form.is_valid():
            # u_form.save()
            p_form.save()
            messages.success(request, f'Account updated!')
            return redirect('profile')
        
    else:
        # u_form = UserUpdateForm(instance=request.user)
        p_form = ProfileUpdateForm(instance=request.user.profile)

    context = {
        # 'u_form': u_form, 
        'p_form': p_form
    }

    return render(request, 'the_gatehouse/profile.html', context)


# class PlayerDetailView(LoginRequiredMixin, DetailView):
#     model = Profile

@login_required
def player_page_view(request, slug):
    player = get_object_or_404(Profile, slug=slug)
    if request.user.profile.weird :
        queryset = player.get_games_queryset()
    else:
        queryset = player.get_games_queryset().only_official_components()
    
    # efforts = player.efforts.all()
    # games = list({effort.game for effort in efforts})
    # games.sort(key=lambda game: game.date_posted, reverse=True)
    games = list(queryset)

    quantity_faction = player.most_used_faction()
    quality_faction = player.most_successful_faction()
    quality_winrate = player.winrate(quality_faction)
    quantity_count = player.games_played(quantity_faction)


    context = {
        'games': games, 
        'player': player,
        'quality_faction': quality_faction,
        'quality_winrate': quality_winrate,
        'quantity_faction': quantity_faction,
        'quantity_count': quantity_count,
        }

    return render(request, 'the_gatehouse/profile_detail.html', context=context)


# Decorator
def designer_required(view_func):
    @login_required  # Ensure the user is authenticated
    @wraps(view_func)  # Preserve the original function's metadata
    def wrapper(request, *args, **kwargs):
        if request.user.profile.designer:
            return view_func(request, *args, **kwargs)  # Continue to the view
        else:
            return HttpResponseForbidden()  # 403 Forbidden
    return wrapper

def designer_required_class_based_view(view_class):
    """Decorator to apply to class-based views."""
    view_class.dispatch = method_decorator(designer_required)(view_class.dispatch)
    return view_class

def player_required(view_func):
    @login_required  # Ensure the user is authenticated
    @wraps(view_func)  # Preserve the original function's metadata
    def wrapper(request, *args, **kwargs):
        if request.user.profile.player:
            return view_func(request, *args, **kwargs)  # Continue to the view
        else:
            return HttpResponseForbidden()  # 403 Forbidden
    return wrapper

def player_required_class_based_view(view_class):
    """Decorator to apply to class-based views."""
    view_class.dispatch = method_decorator(player_required)(view_class.dispatch)
    return view_class

def admin_required(view_func):
    @login_required  # Ensure the user is authenticated
    @wraps(view_func)  # Preserve the original function's metadata
    def wrapper(request, *args, **kwargs):
        if request.user.profile.admin:
            return view_func(request, *args, **kwargs)  # Continue to the view
        else:
            return HttpResponseForbidden()  # 403 Forbidden
    return wrapper

def admin_required_class_based_view(view_class):
    """Decorator to apply to class-based views."""
    view_class.dispatch = method_decorator(admin_required)(view_class.dispatch)
    return view_class


@login_required
@bookmark_toggle(Profile)
def bookmark_player(request, object):
    return render(request, 'the_gatehouse/partials/bookmarks.html', {'player': object })


@player_required
def add_player(request):
    if request.method == 'POST' and request.htmx:
        form = PlayerCreateForm(request.POST)
        if form.is_valid():
            player = form.save(commit=False)  # Save the new player to the database
            player.display_name = player.discord
            player.save()
            response_data = {
                'id': player.id,
                'discord': player.discord,  # Include the new player's details
                'message': f'Player {player.discord} registered successfully!',
            }
            return JsonResponse(response_data)  # Return a success JSON response
        else:
            # If the form is invalid, include the form errors in the response
            errors = form.errors.as_json()  # Serialize the errors into JSON format
            return JsonResponse({'error': errors}, status=400)  # Return the errors in JSON

    return JsonResponse({'error': 'Invalid request'}, status=400)  # Return a 400 error for invalid requests