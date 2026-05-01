from django.apps import apps
from django.db.models.signals import post_save, post_delete

from the_keep.utils import resize_image_to_webp, resize_image


FORGE_MAX = 1600     # full-page backgrounds
MEDIUM_MAX = 1200    # ~4" max display (character, faction back)
SMALL_MAX = 900      # ~3" max display (setup card header)
ICON_MAX = 400       # small icons (~1")
TRACK_MAX = 300      # cardboard track / slot backgrounds (~1" rect/circle)

IMAGE_FIELDS_CONFIG = {
    'ForgedFaction':   {'fields': {'background_image': FORGE_MAX}},
    'FactionSheet':    {'fields': {'action_image': ICON_MAX, 'header_image': FORGE_MAX}},
    'CharacterImage':  {'fields': {'image': MEDIUM_MAX}},
    'StepAction':      {'fields': {'cost_image': ICON_MAX}},
    'PhaseStep':       {'fields': {'step_cost_image': ICON_MAX}},
    'CardboardSlot':   {'fields': {'background_image': TRACK_MAX}},
    'CardboardTrack':  {'fields': {'background_image': TRACK_MAX}},
    'LegendRow':       {'fields': {'image': ICON_MAX}},
    'FactionBack':     {'fields': {'back_image': MEDIUM_MAX}},
    'SetupCard':       {'fields': {'header_image': SMALL_MAX}},
    'Piece':           {'fields': {'small_icon': ICON_MAX}},
}


def _make_resize_handler(field_max_dims):
    def handler(sender, instance, created, **kwargs):
        changed_fields = []
        for field_name, max_dim in field_max_dims.items():
            field = getattr(instance, field_name, None)
            if not field:
                continue
            new_relpath = None
            try:
                new_relpath = resize_image_to_webp(field, max_size=max_dim)
            except Exception:
                try:
                    resize_image(field, max_size=max_dim)
                except Exception:
                    pass
            if new_relpath:
                # resize_image_to_webp wrote a new .webp file and deleted the
                # original, but doesn't update the FieldFile's name. Persist the
                # new path so the model points at the file that actually exists.
                field.name = new_relpath.replace('\\', '/')
                changed_fields.append(field_name)
        if changed_fields:
            # Re-saving fires this signal again, but the second pass hits the
            # "already webp and within max size" early-return in
            # resize_image_to_webp, so it's a no-op.
            instance.save(update_fields=changed_fields)
    return handler


def _make_delete_handler(field_max_dims):
    def handler(sender, instance, **kwargs):
        for field_name in field_max_dims.keys():
            field = getattr(instance, field_name, None)
            if field and field.name and not field.name.startswith('default_images/'):
                try:
                    field.delete(save=False)
                except Exception:
                    pass
    return handler


def _connect():
    for model_name, cfg in IMAGE_FIELDS_CONFIG.items():
        Model = apps.get_model('the_forge', model_name)
        post_save.connect(_make_resize_handler(cfg['fields']), sender=Model)
        post_delete.connect(_make_delete_handler(cfg['fields']), sender=Model)
