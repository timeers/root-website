from django.apps import apps
from django.db.models.signals import post_save, post_delete, pre_save
from django.utils import timezone

from the_keep.utils import resize_image_to_webp, resize_image, center_square_crop_in_place

from .services.slugify_titles import slugify_forged_faction_name


FORGE_MAX = 1600     # full-page backgrounds
MEDIUM_MAX = 1200    # ~4" max display (character, faction back)
SMALL_MAX = 900      # ~3" max display (setup card header)
ICON_MAX = 400       # small icons (~1")
TRACK_MAX = 300      # cardboard track / slot backgrounds (~1" rect/circle)

IMAGE_FIELDS_CONFIG = {
    'ForgedFaction':   {'fields': {'background_image': FORGE_MAX, 'faction_icon': ICON_MAX}},
    'FactionSheet':    {'fields': {'header_image': FORGE_MAX}},
    'CharacterImage':  {'fields': {'image': MEDIUM_MAX}},
    'StepAction':      {'fields': {'cost_image': ICON_MAX}},
    'PhaseStep':       {'fields': {'step_cost_image': ICON_MAX}},
    'CardboardSlot':   {'fields': {'background_image': TRACK_MAX}},
    'CardboardTrack':  {'fields': {'background_image': TRACK_MAX}},
    'LegendRow':       {'fields': {'image': ICON_MAX}},
    'FactionBack':     {'fields': {'back_image': MEDIUM_MAX}},
    'SetupCard':       {'fields': {'header_image': SMALL_MAX}},
    'Piece':           {'fields': {'small_icon': ICON_MAX, 'back_image': ICON_MAX}},
    'ForgedDeckGroup': {'fields': {'back_image': MEDIUM_MAX}},
    'ForgedCard':      {'fields': {'front_image': MEDIUM_MAX}},
}


def _make_resize_handler(field_max_dims):
    def handler(sender, instance, created, **kwargs):
        changed_fields = []
        # Building & token piece icons are square-cropped at source resolution
        # before the WebP downscale, so the crop happens before any quality
        # loss from scaling.
        is_square_piece = (
            sender.__name__ == 'Piece'
            and getattr(instance, 'type', None) in ('B', 'T')
        )
        for field_name, max_dim in field_max_dims.items():
            field = getattr(instance, field_name, None)
            if not field:
                continue
            if is_square_piece:
                try:
                    center_square_crop_in_place(field)
                except Exception:
                    pass
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


PREVIEW_MODELS = ('FactionSheet', 'FactionBack', 'SetupCard')


def _delete_image_preview(sender, instance, **kwargs):
    field = getattr(instance, 'image_preview', None)
    if field and field.name:
        try:
            field.delete(save=False)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Timestamp bubble-up.
#
# The PDF cache fingerprint walks each Sheet/Back/SetupCard plus all child
# rows. A change to any of those should bump:
#   - the immediate parent (FactionSheet / FactionBack / SetupCard)
#   - the grand-parent ForgedFaction
# so the faction list can sort by "recently touched."
#
# We use Model.objects.filter(pk=...).update(...) instead of save() to
# avoid running parent save() overrides and to skip signal recursion.
# ---------------------------------------------------------------------------

def _touch(model_class, pk):
    if pk is None:
        return
    model_class.objects.filter(pk=pk).update(last_updated=timezone.now())


def _touch_sheet_and_faction(sheet_id):
    if sheet_id is None:
        return
    FactionSheet = apps.get_model('the_forge', 'FactionSheet')
    ForgedFaction = apps.get_model('the_forge', 'ForgedFaction')
    faction_id = FactionSheet.objects.filter(pk=sheet_id).values_list('faction_id', flat=True).first()
    _touch(FactionSheet, sheet_id)
    _touch(ForgedFaction, faction_id)


def _touch_back_and_faction(back_id):
    if back_id is None:
        return
    FactionBack = apps.get_model('the_forge', 'FactionBack')
    ForgedFaction = apps.get_model('the_forge', 'ForgedFaction')
    faction_id = FactionBack.objects.filter(pk=back_id).values_list('faction_id', flat=True).first()
    _touch(FactionBack, back_id)
    _touch(ForgedFaction, faction_id)


def _touch_card_and_faction(card_id):
    if card_id is None:
        return
    SetupCard = apps.get_model('the_forge', 'SetupCard')
    ForgedFaction = apps.get_model('the_forge', 'ForgedFaction')
    faction_id = SetupCard.objects.filter(pk=card_id).values_list('faction_id', flat=True).first()
    _touch(SetupCard, card_id)
    _touch(ForgedFaction, faction_id)


def _bubble_sheet_back_card(sender, instance, **kwargs):
    """FactionSheet/FactionBack/SetupCard saved — bubble to ForgedFaction."""
    ForgedFaction = apps.get_model('the_forge', 'ForgedFaction')
    _touch(ForgedFaction, getattr(instance, 'faction_id', None))


