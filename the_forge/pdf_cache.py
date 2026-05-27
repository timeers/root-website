import hashlib
import json

from django.core.cache import cache
from django.core.serializers.json import DjangoJSONEncoder
from django.forms.models import model_to_dict


CACHE_PREFIX = 'forge_pdf:v8'
PDF_CACHE_TTL = 60 * 60 * 24
PDF_CACHE_MAX_BYTES = 25 * 1024 * 1024


# Fields that change as a side-effect of generating a preview (or get bumped
# by Django auto_now hooks). Including them in the fingerprint would make the
# fingerprint self-referential: saving the preview mutates the fingerprint
# inputs, so the next call would compute a different digest and invalidate
# the cache we just populated.
_FINGERPRINT_EXCLUDE_FIELDS = frozenset({
    'preview_fingerprint',
    'preview_version',
    'image_preview',
    'last_generated',
    'last_updated',
    'decree_fingerprint',
    'decree_preview',
    'snap_points',
    'decree_slide_pts',
    'ability_bar_extra_h_pts',
})


# Whitelist of ForgedFaction fields that actually affect the rendered front,
# back, or setup-card PDFs. Everything else on ForgedFaction (icon, markers,
# metadata, sync/link state) is irrelevant to those three renders, so we only
# include these in the fingerprint. The components-sheet fingerprint has its
# own explicit field list and doesn't go through this helper.
_FACTION_RENDER_FIELDS = (
    'faction_name',
    'color',
    'secondary_color',
    'background_preset',
    'background_image',
    'repeat_background_image',
    'background_tile_size',
    'language',
)


def _faction_payload_for_render(faction):
    """Serialize only the ForgedFaction fields that influence sheet/back/card
    PDF output. Markers, metadata, and publish state are deliberately omitted
    so e.g. changing the icon color doesn't invalidate those previews."""
    if faction is None:
        return None
    out = {}
    for name in _FACTION_RENDER_FIELDS:
        v = getattr(faction, name, None)
        if hasattr(v, 'name'):  # ImageField
            out[name] = v.name or ''
        elif hasattr(v, 'pk'):   # FK (Language)
            out[name] = v.pk
        else:
            out[name] = v
    return out


def _serialize_instance(obj):
    if obj is None:
        return None
    data = model_to_dict(obj)
    for k in list(data.keys()):
        if k in _FINGERPRINT_EXCLUDE_FIELDS:
            del data[k]
            continue
        v = data[k]
        if hasattr(v, 'name'):
            data[k] = v.name or ''
    return data


def _serialize_qs(qs, order_by=('pk',)):
    return [_serialize_instance(o) for o in qs.order_by(*order_by)]


def _digest(payload):
    blob = json.dumps(payload, sort_keys=True, cls=DjangoJSONEncoder).encode('utf-8')
    return hashlib.sha256(blob).hexdigest()[:16]


def fingerprint_faction(faction):
    sheet = getattr(faction, 'faction_sheet', None)
    back = getattr(faction, 'faction_back', None)
    payload = {
        'faction': _serialize_instance(faction),
        'sheet': _sheet_payload(sheet) if sheet else None,
        'back': _back_payload(back) if back else None,
    }
    return _digest(payload)


def fingerprint_sheet(sheet):
    return _digest(_sheet_payload(sheet))


def fingerprint_back(back):
    return _digest(_back_payload(back))


def fingerprint_decree(sheet):
    faction = sheet.faction
    decree = sheet.decrees.first()
    slot_count = decree.card_slots.count() if decree else 0
    payload = {
        'include_decree': sheet.include_decree,
        'slot_count': slot_count,
        'background_preset': faction.background_preset,
        'background_image': faction.background_image.name if faction.background_image else '',
        'repeat_background_image': faction.repeat_background_image,
        'background_tile_size': faction.background_tile_size,
        'color': faction.color,
    }
    return _digest(payload)


def fingerprint_setup_card(card):
    return _digest({
        'faction': _faction_payload_for_render(card.faction),
        'card': _serialize_instance(card),
        'steps': _serialize_qs(card.setup_steps.all(), order_by=('number', 'pk')),
    })


