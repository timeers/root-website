from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from the_warroom.models import ScoreCard, TurnScore
from .serializers import ScoreCardDetailSerializer, FactionAverageTurnScoreSerializer
from django.db.models import Avg, Sum

from randomcolor import RandomColor

def generate_neon_color():
    random_color = RandomColor()
    color = random_color.generate(luminosity='bright')  # Use bright luminosity for neon-like colors
    return color[0]


class ScoreCardDetailView(APIView):
    def get(self, request, pk, format=None):
        try:
            scorecard = ScoreCard.objects.get(pk=pk)
        except ScoreCard.DoesNotExist:
            return Response({"detail": "ScoreCard not found."}, status=status.HTTP_404_NOT_FOUND)

        # Retrieve all turns related to this scorecard
        turns = scorecard.turns.all()

        # Prepare chart data: turn number and total points for each turn
        turn_data = []
        game_points = 0
        for turn in turns:
            game_points += turn.total_points
            turn_data.append({
                "turn_number": turn.turn_number,
                "faction_points": turn.faction_points,
                "crafting_points": turn.crafting_points,
                "battle_points": turn.battle_points,
                "other_points": turn.other_points,
                "total_points": turn.total_points,
                "game_points": game_points
            })

        color = scorecard.faction.color if scorecard.faction.color else generate_neon_color()


        # Prepare the data for the response
        chart_data = {
            "scorecard_id": scorecard.id,
            "faction": scorecard.faction.title,  # Assuming Faction model has a title
            "total_faction_points": scorecard.total_faction_points,
            "total_crafting_points": scorecard.total_crafting_points,
            "total_battle_points": scorecard.total_battle_points,
            "total_other_points": scorecard.total_other_points,
            "color": color,
            "turns": turn_data  # This contains the turn data for the chart
        }

        return Response(chart_data)



class GameScorecardView(APIView):
    def get(self, request, pk, format=None):
        # Get all scorecards related to the specified game (via game primary key)
        scorecards = ScoreCard.objects.filter(effort__game__pk=pk)

        # Check if there are no scorecards
        if not scorecards.exists():
            print('No scorecards')
            return Response({
                "message": "No scorecards found for this game."
            }, status=status.HTTP_200_OK)

        # Prepare the data for each scorecard
        all_scorecards_data = []

        for scorecard in scorecards:
            # Retrieve all turns related to this scorecard
            turns = scorecard.turns.all()

            # Prepare chart data: turn number, total points, and game points for each turn
            turn_data = []
            game_points = 0
            for turn in turns:
                game_points += turn.total_points
                turn_data.append({
                    "turn_number": turn.turn_number,
                    "total_points": turn.total_points,
                    "game_points": game_points
                })
            color = scorecard.faction.color if scorecard.faction.color else generate_neon_color()
            # Prepare the data for this scorecard (similar to the ScoreCardDetailView)
            scorecard_data = {
                "scorecard_id": scorecard.id,
                "faction": scorecard.faction.title,  # Assuming Faction model has a title
                "color": color,
                "turns": turn_data  # This contains the turn data for the chart
            }

            # Append this scorecard's data to the final list
            all_scorecards_data.append(scorecard_data)

        # Return the list of scorecards data
        return Response(all_scorecards_data, status=status.HTTP_200_OK)


    




