import random
# import logging
from itertools import groupby
from django.utils import timezone 
from django.shortcuts import render, get_object_or_404, redirect
from django.http import Http404, HttpResponse
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.contrib.auth.decorators import login_required
from django.db.models import Count, F, ExpressionWrapper, FloatField, Q, Case, When, Value
from django.db.models.functions import Cast
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
from django.core.exceptions import PermissionDenied, ObjectDoesNotExist
from django.conf import settings
from django.db import IntegrityError
from django.db.models import ProtectedError, Count
from django.urls import reverse, reverse_lazy
from django.contrib import messages

from the_warroom.filters import GameFilter
from django.views.generic import (
    ListView, 
    DetailView, 
    CreateView,
    UpdateView,
    DeleteView
)
from the_warroom.models import Game, ScoreCard, Effort, Tournament, Round
from the_gatehouse.models import Profile, BackgroundImage, ForegroundImage
from the_gatehouse.views import (designer_required_class_based_view, designer_required, 
                                 player_required, player_required_class_based_view,
                                 admin_onboard_required, admin_required)
from the_gatehouse.discordservice import send_discord_message
from the_gatehouse.utils import get_uuid, build_absolute_uri
from .models import (
    Post, Expansion,
    Faction, Vagabond,
    Map, Deck,
    Hireling, Landmark,
    Piece, Tweak,
    PNPAsset,
    )
from .forms import (MapCreateForm, 
                    DeckCreateForm, LandmarkCreateForm,
                    HirelingCreateForm, VagabondCreateForm,
                    FactionCreateForm, ExpansionCreateForm,
                    PieceForm, ClockworkCreateForm,
                    StatusConfirmForm, TweakCreateForm,
                    PNPAssetCreateForm,
)
from the_tavern.forms import PostCommentCreateForm
from the_tavern.views import bookmark_toggle

# logger = logging.getLogger(__name__)

# activity_logger = logging.getLogger("user_activity")


class ExpansionDetailView(DetailView):
    model = Expansion

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        
        links_count = self.object.count_links(self.request.user)
        # Add links_count to context
        context['links_count'] = links_count

        if self.object.open_roster and (self.object.end_date > timezone.now() or not self.object.end_date):
            context['open_expansion'] = True
        else:
            context['open_expansion'] = False
        
        return context

class ExpansionFactionsListView(ListView):
    model = Faction
    context_object_name = 'objects'

@designer_required_class_based_view
class ExpansionCreateView(LoginRequiredMixin, CreateView):
    model = Expansion
    form_class = ExpansionCreateForm

    def form_valid(self, form):
        form.instance.designer = self.request.user.profile  # Set the designer to the logged-in user
        return super().form_valid(form)
    
    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user  # Pass the current user to the form
        return kwargs
    
@designer_required_class_based_view
class ExpansionUpdateView(LoginRequiredMixin, UserPassesTestMixin, UpdateView):
    model = Expansion
    form_class = ExpansionCreateForm
    def form_valid(self, form):
        form.instance.designer = self.request.user.profile  # Set the designer to the logged-in user
        return super().form_valid(form)
    
    def test_func(self):
        obj = self.get_object()
        # Only allow access if the logged-in user is the designer of the object
        return self.request.user.profile == obj.designer
    
    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user  # Pass the current user to the form
        return kwargs
class ExpansionDeleteView(LoginRequiredMixin, UserPassesTestMixin, DeleteView):
    model = Expansion
    success_url = '/'  # The default success URL after the post is deleted

    def test_func(self):
        expansion = self.get_object()
        return self.request.user.profile == expansion.designer  # Ensure only the designer can delete

    def post(self, request, *args, **kwargs):
        expansion = self.get_object()
        name = expansion.title

        try:
            # Attempt to delete the post
            response = self.delete(request, *args, **kwargs)
            # Add success message upon successful deletion
            messages.success(request, f"The expansion '{name}' was successfully deleted and has been removed from any related posts.")
            return response
        except ProtectedError:
            # Handle the case where the deletion fails due to foreign key protection
            messages.error(request, f"The expansion '{name}' cannot be deleted because it has been used in a game.")
            # Redirect back to the post detail page
            return redirect('expansion-detail', expansion.slug)  # Make sure `post.get_absolute_url()` is correct
        except IntegrityError:
            # Handle other integrity errors (if any)
            messages.error(request, "An error occurred while trying to delete this post.")
            return redirect('expansion-detail', expansion.slug) 







# START CREATE VIEWS
@designer_required
def new_components(request):
    context = {

    }
    return render(request, 'the_keep/new.html', context=context)



class PostCreateView(LoginRequiredMixin, CreateView):
    """
    A base class for all CreateViews that require a designer field to be set to
    the current logged-in user's profile and also pass the user to the form.
    """
    def form_valid(self, form):
        if not form.instance.designer:
            form.instance.designer = self.request.user.profile  # Set the designer to the logged-in user
        return super().form_valid(form)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user  # Pass the current user to the form
        return kwargs

@designer_required_class_based_view
class MapCreateView(PostCreateView):
    model = Map
    form_class = MapCreateForm
    template_name = 'the_keep/post_form.html'

@designer_required_class_based_view
class DeckCreateView(PostCreateView):
    model = Deck
    form_class = DeckCreateForm
    template_name = 'the_keep/post_form.html'

@designer_required_class_based_view
class LandmarkCreateView(PostCreateView):
    model = Landmark
    form_class = LandmarkCreateForm
    template_name = 'the_keep/post_form.html'

@designer_required_class_based_view
class TweakCreateView(PostCreateView):
    model = Tweak
    form_class = TweakCreateForm
    template_name = 'the_keep/post_form.html'

