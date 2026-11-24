"""Forge maps (ForgedMap): the builder's settings, the generated map image and
its TTS JSON. The builder itself, the layout checks and the TTS JSON are the
keep's map builder code (the_keep/services/tts_map.py), shared as is."""
import copy

from django.core.files.base import ContentFile
from django.urls import reverse
from django.utils import timezone

from the_keep.services.tts_map import (DEFAULT_LOGO, FORGE_FRAME, MAP_BLANK, OFFICIAL_RATIO, RATIO_TOLERANCE,
                                       MapGeometry, build_map_tts_json, builder_config, check_builder,
                                       crop_to_official, path_config, print_art)
from the_keep.utils import validate_tts_save

MAX_ART_BYTES = 25 * 1024 * 1024


def map_name_max():
    """A map's name becomes a keep post's title when published, so it's held
    to that length (shorter than faction_name's own limit)."""
    from the_keep.models import Post
    return Post._meta.get_field('title').max_length
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
            'hint': "Here we'll walk you through creating a Root map. All you need is a name to start: "
                    "If you don't have art the map is printed on a grey board with its paths and rivers. "
                    "You will need your own art before you submit the map. "
                    "Please don't use AI-generated art.",
            'link': {'before': 'If you already have a finished map, ',
                     'label': 'submit it to the Database',
                     'url': reverse('map-create'),
                     'after': ' and edit it from there.'},
        },
        'track': {
            'hint': 'Drag the VP track up or down. Move the item grid and the Root logo to where they will be '
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
    from the_gatehouse.models import Language
    from the_forge.models import _default_language
    languages = [{'id': lang.pk, 'name': lang.name} for lang in Language.objects.order_by('name')]
    locked_note = ''
    if forged_map is None:
        state, image, fixed, cropped, name = default_state(), None, False, False, ''
        language = _default_language()
        save_url = upload_url = None
    else:
        faction = forged_map.faction
        state = forged_map.tts_builder or default_state()
        image, fixed, cropped = art_url(forged_map), forged_map.fixed_clearings, forged_map.art_cropped
        name, language = faction.faction_name, faction.language_id
        save_url = reverse('forge-map-builder', kwargs={'pk': forged_map.faction_id})
        upload_url = reverse('forge-map-art', kwargs={'pk': forged_map.faction_id})
        locked_note = name_locked_note(faction)
    return builder_config(
        image, *FORGE_FRAME, state,
        fixed=fixed,
        expected={'clearings': None, 'forests': None, 'buildings': None, 'ruins': None},
        save_url=save_url,
        show_intros=not getattr(profile, 'map_builder_onboard', True),
        upload_step=not readonly,
        upload_url=upload_url,
        # Paths & Rivers: drawn in the builder (and printed on a map without art).
        paths_step=True,
        # Setup Card Rules, after them (the page passes its form: map_views).
        rules_step=not readonly,
        path=path_config(MapGeometry(*FORGE_FRAME)),
        blank=MAP_BLANK,
        create_url=reverse('forge-map-create') if forged_map is None else None,
        art_cropped=cropped,
        official_ratio=OFFICIAL_RATIO,
        ratio_tolerance=RATIO_TOLERANCE,
        print={key: {'url': art['url'], 'w': art['w'], 'h': art['h']} for key, art in print_art().items()},
        default_logo=list(DEFAULT_LOGO),
        step_text=step_text(),
        map_name=name,
        name_max=map_name_max(),
        # Linked to a keep post: its title (and language) can only be changed
        # by an admin, so the map's can't either.
        name_locked=bool(locked_note),
        name_locked_note=locked_note,
        languages=languages,
        language=language,
        # The Forge prints buildings and ruins on the map: at least one of each.
        require_buildings=True,
        readonly=readonly,
    )


def name_locked_note(faction):
    """Why a map's name and language can't change (it's linked to a keep post
    or translation), or ''."""
    if faction.published_post_id:
        title = faction.published_keep_post.title
    elif faction.published_translation_id:
        title = faction.published_translation.translated_title or faction.published_translation.post.title
    else:
        return ''
    return f'Linked to {title} on the Root Database. Its title can only be changed by an admin.'


def set_language(faction, language_pk):
    """The map's language from the builder (an unknown pk is ignored). Returns
    True if it changed."""
    from the_gatehouse.models import Language
    try:
        language = Language.objects.filter(pk=int(language_pk)).first()
    except (TypeError, ValueError):
        return False
    if language is None or language.pk == faction.language_id:
        return False
    faction.language = language
    return True


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


def remove_art(forged_map):
    """Take the map's art away (it's optional): the map is then printed on a
    grey board, with its paths and rivers. The art's name is in the map's
    fingerprint, so the image re-renders on the next Complete or download."""
    if forged_map.art:
        forged_map.art.delete(save=False)
    forged_map.art = None
    forged_map.art_cropped = False
    forged_map.art_version = (forged_map.art_version or 0) + 1
    forged_map.save(update_fields=['art', 'art_cropped', 'art_version', 'last_updated'])


def refresh_map_image(forged_map):
    """Render the map's image (the art with everything printed on it) if it's
    out of date. Runs the PDF engine: call it inside render_slot()."""
    from the_forge.map_pdf_engine import preview_dpi, render_map_pdf
    from the_forge.pdf_cache import fingerprint_map
    from the_forge.pdf_engine import pdf_bytes_to_webp_bytes

    fingerprint = fingerprint_map(forged_map)
    if forged_map.image_preview and forged_map.preview_fingerprint == fingerprint:
        # Already current. The fingerprint covers the layout, so the image was
        # rendered from exactly the current layout: record it as the rendered
        # one if it isn't yet (maps rendered before rendered_builder existed,
        # or a Complete with nothing changed).
        if forged_map.rendered_builder != forged_map.tts_builder:
            forged_map.rendered_builder = copy.deepcopy(forged_map.tts_builder or {})
            forged_map.save(update_fields=['rendered_builder'])
        return False
    dpi = preview_dpi(forged_map)
    webp = pdf_bytes_to_webp_bytes(render_map_pdf(forged_map, target_dpi=dpi), dpi=dpi, quality=90)
    forged_map.image_preview.save('front.webp', ContentFile(webp), save=False)
    forged_map.preview_fingerprint = fingerprint
    forged_map.preview_version = (forged_map.preview_version or 0) + 1
    forged_map.last_generated = timezone.now()
    # The layout this image shows, saved with it: what gets published.
    forged_map.rendered_builder = copy.deepcopy(forged_map.tts_builder or {})
    forged_map.save(update_fields=['image_preview', 'preview_fingerprint', 'preview_version', 'last_generated',
                                   'rendered_builder'])
    return True


def setup_card_args(forged_map):
    """What a Forge map's setup card images are drawn from: its name, the
    layout its published image shows (else the live one), its language and
    the Forge's frame."""
    from the_forge.pdf_engine import _lang_code_for
    layout = forged_map.rendered_builder if forged_map.rendered_builder is not None else forged_map.tts_builder
    return {'title': forged_map.title, 'layout': layout or {}, 'lang_code': _lang_code_for(forged_map),
            'frame': FORGE_FRAME}


def setup_card_fingerprint(forged_map):
    from the_keep.services.map_setup_card import card_fingerprint
    args = setup_card_args(forged_map)
    return card_fingerprint(args['title'], args['layout'], forged_map.map_card, frame=args['frame'],
                            lang_code=args['lang_code'])


def refresh_forge_setup_card(forged_map):
    """Render the map's setup card if it's out of date (a completed map only:
    the card draws the layout its image shows). Call it inside render_slot().
    Returns True if it changed."""
    from the_keep.services.map_setup_card import refresh_setup_card
    if not forged_map.image_preview:
        return False
    return refresh_setup_card(forged_map, **setup_card_args(forged_map))


def store_map_tts(forged_map):
    """Build the map's TTS JSON from its layout (on the generated image) and
    store it on the draft. Returns (ok, errors)."""
    from the_forge.models import ForgedFaction

    errors, _warnings = check_builder(forged_map, forged_map.tts_builder or {}, require_buildings=True)
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
    if forged_map is None:
        return "Complete the map in the builder first."
    forged_map.faction = faction
    try:
        with render_slot():
            refresh_map_image(forged_map)
            refresh_forge_setup_card(forged_map)
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
