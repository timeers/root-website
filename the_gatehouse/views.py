from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.http import JsonResponse, Http404
from django.contrib.auth.decorators import login_required
from django.utils.decorators import method_decorator
from django.views.generic import ListView, UpdateView, CreateView
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.paginator import Paginator, EmptyPage
from django.core.exceptions import PermissionDenied
from django.conf import settings
from functools import wraps
from django.urls import reverse
from django.db.models import Count, Q

from .forms import UserRegisterForm, ProfileUpdateForm, PlayerCreateForm, UserManageForm
from .models import Profile


from the_tavern.views import bookmark_toggle
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
        profile = request.user.profile
        
        # Check if user is a designer
        if profile.designer:
            return view_func(request, *args, **kwargs)  # Proceed with the original view
        else:
            raise PermissionDenied() 
    return wrapper

def designer_onboard_required(view_func):
    @login_required  # Ensure the user is authenticated
    @wraps(view_func)  # Preserve the original function's metadata
    def wrapper(request, *args, **kwargs):
        profile = request.user.profile
        
        # Check if user is a designer and if they are onboarded
        if profile.designer:
            if not profile.designer_onboard:
                # Capture the current URL the user is visiting
                next_url = request.GET.get('next', request.path)
                
                # Redirect to onboarding page with `next` as a query parameter
                return redirect(f'{reverse("onboard-user", args=["designer"])}?next={next_url}')

            else:
                return view_func(request, *args, **kwargs)  # Proceed with the original view
        else:
            raise PermissionDenied() 
    return wrapper

def designer_required_class_based_view(view_class):
    """Decorator to apply to class-based views."""
    view_class.dispatch = method_decorator(designer_onboard_required)(view_class.dispatch)
    return view_class

def player_required(view_func):
    @login_required  # Ensure the user is authenticated
    @wraps(view_func)  # Preserve the original function's metadata
    def wrapper(request, *args, **kwargs):
        if request.user.profile.player:
            return view_func(request, *args, **kwargs) 
        else:
            raise PermissionDenied() 
    return wrapper

def player_onboard_required(view_func):
    @login_required  # Ensure the user is authenticated
    @wraps(view_func)  # Preserve the original function's metadata
    def wrapper(request, *args, **kwargs):
        if request.user.profile.player:
            if request.user.profile.player_onboard == False:
                # Capture the current URL the user is visiting
                next_url = request.GET.get('next', request.path)
                
                # Redirect to onboarding page with `next` as a query parameter
                return redirect(f'{reverse("onboard-user", args=["player"])}?next={next_url}')


                # return redirect('onboard-user', user_type = 'player')
            else:
                return view_func(request, *args, **kwargs) 
        else:
            raise PermissionDenied()   # 403 Forbidden
    return wrapper


def player_required_class_based_view(view_class):
    """Decorator to apply to class-based views."""
    view_class.dispatch = method_decorator(player_onboard_required)(view_class.dispatch)
    return view_class

def tester_required(view_func):
    @login_required  # Ensure the user is authenticated
    @wraps(view_func)  # Preserve the original function's metadata
    def wrapper(request, *args, **kwargs):
        if request.user.profile.player and request.user.profile.tester:
            if request.user.profile.tester_onboard == False:
                # Capture the current URL the user is visiting
                next_url = request.GET.get('next', request.path)
                
                # Redirect to onboarding page with `next` as a query parameter
                return redirect(f'{reverse("onboard-user", args=["tester"])}?next={next_url}')

                # return redirect('onboard-user', user_type = 'tester')
            else:
                return view_func(request, *args, **kwargs) 
        else:
            raise PermissionDenied()  # 403 Forbidden
    return wrapper

def tester_required_class_based_view(view_class):
    """Decorator to apply to class-based views."""
    view_class.dispatch = method_decorator(tester_required)(view_class.dispatch)
    return view_class



def admin_required(view_func):
    @login_required  # Ensure the user is authenticated
    @wraps(view_func)  # Preserve the original function's metadata
    def wrapper(request, *args, **kwargs):
        if request.user.profile.admin:
            return view_func(request, *args, **kwargs)  # Continue to the view
        else:
            raise PermissionDenied()   # 403 Forbidden
    return wrapper

