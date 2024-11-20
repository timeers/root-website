from django.utils import timezone 
from django.shortcuts import render, get_object_or_404
from django.http import Http404
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.db.models import Q
from django.core.paginator import Paginator, EmptyPage
from django.conf import settings
from the_warroom.filters import GameFilter
from django.views.generic import (
    ListView, 
    DetailView, 
    CreateView,
    UpdateView,
    DeleteView
)
from the_gatehouse.models import Profile
from the_gatehouse.views import designer_required_class_based_view
from .models import (
    Post, Expansion,
    Faction, Vagabond,
      Map, Deck,
      Hireling, Landmark
    )
from .forms import (PostCreateForm, MapCreateForm, 
                    DeckCreateForm, LandmarkCreateForm,
                    HirelingCreateForm, VagabondCreateForm,
                    FactionCreateForm,
)
from the_tavern.forms import PostCommentCreateForm


#  A list of all the posts. Most recent update first
class PostListView(ListView):
    model = Post
    template_name = 'the_keep/home.html'
    context_object_name = 'posts'
    ordering = ['-date_updated']
    paginate_by = 20

    def get_queryset(self):
        # Filter posts to only include those where official is True
        if not self.request.user.is_authenticated:
            qs = Post.objects.filter(official=True).order_by('-date_updated')
        else:
            if self.request.user.profile.weird:
                qs = Post.objects.all().order_by('-date_updated')
            else:
                qs = Post.objects.filter(official=True).order_by('-date_updated')

        return qs

# A list of one specific user's posts
class UserPostListView(ListView):
    model = Post
    template_name = 'the_keep/user_posts.html'
    context_object_name = 'posts'
    paginate_by = 20

    def get_queryset(self):
        user = get_object_or_404(Profile, discord=self.kwargs.get('discord'))
        return Post.objects.filter(designer=user).order_by('-date_updated')
    
# A list of one specific user's posts
class ArtistPostListView(ListView):
    model = Post
    template_name = 'the_keep/user_posts_art.html'
    context_object_name = 'posts'
    paginate_by = 20

    def get_queryset(self):
        user = get_object_or_404(Profile, discord=self.kwargs.get('discord'))
        return Post.objects.filter(artist=user).order_by('-date_updated')

# A list of search results
class SearchPostListView(ListView):
    model = Post
    template_name = 'the_keep/search_posts.html'
    context_object_name = 'posts'
    paginate_by = 20

    def get_queryset(self):
        search_term = self.request.GET.get('search_term', '')
        return Post.objects.filter(
            Q(title__icontains=search_term)|
            Q(designer__discord__icontains=search_term)|
            Q(component__icontains=search_term)
            ).order_by('-date_posted')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['search_term'] = self.kwargs.get('search_term', '')
        return context

def post_search_view(request):
    query = request.GET.get('q')
    qs = Post.objects.search(query=query)
    context = {
        "object_list": qs
    }
    return render(request, 'the_keep/search_posts.html', context=context)


class ExpansionDetailView(DetailView):
    model = Expansion


# START CREATE VIEWS
@designer_required_class_based_view
class MapCreateView(LoginRequiredMixin, CreateView):
    model = Map
    form_class = MapCreateForm

    def form_valid(self, form):
        form.instance.designer = self.request.user.profile
        return super().form_valid(form)
    
@designer_required_class_based_view
class DeckCreateView(LoginRequiredMixin, CreateView):
    model = Deck
    form_class = DeckCreateForm

    def form_valid(self, form):
        form.instance.designer = self.request.user.profile
        return super().form_valid(form)
    
@designer_required_class_based_view
class LandmarkCreateView(LoginRequiredMixin, CreateView):
    model = Landmark
    form_class = LandmarkCreateForm

    def form_valid(self, form):
        form.instance.designer = self.request.user.profile
        return super().form_valid(form)

@designer_required_class_based_view
class HirelingCreateView(LoginRequiredMixin, CreateView):
    model = Hireling
    form_class = HirelingCreateForm

    def form_valid(self, form):
        form.instance.designer = self.request.user.profile
        return super().form_valid(form)

@designer_required_class_based_view
class VagabondCreateView(LoginRequiredMixin, CreateView):
    model = Vagabond
    form_class = VagabondCreateForm

    def form_valid(self, form):
        form.instance.designer = self.request.user.profile
        return super().form_valid(form)

@designer_required_class_based_view
class FactionCreateView(LoginRequiredMixin, CreateView):
    model = Faction
    form_class = FactionCreateForm

    def form_valid(self, form):
        form.instance.designer = self.request.user.profile
        return super().form_valid(form)
# END CREATE VIEWS

@designer_required_class_based_view
class PostUpdateView(LoginRequiredMixin, UserPassesTestMixin, UpdateView):
    model = Post
    # This is not working as hoped. It is just returning the PostCreateForm
    def get_form_class(self):
        post = self.get_object()  # Get the specific post instance
        if post.component == "M" or post.component == "Map":
            return MapCreateForm
        elif post.component == "D" or post.component == "Deck":
            return DeckCreateForm
        elif post.component == "L" or post.component == "Landmark":
            return LandmarkCreateForm
        elif post.component == "H" or post.component == "Hireling":
            return HirelingCreateForm
        elif post.component == "V" or post.component == "Vagabond":
            return VagabondCreateForm
        elif post.component == "F" or post.component == "Faction":
            return PostCreateForm
        else:
            return PostCreateForm

    def form_valid(self, form):
        form.instance.designer = self.request.user
        form.instance.date_updated = timezone.now()
        return super().form_valid(form)
    
    def test_func(self):
        post = self.get_object()
        if self.request.user.profile == post.designer:
            return True
        return False

