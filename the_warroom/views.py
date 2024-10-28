from django.shortcuts import render
from django.contrib.auth.mixins import LoginRequiredMixin
from django.views.generic import CreateView
from .models import Game, Effort
from .forms import GameCreateForm, EffortCreateForm

# Create your views here.
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
    
def record_game(request):
    context = {'form': EffortCreateForm(), 'efforts': Effort.objects.all(), 'game': GameCreateForm()}
    return render(request, 'the_warroom/record_game.html', context)
    
def record_effort(request):
    if request.method == 'POST':
        form = EffortCreateForm(request.POST or None)
        if form.is_valid():
            effort = form.save()
            context = {'effort': effort}
            return render(request, 'the_warroom/partials/effort.html', context)
    return render(request, 'the_warroom/partials/effort_form.html', {'form': EffortCreateForm}) 