def admin_onboard_required(view_func):
    @login_required  # Ensure the user is authenticated
    @wraps(view_func)  # Preserve the original function's metadata
    def wrapper(request, *args, **kwargs):
        if request.user.profile.admin:
            if request.user.profile.admin_onboard == False:
                # Capture the current URL the user is visiting
                next_url = request.GET.get('next', request.path)
                
                # Redirect to onboarding page with `next` as a query parameter
                return redirect(f'{reverse("onboard-user", args=["admin"])}?next={next_url}')

                # return redirect('onboard-user', user_type = 'admin')
            
            else:
                return view_func(request, *args, **kwargs)  # Continue to the view
        else:
            raise PermissionDenied()   # 403 Forbidden
    return wrapper

def admin_required_class_based_view(view_class):
    """Decorator to apply to class-based views."""
    view_class.dispatch = method_decorator(admin_onboard_required)(view_class.dispatch)
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
    games_played = player.games_played
    view_status = 4
    if request.user.is_authenticated:
        view_status = request.user.profile.view_status
    components = player.posts.filter(status__lte=view_status)
    posts_count = components.count()
    context = {
        'player': player,
        'games_played': games_played,
        'posts_count': posts_count,
        }
    return render(request, 'the_gatehouse/profile_detail.html', context=context)


@login_required
def designer_component_view(request, slug):
    # Get the designer object using the slug from the URL path
    designer = get_object_or_404(Profile, slug=slug.lower())
    view_status = 4
    if request.user.is_authenticated:
        view_status = request.user.profile.view_status
    # Get the component from the query parameters
    component = request.GET.get('component')  # e.g., /designer/john-doe/component/?component=some_component
    # Filter posts based on the designer and the component (if provided)
    if component:
        components = designer.posts.filter(component__icontains=component, status__lte=view_status)
    else:
        components = designer.posts.filter(status__lte=view_status)
    # print(f'Components: {components.count()}')
    # Get the total count of components (total posts matching the filter)
    total_count = components.count()
 
    # Pagination
    paginator = Paginator(components, settings.PAGE_SIZE)  # Show 10 posts per page
 
    # Get the current page number from the request (default to 1)
    page_number = request.GET.get('page')  # e.g., ?page=2
    page_obj = paginator.get_page(page_number)  # Get the page object for the current page

    # print(f'Page: {page_number}')
    context = {
        'posts': page_obj,
        'total_count': total_count,  # Pass the total count to the template
        'bookmark_page': False,
        'player': designer,
    }


    if request.htmx:
        return render(request, "the_gatehouse/partials/profile_post_list.html", context)   
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
    # print(f'Components: {components.count()}')
    # Get the total count of components (total posts matching the filter)
    total_count = components.count()
 
    # Pagination
    paginator = Paginator(components, settings.PAGE_SIZE)  # Show 10 posts per page
 
    # Get the current page number from the request (default to 1)
    page_number = request.GET.get('page')  # e.g., ?page=2
    # print("Page", page_number)
    page_obj = paginator.get_page(page_number)  # Get the page object for the current page

    # print(f'Page: {page_number}')
    context = {
        'posts': page_obj,
        'total_count': total_count,  # Pass the total count to the template
        'artist_page': True,
        'player': artist,
    }


    if request.htmx:
        return render(request, "the_gatehouse/partials/profile_post_list.html", context)   
    return render(request, "the_gatehouse/partials/profile_post_list.html", context) 
    return redirect('player-detail', slug=slug)






@login_required
def post_bookmarks(request, slug):
    from django.apps import apps
    Post = apps.get_model('the_keep', 'Post')
    components = Post.objects.filter(postbookmark__player=request.user.profile)
    
    # print(f'Components: {components.count()}')
    # Get the total count of components (total posts matching the filter)
    total_count = components.count()
 
    # Pagination
    paginator = Paginator(components, settings.PAGE_SIZE)  # Show 10 posts per page
 
    # Get the current page number from the request (default to 1)
    page_number = request.GET.get('page')  # e.g., ?page=2
    page_obj = paginator.get_page(page_number)  # Get the page object for the current page
    # print(f'Page: {page_number}')
    context = {
        'posts': page_obj,
        'total_count': total_count,  # Pass the total count to the template
        'bookmark_page': True,
        'player': request.user.profile,
    }
    if request.htmx:
        return render(request, "the_gatehouse/partials/profile_post_list.html", context)   
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
    return render(request, 'the_gatehouse/partials/profile_game_list.html', context=context)
    return redirect('player-detail', slug=slug)


