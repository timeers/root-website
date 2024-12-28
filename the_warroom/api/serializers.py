from rest_framework import serializers
from the_warroom.models import ScoreCard, TurnScore

class TurnScoreSerializer(serializers.ModelSerializer):
    class Meta:
        model = TurnScore
        fields = ['turn_number', 'faction_points', 'crafting_points', 'battle_points', 'other_points', 'total_points']

class ScoreCardDetailSerializer(serializers.ModelSerializer):
    turns = TurnScoreSerializer(many=True)  # Serialize the related TurnScore objects

    class Meta:
        model = ScoreCard
        fields = ['faction', 'recorder', 'turns', 'total_points']


class AverageTurnScoreSerializer(serializers.Serializer):
    turn_number = serializers.IntegerField()
    average_total_points = serializers.FloatField()


class FactionAverageTurnScoreSerializer(serializers.Serializer):
    faction_name = serializers.CharField()
    averages = AverageTurnScoreSerializer(many=True)
