from django.utils import timezone 
from django.shortcuts import render, get_object_or_404, redirect
from django.http import Http404, HttpResponse, HttpResponseForbidden
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.contrib.auth.decorators import login_required
from django.db.models import Count, F, ExpressionWrapper, FloatField, Q, Case, When, Value
from django.db.models.functions import Cast
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
from django.core.exceptions import PermissionDenied
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
from the_warroom.models import Game
from the_gatehouse.models import Profile
from the_gatehouse.views import (designer_required_class_based_view, designer_required, 
                                 player_required, player_required_class_based_view,
                                 admin_onboard_required, admin_required)
from .models import (
    Post, Expansion,
    Faction, Vagabond,
    Map, Deck,
    Hireling, Landmark,
    Piece, Tweak,
    PNPAsset,
    )
from .forms import (PostCreateForm, MapCreateForm, 
                    DeckCreateForm, LandmarkCreateForm,
                    HirelingCreateForm, VagabondCreateForm,
                    FactionCreateForm, ExpansionCreateForm,
                    PieceForm, ClockworkCreateForm,
                    StableConfirmForm, TweakCreateForm,
                    PNPAssetCreateForm,
)
from the_tavern.forms import PostCommentCreateForm
from the_tavern.views import bookmark_toggle


# #  A list of all the posts. Most recent update first
# class PostListView(ListView):
#     model = Post
#     template_name = 'the_keep/home.html'
#     context_object_name = 'posts'
#     ordering = ['-date_updated']
#     paginate_by = 20

#     def get_queryset(self):
#         # Filter posts to only include those where official is True
#         if not self.request.user.is_authenticated:
#             qs = Post.objects.filter(official=True)
#         else:
#             if self.request.user.profile.weird:
#                 qs = Post.objects.all()
#             else:
#                 qs = Post.objects.filter(official=True)

#         return qs




class ExpansionDetailView(DetailView):
    model = Expansion

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


class PostCreateView(LoginRequiredMixin, CreateView):
    """
    A base class for all CreateViews that require a designer field to be set to
    the current logged-in user's profile and also pass the user to the form.
    """
    def form_valid(self, form):
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
        form.instance.designer = self.request.user.profile
        form.instance.date_updated = timezone.now()  # Set the updated timestamp
        return super().form_valid(form)

    def test_func(self):
        obj = self.get_object()
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
        try:
            # Attempt to delete the post
            response = self.delete(request, *args, **kwargs)
            # Add success message upon successful deletion
            messages.success(request, f"The {post.component} '{name}' was successfully deleted.")
            return response
        except ProtectedError:
            # Handle the case where the deletion fails due to foreign key protection
            messages.error(request, f"The {post.component} '{name}' cannot be deleted because it has been used in a game.")
            # Redirect back to the post detail page
            return redirect(detail_view, post.slug)  # Make sure `post.get_absolute_url()` is correct
        except IntegrityError:
            # Handle other integrity errors (if any)
            messages.error(request, "An error occurred while trying to delete this post.")
            return redirect(detail_view, post.slug) 



def about(request, *args, **kwargs):
    return render(request, 'the_keep/about.html', {'title': 'About'})




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
    stable_ready = None
    if request.user.is_authenticated:
        if object.designer == request.user.profile:
            stable_ready = object.stable_check()
    # print(f'Stable Ready: {stable_ready}')

    # Start with the base queryset
    games = object.get_games_queryset()

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
    # Get top players for factions
    top_players = []
    most_players = []
    win_count = 0
    coalition_count = 0
    win_rate = 0
    tourney_points = 0
    total_efforts = 0

    # On first load get faction and VB Stats
    page_number = request.GET.get('page')  # Get the page number from the request
    if not page_number:
        if object.component == "Faction":
            top_players = Profile.top_players(faction_id=object.id, limit=5)
            most_players = Profile.top_players(faction_id=object.id, limit=5, top_quantity=True, game_threshold=1)
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
    }
    if request.htmx:
            return render(request, 'the_keep/partials/game_list.html', context)
    return render(request, 'the_keep/component_detail_list.html', context)



@login_required
@bookmark_toggle(Post)
def bookmark_post(request, object):
    return render(request, 'the_keep/partials/bookmarks.html', {'object': object })



