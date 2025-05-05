import django_filters
from django.db.models import Q, Count
from .models import Game
from the_keep.models import Faction, Deck, Map, Vagabond
from the_gatehouse.models import Profile
from django import forms

class GameFilter(django_filters.FilterSet):

    map = django_filters.ModelChoiceFilter(
        # queryset=Map.objects.all(),  # Set the queryset for the filter
        queryset=Map.objects.annotate(games_count=Count('games')).filter(games_count__gt=0),
        empty_label='All',
    )
    deck = django_filters.ModelChoiceFilter(
        queryset=Deck.objects.annotate(games_count=Count('games')).filter(games_count__gt=0),  # Set the queryset for the filter
        empty_label='All',
    )
    faction = django_filters.ModelMultipleChoiceFilter(
        queryset=Faction.objects.annotate(efforts_count=Count('efforts')).filter(efforts_count__gt=0),  # Set the queryset for the filter
        field_name='efforts__faction',
        label = 'Factions',
    )
    vagabond = django_filters.ModelMultipleChoiceFilter(
        queryset=Vagabond.objects.annotate(efforts_count=Count('efforts')).filter(efforts_count__gt=0),  # Set the queryset for the filter
        field_name='efforts__vagabond',
        label = 'Vagabonds',
    )
    player = django_filters.ModelMultipleChoiceFilter(
        queryset=Profile.objects.annotate(efforts_count=Count('efforts')).filter(efforts_count__gt=0),  # Set the queryset for the filter
        field_name='efforts__player',
        label = 'Players',
    )
    official = django_filters.BooleanFilter(
        label='Display Games',
        widget=forms.Select(choices=[
            ('', 'All Games'),    # acts as "unknown" (no filter)
            ('true', 'Games with only Official Content'),
            ('false', 'Games with Fan Content'),
        ])
    )
    class Meta:
        model = Game
        fields = ['faction', 'vagabond', 'map', 'deck', 'player', 'official']


    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        official_only = False
        if user and user.is_authenticated:
            if not user.profile.weird:
                official_only = True
        # print(f'Official Only: {official_only}')
        # Filter for only Official content if fan content is deselected
        if official_only:
            # self.filters['deck'].queryset = Deck.objects.filter(official=True)
            # self.filters['map'].queryset = Map.objects.filter(official=True)
            # self.filters['faction'].queryset = Faction.objects.filter(official=True)
            # self.filters['vagabond'].queryset = Vagabond.objects.filter(official=True)

            self.filters['deck'].queryset = Deck.objects.annotate(games_count=Count('games')).filter(games_count__gt=0, official=True)
            self.filters['map'].queryset=Map.objects.annotate(games_count=Count('games')).filter(games_count__gt=0, official=True)
            self.filters['faction'].queryset = Faction.objects.annotate(efforts_count=Count('efforts')).filter(efforts_count__gt=0, official=True)
            self.filters['vagabond'].queryset = Vagabond.objects.annotate(efforts_count=Count('efforts')).filter(efforts_count__gt=0, official=True)
            self.filters['player'].queryset = Profile.objects.annotate(efforts_count=Count('efforts')).filter(efforts_count__gt=0)



            # self.filters['landmarks'].queryset = Landmark.objects.filter(official=True)
            # self.filters['hirelings'].queryset = Hireling.objects.filter(official=True)



    def filter_queryset(self, queryset):
        # Get the selected factions
        queryset = queryset.prefetch_related('efforts')
        selected_factions = self.data.getlist('faction')
        selected_players = self.data.getlist('player')
        selected_vagabonds = self.data.getlist('vagabond')
        # official_only = self.form.cleaned_data.get('official')
        # print(official_only)
        # print(selected_factions)
        # print(selected_players)
        # print(selected_vagabonds)

        # filter_conditions = Q()

        # if selected_factions:
        #     filter_conditions &= Q(efforts__faction__in=selected_factions)

        # if selected_vagabonds:
        #     filter_conditions &= Q(efforts__vagabond__in=selected_vagabonds)

        # if selected_players:
        #     filter_conditions &= Q(efforts__player__in=selected_players)

        # # Apply the combined filter conditions
        # queryset = queryset.filter(filter_conditions).distinct()


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
    

