"""Convert a forge TTS save into one that only references keep-owned files.

Forge assets live at pinned paths that are overwritten in place (and vanish
with the forge faction), so the keep's tts_json must never point at them. The
forge sync first copies every asset into the keep (images, pieces, decks,
TTSAssets); this module then maps each forge URL to its keep copy and rewrites
the save. Anything left pointing at forge media is reported, never stored.
"""
import copy

from django.conf import settings

from the_keep.services.tts import generate_sprite_sheet, tts_image_url


def _safe_related(obj, attr):
    try:
        return getattr(obj, attr)
    except Exception:  # RelatedObjectDoesNotExist
        return None


def build_url_map(forged, faction, site_request):
    """{forge_url: keep_url} for every asset pair. Both sides go through
    tts_image_url with the same request so the {verifycache} prefix and base
    URL match the strings in the built save exactly.

    Raises ValueError when a sprite sheet can't be generated (pixel cap)."""
    from ..forms_publish import build_deck_plan, build_piece_plan, forge_tts_asset_sources

    pairs = []
    sheet = _safe_related(forged, 'faction_sheet')
    back = _safe_related(forged, 'faction_back')
    card = _safe_related(forged, 'setup_card')
    if sheet:
        pairs.append((sheet.image_preview, faction.board_image))
    if back:
        pairs.append((back.image_preview, faction.board_2_image))
    if card:
        pairs.append((card.image_preview, faction.card_image))
    pairs.append((forged.faction_icon, faction.small_icon))

    piece_plan = build_piece_plan(forged)
    for entry in piece_plan:
        keep_piece = entry['keep_piece']
        if keep_piece is None:
            continue
        forge_piece = entry['forge_piece']
        pairs.append((forge_piece.small_icon, keep_piece.small_icon))
        pairs.append((forge_piece.back_image, keep_piece.back_image))

    keep_assets = {a.role: a for a in faction.tts_assets.all()}
    for role, _label, forge_image, _fingerprint in forge_tts_asset_sources(forged):
        keep_asset = keep_assets.get(role)
        if keep_asset is not None:
            pairs.append((forge_image, keep_asset.image))

    url_map = {}
    for forge_file, keep_file in pairs:
        if forge_file and keep_file:
            url_map[tts_image_url(forge_file, request=site_request)] = tts_image_url(keep_file, request=site_request)

    for entry in build_deck_plan(forged, piece_plan):
        keep_group = entry['keep_group']
        if keep_group is None:
            continue
        forge_group = entry['forge_group']
        if forge_group.back_image and keep_group.back_image:
            url_map[tts_image_url(forge_group.back_image, request=site_request)] = (
                tts_image_url(keep_group.back_image, request=site_request))
        # Same cards split the same way, so sprite sheets pair by deck_index.
        # generate_sprite_sheet returns the URL TTSSpriteDeck puts in FaceURL.
        keep_decks = {d.deck_index: d for d in keep_group.decks.all()}
        for forge_deck in forge_group.decks.all():
            keep_deck = keep_decks.get(forge_deck.deck_index)
            if keep_deck is None:
                continue
            forge_sheet = generate_sprite_sheet(forge_deck)
            keep_sheet = generate_sprite_sheet(keep_deck)
            if forge_sheet and keep_sheet:
                url_map[tts_image_url(forge_sheet, request=site_request)] = (
                    tts_image_url(keep_sheet, request=site_request))
    return url_map


def _replace_strings(value, url_map):
    if isinstance(value, str):
        return url_map.get(value, value)
    if isinstance(value, list):
        return [_replace_strings(v, url_map) for v in value]
    if isinstance(value, dict):
        return {k: _replace_strings(v, url_map) for k, v in value.items()}
    return value


def convert_save(save, url_map):
    """A deep copy of `save` with every string found in url_map replaced."""
    return _replace_strings(copy.deepcopy(save), url_map)


def _contains(value, needle):
    if isinstance(value, str):
        return needle in value
    if isinstance(value, list):
        return any(_contains(v, needle) for v in value)
    if isinstance(value, dict):
        return any(_contains(v, needle) for v in value.values())
    return False


def find_forge_urls(save):
    """Nicknames of top-level objects that still reference forge media."""
    needle = f"{settings.MEDIA_URL.rstrip('/')}/forge/"
    return [
        obj.get('Nickname') or obj.get('Name') or '?'
        for obj in save.get('ObjectStates', [])
        if _contains(obj, needle)
    ]
