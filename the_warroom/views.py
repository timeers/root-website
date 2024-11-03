from django.shortcuts import render
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.decorators import login_required
from django.contrib.admin.views.decorators import staff_member_required
from django.views.generic import CreateView, ListView
from django.shortcuts import get_object_or_404, redirect
from django.forms.models import modelformset_factory
from django.http import HttpResponse, Http404
from django.core.exceptions import ObjectDoesNotExist
from django.urls import reverse
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
    hx_url = reverse("game-hx-detail", kwargs={"id": id})
    try:
        obj = Game.objects.get(id=id)
    except ObjectDoesNotExist:
        obj = None
    context=  {
        'hx_url': hx_url,
        'game': obj
    }
    return render(request, "the_warroom/game_detail.html", context)

@staff_member_required
def game_delete_view(request, id=None):
    try:
        obj = Game.objects.get(id=id)
    except:
        obj = None

    if obj is None:
        if request.htmx:
            return HttpResponse("Not Found")
        raise Http404
    if request.method == "POST":
        obj.delete()
        success_url = reverse('games-home')
        if request.htmx:
            headers = {
                'HX-Redirect': success_url,
            }
            return HttpResponse("Success", headers=headers)
        return redirect(success_url)
    context=  {
        'game': obj
    }
    return render(request, "the_warroom/game_delete.html", context)

@staff_member_required
def game_effort_delete_view(request, parent_id=None, id=None):
    try:
        obj = Effort.objects.get(game__id=parent_id, id=id)
    except:
        obj = None

    if obj is None:
        if request.htmx:
            return HttpResponse("Not Found")
        raise Http404
    if request.method == "POST":
        obj.delete()
        success_url = reverse('games-detail', kwargs={'id': parent_id})
        if request.htmx:
            headers = {
                'HX-Redirect': success_url,
            }
            return HttpResponse("Success", headers=headers)
        return redirect(success_url)
    context=  {
        'object': obj
    }
    return render(request, "the_warroom/game_delete.html", context)

@login_required
def game_detail_hx_view(request, id=None):
    if not request.htmx:
        raise Http404
    try:
        obj = Game.objects.get(id=id)
    except:
        obj = None
    if obj is None:
        return HttpResponse('Game Not Found')
    context=  {
        'game': obj
    }
    return render(request, "the_warroom/partials/game_detail.html", context)

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

@staff_member_required
def update_game(request, id=None):
    obj = get_object_or_404(Game, id=id)
    # obj = get_object_or_404(Game, id=id, recorder=request.user.profile)
    form = GameCreateForm(request.POST or None, instance=obj) 
    # Formset = modelformset_factory(Model, form=ModelForm, extra=0)
    EffortFormset = modelformset_factory(Effort, form=EffortCreateForm, extra=0)
    qs = obj.effort_set.all()
    formset = EffortFormset(request.POST or None, queryset=qs)
    context = {'form': form, 
               'formset': formset,
               'object': obj,
               }

    if  all([form.is_valid(), formset.is_valid()]):
        parent = form.save(commit=False)
        parent.save()
        for form in formset:
            child = form.save(commit=False)
            child.game = parent
            child.save()

        context['message'] = "Game Saved"
    if request.htmx:
        return render(request, 'the_warroom/partials/forms.html', context)
    return render(request, 'the_warroom/record_game.html', context)


# Never used
# @login_required
# def record_effort(request):
#     if request.method == 'POST':
#         form = EffortCreateForm(request.POST or None)
#         if form.is_valid():
#             effort = form.save()
#             context = {'effort': effort}
#             return render(request, 'the_warroom/partials/effort.html', context)
#     return render(request, 'the_warroom/partials/effort_form.html', {'form': EffortCreateForm}) 