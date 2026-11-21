"""Forge maps (ForgedMap): the builder's settings, the generated map image and
its TTS JSON. The builder itself, the layout checks and the TTS JSON are the
keep's map builder code (the_keep/services/tts_map.py), shared as is."""
from django.core.files.base import ContentFile
from django.urls import reverse
from django.utils import timezone

from the_keep.services.tts_map import (DEFAULT_LOGO, FORGE_FRAME, OFFICIAL_RATIO, RATIO_TOLERANCE, MapGeometry,
                                       build_map_tts_json, builder_config, check_builder, crop_to_official,
                                       print_art)
from the_keep.utils import validate_tts_save

MAX_ART_BYTES = 25 * 1024 * 1024
ART_QUALITY = 92


def default_state():
    """A new Forge map's layout: the official maps' track and item grid, and
    the logo top right."""
    state = MapGeometry(*FORGE_FRAME).defaults()
    state['logo'] = list(DEFAULT_LOGO)
    return state


def step_text():
    """The Forge's wording for the builder's steps (the rest is the keep's)."""
    return {
        'upload': {
            'hint': "Here we'll walk you through creating a Root map. All you need is to name your map and upload its art. "
                    "Please don't use AI-generated art.",
            'link': {'before': 'If you already have a finished map, ',
                     'label': 'submit it to the Database',
                     'url': reverse('map-create'),
                     'after': ' and edit it from there.'},
        },
        'track': {
            'hint': 'Drag the VP track up or down, and the item grid and the Root logo, to where they should be '
                    'printed on your map.',
        },
        'review': {
            'hint': 'Check the count of each element, then click "Complete" to generate your map.',
        },
    }


def art_url(forged_map):
    if not forged_map.art:
        return None
    return f'{forged_map.art.url}?v={forged_map.art_version}'


def map_builder_config(forged_map, *, profile, readonly=False):
    """The shared builder's config for a Forge map: drawn in the official
    maps' frame (FORGE_FRAME), with the upload step, the printed art under the
    pieces, and no Map post counts to compare with.

    forged_map=None is a new map: nothing exists until the Art step's Next
    sends its name and art to create_url (the builder then carries on with the
    created map's URLs)."""
    if forged_map is None:
        state, image, fixed, cropped, name = default_state(), None, False, False, ''
        save_url = upload_url = None
    else:
        state = forged_map.tts_builder or default_state()
        image, fixed, cropped = art_url(forged_map), forged_map.fixed_clearings, forged_map.art_cropped
        name = forged_map.faction.faction_name
        save_url = reverse('forge-map-builder', kwargs={'pk': forged_map.faction_id})
        upload_url = reverse('forge-map-art', kwargs={'pk': forged_map.faction_id})
    return builder_config(
        image, *FORGE_FRAME, state,
        fixed=fixed,
        expected={'clearings': None, 'forests': None, 'buildings': None, 'ruins': None},
        save_url=save_url,
        show_intros=not getattr(profile, 'map_builder_onboard', True),
        upload_step=not readonly,
        upload_url=upload_url,
        create_url=reverse('forge-map-create') if forged_map is None else None,
        art_cropped=cropped,
        official_ratio=OFFICIAL_RATIO,
        ratio_tolerance=RATIO_TOLERANCE,
        print={key: {'url': art['url'], 'w': art['w'], 'h': art['h']} for key, art in print_art().items()},
        default_logo=list(DEFAULT_LOGO),
        step_text=step_text(),
        map_name=name,
        readonly=readonly,
    )


class ArtRejected(Exception):
    """The uploaded file can't be used as map art."""


