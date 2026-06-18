from rest_framework import serializers

from the_warroom.models import Game, Effort


class ParticipantSerializer(serializers.ModelSerializer):
    """One seat (Effort) in a game."""
    player = serializers.SerializerMethodField()
    player_id = serializers.IntegerField(read_only=True)
    coalition = serializers.SerializerMethodField()
    faction = serializers.SerializerMethodField()
    game_score = serializers.IntegerField(source='score', read_only=True)
    vagabond = serializers.SerializerMethodField()
    tournament_score = serializers.SerializerMethodField()
    turn_order = serializers.IntegerField(source='seat', read_only=True)

    class Meta:
        model = Effort
        fields = [
            'id', 'player', 'player_id', 'coalition', 'faction',
            'game_score', 'dominance', 'vagabond', 'tournament_score', 'turn_order',
        ]

    def get_player(self, obj):
        return obj.player.discord if obj.player else None

    def get_coalition(self, obj):
        return obj.coalition_with.slug if obj.coalition_with else None

    def get_faction(self, obj):
        return obj.faction.slug if obj.faction else None

    def get_vagabond(self, obj):
        return obj.vagabond.slug if obj.vagabond else None

    def get_tournament_score(self, obj):
        # 1 for a solo win, 0.5 for a coalition win, 0 for a loss.
        if not obj.win:
            return 0
        return 0.5 if obj.coalition_with_id else 1


class GameSerializer(serializers.ModelSerializer):
    participants = ParticipantSerializer(many=True, source='efforts', read_only=True)
    tournament = serializers.SerializerMethodField()
    hirelings = serializers.SerializerMethodField()
    landmarks = serializers.SerializerMethodField()
    title = serializers.CharField(source='nickname', read_only=True)
    date_registered = serializers.DateTimeField(source='date_posted', read_only=True)
    date_closed = serializers.DateTimeField(source='date_posted', read_only=True)
    turn_timing = serializers.SerializerMethodField()
    table_talk_url = serializers.URLField(source='link', read_only=True)
    undrafted_faction = serializers.SerializerMethodField()
    undrafted_vagabond = serializers.SerializerMethodField()
    deck = serializers.SerializerMethodField()
    board_map = serializers.SerializerMethodField()
    random_suits = serializers.BooleanField(source='random_clearing', read_only=True)

    class Meta:
        model = Game
        fields = [
            'id', 'participants', 'tournament', 'hirelings', 'landmarks',
            'title', 'date_registered', 'date_modified', 'date_closed',
            'turn_timing', 'table_talk_url', 'undrafted_faction', 'undrafted_vagabond',
            'deck', 'board_map', 'random_suits',
        ]

    def get_tournament(self, obj):
        return str(obj.round) if obj.round else None

    def get_hirelings(self, obj):
        return [hireling.slug for hireling in obj.hirelings.all()]

    def get_landmarks(self, obj):
        return [landmark.slug for landmark in obj.landmarks.all()]

    def get_turn_timing(self, obj):
        return obj.type.lower() if obj.type else None

    def get_undrafted_faction(self, obj):
        return obj.undrafted_faction.slug if obj.undrafted_faction else None

    def get_undrafted_vagabond(self, obj):
        return obj.undrafted_vagabond.slug if obj.undrafted_vagabond else None

    def get_deck(self, obj):
        return obj.deck.slug if obj.deck else None

    def get_board_map(self, obj):
        return obj.map.slug if obj.map else None
