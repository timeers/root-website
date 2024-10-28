from django.urls import path
# from .views import 
from . import views

urlpatterns = [
    path('record-game/', views.record_game, name='record_game'),
    path('create-form/', views.record_effort, name='create-player'),
]