@designer_required_class_based_view
class HirelingCreateView(PostCreateView):
    model = Hireling
    form_class = HirelingCreateForm
    template_name = 'the_keep/post_form.html'

    def get_form_kwargs(self):
        # Get the default form kwargs
        kwargs = super().get_form_kwargs()
        # Add user to the kwargs
        kwargs['designer'] = self.request.user.profile
        print("TEST")
        return kwargs

@designer_required_class_based_view
class VagabondCreateView(PostCreateView):
    model = Vagabond
    form_class = VagabondCreateForm
    template_name = 'the_keep/post_form.html'

@designer_required_class_based_view
class FactionCreateView(PostCreateView):
    model = Faction
    form_class = FactionCreateForm
    template_name = 'the_keep/post_form.html'

@designer_required_class_based_view
class ClockworkCreateView(PostCreateView):
    model = Faction
    form_class = ClockworkCreateForm
    template_name = 'the_keep/post_form.html'

# END CREATE VIEWS


class PostUpdateView(LoginRequiredMixin, UserPassesTestMixin, UpdateView):
    """
    A base class for all UpdateViews that require:
    1. The designer to be set to the current logged-in user.
    2. The date_updated field to be set to the current timestamp.
    3. Checking if the logged-in user is the designer of the object.
    """

    def form_valid(self, form):
        # Ensure the designer is set to the logged-in user's profile
        # if not self.request.user.profile.admin:
        #     form.instance.designer = self.request.user.profile
        form.instance.date_updated = timezone.now()  # Set the updated timestamp
        return super().form_valid(form)

    def test_func(self):
        obj = self.get_object()
        if self.request.user.profile.admin and not obj.designer.designer:
            return True
        # Only allow access if the logged-in user is the designer of the object
        return self.request.user.profile == obj.designer

    def get_object(self, queryset=None):
        obj = super().get_object(queryset)
        # Store the object to be reused in other methods if needed
        self._obj = obj
        return obj

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user  # Pass the current user to the form

        # Pass the 'expansion' from the object (fetched in get_object)
        kwargs['expansion'] = self._obj.expansion if hasattr(self, '_obj') else None

        return kwargs

    
@designer_required_class_based_view
class MapUpdateView(PostUpdateView):
    model = Map
    form_class = MapCreateForm
    template_name = 'the_keep/post_form.html'

@designer_required_class_based_view
class DeckUpdateView(PostUpdateView):
    model = Deck
    form_class = DeckCreateForm
    template_name = 'the_keep/post_form.html'

@designer_required_class_based_view
class LandmarkUpdateView(PostUpdateView):
    model = Landmark
    form_class = LandmarkCreateForm
    template_name = 'the_keep/post_form.html'

@designer_required_class_based_view
class TweakUpdateView(PostUpdateView):
    model = Tweak
    form_class = TweakCreateForm
    template_name = 'the_keep/post_form.html'

@designer_required_class_based_view
class HirelingUpdateView(PostUpdateView):
    model = Hireling
    form_class = HirelingCreateForm
    template_name = 'the_keep/post_form.html'

    def get_form_kwargs(self):
        # Get the default form kwargs
        kwargs = super().get_form_kwargs()
        # Add user to the kwargs
        kwargs['designer'] = self.request.user.profile
        return kwargs

@designer_required_class_based_view
class VagabondUpdateView(PostUpdateView):
    model = Vagabond
    form_class = VagabondCreateForm
    template_name = 'the_keep/post_form.html'

@designer_required_class_based_view  
class FactionUpdateView(PostUpdateView):
    model = Faction
    form_class = FactionCreateForm
    template_name = 'the_keep/post_form.html'

@designer_required_class_based_view  
class ClockworkUpdateView(PostUpdateView):
    model = Faction
    form_class = ClockworkCreateForm
    template_name = 'the_keep/post_form.html'

@designer_required_class_based_view
class PostDeleteView(LoginRequiredMixin, UserPassesTestMixin, DeleteView):
    model = Post
    success_url = '/'  # The default success URL after the post is deleted

    def test_func(self):
        post = self.get_object()
        # print("testing delete function")
        return self.request.user.profile == post.designer  # Ensure only the designer can delete

    def post(self, request, *args, **kwargs):
        # print('Trying to delete')
        post = self.get_object()
        name = post.title
        detail_view = f'{post.component.lower()}-detail'

        component_mapping = {
                "Map": Map,
                "Deck": Deck,
                "Landmark": Landmark,
                "Tweak": Tweak,
                "Hireling": Hireling,
                "Vagabond": Vagabond,
                "Faction": Faction,
                "Clockwork": Faction,
            }
        Klass = component_mapping.get(post.component)
        object = get_object_or_404(Klass, slug=post.slug)

        try:
            # # Attempt to delete the post
            # response = self.delete(request, *args, **kwargs)
            # # Add success message upon successful deletion
            # messages.success(request, f"The {post.component} '{name}' was successfully deleted.")
            # return response
            games = object.get_games_queryset()
            if not games.exists():
                # Abandon the post without deleting
                post.status = 5  # Set the status to abandoned
                rand_int = random.randint(100, 999)
                post.title = f'Deleted {post.component}-{rand_int}'
                if post.component == 'Hireling':
                    hireling = Hireling.objects.get(slug=post.slug)
                    if hireling.other_side:
                        other_side = hireling.other_side
                        other_side.other_side = None
                        hireling.other_side = None
                        other_side.save()
                        hireling.save()

                post.save()
                messages.success(request, f'The {post.component} "{name}" was successfully deleted.')
                return redirect('keep-home')
            else:
                # Do not delete posts with games recorded.
                post.status = 4  # Set the status to inactive
                post.save()
                messages.error(request, f'The {post.component} "{name}"" cannot be deleted because it has been used in a game. Status has been set to "Inactive".')
                # Redirect back to the post detail page
                return redirect(detail_view, post.slug)
            

        except ProtectedError:
            # Handle the case where the deletion fails due to foreign key protection
            messages.error(request, f"The {post.component} '{name}' cannot be deleted because it has been used in a game.")
            # Redirect back to the post detail page
            return redirect(detail_view, post.slug)
        except IntegrityError:
            # Handle other integrity errors (if any)
            messages.error(request, "An error occurred while trying to delete this post.")
            return redirect(detail_view, post.slug) 



