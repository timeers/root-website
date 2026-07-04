from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status, generics
from rest_framework.pagination import CursorPagination
from rest_framework.permissions import IsAuthenticated
from django_filters.rest_framework import DjangoFilterBackend
from the_warroom.models import ScoreCard, Game, Effort
from the_keep.models import Faction, PostTranslation
from the_gatehouse.utils import generate_neon_color
from .game_serializers import GameSerializer
from .game_filters import GameFilter
from django.db.models import Count, Prefetch, Q

from django.utils.translation import get_language
from collections import defaultdict


def aggregate_turns(turns_lists):
    """Aggregate per-turn averages across an iterable of turns_data lists.

    Reproduces the old SQL ``.values('turn_number').annotate(Sum(...)/Count('id'))``:
    the count for a turn number is the number of scorecards that have that turn
    (turn_number 1..10), and ``average_game_points`` averages the stored cumulative
    ``game_points_total``. Returns (averages_list, totals_dict).
    """
    sums = defaultdict(lambda: defaultdict(float))
    counts = defaultdict(int)
    for turns in turns_lists:
        for t in (turns or []):
            tn = t.get('turn_number', 0)
            if tn < 1 or tn > 10:
                continue
            counts[tn] += 1
            sums[tn]['total'] += t.get('total_points', 0)
            sums[tn]['faction'] += t.get('faction_points', 0)
            sums[tn]['crafting'] += t.get('crafting_points', 0)
            sums[tn]['battle'] += t.get('battle_points', 0)
            sums[tn]['other'] += t.get('other_points', 0)
            sums[tn]['game'] += t.get('game_points_total', 0)

    averages = []
    totals = {'total_faction_points': 0, 'total_crafting_points': 0,
              'total_battle_points': 0, 'total_other_points': 0}
    for tn in sorted(counts):
        c = counts[tn]
        avg_faction = sums[tn]['faction'] / c
        avg_crafting = sums[tn]['crafting'] / c
        avg_battle = sums[tn]['battle'] / c
        avg_other = sums[tn]['other'] / c
        totals['total_faction_points'] += avg_faction
        totals['total_crafting_points'] += avg_crafting
        totals['total_battle_points'] += avg_battle
        totals['total_other_points'] += avg_other
        averages.append({
            "turn_number": tn,
            "average_total_points": sums[tn]['total'] / c,
            "average_faction_points": avg_faction,
            "average_crafting_points": avg_crafting,
            "average_battle_points": avg_battle,
            "average_other_points": avg_other,
            "average_game_points": sums[tn]['game'] / c,
        })
    return averages, totals


class ScoreCardDetailView(APIView):
    def get(self, request, pk, format=None):
        try:
            scorecard = ScoreCard.objects.get(pk=pk)
        except ScoreCard.DoesNotExist:
            return Response({"detail": "ScoreCard not found."}, status=status.HTTP_404_NOT_FOUND)

        # get_turns() already emits sorted turns with a cumulative 'game_points' alias.
        turn_data = [
            {k: t.get(k, 0) for k in (
                'turn_number', 'faction_points', 'crafting_points',
                'battle_points', 'other_points', 'total_points', 'game_points',
            )}
            for t in scorecard.get_turns()
        ]

        color = scorecard.faction.color if scorecard.faction.color else generate_neon_color()

        chart_data = {
            "scorecard_id": scorecard.id,
            "faction": scorecard.faction.title,
            "total_faction_points": scorecard.total_faction_points,
            "total_crafting_points": scorecard.total_crafting_points,
            "total_battle_points": scorecard.total_battle_points,
            "total_other_points": scorecard.total_other_points,
            "color": color,
            "turns": turn_data
        }

        return Response(chart_data)


class GameScorecardView(APIView):
    def get(self, request, pk, format=None):
        language_code = get_language()
        translations = PostTranslation.objects.filter(language__code=language_code)
        
        # Optimize: prefetch turns along with faction data
        scorecards = ScoreCard.objects.filter(
            effort__game__pk=pk
        ).select_related('faction').prefetch_related(
            Prefetch('faction__translations', queryset=translations, to_attr='filtered_translations')
        )

        if not scorecards.exists():
            return Response({
                "message": "No scorecards found for this game."
            }, status=status.HTTP_200_OK)

        all_scorecards_data = []

        for scorecard in scorecards:
            translated_title = scorecard.faction.title
            if hasattr(scorecard.faction, 'filtered_translations') and scorecard.faction.filtered_translations:
                translated_title = scorecard.faction.filtered_translations[0].translated_title

            # Build turn data from the turns_data JSON (game_points alias included).
            turn_data = [
                {k: t.get(k, 0) for k in ('turn_number', 'total_points', 'game_points')}
                for t in scorecard.get_turns()
            ]

            color = scorecard.faction.color if scorecard.faction.color else generate_neon_color()

            scorecard_data = {
                "scorecard_id": scorecard.id,
                "faction": translated_title,
                "color": color,
                "turns": turn_data
            }

            all_scorecards_data.append(scorecard_data)

        return Response(all_scorecards_data, status=status.HTTP_200_OK)


class FactionAverageTurnScoreView(APIView):
    def get(self, request, slug, format=None):
        # Get faction
        try:
            faction = Faction.objects.get(slug=slug)
        except Faction.DoesNotExist:
            return Response({
                "message": "Faction not found."
            }, status=status.HTTP_404_NOT_FOUND)
        
        color = faction.color if faction.color else generate_neon_color()
        
        # Get the filtered scorecards' turns_data in one query.
        turns_lists = ScoreCard.objects.filter(
            faction=faction,
            effort__isnull=False,
            final=True
        ).values_list('turns_data', flat=True)

        scorecard_count = len(turns_lists)
        if scorecard_count == 0:
            return Response({
                "message": "No scorecards found."
            }, status=status.HTTP_200_OK)

        averages, totals = aggregate_turns(turns_lists)

        average_data = {
            "faction_name": faction.title,
            "count": scorecard_count,
            "color": color,
            "averages": averages,
            "totals": totals,
        }

        return Response(average_data, status=status.HTTP_200_OK)



