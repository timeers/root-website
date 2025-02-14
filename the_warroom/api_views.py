# api_views.py
from django.http import JsonResponse
from .models import Round
from the_gatehouse.models import Profile
from the_keep.models import Map, Faction, Deck, Vagabond, Landmark, Tweak, Hireling
from django.shortcuts import get_object_or_404


def get_options_for_tournament(request, pk):
    if request.user.is_authenticated:
        round = get_object_or_404(Round, id=pk)
        tournament = round.tournament

        if round.tournament.open_assets:
            maps = Map.objects.all()
            factions = Faction.objects.all()
            decks = Deck.objects.all()
            vagabonds = Vagabond.objects.all()
            landmarks = Landmark.objects.all()
            tweaks = Tweak.objects.all()
            hirelings = Hireling.objects.all()
        else:
            maps = tournament.maps
            factions = tournament.factions
            decks = tournament.decks
            vagabonds = tournament.vagabonds
            landmarks = tournament.landmarks
            tweaks = tournament.tweaks
            hirelings = tournament.hirelings
        if round.tournament.open_roster:
            players = Profile.objects.all()
        else:
            if round.players.count() > 0:
                players = round.players
            else:
                players = tournament.players
        platform = tournament.platform


    # Serialize the data
    tournament_data = {
        'id': round.id,
        'tournament': tournament.name,
        'round_name': round.name,
        'round_number': round.round_number,
                       }
    decks_data = [{'id': deck.id, 'name': f'{deck.title}'} for deck in decks.all()]
    maps_data = [{'id': map.id, 'name': f'{map.title}'} for map in maps.all()]
    factions_data = [{'id': faction.id, 'name': faction.title} for faction in factions.all()]
    vagabonds_data = [{'id': vagabond.id, 'name': vagabond.title} for vagabond in vagabonds.all()]
    landmarks_data = [{'id': landmark.id, 'name': landmark.title} for landmark in landmarks.all()]
    tweaks_data = [{'id': tweak.id, 'name': tweak.title} for tweak in tweaks.all()]
    hirelings_data = [{'id': hireling.id, 'name': hireling.title} for hireling in hirelings.all()]
    players_data = [{'id': player.id, 'name': f'{player.name} ({player.discord})'} for player in players.all()]

    # Return all the data in a single response
    return JsonResponse({
        'tournament': tournament_data,
        'decks': decks_data,
        'maps': maps_data,
        'factions': factions_data,
        'vagabonds': vagabonds_data,
        'landmarks': landmarks_data,
        'tweaks': tweaks_data,
        'hirelings': hirelings_data,
        'players': players_data,
        'platform': platform,
    })