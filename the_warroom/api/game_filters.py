import django_filters
from django.db.models import Count, Q
from crispy_forms.helper import FormHelper
from crispy_forms.layout import Layout, HTML

from the_warroom.models import Game, Tournament
from the_keep.models import Faction, Vagabond, Landmark, Hireling, Deck, Map
from the_gatehouse.models import Profile


class GameFilter(django_filters.FilterSet):
    """Query-param filters for the game download API.

    Single-value filters (map, deck, tournament, dates, official) match one value each.

    Multi-value filters (factions, vagabonds, players, landmarks, hirelings) accept the
    param repeated several times, e.g. ``?factions=cats&factions=birds``, and return only
    games that contain **all** of the selected values (AND). They render as multi-select
    boxes on the DRF browsable API filter form.

    NB: these use a grouped-count subquery (filter to the selected values, then require the
    distinct match count to equal the number selected) rather than ``conjoined=True``.
    Conjoined adds one JOIN per selected value, and because each asset is a multi-table
    inheritance child of Post that means 3 joins per value -- selecting 3 factions produced
    a 9-way join that took ~10s. The grouped count is a single join + aggregation regardless
    of how many values are selected. Mirrors ``BaseGameFilter`` in the_warroom/filters.py.

    To add a filter: declare an attribute here and list its name in ``Meta.fields``. The
    attribute name is the URL query param; ``field_name`` is the ORM lookup path.
    """

    # Single-value filters (dropdowns). The URL param accepts the slug/name (to_field_name);
    # field_name targets the relation so the resolved instance is matched directly.
    map = django_filters.ModelChoiceFilter(
        field_name='map', to_field_name='slug',
        queryset=Map.objects.all(), label='Map',
    )
    deck = django_filters.ModelChoiceFilter(
        field_name='deck', to_field_name='slug',
        queryset=Deck.objects.all(), label='Deck',
    )
    tournament = django_filters.ModelChoiceFilter(
        field_name='round__tournament', to_field_name='slug',
        queryset=Tournament.objects.all(), label='Tournament',
    )
    date_after = django_filters.DateFilter(field_name='date_posted', lookup_expr='gte')
    date_before = django_filters.DateFilter(field_name='date_posted', lookup_expr='lte')
    official = django_filters.BooleanFilter(field_name='official')

    # Multi-value "match all" filters. Assets match on slug; players match on id (pk).
    factions = django_filters.ModelMultipleChoiceFilter(
        field_name='efforts__faction', to_field_name='slug',
        queryset=Faction.objects.all(), method='filter_all', label='Factions',
    )
    vagabonds = django_filters.ModelMultipleChoiceFilter(
        field_name='efforts__vagabond', to_field_name='slug',
        queryset=Vagabond.objects.all(), method='filter_all', label='Vagabonds',
    )
    players = django_filters.ModelMultipleChoiceFilter(
        field_name='efforts__player',
        queryset=Profile.objects.all(), method='filter_all', label='Players',
    )
    landmarks = django_filters.ModelMultipleChoiceFilter(
        field_name='landmarks', to_field_name='slug',
        queryset=Landmark.objects.all(), method='filter_all', label='Landmarks',
    )
    hirelings = django_filters.ModelMultipleChoiceFilter(
        field_name='hirelings', to_field_name='slug',
        queryset=Hireling.objects.all(), method='filter_all', label='Hirelings',
    )

    def filter_all(self, queryset, name, value):
        """Keep only games that contain *all* the selected values for this field.

        ``value`` is the cleaned list of model instances; ``name`` is the filter's
        ``field_name`` (the ORM path, e.g. 'efforts__faction', 'landmarks').
        """
        if not value:
            return queryset
        annotation = f'_match_{name.replace("__", "_")}'
        return (
            queryset.filter(**{f'{name}__in': value})
            .annotate(**{annotation: Count(name, filter=Q(**{f'{name}__in': value}), distinct=True)})
            .filter(**{annotation: len(set(value))})
        )

    class Meta:
        model = Game
        fields = [
            'factions', 'vagabonds', 'players', 'landmarks', 'hirelings',
            'map', 'deck', 'tournament', 'date_after', 'date_before', 'official',
        ]

    @property
    def form(self):
        # Attach a crispy FormHelper so the DRF browsable API renders the filter form
        # as a GET <form> with a Submit button (crispy_bootstrap5 otherwise emits only
        # the fields, leaving the "Filters" modal with no way to submit).
        form = super().form
        if not hasattr(form, 'helper'):
            helper = FormHelper()
            helper.form_method = 'get'
            # Lay out all fields, then a button row. "Clear all" resets every input in the
            # form in place (deselects multi-selects, blanks text/date inputs) without
            # navigating; the user then clicks Submit to fetch unfiltered results.
            helper.layout = Layout(
                *list(form.fields.keys()),
                HTML(
                    '<div class="d-flex gap-2">'
                    '<input type="submit" name="" value="Submit" class="btn btn-primary">'
                    '<button type="button" class="btn btn-outline-secondary"'
                    ' onclick="'
                    "var f=this.closest('form');"
                    "f.querySelectorAll('select').forEach(function(s){"
                    "Array.from(s.options).forEach(function(o){o.selected=false;});"
                    # If Select2 is enhancing this select, notify it so its UI updates too.
                    "if(window.jQuery){window.jQuery(s).trigger('change');}});"
                    "f.querySelectorAll('input[type=text],input[type=date],input[type=number]')"
                    ".forEach(function(i){i.value='';});"
                    '">Clear all</button>'
                    '</div>'
                ),
            )
            form.helper = helper
        return form
