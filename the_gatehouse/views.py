from functools import wraps
from datetime import timedelta

from django.apps import apps
from django.core.paginator import Paginator, EmptyPage
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

from .forms import UserRegisterForm, ProfileUpdateForm, PlayerCreateForm, UserManageForm, MessageForm, GuildJoinRequestForm
from .models import Profile, Language, Website, Changelog, ChangelogEntry, DiscordGuild, DiscordGuildJoinRequest
from .services.discordservice import send_rich_discord_message, send_discord_message, update_discord_avatar, get_discord_invite_info
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
def bookmark_player(request, obj):
    return render(request, 'the_gatehouse/partials/bookmarks.html', {'player': obj})


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
                'discord': str(player),  # Include the new player's details
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
    submissions = player.submissions.filter(status='9')
    posts_count = components.count()
    context = {
        'player': player,
        'games_played': games_played,
        'posts_count': posts_count,
        'submissions': submissions,
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
    component = request.GET.get('component')
    # Filter posts based on the designer and the component (if provided)
    components = Post.objects.filter(
        (Q(designer=designer) |
        Q(co_designers=designer)) &
        Q(status__lte=view_status)
    ).distinct()
    if component:
        components = components.filter(component__icontains=component)
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
def submitted_component_view(request, slug):
    # Get the artist object using the slug from the URL path
    profile = get_object_or_404(Profile, slug=slug.lower())

    # Get the component from the query parameters
    component = request.GET.get('component')  # e.g., /artist/john-doe/component/?component=some_component
    # Filter posts based on the artist and the component (if provided)
    if component:
        components = profile.submissions.filter(component__icontains=component, status='9')
    else:
        components = profile.submissions.filter(status='9')
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
        'submitted_page': True,
        'player': profile,
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
        if next_url:
            return redirect(next_url)
        else:
            return redirect('site-home')

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

    language_code = get_language()
    language = Language.objects.filter(code=language_code).first()
    object_translation = post.translations.filter(language=language).first()
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

    if request.user.profile.player:
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



def law_feedback(request, slug, language_code):

    language = Language.objects.filter(code=language_code).first()
    law_group = get_object_or_404(LawGroup, slug=slug)
    prime_law = law_group.get_prime_law(language=language)

    message_category = 'report'
    feedback_subject = f'Law: {law_group} ({language_code})'

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
    language_code = request.POST.get('language')

    # Validate language code
    if language_code not in dict(settings.LANGUAGES):
        return HttpResponseBadRequest("Invalid language code")

    # Activate it for the current request
    activate(language_code)

    # Save it to the session
    request.session['language'] = language_code
    request.session['django_language'] = language_code

    # Optional: Save it to the user's profile
    if request.user.is_authenticated:
        profile = getattr(request.user, 'profile', None)
        if profile and hasattr(profile, 'language'):
            lang_obj = Language.objects.filter(code=language_code).first()
            if lang_obj:
                profile.language = lang_obj
                profile.save()

    # Redirect back (use ?next= in your form or JS to set this)
    next_url = request.POST.get('next') or request.META.get('HTTP_REFERER', '/')
    return redirect(next_url)


@admin_onboard_required
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

    # Guild invites
    pending_guild_invites_count = DiscordGuildJoinRequest.objects.filter(
        status=DiscordGuildJoinRequest.Status.PENDING
    ).count()

    # Pending posts (submitted status = '9')
    from the_keep.models import StatusChoices
    pending_posts_count = Post.objects.filter(status=StatusChoices.SUBMITTED).count()

    law_url = reverse('manage-law-updates')
    admin_url = reverse('admin:index')
    guild_invites_url = reverse('pending-guild-invites')
    pending_posts_url = reverse('pending-posts')
    

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
            "title": "Pending Posts",
            "subtitle": "Posts Awaiting Review",
            "image": "/static/images/ambush.jpg",
            "fields": [
                {"title": "Submitted Posts", "content": pending_posts_count},
            ],
            "buttons": [
                {
                    "label": "View Pending Posts",
                    "link": pending_posts_url,
                    "class": 'primary' if pending_posts_count > 0 else 'secondary',
                 },
            ],
        },
        {
            "title": "Guild Invites",
            "subtitle": "Discord Guild Join Requests",
            "image": "/static/images/ambush.jpg",
            "fields": [
                {"title": "Pending Requests", "content": pending_guild_invites_count},
            ],
            "buttons": [
                {
                    "label": "Manage Invites",
                    "link": guild_invites_url,
                    "class": 'primary' if pending_guild_invites_count > 0 else 'secondary',
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

        # {
        #     "title": "Admin Site",
        #     "subtitle": "View the Django Admin Site",
        #     "image": "/static/images/ambush.jpg",
        #     "buttons": [
        #         {
        #             "label": "Go To Admin", 
        #             "link": admin_url, 
        #             "class": 'primary',
        #             "size": 'lg',

        #          },
        #     ],
        # },

    ]


    context = {
        'widgets': widgets,
        'admin_url': admin_url,
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


def latest_changelog_redirect(request):
    latest_changelog = Changelog.objects.first()
    return redirect('changelog-select-view', slug=latest_changelog.slug)

def changelog_select_view(request, slug):

    selected_changelog = get_object_or_404(Changelog, slug=slug)
    all_changelogs = Changelog.objects.all()
    title = "Updates"
    meta_title = f'Update {selected_changelog.version} - {selected_changelog.title} ({selected_changelog.date.strftime("%B %Y")})'
    if selected_changelog.description:
        meta_description = f"{selected_changelog.description}"
    else:
        meta_description = ''

    if hasattr(request, 'htmx') and request.htmx:
        template_name = 'the_gatehouse/partials/changelog_list.html'
    else:
        template_name = 'the_gatehouse/changelog_select.html'

    # Pagination
    paginate_by = settings.PAGE_SIZE
    paginator = Paginator(all_changelogs, paginate_by)

    # Find which page contains the selected changelog
    selected_page = 1
    for i, changelog in enumerate(all_changelogs):
        if changelog.slug == slug:
            selected_page = (i // paginate_by) + 1
            break

    page_number = request.GET.get('page', selected_page)

    try:
        page_obj = paginator.get_page(page_number)
    except EmptyPage:
        page_obj = paginator.page(paginator.num_pages)

    context = {
        "meta_description": meta_description,
        'meta_title': meta_title,
        "title": title,
        'changelogs': page_obj,
        'is_paginated': paginator.num_pages > 1,
        'page_obj': page_obj,
        'selected_changelog': selected_changelog,
    }

    return render(request, template_name, context)


# Discord Server Invites

@player_required
def guild_join_request(request, guild_id):
    guild = get_object_or_404(DiscordGuild, guild_id=guild_id)
    profile = request.user.profile

    # Get the next URL from query parameters and resolve it
    next_param = request.GET.get('next', '')
    
    # Resolve next_url to an actual path
    if next_param:
        # If it's already a path (starts with /), use it
        if next_param.startswith('/'):
            next_url = next_param
        else:
            # Otherwise try to reverse it as a URL name
            try:
                next_url = reverse(next_param)
            except:
                # If that fails, default to profile
                next_url = reverse('profile')
    else:
        # No next parameter, use default
        next_url = reverse('profile')

    is_member = profile.guilds.filter(guild_id=guild_id).exists()

    if is_member:
        messages.info(request, f"You are already a member of {guild.name}.")
        return redirect(next_url)

    # Check for existing requests
    existing_request = DiscordGuildJoinRequest.objects.filter(
        profile=profile,
        guild=guild,
    ).first()

    if existing_request:
        if existing_request.status == DiscordGuildJoinRequest.Status.PENDING:
            messages.info(request, f"You already have a pending request for {guild.name}.")
            return redirect(next_url)
        elif existing_request.status == DiscordGuildJoinRequest.Status.APPROVED:
            # Redirect to guild invite page where they can join
            messages.success(request, f"Your request to join {guild.name} has been approved!")
            return redirect(f"{reverse('guild-invite', kwargs={'guild_id': guild_id})}?next={next_url}")
        elif existing_request.status == DiscordGuildJoinRequest.Status.REJECTED:
            # Allow resubmission - delete old rejected request
            existing_request.delete()
            messages.warning(request, f"Your previous request was rejected. You may submit a new request.")
        elif existing_request.status == DiscordGuildJoinRequest.Status.WITHDRAWN:
            # Allow resubmission - delete old withdrawn request
            existing_request.delete()
            messages.info(request, f"You previously withdrew your request. You may submit a new one.")
        elif existing_request.status == DiscordGuildJoinRequest.Status.COMPLETED:
            # They completed the join but aren't a member yet?
            messages.info(request, f"You have a completed request for {guild.name}. Please contact an admin.")
            return redirect(next_url)

    if request.method == "POST":
        form = GuildJoinRequestForm(request.POST)
        if form.is_valid():
            if guild.auto_approve_invite:
                DiscordGuildJoinRequest.objects.create(
                    profile=profile,
                    guild=guild,
                    request_message=form.cleaned_data["request_message"],
                    agreement_message=form.cleaned_data["agreement_message"],
                    acknowledgement=form.cleaned_data["acknowledgement"],
                    status=DiscordGuildJoinRequest.Status.APPROVED,
                )
                if next_url:
                    return redirect(f"{reverse('guild-invite', kwargs={'guild_id': guild_id})}?next={next_url}")
                return redirect("guild-invite", guild_id=guild_id)
            else:
                DiscordGuildJoinRequest.objects.create(
                    profile=profile,
                    guild=guild,
                    request_message=form.cleaned_data["request_message"],
                    agreement_message=form.cleaned_data["agreement_message"],
                    acknowledgement=form.cleaned_data["acknowledgement"],
                    status=DiscordGuildJoinRequest.Status.PENDING,
                )
                fields = []
                fields.append({
                    'name': 'Request', 
                    'value': form.cleaned_data["request_message"]
                    })
                fields.append({
                    'name': 'Acknowledgement',
                    'value': form.cleaned_data["agreement_message"]
                })
                message = f'{profile.discord} would like to join {guild.name}'
                send_rich_discord_message(message, author_name=None, category='weird-root', title=f'Invite Request', fields=fields)
                messages.info(request, "Your request has been submitted. Please check back here later.")
                return redirect(next_url)
    else:
        form = GuildJoinRequestForm()

    title = f'{guild.name} Invite'

    return render(request, "the_gatehouse/join_guild.html", {
        "guild": guild,
        "form": form,
        "next_url": next_url,
        "title": title,
    })

@player_required
def guild_invite_view(request, guild_id):
    """Display Discord-style invite card for approved guild"""
    guild = get_object_or_404(DiscordGuild, guild_id=guild_id)
    next_url = request.GET.get('next')

    if not DiscordGuildJoinRequest.objects.filter(
            profile=request.user.profile,
            guild=guild,
            status=DiscordGuildJoinRequest.Status.APPROVED,
        ).exists():
        
        if next_url:
            return redirect(f"{reverse('guild-request', kwargs={'guild_id': guild_id})}?next={next_url}")
        return redirect("guild-request", guild_id=guild_id)

    # Extract invite code from URL
    invite_code = guild.get_invite_code()
    
    discord_info = None
    if invite_code:
        discord_info = get_discord_invite_info(invite_code)
        
        # Update guild info in database if successful
        if discord_info.get('success'):
            guild.actual_name = discord_info['name']
            guild.description = discord_info.get('description', '')
            guild.icon_hash = discord_info.get('icon_hash', '')
            guild.banner_hash = discord_info.get('banner_hash') or discord_info.get('splash_hash', '')
            guild.member_count = discord_info['member_count']
            guild.online_count = discord_info['online_count']
            guild.save()

            # Add computed URLs to discord_info for template
            discord_info['icon_url'] = guild.get_icon_url()
            discord_info['banner_url'] = guild.get_banner_url()

    title = f'Join {guild.name}'

    context = {
        'guild': guild,
        'discord_info': discord_info,
        'next_url': next_url,
        'title': title,
    }

    return render(request, 'the_gatehouse/guild_invite.html', context)


@login_required
@admin_required
def pending_guild_invites(request):
    """Admin view to manage pending guild join requests."""
    pending_invites = DiscordGuildJoinRequest.objects.filter(
        status=DiscordGuildJoinRequest.Status.PENDING
    ).select_related('profile', 'guild').order_by('-created_at')

    title = "Pending Guild Invites"

    context = {
        'pending_invites': pending_invites,
        'title': title,
    }

    return render(request, 'the_gatehouse/pending_guild_invites.html', context)



@login_required
@admin_required
def approve_guild_invite(request, invite_id):
    """Approve a pending guild join request."""
    invite = get_object_or_404(DiscordGuildJoinRequest, id=invite_id)

    # Save moderator message and note if provided
    if request.method == 'POST':
        fields_to_update = []

        moderator_message = request.POST.get('moderator_message', '').strip()
        if moderator_message:
            invite.moderator_message = moderator_message
            fields_to_update.append('moderator_message')

        moderator_note = request.POST.get('moderator_note', '').strip()
        if moderator_note:
            invite.moderator_note = moderator_note
            fields_to_update.append('moderator_note')

        if fields_to_update:
            invite.save(update_fields=fields_to_update)

    try:
        invite.approve()
        messages.success(
            request,
            f"Approved {invite.profile.name}'s request to join {invite.guild.name}"
        )

        # Send Discord notification
        send_discord_message(
            f"Guild invite approved: {invite.profile.name} → {invite.guild.name} (by {request.user.profile.name})",
            'report'
        )

        # Send user notification
        from .models import UserNotification, MessageChoices
        notification_message = f"Your request to join {invite.guild.name} has been approved!"
        if invite.moderator_message:
            notification_message += f" {invite.moderator_message}"

        UserNotification.create_notification(
            profile=invite.profile,
            message=notification_message,
            message_type=MessageChoices.SUCCESS,
            related_url=reverse('guild-invite', kwargs={'guild_id': invite.guild.guild_id})
        )

    except ValueError as e:
        messages.error(request, str(e))

    return redirect('pending-guild-invites')


@login_required
@admin_required
def reject_guild_invite(request, invite_id):
    """Reject a pending guild join request."""
    invite = get_object_or_404(DiscordGuildJoinRequest, id=invite_id)

    # Save moderator message and note if provided
    if request.method == 'POST':
        fields_to_update = []

        moderator_message = request.POST.get('moderator_message', '').strip()
        if moderator_message:
            invite.moderator_message = moderator_message
            fields_to_update.append('moderator_message')

        moderator_note = request.POST.get('moderator_note', '').strip()
        if moderator_note:
            invite.moderator_note = moderator_note
            fields_to_update.append('moderator_note')

        if fields_to_update:
            invite.save(update_fields=fields_to_update)

    try:
        invite.reject()
        messages.warning(
            request,
            f"Rejected {invite.profile.name}'s request to join {invite.guild.name}"
        )

        # Send Discord notification
        send_discord_message(
            f"Guild invite rejected: {invite.profile.name} → {invite.guild.name} (by {request.user.profile.name})",
            'report'
        )

        # Send user notification
        from .models import UserNotification, MessageChoices
        notification_message = f"Your request to join {invite.guild.name} has been rejected."
        if invite.moderator_message:
            notification_message += f" {invite.moderator_message}"

        UserNotification.create_notification(
            profile=invite.profile,
            message=notification_message,
            message_type=MessageChoices.WARNING,
            related_url=None
        )

    except ValueError as e:
        messages.error(request, str(e))

    return redirect('pending-guild-invites')


@login_required
def mark_guild_invite_clicked(request, guild_id):
    """Add guild to user's profile when they click to join (immediate access to guild links)."""
    guild = get_object_or_404(DiscordGuild, guild_id=guild_id)
    profile = request.user.profile
    # Find the approved invite
    invite = DiscordGuildJoinRequest.objects.filter(
        profile=profile,
        guild=guild,
        status=DiscordGuildJoinRequest.Status.APPROVED
    ).first()

    if invite:
        # Add guild to user's profile immediately so they get access to guild-gated links
        # If they don't actually join Discord, the next sync will remove it
        if guild not in profile.guilds.all():
            profile.guilds.add(guild)
            profile.save()

    # Return success so JavaScript knows it worked
    return JsonResponse({'success': True})




@login_required
@admin_required
def pending_posts(request):
    """Admin view to manage pending posts."""
    from the_keep.models import StatusChoices

    pending_posts = Post.objects.filter(
        status=StatusChoices.SUBMITTED
    ).select_related('designer', 'submitted_by').order_by('-date_posted')

    title = "Pending Posts"

    context = {
        'pending_posts': pending_posts,
        'title': title,
    }

    return render(request, 'the_gatehouse/pending_posts.html', context)


@login_required
@admin_required
def approve_post(request, post_id):
    """Approve a pending post and move it to Development status."""
    from the_keep.models import StatusChoices
    from .models import UserNotification, MessageChoices

    post = get_object_or_404(Post, id=post_id)

    if request.method == 'POST':
        try:
            post.status = StatusChoices.DEVELOPMENT
            post.save()

            messages.success(
                request,
                f"Approved '{post.title}' and moved to Development status"
            )

            # Send Discord notification
            send_discord_message(
                f"Post approved: {post.title} ({post.get_component_display()}) by {request.user.profile.name}",
                'report'
            )

            # Create user notification for the submitter
            if post.submitted_by:
                UserNotification.create_notification(
                    profile=post.submitted_by,
                    message=f"Your submitted {post.get_component_display()} '{post.title}' has been approved and moved to Development status!",
                    message_type=MessageChoices.SUCCESS,
                    related_post=post,
                    related_url=post.get_absolute_url()
                )

        except Exception as e:
            messages.error(request, f"Error approving post: {str(e)}")

    return redirect('pending-posts')


@login_required
@admin_required
def reject_post(request, post_id):
    """Reject a pending post and delete it."""
    from the_keep.models import StatusChoices

    post = get_object_or_404(Post, id=post_id)

    if request.method == 'POST':
        try:
            post_title = post.title
            post_component = post.get_component_display()

            # Delete the post
            post.delete()

            messages.warning(
                request,
                f"Rejected and deleted '{post_title}'"
            )

            # Send Discord notification
            send_discord_message(
                f"Post rejected and deleted: {post_title} ({post_component}) by {request.user.profile.name}",
                'report'
            )

        except Exception as e:
            messages.error(request, f"Error rejecting post: {str(e)}")

    return redirect('pending-posts')

@login_required
def dismiss_notification(request, notification_id):
    """Dismiss a user notification."""
    from .models import UserNotification

    notification = get_object_or_404(UserNotification, id=notification_id, profile=request.user.profile)
    notification.dismiss()

    # Return JSON for AJAX requests
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return JsonResponse({'success': True})

    # Redirect back for regular requests
    return redirect(request.META.get('HTTP_REFERER', '/'))

