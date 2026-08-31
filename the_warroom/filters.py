import django_filters
from django.db.models import Q, Count
from .models import Game, game_counts_for_elo_system_q, elo_eligibility_q
from the_keep.models import Faction, Deck, Map, Vagabond
from the_gatehouse.models import Profile
from django import forms


def _prefixed(q, prefix):
    """Re-root a Q written against Game so it applies from a related model.

    NOTE: currently unused. Its only caller was EloSystemGameFilter, which now scopes at
    the Game level (no prefixing needed) via _scoped_option_querysets. Kept because it is
    the natural helper if another filter ever needs to push a Game-level predicate down a
    relation; delete it if that never materialises.

    Filtering Faction/Deck/Profile by properties of their games needs the same
    predicate under a relation prefix ('games__final=True' rather than
    'final=True'). Rewriting the keys keeps one definition of the predicate
    instead of a hand-maintained copy per prefix.
    """
    children = []
    for child in q.children:
        if isinstance(child, Q):
            children.append(_prefixed(child, prefix))
        else:
            key, value = child
            children.append((f'{prefix}__{key}', value))
    clone = Q()
    clone.connector = q.connector
    clone.negated = q.negated
    clone.children = children
    return clone


# Columns a <select> option actually needs. The filter template renders plain
# `{{ form.field }}`, so only the label (__str__) and the pk are read.
#
# This matters far more than it looks: the scoped dropdowns below are SELECT DISTINCT
# over a multi-table join, and DISTINCT compares EVERY selected column. Faction is
# multi-table inheritance, so an unrestricted select is 77 columns across
# the_keep_faction + the_keep_post -- de-duplicated across every row of an 8-way join
# spanning the whole series, to return ~10 rows. Narrowing the projection is what makes
# these cheap; the extra fields beyond `title` are kept only so a future template tweak
# (icon, colour) doesn't silently trigger a per-option deferred-field reload.
_POST_OPTION_FIELDS = ('title', 'slug', 'component', 'official', 'color', 'picture')
_FACTION_OPTION_FIELDS = _POST_OPTION_FIELDS + ('small_icon', 'small_icon_version')
_PROFILE_OPTION_FIELDS = ('display_name', 'discord', 'slug', 'image')


