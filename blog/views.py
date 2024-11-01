from django.utils import timezone 
from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.contrib.auth.models import User
from django.db.models import Q
from django.views.generic import (
    ListView, 
    DetailView, 
    CreateView,
    UpdateView,
    DeleteView
)
from the_gatehouse.views import creative_required_class_based_view
from .models import Post, Map, Vagabond, Hireling, Landmark, Deck, Faction, Expansion
from .forms import (PostCreateForm, MapCreateForm, 
                    DeckCreateForm, LandmarkCreateForm,
                    HirelingCreateForm, VagabondCreateForm,
                    FactionCreateForm,
)


#  A list of all the posts. Most recent update first
class PostListView(ListView):
    model = Post
    template_name = 'blog/home.html' # <app>/<model>_<viewtype>.html
    context_object_name = 'posts'
    ordering = ['-date_updated']
    paginate_by = 10

# A list of one specific user's posts
class UserPostListView(ListView):
    model = Post
    template_name = 'blog/user_posts.html' # <app>/<model>_<viewtype>.html
    context_object_name = 'posts'
    paginate_by = 10

    def get_queryset(self):
        user = get_object_or_404(User, username=self.kwargs.get('username'))
        return Post.objects.filter(designer=user).order_by('-date_updated')
    
# A list of one specific user's posts
class ArtistPostListView(ListView):
    model = Post
    template_name = 'blog/user_posts_art.html' # <app>/<model>_<viewtype>.html
    context_object_name = 'posts'
    paginate_by = 10

    def get_queryset(self):
        user = get_object_or_404(User, username=self.kwargs.get('username'))
        return Post.objects.filter(artist=user).order_by('-date_updated')

# A list of search results
class SearchPostListView(ListView):
    model = Post
    template_name = 'blog/search_posts.html' # <app>/<model>_<viewtype>.html
    context_object_name = 'posts'
    paginate_by = 10

    def get_queryset(self):
        search_term = self.request.GET.get('search_term', '')
        return Post.objects.filter(
            Q(title__icontains=search_term)|
            Q(designer__username__icontains=search_term)|
            Q(artist__username__icontains=search_term) 
            ).order_by('-date_posted')
#           Q(description__icontains=search_term)|
#           Removed this because I don't think I want search to search everything. Just Name, Username and Artist (might want to remove artist search just for clarity)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['search_term'] = self.kwargs.get('search_term', '')
        return context

class PostDetailView(DetailView):
    model = Post

class ExpansionDetailView(DetailView):
    model = Expansion

class MapDetailView(DetailView):
    model = Map

class DeckDetailView(DetailView):
    model = Deck

class LandmarkDetailView(DetailView):
    model = Landmark

class HirelingDetailView(DetailView):
    model = Hireling

class VagabondDetailView(DetailView):
    model = Vagabond

class FactionDetailView(DetailView):
    model = Faction

# Moved this to the form.py file so that I can make custom labels
@creative_required_class_based_view
class PostCreateView(LoginRequiredMixin, CreateView):
    model = Post
    form_class = PostCreateForm

    def form_valid(self, form):
        form.instance.designer = self.request.user
        return super().form_valid(form)


# START CREATE VIEWS
@creative_required_class_based_view
class MapCreateView(LoginRequiredMixin, CreateView):
    model = Map
    form_class = MapCreateForm

    def form_valid(self, form):
        form.instance.designer = self.request.user
        return super().form_valid(form)
    
@creative_required_class_based_view
class DeckCreateView(LoginRequiredMixin, CreateView):
    model = Deck
    form_class = DeckCreateForm

    def form_valid(self, form):
        form.instance.designer = self.request.user
        return super().form_valid(form)
    
@creative_required_class_based_view
class LandmarkCreateView(LoginRequiredMixin, CreateView):
    model = Landmark
    form_class = LandmarkCreateForm

    def form_valid(self, form):
        form.instance.designer = self.request.user
        return super().form_valid(form)

