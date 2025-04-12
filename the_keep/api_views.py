# api_views.py
from django.http import JsonResponse
from .models import Deck, Faction, Map, Vagabond, Hireling, Landmark, Tweak, Post, PostTranslation
from .serializers import (
    MapSerializer, DeckSerializer, LandmarkSerializer, TweakSerializer, 
    HirelingSerializer, VagabondSerializer, FactionSerializer
)
from the_gatehouse.models import Profile
from rest_framework.response import Response
from rest_framework.decorators import api_view
from django.utils.translation import get_language

from django.db.models import OuterRef, Subquery, F, Value
from django.db.models.functions import Coalesce


def get_options_for_platform(request, platform):
    if request.user.is_authenticated:
        language_code = get_language()
        translated_title_subquery = PostTranslation.objects.filter(
            post=OuterRef('pk'),
            language__code=language_code
        ).values('translated_title')[:1]
        if platform == 'root_digital':
            # maps = Map.objects.filter(in_root_digital=True)
            # factions = Faction.objects.filter(in_root_digital=True)
            # decks = Deck.objects.filter(in_root_digital=True)
            # vagabonds = Vagabond.objects.filter(in_root_digital=True)
            # landmarks = Landmark.objects.filter(in_root_digital=True)
            # tweaks = Tweak.objects.filter(in_root_digital=True)
            # hirelings = Hireling.objects.filter(in_root_digital=True)

            maps = Map.objects.filter(in_root_digital=True).annotate(
                display_title=Coalesce(
                    Subquery(translated_title_subquery),
                    F('title')  # fallback to original title
                )
            )
            factions = Faction.objects.filter(in_root_digital=True).annotate(
                display_title=Coalesce(
                    Subquery(translated_title_subquery),
                    F('title')  # fallback to original title
                )
            )
            decks = Deck.objects.filter(in_root_digital=True).annotate(
                display_title=Coalesce(
                    Subquery(translated_title_subquery),
                    F('title')  # fallback to original title
                )
            )
            vagabonds = Vagabond.objects.filter(in_root_digital=True).annotate(
                display_title=Coalesce(
                    Subquery(translated_title_subquery),
                    F('title')  # fallback to original title
                )
            )
            landmarks = Landmark.objects.filter(in_root_digital=True).annotate(
                display_title=Coalesce(
                    Subquery(translated_title_subquery),
                    F('title')  # fallback to original title
                )
            )
            tweaks = Tweak.objects.filter(in_root_digital=True).annotate(
                display_title=Coalesce(
                    Subquery(translated_title_subquery),
                    F('title')  # fallback to original title
                )
            )
            hirelings = Hireling.objects.filter(in_root_digital=True).annotate(
                display_title=Coalesce(
                    Subquery(translated_title_subquery),
                    F('title')  # fallback to original title
                )
            )

        elif platform == 'in_person' or platform == 'tabletop_simulator':
            if request.user.profile.weird:
                # maps = Map.objects.filter(status__lte=request.user.profile.view_status)
                # factions = Faction.objects.filter(status__lte=request.user.profile.view_status)
                # decks = Deck.objects.filter(status__lte=request.user.profile.view_status)
                # vagabonds = Vagabond.objects.filter(status__lte=request.user.profile.view_status)
                # landmarks = Landmark.objects.filter(status__lte=request.user.profile.view_status)
                # tweaks = Tweak.objects.filter(status__lte=request.user.profile.view_status)
                # hirelings = Hireling.objects.filter(status__lte=request.user.profile.view_status)

                maps = Map.objects.filter(status__lte=request.user.profile.view_status).annotate(
                    display_title=Coalesce(
                        Subquery(translated_title_subquery),
                        F('title')  # fallback to original title
                    )
                )
                factions = Faction.objects.filter(status__lte=request.user.profile.view_status).annotate(
                    display_title=Coalesce(
                        Subquery(translated_title_subquery),
                        F('title')  # fallback to original title
                    )
                )
                decks = Deck.objects.filter(status__lte=request.user.profile.view_status).annotate(
                    display_title=Coalesce(
                        Subquery(translated_title_subquery),
                        F('title')  # fallback to original title
                    )
                )
                vagabonds = Vagabond.objects.filter(status__lte=request.user.profile.view_status).annotate(
                    display_title=Coalesce(
                        Subquery(translated_title_subquery),
                        F('title')  # fallback to original title
                    )
                )
                landmarks = Landmark.objects.filter(status__lte=request.user.profile.view_status).annotate(
                    display_title=Coalesce(
                        Subquery(translated_title_subquery),
                        F('title')  # fallback to original title
                    )
                )
                tweaks = Tweak.objects.filter(status__lte=request.user.profile.view_status).annotate(
                    display_title=Coalesce(
                        Subquery(translated_title_subquery),
                        F('title')  # fallback to original title
                    )
                )
                hirelings = Hireling.objects.filter(status__lte=request.user.profile.view_status).annotate(
                    display_title=Coalesce(
                        Subquery(translated_title_subquery),
                        F('title')  # fallback to original title
                    )
                )

            else:
                # maps = Map.objects.filter(official=True, status__lte=request.user.profile.view_status)
                # factions = Faction.objects.filter(official=True, status__lte=request.user.profile.view_status)
                # decks = Deck.objects.filter(official=True, status__lte=request.user.profile.view_status)
                # vagabonds = Vagabond.objects.filter(official=True, status__lte=request.user.profile.view_status)
                # landmarks = Landmark.objects.filter(official=True, status__lte=request.user.profile.view_status)
                # tweaks = Tweak.objects.filter(official=True, status__lte=request.user.profile.view_status)
                # hirelings = Hireling.objects.filter(official=True, status__lte=request.user.profile.view_status)

                maps = Map.objects.filter(official=True, status__lte=request.user.profile.view_status).annotate(
                    display_title=Coalesce(
                        Subquery(translated_title_subquery),
                        F('title')  # fallback to original title
                    )
                )
                factions = Faction.objects.filter(official=True, status__lte=request.user.profile.view_status).annotate(
                    display_title=Coalesce(
                        Subquery(translated_title_subquery),
                        F('title')  # fallback to original title
                    )
                )
                decks = Deck.objects.filter(official=True, status__lte=request.user.profile.view_status).annotate(
                    display_title=Coalesce(
                        Subquery(translated_title_subquery),
                        F('title')  # fallback to original title
                    )
                )
                vagabonds = Vagabond.objects.filter(official=True, status__lte=request.user.profile.view_status).annotate(
                    display_title=Coalesce(
                        Subquery(translated_title_subquery),
                        F('title')  # fallback to original title
                    )
                )
                landmarks = Landmark.objects.filter(official=True, status__lte=request.user.profile.view_status).annotate(
                    display_title=Coalesce(
                        Subquery(translated_title_subquery),
                        F('title')  # fallback to original title
                    )
                )
                tweaks = Tweak.objects.filter(official=True, status__lte=request.user.profile.view_status).annotate(
                    display_title=Coalesce(
                        Subquery(translated_title_subquery),
                        F('title')  # fallback to original title
                    )
                )
                hirelings = Hireling.objects.filter(official=True, status__lte=request.user.profile.view_status).annotate(
                    display_title=Coalesce(
                        Subquery(translated_title_subquery),
                        F('title')  # fallback to original title
                    )
                )

        else:
            maps = Map.objects.none()  # No maps for invalid platform
            factions = Faction.objects.none()
            decks = Deck.objects.none()
            vagabonds = Vagabond.objects.none()
            landmarks = Landmark.objects.none()
            tweaks = Tweak.objects.none()
            hirelings = Hireling.objects.none()

    else:
        maps = Map.objects.none()
        factions = Faction.objects.none()
        decks = Deck.objects.none()
        vagabonds = Vagabond.objects.none()
        landmarks = Landmark.objects.none()
        tweaks = Tweak.objects.none()
        hirelings = Hireling.objects.none()
    
    players = Profile.objects.all()

    # Serialize the data
    decks_data = [{'id': deck.id, 'name': f'{deck.display_title}'} for deck in decks]
    maps_data = [{'id': map.id, 'name': f'{map.display_title}'} for map in maps]
    factions_data = [{'id': faction.id, 'name': faction.display_title} for faction in factions]
    vagabonds_data = [{'id': vagabond.id, 'name': vagabond.display_title} for vagabond in vagabonds]
    landmarks_data = [{'id': landmark.id, 'name': landmark.display_title} for landmark in landmarks]
    tweaks_data = [{'id': tweak.id, 'name': tweak.display_title} for tweak in tweaks]
    hirelings_data = [{'id': hireling.id, 'name': hireling.display_title} for hireling in hirelings]

    if platform == 'root_digital':
        players_data = [{'id': player.id, 'name': f'{player.name} ({player.dwd})' if player.dwd else str(player)} for player in players.all()]
    else:
        players_data = [{'id': player.id, 'name': str(player)} for player in players.all()]
        
    # Return all the data in a single response
    return JsonResponse({
        'decks': decks_data,
        'maps': maps_data,
        'factions': factions_data,
        'vagabonds': vagabonds_data,
        'landmarks': landmarks_data,
        'tweaks': tweaks_data,
        'hirelings': hirelings_data,
        'players': players_data,
    })


