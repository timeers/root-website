from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from the_warroom.models import ScoreCard, TurnScore
from the_keep.models import Faction
from the_gatehouse.models import Profile
from .serializers import ScoreCardDetailSerializer, FactionAverageTurnScoreSerializer
from django.db.models import Avg, Sum

from collections import defaultdict

from randomcolor import RandomColor

def generate_neon_color():
    random_color = RandomColor()
    color = random_color.generate(luminosity='light', hue='pastel')[0]  # Use bright luminosity for neon-like colors
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
        faction = Faction.objects.get(slug=slug)

        color = faction.color if faction.color else generate_neon_color()

        # Check if there are no scorecards
        if not scorecards.exists():
            # Handle case where there are no scorecards
            return Response({
                "message": "No scorecards found."
            }, status=status.HTTP_200_OK)

        # Calculate the total points for each turn across all scorecards
        turn_averages = (
            TurnScore.objects.filter(scorecard__in=scorecards, turn_number__lte=10)  # Filter by the related scorecards
            .values('turn_number')  # Group by turn number
            .annotate(
                total_points_sum=Sum('total_points'),  # Calculate the total points per turn
                faction_points_sum=Sum('faction_points'),  # Calculate the faction points per turn
                crafting_points_sum=Sum('crafting_points'),  # Calculate the crafting points per turn
                battle_points_sum=Sum('battle_points'),  # Calculate the battle points per turn
                other_points_sum=Sum('other_points')  # Calculate the other points per turn
            )
            .order_by('turn_number')  # Optional: to order by turn number
        )

        # Calculate the count of the scorecards
        scorecard_count = scorecards.count()

        # Initialize variables to store the total sum of points across all turns
        total_faction_points = 0
        total_crafting_points = 0
        total_battle_points = 0
        total_other_points = 0

        # Format the data for the response
        average_data = {
            "faction_name": scorecards.first().faction.title,  # Get the faction's name
            "count": scorecard_count,
            "color": color,
            "averages": [],
            "totals": [],
        }
        average_game_points = 0
        for avg in turn_averages:
            # Divide the total points by the number of scorecards for the faction
            
            average_total_points = avg['total_points_sum'] / scorecard_count if scorecard_count else 0
            average_faction_points = avg['faction_points_sum'] / scorecard_count if scorecard_count else 0
            average_crafting_points = avg['crafting_points_sum'] / scorecard_count if scorecard_count else 0
            average_battle_points = avg['battle_points_sum'] / scorecard_count if scorecard_count else 0
            average_other_points = avg['other_points_sum'] / scorecard_count if scorecard_count else 0
            average_game_points += average_total_points

            # Add the aggregated totals to the overall totals
            total_faction_points += average_faction_points
            total_crafting_points += average_crafting_points
            total_battle_points += average_battle_points
            total_other_points += average_other_points

            average_data["averages"].append({
                "turn_number": avg['turn_number'],
                "average_total_points": average_total_points,
                "average_faction_points": average_faction_points,
                "average_crafting_points": average_crafting_points,
                "average_battle_points": average_battle_points,
                "average_other_points": average_other_points,
                "average_game_points": average_game_points,

            })
        # Add the aggregated totals to the "totals" list
        average_data["totals"] = {
            "total_faction_points": total_faction_points,
            "total_crafting_points": total_crafting_points,
            "total_battle_points": total_battle_points,
            "total_other_points": total_other_points
        }
        # Return the data in JSON format
        return Response(average_data, status=status.HTTP_200_OK)


    

class AverageTurnScoreView(APIView):
    def get(self, request, format=None):

        # Get the 'type' query parameter (optional)
        faction_type = request.query_params.get('type', None)

        # Get all scorecards related to the given faction type (if provided)
        if faction_type:
            # Filter by the provided faction type
            print('Faction Type', faction_type)
            scorecards = ScoreCard.objects.filter(effort__isnull=False, dominance=False, faction__type=faction_type)
        else:
            faction_type = "A"
            # If no type is provided, get all scorecards with the original conditions
            scorecards = ScoreCard.objects.filter(effort__isnull=False, dominance=False)


        # Check if there are no scorecards
        if not scorecards.exists():
            # Handle case where there are no scorecards
            return Response({
                "message": "No scorecards found."
            }, status=status.HTTP_200_OK)

        # Calculate the total points for each turn across all scorecards
        turn_averages = (
            TurnScore.objects.filter(scorecard__in=scorecards, turn_number__lte=10)  # Filter by the related scorecards
            .values('turn_number')  # Group by turn number
            .annotate(
                total_points_sum=Sum('total_points'),  # Calculate the total points per turn
                faction_points_sum=Sum('faction_points'),  # Calculate the faction points per turn
                crafting_points_sum=Sum('crafting_points'),  # Calculate the crafting points per turn
                battle_points_sum=Sum('battle_points'),  # Calculate the battle points per turn
                other_points_sum=Sum('other_points')  # Calculate the other points per turn
            )
            .order_by('turn_number')  # Optional: to order by turn number
        )

        # Calculate the count of the scorecards
        scorecard_count = scorecards.count()

        # Initialize variables to store the total sum of points across all turns
        total_faction_points = 0
        total_crafting_points = 0
        total_battle_points = 0
        total_other_points = 0

        # Format the data for the response
        average_data = {
            "type": faction_type,
            "count": scorecard_count,
            "averages": [],
            "totals": [],
        }
        average_game_points = 0
        for avg in turn_averages:
            # Divide the total points by the number of scorecards for the faction
            
            average_total_points = avg['total_points_sum'] / scorecard_count if scorecard_count else 0
            average_faction_points = avg['faction_points_sum'] / scorecard_count if scorecard_count else 0
            average_crafting_points = avg['crafting_points_sum'] / scorecard_count if scorecard_count else 0
            average_battle_points = avg['battle_points_sum'] / scorecard_count if scorecard_count else 0
            average_other_points = avg['other_points_sum'] / scorecard_count if scorecard_count else 0
            average_game_points += average_total_points

            # Add the aggregated totals to the overall totals
            total_faction_points += average_faction_points
            total_crafting_points += average_crafting_points
            total_battle_points += average_battle_points
            total_other_points += average_other_points

            average_data["averages"].append({
                "turn_number": avg['turn_number'],
                "average_total_points": average_total_points,
                "average_faction_points": average_faction_points,
                "average_crafting_points": average_crafting_points,
                "average_battle_points": average_battle_points,
                "average_other_points": average_other_points,
                "average_game_points": average_game_points,

            })
        # Add the aggregated totals to the "totals" list
        average_data["totals"] = {
            "total_faction_points": total_faction_points,
            "total_crafting_points": total_crafting_points,
            "total_battle_points": total_battle_points,
            "total_other_points": total_other_points
        }
        # Return the data in JSON format
        return Response(average_data, status=status.HTTP_200_OK)
    

