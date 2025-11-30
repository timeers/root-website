from functools import wraps
from datetime import timedelta

from django.apps import apps
from django.core.paginator import Paginator
from django.core.exceptions import PermissionDenied
from django.core.cache import cache
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import connection
from django.db.models import Count, Q
from django.http import JsonResponse, Http404, HttpResponseBadRequest
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.utils.translation import gettext_lazy as _
from django.utils.translation import activate, get_language
from django.urls import reverse
from django.views.generic import ListView

from the_tavern.views import bookmark_toggle
from the_warroom.models import Tournament, Round, Effort, Game
from the_keep.models import Faction, Post, RulesFile, LawGroup

from .forms import UserRegisterForm, ProfileUpdateForm, PlayerCreateForm, UserManageForm, MessageForm
from .models import Profile, Language, Website
from .services.discordservice import send_rich_discord_message, send_discord_message, update_discord_avatar
from .services.context_service import get_daily_user_summary
from .utils import build_absolute_uri



def trigger_error(request):
    return render(request, '500.html')

def trigger_other_error(request):
    raise Exception("Test 500 error")



def register(request):
    if request.user.is_authenticated:
        return redirect(request.user.profile.get_absolute_url())
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
def user_settings(request):
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
            # Check if user Registered for TTS League
            new_status = p_form.instance.league
            if new_status and not league_status:
                try:
                    # Get the 'Root TTS League' tournament, or return 404 if not found
                    digital_league = get_object_or_404(Tournament, name="Root TTS League")
                    
                    # Add the user to the tournament's players
                    digital_league.players.add(request.user.profile)
                    messages.success(request, f'You are now registered for Root TTS League!')
                except Tournament.DoesNotExist:
                    # Handle the case where the tournament doesn't exist
                    messages.error(request, 'Could not find the Root TTS League.')
            
            messages.success(request, _('Account updated!'))
            return redirect(request.user.profile.get_absolute_url())
        
    else:
        # u_form = UserUpdateForm(instance=request.user)
        p_form = ProfileUpdateForm(instance=request.user.profile)

    context = {
        # 'u_form': u_form, 
        'p_form': p_form
    }

    return render(request, 'the_gatehouse/user_settings.html', context)



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
            messages.error(request, "You do not have full permissions to view this page.")
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
            messages.error(request, "You do not have full permissions to view this page.")
            raise PermissionDenied() 
    return wrapper

def designer_required_class_based_view(view_class):
    """Decorator to apply to class-based views."""
    view_class.dispatch = method_decorator(designer_onboard_required)(view_class.dispatch)
    return view_class


def editor_required(view_func):
    @login_required  # Ensure the user is authenticated
    @wraps(view_func)  # Preserve the original function's metadata
    def wrapper(request, *args, **kwargs):
        profile = request.user.profile
        
        # Check if user is a editor
        if profile.editor:
            return view_func(request, *args, **kwargs)  # Proceed with the original view
        else:
            messages.error(request, "You do not have full permissions to view this page.")
            raise PermissionDenied() 
    return wrapper

def editor_onboard_required(view_func):
    @login_required  # Ensure the user is authenticated
    @wraps(view_func)  # Preserve the original function's metadata
    def wrapper(request, *args, **kwargs):
        profile = request.user.profile
        
        # Check if user is a editor and if they are onboarded
        if profile.editor:
            if not profile.editor_onboard:
                # Capture the current URL the user is visiting
                next_url = request.GET.get('next', request.path)
                
                # Redirect to onboarding page with `next` as a query parameter
                return redirect(f'{reverse("onboard-user", args=["editor"])}?next={next_url}')

            else:
                return view_func(request, *args, **kwargs)  # Proceed with the original view
        else:
            messages.error(request, "You do not have full permissions to view this page.")
            raise PermissionDenied() 
    return wrapper

def editor_required_class_based_view(view_class):
    """Decorator to apply to class-based views."""
    view_class.dispatch = method_decorator(editor_onboard_required)(view_class.dispatch)
    return view_class