def save_art(forged_map, upload):
    """Store an uploaded image as the map's art, centre-cropped to the official
    shape. Returns True if it was cropped. Raises ArtRejected."""
    from io import BytesIO

    from PIL import Image, ImageOps, UnidentifiedImageError

    if upload.size > MAX_ART_BYTES:
        raise ArtRejected("That image is too big (25 MB at most).")
    try:
        with Image.open(upload) as probe:
            probe.verify()
        upload.seek(0)
        with Image.open(upload) as image:
            image = ImageOps.exif_transpose(image)
            image, cropped = crop_to_official(image.convert('RGB'))
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        raise ArtRejected("That file isn't an image we can read. Use a PNG, JPG or WebP.")

    out = BytesIO()
    image.save(out, format='WEBP', quality=ART_QUALITY, method=4)
    forged_map.art.save('art.webp', ContentFile(out.getvalue()), save=False)
    forged_map.art_cropped = cropped
    forged_map.art_version = (forged_map.art_version or 0) + 1
    forged_map.save(update_fields=['art', 'art_cropped', 'art_version', 'last_updated'])
    return cropped


def refresh_map_image(forged_map):
    """Render the map's image (the art with everything printed on it) if it's
    out of date. Runs the PDF engine: call it inside render_slot()."""
    from the_forge.map_pdf_engine import preview_dpi, render_map_pdf
    from the_forge.pdf_cache import fingerprint_map
    from the_forge.pdf_engine import pdf_bytes_to_webp_bytes

    fingerprint = fingerprint_map(forged_map)
    if forged_map.image_preview and forged_map.preview_fingerprint == fingerprint:
        return False
    dpi = preview_dpi(forged_map)
    webp = pdf_bytes_to_webp_bytes(render_map_pdf(forged_map, target_dpi=dpi), dpi=dpi, quality=90)
    forged_map.image_preview.save('front.webp', ContentFile(webp), save=False)
    forged_map.preview_fingerprint = fingerprint
    forged_map.preview_version = (forged_map.preview_version or 0) + 1
    forged_map.last_generated = timezone.now()
    forged_map.save(update_fields=['image_preview', 'preview_fingerprint', 'preview_version', 'last_generated'])
    return True


def store_map_tts(forged_map):
    """Build the map's TTS JSON from its layout (on the generated image) and
    store it on the draft. Returns (ok, errors)."""
    from the_forge.models import ForgedFaction

    errors, _warnings = check_builder(forged_map, forged_map.tts_builder or {})
    if errors:
        return False, errors
    save = build_map_tts_json(forged_map, forged_map.tts_builder)
    validate_tts_save(save)
    faction = forged_map.faction
    faction.tts_json = save
    faction.tts_json_generated = timezone.now()
    ForgedFaction.objects.filter(pk=faction.pk).update(tts_json=save, tts_json_generated=faction.tts_json_generated)
    return True, []


def refresh_map_snapshot(faction):
    """Bring a Map draft's image and TTS JSON up to date, for the TTS Library
    (the admin reviews this JSON, and the loader serves it once approved).
    Returns an error message, or None."""
    from the_forge.models import ForgedMap
    from the_forge.render_guard import RenderBusy, render_slot

    forged_map = ForgedMap.objects.filter(faction=faction).first()
    if forged_map is None or not forged_map.art:
        return "Upload the map's art and complete the map first."
    forged_map.faction = faction
    try:
        with render_slot():
            refresh_map_image(forged_map)
    except RenderBusy:
        return "The map generator is busy. Try again in a moment."
    ok, errors = store_map_tts(forged_map)
    return None if ok else "Finish the map in the builder first: " + ' '.join(errors)


def map_library_state(faction, forged_map):
    """The TTS Library card's state for a Map draft (see _tts_library_card.html):
    a map can be submitted once it's been completed (it has a generated image)."""
    from the_forge.models import ForgedFaction
    status = ForgedFaction.TTSStatus
    if faction.tts_status in (status.SUBMITTED, status.APPROVED):
        return faction.tts_status
    if not (forged_map.image_preview and faction.tts_json):
        return 'not_ready'
    return 'rejected' if faction.tts_status == status.REJECTED else 'can_submit'
