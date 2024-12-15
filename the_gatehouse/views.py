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



# Decorators
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
                'message': f"Player '{player.discord} (New)' registered successfully!",
            }
            return JsonResponse(response_data)  # Return a success JSON response
        else:
            # If the form is invalid, include the form errors in the response
            errors = form.errors.as_json()  # Serialize the errors into JSON format
            return JsonResponse({'error': errors}, status=400)  # Return the errors in JSON

    return JsonResponse({'error': 'Invalid request'}, status=400)  # Return a 400 error for invalid requests



@login_required
def player_page_view(request, slug):
    player = get_object_or_404(Profile, slug=slug)
    context = {
        'player': player,
        }
    return render(request, 'the_gatehouse/profile_detail.html', context=context)


@login_required
def designer_component_view(request, slug):
    # Get the designer object using the slug from the URL path
    designer = get_object_or_404(Profile, slug=slug)

    # Get the component from the query parameters
    component = request.GET.get('component')  # e.g., /designer/john-doe/component/?component=some_component
    # Filter posts based on the designer and the component (if provided)
    if component:
        components = designer.posts.filter(component__icontains=component)
    else:
        components = designer.posts.all()
    print(f'Components: {components.count()}')
    # Get the total count of components (total posts matching the filter)
    total_count = components.count()
 
    # Pagination
    paginator = Paginator(components, settings.PAGE_SIZE)  # Show 10 posts per page
 
    # Get the current page number from the request (default to 1)
    page_number = request.GET.get('page')  # e.g., ?page=2
    page_obj = paginator.get_page(page_number)  # Get the page object for the current page

    print(f'Page: {page_number}')
    context = {
        'posts': page_obj,
        'total_count': total_count,  # Pass the total count to the template
        'bookmark_page': False,
        'player': designer,
    }


    if request.htmx:
        return render(request, "the_gatehouse/partials/profile_post_list.html", context)   
 
    return redirect('player-detail', slug=slug)


@login_required
def post_bookmarks(request, slug):
    from django.apps import apps
    Post = apps.get_model('the_keep', 'Post')
    components = Post.objects.filter(postbookmark__player=request.user.profile)
    
    print(f'Components: {components.count()}')
    # Get the total count of components (total posts matching the filter)
    total_count = components.count()
 
    # Pagination
    paginator = Paginator(components, settings.PAGE_SIZE)  # Show 10 posts per page
 
    # Get the current page number from the request (default to 1)
    page_number = request.GET.get('page')  # e.g., ?page=2
    page_obj = paginator.get_page(page_number)  # Get the page object for the current page
    print(f'Page: {page_number}')
    context = {
        'posts': page_obj,
        'total_count': total_count,  # Pass the total count to the template
        'bookmark_page': True,
        'player': request.user.profile,
    }
    if request.htmx:
        return render(request, "the_gatehouse/partials/profile_post_list.html", context)   
 
    return redirect('player-detail', slug=slug)

@login_required
def game_bookmarks(request, slug):
    from django.apps import apps
    Game = apps.get_model('the_warroom', 'Game')
    games = Game.objects.filter(gamebookmark__player=request.user.profile)
    
    # print(f'games: {games.count()}')
    # Get the total count of games (total posts matching the filter)
    total_count = games.count()
 
    # Pagination
    paginator = Paginator(games, settings.PAGE_SIZE)  # Show 10 posts per page
 
    # Get the current page number from the request (default to 1)
    page_number = request.GET.get('page')  # e.g., ?page=2
    page_obj = paginator.get_page(page_number)  # Get the page object for the current page
    # print(f'Page: {page_number}')
    context = {
        'games': page_obj,
        'total_count': total_count,  # Pass the total count to the template
        'page_obj': page_obj,
        'player': request.user.profile,
        'bookmark_page': True
    }
    if request.htmx:
        return render(request, 'the_gatehouse/partials/profile_game_list.html', context=context)
    return redirect('player-detail', slug=slug)


@login_required
def game_list(request, slug):
    player = get_object_or_404(Profile, slug=slug)
    if request.user.profile.weird :
        games = player.get_games_queryset()
    else:
        games = player.get_games_queryset().only_official_components()
    
    # print(f'games: {games.count()}')
    # Get the total count of games (total posts matching the filter)
    total_count = games.count()
 
    # Pagination
    paginator = Paginator(games, settings.PAGE_SIZE)  # Show 10 posts per page
 
    # Get the current page number from the request (default to 1)
    page_number = request.GET.get('page')  # e.g., ?page=2


    quantity_faction = None
    quality_faction = None
    quality_winrate = None
    quantity_count = None
    if not page_number:
            quantity_faction = player.most_used_faction()
            quality_faction = player.most_successful_faction()
            quality_winrate = player.winrate(faction=quality_faction)
            quantity_count = player.games_played(quantity_faction)
    page_obj = paginator.get_page(page_number)  # Get the page object for the current page
    # print(f'Page: {page_number}')
    context = {
        'games': page_obj,
        'total_count': total_count,  # Pass the total count to the template
        'page_obj': page_obj,
        'player': player,
        'bookmark_page': False,
        'quality_faction': quality_faction,
        'quality_winrate': quality_winrate,
        'quantity_faction': quantity_faction,
        'quantity_count': quantity_count,
    }
    if request.htmx:
        return render(request, 'the_gatehouse/partials/profile_game_list.html', context=context)
    return redirect('player-detail', slug=slug)