@login_required
def player_games(request, slug):
    player = get_object_or_404(Profile, slug=slug.lower())
    if request.user.is_authenticated:
        if request.user.profile.weird :
            games = player.get_games_queryset()
        else:
            games = player.get_games_queryset().filter(official=True)
    else:
        games = player.get_games_queryset()
    
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
    return render(request, 'the_gatehouse/partials/profile_game_list.html', context=context)

@login_required
def onboard_user(request, user_type=None):
    """
    This view handles the onboarding process for players, designers and admins.
    It updates the onboard fields depending on the user_type passed.
    """
    
    profile = request.user.profile
    if request.method == 'POST':
        
        # Check the user_type and update the corresponding onboarding field
        if user_type == 'player' and not profile.player_onboard:
            if profile.player:
                profile.player_onboard = True
                profile.save()
                messages.info(request, "Welcome!")
            else:
                messages.warning(request, f"You do not have access to the {user_type} role")
        elif user_type == 'tester' and not profile.tester_onboard:
            if profile.tester:
                profile.tester_onboard = True
                profile.save()
                messages.info(request, "Enjoy!")
            else:
                messages.warning(request, f"You do not have access to the {user_type} role")
        elif user_type == 'designer' and not profile.designer_onboard:
            if profile.designer:
                profile.designer_onboard = True
                profile.save()
                messages.info(request, "Welcome, go make some cool stuff!")
            else:
                messages.warning(request, f"You do not have access to the {user_type} role")
        elif user_type == 'admin' and not profile.admin_onboard:
            if profile.admin:
                profile.admin_onboard = True
                profile.save()
                messages.info(request, "Thank you for helping out!")
            else:
                messages.warning(request, f"You do not have access to the {user_type} role")
        else:
            # Handle cases where the user is already onboarded
            messages.info(request, f"You already have access to the {user_type} role")
            
        # Redirect to a relevant page after onboarding
        next_url = request.POST.get('next', 'keep-home')  # Fallback to a default URL if no `next`
        return redirect(next_url)

    context = {
        'user_type': user_type,
        'active_user': profile,
    }

    # Load onboard page with relevant onboard data
    return render(request, 'the_gatehouse/onboard_user.html', context)


@login_required
def onboard_decline(request, user_type=None):
    """
    This view handles the onboarding process for players, testers, designers and admins.
    It updates the onboard fields depending on the user_type passed.
    """
    decline_choices = {
    "admin": "D",
    "designer": "P",
    "player": "P",
    'tester': "T",
    }
    print(user_type)
    print(decline_choices[user_type])
    decline_type = decline_choices[user_type]
    # If a user refuses to accept website policies they will demote themselves
    if request.method == 'POST':
        profile = request.user.profile
        # Refused admin become designers. Refused designers become players. Refused players become banned (deactivated)
        # print(decline_type)
        if decline_type:
            if decline_type == "T":
                profile.tester = False
            else:
                profile.group = decline_type
        else:
             profile.gourp = "B"
        
        
        match user_type:
            case "admin":
                profile.admin_onboard = False
                messages.error(request, "Contact an Administrator if you change your mind")
            case "designer":
                profile.designer_onboard = False
                messages.error(request, "Contact an Administrator if you would like to post")
            case "tester":
                profile.tester_onboard = False
                messages.error(request, "Contact an Administrator if you want to record detailed game data")
            case "player":
                profile.tester_onboard = False
                messages.warning(request, "Once you accept you will be able to record games")
            case _:
                profile.player_onboard = False
                messages.error(request, "An error occured")
        profile.save()

        # Redirect to homepage
        return redirect('keep-home') 
    
    # Load onboard page with relevant onboard data
    return render(request, 'the_gatehouse/onboard_user.html')



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
        efforts = Effort.objects.filter(player=player, game__round=round, game__final=True)
    elif tournament:
        efforts = Effort.objects.filter(player=player, game__round__tournament=tournament, game__final=True)
    else:
        efforts = Effort.objects.filter(player=player, game__test_match=False, game__final=True)

    game_threshold = 2



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
    print(top_factions)
    context = {
        'player': player,
        'selected_tournament': tournament,
        'tournament_round': round,
        'top_factions': most_factions,
        'most_factions': top_factions,
        'all_games': all_games,
        'win_points': win_points,
        'win_rate': win_rate,
    }
    if request.htmx:
        return render(request, 'the_warroom/partials/player_stats.html', context)

    return render(request, 'the_warroom/player_tournament_stats.html', context)