class FactionAverageTurnScoreView(APIView):
    def get(self, request, slug, format=None):
        # Get all scorecards related to the given faction
        scorecards = ScoreCard.objects.filter(faction__slug=slug, effort__isnull=False, dominance=False)

        # Check if there are no scorecards
        if not scorecards.exists():
            # Handle case where there are no scorecards
            return Response({
                "message": "No scorecards found."
            }, status=status.HTTP_404_NOT_FOUND)

        # Calculate the total points for each turn across all scorecards
        turn_averages = (
            TurnScore.objects.filter(scorecard__in=scorecards)  # Filter by the related scorecards
            .values('turn_number')  # Group by turn number
            .annotate(
                total_points_sum=Sum('total_points')  # Calculate the total points per turn
            )
            .order_by('turn_number')  # Optional: to order by turn number
        )

        # Calculate the count of the scorecards
        scorecard_count = scorecards.count()

        # Format the data for the response
        average_data = {
            "faction_name": scorecards.first().faction.title,  # Get the faction's name
            "averages": []
        }

        for avg in turn_averages:
            # Divide the total points by the number of scorecards for the faction
            average_total_points = avg['total_points_sum'] / scorecard_count if scorecard_count else 0
            average_data["averages"].append({
                "turn_number": avg['turn_number'],
                "average_total_points": average_total_points
            })

        # Return the data in JSON format
        return Response(average_data, status=status.HTTP_200_OK)


    # def get(self, request, slug, format=None):
    #     # Get all scorecards related to the given faction
    #     scorecards = ScoreCard.objects.filter(faction__slug=slug, effort__isnull=False)

    #     # Calculate average points for each turn_number
    #     turn_averages = (
    #         TurnScore.objects.filter(scorecard__in=scorecards)  # Filter by the related scorecards
    #         .values('turn_number')  # Group by turn number
    #         .annotate(average_total_points=Avg('total_points'))  # Calculate the average for each turn
    #         .order_by('turn_number')  # Optional: to order by turn number
    #     )

    #     # Format the data for the response
    #     average_data = {
    #         "faction_name": scorecards.first().faction.title,  # Get the faction's name
    #         "averages": []
    #     }

    #     for avg in turn_averages:
    #         average_data["averages"].append({
    #             "turn_number": avg['turn_number'],
    #             "average_total_points": avg['average_total_points']
    #         })

    #     # Use the serializer to return the data in JSON format
    #     serializer = FactionAverageTurnScoreSerializer(average_data)

    #     return Response(serializer.data, status=status.HTTP_200_OK)
    

class AverageTurnScoreView(APIView):
    def get(self, request, format=None):
        # Get all scorecards related to the given faction
        scorecards = ScoreCard.objects.filter(effort__isnull=False, dominance=False)

        # Check if there are no scorecards
        if not scorecards.exists():
            # Handle case where there are no scorecards
            return Response({
                "message": "No scorecards found for the specified faction."
            }, status=status.HTTP_404_NOT_FOUND)


        # Calculate the total points for each turn across all scorecards
        turn_averages = (
            TurnScore.objects.filter(scorecard__in=scorecards)  # Filter by the related scorecards
            .values('turn_number')  # Group by turn number
            .annotate(
                total_points_sum=Sum('total_points')  # Calculate the total points per turn
            )
            .order_by('turn_number')  # Optional: to order by turn number
        )

        # Calculate the count of the scorecards
        scorecard_count = scorecards.count()

        # Format the data for the response
        average_data = {
            "faction_name": scorecards.first().faction.title,  # Get the faction's name
            "averages": []
        }

        for avg in turn_averages:
            # Divide the total points by the number of scorecards for the faction
            average_total_points = avg['total_points_sum'] / scorecard_count if scorecard_count else 0
            average_data["averages"].append({
                "turn_number": avg['turn_number'],
                "average_total_points": average_total_points
            })

        # Return the data in JSON format
        return Response(average_data, status=status.HTTP_200_OK)
    # def get(self, request, format=None):
    #     # Get all scorecards related to the given faction
    #     scorecards = ScoreCard.objects.filter(effort__isnull=False, faction__official=True)

    #     # Calculate average points for each turn_number
    #     turn_averages = (
    #         TurnScore.objects.filter(scorecard__in=scorecards)  # Filter by the related scorecards
    #         .values('turn_number')  # Group by turn number
    #         .annotate(average_total_points=Avg('total_points'))  # Calculate the average for each turn
    #         .order_by('turn_number')  # Optional: to order by turn number
    #     )

    #     # Format the data for the response
    #     average_data = {
    #         "faction_name": scorecards.first().faction.title,  # Get the faction's name
    #         "averages": []
    #     }

    #     for avg in turn_averages:
    #         average_data["averages"].append({
    #             "turn_number": avg['turn_number'],
    #             "average_total_points": avg['average_total_points']
    #         })

    #     # Use the serializer to return the data in JSON format
    #     serializer = FactionAverageTurnScoreSerializer(average_data)

    #     return Response(serializer.data, status=status.HTTP_200_OK)