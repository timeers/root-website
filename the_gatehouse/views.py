from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.http import HttpResponseForbidden
from django.contrib.auth.decorators import login_required
from django.utils.decorators import method_decorator
from django.views.generic import DetailView, ListView
from django.contrib.auth.mixins import LoginRequiredMixin
from functools import wraps
from .forms import UserRegisterForm, UserUpdateForm, ProfileUpdateForm
from .models import Profile

def register(request):
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
def profile(request):
    if request.method == 'POST':
        u_form = UserUpdateForm(request.POST, instance=request.user)
        p_form = ProfileUpdateForm(request.POST, 
                                   request.FILES, 
                                   instance=request.user.profile)
        if u_form.is_valid() and p_form.is_valid():
            u_form.save()
            p_form.save()
            messages.success(request, f'Account updated!')
            return redirect('profile')
        
    else:
        u_form = UserUpdateForm(instance=request.user)
        p_form = ProfileUpdateForm(instance=request.user.profile)

    context = {
        'u_form': u_form, 
        'p_form': p_form
    }

    return render(request, 'the_gatehouse/profile.html', context)


class PlayerDetailView(LoginRequiredMixin, DetailView):
    model = Profile

@login_required
def player_page_view(request, slug):
    player = get_object_or_404(Profile, slug=slug)

    efforts = player.efforts.all()
    games = list({effort.game for effort in efforts})
    games.sort(key=lambda game: game.date_posted, reverse=True)

    return render(request, 'the_gatehouse/profile_detail.html', {'games': games, 'player': player})


# Decorator
def creative_required(view_func):
    @login_required  # Ensure the user is authenticated
    @wraps(view_func)  # Preserve the original function's metadata
    def wrapper(request, *args, **kwargs):
        if request.user.profile.creative:
            return view_func(request, *args, **kwargs)  # Continue to the view
        else:
            return HttpResponseForbidden()  # 403 Forbidden
    return wrapper

def creative_required_class_based_view(view_class):
    """Decorator to apply to class-based views."""
    view_class.dispatch = method_decorator(creative_required)(view_class.dispatch)
    return view_class