def _bubble_sheet_child(sender, instance, **kwargs):
    """Direct child of FactionSheet (FK 'sheet')."""
    _touch_sheet_and_faction(getattr(instance, 'sheet_id', None))


def _bubble_phasestep_grandchild(sender, instance, **kwargs):
    """Grandchild via PhaseStep (FK 'step')."""
    step_id = getattr(instance, 'step_id', None)
    if step_id is None:
        return
    PhaseStep = apps.get_model('the_forge', 'PhaseStep')
    sheet_id = PhaseStep.objects.filter(pk=step_id).values_list('sheet_id', flat=True).first()
    _touch_sheet_and_faction(sheet_id)


def _bubble_track_grandchild(sender, instance, **kwargs):
    """Grandchild via CardboardTrack (FK 'track') -> PhaseStep -> FactionSheet."""
    track_id = getattr(instance, 'track_id', None)
    if track_id is None:
        return
    CardboardTrack = apps.get_model('the_forge', 'CardboardTrack')
    step_id = CardboardTrack.objects.filter(pk=track_id).values_list('step_id', flat=True).first()
    if step_id is None:
        return
    PhaseStep = apps.get_model('the_forge', 'PhaseStep')
    sheet_id = PhaseStep.objects.filter(pk=step_id).values_list('sheet_id', flat=True).first()
    _touch_sheet_and_faction(sheet_id)


def _bubble_legend_grandchild(sender, instance, **kwargs):
    """LegendRow (FK 'legend') -> Legend -> PhaseStep -> FactionSheet."""
    legend_id = getattr(instance, 'legend_id', None)
    if legend_id is None:
        return
    Legend = apps.get_model('the_forge', 'Legend')
    step_id = Legend.objects.filter(pk=legend_id).values_list('step_id', flat=True).first()
    if step_id is None:
        return
    PhaseStep = apps.get_model('the_forge', 'PhaseStep')
    sheet_id = PhaseStep.objects.filter(pk=step_id).values_list('sheet_id', flat=True).first()
    _touch_sheet_and_faction(sheet_id)


def _bubble_scale_grandchild(sender, instance, **kwargs):
    """ScaleRow (FK 'scale') -> Scale -> PhaseStep -> FactionSheet."""
    scale_id = getattr(instance, 'scale_id', None)
    if scale_id is None:
        return
    Scale = apps.get_model('the_forge', 'Scale')
    step_id = Scale.objects.filter(pk=scale_id).values_list('step_id', flat=True).first()
    if step_id is None:
        return
    PhaseStep = apps.get_model('the_forge', 'PhaseStep')
    sheet_id = PhaseStep.objects.filter(pk=step_id).values_list('sheet_id', flat=True).first()
    _touch_sheet_and_faction(sheet_id)


def _bubble_decree_grandchild(sender, instance, **kwargs):
    """CardSlot (FK 'decree') -> DecreeSection -> FactionSheet."""
    decree_id = getattr(instance, 'decree_id', None)
    if decree_id is None:
        return
    DecreeSection = apps.get_model('the_forge', 'DecreeSection')
    sheet_id = DecreeSection.objects.filter(pk=decree_id).values_list('sheet_id', flat=True).first()
    _touch_sheet_and_faction(sheet_id)


def _bubble_piece(sender, instance, **kwargs):
    """Piece (FK 'faction') -> ForgedFaction. Also bumps the related
    FactionBack since pieces are rendered onto the back preview."""
    faction_id = getattr(instance, 'faction_id', None)
    if faction_id is None:
        return
    ForgedFaction = apps.get_model('the_forge', 'ForgedFaction')
    FactionBack = apps.get_model('the_forge', 'FactionBack')
    _touch(ForgedFaction, faction_id)
    back_id = FactionBack.objects.filter(faction_id=faction_id).values_list('pk', flat=True).first()
    _touch(FactionBack, back_id)


def _bubble_setupstep(sender, instance, **kwargs):
    """SetupStep has nullable FKs to FactionBack ('faction_back') AND SetupCard ('card')."""
    _touch_back_and_faction(getattr(instance, 'faction_back_id', None))
    _touch_card_and_faction(getattr(instance, 'card_id', None))


def _bubble_deckgroup(sender, instance, **kwargs):
    """ForgedDeckGroup (FK 'piece') -> Piece -> ForgedFaction."""
    piece_id = getattr(instance, 'piece_id', None)
    if piece_id is None:
        return
    Piece = apps.get_model('the_forge', 'Piece')
    faction_id = Piece.objects.filter(pk=piece_id).values_list('faction_id', flat=True).first()
    if faction_id is None:
        return
    ForgedFaction = apps.get_model('the_forge', 'ForgedFaction')
    _touch(ForgedFaction, faction_id)


