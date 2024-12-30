# api_views.py
from django.http import JsonResponse
from .models import Deck, Faction, Map, Vagabond, Hireling, Landmark, Tweak
from the_gatehouse.models import Profile


def get_options_for_platform(request, platform):
    if request.user.is_authenticated:
        if platform == 'root_digital':
            maps = Map.objects.filter(in_root_digital=True)
            factions = Faction.objects.filter(in_root_digital=True)
            decks = Deck.objects.filter(in_root_digital=True)
            vagabonds = Vagabond.objects.filter(in_root_digital=True)
            landmarks = Landmark.objects.filter(in_root_digital=True)
            tweaks = Tweak.objects.filter(in_root_digital=True)
            hirelings = Hireling.objects.filter(in_root_digital=True)
        elif platform == 'in_person' or platform == 'tabletop_simulator':
            if request.user.profile.weird:
                maps = Map.objects.all()
                factions = Faction.objects.all()
                decks = Deck.objects.all()
                vagabonds = Vagabond.objects.all()
                landmarks = Landmark.objects.all()
                tweaks = Tweak.objects.all()
                hirelings = Hireling.objects.all()
            else:
                maps = Map.objects.filter(official=True)
                factions = Faction.objects.filter(official=True)
                decks = Deck.objects.filter(official=True)
                vagabonds = Vagabond.objects.filter(official=True)
                landmarks = Landmark.objects.filter(official=True)
                tweaks = Tweak.objects.filter(official=True)
                hirelings = Hireling.objects.filter(official=True)
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
    decks_data = [{'id': deck.id, 'name': f'{deck.title}'} for deck in decks]
    maps_data = [{'id': map.id, 'name': f'{map.title}'} for map in maps]
    factions_data = [{'id': faction.id, 'name': faction.title} for faction in factions]
    vagabonds_data = [{'id': vagabond.id, 'name': vagabond.title} for vagabond in vagabonds]
    landmarks_data = [{'id': landmark.id, 'name': landmark.title} for landmark in landmarks]
    tweaks_data = [{'id': tweak.id, 'name': tweak.title} for tweak in tweaks]
    hirelings_data = [{'id': hireling.id, 'name': hireling.title} for hireling in hirelings]
    players_data = [{'id': player.id, 'name': f'{player.name} ({player.discord})'} for player in players.all()]

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