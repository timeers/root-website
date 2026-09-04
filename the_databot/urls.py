from django.urls import path

from .discord_interactions import discord_interactions

# The path '/discord/interactions/' is pinned by two things outside this repo:
# the Interactions Endpoint URL registered in the Discord developer portal, and
# an Apache <Location /discord/interactions> block that gives the bot its own
# mod_wsgi process group. Renaming it here breaks both. The URL name
# 'discord-interactions' is likewise unnamespaced on purpose -- adding an
# app_name would change it to 'the_databot:discord-interactions' and break any
# existing reverse().
urlpatterns = [
    path('discord/interactions/', discord_interactions, name='discord-interactions'),
]
