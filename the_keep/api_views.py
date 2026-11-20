# api_views.py
from django.http import JsonResponse
from .models import Deck, Faction, Map, Vagabond, Hireling, Landmark, Tweak, Post, PostTranslation, StatusChoices
from .serializers import (
    MapSerializer, DeckSerializer, LandmarkSerializer, TweakSerializer, 
    HirelingSerializer, VagabondSerializer, FactionSerializer
)
from the_gatehouse.models import Profile
from rest_framework.response import Response
from rest_framework.decorators import api_view
from rest_framework.views import APIView
from rest_framework.throttling import ScopedRateThrottle
from django.shortcuts import get_object_or_404
import hashlib
import json
import os
import re
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.http import HttpResponse
from django.templatetags.static import static
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
    decks_data = [{'id': deck.id, 'name': f'{deck.display_title}', 'icon_url': deck.card_image.url if deck.card_image else None} for deck in decks]
    maps_data = [{'id': map.id, 'name': f'{map.display_title}', 'icon_url': map.board_image.url if map.board_image else None} for map in maps]
    factions_data = [{'id': faction.id, 'name': faction.display_title, 'icon_url': faction.small_icon.url if faction.small_icon else None} for faction in factions]
    vagabonds_data = [{'id': vagabond.id, 'name': vagabond.display_title} for vagabond in vagabonds]
    captains_data = [{'id': vagabond.id, 'name': vagabond.display_title} for vagabond in vagabonds if vagabond.captain]
    landmarks_data = [{'id': landmark.id, 'name': landmark.display_title} for landmark in landmarks]
    tweaks_data = [{'id': tweak.id, 'name': tweak.display_title} for tweak in tweaks]
    hirelings_data = [{'id': hireling.id, 'name': hireling.display_title} for hireling in hirelings]

    if platform == 'root_digital':
        players_data = [{'id': player.id, 'name': f'{player.name} ({player.dwd})' if player.dwd else str(player), 'avatar_url': player.image.url if player.image else None} for player in players.all()]
    else:
        players_data = [{'id': player.id, 'name': str(player), 'avatar_url': player.image.url if player.image else None} for player in players.all()]
        
    # Return all the data in a single response
    return JsonResponse({
        'decks': decks_data,
        'maps': maps_data,
        'factions': factions_data,
        'vagabonds': vagabonds_data,
        'captains': captains_data,
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


# ── Tabletop Simulator loader ───────────────────────────────────────────────
# Read-only and public: the TTS loader object can't log in. Throttled per
# caller because the project default (UserRateThrottle) ignores anonymous
# requests entirely.

# Posts the TTS Library lists: publicly active only (no Abandoned / Rejected /
# Submitted). Enum members, not ints -- status values are strings ('1').
TTS_LISTED_STATUSES = (
    StatusChoices.STABLE, StatusChoices.TESTING,
    StatusChoices.DEVELOPMENT, StatusChoices.INACTIVE,
)


class TTSLoaderView(APIView):
    authentication_classes = []
    permission_classes = []
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'tts_loader'


def _site_url(image):
    from .services.tts import SiteURLRequest
    return SiteURLRequest().build_absolute_uri(image.url) if image else ''


def _number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


# Tags the RTT coordinator's shuffleMaps acts on; a map carrying any of them
# gets random set-up (ruins, suits, ...) when the loader asks for a shuffle.
MAP_SHUFFLE_TAGS = {'Ruin', 'Dummy', 'Clearing Marker', 'Shuffleable', 'City Marker', 'Tool',
                    'Clearing 1', 'Clearing 2', 'Clearing 3', 'Clearing 4', 'Clearing 5'}
DECK_OBJECT_NAMES = {'Deck', 'DeckCustom', 'Card', 'CardCustom'}


def _add_tags(obj, *tags):
    """A copy of `obj` with `tags` merged into its Tags (no duplicates)."""
    obj = dict(obj)
    current = list(obj.get('Tags') or [])
    for tag in tags:
        if tag not in current:
            current.append(tag)
    obj['Tags'] = current
    return obj


def _without_tag(obj, tag):
    obj = dict(obj)
    obj['Tags'] = [t for t in (obj.get('Tags') or []) if t != tag]
    return obj


def _rtt_ready_objects(title, states, component, fixed_clearings=False):
    """The objects as served, tagged so the RTT coordinator handles them like its
    own map or deck. Returns (objects, map_board index or None). Only the served
    copy is changed; the library deletes itself before spawn callbacks run, so
    tags have to travel in the JSON."""
    if component == 'Map' and states:
        board = max(range(len(states)), key=lambda i: (len(states[i].get('AttachedSnapPoints') or []), -i))
        out = []
        for i, obj in enumerate(states):
            if fixed_clearings:
                obj = _without_tag(obj, 'Clearing Marker')   # fixed suits: never shuffled
            obj = _add_tags(obj, 'Map Object')
            if i == board:
                obj = _add_tags(obj, 'RTT Map', f'RTT Map: {title}')
            out.append(obj)
        return out, board + 1
    if component == 'Deck':
        return [_add_tags(o, 'Deck Object') if o.get('Name') in DECK_OBJECT_NAMES else o
                for o in states], None
    return list(states), None


def _tts_objects_payload(title, tts_json, component='', slug='', fixed_clearings=False):
    """Each object pre-encoded as a string so the Lua side hands it straight to
    spawnObjectJSON (MoonSharp's JSON is very slow on big deck blobs), with its
    position pulled out here so Lua never decodes an object at all."""
    states, map_board = _rtt_ready_objects(title, tts_json.get('ObjectStates', []), component,
                                           fixed_clearings=fixed_clearings)
    objects = []
    vp_rows = []
    nicknames = [(o.get('Nickname') or '') for o in states]
    for obj in states:
        transform = obj.get('Transform') or {}
        position = {
            'x': _number(transform.get('posX')),
            'y': _number(transform.get('posY')),
            'z': _number(transform.get('posZ')),
        }
        objects.append({**position, 'json': json.dumps(obj)})
        # A "<faction> VP" marker means this item is a playable faction; the
        # loader adds an RTT VP Panel for it when the RTT coordinator is present
        # (the panel matches its marker by exactly this name).
        nickname = obj.get('Nickname') or ''
        if nickname.endswith(' VP') and len(nickname) > 3:
            row = nickname[:-3]
            # 1-based index of this faction's board ("<row> - Faction Board") in
            # `objects`, so the loader can hand its GUID to the RTT coordinator.
            board_name = f'{row} - Faction Board'
            board = nicknames.index(board_name) + 1 if board_name in nicknames else None
            vp_rows.append({'row': row, **position, 'board': board})
    payload = {'title': title, 'component': component or '', 'slug': slug or '',
               'objects': objects, 'vp_rows': vp_rows}
    if component == 'Map':
        payload['map_board'] = map_board
        payload['shuffle'] = any(MAP_SHUFFLE_TAGS & set(o.get('Tags') or []) for o in states)
        # Clearings / forests / relics as data, like the RTT coordinator's own
        # tables -- see tts/BOARD_LAYOUT.md.
        if isinstance(tts_json.get('BoardLayout'), dict):
            payload['layout'] = tts_json['BoardLayout']
    return payload


class TTSCatalogView(TTSLoaderView):
    """Everything the loader can spawn, without the blobs."""

    def get(self, request):
        from the_forge.models import ForgedFaction

        keep_posts = (
            Post.objects.filter(tts_json__isnull=False, status__in=TTS_LISTED_STATUSES)
            .select_related('designer')
            .only('slug', 'title', 'component', 'status', 'designers_list', 'small_icon', 'designer__display_name')
            # Status codes are '1'..'4', so this groups Stable, Testing,
            # Development, Inactive -- the loader adds a header per group.
            .order_by('status', 'component', 'title')
        )
        forge_factions = (
            ForgedFaction.objects.filter(tts_status=ForgedFaction.TTSStatus.APPROVED, tts_json__isnull=False)
            .select_related('designer')
            .only('pk', 'faction_name', 'component', 'faction_icon', 'designer__display_name')
            .order_by('faction_name')
        )
        return Response({
            'keep': [{
                'slug': post.slug,
                'title': post.title,
                'component': post.component or '',
                'status': post.status,
                'status_label': post.get_status_display(),
                'designers': post.designers_list or (post.designer.display_name if post.designer else ''),
                'icon': _site_url(post.small_icon),
            } for post in keep_posts],
            'forge': [{
                'id': forged.pk,
                'title': forged.faction_name,
                'component': forged.component,
                'designer': forged.designer.display_name if forged.designer else '',
                'icon': _site_url(forged.faction_icon),
            } for forged in forge_factions],
        })


class TTSKeepDetailView(TTSLoaderView):
    def get(self, request, slug):
        post = get_object_or_404(
            Post.objects.filter(tts_json__isnull=False, status__in=TTS_LISTED_STATUSES)
            .only('title', 'slug', 'component', 'tts_json'),
            slug=slug,
        )
        fixed_clearings = False
        if post.component == Post.ComponentChoices.MAP:
            fixed_clearings = Map.objects.filter(pk=post.pk).values_list('fixed_clearings', flat=True).first() or False
        return Response(_tts_objects_payload(post.title, post.tts_json, component=post.component,
                                             slug=post.slug, fixed_clearings=fixed_clearings))


class TTSForgeDetailView(TTSLoaderView):
    def get(self, request, pk):
        from the_forge.models import ForgedFaction

        forged = get_object_or_404(
            ForgedFaction.objects.filter(tts_status=ForgedFaction.TTSStatus.APPROVED, tts_json__isnull=False)
            .only('faction_name', 'component', 'tts_json'),
            pk=pk,
        )
        return Response(_tts_objects_payload(forged.faction_name, forged.tts_json,
                                             component=forged.component))


# ── TTS Library object: served straight from tts/loader.lua ─────────────────
# The repo file is the single source. These endpoints serve it with its version
# and BASE_URL filled in, and wrap it in a ready-made object; objects built that
# way update themselves on load (see checkForUpdate in the script). Unlisted:
# nothing on the site links here yet.

_LOADER_VERSION_LINE = re.compile(r'^LOADER_VERSION = "[^"]*"$', re.MULTILINE)
_LOADER_BASE_URL_LINE = re.compile(r'^BASE_URL = "[^"]*"$', re.MULTILINE)
_loader_cache = {}   # (path, mtime) -> (version, raw text)


def _loader_source():
    """(version, raw text) of tts/loader.lua, re-read only when the file changes."""
    path = os.path.join(settings.BASE_DIR, 'tts', 'loader.lua')
    key = (str(path), os.path.getmtime(path))
    cached = _loader_cache.get(key)
    if cached is None:
        with open(path, encoding='utf-8') as fh:
            raw = fh.read()
        # Each marker must appear exactly once, or a served script could never
        # match its advertised version and the self-update would loop.
        for pattern, name in ((_LOADER_VERSION_LINE, 'LOADER_VERSION'), (_LOADER_BASE_URL_LINE, 'BASE_URL')):
            if len(pattern.findall(raw)) != 1:
                raise ImproperlyConfigured(f'tts/loader.lua needs exactly one top-level {name} = "..." line')
        version = hashlib.sha256(raw.encode('utf-8')).hexdigest()[:12]
        _loader_cache.clear()
        cached = _loader_cache[key] = (version, raw)
    return cached


def _loader_script(request):
    """(version, script) with LOADER_VERSION and BASE_URL filled in.

    BASE_URL is always settings.SITE_URL (production): TTS can't reliably load
    the script, data or images from a local dev server. So in DEBUG the served
    script keeps LOADER_VERSION = "dev" -- a dev-downloaded object runs your
    local script against production data and never self-updates (which would
    swap in production's script). Re-download it to test another change."""
    version, raw = _loader_source()
    base_url = settings.SITE_URL.rstrip('/')
    served_version = 'dev' if settings.DEBUG else version
    script = _LOADER_VERSION_LINE.sub(f'LOADER_VERSION = "{served_version}"', raw, count=1)
    script = _LOADER_BASE_URL_LINE.sub(f'BASE_URL = "{base_url}"', script, count=1)
    return version, script


class TTSLoaderVersionView(TTSLoaderView):
    def get(self, request):
        version, _script = _loader_script(request)
        return Response({'version': version})


class TTSLoaderScriptView(TTSLoaderView):
    def get(self, request):
        _version, script = _loader_script(request)
        return HttpResponse(script, content_type='text/plain; charset=utf-8')


class TTSLoaderObjectView(TTSLoaderView):
    """A TTS Save Object file: one locked tile carrying the current script.
    Drop it in Tabletop Simulator's Saved Objects folder."""

    def get(self, request):
        from .services.tts import SiteURLRequest, generate_tts_guid, wrap_tts_save

        _version, script = _loader_script(request)
        tile = {
            "GUID": generate_tts_guid(),
            "Name": "Custom_Tile",
            "Nickname": "Root Database Library",
            "Description": "Search json objects from therootdatabase.com.",
            # Same footprint as the TTS Faction Board (scale 15.5, 0.1 thick):
            # the background has the board's ~1.31 aspect, and the panel in
            # tts/loader.lua is sized to this tile's ~240 x 180 UI area.
            "Transform": {
                "posX": 0.0, "posY": 1.0, "posZ": 0.0,
                "rotX": 0.0, "rotY": 0.0, "rotZ": 0.0,
                "scaleX": 15.5, "scaleY": 1.0, "scaleZ": 15.5,
            },
            "Locked": True,
            "CustomImage": {
                # Self-hosted on SITE_URL (fingerprinted in production), so TTS
                # refetches it if replaced. Dev downloads point at production too.
                "ImageURL": SiteURLRequest().build_absolute_uri(static('images/tts/library_background.jpg')),
                "ImageSecondaryURL": request.build_absolute_uri(static('images/tts/library_background.jpg')),
                "ImageScalar": 1.0,
                "WidthScale": 0.0,
                "CustomTile": {"Type": 0, "Thickness": 0.1, "Stackable": False, "Stretch": True},
            },
            "LuaScript": script,
            "LuaScriptState": "",
            "XmlUI": "",
        }
        response = HttpResponse(
            json.dumps(wrap_tts_save([tile], save_name='Root Database Library'), indent=2),
            content_type='application/json',
        )
        response['Content-Disposition'] = 'attachment; filename="Root Database Library.json"'
        return response


class TTSVPPanelView(TTSLoaderView):
    """The Root Tabletop Tournament "VP Panel" object, unchanged except that its
    saved state is the placeholder "__VP_ROW__" (the loader swaps in the
    faction) and its GUID is dropped. It only works with the RTT coordinator,
    so the loader only spawns it when that coordinator is on the table."""

    def get(self, request):
        path = os.path.join(settings.BASE_DIR, 'tts', 'vp_panel.json')
        with open(path, encoding='utf-8') as fh:
            panel = json.load(fh)
        return HttpResponse(json.dumps(panel), content_type='text/plain; charset=utf-8')