@player_required_class_based_view
class ProfileListView(ListView):
    model = Profile
    template_name = 'the_gatehouse/profiles_list.html'
    context_object_name = 'objects'

        
    # Filter the queryset if there is a search query
    def get_queryset(self):
        queryset = super().get_queryset()

        # Get the search query from the GET parameters
        search_query = self.request.GET.get('search', '')
        
        # If a search query is provided, filter the queryset based on title, category, and shared_by
        if search_query:
            queryset = queryset.filter(
                Q(display_name__icontains=search_query) |
                Q(discord__icontains=search_query) |
                Q(dwd__icontains=search_query)
            )
        queryset = queryset.prefetch_related('efforts')
        queryset = queryset.annotate(
            active_posts_count=Count('posts', filter=Q(posts__status__lte=4), distinct=True),
            complete_games_count=Count('efforts', filter=Q(efforts__game__final=True), distinct=True)
        )
        queryset = queryset.order_by('display_name')
        return queryset


    def get_context_data(self, **kwargs):
        # Add current user to the context data
        context = super().get_context_data(**kwargs)
        context['player_form'] = PlayerCreateForm()

        return context
    
    def render_to_response(self, context, **response_kwargs):
        # Check if it's an HTMX request
        if self.request.headers.get('HX-Request') == 'true':
            # Only return the part of the template that HTMX will update
            return render(self.request, 'the_gatehouse/partials/profiles_list_table.html', context)

        return super().render_to_response(context, **response_kwargs)


@admin_onboard_required
def manage_user(request, slug):
    user = get_object_or_404(Profile, slug=slug.lower())

    # If form is submitted (POST request)
    if request.method == 'POST':
        # Pass request.POST as the first argument and user_to_edit as a keyword argument
        form = UserManageForm(request.POST, user_to_edit=user, current_user=request.user.profile)

        # If the form is valid, save the new user status
        if form.is_valid():
            update_user = False
            update_message = "No changes were made"
            # Handle updating the group status
            if form.cleaned_data.get('group'):
                user.group = form.cleaned_data['group']
                update_user = True
                update_message  = f'{user.name} has been updated.'

            # Handle updating the nominate_admin status
            if form.cleaned_data.get('nominate_admin'):
                # Logic for nominating admin
                if user.admin_nominated and user.admin_nominated != request.user.profile:
                    user.group = "A"
                    user.admin_dismiss = None
                    update_message = f'{user.name} has been promoted to Moderator.'
                else:
                    user.admin_nominated = request.user.profile
                    update_message = f'{user.name} has been recommended as Moderator.'
                update_user = True

            # Handle updating the dismiss_admin status
            if form.cleaned_data.get('dismiss_admin'):
                # Logic for dismissing admin
                if user.admin_dismiss and user.admin_dismiss != request.user.profile:
                    user.group = "D"
                    user.admin_nominated = None
                    update_message = f'{user.name} has been removed from the Moderator group.'
                else:
                    user.admin_dismiss = request.user.profile
                    update_message = f'You have voted to remove {user.name} from the Moderator group.'
                update_user = True

            # Save the user if any change was made
            if update_user:
                user.save()

            # Redirect with a success message
            messages.success(request, update_message)
        else:
            # Handle form validation errors (optional)
            messages.error(request, 'There were errors in the form submission.')

        # Redirect after the form is successfully saved or errors are handled
        return redirect(user.get_absolute_url())

    # If GET request, render the form with the current user's status pre-filled
    else:
        # Pre-populate the form with the current status of the user
        form = UserManageForm(initial={'group': user.group}, user_to_edit=user, current_user=request.user.profile)

    context = {
        'user_to_edit': user,
        'form': form,
    }
    return render(request, 'the_gatehouse/manage_user.html', context)