class AverageTurnScoreView(APIView):
    def get(self, request, format=None):
        # Get the 'type' query parameter (optional)
        faction_type = request.query_params.get('type', None)

        # Build the base filter
        scorecard_filter = Q(effort__isnull=False, final=True)
        
        if faction_type:
            scorecard_filter &= Q(faction__type=faction_type, faction__official=True)
        else:
            faction_type = "A"

        # Fetch the filtered scorecards' turns_data in one query.
        turns_lists = ScoreCard.objects.filter(scorecard_filter).values_list('turns_data', flat=True)

        scorecard_count = len(turns_lists)
        if scorecard_count == 0:
            return Response({
                "message": "No scorecards found."
            }, status=status.HTTP_200_OK)

        averages, totals = aggregate_turns(turns_lists)

        average_data = {
            "type": faction_type,
            "count": scorecard_count,
            "averages": averages,
            "totals": totals,
        }

        return Response(average_data, status=status.HTTP_200_OK)


class PlayerScorecardView(APIView):
    def get(self, request, slug, format=None):
        # Get the 'recorder' query parameter from the URL (if provided)
        recorder = request.query_params.get('recorder', None)

        if recorder:
            # Get all scorecards recorded by a player (via player slug)
            scorecards = ScoreCard.objects.filter(
                recorder__slug=slug,
                effort__isnull=False,
                final=True
            )
        else:
            # Get all scorecards related to the specified player (via player slug)
            scorecards = ScoreCard.objects.filter(
                effort__player__slug=slug,
                final=True
            )

        language_code = get_language()
        translations = PostTranslation.objects.filter(language__code=language_code)
        
        # Optimize: select_related for faction, prefetch translations
        scorecards = scorecards.select_related('faction').prefetch_related(
            Prefetch('faction__translations', queryset=translations, to_attr='filtered_translations')
        )

        # Check if there are no scorecards
        if not scorecards.exists():
            return Response({
                "message": "No scorecards found."
            }, status=status.HTTP_200_OK)

        # Get all faction IDs from scorecards
        faction_ids = scorecards.values_list('faction_id', flat=True).distinct()

        # Group each scorecard's turns_data by faction in one query.
        turns_by_faction = defaultdict(list)
        for faction_id, turns in scorecards.values_list('faction_id', 'turns_data'):
            turns_by_faction[faction_id].append(turns)

        # Group scorecards by faction for counting
        faction_scorecard_counts = (
            scorecards
            .values('faction_id')
            .annotate(count=Count('id'))
        )
        scorecard_count_dict = {item['faction_id']: item['count'] for item in faction_scorecard_counts}
        
        # Get unique factions from scorecards
        factions = Faction.objects.filter(id__in=faction_ids).prefetch_related(
            Prefetch('translations', queryset=translations, to_attr='filtered_translations')
        )
        
        # Build response data
        average_data_by_faction = {}
        
        for faction in factions:
            # Get translated title
            translated_title = faction.title
            if hasattr(faction, 'filtered_translations') and faction.filtered_translations:
                translated_title = faction.filtered_translations[0].translated_title
            
            # Aggregate this faction's turns_data into per-turn averages.
            averages, totals = aggregate_turns(turns_by_faction.get(faction.id, []))

            faction_average_data = {
                "faction": translated_title,
                "color": faction.color,
                "count": scorecard_count_dict.get(faction.id, 0),
                "averages": averages,
                "totals": totals,
            }

            average_data_by_faction[faction.title] = faction_average_data

        return Response(average_data_by_faction, status=status.HTTP_200_OK)


class GameCursorPagination(CursorPagination):
    """Cursor pagination for the game download API.

    Keyset pagination on ``-date_posted`` keeps every page fast regardless of depth and
    stays consistent if games are added mid-download. Clients page by following ``next``
    until it is null.
    """
    ordering = '-date_posted'
    page_size = 50
    page_size_query_param = 'page_size'
    max_page_size = 500


class GameListView(generics.ListAPIView):
    """Filterable, paginated download of finalized game data (API key or session auth)."""
    serializer_class = GameSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = GameCursorPagination
    filter_backends = [DjangoFilterBackend]
    filterset_class = GameFilter

    def get_queryset(self):
        # No .distinct(): the base query has no row-multiplying joins (FKs are 1:1, M2M are
        # fetched via separate prefetch queries), and the multi-select filters use
        # conjoined=True which chains one JOIN per value without producing duplicate rows.
        # A blanket DISTINCT would force Postgres to dedupe the whole table before applying
        # the cursor limit, defeating keyset pagination (~240ms even for a 10-row page).
        related = Game.with_efforts()
        return (
            Game.objects.filter(final=True)
            .select_related(*related['select'], 'undrafted_faction', 'undrafted_vagabond')
            .prefetch_related(
                # Override the standard efforts prefetch so the Knaves captain
                # fields (FK discarded_captain, M2M captains) are loaded too.
                Prefetch(
                    'efforts',
                    queryset=Effort.objects.select_related(
                        'player', 'faction', 'vagabond', 'coalition_with', 'discarded_captain'
                    ).prefetch_related('captains'),
                ),
                'landmarks',
                'hirelings',
                'undrafted_captains',
            )
        )