def player_required(view_func):
    @login_required  # Ensure the user is authenticated
    @wraps(view_func)  # Preserve the original function's metadata
    def wrapper(request, *args, **kwargs):
        if request.user.profile.player:
            return view_func(request, *args, **kwargs) 
        else:
            messages.error(request, "Please join the Woodland Warriors Discord Server. Once you have joined, log in again to update your profile.")
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
            messages.error(request, "Please join the Woodland Warriors Discord Server. Once you have joined, log in again to update your profile.")
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
            messages.error(request, "You must be a site admin to view this page.")
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
            messages.error(request, "You must be a site admin to view this page.")
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
            if not player.display_name:
                player.display_name = player.discord
            player.save()
            response_data = {
                'id': player.id,
                'discord': player.discord,  # Include the new player's details
                'message': f"Player '{player}' registered successfully!",
            }
            return JsonResponse(response_data)  # Return a success JSON response
        else:
            # If the form is invalid, include the form errors in the response
            errors = form.errors.as_json()  # Serialize the errors into JSON format
            return JsonResponse({'error': errors}, status=400)  # Return the errors in JSON

    return JsonResponse({'error': 'Invalid request'}, status=400)  # Return a 400 error for invalid requests



@login_required
def player_page_view(request, slug=None):
    if slug:
        player = get_object_or_404(Profile, slug=slug.lower())
    else:
        player = request.user.profile
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
    raise Http404("Object not found") 
    # return render(request, "the_gatehouse/partials/profile_post_list.html", context)  
    # return redirect('player-detail', slug=slug)





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
    raise Http404("Object not found")
    # return render(request, "the_gatehouse/partials/profile_post_list.html", context) 
    # return redirect('player-detail', slug=slug)



@login_required
def user_bookmarks(request):
    player = request.user.profile
    context = {
        'player': player,
        }
    return render(request, 'the_gatehouse/user_bookmarks.html', context=context)


@login_required
def post_bookmarks(request, slug):
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
    raise Http404("Object not found")
    # return render(request, "the_gatehouse/partials/profile_post_list.html", context)
    # return redirect('player-detail', slug=slug)

