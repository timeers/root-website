from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.http import HttpResponseForbidden, JsonResponse
from django.contrib.auth.decorators import login_required
from django.utils.decorators import method_decorator
from django.views.generic import DetailView, ListView
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.paginator import Paginator, EmptyPage
from django.conf import settings
from functools import wraps

from .forms import UserRegisterForm, UserUpdateForm, ProfileUpdateForm, PlayerCreateForm
from .models import Profile

from the_tavern.views import bookmark_toggle
from the_warroom.filters import GameFilter
from the_warroom.models import Tournament, Round, Effort



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
    league_status = request.user.profile.league
    if request.method == 'POST':
        # u_form = UserUpdateForm(request.POST, instance=request.user)
        p_form = ProfileUpdateForm(request.POST, 
                                   request.FILES, 
                                   instance=request.user.profile)
        # if u_form.is_valid() and p_form.is_valid():
        if p_form.is_valid():
            # u_form.save()
            p_form.save()
            # Check if user Registered for Digital League
            new_status = p_form.instance.league
            if new_status and not league_status:
                try:
                    # Get the 'Root Digital League' tournament, or return 404 if not found
                    digital_league = get_object_or_404(Tournament, name="Root Digital League")
                    
                    # Add the user to the tournament's players
                    digital_league.players.add(request.user.profile)
                    messages.success(request, f'You are now registered for Root Digital League!')
                except Tournament.DoesNotExist:
                    # Handle the case where the tournament doesn't exist
                    messages.error(request, 'Could not find the Root Digital League.')

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
    player = get_object_or_404(Profile, slug=slug.lower())
    context = {
        'player': player,
        }
    return render(request, 'the_gatehouse/profile_detail.html', context=context)


@login_required
def designer_component_view(request, slug):
    # Get the designer object using the slug from the URL path
    designer = get_object_or_404(Profile, slug=slug.lower())

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
def artist_component_view(request, slug):
    # Get the artist object using the slug from the URL path
    artist = get_object_or_404(Profile, slug=slug.lower())

    # Get the component from the query parameters
    component = request.GET.get('component')  # e.g., /artist/john-doe/component/?component=some_component
    # Filter posts based on the artist and the component (if provided)
    if component:
        components = artist.artist_posts.filter(component__icontains=component)
    else:
        components = artist.artist_posts.all()
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
        'player': artist,
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
    player = get_object_or_404(Profile, slug=slug.lower())
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
    quality_count = None
    quantity_count = None
    if not page_number:
            quantity_faction = player.most_used_faction()
            quantity_count = player.games_played(quantity_faction)

            quality_faction = player.most_successful_faction()
            quality_winrate = player.winrate(faction=quality_faction)
            quality_count = player.games_won(quality_faction)
            
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
        'quality_count': quality_count,
        'quantity_faction': quantity_faction,
        'quantity_count': quantity_count,
    }
    if request.htmx:
        return render(request, 'the_gatehouse/partials/profile_game_list.html', context=context)
    return redirect('player-detail', slug=slug)

@login_required
def onboard_user(request, user_type=None):
    """
    This view handles the onboarding process for players, designers and admins.
    It updates the onboard fields depending on the user_type passed.
    """
    if request.method == 'POST':
        profile = request.user.profile
        # Check the user_type and update the corresponding onboarding field
        if user_type == 'player' and not profile.player_onboard:
            profile.player_onboard = True
            profile.save()
            messages.info(request, "Welcome!")
        elif user_type == 'designer' and not profile.designer_onboard:
            profile.designer_onboard = True
            profile.save()
            messages.info(request, "Have fun!")
        elif user_type == 'admin' and not profile.admin_onboard:
            profile.admin_onboard = True
            profile.save()
            messages.info(request, "Thank you for helping out!")
        else:
            # Handle cases where the user is already onboarded
            if user_type == 'player':
                messages.info(request, "You are already onboarded as a player.")
            elif user_type == 'designer':
                messages.info(request, "You are already onboarded as a designer.")
            elif user_type == 'admin':
                messages.info(request, "You are already onboarded as an admin.")
        request.session.pop('onboard_data', None)
        # Redirect to a relevant page after onboarding
        return redirect('keep-home') 
    onboard_data = request.session.get('onboard_data', {})
    # Load onboard page with relevant onboard data
    return render(request, 'the_gatehouse/onboard_user.html', onboard_data)


@login_required
def onboard_decline(request):
    """
    This view handles the onboarding process for players, designers and admins.
    It updates the onboard fields depending on the user_type passed.
    """
    # If a user refuses to accept website policies they will demote themselves
    if request.method == 'POST':
        decline_type = request.GET.get('type')
        profile = request.user.profile
        # Refused admin become designers. Refused designers become players. Refused players become banned (deactivated)
        if decline_type == "D" or decline_type == "P" or decline_type == "B":
            profile.group = decline_type
        else:
            profile.group = "B"
        profile.admin_onboard = False
        profile.designer_onboard = False
        profile.player_onboard = False
        profile.save()
        match decline_type:
            case "D":
                messages.error(request, "No problem. Contact an Administrator if you change your mind")
            case "P":
                messages.error(request, "No problem. Contact an Administrator if you change your mind")
            case _:
                messages.error(request, "We're sorry to see you go")

        request.session.pop('onboard_data', None)
        # Redirect to homepage
        return redirect('keep-home') 
    
    onboard_data = request.session.get('onboard_data', {})
    # Load onboard page with relevant onboard data
    return render(request, 'the_gatehouse/onboard_user.html', onboard_data)



@player_required
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

    game_threshold = 1
    if not tournament and not round:
        if efforts.count() > 200:
            game_threshold = 10
        elif efforts.count() > 100:
            game_threshold = 5
        elif efforts.count() > 10:
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

    # print("Stat Lookup", f'tournament-{tournament}', f'round-{round}', f'game threshold-{game_threshold}')
    top_factions = player.faction_stats(tournament=tournament, round=round, game_threshold=game_threshold)
    most_factions = player.faction_stats(most_wins=True, tournament=tournament, round=round)

    context = {
        'player': player,
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