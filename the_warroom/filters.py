import django_filters
from django.db.models import Q, Count
from .models import Game
from the_keep.models import Faction, Deck, Map, Vagabond
from the_gatehouse.models import Profile
from django import forms

class GameFilter(django_filters.FilterSet):

    map = django_filters.ModelChoiceFilter(
        # queryset=Map.objects.all(),  # Set the queryset for the filter
        queryset=Map.objects.none(),
        empty_label='All',
    )
    deck = django_filters.ModelChoiceFilter(
        queryset=Deck.objects.none(),  # Set the queryset for the filter
        empty_label='All',
    )
    faction = django_filters.ModelMultipleChoiceFilter(
        queryset=Faction.objects.none(),  # Set the queryset for the filter
        field_name='efforts__faction',
        label = 'Factions',
    )
    vagabond = django_filters.ModelMultipleChoiceFilter(
        queryset=Vagabond.objects.none(),  # Set the queryset for the filter
        field_name='efforts__vagabond',
        label = 'Vagabonds',
    )
    player = django_filters.ModelMultipleChoiceFilter(
        queryset=Profile.objects.none(),  # Set the queryset for the filter
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
            # decks_qs = Deck.objects.filter(games__isnull=False, official=True).distinct()
            # maps_qs = Map.objects.filter(games__isnull=False, official=True).distinct()
            # factions_qs = Faction.objects.filter(efforts__isnull=False, official=True).distinct()
            # vagabonds_qs = Vagabond.objects.filter(efforts__isnull=False, official=True).distinct()
            # players_qs = Profile.objects.filter(efforts__isnull=False, official=True).distinct()

            decks_qs = Deck.objects.filter(official=True)
            maps_qs = Map.objects.filter(official=True)
            factions_qs = Faction.objects.filter(official=True)
            vagabonds_qs = Vagabond.objects.filter(official=True)
            players_qs = Profile.objects.filter(official=True)

        else:
            # decks_qs = Deck.objects.filter(games__isnull=False).distinct()
            # maps_qs = Map.objects.filter(games__isnull=False).distinct()
            # factions_qs = Faction.objects.filter(efforts__isnull=False).distinct()
            # vagabonds_qs = Vagabond.objects.filter(efforts__isnull=False).distinct()
            # players_qs = Profile.objects.filter(efforts__isnull=False).distinct()

            decks_qs = Deck.objects.all()
            maps_qs = Map.objects.all()
            factions_qs = Faction.objects.all()
            vagabonds_qs = Vagabond.objects.all()
            players_qs = Profile.objects.all()

        self.filters['faction'].queryset = factions_qs
        self.filters['vagabond'].queryset = vagabonds_qs
        self.filters['deck'].queryset = decks_qs
        self.filters['map'].queryset = maps_qs
        self.filters['player'].queryset = players_qs


    def filter_queryset(self, queryset):
        selected_factions = self.data.getlist('faction')
        selected_players = self.data.getlist('player')
        selected_vagabonds = self.data.getlist('vagabond')

        if selected_factions:
            queryset = queryset.filter(efforts__faction__in=selected_factions)
            queryset = queryset.annotate(
                matched_factions=Count('efforts__faction', filter=Q(efforts__faction__in=selected_factions), distinct=True)
            ).filter(matched_factions=len(selected_factions))

        if selected_vagabonds:
            queryset = queryset.filter(efforts__vagabond__in=selected_vagabonds)
            queryset = queryset.annotate(
                matched_vagabonds=Count('efforts__vagabond', filter=Q(efforts__vagabond__in=selected_vagabonds), distinct=True)
            ).filter(matched_vagabonds=len(selected_vagabonds))

        if selected_players:
            queryset = queryset.filter(efforts__player__in=selected_players)
            queryset = queryset.annotate(
                matched_players=Count('efforts__player', filter=Q(efforts__player__in=selected_players), distinct=True)
            ).filter(matched_players=len(selected_players))

        return super().filter_queryset(queryset.distinct())

    

class PlayerGameFilter(django_filters.FilterSet):

    map = django_filters.ModelChoiceFilter(
        # queryset=Map.objects.all(),  # Set the queryset for the filter
        queryset=Map.objects.none(),
        empty_label='All',
    )
    deck = django_filters.ModelChoiceFilter(
        queryset=Deck.objects.none(),  # Set the queryset for the filter
        empty_label='All',
    )
    faction = django_filters.ModelChoiceFilter(
        queryset=Faction.objects.none(),  # Set the queryset for the filter
        field_name='efforts__faction',
        label = 'Faction',
    )
    factions = django_filters.ModelMultipleChoiceFilter(
        queryset=Faction.objects.none(),  # Set the queryset for the filter
        field_name='efforts__faction',
        label = 'Factions',
    )
    vagabonds = django_filters.ModelMultipleChoiceFilter(
        queryset=Vagabond.objects.none(),  # Set the queryset for the filter
        field_name='efforts__vagabond',
        label = 'Vagabonds',
    )
    players = django_filters.ModelMultipleChoiceFilter(
        queryset=Profile.objects.none(),  # Set the queryset for the filter
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

        if player:
            # Build querysets once
            faction_qs = Faction.objects.filter(
                efforts__player=player
            ).distinct().order_by('title')

            factions_qs = Faction.objects.filter(
                efforts__game__efforts__player=player
            ).distinct().order_by('title')

            vagabonds_qs = Vagabond.objects.filter(
                efforts__game__efforts__player=player
            ).distinct().order_by('title')

            decks_qs = Deck.objects.filter(
                games__efforts__player=player
            ).distinct().order_by('title')

            maps_qs = Map.objects.filter(
                games__efforts__player=player
            ).distinct().order_by('title')

            players_qs = Profile.objects.filter(
                efforts__game__efforts__player=player
            ).distinct().order_by('discord')

            # Assign them to the filters
            self.filters['faction'].queryset = faction_qs
            self.filters['factions'].queryset = factions_qs
            self.filters['vagabonds'].queryset = vagabonds_qs
            self.filters['deck'].queryset = decks_qs
            self.filters['map'].queryset = maps_qs
            self.filters['players'].queryset = players_qs



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
            queryset = queryset.filter(efforts__faction__in=selected_factions)
            queryset = queryset.annotate(
                matched_factions=Count(
                    'efforts__faction',
                    filter=Q(efforts__faction__in=selected_factions),
                    distinct=True
                )
            ).filter(matched_factions=len(selected_factions))

        if selected_vagabonds:
            queryset = queryset.filter(efforts__vagabond__in=selected_vagabonds)
            queryset = queryset.annotate(
                matched_vagabonds=Count(
                    'efforts__vagabond',
                    filter=Q(efforts__vagabond__in=selected_vagabonds),
                    distinct=True
                )
            ).filter(matched_vagabonds=len(selected_vagabonds))

        if selected_players:
            queryset = queryset.filter(efforts__player__in=selected_players)
            queryset = queryset.annotate(
                matched_players=Count(
                    'efforts__player',
                    filter=Q(efforts__player__in=selected_players),
                    distinct=True
                )
            ).filter(matched_players=len(selected_players))

        return super().filter_queryset(queryset.distinct())