@login_required
def game_bookmarks(request, slug):
    
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
    raise Http404("Object not found")




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
        elif user_type == 'editor' and not profile.editor_onboard:
            if profile.editor:
                profile.editor_onboard = True
                profile.save()
                messages.info(request, "Thank you!")
            else:
                messages.warning(request, f"You do not have access to the {user_type} role")
        elif user_type == 'designer' and not profile.designer_onboard:
            if profile.designer:
                profile.designer_onboard = True
                profile.editor_onboard = True
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
        next_url = request.POST.get('next', 'archive-home')  # Fallback to a default URL if no `next`
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
    "editor": "P",
    "player": "P",
    }
    # print(user_type)
    # print(decline_choices[user_type])
    decline_type = decline_choices[user_type]
    # If a user refuses to accept website policies they will demote themselves
    if request.method == 'POST':
        profile = request.user.profile
        # Refused admin become designers. Refused designers become players. Refused players become banned (deactivated)
        # print(decline_type)
        if decline_type:
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
            case "editor":
                profile.editor_onboard = False
                messages.error(request, "Contact an Administrator if you would like to edit your posts")
            case "player":
                profile.player_onboard = False
                messages.warning(request, "Once you accept you will be able to record games")
            case _:
                profile.player_onboard = False
                messages.error(request, "An error occured")
        profile.save()

        # Redirect to homepage
        return redirect('archive-home') 
    
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

    top_factions = Faction.leaderboard(effort_qs=efforts, game_threshold=game_threshold)
    most_factions = Faction.leaderboard(top_quantity=True, effort_qs=efforts, game_threshold=1)

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
    user_to_edit = get_object_or_404(Profile, slug=slug.lower())

    # If form is submitted (POST request)
    if request.method == 'POST':
        # Pass request.POST as the first argument and user_to_edit as a keyword argument
        form = UserManageForm(request.POST, user_to_edit=user_to_edit, current_user=request.user.profile)

        # If the form is valid, save the new user_to_edit status
        if form.is_valid():
            update_user = False
            update_message = "No changes were made"
            # Handle updating the group status
            if form.cleaned_data.get('group'):
                user_to_edit.group = form.cleaned_data['group']
                update_user = True
                update_message  = f'{user_to_edit.name} has been updated.'
                match user_to_edit.group:
                    case 'D':
                        user_to_edit.admin_onboard = False
                    case 'E':
                        user_to_edit.admin_onboard = False
                        user_to_edit.designer_onboard = False
                    case 'P':
                        user_to_edit.admin_onboard = False
                        user_to_edit.designer_onboard = False
                        user_to_edit.editor_onboard = False
                    case 'B':
                        user_to_edit.admin_onboard = False
                        user_to_edit.designer_onboard = False
                        user_to_edit.editor_onboard = False
                        user_to_edit.player_onboard = False
            
            if form.cleaned_data.get('dwd'):
                user_to_edit.dwd = form.cleaned_data['dwd']
                update_user = True
                update_message  = f'{user_to_edit.name} has been updated.'

            # Handle updating the nominate_admin status
            if form.cleaned_data.get('nominate_admin'):
                # Logic for nominating admin
                if user_to_edit.admin_nominated and user_to_edit.admin_nominated != request.user.profile:
                    user_to_edit.group = "A"
                    user_to_edit.admin_dismiss = None
                    update_message = f'{user_to_edit.name} has been promoted to Moderator.'
                else:
                    user_to_edit.admin_nominated = request.user.profile
                    update_message = f'{user_to_edit.name} has been recommended as Moderator.'
                update_user = True

            # Handle updating the dismiss_admin status
            if form.cleaned_data.get('dismiss_admin'):
                # Logic for dismissing admin
                if user_to_edit.admin_dismiss and user_to_edit.admin_dismiss != request.user.profile:
                    user_to_edit.group = "D"
                    user_to_edit.admin_nominated = None
                    user_to_edit.admin_onboard = False
                    update_message = f'{user_to_edit.name} has been removed from the Moderator group.'
                else:
                    user_to_edit.admin_dismiss = request.user.profile
                    update_message = f'You have voted to remove {user_to_edit.name} from the Moderator group.'
                update_user = True



            # Save the user if any change was made
            if update_user:
                user_to_edit.save()
                send_discord_message(f'{user_to_edit.discord} ({user_to_edit.group}) updated by {request.user.profile.discord}', category='user_updates')

            # Redirect with a success message
            messages.success(request, update_message)
            # Redirect after the form is successfully saved
            return redirect(user_to_edit.get_absolute_url())
        else:
            # Handle form validation errors (optional)
            messages.error(request, 'There were errors in the form submission.')



    # If GET request, render the form with the current user's status pre-filled
    else:
        # Pre-populate the form with the current status of the user
        form = UserManageForm(initial={'group': user_to_edit.group}, user_to_edit=user_to_edit, current_user=request.user.profile)

    context = {
        'user_to_edit': user_to_edit,
        'form': form,
    }
    return render(request, 'the_gatehouse/manage_user.html', context)


