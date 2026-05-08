import os
import uuid

from django.conf import settings


def upload_path(slug_parts, filename, force_ext='webp'):
    """Build a relative MEDIA_ROOT path under forge/factions/<slug>/...

    Pinned filenames overwrite the previous file at that path so each
    faction has a single canonical file per slot.
    """
    slug_parts = [str(part) for part in slug_parts if part]
    folder = os.path.join('forge', 'factions', *slug_parts)
    os.makedirs(os.path.join(settings.MEDIA_ROOT, folder), exist_ok=True)

    name, ext = os.path.splitext(filename)
    if force_ext:
        ext = f".{force_ext.lstrip('.')}"
    if not ext:
        ext = '.webp'
    filename = f'{name}{ext}'

    relative_path = os.path.join(folder, filename)
    full_path = os.path.join(settings.MEDIA_ROOT, relative_path)
    if os.path.exists(full_path):
        os.remove(full_path)
    return relative_path


def sheet_preview_upload_path(instance, filename):
    return upload_path(
        slug_parts=[instance.faction.slug, 'board'],
        filename='front.webp',
    )

def decree_preview_upload_path(instance, filename):
    return upload_path(
        slug_parts=[instance.faction.slug, 'board'],
        filename='decree.webp',
    )

def back_preview_upload_path(instance, filename):
    return upload_path(
        slug_parts=[instance.faction.slug, 'board'],
        filename='back.webp',
    )


def card_preview_upload_path(instance, filename):
    return upload_path(
        slug_parts=[instance.faction.slug, 'card'],
        filename='front.webp',
    )


def _resolve_faction(obj):
    """Walk up FK relations to the owning ForgedFaction. Mirrors
    views._faction_for() so the file structure tracks the same notion of
    ownership the rest of the app uses."""
    from the_forge.models import ForgedFaction
    if isinstance(obj, ForgedFaction):
        return obj
    for attr in ('faction', 'sheet', 'step', 'legend', 'scale',
                 'track', 'parent', 'decree', 'card', 'faction_back'):
        parent = getattr(obj, attr, None)
        if parent is not None:
            return _resolve_faction(parent)
    return None


def faction_upload_path(instance, filename):
    faction = _resolve_faction(instance)
    slug = faction.slug if faction else None
    ext = os.path.splitext(filename)[1].lower() or '.webp'
    return upload_path(
        slug_parts=[slug, 'uploads'],
        filename=uuid.uuid4().hex,
        force_ext=ext.lstrip('.'),
    )


def _piece_upload_path(instance, face):
    faction = _resolve_faction(instance)
    slug = faction.slug if faction else None
    piece_type = instance.get_type_display().lower()
    return upload_path(
        slug_parts=[slug, 'pieces', f'{piece_type}s'],
        filename=f'{instance.pk}-{face}',
        force_ext='webp',
    )


def piece_front_upload_path(instance, filename):
    return _piece_upload_path(instance, 'front')


def piece_back_upload_path(instance, filename):
    return _piece_upload_path(instance, 'back')