# Search Page
def list_view(request, slug=None):
    posts, search, search_type, designer, faction_type, reach_value, status = _search_components(request, slug)
    # designers = Profile.objects.annotate(posts_count=Count('posts')).filter(posts_count__gt=0)
    if request.user.is_authenticated:
        if request.user.profile.weird:
            designers = Profile.objects.annotate(posts_count=Count('posts')).filter(posts_count__gt=0)
        else:
            # Filter designers who have at least one post with 'official' property set to True
            designers = Profile.objects.annotate(official_posts_count=Count('posts', filter=Q(posts__official=True))) \
                                .filter(official_posts_count__gt=0)
    else:
        designers = Profile.objects.annotate(posts_count=Count('posts')).filter(posts_count__gt=0)
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
        }
    # if request.htmx:
    #     return render(request, "the_keep/partials/search_body.html", context)    

    return render(request, "the_keep/list.html", context)


def search_view(request, slug=None):
    posts, search, search_type, designer, faction_type, reach_value, status = _search_components(request, slug)
    # Get all designers (Profiles) who have at least one post
    if request.user.is_authenticated:
        if request.user.profile.weird:
            designers = Profile.objects.annotate(posts_count=Count('posts')).filter(posts_count__gt=0)
        else:
            # Filter designers who have at least one post with 'official' property set to True
            designers = Profile.objects.annotate(official_posts_count=Count('posts', filter=Q(posts__official=True))) \
                                .filter(official_posts_count__gt=0)
    else:
        designers = Profile.objects.annotate(posts_count=Count('posts')).filter(posts_count__gt=0)
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

    if faction_type or reach_value:
        if request.user.is_authenticated:
            if request.user.profile.weird:
                posts = Faction.objects.all().prefetch_related('designer')
            else:
                posts = Faction.objects.filter(official=True).prefetch_related('designer')
        else:
            posts = Faction.objects.all().prefetch_related('designer')

        if faction_type:
            posts = posts.filter(type=faction_type)
        if reach_value:
            posts = posts.filter(reach=reach_value)

    else:
        if request.user.is_authenticated:
            if request.user.profile.weird:
                posts = Post.objects.all().prefetch_related('designer')
            else:
                posts = Post.objects.filter(official=True).prefetch_related('designer')
        else:
            posts = Post.objects.all().prefetch_related('designer')

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
            raise HttpResponseForbidden()
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
        raise HttpResponseForbidden()




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
        object.status = 'Stable'
        object.save()

        # Redirect to a success page or back to the post detail page
        messages.success(request, "The post has been marked as stable.")
        return redirect(object.get_absolute_url())

    # If GET request, render the confirmation form
    form = StableConfirmForm()
    return render(request, 'the_keep/confirm_stable.html', {'form': form, 'post': object})



#### PNP Assets

@player_required_class_based_view
class PNPAssetCreateView(CreateView):
    model = PNPAsset
    form_class = PNPAssetCreateForm
    template_name = 'the_keep/asset_form.html'

    def form_valid(self, form):
        # Set the shared_by field to the current user's profile
        if not self.request.user.profile.admin:
            form.instance.shared_by = self.request.user.profile
        else:
            form.instance.pinned = True
        
        return super().form_valid(form)

    def get_form_kwargs(self):
        # Add the current user to the form kwargs
        kwargs = super().get_form_kwargs()
        kwargs['profile'] = self.request.user.profile
        return kwargs    
    
    def get_success_url(self):
        # You can return different success URLs based on the user profile
        if self.request.user.profile.admin:
            # Redirect to the asset list for admins
            return reverse_lazy('asset-list')  # Modify with your actual admin URL
        else:
            # Default URL (e.g., asset list or home page)
            return reverse_lazy('player-detail', kwargs={'slug': self.request.user.profile.slug})



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
        
        # If a search query is provided, filter the queryset based on title, category, and shared_by
        if search_query:
            queryset = queryset.filter(
                Q(title__icontains=search_query) |
                Q(category__icontains=search_query) |
                Q(shared_by__discord__icontains=search_query)
            )

        return queryset


    def get_context_data(self, **kwargs):
        # Add current user to the context data
        context = super().get_context_data(**kwargs)
        if self.request.user.is_authenticated:
            context['profile'] = self.request.user.profile  # Adding the user to the context
        else:
            context['profile'] = None
        return context
    
    def render_to_response(self, context, **response_kwargs):
        # Check if it's an HTMX request
        if self.request.headers.get('HX-Request') == 'true':
            # Only return the part of the template that HTMX will update
            return render(self.request, 'the_keep/partials/asset_list_table.html', context)

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