import django_filters
from django.db.models import Q
from .models import Game
from blog.models import Faction, Deck, Map, Vagabond
from the_gatehouse.models import Profile

class GameFilter(django_filters.FilterSet):

    map = django_filters.ModelChoiceFilter(
        queryset=Map.objects.all(),  # Set the queryset for the filter
        empty_label='All',
    )
    deck = django_filters.ModelChoiceFilter(
        queryset=Deck.objects.all(),  # Set the queryset for the filter
        empty_label='All',
    )
    faction = django_filters.ModelMultipleChoiceFilter(
        queryset=Faction.objects.all(),  # Set the queryset for the filter
        field_name='efforts__faction',
        label = 'Factions',
    )
    vagabond = django_filters.ModelMultipleChoiceFilter(
        queryset=Vagabond.objects.all(),  # Set the queryset for the filter
        field_name='efforts__vagabond',
        label = 'Vagabonds',
    )
    player = django_filters.ModelMultipleChoiceFilter(
        queryset=Profile.objects.all(),  # Set the queryset for the filter
        field_name='efforts__player',
        label = 'Players',
    )
    class Meta:
        model = Game
        fields = ['map', 'deck', 'faction', 'player', 'vagabond']

    def filter_queryset(self, queryset):
        # Get the selected factions
        selected_factions = self.data.getlist('faction')
        selected_players = self.data.getlist('player')
        selected_vagabonds = self.data.getlist('vagabond')

        if selected_factions:
            # Build the filter condition for all selected factions
            for faction in selected_factions:
                queryset = queryset.filter(
                    Q(efforts__faction=faction)  # Filter by any selected faction
                ).distinct()

        if selected_vagabonds:
            # Build the filter condition for all selected vagabonds
            for vagabond in selected_vagabonds:
                queryset = queryset.filter(
                    Q(efforts__vagabond=vagabond)  # Filter by any selected vagabond
                ).distinct()

        if selected_players:
            # Build the filter condition for all selected players
            for player in selected_players:
                queryset = queryset.filter(
                    Q(efforts__player=player)  # Filter by any selected player
                ).distinct()
        return super().filter_queryset(queryset)