class PlayerScorecardView(APIView):
    def get(self, request, slug, format=None):

        # Get the 'recorder' query parameter from the URL (if provided)
        recorder = request.query_params.get('recorder', None)

        if recorder:
            # Get all scorecards recorded by a player (via player slug)        
            scorecards = ScoreCard.objects.filter(recorder__slug=slug, effort__isnull=False)
        else:
            # Get all scorecards related to the specified player (via player slug)        
            scorecards = ScoreCard.objects.filter(effort__player__slug=slug)

        # Check if there are no scorecards
        if not scorecards.exists():
            print('No scorecards')
            return Response({
                "message": "No scorecards found."
            }, status=status.HTTP_200_OK)
        # Group scorecards by faction
        faction_groups = defaultdict(list)
        for scorecard in scorecards:
            faction_groups[scorecard.faction].append(scorecard)

        # Initialize a dictionary to store the average data per faction
        average_data_by_faction = {}

        # Loop through each faction and calculate averages
        for faction, faction_scorecards in faction_groups.items():
            turn_averages = (
                TurnScore.objects.filter(scorecard__in=faction_scorecards, turn_number__lte=10)
                .values('turn_number')  # Group by turn number
                .annotate(
                    total_points_sum=Sum('total_points'),
                    faction_points_sum=Sum('faction_points'),
                    crafting_points_sum=Sum('crafting_points'),
                    battle_points_sum=Sum('battle_points'),
                    other_points_sum=Sum('other_points')
                )
                .order_by('turn_number')  # Order by turn number
            )

            # Calculate the count of the scorecards for the current faction
            faction_scorecard_count = len(faction_scorecards)

            # Initialize variables to store the total sum of points for the current faction
            total_faction_points = 0
            total_crafting_points = 0
            total_battle_points = 0
            total_other_points = 0

            # Initialize the data for the current faction
            faction_average_data = {
                "faction": faction.title, 
                "color": faction.color,
                "count": faction_scorecard_count,
                "averages": [],
                "totals": {},
            }

            # Format the data for the current faction
            average_game_points = 0
            for avg in turn_averages:
                average_total_points = avg['total_points_sum'] / faction_scorecard_count if faction_scorecard_count else 0
                average_faction_points = avg['faction_points_sum'] / faction_scorecard_count if faction_scorecard_count else 0
                average_crafting_points = avg['crafting_points_sum'] / faction_scorecard_count if faction_scorecard_count else 0
                average_battle_points = avg['battle_points_sum'] / faction_scorecard_count if faction_scorecard_count else 0
                average_other_points = avg['other_points_sum'] / faction_scorecard_count if faction_scorecard_count else 0
                average_game_points += average_total_points

                total_faction_points += average_faction_points
                total_crafting_points += average_crafting_points
                total_battle_points += average_battle_points
                total_other_points += average_other_points

                # Append the averages for the current turn
                faction_average_data["averages"].append({
                    "turn_number": avg['turn_number'],
                    "average_total_points": average_total_points,
                    "average_faction_points": average_faction_points,
                    "average_crafting_points": average_crafting_points,
                    "average_battle_points": average_battle_points,
                    "average_other_points": average_other_points,
                    "average_game_points": average_game_points,
                })

            # Add the aggregated totals to the "totals" for the current faction
            faction_average_data["totals"] = {
                "total_faction_points": total_faction_points,
                "total_crafting_points": total_crafting_points,
                "total_battle_points": total_battle_points,
                "total_other_points": total_other_points
            }

            # Add this faction's data to the overall result
            average_data_by_faction[faction.title] = faction_average_data

        # Return the average data by faction in the response
        return Response(average_data_by_faction, status=status.HTTP_200_OK)