def about(request, *args, **kwargs):
    return render(request, 'the_keep/about.html', {'title': 'About'})

@admin_onboard_required
def home(request, *args, **kwargs):

    faction_count = Faction.objects.filter(status__lte=4, official=False).count()
    deck_count = Deck.objects.filter(status__lte=4, official=False).count()
    map_count = Map.objects.filter(status__lte=4, official=False).count()
    official_faction_count = Faction.objects.filter(status__lte=4, official=True).count()
    official_deck_count = Deck.objects.filter(status__lte=4, official=True).count()
    official_map_count = Map.objects.filter(status__lte=4, official=True).count()
    game_count = Game.objects.filter(final=True).count()


    if request.user.is_authenticated:
        theme = request.user.profile.theme
    else:
        theme = None

    background_image = BackgroundImage.objects.filter(theme=theme, page="library").order_by('?').first()
    # foreground_images = ForegroundImage.objects.filter(theme=theme, page="library")
    all_foreground_images = ForegroundImage.objects.filter(theme=theme, page="library")
    # Group the images by location
    grouped_by_location = groupby(sorted(all_foreground_images, key=lambda x: x.location), key=lambda x: x.location)
    # Select a random image from each location
    foreground_images = [random.choice(list(group)) for _, group in grouped_by_location]
    # If using PostgreSQL or another database that supports 'distinct' on a field:
    # foreground_images = ForegroundImage.objects.filter(theme=theme, page="library").distinct('location')


    context = {
        'title': 'Home',
        'faction_count': faction_count,
        'deck_count': deck_count,
        'map_count': map_count,
        'official_faction_count': official_faction_count,
        'official_deck_count': official_deck_count,
        'official_map_count': official_map_count,
        'game_count': game_count,
        'background_image': background_image,
        'foreground_images': foreground_images,



    }

    return render(request, 'the_keep/home.html', context)




def ultimate_component_view(request, slug):
    
    post = get_object_or_404(Post, slug=slug)
    component_mapping = {
            "Map": Map,
            "Deck": Deck,
            "Landmark": Landmark,
            "Tweak": Tweak,
            "Hireling": Hireling,
            "Vagabond": Vagabond,
            "Faction": Faction,
            "Clockwork": Faction,
        }
    Klass = component_mapping.get(post.component)
    object = get_object_or_404(Klass, slug=slug)
    # full_url = request.build_absolute_uri()
    if request.user.is_authenticated:
        send_discord_message(f'[{request.user}]({build_absolute_uri(request, request.user.profile.get_absolute_url())}) viewed {object.component}: {object.title}')
    else:
        send_discord_message(f'{get_uuid(request)} viewed {object.component}: {object.title}')
    print('Request received')
    # print(f'Stable Ready: {stable_ready}')
    view_status = 4
    if request.user.is_authenticated:
        view_status = request.user.profile.view_status
    related_posts = Post.objects.filter(based_on=object, status__lte=view_status)
    # Add the post that the current object is based on (if it exists)
    if object.based_on:
        related_posts |= Post.objects.filter(id=object.based_on.id, status__lte=view_status)
    # Start with the base queryset
    games = object.get_games_queryset()

    stable_ready = None
    testing_ready = None
    if request.user.is_authenticated:
        if object.designer == request.user.profile:
            stable_ready = object.stable_check()
            if object.status == '3' and games.count() > 0:
                testing_ready = True

    # Apply the conditional filter if needed
    if request.user.is_authenticated:
        if not request.user.profile.weird:
            games = games.filter(official=True)
    # else:
    #     games = games.filter(official=True)

    # Apply distinct and prefetch_related to all cases  
    prefetch_values = [
        'efforts__player', 'efforts__faction', 'efforts__vagabond', 'round__tournament', 
        'hirelings', 'landmarks', 'tweaks', 'map', 'deck', 'undrafted_faction', 'undrafted_vagabond'
    ]
    games = games.distinct().prefetch_related(*prefetch_values)



    commentform = PostCommentCreateForm()
    game_filter = GameFilter(request.GET, user=request.user, queryset=games)

    # Get the filtered queryset
    filtered_games = game_filter.qs.distinct()

    
    if post.component == "Faction" or post.component == "Clockwork":
        efforts = Effort.objects.filter(game__in=filtered_games, faction=post)
    elif post.component == "Vagabond":
        efforts = Effort.objects.filter(game__in=filtered_games, vagabond=post)
    else:
        efforts = Effort.objects.filter(game__in=filtered_games)
    
    # Get top players for factions
    top_players = []
    most_players = []
    win_count = 0
    coalition_count = 0
    win_rate = 0
    tourney_points = 0
    total_efforts = 0
    scorecard_count = None
    detail_scorecard_count = None
    # On first load get faction and VB Stats
    page_number = request.GET.get('page')  # Get the page number from the request
    if not page_number:
        if object.component == "Faction":
            # top_players = Profile.top_players(faction_id=object.id, limit=10, game_threshold=5)
            # most_players = Profile.top_players(faction_id=object.id, limit=10, top_quantity=True, game_threshold=1)
            top_players = Profile.leaderboard(effort_qs=efforts, limit=10, game_threshold=5)
            most_players = Profile.leaderboard(effort_qs=efforts, limit=10, top_quantity=True, game_threshold=1)
            game_values = filtered_games.aggregate(
                        total_efforts=Count('efforts', filter=Q(efforts__faction=object)),
                        win_count=Count('efforts', filter=Q(efforts__win=True, efforts__faction=object)),
                        coalition_count=Count('efforts', filter=Q(efforts__win=True, efforts__game__coalition_win=True, efforts__faction=object))
                    )
            scorecard_count = ScoreCard.objects.filter(faction__slug=object.slug, effort__isnull=False).count()
            detail_scorecard_count = ScoreCard.objects.filter(faction__slug=object.slug, effort__isnull=False, total_generic_points=0).count()
        if object.component == "Vagabond":
            game_values = filtered_games.aggregate(
                        total_efforts=Count('efforts', filter=Q(efforts__vagabond=object)),
                        win_count=Count('efforts', filter=Q(efforts__win=True, efforts__vagabond=object)),
                        coalition_count=Count('efforts', filter=Q(efforts__win=True, efforts__game__coalition_win=True, efforts__vagabond=object))
                    )
        if object.component == "Faction" or object.component == "Vagabond":
            # Access the aggregated values from the dictionary returned by .aggregate()
            total_efforts = game_values['total_efforts']
            win_count = game_values['win_count']
            coalition_count = game_values['coalition_count']
        if total_efforts > 0:
            win_rate = (win_count - (coalition_count / 2)) / total_efforts * 100
        else:
            win_rate = 0
        tourney_points = win_count - (coalition_count / 2)



    # Paginate games
    paginate_by = settings.PAGE_SIZE
    paginator = Paginator(filtered_games, paginate_by)  # Use the queryset directly
    try:
        page_obj = paginator.get_page(page_number)  # Get the specific page of games
    except EmptyPage:
        page_obj = paginator.page(paginator.num_pages)  # Redirect to the last page if invalid

    links_count = object.count_links(request.user)


    context = {
        'object': object,
        'games_total': games.count(),
        'filtered_games': filtered_games.count(),
        'games': page_obj,  # Pagination applied here
        'is_paginated': len(filtered_games) > paginate_by,
        # 'page_obj': page_obj,  # Pass the paginated page object to the context
        'commentform': commentform,
        'form': game_filter.form,
        'filterset': game_filter,
        'top_players': top_players,
        'most_players': most_players,
        'win_count': win_count,
        'coalition_count': coalition_count,
        'win_rate': win_rate,
        'tourney_points': tourney_points,
        'total_efforts': total_efforts,
        'stable_ready': stable_ready,
        'testing_ready': testing_ready,
        'related_posts': related_posts,
        'links_count': links_count,
        'scorecard_count': scorecard_count,
        'detail_scorecard_count': detail_scorecard_count,
    }
    if request.htmx:
            return render(request, 'the_keep/partials/game_list.html', context)
    return render(request, 'the_keep/post_detail.html', context)