def get_feedback_context(request, message_category, feedback_subject=None):
    # Title Mapping for message categories
    title_mapping = {
        'general': 'General Feedback',
        'bug': 'Bug Report',
        'feature': 'Feature Request',
        'usability': 'Usability Feedback',
        'outdated': 'Outdated Information',
        'incorrect': 'Incorrect Information',
        'translation': 'Existing Translation Missing',
        'offensive': 'Offensive Image/Language',
        'spam': 'Spam',
        'faction': 'Faction Request',
        'map': 'Map Request',
        'deck': 'Deck Request',
        'other': 'Other',
        'weird-root': 'Weird Root',
        'french-root': 'French Root'
    }

    response_mapping = {
        "feedback": _('Thank you for your feedback!'),
        "request": _('Your request has been received'),
        "report": _('Your report has been received'),
        "weird-root": _('Your request has been received, you should receive a Discord DM once an admin sees your request.'),
        "french-root": _('Your request has been received'),
        "bug": _('Thank you for your report'),
    }

    # Page Title Logic
    if message_category == 'report':
        page_title = _('Report {subject}').format(subject=feedback_subject)
    elif message_category == 'weird-root':
        page_title = _('Request Invite to Weird Root')
    elif message_category == 'french-root':
        page_title = _('Request Invite to French Root')
    elif message_category == 'bug':
        page_title = _('Bug Report')
    elif message_category == 'request':
        page_title = _('Request a New Post')
    elif message_category:
        page_title = _('Send {category}').format(category=message_category.title())
    else:
        page_title = _('Send Feedback')


    # Authentication Check
    if not request.user.is_authenticated and (message_category == 'request' or message_category == 'weird-root' or message_category == 'french-root'):
        messages.error(request, "You must be logged in to view this page.")
        raise PermissionDenied()

    # Author determination logic
    if not request.user.is_authenticated:
        author = None
    else:
        author = request.user.profile.discord

    # Form processing
    if request.method == 'POST':
        form = MessageForm(request.POST, author=author, message_category=message_category)
        if form.is_valid():
            title = form.cleaned_data['title']
            message_title = title_mapping.get(title, "General")

            message = form.cleaned_data['message']
            fields = []
            if not request.user.is_authenticated:
                from_user = form.cleaned_data['author']
                if not from_user:
                    from_user = "Anonymous User"
            else:
                from_user = f'[{request.user}]({build_absolute_uri(request, request.user.profile.get_absolute_url())})'

            fields.append({'name': 'From', 'value': from_user})

            # Add subject if provided
            if feedback_subject:
                fields.append({'name': 'Subject', 'value': feedback_subject})

            # Send Discord message
            send_rich_discord_message(message, author_name=author, category=message_category, title=message_title, fields=fields)

            # Set success response message
            response_message = response_mapping.get(message_category, 'Your message has been sent!')
            messages.success(request, response_message)
            # return redirect('archive-home')
    else:
        form = MessageForm(author=author, message_category=message_category)

    return {
        'form': form,
        'title': page_title
    }


def discord_feedback(request):

    message_category = request.GET.get('category', 'feedback')  # Default to 'feedback' if not provided
    feedback_subject = request.GET.get('feedback_subject', None)  # Default to None if not provided

    if message_category == 'report':
        page_title = f'Report {feedback_subject}'
    elif message_category == 'weird-root':
        page_title = f'Request Invite to Weird Root'
    elif message_category == 'french-root':
        page_title = f'Request Invite to French Root'
    elif message_category:
        page_title = f"Send {message_category.title()}"
    else:
        page_title = "Send Feedback"

    if not request.user.is_authenticated and (message_category == 'request' or message_category == 'weird-root' or message_category == 'french-root'):
        messages.error(request, "You must be logged in to view this page.")
        raise PermissionDenied() 

    response_mapping = {
        "feedback": _('Thank you for your feedback!'),
        "request": _('Your request has been received'),
        "report": _('Your report has been received'),
        "weird-root": _('Your request has been received, you should receive a Discord DM once an admin sees your request.'),
        "french-root": _('Your request has been received, you should receive a Discord DM once an admin sees your request.'),
    }
    title_mapping = {
        'general': 'General Feedback',
        'bug': 'Bug Report',
        'feature': 'Feature Request',
        'usability': 'Usability Feedback',
        'outdated': 'Outdated Information',
        'incorrect': 'Incorrect Information',
        'translation': 'Existing Translation Missing',
        'offensive': 'Offensive Image/Language',
        'spam': 'Spam',
        'faction': 'Faction Request',
        'map': 'Map Request',
        'deck': 'Deck Request',
        'other': 'Other',
        'weird-root': 'Weird Root',
        'french-root': 'French Root'
    }

    if not request.user.is_authenticated:
        author = None
    else:
        author = request.user.profile.discord

    # form_class = form_mapping.get(message_category, FeedbackForm)

    if request.method == 'POST':
        form = MessageForm(request.POST, author=author, message_category=message_category)
        if form.is_valid():
            # Get form data
            title = form.cleaned_data['title']
            message_title = title_mapping.get(title, "General")
            
            message = form.cleaned_data['message']
            fields = []
            author = None
            if not request.user.is_authenticated:
                # author = form.cleaned_data['author']
                from_user = form.cleaned_data['author']
                if not from_user:
                    from_user = "Anonymous User"
            else:
                # author = request.user.profile.discord
                from_user = f'[{request.user}]({build_absolute_uri(request, request.user.profile.get_absolute_url())})'

            fields.append({
                    'name': 'From',
                    'value': from_user
                })
            # Add in subject
            if feedback_subject:
                fields.append({
                    'name': 'Subject', 
                    'value': feedback_subject
                    })
                
            # Call the function to send the message to Discord
            send_rich_discord_message(message, author_name=author, category=message_category, title=message_title, fields=fields)
            # Redirect and return a success message
            response_message = response_mapping.get(message_category, 'Your message has been sent!')
            messages.success(request, response_message)
            return redirect('archive-home')
    else:
        form = MessageForm(author=author, message_category=message_category)


    context = {
        'form': form,
        'title': page_title
    }

    return render(request, 'the_gatehouse/discord_feedback.html', context)



