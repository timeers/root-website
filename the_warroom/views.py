from django.shortcuts import render
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.decorators import login_required
from django.views.generic import CreateView, ListView
from django.shortcuts import get_object_or_404, redirect
from .models import Game, Effort
from .forms import GameCreateForm, EffortCreateForm



#  A list of all the posts. Most recent update first
class GameListView(ListView):
    model = Game
    template_name = 'the_warroom/games_home.html' # <app>/<model>_<viewtype>.html
    context_object_name = 'games'
    ordering = ['-date_posted']
    paginate_by = 25

class GameCreateView(LoginRequiredMixin, CreateView):
    model = Game
    form_class = GameCreateForm

    def form_valid(self, form):
        form.instance.recorder = self.request.user
        return super().form_valid(form)
    
class EffortCreateView(LoginRequiredMixin, CreateView):
    model = Effort
    form_class = EffortCreateForm

    def form_valid(self, form):
        return super().form_valid(form)

@login_required
def game_detail_view(request, id=None):
    obj = get_object_or_404(Game, id=id)
    print(obj)
    context=  {
        'game': obj
    }
    return render(request, "the_warroom/game_detail.html", context)

@login_required
def record_game(request):
    form = GameCreateForm(request.POST or None)
    context = {
        'form': form, 
    }
    if form.is_valid():
        obj = form.save(commit=False)
        obj.recorder = request.user.profile
        obj.save()
        return redirect(obj.get_absolute_url())
    return render(request, 'the_warroom/record_game.html', context)

@login_required
def update_game(request, id=None):
    obj = get_object_or_404(Game, id=id, recorder=request.user.profile)
    form = GameCreateForm(request.POST or None, instance=obj)
    context = {'form': form, 
               'object': obj,
               }
    if form.is_valid():
        form.save()
        context['message'] = "Game Saved"
    return render(request, 'the_warroom/record_game.html', context)

@login_required
def record_effort(request):
    if request.method == 'POST':
        form = EffortCreateForm(request.POST or None)
        if form.is_valid():
            effort = form.save()
            context = {'effort': effort}
            return render(request, 'the_warroom/partials/effort.html', context)
    return render(request, 'the_warroom/partials/effort_form.html', {'form': EffortCreateForm}) 