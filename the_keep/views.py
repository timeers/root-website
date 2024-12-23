from django.utils import timezone 
from django.shortcuts import render, get_object_or_404, redirect
from django.http import Http404, HttpResponse, HttpResponseForbidden
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.contrib.auth.decorators import login_required
from django.db.models import Count, F, ExpressionWrapper, FloatField, Q, Case, When, Value
from django.db.models.functions import Cast
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
from django.conf import settings
from django.db import IntegrityError
from django.db.models import ProtectedError, Count
from django.urls import reverse
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
from the_gatehouse.views import designer_required_class_based_view, designer_required
from .models import (
    Post, Expansion,
    Faction, Vagabond,
      Map, Deck,
      Hireling, Landmark,
      Piece,
    )
from .forms import (PostCreateForm, MapCreateForm, 
                    DeckCreateForm, LandmarkCreateForm,
                    HirelingCreateForm, VagabondCreateForm,
                    FactionCreateForm, ExpansionCreateForm,
                    PieceForm, ClockworkCreateForm
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

# A list of one specific user's posts
class UserPostListView(ListView):
    model = Post
    template_name = 'the_keep/user_posts.html'
    context_object_name = 'posts'
    paginate_by = 20

    def get_queryset(self):
        user = get_object_or_404(Profile, slug=self.kwargs.get('slug'))
        return Post.objects.filter(designer=user).order_by('-date_updated')
    
# A list of one specific user's posts
class ArtistPostListView(ListView):
    model = Post
    template_name = 'the_keep/user_posts.html'
    context_object_name = 'posts'
    paginate_by = 20

    def get_queryset(self):
        user = get_object_or_404(Profile, slug=self.kwargs.get('slug'))
        return Post.objects.filter(artist=user).order_by('-date_updated')


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


# @designer_required_class_based_view
# def manage_expansion(request, slug=None):
#     if slug:
#         obj = get_object_or_404(Expansion, slug=slug)
#     else:
#         obj = Expansion()  # Create a new Game instance but do not save it yet
#     user = request.user
#     form = ExpansionCreateForm(request.POST or None, instance=obj, user=user)

#     context = {
#         'form': form,
#         'object': obj,
#     }
#     # Handle form submission
#     if form.is_valid():
#         parent = form.save(commit=False)
#         parent.designer = request.user.profile  # Set the recorder
#         parent.save()  # Save the new or updated Game instance
#         context['message'] = "Game Saved"
#         return redirect(parent.get_absolute_url())
    
#     return render(request, 'the_keep/expansion_form.html', context)







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




class ComponentDetailListView(ListView):
    detail_context_object_name = 'object'
    # template_name = 'the_keep/component_detail_list.html'
    paginate_by = settings.PAGE_SIZE
    
    def get(self, request, *args, **kwargs):
        self.object = self.get_object()  # Set the object here
        return super(ComponentDetailListView, self).get(request, *args, **kwargs)

    def get_template_names(self):
        if self.request.htmx:
            return 'the_keep/partials/game_list.html'
        return 'the_keep/component_detail_list.html'

    def get_queryset(self):
        if not hasattr(self, 'object'):
            self.object = self.get_object()  # Ensure the object is set
        if not self.request.user.is_authenticated:
            queryset = self.object.get_games_queryset().only_official_components()
        else:
            # If show official only if user is not a member of Weird Root
            if self.request.user.profile.weird:
                queryset = self.object.get_games_queryset()
            else:
                queryset = self.object.get_games_queryset().only_official_components()

        self.filterset = GameFilter(self.request.GET, queryset=queryset)
        return self.filterset.qs


    def get_object(self):
        # Retrieve the faction based on the primary key
        slug = self.kwargs.get('slug')
        if slug is None:
            raise Http404('Slug is required in the URL.')
        post = get_object_or_404(Post, slug=self.kwargs.get('slug'))
        component_mapping = {
            "Map": Map,
            "Deck": Deck,
            "Landmark": Landmark,
            "Hireling": Hireling,
            "Vagabond": Vagabond,
            "Faction": Faction,
            "Clockwork": Faction,
        }
        Klass = component_mapping.get(post.component)
        return get_object_or_404(Klass, slug=slug)

    def get_context_data(self, **kwargs):
        context = super(ComponentDetailListView, self).get_context_data(**kwargs)
        context[self.detail_context_object_name] = self.object
        commentform = PostCommentCreateForm()
        # Get the ordered queryset of games
        games = self.get_queryset()
        # Initialize variables for aggregate data
        win_count = 0
        coalition_count = 0
        win_rate = 0
        tourney_points = 0
        total_efforts = 0

        # Get top players for factions
        top_players = []
        most_players = []

        # If loading the initial page or filtering results run this calculation
        # If just getting the next page we can skip this
        if not self.request.GET.get('page'):
            if self.object.component == "Faction":
                top_players = Profile.top_players(faction_id=self.object.id, limit=5)
                most_players = Profile.top_players(faction_id=self.object.id, limit=5, top_quantity=True, game_threshold=1)

            if self.object.component == "Faction" or self.object.component == "Vagabond":
                for game in games:
                    for effort in game.efforts.all():
                        if self.object.component == "Faction":
                            if effort.faction == self.object:
                                total_efforts += 1
                                if effort.win:
                                    win_count += 1
                                    if game.coalition_win:
                                        coalition_count += 1
                        else:
                            if effort.faction.title == "Vagabond" and effort.vagabond == self.object:
                                total_efforts += 1
                                if effort.win:
                                    win_count += 1
                                    if game.coalition_win:
                                        coalition_count += 1

                if total_efforts > 0:
                    win_rate = (win_count - (coalition_count / 2)) / total_efforts * 100
                else:
                    win_rate = 0
                tourney_points = win_count - (coalition_count / 2)


        # Paginate games
        paginator = Paginator(games, self.paginate_by)  # Use the queryset directly
        page_number = self.request.GET.get('page')  # Get the page number from the request

        try:
            page_obj = paginator.get_page(page_number)  # Get the specific page of games
        except EmptyPage:
            page_obj = paginator.page(paginator.num_pages)  # Redirect to the last page if invalid
        # context['games_total'] = games.count()
        # context['games'] = page_obj  # Pass the paginated page object to the context
        # context['is_paginated'] = paginator.num_pages > 1  # Set is_paginated boolean
        # context['page_obj'] = page_obj  # Pass the page_obj to the context
        # context['commentform'] = commentform
        # context['form'] = self.filterset.form
        # context['filterset'] = self.filterset    
        # context['top_players'] = top_players 
        # context['most_players'] = most_players 
        # context['win_count'] = win_count
        # context['coalition_count'] = coalition_count
        # context['win_rate'] = win_rate
        # context['tourney_points'] = tourney_points
        # context['total_efforts'] = total_efforts

        # Prepare the context dictionary
        context_data = {
            'games_total': games.count(),
            'games': page_obj,  # Pagination applied here
            'is_paginated': len(games) > self.paginate_by,
            'page_obj': page_obj,  # Pass the paginated page object to the context
            'commentform': commentform,
            'form': self.filterset.form,
            'filterset': self.filterset,
            'top_players': top_players,
            'most_players': most_players,
            'win_count': win_count,
            'coalition_count': coalition_count,
            'win_rate': win_rate,
            'tourney_points': tourney_points,
            'total_efforts': total_efforts
        }

        # Update the context with the context_data
        context.update(context_data)                        

        
        return context


@login_required
@bookmark_toggle(Post)
def bookmark_post(request, object):
    return render(request, 'the_keep/partials/bookmarks.html', {'object': object })



# Search Page
def list_view(request, slug=None):
    posts, search, search_type, designer = _search_components(request, slug)
    designers = Profile.objects.annotate(posts_count=Count('posts')).filter(posts_count__gt=0)
    context = {
        "posts": posts, 
        'search': search or "", 
        'search_type': search_type or "",
        "designers": designers,
        'designer': designer,
        'is_search_view': False,
        'slug': slug,
        }
    # if request.htmx:
    #     return render(request, "the_keep/partials/search_body.html", context)    

    return render(request, "the_keep/list.html", context)


def search_view(request, slug=None):
    posts, search, search_type, designer = _search_components(request, slug)
    # Get all designers (Profiles) who have at least one post
    designers = Profile.objects.annotate(posts_count=Count('posts')).filter(posts_count__gt=0)
    context = {
        "posts": posts, 
        'search': search or "", 
        'search_type': search_type or "",
        "designers": designers,
        'designer': designer,
        'is_search_view': True,
        'slug': slug,
        }
    return render(request, "the_keep/search_results.html", context)


def _search_components(request, slug=None):
    search = request.GET.get('search')
    search_type = request.GET.get('search_type', '')
    designer = request.GET.get('designer') 
    page = request.GET.get('page')
    if not request.user.is_authenticated:
        posts = Post.objects.filter(official=True)
    else:
        if request.user.profile.weird:
            posts = Post.objects.all()
        else:
            posts = Post.objects.filter(official=True)
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

    paginator = Paginator(posts, settings.PAGE_SIZE)
    try:
        posts = paginator.page(page)
    except PageNotAnInteger:
        posts = paginator.page(1)
    except EmptyPage:
        posts = paginator.page(paginator.num_pages)
    return posts, search or "", search_type or "", designer or ""



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

    form = PieceForm(request.POST or None, instance=obj)

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
