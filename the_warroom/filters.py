import django_filters
from django.db.models import Q, Count
from .models import (Game, effort_counts_for_round_q, effort_counts_for_stage_q,
                     effort_counts_for_tournament_q)
from the_keep.models import Faction, Deck, Map, Vagabond
from the_gatehouse.models import Profile
from django import forms


class BaseGameFilter(django_filters.FilterSet):
    """Base filter with shared fields and multi-select logic for all game filter views."""

    map = django_filters.ModelChoiceFilter(
        queryset=Map.objects.none(),
        empty_label='All',
    )
    deck = django_filters.ModelChoiceFilter(
        queryset=Deck.objects.none(),
        empty_label='All',
    )
    factions = django_filters.ModelMultipleChoiceFilter(
        queryset=Faction.objects.none(),
        field_name='efforts__faction',
        label='Factions',
    )
    vagabonds = django_filters.ModelMultipleChoiceFilter(
        queryset=Vagabond.objects.none(),
        field_name='efforts__vagabond',
        label='Vagabonds',
    )
    players = django_filters.ModelMultipleChoiceFilter(
        queryset=Profile.objects.none(),
        field_name='efforts__player',
        label='Players',
    )
    date_after = django_filters.DateFilter(
        field_name='date_posted',
        lookup_expr='gte',
        label='From',
        widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
    )
    date_before = django_filters.DateFilter(
        field_name='date_posted',
        lookup_expr='lte',
        label='To',
        widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
    )
    official = django_filters.BooleanFilter(
        label='Display Games',
        widget=forms.Select(choices=[
            ('', 'All Games'),
            ('true', 'Games with only Official Content'),
            ('false', 'Games with Fan Content'),
        ])
    )

    class Meta:
        model = Game
        fields = ['factions', 'vagabonds', 'map', 'deck', 'players', 'date_after', 'date_before', 'official']

    def _apply_multi_filter(self, queryset, param_name, field_path):
        selected = self.data.getlist(param_name)
        if selected:
            queryset = queryset.filter(**{f'{field_path}__in': selected})
            queryset = queryset.annotate(**{
                f'matched_{param_name}': Count(
                    field_path,
                    filter=Q(**{f'{field_path}__in': selected}),
                    distinct=True
                )
            }).filter(**{f'matched_{param_name}': len(selected)})
        return queryset

    def filter_queryset(self, queryset):
        queryset = self._apply_multi_filter(queryset, 'factions', 'efforts__faction')
        queryset = self._apply_multi_filter(queryset, 'vagabonds', 'efforts__vagabond')
        queryset = self._apply_multi_filter(queryset, 'players', 'efforts__player')
        return super().filter_queryset(queryset.distinct())


class GameFilter(BaseGameFilter):

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)

        official_only = False
        if user and user.is_authenticated:
            if not user.profile.weird:
                official_only = True

        if official_only:
            decks_qs = Deck.objects.filter(official=True)
            maps_qs = Map.objects.filter(official=True)
            factions_qs = Faction.objects.filter(official=True)
            vagabonds_qs = Vagabond.objects.filter(official=True)
            players_qs = Profile.objects.filter(official=True)
        else:
            decks_qs = Deck.objects.all()
            maps_qs = Map.objects.all()
            factions_qs = Faction.objects.all()
            vagabonds_qs = Vagabond.objects.all()
            players_qs = Profile.objects.all()

        self.filters['factions'].queryset = factions_qs
        self.filters['vagabonds'].queryset = vagabonds_qs
        self.filters['deck'].queryset = decks_qs
        self.filters['map'].queryset = maps_qs
        self.filters['players'].queryset = players_qs


class PlayerGameFilter(BaseGameFilter):

    faction = django_filters.ModelChoiceFilter(
        queryset=Faction.objects.none(),
        field_name='efforts__faction',
        label='Faction',
    )

    class Meta(BaseGameFilter.Meta):
        fields = BaseGameFilter.Meta.fields + ['faction']

    def __init__(self, *args, player=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._player = player

        if player:
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

            self.filters['faction'].queryset = faction_qs
            self.filters['factions'].queryset = factions_qs
            self.filters['vagabonds'].queryset = vagabonds_qs
            self.filters['deck'].queryset = decks_qs
            self.filters['map'].queryset = maps_qs
            self.filters['players'].queryset = players_qs

    def filter_queryset(self, queryset):
        queryset = queryset.prefetch_related('efforts')
        selected_faction = self.data.get('faction')
        player = self._player

        if player:
            if selected_faction:
                queryset = queryset.filter(
                    Q(efforts__faction=selected_faction, efforts__player=player)
                )
            else:
                queryset = queryset.filter(efforts__player=player)

        return super().filter_queryset(queryset)


class TournamentGameFilter(BaseGameFilter):

    def __init__(self, *args, tournament=None, stage=None, round=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._tournament = tournament
        self._stage = stage
        self._round = round

        if round:
            game_filter = effort_counts_for_round_q(round, prefix='games') & Q(games__final=True)
            effort_filter = effort_counts_for_round_q(round, prefix='efforts__game') & Q(efforts__game__final=True)
        elif stage:
            game_filter = effort_counts_for_stage_q(stage, prefix='games') & Q(games__final=True)
            effort_filter = effort_counts_for_stage_q(stage, prefix='efforts__game') & Q(efforts__game__final=True)
        elif tournament:
            game_filter = effort_counts_for_tournament_q(tournament, prefix='games') & Q(games__final=True)
            effort_filter = effort_counts_for_tournament_q(tournament, prefix='efforts__game') & Q(efforts__game__final=True)
        else:
            return

        self.filters['factions'].queryset = Faction.objects.filter(effort_filter).distinct().order_by('title')
        self.filters['vagabonds'].queryset = Vagabond.objects.filter(effort_filter).distinct().order_by('title')
        self.filters['deck'].queryset = Deck.objects.filter(game_filter).distinct().order_by('title')
        self.filters['map'].queryset = Map.objects.filter(game_filter).distinct().order_by('title')
        self.filters['players'].queryset = Profile.objects.filter(effort_filter).distinct().order_by('display_name')
