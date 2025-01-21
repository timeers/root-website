from rest_framework import serializers
from .models import Post, Map, Deck, Landmark, Tweak, Hireling, Vagabond, Faction



class BasePostSerializer(serializers.ModelSerializer):
    designer_display_name = serializers.SerializerMethodField()
    absolute_url = serializers.SerializerMethodField()

    class Meta:
        fields = ['title', 'component', 'designer_display_name', 'absolute_url']

    def get_designer_display_name(self, obj):
        # Assuming designer is a related field, safely get the display name
        return obj.designer.display_name if obj.designer else None

    def get_absolute_url(self, obj):
        # Get the relative URL from the model's get_absolute_url method
        relative_url = obj.get_absolute_url()
        
        # Get the current request object (this is available in DRF views)
        request = self.context.get('request')
        if request:
            # Build the full URL (host + relative path)
            return request.build_absolute_uri(relative_url)
        else:
            # If no request object is available, fallback to just the relative URL
            return relative_url
class MapSerializer(BasePostSerializer):
    class Meta(BasePostSerializer.Meta):
        model = Map

class DeckSerializer(BasePostSerializer):
    class Meta(BasePostSerializer.Meta):
        model = Deck

class LandmarkSerializer(BasePostSerializer):
    class Meta(BasePostSerializer.Meta):
        model = Landmark

class TweakSerializer(BasePostSerializer):
    class Meta(BasePostSerializer.Meta):
        model = Tweak

class HirelingSerializer(BasePostSerializer):
    class Meta(BasePostSerializer.Meta):
        model = Hireling

class VagabondSerializer(BasePostSerializer):
    class Meta(BasePostSerializer.Meta):
        model = Vagabond

class FactionSerializer(BasePostSerializer):
    class Meta(BasePostSerializer.Meta):
        model = Faction
