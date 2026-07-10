# api_views.py
from django.http import JsonResponse
from django.db.models import Q
from .models import Round, StageParticipant, TournamentPlayer, Tournament, PlatformChoices
from the_gatehouse.models import Profile
from django.shortcuts import get_object_or_404


def search_profiles(request):
    """JSON endpoint: search profiles by display name / discord for pickers."""
    if not request.user.is_authenticated:
        return JsonResponse({'results': []})
    query = request.GET.get('q', '').strip()
    if not query:
        return JsonResponse({'results': []})
    profiles = Profile.objects.filter(
        Q(display_name__icontains=query) | Q(discord__icontains=query)
    ).order_by('display_name')[:10]
    return JsonResponse({
        'results': [
            {
                'id': p.id,
                'display_name': p.display_name,
                'image_url': p.image.url if p.image else '',
            }
            for p in profiles
        ]
    })


def get_options_for_tournament(request, pk):
    if request.user.is_authenticated:
        round = get_object_or_404(Round, id=pk)
        tournament = round.get_tournament()

        assets = tournament.get_asset_querysets()

        # When the tournament doesn't fix a platform, the game recorder can pick
        # one at record time. If they pick Root Digital, additionally restrict
        # the tournament's allowed assets to those available digitally
        # (intersection). get_asset_querysets already applies this when the
        # tournament itself is Root Digital, so this only matters otherwise.
        requested_platform = request.GET.get('platform')
        if requested_platform == PlatformChoices.DWD and tournament.platform != PlatformChoices.DWD:
            assets = {key: qs.filter(in_root_digital=True) for key, qs in assets.items()}

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

            # Under GUILD recording access, any member of the linked guild may
            # record games even if they aren't a stage participant or registered,
            # so include them alongside the participation-based list above.
            if (tournament.recording_access == Tournament.RecordingAccessTypes.GUILD
                    and tournament.guild_id):
                guild_members = Profile.objects.filter(guilds__pk=tournament.guild_id)
                players = (players | guild_members).distinct()
        platform = tournament.platform


    # Serialize the data
    tournament_data = {
        'id': round.id,
        'tournament': tournament.name,
        'round_name': round.name,
        'round_number': round.round_number,
                       }
    decks_data = [{'id': deck.id, 'name': f'{deck.title}', 'icon_url': deck.card_image.url if deck.card_image else None} for deck in decks.all()]
    maps_data = [{'id': map.id, 'name': f'{map.title}', 'icon_url': map.board_image.url if map.board_image else None} for map in maps.all()]
    factions_data = [{'id': faction.id, 'name': faction.title, 'icon_url': faction.small_icon.url if faction.small_icon else None} for faction in factions.all()]
    vagabonds_data = [{'id': vagabond.id, 'name': vagabond.title} for vagabond in vagabonds.all()]
    captains_data = [{'id': vagabond.id, 'name': vagabond.title} for vagabond in vagabonds.filter(captain=True)]
    landmarks_data = [{'id': landmark.id, 'name': landmark.title} for landmark in landmarks.all()]
    tweaks_data = [{'id': tweak.id, 'name': tweak.title} for tweak in tweaks.all()]
    hirelings_data = [{'id': hireling.id, 'name': hireling.title} for hireling in hirelings.all()]
    players_data = [{'id': player.id, 'name': f'{player.name} ({player.discord})', 'avatar_url': player.image.url if player.image else None} for player in players.all()]

    # Return all the data in a single response
    return JsonResponse({
        'tournament': tournament_data,
        'decks': decks_data,
        'maps': maps_data,
        'factions': factions_data,
        'vagabonds': vagabonds_data,
        'captains': captains_data,
        'landmarks': landmarks_data,
        'tweaks': tweaks_data,
        'hirelings': hirelings_data,
        'players': players_data,
        'platform': platform,
        'link_required': tournament.link_required,
        'box_score_required': tournament.box_score_required,
        'open_roster': tournament.open_roster,
    })
