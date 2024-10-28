from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from .models import Post, Map, Deck, Landmark, Vagabond, Hireling, Faction, Expansion
from django.contrib.auth.models import User


admin.site.register(Post)
admin.site.register(Map)
admin.site.register(Deck)
admin.site.register(Landmark)
admin.site.register(Vagabond)
admin.site.register(Hireling)
admin.site.register(Faction)
admin.site.register(Expansion)