def general_feedback(request):
    message_category = 'feedback'
    feedback_subject = None

    context = get_feedback_context(request, message_category=message_category, feedback_subject=feedback_subject)

    # If form is valid (i.e., handled in the utility function)
    if request.method == 'POST' and context.get('form').is_valid():
        return redirect('archive-home')

    return render(request, 'the_gatehouse/discord_feedback.html', context)

def post_feedback(request, slug):

    post = get_object_or_404(Post, slug=slug)

    language = get_language()
    language_object = Language.objects.filter(code=language).first()
    object_translation = post.translations.filter(language=language_object).first()
    object_title = object_translation.translated_title if object_translation and object_translation.translated_title else post.title


    message_category = 'report'
    feedback_subject = f'{post.get_component_display()}: {object_title}'

    context = get_feedback_context(request, message_category=message_category, feedback_subject=feedback_subject)

    # If form is valid (i.e., handled in the utility function)
    if request.method == 'POST' and context.get('form').is_valid():
        return redirect(post.get_absolute_url())

    return render(request, 'the_gatehouse/discord_feedback.html', context)

@player_required
def post_request(request):

    if request.user.profile.designer:
        return redirect('new-components')

    message_category = 'request'

    context = get_feedback_context(request, message_category=message_category)

    # If form is valid (i.e., handled in the utility function)
    if request.method == 'POST' and context.get('form').is_valid():
        return redirect('archive-home')

    return render(request, 'the_gatehouse/discord_feedback.html', context)


@player_required
def player_feedback(request, slug):

    player = get_object_or_404(Profile, slug=slug.lower())

    message_category = 'report'
    feedback_subject = f'User: {player.discord}'

    context = get_feedback_context(request, message_category=message_category, feedback_subject=feedback_subject)

    # If form is valid (i.e., handled in the utility function)
    if request.method == 'POST' and context.get('form').is_valid():
        return redirect(player.get_absolute_url())

    return render(request, 'the_gatehouse/discord_feedback.html', context)

def game_feedback(request, id):

    game = get_object_or_404(Game, id=id)

    message_category = 'report'
    feedback_subject = f'Game: {id}'

    context = get_feedback_context(request, message_category=message_category, feedback_subject=feedback_subject)

    # If form is valid (i.e., handled in the utility function)
    if request.method == 'POST' and context.get('form').is_valid():
        return redirect(game.get_absolute_url())

    return render(request, 'the_gatehouse/discord_feedback.html', context)



def law_feedback(request, slug, lang_code):

    language = Language.objects.filter(code=lang_code).first()
    law_group = get_object_or_404(LawGroup, slug=slug)
    prime_law = law_group.get_prime_law(language=language)

    message_category = 'report'
    feedback_subject = f'Law: {law_group} ({lang_code})'

    context = get_feedback_context(request, message_category=message_category, feedback_subject=feedback_subject)

    # If form is valid (i.e., handled in the utility function)
    if request.method == 'POST' and context.get('form').is_valid():
        return redirect(prime_law.get_absolute_url())

    return render(request, 'the_gatehouse/discord_feedback.html', context)