# API search view
@api_view(['GET'])
def search_posts(request):
    search_query = request.GET.get('search', None)  # Get the 'search' kwarg from the URL
    
    if search_query:
        # First, attempt to find posts with an exact match on the title
        exact_match = Post.objects.filter(title__iexact=search_query).first()

        if exact_match:
            post = exact_match
        else:
            # If no exact match is found, search for the first post containing the query in the title (case-insensitive)
            post = Post.objects.filter(title__icontains=search_query).first()

        
        if post:

            # Define the serializer mapping based on component type
            component_serializer_mapping = {
                "Map": MapSerializer,
                "Deck": DeckSerializer,
                "Landmark": LandmarkSerializer,
                "Tweak": TweakSerializer,
                "Hireling": HirelingSerializer,
                "Vagabond": VagabondSerializer,
                "Faction": FactionSerializer,
                "Clockwork": FactionSerializer,  # Mapping Clockwork to FactionSerializer
            }

            # Get the correct serializer for the component
            SerializerClass = component_serializer_mapping.get(post.component)

            if SerializerClass:
                # If serializer is found, serialize the instance
                serializer = SerializerClass(post)
                return Response(serializer.data)
            else:
                # Handle the case where the component type does not exist
                return Response({"message": "No matching component found"}, status=404)
        else:
            return Response({"message": "No matching post found"}, status=404)
    else:
        return Response({"message": "No search query provided"}, status=400)