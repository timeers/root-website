from django.utils import timezone 
from django.shortcuts import render, get_object_or_404
from django.http import HttpResponseNotFound 
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.contrib.auth.models import User
from django.db.models import Q
from django.core.exceptions import ObjectDoesNotExist
from django.views.generic import (
    ListView, 
    DetailView, 
    CreateView,
    UpdateView,
    DeleteView
)
from the_gatehouse.views import creative_required_class_based_view
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


#  A list of all the posts. Most recent update first
class PostListView(ListView):
    model = Post
    template_name = 'blog/home.html'
    context_object_name = 'posts'
    ordering = ['-date_updated']
    paginate_by = 20

# A list of one specific user's posts
class UserPostListView(ListView):
    model = Post
    template_name = 'blog/user_posts.html'
    context_object_name = 'posts'
    paginate_by = 20

    def get_queryset(self):
        user = get_object_or_404(User, username=self.kwargs.get('username'))
        return Post.objects.filter(designer=user).order_by('-date_updated')
    
# A list of one specific user's posts
class ArtistPostListView(ListView):
    model = Post
    template_name = 'blog/user_posts_art.html'
    context_object_name = 'posts'
    paginate_by = 20

    def get_queryset(self):
        user = get_object_or_404(User, username=self.kwargs.get('username'))
        return Post.objects.filter(artist=user).order_by('-date_updated')

# A list of search results
class SearchPostListView(ListView):
    model = Post
    template_name = 'blog/search_posts.html'
    context_object_name = 'posts'
    paginate_by = 20

    def get_queryset(self):
        search_term = self.request.GET.get('search_term', '')
        return Post.objects.filter(
            Q(title__icontains=search_term)|
            Q(designer__username__icontains=search_term)
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
    return render(request, 'blog/search_posts.html', context=context)

def component_detail_view(request, slug=None):
    post = get_object_or_404(Post, slug=slug)

    component_mapping = {
        "Map": Map,
        "Deck": Deck,
        "Landmark": Landmark,
        "Hireling": Hireling,
        "Vagabond": Vagabond,
        "Faction": Faction,
    }
    Klass = component_mapping.get(post.component)
    
    if Klass is None:
        return HttpResponseNotFound("Component not found.")
    
    obj = get_object_or_404(Klass, slug=slug)

    context = {
        'object': obj
    }
    return render(request, "blog/component_detail.html", context)

class ExpansionDetailView(DetailView):
    model = Expansion

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