def faq_feedback(request, slug=None):

    if slug:
        post = get_object_or_404(Post, slug=slug)
    else:
        post = None

    message_category = 'report'
    if post:
        feedback_subject = f'{post.title}: FAQ'
    else:
        feedback_subject = 'FAQ Feedback'

    context = get_feedback_context(request, message_category=message_category, feedback_subject=feedback_subject)

    # If form is valid (i.e., handled in the utility function)
    
    if request.method == 'POST' and context.get('form').is_valid():
        if post:
            return redirect(post.get_absolute_url())
        else:
            return redirect('faq-home')

    return render(request, 'the_gatehouse/discord_feedback.html', context)


def bug_report(request):

    feedback_subject = "Bug Report"
    message_category = 'bug'
    
    context = get_feedback_context(request, message_category=message_category, feedback_subject=feedback_subject)

    # If form is valid (i.e., handled in the utility function)
    if request.method == 'POST' and context.get('form').is_valid():
        return redirect('site-home')

    return render(request, 'the_gatehouse/discord_feedback.html', context)


@player_required
def weird_root_invite(request, slug=None):
    if slug:
        post = get_object_or_404(Post, slug=slug)
        feedback_subject = f'{post.component}: {post.title}'
    else:
        feedback_subject = "Generic Invite"
    message_category = 'weird-root'
    

    context = get_feedback_context(request, message_category=message_category, feedback_subject=feedback_subject)

    # If form is valid (i.e., handled in the utility function)
    if request.method == 'POST' and context.get('form').is_valid():
        request.user.profile.in_weird_root = True
        request.user.profile.save()
        if slug:
            return redirect(post.get_absolute_url())
        else:
            return redirect('archive-home')


    return render(request, 'the_gatehouse/discord_feedback.html', context)

@player_required
def french_root_invite(request, slug=None):
    if slug:
        post = get_object_or_404(Post, slug=slug)
        feedback_subject = f'{post.component}: {post.title}'
    else:
        feedback_subject = "Generic Invite"
    message_category = 'french-root'
    

    context = get_feedback_context(request, message_category=message_category, feedback_subject=feedback_subject)

    # If form is valid (i.e., handled in the utility function)
    if request.method == 'POST' and context.get('form').is_valid():
        request.user.profile.in_french_root = True
        request.user.profile.save()
        if slug:
            return redirect(post.get_absolute_url())
        else:
            return redirect('archive-home')

    return render(request, 'the_gatehouse/discord_feedback.html', context)

@login_required
def join_discord_server(request):
    origin = request.GET.get("origin")
    invite = request.GET.get("invite")

    config = Website.get_singular_instance()
    invite_map = {
        config.french_root_invite: "in_french_root",
        config.woodland_warriors_invite: "in_woodland_warriors",
        config.weird_root_invite: "in_weird_root",
    }

    field_to_update = invite_map.get(invite)
    if field_to_update:
        setattr(request.user.profile, field_to_update, True)
        request.user.profile.save()

    return redirect(origin or "/")


def status_check(request):
    # Check database connection
    try:
        # Run a simple database query to check if it's responding
        with connection.cursor() as cursor:
            cursor.execute('SELECT 1')
    except Exception as e:
        return JsonResponse({'status': 'error', 'message': f'Database error: {str(e)}'}, status=500)

    # Check if the cache is working (optional, if you're using Django caching)
    try:
        cache.set('status_check', 'ok', timeout=1)
        if cache.get('status_check') != 'ok':
            raise Exception('Cache check failed')
    except Exception as e:
        return JsonResponse({'status': 'error', 'message': f'Cache error: {str(e)}'}, status=500)

    # Return a successful status
    return JsonResponse({'status': 'ok'}, status=200)




def set_language_custom(request):
    lang_code = request.POST.get('language')

    # Validate language code
    if lang_code not in dict(settings.LANGUAGES):
        return HttpResponseBadRequest("Invalid language code")

    # Activate it for the current request
    activate(lang_code)

    # Save it to the session
    request.session['language'] = lang_code
    request.session['django_language'] = lang_code

    # Optional: Save it to the user's profile
    if request.user.is_authenticated:
        profile = getattr(request.user, 'profile', None)
        if profile and hasattr(profile, 'language'):
            lang_obj = Language.objects.filter(code=lang_code).first()
            if lang_obj:
                profile.language = lang_obj
                profile.save()

    # Redirect back (use ?next= in your form or JS to set this)
    next_url = request.POST.get('next') or request.META.get('HTTP_REFERER', '/')
    return redirect(next_url)