def component_games(request, slug):
    
    post = get_object_or_404(Post, slug=slug)
    component_mapping = {
            "Map": Map,
            "Deck": Deck,
            "Landmark": Landmark,
            "Tweak": Tweak,
            "Hireling": Hireling,
            "Vagabond": Vagabond,
            "Faction": Faction,
            "Clockwork": Faction,
        }
    Klass = component_mapping.get(post.component)
    object = get_object_or_404(Klass, slug=slug)

    # Start with the base queryset
    games = object.get_games_queryset()

    # Apply the conditional filter if needed
    if request.user.is_authenticated:
        if not request.user.profile.weird:
            games = games.filter(official=True)

    # Apply distinct and prefetch_related to all cases  
    prefetch_values = [
        'efforts__player', 'efforts__faction', 'efforts__vagabond', 'round__tournament', 
        'hirelings', 'landmarks', 'tweaks', 'map', 'deck', 'undrafted_faction', 'undrafted_vagabond'
    ]
    games = games.distinct().prefetch_related(*prefetch_values)

    game_filter = GameFilter(request.GET, user=request.user, queryset=games)

    # Get the filtered queryset
    filtered_games = game_filter.qs.distinct()

    if post.component == "Faction" or post.component == "Clockwork":
        efforts = Effort.objects.filter(game__in=filtered_games, faction=post)
    elif post.component == "Vagabond":
        efforts = Effort.objects.filter(game__in=filtered_games, vagabond=post)
    else:
        efforts = Effort.objects.filter(game__in=filtered_games)
    
    # Get top players for factions
    win_count = 0
    coalition_count = 0
    win_rate = 0
    tourney_points = 0
    total_efforts = 0
    # On first load get faction and VB Stats
    page_number = request.GET.get('page')  # Get the page number from the request
    page_number = request.GET.get('page')  # Get the page number from the request
    if not page_number:
        if object.component == "Faction":
            game_values = filtered_games.aggregate(
                        total_efforts=Count('efforts', filter=Q(efforts__faction=object)),
                        win_count=Count('efforts', filter=Q(efforts__win=True, efforts__faction=object)),
                        coalition_count=Count('efforts', filter=Q(efforts__win=True, efforts__game__coalition_win=True, efforts__faction=object))
                    )
        if object.component == "Vagabond":
            game_values = filtered_games.aggregate(
                        total_efforts=Count('efforts', filter=Q(efforts__vagabond=object)),
                        win_count=Count('efforts', filter=Q(efforts__win=True, efforts__vagabond=object)),
                        coalition_count=Count('efforts', filter=Q(efforts__win=True, efforts__game__coalition_win=True, efforts__vagabond=object))
                    )
        if object.component == "Faction" or object.component == "Vagabond":
            # Access the aggregated values from the dictionary returned by .aggregate()
            total_efforts = game_values['total_efforts']
            win_count = game_values['win_count']
            coalition_count = game_values['coalition_count']
        if total_efforts > 0:
            win_rate = (win_count - (coalition_count / 2)) / total_efforts * 100
        else:
            win_rate = 0
        tourney_points = win_count - (coalition_count / 2)
    # Paginate games
    paginate_by = settings.PAGE_SIZE
    paginator = Paginator(filtered_games, paginate_by)  # Use the queryset directly
    try:
        page_obj = paginator.get_page(page_number)  # Get the specific page of games
    except EmptyPage:
        page_obj = paginator.page(paginator.num_pages)  # Redirect to the last page if invalid




    context = {
       'object': object,
        'games_total': games.count(),
        'filtered_games': filtered_games.count(),
        'games': page_obj,  # Pagination applied here
        'is_paginated': len(filtered_games) > paginate_by,
        # 'page_obj': page_obj,  # Pass the paginated page object to the context
        'form': game_filter.form,
        'filterset': game_filter,
        'win_count': win_count,
        'coalition_count': coalition_count,
        'win_rate': win_rate,
        'tourney_points': tourney_points,
        'total_efforts': total_efforts,
    }
    if request.htmx:
            return render(request, 'the_keep/partials/game_list.html', context)
    return render(request, 'the_keep/post_games.html', context)




