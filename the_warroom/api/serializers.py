from rest_framework import serializers
from the_warroom.models import ScoreCard

TURN_SERIALIZER_FIELDS = ('turn_number', 'faction_points', 'crafting_points',
                          'battle_points', 'other_points', 'total_points')


class ScoreCardDetailSerializer(serializers.ModelSerializer):
    turns = serializers.SerializerMethodField()  # Serialize the turns_data JSON

    class Meta:
        model = ScoreCard
        fields = ['faction', 'recorder', 'turns', 'total_points']

    def get_turns(self, obj):
        return [{k: t.get(k, 0) for k in TURN_SERIALIZER_FIELDS} for t in obj.get_turns()]


class AverageTurnScoreSerializer(serializers.Serializer):
    turn_number = serializers.IntegerField()
    average_total_points = serializers.FloatField()


class FactionAverageTurnScoreSerializer(serializers.Serializer):
    faction_name = serializers.CharField()
    averages = AverageTurnScoreSerializer(many=True)
