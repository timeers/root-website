"""
URL configuration for django_project project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.1/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from the_gatehouse import views as user_views
from the_tavern import views as comment_views
from the_keep.views import list_view, search_view
from the_keep.api_views import get_options_for_platform
from the_warroom.api_views import get_options_for_tournament
from the_gatehouse.views import bookmark_player, onboard_user, onboard_decline
# from debug_toolbar.toolbar import debug_toolbar_urls


urlpatterns = [
    path('woodland-admin/', admin.site.urls),
    path('api/platform/<str:platform>/', get_options_for_platform, name='get_options_for_platform'),
    path('api/tournament/<pk>/', get_options_for_tournament, name='get_options_for_tournament'),
    # path('register/', user_views.register, name='register'),
    # path('player/<slug:slug>/', user_views.PlayerDetailView.as_view(), name='player-detail'),

    # path('login/', auth_views.LoginView.as_view(template_name='the_gatehouse/login.html'), name='login'),
    # path('logout/', auth_views.LogoutView.as_view(template_name='the_gatehouse/logout.html'), name='logout'),

    path('password-reset/', 
         auth_views.PasswordResetView.as_view(template_name='the_gatehouse/password_reset.html'), 
         name='password_reset'),
    path('password-reset/done/', 
         auth_views.PasswordResetDoneView.as_view(template_name='the_gatehouse/password_reset_done.html'), 
         name='password_reset_done'),
    path('password-reset-confirm/<uidb64>/<token>/', 
         auth_views.PasswordResetConfirmView.as_view(template_name='the_gatehouse/password_reset_confirm.html'), 
         name='password_reset_confirm'),
    path('password-reset-complete/', 
         auth_views.PasswordResetCompleteView.as_view(template_name='the_gatehouse/password_reset_complete.html'), 
         name='password_reset_complete'),
         

    path('hx/gamecommentsent/<pk>/', comment_views.game_comment_sent, name='game-comment-sent'),
    path('hx/comment/game/delete/<pk>/', comment_views.game_comment_delete, name='game-comment-delete'),
    path('hx/postcommentsent/<pk>/', comment_views.post_comment_sent, name='post-comment-sent'),
    path('hx/comment/post/delete/<pk>/', comment_views.post_comment_delete, name='post-comment-delete'),
    path('hx/add-player/', user_views.add_player,name='add-discord-player'),
    path("hx/profile/<int:id>/bookmark/", bookmark_player, name='bookmark-player'), 

    path('onboard/', onboard_user, name='onboard-user'),
    path('onboard/reject/', onboard_decline, name='onboard-decline'),
    path('onboard/<str:user_type>/', onboard_user, name='onboard-user'),
    path('', include('the_keep.urls')),
    path('profile/', include('the_gatehouse.urls')),
    path('', include('the_warroom.urls')),
    path('accounts/', include('allauth.urls')),


] #+ debug_toolbar_urls()
# Debug toolbar was not working

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