@admin_required
def admin_dashboard(request):

    new_rules = RulesFile.objects.filter(status=RulesFile.Status.NEW).count()
    last_law_check = Website.get_singular_instance().last_law_check
    formatted_date = last_law_check.strftime("%B %d, %Y, %-I:%M%p")
    daily_user_summary = get_daily_user_summary()
    admin_users = Profile.objects.filter(group="A", user__isnull=False).count()
    designer_users = Profile.objects.filter(group="D", user__isnull=False).count()
    editor_users = Profile.objects.filter(group="E", user__isnull=False).count()
    registered_users = Profile.objects.filter(group="P", user__isnull=False).count()
    unregistered_users = Profile.objects.filter(group="O", user__isnull=False).count()

    law_url = reverse('manage-law-updates')
    admin_url = reverse('admin:index')
    

        # Example Widget
        # {
        #     "title": "Player Stats",
        #     "subtitle": "Performance summary",
        #     "image": "/static/images/ambush.jpg",
        #     "fields": [
        #         {"title": "Games Played", "content": 42},
        #     ],
        #     "buttons": [
        #         {
        #             "icon": '<i class="bi bi-chevron-right"></i>',
        #             "label": "Games Played", 
        #             "link": 'x', 
        #             "custom": True,
        #             "button_color": 'black', 
        #             "text_color": "white", 
        #             "hover_color": 'blue', 
        #             "text_hover_color": "grey"
        #          },
        #         {
        #             "icon": '<i class="bi bi-chevron-right"></i>',
        #             "label": "Games Played", 
        #             "link": 'x', 
        #             "class": 'primary', 
        #             "size": "sm", 
        #          },
        #     ],
        # },

    widgets = [
        {
            "title": "Law of Root",
            "subtitle": "Sync Status with Leder Card Library",
            "image": "/static/images/ambush.jpg",
            "fields": [
                {"title": "Last Updated", "content": formatted_date},
                {"title": "Available Updates", "content": new_rules},
            ],
            "buttons": [
                {
                    "label": "Manage Updates", 
                    "link": law_url, 
                    "class": 'primary',

                 },
            ],
        },
        {
            "title": "User Stats",
            "subtitle": "Root Database Users",
            "image": "/static/images/ambush.jpg",
            "fields": [
                {"title": "Admin", "content": admin_users},
                {"title": "Designers", "content": designer_users},
                {"title": "Editors", "content": editor_users},
                {"title": "Registered", "content": registered_users},
                {"title": "Unregistered", "content": unregistered_users},
            ]
        },
        {
            "title": f"Daily Users – {daily_user_summary['date'].strftime('%b %d, %Y')}",
            "subtitle": daily_user_summary["message"],
            "image": "/static/images/ambush.jpg",
            "fields": [
                {"title": f["name"], "content": f["value"]} for f in daily_user_summary["fields"]
            ]
        },

        {
            "title": "Admin Site",
            "subtitle": "View the Django Admin Site",
            "image": "/static/images/ambush.jpg",
            "buttons": [
                {
                    "label": "Go To Admin", 
                    "link": admin_url, 
                    "class": 'primary',
                    "size": 'lg',

                 },
            ],
        },

    ]


    context = {
        'widgets': widgets,
    }

    return render(request, 'the_gatehouse/admin_dashboard.html', context) 

@login_required
def sync_discord_avatar(request):
    profile = request.user.profile

    cooldown = timedelta(seconds=60)

    if profile.last_avatar_sync and timezone.now() - profile.last_avatar_sync < cooldown:
        messages.warning(request, "Please wait before syncing again.")
        return redirect(request.META.get('HTTP_REFERER', '/'))


    result = update_discord_avatar(request.user, force=True)

    profile.last_avatar_sync = timezone.now()
    profile.save()

    if result:
        messages.success(request, "Your Discord avatar has been synced successfully!")
    else:
        messages.warning(request, "Could not sync avatar. Make sure your Discord account is connected.")

    return redirect(request.META.get('HTTP_REFERER', '/'))