@login_required
@bookmark_toggle(Post)
def bookmark_post(request, object):
    return render(request, 'the_keep/partials/bookmarks.html', {'object': object })



# Search Page
def list_view(request, slug=None):

    if request.user.is_authenticated:
        send_discord_message(f'[{request.user}]({build_absolute_uri(request, request.user.profile.get_absolute_url())}) on Home Page')
        theme = request.user.profile.theme
    else:
        theme = None

    background_image = BackgroundImage.objects.filter(theme=theme, page="library").order_by('?').first()
    # foreground_images = ForegroundImage.objects.filter(theme=theme, page="library")
    all_foreground_images = ForegroundImage.objects.filter(theme=theme, page="library")
    # Group the images by location
    grouped_by_location = groupby(sorted(all_foreground_images, key=lambda x: x.location), key=lambda x: x.location)
    # Select a random image from each location
    foreground_images = [random.choice(list(group)) for _, group in grouped_by_location]
    # If using PostgreSQL or another database that supports 'distinct' on a field:
    # foreground_images = ForegroundImage.objects.filter(theme=theme, page="library").distinct('location')




    posts, search, search_type, designer, faction_type, reach_value, status = _search_components(request, slug)
    # designers = Profile.objects.annotate(posts_count=Count('posts')).filter(posts_count__gt=0)
    view_status = 4
    if request.user.is_authenticated:
        view_status = request.user.profile.view_status
        if request.user.profile.weird:
            # Filter designers who have at least one post with a status less than or equal to the user's view_status
            designers = Profile.objects.annotate(
                posts_count=Count('posts'),
                valid_posts_count=Count('posts', filter=Q(posts__status__lte=view_status))
            ).filter(posts_count__gt=0, valid_posts_count__gt=0)
        else:
            # Filter designers who have at least one post with 'official' property set to True
            designers = Profile.objects.annotate(
                official_posts_count=Count('posts', filter=Q(posts__official=True)),
                valid_posts_count=Count('posts', filter=Q(posts__status__lte=view_status))
                ).filter(official_posts_count__gt=0, valid_posts_count__gt=0)
    else:
        designers = Profile.objects.annotate(
            posts_count=Count('posts'),
            valid_posts_count=Count('posts', filter=Q(posts__status__lte=view_status))
            ).filter(posts_count__gt=0, valid_posts_count__gt=0)
    context = {
        "posts": posts, 
        'search': search or "", 
        'search_type': search_type or "",
        'faction_type': faction_type or "",
        'reach_value': reach_value or "",
        'status': status or "",
        "designers": designers,
        'designer': designer,
        'is_search_view': False,
        'slug': slug,
        'background_image': background_image,
        'foreground_images': foreground_images,
        }
    # if request.htmx:
    #     return render(request, "the_keep/partials/search_body.html", context)    

    return render(request, "the_keep/list.html", context)


def search_view(request, slug=None):
    posts, search, search_type, designer, faction_type, reach_value, status = _search_components(request, slug)
    # Get all designers (Profiles) who have at least one post
    view_status = 4
    if request.user.is_authenticated:
        view_status = request.user.profile.view_status
        if request.user.profile.weird:
            # Filter designers who have at least one post with a status less than or equal to the user's view_status
            designers = Profile.objects.annotate(
                posts_count=Count('posts'),
                valid_posts_count=Count('posts', filter=Q(posts__status__lte=view_status))
            ).filter(posts_count__gt=0, valid_posts_count__gt=0)
        else:
            # Filter designers who have at least one post with 'official' property set to True
            designers = Profile.objects.annotate(
                official_posts_count=Count('posts', filter=Q(posts__official=True)),
                valid_posts_count=Count('posts', filter=Q(posts__status__lte=view_status))
                ).filter(official_posts_count__gt=0, valid_posts_count__gt=0)
    else:
        designers = Profile.objects.annotate(
            posts_count=Count('posts'),
            valid_posts_count=Count('posts', filter=Q(posts__status__lte=view_status))
            ).filter(posts_count__gt=0, valid_posts_count__gt=0)
    context = {
        "posts": posts, 
        'search': search or "", 
        'search_type': search_type or "",
        'faction_type': faction_type or "",
        'reach_value': reach_value or "",
        'status': status or "",
        "designers": designers,
        'designer': designer,
        'is_search_view': True,
        'slug': slug,
        }
    return render(request, "the_keep/partials/search_results.html", context)


