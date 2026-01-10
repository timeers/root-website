from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from the_warroom.models import ScoreCard, TurnScore
from the_keep.models import Faction, PostTranslation
from the_gatehouse.utils import generate_neon_color
# from .serializers import ScoreCardDetailSerializer, FactionAverageTurnScoreSerializer
from django.db.models import Avg, Sum, Count, Prefetch, F, FloatField, Q
from django.db.models.functions import Cast

from django.utils.translation import get_language
from collections import defaultdict


class ScoreCardDetailView(APIView):
    def get(self, request, pk, format=None):
        try:
            scorecard = ScoreCard.objects.prefetch_related('turns').get(pk=pk)
        except ScoreCard.DoesNotExist:
            return Response({"detail": "ScoreCard not found."}, status=status.HTTP_404_NOT_FOUND)

        # Get turns with all data at once (already prefetched)
        turn_data = list(scorecard.turns.values(
            'turn_number',
            'faction_points',
            'crafting_points',
            'battle_points',
            'other_points',
            'total_points',
            'game_points_total'
        ).order_by('turn_number'))
        
        # Rename game_points_total to game_points in the response
        for turn in turn_data:
            turn['game_points'] = turn.pop('game_points_total')

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
        current_language_code = get_language()
        translations = PostTranslation.objects.filter(language__code=current_language_code)
        
        # Optimize: prefetch turns along with faction data
        scorecards = ScoreCard.objects.filter(
            effort__game__pk=pk
        ).select_related('faction').prefetch_related(
            'turns',  # Add this!
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

            # Get turn data directly from prefetched data
            turn_data = list(scorecard.turns.values(
                'turn_number',
                'total_points',
                'game_points_total'
            ).order_by('turn_number'))
            
            # Rename game_points_total to game_points
            for turn in turn_data:
                turn['game_points'] = turn.pop('game_points_total')

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
        
        # Get scorecard count
        scorecard_count = ScoreCard.objects.filter(
            faction=faction,
            effort__isnull=False,
            final=True
        ).count()
        
        if scorecard_count == 0:
            return Response({
                "message": "No scorecards found."
            }, status=status.HTTP_200_OK)
        
        # Single optimized query with all aggregations
        turn_data = (
            TurnScore.objects
            .filter(
                scorecard__faction=faction,
                scorecard__effort__isnull=False,
                scorecard__final=True,
                turn_number__lte=10
            )
            .values('turn_number')
            .annotate(
                turn_count=Count('id'),
                avg_total_points=Sum('total_points') / Cast(Count('id'), FloatField()),
                avg_faction_points=Sum('faction_points') / Cast(Count('id'), FloatField()),
                avg_crafting_points=Sum('crafting_points') / Cast(Count('id'), FloatField()),
                avg_battle_points=Sum('battle_points') / Cast(Count('id'), FloatField()),
                avg_other_points=Sum('other_points') / Cast(Count('id'), FloatField()),
                avg_game_points=Sum('game_points_total') / Cast(Count('id'), FloatField()),
            )
            .order_by('turn_number')
        )
        
        # Build response data
        averages = []
        total_faction_points = 0
        total_crafting_points = 0
        total_battle_points = 0
        total_other_points = 0
        
        for turn in turn_data:
            avg_total = turn['avg_total_points'] or 0
            avg_faction = turn['avg_faction_points'] or 0
            avg_crafting = turn['avg_crafting_points'] or 0
            avg_battle = turn['avg_battle_points'] or 0
            avg_other = turn['avg_other_points'] or 0
            avg_game = turn['avg_game_points'] or 0
            
            total_faction_points += avg_faction
            total_crafting_points += avg_crafting
            total_battle_points += avg_battle
            total_other_points += avg_other
            
            averages.append({
                "turn_number": turn['turn_number'],
                "average_total_points": avg_total,
                "average_faction_points": avg_faction,
                "average_crafting_points": avg_crafting,
                "average_battle_points": avg_battle,
                "average_other_points": avg_other,
                "average_game_points": avg_game,
            })
        
        average_data = {
            "faction_name": faction.title,
            "count": scorecard_count,
            "color": color,
            "averages": averages,
            "totals": {
                "total_faction_points": total_faction_points,
                "total_crafting_points": total_crafting_points,
                "total_battle_points": total_battle_points,
                "total_other_points": total_other_points
            }
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

        # Get scorecard count
        scorecard_count = ScoreCard.objects.filter(scorecard_filter).count()

        if scorecard_count == 0:
            return Response({
                "message": "No scorecards found."
            }, status=status.HTTP_200_OK)

        # Single optimized query with all aggregations
        turn_data = (
            TurnScore.objects
            .filter(
                scorecard__effort__isnull=False,
                scorecard__final=True,
                turn_number__lte=10
            )
        )
        
        # Add faction type filter if specified
        if faction_type and faction_type != "A":
            turn_data = turn_data.filter(
                scorecard__faction__type=faction_type,
                scorecard__faction__official=True
            )
        
        # Aggregate by turn number
        turn_data = (
            turn_data
            .values('turn_number')
            .annotate(
                turn_count=Count('id'),
                avg_total_points=Sum('total_points') / Cast(Count('id'), FloatField()),
                avg_faction_points=Sum('faction_points') / Cast(Count('id'), FloatField()),
                avg_crafting_points=Sum('crafting_points') / Cast(Count('id'), FloatField()),
                avg_battle_points=Sum('battle_points') / Cast(Count('id'), FloatField()),
                avg_other_points=Sum('other_points') / Cast(Count('id'), FloatField()),
                avg_game_points=Sum('game_points_total') / Cast(Count('id'), FloatField()),
            )
            .order_by('turn_number')
        )

        # Build response data
        averages = []
        total_faction_points = 0
        total_crafting_points = 0
        total_battle_points = 0
        total_other_points = 0

        for turn in turn_data:
            avg_total = turn['avg_total_points'] or 0
            avg_faction = turn['avg_faction_points'] or 0
            avg_crafting = turn['avg_crafting_points'] or 0
            avg_battle = turn['avg_battle_points'] or 0
            avg_other = turn['avg_other_points'] or 0
            avg_game = turn['avg_game_points'] or 0

            total_faction_points += avg_faction
            total_crafting_points += avg_crafting
            total_battle_points += avg_battle
            total_other_points += avg_other

            averages.append({
                "turn_number": turn['turn_number'],
                "average_total_points": avg_total,
                "average_faction_points": avg_faction,
                "average_crafting_points": avg_crafting,
                "average_battle_points": avg_battle,
                "average_other_points": avg_other,
                "average_game_points": avg_game,
            })

        average_data = {
            "type": faction_type,
            "count": scorecard_count,
            "averages": averages,
            "totals": {
                "total_faction_points": total_faction_points,
                "total_crafting_points": total_crafting_points,
                "total_battle_points": total_battle_points,
                "total_other_points": total_other_points
            }
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

        current_language_code = get_language()
        translations = PostTranslation.objects.filter(language__code=current_language_code)
        
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
        
        # Single query to get all turn data grouped by faction and turn_number
        turn_data_by_faction = (
            TurnScore.objects
            .filter(
                scorecard__in=scorecards,
                scorecard__final=True,
                turn_number__lte=10
            )
            .values('scorecard__faction_id', 'turn_number')
            .annotate(
                turn_count=Count('id'),
                avg_total_points=Sum('total_points') / Cast(Count('id'), FloatField()),
                avg_faction_points=Sum('faction_points') / Cast(Count('id'), FloatField()),
                avg_crafting_points=Sum('crafting_points') / Cast(Count('id'), FloatField()),
                avg_battle_points=Sum('battle_points') / Cast(Count('id'), FloatField()),
                avg_other_points=Sum('other_points') / Cast(Count('id'), FloatField()),
                avg_game_points=Sum('game_points_total') / Cast(Count('id'), FloatField()),
            )
            .order_by('scorecard__faction_id', 'turn_number')
        )
        
        # Group turn data by faction_id
        turns_by_faction = defaultdict(list)
        for turn in turn_data_by_faction:
            turns_by_faction[turn['scorecard__faction_id']].append(turn)
        
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
            
            # Get turn data for this faction
            faction_turns = turns_by_faction.get(faction.id, [])
            
            # Calculate totals
            total_faction_points = 0
            total_crafting_points = 0
            total_battle_points = 0
            total_other_points = 0
            
            averages = []
            for turn in faction_turns:
                avg_total = turn['avg_total_points'] or 0
                avg_faction = turn['avg_faction_points'] or 0
                avg_crafting = turn['avg_crafting_points'] or 0
                avg_battle = turn['avg_battle_points'] or 0
                avg_other = turn['avg_other_points'] or 0
                avg_game = turn['avg_game_points'] or 0
                
                total_faction_points += avg_faction
                total_crafting_points += avg_crafting
                total_battle_points += avg_battle
                total_other_points += avg_other
                
                averages.append({
                    "turn_number": turn['turn_number'],
                    "average_total_points": avg_total,
                    "average_faction_points": avg_faction,
                    "average_crafting_points": avg_crafting,
                    "average_battle_points": avg_battle,
                    "average_other_points": avg_other,
                    "average_game_points": avg_game,
                })
            
            faction_average_data = {
                "faction": translated_title,
                "color": faction.color,
                "count": scorecard_count_dict.get(faction.id, 0),
                "averages": averages,
                "totals": {
                    "total_faction_points": total_faction_points,
                    "total_crafting_points": total_crafting_points,
                    "total_battle_points": total_battle_points,
                    "total_other_points": total_other_points
                }
            }
            
            average_data_by_faction[faction.title] = faction_average_data

        return Response(average_data_by_faction, status=status.HTTP_200_OK)