def _bubble_carddeck(sender, instance, **kwargs):
    """ForgedCardDeck (FK 'group') -> ForgedDeckGroup -> Piece -> ForgedFaction.
    The model's save() already suppresses bubbling for sprite-sheet-only writes,
    but post_delete still routes through here for cleanup."""
    group_id = getattr(instance, 'group_id', None)
    if group_id is None:
        return
    ForgedDeckGroup = apps.get_model('the_forge', 'ForgedDeckGroup')
    piece_id = ForgedDeckGroup.objects.filter(pk=group_id).values_list('piece_id', flat=True).first()
    if piece_id is None:
        return
    Piece = apps.get_model('the_forge', 'Piece')
    faction_id = Piece.objects.filter(pk=piece_id).values_list('faction_id', flat=True).first()
    if faction_id is None:
        return
    ForgedFaction = apps.get_model('the_forge', 'ForgedFaction')
    _touch(ForgedFaction, faction_id)


def _bubble_forged_card(sender, instance, **kwargs):
    """ForgedCard (FK 'group') -> same chain as ForgedCardDeck. Also keeps
    the parent Piece.quantity in sync with the deck's actual card count so
    component renders (cardboard pages, faction back) reflect the deck size."""
    _bubble_carddeck(sender, instance, **kwargs)
    group_id = getattr(instance, 'group_id', None)
    if group_id is None:
        return
    ForgedDeckGroup = apps.get_model('the_forge', 'ForgedDeckGroup')
    ForgedCard = apps.get_model('the_forge', 'ForgedCard')
    Piece = apps.get_model('the_forge', 'Piece')
    piece_id = ForgedDeckGroup.objects.filter(pk=group_id).values_list('piece_id', flat=True).first()
    if piece_id is None:
        return
    actual = ForgedCard.objects.filter(group_id=group_id).count()
    # Piece.quantity has MinValueValidator(1) — clamp empty decks to 1.
    new_qty = max(1, min(99, actual))
    Piece.objects.filter(pk=piece_id).exclude(quantity=new_qty).update(quantity=new_qty)


# Map: model name -> bubble handler. Each handler is connected to both
# post_save and post_delete so adds/edits/removals all bump.
TIMESTAMP_BUBBLES = {
    'FactionSheet':    _bubble_sheet_back_card,
    'FactionBack':     _bubble_sheet_back_card,
    'SetupCard':       _bubble_sheet_back_card,
    'CharacterImage':  _bubble_sheet_child,
    'FactionAbility':  _bubble_sheet_child,
    'ContentBox':      _bubble_sheet_child,
    'CardPile':        _bubble_sheet_child,
    'DecreeSection':   _bubble_sheet_child,
    'PhaseStep':       _bubble_sheet_child,
    'StepAction':      _bubble_phasestep_grandchild,
    'BorderedBox':     _bubble_phasestep_grandchild,
    'CardboardTrack':  _bubble_phasestep_grandchild,
    'Legend':          _bubble_phasestep_grandchild,
    'Scale':           _bubble_phasestep_grandchild,
    'CardboardSlot':   _bubble_track_grandchild,
    'LegendRow':       _bubble_legend_grandchild,
    'ScaleRow':        _bubble_scale_grandchild,
    'CardSlot':        _bubble_decree_grandchild,
    'Piece':           _bubble_piece,
    'SetupStep':       _bubble_setupstep,
    'ForgedDeckGroup': _bubble_deckgroup,
    'ForgedCardDeck':  _bubble_carddeck,
    'ForgedCard':      _bubble_forged_card,
}


def _forged_faction_pre_save(sender, instance, **kwargs):
    if instance.slug is None:
        slugify_forged_faction_name(instance, save=False)


def _forged_faction_post_save(sender, instance, created, **kwargs):
    if created:
        slugify_forged_faction_name(instance, save=True)


def _connect():
    for model_name, cfg in IMAGE_FIELDS_CONFIG.items():
        Model = apps.get_model('the_forge', model_name)
        post_save.connect(_make_resize_handler(cfg['fields']), sender=Model)
        post_delete.connect(_make_delete_handler(cfg['fields']), sender=Model)
    for model_name in PREVIEW_MODELS:
        Model = apps.get_model('the_forge', model_name)
        post_delete.connect(_delete_image_preview, sender=Model)
    for model_name, handler in TIMESTAMP_BUBBLES.items():
        Model = apps.get_model('the_forge', model_name)
        post_save.connect(handler, sender=Model)
        post_delete.connect(handler, sender=Model)

    ForgedFaction = apps.get_model('the_forge', 'ForgedFaction')
    pre_save.connect(_forged_faction_pre_save, sender=ForgedFaction)
    post_save.connect(_forged_faction_post_save, sender=ForgedFaction)
