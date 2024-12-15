import django_filters
from django.db.models import Q
from .models import Game
from the_keep.models import Faction, Deck, Map, Vagabond
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


    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        official_only = True
        if user and user.is_authenticated:
            if user.profile.weird:
                official_only = False
        # Filter for only Official content if not a member of Weird Root
        if official_only:
            self.filters['deck'].queryset = Deck.objects.filter(official=True)
            self.filters['map'].queryset = Map.objects.filter(official=True)
            self.filters['faction'].queryset = Faction.objects.filter(official=True)
            self.filters['vagabond'].queryset = Vagabond.objects.filter(official=True)
            # self.filters['landmarks'].queryset = Landmark.objects.filter(official=True)
            # self.filters['hirelings'].queryset = Hireling.objects.filter(official=True)


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
                )

        if selected_vagabonds:
            # Build the filter condition for all selected vagabonds
            for vagabond in selected_vagabonds:
                queryset = queryset.filter(
                    Q(efforts__vagabond=vagabond)  # Filter by any selected vagabond
                )

        if selected_players:
            # Build the filter condition for all selected players
            for player in selected_players:
                queryset = queryset.filter(
                    Q(efforts__player=player)  # Filter by any selected player
                )
        return super().filter_queryset(queryset)
    
