# api_views.py
from django.http import JsonResponse
from .models import Round, AssetModeChoices, StageParticipant, TournamentPlayer
from the_gatehouse.models import Profile
from the_keep.models import Map, Faction, Deck, Vagabond, Landmark, Tweak, Hireling
from django.shortcuts import get_object_or_404


def get_tournament_asset_querysets(tournament):
    """Return a dict of querysets for each asset type, filtered by the tournament's asset_mode."""
    if tournament.asset_mode == AssetModeChoices.OPEN:
        assets = {
            'maps': Map.objects.all(),
            'factions': Faction.objects.all(),
            'decks': Deck.objects.all(),
            'vagabonds': Vagabond.objects.all(),
            'landmarks': Landmark.objects.all(),
            'tweaks': Tweak.objects.all(),
            'hirelings': Hireling.objects.all(),
        }
    elif tournament.asset_mode == AssetModeChoices.OFFICIAL:
        assets = {
            'maps': Map.objects.filter(official=True),
            'factions': Faction.objects.filter(official=True),
            'decks': Deck.objects.filter(official=True),
            'vagabonds': Vagabond.objects.filter(official=True),
            'landmarks': Landmark.objects.filter(official=True),
            'tweaks': Tweak.objects.filter(official=True),
            'hirelings': Hireling.objects.filter(official=True),
        }
    else:
        # SELECTED mode
        assets = {
            'maps': tournament.maps,
            'factions': tournament.factions,
            'decks': tournament.decks,
            'vagabonds': tournament.vagabonds,
            'landmarks': tournament.landmarks,
            'tweaks': tournament.tweaks,
            'hirelings': tournament.hirelings,
        }

    if not tournament.include_clockwork:
        assets['factions'] = assets['factions'].exclude(component="Clockwork")

    return assets


def get_options_for_tournament(request, pk):
    if request.user.is_authenticated:
        round = get_object_or_404(Round, id=pk)
        tournament = round.get_tournament()

        assets = get_tournament_asset_querysets(tournament)
        maps = assets['maps']
        factions = assets['factions']
        decks = assets['decks']
        vagabonds = assets['vagabonds']
        landmarks = assets['landmarks']
        tweaks = assets['tweaks']
        hirelings = assets['hirelings']

        if tournament.open_roster:
            players = Profile.objects.all()
        else:
            stage_players = Profile.objects.filter(
                tournament_participations__stage_participations__stage=round.stage,
                tournament_participations__stage_participations__status=StageParticipant.ParticipantStatus.ACTIVE,
            )
            if stage_players.exists():
                players = stage_players
            else:
                players = Profile.objects.filter(
                    tournament_participations__tournament=tournament,
                    tournament_participations__status=TournamentPlayer.StatusChoices.REGISTERED,
                )
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