class PlayerGameFilter(django_filters.FilterSet):

    map = django_filters.ModelChoiceFilter(
        # queryset=Map.objects.all(),  # Set the queryset for the filter
        queryset=Map.objects.annotate(games_count=Count('games')).filter(games_count__gt=0),
        empty_label='All',
    )
    deck = django_filters.ModelChoiceFilter(
        queryset=Deck.objects.annotate(games_count=Count('games')).filter(games_count__gt=0),  # Set the queryset for the filter
        empty_label='All',
    )
    faction = django_filters.ModelChoiceFilter(
        queryset=Faction.objects.annotate(efforts_count=Count('efforts')).filter(efforts_count__gt=0),  # Set the queryset for the filter
        field_name='efforts__faction',
        label = 'Faction',
    )
    factions = django_filters.ModelMultipleChoiceFilter(
        queryset=Faction.objects.annotate(efforts_count=Count('efforts')).filter(efforts_count__gt=0),  # Set the queryset for the filter
        field_name='efforts__faction',
        label = 'Factions',
    )
    vagabonds = django_filters.ModelMultipleChoiceFilter(
        queryset=Vagabond.objects.annotate(efforts_count=Count('efforts')).filter(efforts_count__gt=0),  # Set the queryset for the filter
        field_name='efforts__vagabond',
        label = 'Vagabonds',
    )
    players = django_filters.ModelMultipleChoiceFilter(
        queryset=Profile.objects.annotate(efforts_count=Count('efforts')).filter(efforts_count__gt=0),  # Set the queryset for the filter
        field_name='efforts__player',
        label = 'Players',
    )
    official = django_filters.BooleanFilter(
        label='Display Games',
        widget=forms.Select(choices=[
            ('', 'All Games'),    # acts as "unknown" (no filter)
            ('true', 'Games with only Official Content'),
            ('false', 'Games with Fan Content'),
        ])
    )
    class Meta:
        model = Game
        fields = ['faction', 'factions', 'vagabonds', 'map', 'deck','players', 'official']


    def __init__(self, *args, player=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._player = player

        self.filters['faction'].queryset = Faction.objects.annotate(efforts_count=Count('efforts')).filter(efforts_count__gt=0, efforts__player=player)
        self.filters['deck'].queryset = Deck.objects.annotate(games_count=Count('games')).filter(games_count__gt=0, games__efforts__player=player)
        self.filters['map'].queryset=Map.objects.annotate(games_count=Count('games')).filter(games_count__gt=0, games__efforts__player=player)
        self.filters['factions'].queryset = Faction.objects.annotate(efforts_count=Count('efforts')).filter(efforts_count__gt=0, efforts__game__efforts__player=player)
        self.filters['vagabonds'].queryset = Vagabond.objects.annotate(efforts_count=Count('efforts')).filter(efforts_count__gt=0, efforts__game__efforts__player=player)
        self.filters['players'].queryset = Profile.objects.annotate(efforts_count=Count('efforts')).filter(efforts_count__gt=0, efforts__game__efforts__player=player)

    def filter_queryset(self, queryset):
        # Get the selected factions
        queryset = queryset.prefetch_related('efforts')
        selected_faction = self.data.get('faction')
        selected_factions = self.data.getlist('factions')
        selected_players = self.data.getlist('players')
        selected_vagabonds = self.data.getlist('vagabonds')

        # Get the player from the filter initialization
        player = self._player  # `self._user` should be set in `__init__`

        # If a player is passed, filter only the games that the player is involved in
        if player:
            
            if selected_faction:
                queryset = queryset.filter(
                        Q(efforts__faction=selected_faction, efforts__player=player)  # Filter by any selected faction
                    )
            else:
                queryset = queryset.filter(efforts__player=player)

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