@creative_required_class_based_view
class HirelingCreateView(LoginRequiredMixin, CreateView):
    model = Hireling
    form_class = HirelingCreateForm

    def form_valid(self, form):
        form.instance.designer = self.request.user
        return super().form_valid(form)

@creative_required_class_based_view
class VagabondCreateView(LoginRequiredMixin, CreateView):
    model = Vagabond
    form_class = VagabondCreateForm

    def form_valid(self, form):
        form.instance.designer = self.request.user
        return super().form_valid(form)

@creative_required_class_based_view
class FactionCreateView(LoginRequiredMixin, CreateView):
    model = Faction
    form_class = FactionCreateForm

    def form_valid(self, form):
        form.instance.designer = self.request.user
        return super().form_valid(form)
# END CREATE VIEWS

@creative_required_class_based_view
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
        if self.request.user == post.designer:
            return True
        return False

@creative_required_class_based_view
class MapUpdateView(LoginRequiredMixin, UserPassesTestMixin, UpdateView):
    model = Map
    form_class = MapCreateForm

    def form_valid(self, form):
        form.instance.designer = self.request.user
        form.instance.date_updated = timezone.now()
        return super().form_valid(form)
    
    def test_func(self):
        post = self.get_object()
        if self.request.user == post.designer:
            return True
        return False

@creative_required_class_based_view
class DeckUpdateView(LoginRequiredMixin, UserPassesTestMixin, UpdateView):
    model = Deck
    form_class = DeckCreateForm

    def form_valid(self, form):
        form.instance.designer = self.request.user
        form.instance.date_updated = timezone.now()
        return super().form_valid(form)
    
    def test_func(self):
        post = self.get_object()
        if self.request.user == post.designer:
            return True
        return False

@creative_required_class_based_view
class LandmarkUpdateView(LoginRequiredMixin, UserPassesTestMixin, UpdateView):
    model = Landmark
    form_class = LandmarkCreateForm

    def form_valid(self, form):
        form.instance.designer = self.request.user
        form.instance.date_updated = timezone.now()
        return super().form_valid(form)
    
    def test_func(self):
        post = self.get_object()
        if self.request.user == post.designer:
            return True
        return False

@creative_required_class_based_view
class HirelingUpdateView(LoginRequiredMixin, UserPassesTestMixin, UpdateView):
    model = Hireling
    form_class = HirelingCreateForm

    def form_valid(self, form):
        form.instance.designer = self.request.user
        form.instance.date_updated = timezone.now()
        return super().form_valid(form)
    
    def test_func(self):
        post = self.get_object()
        if self.request.user == post.designer:
            return True
        return False

@creative_required_class_based_view
class VagabondUpdateView(LoginRequiredMixin, UserPassesTestMixin, UpdateView):
    model = Vagabond
    form_class = VagabondCreateForm

    def form_valid(self, form):
        form.instance.designer = self.request.user
        form.instance.date_updated = timezone.now()
        return super().form_valid(form)
    
    def test_func(self):
        post = self.get_object()
        if self.request.user == post.designer:
            return True
        return False
    
@creative_required_class_based_view  
class FactionUpdateView(LoginRequiredMixin, UserPassesTestMixin, UpdateView):
    model = Faction
    form_class = FactionCreateForm

    def form_valid(self, form):
        form.instance.designer = self.request.user
        form.instance.date_updated = timezone.now()
        return super().form_valid(form)
    
    def test_func(self):
        post = self.get_object()
        if self.request.user == post.designer:
            return True
        return False

@creative_required_class_based_view
class PostDeleteView(LoginRequiredMixin, UserPassesTestMixin, DeleteView):
    model = Post
    success_url = '/'

    def test_func(self):
        post = self.get_object()
        if self.request.user == post.designer:
            return True
        return False

def about(request):
    return render(request, 'blog/about.html', {'title': 'About'})

def test(request):
    return render(request, 'blog/test.html', {'title': 'Test'})