def fingerprint_components_sheet(faction):
    card = getattr(faction, 'setup_card', None)
    pieces = []
    for p in faction.pieces.filter(type__in=('B', 'T')).order_by('type', 'pk'):
        pieces.append((p.pk, p.front_version, p.back_version, p.quantity, p.type))
    return _digest({
        'card_fp': fingerprint_setup_card(card) if card else '',
        'markers_version': faction.markers_version,
        'vp_marker': faction.vp_marker.name if faction.vp_marker else '',
        'rel_marker': faction.relationship_marker.name if faction.relationship_marker else '',
        'print_backs': bool(faction.print_component_backs),
        'pieces': pieces,
    })


def fingerprint_cards(faction):
    """Fingerprint every ForgedCard belonging to card-type Pieces on this
    faction. Includes file mtime so re-uploading an image (which may keep the
    same path on overwrite) still busts the cache."""
    import os
    from .models import ForgedDeckGroup
    payload = []
    groups = (
        ForgedDeckGroup.objects
        .filter(piece__faction=faction, piece__type='C')
        .order_by('piece__pk')
    )
    for group in groups:
        group_entry = {
            'group_pk': group.pk,
            'group_name': group.name,
            'back_image': group.back_image.name if group.back_image else '',
            'cards': [],
        }
        for card in group.cards.all().order_by('order'):
            path = getattr(card.front_image, 'path', None) if card.front_image else None
            mtime = os.path.getmtime(path) if path and os.path.exists(path) else 0
            group_entry['cards'].append((
                card.pk, card.order, card.name or '', card.text or '',
                card.front_image.name if card.front_image else '',
                mtime,
            ))
        payload.append(group_entry)
    return _digest({'groups': payload})


def _sheet_payload(sheet):
    steps = list(sheet.phase_steps.all().order_by('phase', 'number', 'pk'))
    payload = {
        'faction': _faction_payload_for_render(sheet.faction),
        'sheet': _serialize_instance(sheet),
        'character_images': _serialize_qs(sheet.character_images.all(), order_by=('order', 'pk')),
        'custom_inline_images': _serialize_qs(sheet.custom_inline_images.all(), order_by=('slot', 'pk')),
        'abilities': _serialize_qs(sheet.abilities.all(), order_by=('order', 'pk')),
        'content_boxes': _serialize_qs(sheet.content_boxes.all(), order_by=('order', 'pk')),
        'card_piles': _serialize_qs(sheet.card_piles.all(), order_by=('number', 'pk')),
        'decrees': [],
        'phase_steps': [],
    }
    for decree in sheet.decrees.all().order_by('pk'):
        payload['decrees'].append({
            'decree': _serialize_instance(decree),
            'card_slots': _serialize_qs(decree.card_slots.all(), order_by=('number', 'pk')),
        })
    for step in steps:
        payload['phase_steps'].append({
            'step': _serialize_instance(step),
            'actions': _serialize_qs(step.actions.all(), order_by=('order', 'pk')),
            'boxes': _serialize_qs(step.boxes.all(), order_by=('order', 'pk')),
            'tracks': [_track_payload(t) for t in step.tracks.all().order_by('order', 'pk')],
            'legends': [_legend_payload(L) for L in step.legends.all().order_by('order', 'pk')],
            'scales': [_scale_payload(s) for s in step.scales.all().order_by('order', 'pk')],
        })
    return payload


def _track_payload(track):
    return {
        'track': _serialize_instance(track),
        'slots': _serialize_qs(track.slots.all(), order_by=('row', 'column', 'pk')),
    }


def _legend_payload(legend):
    return {
        'legend': _serialize_instance(legend),
        'rows': _serialize_qs(legend.rows.all(), order_by=('order', 'pk')),
    }


def _scale_payload(scale):
    return {
        'scale': _serialize_instance(scale),
        'rows': _serialize_qs(scale.rows.all(), order_by=('order', 'pk')),
    }


def _back_payload(back):
    return {
        'faction': _faction_payload_for_render(back.faction),
        'back': _serialize_instance(back),
        'pieces': _serialize_qs(back.faction.pieces.all(), order_by=('pk',)),
        'setup_steps': _serialize_qs(back.setup_steps.all(), order_by=('number', 'pk')),
    }


def cache_key(prefix, pk, fingerprint):
    return f'{CACHE_PREFIX}:{prefix}:{pk}:{fingerprint}'


def get_or_build(key, builder):
    cached = cache.get(key)
    if cached is not None:
        return cached
    data = builder()
    if len(data) <= PDF_CACHE_MAX_BYTES:
        cache.set(key, data, timeout=PDF_CACHE_TTL)
    return data