def _search_components(request, slug=None):
    search = request.GET.get('search')
    search_type = request.GET.get('search_type', '')
    faction_type = request.GET.get('faction_type', '')
    reach_value = request.GET.get('reach_value', '')
    status = request.GET.get('status', '')
    designer = request.GET.get('designer') 
    page = request.GET.get('page')
    view_status = 4
    if request.user.is_authenticated:
        view_status = request.user.profile.view_status

    if faction_type or reach_value:
        if request.user.is_authenticated:
            if request.user.profile.weird:
                posts = Faction.objects.filter(status__lte=view_status).prefetch_related('designer')
            else:
                posts = Faction.objects.filter(official=True, status__lte=view_status).prefetch_related('designer')
        else:
            posts = Faction.objects.filter(status__lte=view_status).prefetch_related('designer')

        if faction_type:
            posts = posts.filter(type=faction_type)
        if reach_value:
            posts = posts.filter(reach=reach_value)

    else:
        if request.user.is_authenticated:
            if request.user.profile.weird:
                posts = Post.objects.filter(status__lte=view_status).prefetch_related('designer')
            else:
                posts = Post.objects.filter(official=True, status__lte=view_status).prefetch_related('designer')
        else:
            posts = Post.objects.filter(status__lte=view_status).prefetch_related('designer')

    if slug:
        player = get_object_or_404(Profile, slug=slug)
        posts = posts.filter(designer=player)
    if search:
        # posts = posts.filter(title__icontains=search)
        posts = posts.filter(Q(title__icontains=search)|Q(animal__icontains=search))
        
    if search_type:
        posts = posts.filter(component__icontains=search_type)

    if designer:
        posts = posts.filter(designer__id=designer)

    if status:
        posts = posts.filter(status=status)

    paginator = Paginator(posts, settings.PAGE_SIZE)
    try:
        posts = paginator.page(page)
    except PageNotAnInteger:
        posts = paginator.page(1)
    except EmptyPage:
        posts = paginator.page(paginator.num_pages)
    return posts, search or "", search_type or "", designer or "", faction_type or "", reach_value or "", status or ""



@designer_required
def add_piece(request, id=None):
    if not request.htmx:
        raise Http404("Not an HTMX request")
    if id:
        obj = get_object_or_404(Piece, id=id)
        #Check if user owns this object
        if obj.parent.designer!=request.user.profile:
            raise PermissionDenied() 
    else:
        obj = Piece()  # Create a new Piece instance but do not save it yet

    form = PieceForm(request.POST or None, request.FILES or None, instance=obj)

    piece_type = request.GET.get('piece')
    slug = request.GET.get('slug')

    parent = Post.objects.get(slug=slug)

    context = {
        'form': form,
        'object': parent,
        'piece_type': piece_type,
        'piece': obj,
    }

    if request.method == 'POST':
        if form.is_valid():
            # Form is valid, save the piece
            child = form.save(commit=False)
            child.parent = parent
            child.type = piece_type
            child.save()

            # Return a partial to indicate the piece has been updated
            return render(request, 'the_keep/partials/piece_line.html', context)
        else:
            # If form is not valid, it will still return the form with error messages
            return render(request, 'the_keep/partials/piece_add.html', context)  # Render the form with error messages
    
    # If GET request, render the form without errors (initial state)

    if id:
        return render(request, 'the_keep/partials/piece_update.html', context)
    else:
        return render(request, 'the_keep/partials/piece_add.html', context)

@designer_required
def delete_piece(request, id):
    if not request.htmx:
        raise Http404("Not an HTMX request")
    piece = get_object_or_404(Piece, id=id)
    # Check if user owns this object
    if piece.parent.designer==request.user.profile:
        piece.delete()
        return HttpResponse('')
    else:
        raise PermissionDenied() 




# Not used. Might reuse for bookmarks
def activity_list(request):
    from itertools import chain
    from operator import attrgetter
    # Retrieve the objects
    game_list = Game.objects.all().order_by('-date_posted')[:50]
    post_list = Post.objects.all().order_by('-date_posted')[:50]
    
    # Combine both lists
    combined_list = list(chain(game_list, post_list))

    # Sort combined list by date_posted (latest first)
    sorted_combined_list = sorted(combined_list, key=attrgetter('date_posted'), reverse=True)

    context = {
        'sorted_combined_list': sorted_combined_list
    }
    
    return render(request, 'the_keep/activity_list.html', context)

@player_required
def confirm_stable(request, slug):
    # Get the Post object based on the slug from the URL
    post = get_object_or_404(Post, slug=slug)
    component_mapping = {
            "Map": Map,
            "Deck": Deck,
            "Landmark": Landmark,
            "Tweak": Tweak,
            "Hireling": Hireling,
            "Vagabond": Vagabond,
            "Faction": Faction,
            "Clockwork": Faction,
        }
    Klass = component_mapping.get(post.component)
    object = get_object_or_404(Klass, slug=slug)

    stable = object.stable_check()
    # print(stable)

    if stable[0] == False:
        messages.info(request, f'{object} has not yet met the stability requirements. Current stats: {stable[1]} plays with {stable[2]} players and {stable[3]} official factions.')
        return redirect(object.get_absolute_url())
    
    # Check if the current user is the designer
    if object.designer != request.user.profile:
        messages.error(request, "You are not authorized to make this change.")
        return redirect(object.get_absolute_url())

    # If form is submitted (POST request)
    if request.method == 'POST':
        # Update the `stable` property to True
        object.status = 1
        object.save()

        # Redirect to a success page or back to the post detail page
        messages.success(request, f'{object.title} has been marked as "Stable".')
        return redirect(object.get_absolute_url())

    # If GET request, render the confirmation form
    form = StatusConfirmForm()
    return render(request, 'the_keep/confirm_stable.html', {'form': form, 'post': object})