@designer_required_class_based_view
class MapUpdateView(LoginRequiredMixin, UserPassesTestMixin, UpdateView):
    model = Map
    form_class = MapCreateForm

    def form_valid(self, form):
        form.instance.designer = self.request.user.profile
        form.instance.date_updated = timezone.now()
        return super().form_valid(form)
    
    def test_func(self):
        post = self.get_object()
        if self.request.user.profile == post.designer:
            return True
        return False

@designer_required_class_based_view
class DeckUpdateView(LoginRequiredMixin, UserPassesTestMixin, UpdateView):
    model = Deck
    form_class = DeckCreateForm

    def form_valid(self, form):
        form.instance.designer = self.request.user.profile
        form.instance.date_updated = timezone.now()
        return super().form_valid(form)
    
    def test_func(self):
        post = self.get_object()
        if self.request.user.profile == post.designer:
            return True
        return False

@designer_required_class_based_view
class LandmarkUpdateView(LoginRequiredMixin, UserPassesTestMixin, UpdateView):
    model = Landmark
    form_class = LandmarkCreateForm

    def form_valid(self, form):
        form.instance.designer = self.request.user.profile
        form.instance.date_updated = timezone.now()
        return super().form_valid(form)
    
    def test_func(self):
        post = self.get_object()
        if self.request.user.profile == post.designer:
            return True
        return False

@designer_required_class_based_view
class HirelingUpdateView(LoginRequiredMixin, UserPassesTestMixin, UpdateView):
    model = Hireling
    form_class = HirelingCreateForm

    def form_valid(self, form):
        form.instance.designer = self.request.user.profile
        form.instance.date_updated = timezone.now()
        return super().form_valid(form)
    
    def test_func(self):
        post = self.get_object()
        if self.request.user.profile == post.designer:
            return True
        return False

@designer_required_class_based_view
class VagabondUpdateView(LoginRequiredMixin, UserPassesTestMixin, UpdateView):
    model = Vagabond
    form_class = VagabondCreateForm

    def form_valid(self, form):
        form.instance.designer = self.request.user.profile
        form.instance.date_updated = timezone.now()
        return super().form_valid(form)
    
    def test_func(self):
        post = self.get_object()
        if self.request.user.profile == post.designer:
            return True
        return False
    
@designer_required_class_based_view  
class FactionUpdateView(LoginRequiredMixin, UserPassesTestMixin, UpdateView):
    model = Faction
    form_class = FactionCreateForm

    def form_valid(self, form):
        form.instance.designer = self.request.user.profile
        form.instance.date_updated = timezone.now()
        return super().form_valid(form)
    
    def test_func(self):
        post = self.get_object()
        if self.request.user.profile == post.designer:
            return True
        return False

class PostDeleteView(LoginRequiredMixin, UserPassesTestMixin, DeleteView):
    model = Post
    success_url = '/'

    def test_func(self):
        print("testing if user is owner")
        post = self.get_object()
        print("post")
        print(post.designer)
        print(self.request.user.profile)
        print(post.designer == self.request.user.profile)
        return self.request.user.profile == post.designer

def about(request, *args, **kwargs):
    if request.method == 'POST':
        number = request.POST.get('player-number')
        player = request.POST.get('player-name')
        faction = request.POST.get('faction')
        score = request.POST.get('score')
        
        print(f'Seat: {number}, Player: {player}, Faction: {faction}, Score: {score}')

    return render(request, 'the_keep/about.html', {'title': 'About'})

def test(request):
    return render(request, 'the_keep/test.html', {'title': 'Test'})



class ComponentDetailListView(ListView):
    detail_context_object_name = 'object'
    # template_name = 'the_keep/component_detail_list.html'
    paginate_by = settings.PAGE_SIZE
    
    def get(self, request, *args, **kwargs):
        self.object = self.get_object()  # Set the object here
        return super(ComponentDetailListView, self).get(request, *args, **kwargs)

    def get_template_names(self):
        if self.request.htmx:
            return 'the_warroom/partials/hx_game_list.html'
        return 'the_keep/component_detail_list.html'

    # def get_queryset(self):
    #     queryset = self.object.get_games_queryset() 
    #     self.filterset = GameFilter(self.request.GET, queryset=queryset)
    #     return self.filterset.qs
    
    def get_queryset(self):
        if not hasattr(self, 'object'):
            self.object = self.get_object()  # Ensure the object is set
        if not self.request.user.is_authenticated:
            queryset = self.object.get_games_queryset().only_official_components()
        else:
            if self.request.user.profile.weird:
                print("include weird games")
                queryset = self.object.get_games_queryset()
            else:
                print('exclude weird games')
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
        }
        Klass = component_mapping.get(post.component)
        return get_object_or_404(Klass, slug=slug)

    def get_context_data(self, **kwargs):
        context = super(ComponentDetailListView, self).get_context_data(**kwargs)
        context[self.detail_context_object_name] = self.object
        commentform = PostCommentCreateForm()
        # Get the ordered queryset of games
        games = self.get_queryset()

        # Paginate games
        paginator = Paginator(games, self.paginate_by)  # Use the queryset directly
        page_number = self.request.GET.get('page')  # Get the page number from the request

        try:
            page_obj = paginator.get_page(page_number)  # Get the specific page of games
        except EmptyPage:
            page_obj = paginator.page(paginator.num_pages)  # Redirect to the last page if invalid
        context['games_total'] = games.count()
        context['games'] = page_obj  # Pass the paginated page object to the context
        context['is_paginated'] = paginator.num_pages > 1  # Set is_paginated boolean
        context['page_obj'] = page_obj  # Pass the page_obj to the context
        context['commentform'] = commentform
        context['form'] = self.filterset.form
        context['filterset'] = self.filterset     
        
        return context