def _scoped_option_querysets(games):
    """Distinct factions/vagabonds/decks/maps/players appearing in `games`.

    One pass over the scoped Efforts for the three effort-level FKs and one over the
    scoped Games for deck/map, instead of five independent OR-joined DISTINCT queries
    (one per dropdown). The per-model queries that follow are plain indexed pk__in
    lookups with no join at all.

    `games` must already be de-duplicated (the counting_for_* helpers apply .distinct()),
    since membership spans the round/extra_rounds OR.

    Returns {field_name: queryset} for the five shared dropdowns.
    """
    from .models import Effort

    faction_ids, vagabond_ids, player_ids = set(), set(), set()
    for faction_id, vagabond_id, player_id in (
        Effort.objects.filter(game__in=games)
        .values_list('faction_id', 'vagabond_id', 'player_id').distinct()
    ):
        # NULLs are dropped, matching the inner-join semantics of the queries this
        # replaces (Faction.objects.filter(efforts__...) never yields a null row).
        if faction_id:
            faction_ids.add(faction_id)
        if vagabond_id:
            vagabond_ids.add(vagabond_id)
        if player_id:
            player_ids.add(player_id)

    deck_ids, map_ids = set(), set()
    for deck_id, map_id in games.values_list('deck_id', 'map_id').distinct():
        if deck_id:
            deck_ids.add(deck_id)
        if map_id:
            map_ids.add(map_id)

    return {
        'factions': Faction.objects.filter(pk__in=faction_ids)
                    .only(*_FACTION_OPTION_FIELDS).order_by('title'),
        'vagabonds': Vagabond.objects.filter(pk__in=vagabond_ids)
                     .only(*_FACTION_OPTION_FIELDS).order_by('title'),
        'deck': Deck.objects.filter(pk__in=deck_ids)
                .only(*_POST_OPTION_FIELDS).order_by('title'),
        'map': Map.objects.filter(pk__in=map_ids)
               .only(*_POST_OPTION_FIELDS).order_by('title'),
        'players': Profile.objects.filter(pk__in=player_ids)
                   .only(*_PROFILE_OPTION_FIELDS).order_by('display_name'),
    }


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
            # Only the projection is narrowed here (see _POST_OPTION_FIELDS): these are
            # DISTINCT over a join, so selecting all ~60-90 columns is what costs.
            #
            # The query STRUCTURE is deliberately left alone -- unlike the tournament and
            # elo filters, this class cannot use _scoped_option_querysets(). It has two
            # faction fields with different meanings ('faction' = what this player played
            # as, 'factions' = every faction in their games), which one effort pass cannot
            # produce, and it applies no final=True predicate.
            faction_qs = Faction.objects.filter(
                efforts__player=player
            ).distinct().only(*_FACTION_OPTION_FIELDS).order_by('title')

            factions_qs = Faction.objects.filter(
                efforts__game__efforts__player=player
            ).distinct().only(*_FACTION_OPTION_FIELDS).order_by('title')

            vagabonds_qs = Vagabond.objects.filter(
                efforts__game__efforts__player=player
            ).distinct().only(*_FACTION_OPTION_FIELDS).order_by('title')

            decks_qs = Deck.objects.filter(
                games__efforts__player=player
            ).distinct().only(*_POST_OPTION_FIELDS).order_by('title')

            maps_qs = Map.objects.filter(
                games__efforts__player=player
            ).distinct().only(*_POST_OPTION_FIELDS).order_by('title')

            players_qs = Profile.objects.filter(
                efforts__game__efforts__player=player
            ).distinct().only(*_PROFILE_OPTION_FIELDS).order_by('discord')

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

        # Resolve the in-scope games once, then derive every dropdown from them. Each
        # dropdown used to carry the round/extra_rounds OR itself, so rendering the form
        # ran that M2M-spanning OR + a DISTINCT over ~60-90 columns five separate times.
        # On the largest production series that made the form render take 7.4s -- the
        # bulk of the page, and several times the leaderboard aggregates beside it.
        if round:
            games = Game.objects.counting_for_round(round)
        elif stage:
            games = Game.objects.counting_for_stage(stage)
        elif tournament:
            games = Game.objects.counting_for_tournament(tournament)
        else:
            return

        for name, queryset in _scoped_option_querysets(games.filter(final=True)).items():
            self.filters[name].queryset = queryset


class EloSystemGameFilter(BaseGameFilter):
    """Dropdowns scoped to the games one EloSystem rates.

    Mirrors TournamentGameFilter: the elo_system kwarg only narrows the choice
    querysets, it does NOT filter the games themselves -- the view already passes
    an elo-scoped queryset in.

    Unlike a tournament, a system has player-count bounds, so the scoping mirrors
    the full eligibility predicate (final, non-test, in bounds). Without the bounds
    an option could appear in a dropdown and then match zero games.
    """

    def __init__(self, *args, elo_system=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._elo_system = elo_system

        if not elo_system:
            return

        # Resolve the rated games once, then derive every dropdown from them -- same
        # motivation as TournamentGameFilter (see _scoped_option_querysets).
        #
        # Both predicates are applied at the GAME level here. elo_eligibility_q is
        # already game-level (final, non-test, player count in bounds), and the
        # membership Q must be the game_counts_ variant: effort_counts_for_elo_system_q
        # is pre-prefixed with 'game__' and raises FieldError against Game.
        games = Game.objects.filter(
            game_counts_for_elo_system_q(elo_system),
            elo_eligibility_q(elo_system),
        ).distinct()

        for name, queryset in _scoped_option_querysets(games).items():
            self.filters[name].queryset = queryset