@player_required
def confirm_testing(request, slug):
    # Get the Post object based on the slug from the URL
    post = get_object_or_404(Post, slug=slug)
    component_mapping = {
            "Map": Map,
            "Deck": Deck,
            "Landmark": Landmark,
            "Tweak": Tweak,
            "Hireling": Hireling,
            "Vagabond": Vagabond,
            "Faction": Faction,
            "Clockwork": Faction,
        }
    Klass = component_mapping.get(post.component)
    object = get_object_or_404(Klass, slug=slug)

    testing = object.get_games_queryset().count() > 0

    # print(stable)

    if testing == False:
        messages.info(request, f'{object} has not yet recorded a playtest.')
        return redirect(object.get_absolute_url())
    
    # Check if the current user is the designer
    if object.designer != request.user.profile:
        messages.error(request, "You are not authorized to make this change.")
        return redirect(object.get_absolute_url())

    # If form is submitted (POST request)
    if request.method == 'POST':
        # Update the `testing` property to True
        object.status = 2
        object.save()

        # Redirect to a success page or back to the post detail page
        messages.success(request, f'{object.title} has been marked as "Testing".')
        return redirect(object.get_absolute_url())

    # If GET request, render the confirmation form
    form = StatusConfirmForm()
    return render(request, 'the_keep/confirm_testing.html', {'form': form, 'post': object})



#### PNP Assets

@player_required_class_based_view
class PNPAssetCreateView(CreateView):
    model = PNPAsset
    form_class = PNPAssetCreateForm
    template_name = 'the_keep/asset_form.html'

    def form_valid(self, form):
        # Set the 'shared_by' field to the current user's profile
        if not self.request.user.profile.admin:
            form.instance.shared_by = self.request.user.profile
         # Unpin resource
        form.instance.pinned = False
        return super().form_valid(form)

    def get_form_kwargs(self):
        # Add the current user to the form kwargs
        kwargs = super().get_form_kwargs()
        kwargs['profile'] = self.request.user.profile
        return kwargs    
    
    def get_success_url(self):

        # Redirect to the asset list
        return reverse_lazy('asset-list') 




@player_required_class_based_view
class PNPAssetUpdateView(UpdateView):
    model = PNPAsset
    form_class = PNPAssetCreateForm  # Reusing the form
    template_name = 'the_keep/asset_form.html'
    success_url = reverse_lazy('asset-list')  # Redirect after successful update

    # Optionally, override `get_object` to ensure permissions or ownership checks
    def get_object(self, queryset=None):
        obj = super().get_object(queryset=queryset)
                # Ensure the current user can update the object
        if obj.shared_by != self.request.user.profile and not self.request.user.profile.admin:
            raise PermissionDenied("You are not authorized to edit this resource.")
        return obj

    def get_form_kwargs(self):
        # Add the current user to the form kwargs
        kwargs = super().get_form_kwargs()
        kwargs['profile'] = self.request.user.profile
        return kwargs 


class PNPAssetListView(ListView):
    model = PNPAsset
    template_name = 'the_keep/asset_list.html'
    context_object_name = 'objects'

        
        # Filter the queryset if there is a search query
    def get_queryset(self):
        queryset = super().get_queryset()

        player_slug = self.kwargs.get('slug')

        if player_slug:
            # Filter for assets shared by player
            queryset = queryset.filter(shared_by__slug=player_slug)
        else:
            # Filter for only pinned assets
            queryset = queryset.filter(pinned=True)
        
        # Get the search query from the GET parameters
        search_query = self.request.GET.get('search', '')
        search_type = self.request.GET.get('search_type', '')
        file_type = self.request.GET.get('file_type', '')

        # If a search query is provided, filter the queryset based on title, category, and shared_by
        if search_query:
            queryset = queryset.filter(
                Q(title__icontains=search_query) |
                Q(shared_by__discord__icontains=search_query)
            )
        if search_type:
            queryset = queryset.filter(category__icontains=search_type)

        if file_type:
            queryset = queryset.filter(file_type__icontains=file_type)

        return queryset


    def get_context_data(self, **kwargs):
        # Add current user to the context data
        context = super().get_context_data(**kwargs)
        if self.request.user.is_authenticated:
            context['profile'] = self.request.user.profile  # Adding the user to the context
            context['shared_assets'] = PNPAsset.objects.filter(shared_by__slug=self.request.user.profile.slug)
            theme = self.request.user.profile.theme
        else:
            context['profile'] = None
            context['shared_assets'] = None
            theme = None

        # background_image = BackgroundImage.objects.filter(theme=theme, page="library").order_by('?').first()
        # all_foreground_images = ForegroundImage.objects.filter(theme=theme, page="library")
        # # Group the images by location
        # grouped_by_location = groupby(sorted(all_foreground_images, key=lambda x: x.location), key=lambda x: x.location)
        # # Select a random image from each location
        # foreground_images = [random.choice(list(group)) for _, group in grouped_by_location]

        # context['background_image'] = background_image
        # context['foreground_images'] = foreground_images


        return context
    
    def render_to_response(self, context, **response_kwargs):

        # Check if it's an HTMX request
        if self.request.headers.get('HX-Request') == 'true':
            # Only return the part of the template that HTMX will update
            # print("HTMX")
            # print(context)
            return render(self.request, 'the_keep/partials/asset_list_table.html', context)
        # print("NOT HTMX")
        if self.request.user.is_authenticated:
            send_discord_message(f'[{self.request.user}]({build_absolute_uri(self.request, self.request.user.profile.get_absolute_url())}) on Resource Page')
        else:
            send_discord_message(f'{get_uuid(self.request)} on Resource Page')

        return super().render_to_response(context, **response_kwargs)
    
