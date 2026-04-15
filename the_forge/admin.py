from django.contrib import admin
from .models import CardboardTrack, CardboardSlot


class CardboardSlotInline(admin.TabularInline):
    model = CardboardSlot
    extra = 1
    fields = ('number', 'row', 'column', 'row_title', 'content', 'background_image')
    ordering = ('row', 'column')


@admin.register(CardboardTrack)
class CardboardTrackAdmin(admin.ModelAdmin):
    inlines = [CardboardSlotInline]
    list_display = ('title', 'step', 'type', 'num_columns', 'order')
    list_filter = ('type',)