@player_required_class_based_view
class PNPAssetDeleteView(LoginRequiredMixin, UserPassesTestMixin, DeleteView):
    model = PNPAsset
    template_name = 'the_keep/asset_confirm_delete.html'
    success_url = reverse_lazy('asset-list')   # The default success URL after deletion

    def test_func(self):
        asset = self.get_object()
        # print("testing delete function")
        return self.request.user.profile == asset.shared_by or self.request.user.profile.admin  # Ensure only the designer can delete

    def post(self, request, *args, **kwargs):
        # print('Trying to delete')
        asset = self.get_object()
        name = asset.title
        try:
            # Attempt to delete the asset
            response = self.delete(request, *args, **kwargs)
            # Add success message upon successful deletion
            messages.success(request, f"The asset link '{name}' was successfully deleted.")
            return response
        except ProtectedError:
            # Handle the case where the deletion fails due to foreign key protection
            messages.error(request, f"The asset link '{name}' cannot be deleted.")
            # Redirect back to the asset detail page
            return redirect('asset-list')
        except IntegrityError:
            # Handle other integrity errors (if any)
            messages.error(request, "An error occurred while trying to delete this asset.")
            return redirect('asset-list')
        

@admin_required
def pin_asset(request, id):

    object = get_object_or_404(PNPAsset, id=id)
    asset_pinned = object.pinned
    if asset_pinned:
        object.pinned = False
    else:
        object.pinned = True
    object.save()

    return render(request, 'the_keep/partials/asset_pins.html', {'obj': object })

def universal_search(request):
    query = request.GET.get('query', '')
    
    
    if request.user.is_authenticated:
        view_status = request.user.profile.view_status
    else:
        view_status = 4

    # If the query is empty, set all results to empty QuerySets
    players = Profile.objects.none()
    scorecards = ScoreCard.objects.none()
    if not query:
        factions = Faction.objects.none()
        maps = Map.objects.none()
        decks = Deck.objects.none()
        vagabonds = Vagabond.objects.none()
        landmarks = Landmark.objects.none()
        hirelings = Hireling.objects.none()
        tweaks = Tweak.objects.none()
        expansions = Expansion.objects.none()
        games = Game.objects.none()
        tournaments = Tournament.objects.none()
        rounds = Round.objects.none()
        resources = PNPAsset.objects.none()
    else:
        # If the query is not empty, perform the search as usual
        factions = Faction.objects.filter(Q(title__icontains=query)|Q(designer__display_name__icontains=query)|Q(designer__discord__icontains=query), status__lte=view_status).order_by('status')
        maps = Map.objects.filter(Q(title__icontains=query)|Q(designer__display_name__icontains=query)|Q(designer__discord__icontains=query), status__lte=view_status).order_by('status')
        decks = Deck.objects.filter(Q(title__icontains=query)|Q(designer__display_name__icontains=query)|Q(designer__discord__icontains=query), status__lte=view_status).order_by('status')
        vagabonds = Vagabond.objects.filter(Q(title__icontains=query)|Q(designer__display_name__icontains=query)|Q(designer__discord__icontains=query), status__lte=view_status).order_by('status')
        landmarks = Landmark.objects.filter(Q(title__icontains=query)|Q(designer__display_name__icontains=query)|Q(designer__discord__icontains=query), status__lte=view_status).order_by('status')
        hirelings = Hireling.objects.filter(Q(title__icontains=query)|Q(designer__display_name__icontains=query)|Q(designer__discord__icontains=query), status__lte=view_status).order_by('status')
        tweaks = Tweak.objects.filter(Q(title__icontains=query)|Q(designer__display_name__icontains=query)|Q(designer__discord__icontains=query), status__lte=view_status).order_by('status')
        expansions = Expansion.objects.filter(Q(title__icontains=query)|Q(designer__display_name__icontains=query)|Q(designer__discord__icontains=query))
        if request.user.is_authenticated:
            if request.user.profile.player:
                players = Profile.objects.filter(Q(display_name__icontains=query)|Q(discord__icontains=query)|Q(dwd__icontains=query))
                scorecards = ScoreCard.objects.filter(game_group__icontains=query, effort=None, recorder=request.user.profile)
        games = Game.objects.filter(nickname__icontains=query)     
        tournaments = Tournament.objects.filter(name__icontains=query, start_date__lte=timezone.now())  
        rounds = Round.objects.filter(Q(name__icontains=query)|Q(tournament__name__icontains=query), start_date__lte=timezone.now())   
        resources = PNPAsset.objects.filter(Q(title__icontains=query)|Q(shared_by__display_name__icontains=query)|Q(shared_by__discord__icontains=query), pinned=True)


    total_results = (factions.count() + maps.count() + decks.count() + vagabonds.count() +
                     landmarks.count() + hirelings.count() + expansions.count() + 
                     players.count() + games.count() + scorecards.count() + 
                     tournaments.count() + rounds.count() + tweaks.count() + 
                     resources.count())
    
    if total_results == 0:
        no_results = True
    else:
        no_results = False

    if total_results < 10:
        result_count = total_results
    elif total_results < 16:
        result_count = 4
    else:
        result_count = 3

    context = {
        'factions': factions[:result_count],
        'maps': maps[:result_count],
        'decks': decks[:result_count],
        'vagabonds': vagabonds[:result_count],
        'landmarks': landmarks[:result_count],
        'hirelings': hirelings[:result_count],
        'tweaks': tweaks[:result_count],
        'expansions': expansions[:result_count],
        'players': players[:result_count],
        'games': games[:result_count],
        'scorecards': scorecards[:result_count],
        'tournaments': tournaments[:result_count],
        'rounds': rounds[:result_count],
        'resources': resources[:result_count],
        'no_results': no_results,
        'query': query,
    }

    return render(request, 'the_keep/partials/universal_results.html', context)
