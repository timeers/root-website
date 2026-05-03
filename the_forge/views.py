import json

from django.db import transaction
from django.http import (
    FileResponse,
    HttpResponse,
    HttpResponseBadRequest,
    HttpResponseForbidden,
    JsonResponse,
)
from django.shortcuts import get_object_or_404, redirect, render
from django.templatetags.static import static
from django.views.decorators.http import require_http_methods

from django.contrib.auth.decorators import login_required
from the_gatehouse.views import forge_onboard_required, player_required
from .inline_images import picker_image_map, picker_keywords, sheet_inline_images, sheet_picker_keywords
from .layout_autogrow import ensure_step_parent_fits

from the_gatehouse.utils import build_absolute_uri
from the_gatehouse.tasks import send_discord_message_task, send_rich_discord_message_task

from .forms import (
    BorderedBoxForm,
    CardPileForm,
    CardSlotForm,
    CardboardSlotForm,
    CardboardTrackForm,
    CharacterImageForm,
    ContentBoxForm,
    CustomInlineImageForm,
    DecreeSectionForm,
    FactionAbilityForm,
    FactionBackForm,
    FactionHeaderForm,
    ForgedFactionForm,
    LegendForm,
    LegendRowForm,
    PhaseStepCostImageForm,
    PhaseStepForm,
    ScaleForm,
    ScaleRowForm,
    SetupCardForm,
    StepActionForm,
)
from .models import (
    BorderedBox,
    CardPile,
    CardSlot,
    CardboardSlot,
    CardboardTrack,
    CharacterImage,
    ContentBox,
    CustomInlineImage,
    DecreeSection,
    FactionAbility,
    FactionBack,
    FactionSheet,
    ForgedFaction,
    Legend,
    LegendRow,
    PhaseStep,
    Piece,
    Scale,
    ScaleRow,
    SetupCard,
    SetupStep,
    StepAction,
)


# Caps on per-sheet child collections that the printable layout can render.
MAX_CARD_PILES = 5
MAX_CARD_SLOTS = 5
MAX_CHARACTER_IMAGES = 5
MAX_CUSTOM_INLINE_IMAGES = 10


# ---------- Helpers ----------

def _faction_for(obj):
    """Walk a forge object up to its owning ForgedFaction."""
    if isinstance(obj, ForgedFaction):
        return obj
    for attr in ('faction', 'sheet', 'step', 'legend', 'scale',
                 'track', 'parent', 'decree', 'card', 'faction_back'):
        parent = getattr(obj, attr, None)
        if parent is not None:
            return _faction_for(parent)
    return None


def user_can_edit_forge(request, obj):
    """True if request.user is the faction's designer.

    `obj` may be a ForgedFaction or any descendant (FactionSheet, PhaseStep,
    Legend, LegendRow, etc.). Returns False if obj has no resolvable faction.
    Admins can view but not edit other designers' factions — see
    user_can_view_forge for the view-only check.
    """
    if not request.user.is_authenticated:
        return False
    profile = getattr(request.user, 'profile', None)
    if not profile:
        return False
    faction = _faction_for(obj)
    if faction is None:
        return False
    return faction.designer_id == profile.id


def user_can_view_forge(request, obj):
    """True if request.user is the faction's designer OR an admin.
    Used to gate read-only access to detail pages."""
    if user_can_edit_forge(request, obj):
        return True
    profile = getattr(request.user, 'profile', None)
    return bool(profile and getattr(profile, 'admin', False))


def _inline_keywords(sheet=None):
    if sheet is None:
        return picker_keywords()
    return sheet_picker_keywords(sheet)


def _inline_images_map(sheet=None):
    if sheet is None:
        return picker_image_map()
    return sheet_inline_images(sheet)


def _inline_image_labels(sheet=None):
    """Per-sheet `custom_image_N` -> user-supplied display name. Built-in
    keywords are not in this map; pickers fall back to the keyword itself."""
    if sheet is None:
        return {}
    return {ci.keyword: ci.name for ci in sheet.custom_inline_images.all()}


def _sheet_from(obj):
    """Walk a forge object up to its owning FactionSheet. Returns None if
    none reachable (e.g. FactionBack / SetupCard children)."""
    if obj is None:
        return None
    if isinstance(obj, FactionSheet):
        return obj
    sheet = getattr(obj, 'sheet', None)
    if sheet is not None:
        return sheet
    for parent_attr in ('step', 'legend', 'scale', 'track', 'box'):
        parent = getattr(obj, parent_attr, None)
        if parent is not None:
            resolved = _sheet_from(parent)
            if resolved is not None:
                return resolved
    return None


def _step_svg_url(number):
    return static(f'pdf/svg/{(number or 0) % 10}.svg')


def _annotate_step(step):
    step.svg_url = _step_svg_url(step.number)
    return step


def _annotate_steps(steps):
    return [_annotate_step(s) for s in steps]


def _forbid_if_not_editor(request, obj):
    if not user_can_edit_forge(request, obj):
        is_partial = (
            request.headers.get('HX-Request')
            or request.path.startswith('/hx/')
        )
        if is_partial:
            return HttpResponseForbidden("You do not have permission to edit this faction.")
        return redirect('forge-home')
    return None


def _background_preset_options():
    return [
        {
            'value': value,
            'label': label,
            'static_path': ForgedFaction.BACKGROUND_PRESET_FILES[value],
        }
        for value, label in ForgedFaction.BackgroundPreset.choices
        if value
    ]


# ---------- Forge Home (public landing page) ----------

def forge_home(request):
    has_factions = False
    if request.user.is_authenticated:
        profile = getattr(request.user, 'profile', None)
        if profile is not None:
            has_factions = ForgedFaction.objects.filter(designer=profile).exists()

        send_discord_message_task.delay(f'[{request.user}]({build_absolute_uri(request, request.user.profile.get_absolute_url())}) ({request.user.profile.group}) viewed The Forge')

    return render(request, 'the_forge/forge_home.html', {
        'has_factions': has_factions,
    })


def forge_style_guide(request):
    if request.user.is_authenticated:
        send_discord_message_task.delay(f'[{request.user}]({build_absolute_uri(request, request.user.profile.get_absolute_url())}) ({request.user.profile.group}) viewed The Forge Style Guide')

    return render(request, 'the_forge/forge_style_guide.html')


def forge_how_to(request):
    if request.user.is_authenticated:
        send_discord_message_task.delay(f'[{request.user}]({build_absolute_uri(request, request.user.profile.get_absolute_url())}) ({request.user.profile.group}) viewed The Forge How-To')

    return render(request, 'the_forge/forge_how_to.html')


# ---------- ForgedFaction CRUD ----------

@forge_onboard_required
def forgedfaction_list(request):
    profile = getattr(request.user, 'profile', None)
    if profile:
        factions = (
            ForgedFaction.objects
            .filter(designer=profile)
            .select_related('faction_sheet', 'faction_back', 'setup_card')
            .order_by('faction_name')
        )
    else:
        factions = []
    return render(request, 'the_forge/forgedfaction_list.html', {'factions': factions})


@forge_onboard_required
def forgedfaction_create(request):
    if request.method == 'POST':
        form = ForgedFactionForm(request.POST, request.FILES)
        if form.is_valid():
            faction = form.save(commit=False)
            faction.designer = request.user.profile
            faction.save()
            return redirect('forge-faction-detail', pk=faction.pk)
    else:
        form = ForgedFactionForm()
    return render(request, 'the_forge/forgedfaction_form.html', {
        'form': form, 'is_create': True,
        'background_presets': _background_preset_options(),
    })


@forge_onboard_required
def forgedfaction_detail(request, pk):
    faction = get_object_or_404(ForgedFaction, pk=pk)
    if not user_can_view_forge(request, faction):
        return redirect('forge-home')
    try:
        sheet = faction.faction_sheet
    except FactionSheet.DoesNotExist:
        sheet = None
    try:
        back = faction.faction_back
    except FactionBack.DoesNotExist:
        back = None
    try:
        setup_card = faction.setup_card
    except SetupCard.DoesNotExist:
        setup_card = None
    return render(request, 'the_forge/forgedfaction_detail.html', {
        'faction': faction,
        'can_edit': user_can_edit_forge(request, faction),
        'sheet': sheet,
        'back': back,
        'setup_card': setup_card,
    })


@forge_onboard_required
def forgedfaction_edit(request, pk):
    faction = get_object_or_404(ForgedFaction, pk=pk)
    if (resp := _forbid_if_not_editor(request, faction)):
        return resp
    if request.method == 'POST':
        form = ForgedFactionForm(request.POST, request.FILES, instance=faction)
        if form.is_valid():
            form.save()
            return redirect('forge-faction-detail', pk=faction.pk)
    else:
        form = ForgedFactionForm(instance=faction)
    return render(request, 'the_forge/forgedfaction_form.html', {
        'form': form, 'is_create': False, 'faction': faction,
        'background_presets': _background_preset_options(),
    })


@login_required
@require_http_methods(["POST"])
def forgedfaction_delete(request, pk):
    faction = get_object_or_404(ForgedFaction, pk=pk)
    if (resp := _forbid_if_not_editor(request, faction)):
        return resp
    faction.delete()
    return redirect('forge-faction-list')


# ---------- FactionSheet ----------

@forge_onboard_required
def factionsheet_create(request, faction_pk):
    faction = get_object_or_404(ForgedFaction, pk=faction_pk)
    if (resp := _forbid_if_not_editor(request, faction)):
        return resp
    sheet, _ = FactionSheet.objects.get_or_create(faction=faction)
    return redirect('forge-sheet-edit', pk=sheet.pk)


@forge_onboard_required
def factionsheet_edit(request, pk):
    sheet = get_object_or_404(FactionSheet, pk=pk)
    if (resp := _forbid_if_not_editor(request, sheet.faction)):
        return resp
    content_boxes = list(sheet.content_boxes.all())
    for _box in content_boxes:
        _box.annotated_steps = _annotate_steps(_box.steps.order_by('number'))
    return render(request, 'the_forge/factionsheet_editor.html', {
        'sheet': sheet,
        'faction': sheet.faction,
        'abilities': sheet.abilities.order_by('order'),
        'content_boxes': content_boxes,
        'phase_sections': [
            ('birdsong', 'Birdsong',
             _annotate_steps(sheet.phase_steps.filter(phase='birdsong').order_by('number')),
             'pdf/headers/BirdsongBarLong.png'),
            ('daylight', 'Daylight',
             _annotate_steps(sheet.phase_steps.filter(phase='daylight').order_by('number')),
             'pdf/headers/DaylightBarLong.png'),
            ('evening',  'Evening',
             _annotate_steps(sheet.phase_steps.filter(phase='evening').order_by('number')),
             'pdf/headers/EveningBarLong.png'),
        ],
        'decree': sheet.decrees.first(),
        'card_piles': sheet.card_piles.order_by('number'),
        'inline_keywords': _inline_keywords(sheet),
        'inline_images_map': _inline_images_map(sheet),
        'inline_images': _inline_images_map(sheet),
        'inline_image_labels': _inline_image_labels(sheet),
        'box_height_choices': BorderedBox.BoxSize.choices,
        'ability_form': FactionAbilityForm(),
        'content_box_form': ContentBoxForm(),
        'phase_step_form': PhaseStepForm(sheet=sheet),
        'decree_form': DecreeSectionForm(),
        'card_pile_form': CardPileForm(),
        'header_form': FactionHeaderForm(sheet=sheet),
        'character_images': sheet.character_images.order_by('order'),
        'character_image_form': CharacterImageForm(),
        'custom_inline_images': sheet.custom_inline_images.order_by('slot'),
        'custom_inline_image_form': CustomInlineImageForm(),
        'max_custom_inline_images': MAX_CUSTOM_INLINE_IMAGES,
    })


@forge_onboard_required
def factionsheet_preview(request, pk):
    sheet = get_object_or_404(FactionSheet, pk=pk)
    if (resp := _forbid_if_not_editor(request, sheet.faction)):
        return resp
    from .pdf_engine import SheetLayoutEngine
    from .layout_cache import get_or_compute_layout

    def compute(mode):
        return get_or_compute_layout(
            sheet,
            lambda: SheetLayoutEngine(sheet).compute_layout(layout_mode=mode),
            layout_mode=mode,
        )

    payloads = {
        'horizontal': compute('horizontal'),
        'vertical': compute('vertical'),
    }
    payload = payloads[sheet.layout_mode]
    import json
    return render(request, 'the_forge/factionsheet_preview.html', {
        'sheet': sheet,
        'faction': sheet.faction,
        'payload': payload,
        'payload_json': json.dumps(payload),
        'payloads_json': json.dumps(payloads),
    })


@login_required
@require_http_methods(["POST"])
def factionsheet_preview_save(request, pk):
    """Save layout overrides from the preview page form.

    Form fields:
      layout_mode: 'horizontal' | 'vertical'
      reset_to_auto: '1' to clear all overrides for the active layout
      phase_box_x, phase_box_y, phase_box_w, phase_box_h
      decree_y
      content_box_<id>_x, _y, _w, _h
      card_pile_<id>_x, _y

    Blank inputs → None (auto). The non-active layout's `_h`/`_v` fields are
    not touched.
    """
    sheet = get_object_or_404(FactionSheet, pk=pk)
    if (resp := _forbid_if_not_editor(request, sheet.faction)):
        return resp

    mode = request.POST.get('layout_mode', sheet.layout_mode)
    if mode not in ('horizontal', 'vertical'):
        return HttpResponseBadRequest("Invalid layout_mode")
    s = mode[0]  # 'h' or 'v'

    reset = request.POST.get('reset_to_auto') == '1'

    def parse(name):
        raw = request.POST.get(name, '').strip()
        if not raw:
            return None
        try:
            return float(raw)
        except ValueError:
            return None

    sheet.layout_mode = mode

    if reset:
        setattr(sheet, f'phase_box_x_{s}', None)
        setattr(sheet, f'phase_box_y_{s}', None)
        setattr(sheet, f'phase_box_w_{s}', None)
        setattr(sheet, f'phase_box_h_{s}', None)
        setattr(sheet, f'decree_y_{s}', None)
        sheet.save()
        for cb in sheet.content_boxes.all():
            setattr(cb, f'x_{s}', None)
            setattr(cb, f'y_{s}', None)
            setattr(cb, f'w_{s}', None)
            setattr(cb, f'h_{s}', None)
            cb.save()
        for cp in sheet.card_piles.all():
            setattr(cp, f'x_{s}', None)
            setattr(cp, f'y_{s}', None)
            setattr(cp, f'orientation_{s}', CardPile.Orientation.BOTTOM)
            cp.save()
        for ci in sheet.character_images.all():
            setattr(ci, f'x_{s}', None)
            setattr(ci, f'y_{s}', None)
            setattr(ci, f'width_{s}', None)
            ci.save()
        return redirect('forge-sheet-preview', pk=sheet.pk)

    setattr(sheet, f'phase_box_x_{s}', parse('phase_box_x'))
    setattr(sheet, f'phase_box_y_{s}', parse('phase_box_y'))
    setattr(sheet, f'phase_box_w_{s}', parse('phase_box_w'))
    setattr(sheet, f'phase_box_h_{s}', parse('phase_box_h'))
    if sheet.include_decree:
        setattr(sheet, f'decree_y_{s}', parse('decree_y'))
    sheet.save()

    for cb in sheet.content_boxes.all():
        setattr(cb, f'x_{s}', parse(f'content_box_{cb.id}_x'))
        setattr(cb, f'y_{s}', parse(f'content_box_{cb.id}_y'))
        setattr(cb, f'w_{s}', parse(f'content_box_{cb.id}_w'))
        setattr(cb, f'h_{s}', parse(f'content_box_{cb.id}_h'))
        cb.save()

    for cp in sheet.card_piles.all():
        setattr(cp, f'x_{s}', parse(f'card_pile_{cp.id}_x'))
        setattr(cp, f'y_{s}', parse(f'card_pile_{cp.id}_y'))
        raw_o = request.POST.get(f'card_pile_{cp.id}_orientation', '').strip()
        if raw_o in CardPile.Orientation.values:
            setattr(cp, f'orientation_{s}', raw_o)
        cp.save()

    for ci in sheet.character_images.all():
        setattr(ci, f'x_{s}', parse(f'character_image_{ci.id}_x'))
        setattr(ci, f'y_{s}', parse(f'character_image_{ci.id}_y'))
        setattr(ci, f'width_{s}', parse(f'character_image_{ci.id}_w'))
        ci.save()

    return redirect('forge-sheet-preview', pk=sheet.pk)


@login_required
@require_http_methods(["POST"])
def factionsheet_delete(request, pk):
    sheet = get_object_or_404(FactionSheet, pk=pk)
    if (resp := _forbid_if_not_editor(request, sheet.faction)):
        return resp
    faction_pk = sheet.faction_id
    sheet.delete()
    return redirect('forge-faction-detail', pk=faction_pk)


@player_required
@require_http_methods(["POST"])
def sheet_flavor_edit(request, pk):
    sheet = get_object_or_404(FactionSheet, pk=pk)
    if (resp := _forbid_if_not_editor(request, sheet.faction)):
        return resp
    sheet.flavor_text = request.POST.get('flavor_text', '')
    sheet.save(update_fields=['flavor_text'])
    return render(request, 'the_forge/partials/sheet_flavor_form.html', {'sheet': sheet})


@player_required
@require_http_methods(["POST"])
def sheet_header_edit(request, pk):
    sheet = get_object_or_404(FactionSheet, pk=pk)
    if (resp := _forbid_if_not_editor(request, sheet.faction)):
        return resp
    form = FactionHeaderForm(request.POST, request.FILES, sheet=sheet)
    if not form.is_valid():
        return HttpResponseBadRequest(form.errors.as_text())
    form.save()
    sheet.refresh_from_db()
    return render(request, 'the_forge/partials/sheet_header_form.html', {
        'sheet': sheet,
        'faction': sheet.faction,
        'header_form': FactionHeaderForm(sheet=sheet),
    })


@player_required
@require_http_methods(["POST"])
def sheet_crafted_toggle(request, pk):
    sheet = get_object_or_404(FactionSheet, pk=pk)
    if (resp := _forbid_if_not_editor(request, sheet.faction)):
        return resp
    sheet.include_crafted_items = request.POST.get('value') == 'true'
    sheet.save(update_fields=['include_crafted_items'])
    return HttpResponse(status=204)


@player_required
@require_http_methods(["POST"])
def sheet_layout_toggle(request, pk):
    sheet = get_object_or_404(FactionSheet, pk=pk)
    if (resp := _forbid_if_not_editor(request, sheet.faction)):
        return resp
    value = request.POST.get('value')
    if value not in {c.value for c in FactionSheet.LayoutChoices}:
        return HttpResponseBadRequest('Invalid layout value')
    sheet.layout_mode = value
    sheet.save(update_fields=['layout_mode'])
    return HttpResponse(status=204)


@player_required
@require_http_methods(["POST"])
def sheet_decree_toggle(request, pk):
    sheet = get_object_or_404(FactionSheet, pk=pk)
    if (resp := _forbid_if_not_editor(request, sheet.faction)):
        return resp
    turning_on = request.POST.get('value') == 'true'
    if turning_on:
        sheet.include_decree = True
        sheet.save(update_fields=['include_decree'])
        decree, _ = DecreeSection.objects.get_or_create(sheet=sheet)
        return render(request, 'the_forge/partials/decree_section.html', {
            'decree': decree,
            'inline_keywords': _inline_keywords(sheet),
        })
    sheet.decrees.all().delete()
    sheet.include_decree = False
    sheet.save(update_fields=['include_decree'])
    return HttpResponse(status=204)


# ---------- FactionBack ----------

@forge_onboard_required
def factionback_create(request, faction_pk):
    faction = get_object_or_404(ForgedFaction, pk=faction_pk)
    if (resp := _forbid_if_not_editor(request, faction)):
        return resp
    back, _ = FactionBack.objects.get_or_create(faction=faction)
    return redirect('forge-back-edit', pk=back.pk)


@forge_onboard_required
def factionback_edit(request, pk):
    back = get_object_or_404(FactionBack, pk=pk)
    if (resp := _forbid_if_not_editor(request, back.faction)):
        return resp
    valid_piece_types = {c.value for c in Piece.TypeChoices}
    if request.method == 'POST':
        form = FactionBackForm(request.POST, request.FILES, back=back)
        piece_ids = request.POST.getlist('piece_id')
        piece_types = request.POST.getlist('piece_type')
        piece_names = request.POST.getlist('piece_name')
        piece_quantities = request.POST.getlist('piece_quantity')
        piece_clear_flags = request.POST.getlist('piece_clear_icon')
        step_ids = request.POST.getlist('step_id')
        step_texts = request.POST.getlist('step_text')
        if form.is_valid():
            with transaction.atomic():
                form.save(back)

                existing_pieces = {p.pk: p for p in back.pieces.all()}
                kept_piece_ids = set()
                rows = list(zip(
                    piece_ids, piece_types, piece_names, piece_quantities,
                    piece_clear_flags,
                ))
                for index, (sid, ptype, name, qty, clear) in enumerate(rows):
                    upload = request.FILES.get(f'piece_icon_{index}')
                    name = (name or '').strip()
                    if not name:
                        continue
                    if ptype not in valid_piece_types:
                        continue
                    try:
                        quantity = max(1, min(99, int(qty or 1)))
                    except (TypeError, ValueError):
                        quantity = 1
                    has_new_file = bool(upload)
                    should_clear = (clear == '1') and not has_new_file
                    if sid and sid.isdigit() and int(sid) in existing_pieces:
                        piece = existing_pieces[int(sid)]
                        kept_piece_ids.add(piece.pk)
                        piece.name = name
                        piece.quantity = quantity
                        piece.type = ptype
                        if has_new_file:
                            piece.small_icon = upload
                        elif should_clear:
                            piece.small_icon = None
                        piece.save()
                    else:
                        new_piece = Piece(
                            parent=back,
                            name=name,
                            quantity=quantity,
                            type=ptype,
                        )
                        if has_new_file:
                            new_piece.small_icon = upload
                        new_piece.save()
                stale_piece_ids = set(existing_pieces) - kept_piece_ids
                if stale_piece_ids:
                    Piece.objects.filter(pk__in=stale_piece_ids).delete()

                step_pairs = [
                    (sid, txt) for sid, txt in zip(step_ids, step_texts)
                    if (txt or '').strip()
                ]
                existing_steps = {s.pk: s for s in back.setup_steps.all()}
                kept_step_ids = set()
                steps_to_update = []
                steps_to_create = []
                for index, (sid, text) in enumerate(step_pairs, start=1):
                    if sid and sid.isdigit() and int(sid) in existing_steps:
                        step = existing_steps[int(sid)]
                        kept_step_ids.add(step.pk)
                        if step.text != text or step.number != index:
                            step.text = text
                            step.number = index
                            steps_to_update.append(step)
                    else:
                        steps_to_create.append(SetupStep(
                            faction_back=back, number=index, text=text,
                        ))
                stale_step_ids = set(existing_steps) - kept_step_ids
                if stale_step_ids:
                    SetupStep.objects.filter(pk__in=stale_step_ids).delete()
                if steps_to_update:
                    SetupStep.objects.bulk_update(steps_to_update, ['text', 'number'])
                if steps_to_create:
                    SetupStep.objects.bulk_create(steps_to_create)
            return redirect('forge-back-edit', pk=back.pk)
    else:
        form = FactionBackForm(back=back)
    pieces_qs = list(back.pieces.order_by('id'))
    piece_sections = [
        ('W', 'Warriors', 'Warrior', [p for p in pieces_qs if p.type == 'W']),
        ('B', 'Buildings', 'Building', [p for p in pieces_qs if p.type == 'B']),
        ('T', 'Tokens', 'Token', [p for p in pieces_qs if p.type == 'T']),
        ('C', 'Cards', 'Card', [p for p in pieces_qs if p.type == 'C']),
        ('O', 'Other Pieces', 'Other Piece', [p for p in pieces_qs if p.type == 'O']),
    ]
    return render(request, 'the_forge/factionback_editor.html', {
        'back': back,
        'faction': back.faction,
        'form': form,
        'piece_sections': piece_sections,
        'setup_steps': _annotate_steps(back.setup_steps.order_by('number')),
        'inline_keywords': _inline_keywords(),
        'inline_images_map': _inline_images_map(),
        'attribute_fields': [
            ('complexity', 'Complexity'),
            ('aggression', 'Aggression'),
            ('card_wealth', 'Card Wealth'),
            ('crafting_ability', 'Crafting Ability'),
        ],
    })


# ---------- SetupCard (child of ForgedFaction) ----------

@forge_onboard_required
def setup_card_create(request, faction_pk):
    faction = get_object_or_404(ForgedFaction, pk=faction_pk)
    if (resp := _forbid_if_not_editor(request, faction)):
        return resp
    card, _ = SetupCard.objects.get_or_create(faction=faction)
    return redirect('forge-setup-card-edit', pk=card.pk)


@forge_onboard_required
def setup_card_edit(request, pk):
    card = get_object_or_404(SetupCard, pk=pk)
    if (resp := _forbid_if_not_editor(request, card.faction)):
        return resp
    if request.method == 'POST':
        form = SetupCardForm(request.POST, request.FILES, card=card)
        ids = request.POST.getlist('step_id')
        texts = request.POST.getlist('step_text')
        pairs = [(sid, txt) for sid, txt in zip(ids, texts) if txt.strip()]
        if form.is_valid():
            with transaction.atomic():
                form.save(card)
                existing = {s.pk: s for s in card.setup_steps.all()}
                kept_ids = set()
                to_update = []
                to_create = []
                for index, (sid, text) in enumerate(pairs, start=1):
                    if sid and sid.isdigit() and int(sid) in existing:
                        step = existing[int(sid)]
                        kept_ids.add(step.pk)
                        if step.text != text or step.number != index:
                            step.text = text
                            step.number = index
                            to_update.append(step)
                    else:
                        to_create.append(SetupStep(card=card, number=index, text=text))
                stale_ids = set(existing) - kept_ids
                if stale_ids:
                    SetupStep.objects.filter(pk__in=stale_ids).delete()
                if to_update:
                    SetupStep.objects.bulk_update(to_update, ['text', 'number'])
                if to_create:
                    SetupStep.objects.bulk_create(to_create)
            return redirect('forge-setup-card-edit', pk=card.pk)
    else:
        form = SetupCardForm(card=card)
    return render(request, 'the_forge/setup_card_editor.html', {
        'card': card,
        'faction': card.faction,
        'form': form,
        'setup_steps': _annotate_steps(card.setup_steps.order_by('number')),
        'inline_keywords': _inline_keywords(),
        'inline_images_map': _inline_images_map(),
    })


# ---------- FactionAbility (child of FactionSheet) ----------

@player_required
@require_http_methods(["POST"])
def ability_add(request, sheet_pk):
    sheet = get_object_or_404(FactionSheet, pk=sheet_pk)
    if (resp := _forbid_if_not_editor(request, sheet.faction)):
        return resp
    form = FactionAbilityForm(request.POST)
    if not form.is_valid():
        return HttpResponseBadRequest(str(form.errors))
    ability = form.save(commit=False)
    ability.sheet = sheet
    if not ability.order:
        ability.order = sheet.abilities.count() + 1
    ability.save()
    return render(request, 'the_forge/partials/ability_row.html', {
        'ability': ability, 'inline_keywords': _inline_keywords(ability.sheet),
    })


@player_required
@require_http_methods(["POST"])
def ability_edit(request, pk):
    ability = get_object_or_404(FactionAbility, pk=pk)
    if (resp := _forbid_if_not_editor(request, ability.sheet.faction)):
        return resp
    form = FactionAbilityForm(request.POST, instance=ability)
    if not form.is_valid():
        return HttpResponseBadRequest(str(form.errors))
    form.save()
    return render(request, 'the_forge/partials/ability_row.html', {
        'ability': ability, 'inline_keywords': _inline_keywords(ability.sheet),
    })


@player_required
@require_http_methods(["DELETE"])
def ability_delete(request, pk):
    ability = get_object_or_404(FactionAbility, pk=pk)
    if (resp := _forbid_if_not_editor(request, ability.sheet.faction)):
        return resp
    ability.delete()
    return HttpResponse(status=204)


@player_required
@require_http_methods(["POST"])
def ability_reorder(request, sheet_pk):
    sheet = get_object_or_404(FactionSheet, pk=sheet_pk)
    if (resp := _forbid_if_not_editor(request, sheet.faction)):
        return resp
    data = json.loads(request.body)
    for index, aid in enumerate(data.get('order', []), start=1):
        FactionAbility.objects.filter(id=aid, sheet=sheet).update(order=index)
    return HttpResponse(status=204)


# ---------- ContentBox (child of FactionSheet) ----------

_KIND_CHILD_FORMS = {
    ContentBox.KindChoices.BOX: BorderedBoxForm,
    ContentBox.KindChoices.TRACK: CardboardTrackForm,
    ContentBox.KindChoices.LEGEND: LegendForm,
    ContentBox.KindChoices.SCALE: ScaleForm,
    # ACTIONS uses StepActionForm but with extra action_type handling
}


@player_required
@require_http_methods(["POST"])
def contentbox_add(request, sheet_pk):
    sheet = get_object_or_404(FactionSheet, pk=sheet_pk)
    if (resp := _forbid_if_not_editor(request, sheet.faction)):
        return resp

    kind = request.POST.get('kind') or ContentBox.KindChoices.SECTION
    valid_kinds = {c.value for c in ContentBox.KindChoices}
    if kind not in valid_kinds:
        return HttpResponseBadRequest(f"Invalid kind: {kind}")

    if kind == ContentBox.KindChoices.SECTION:
        form = ContentBoxForm(request.POST)
        if not form.is_valid():
            return HttpResponseBadRequest(str(form.errors))
        box = form.save(commit=False)
        box.sheet = sheet
        box.kind = kind
        if not box.order:
            box.order = sheet.content_boxes.count() + 1
        box.save()
        box.annotated_steps = []
        return render(request, 'the_forge/partials/content_box_row.html', {
            'box': box, 'inline_keywords': _inline_keywords(box.sheet),
        })

    # Single-element kind: validate the child form first, then create
    # ContentBox + hidden PhaseStep + the chosen child in a single transaction.
    if kind == ContentBox.KindChoices.ACTIONS:
        action_type = request.POST.get('action_type', '')
        if action_type not in {c.value for c in PhaseStep.ActionType}:
            return HttpResponseBadRequest("Invalid action_type for Actions section.")
        child_form = StepActionForm(request.POST, request.FILES)
    else:
        FormCls = _KIND_CHILD_FORMS[kind]
        child_form = FormCls(request.POST, request.FILES)

    if not child_form.is_valid():
        return HttpResponseBadRequest(str(child_form.errors))

    if kind == ContentBox.KindChoices.ACTIONS:
        # Validate cost matches the chosen action_type before creating anything.
        # (PhaseStep.allowed_cost_choices needs an instance, but only reads action_type.)
        probe = PhaseStep(action_type=action_type)
        cost = child_form.cleaned_data.get('cost')
        if cost not in {v for v, _ in probe.allowed_cost_choices()}:
            return HttpResponseBadRequest(f"Cost '{cost}' is not allowed for this action_type.")

    with transaction.atomic():
        box = ContentBox.objects.create(
            sheet=sheet,
            kind=kind,
            order=sheet.content_boxes.count() + 1,
        )
        step_kwargs = {
            'sheet': sheet,
            'content_box': box,
            'phase': PhaseStep.PhaseChoices.OTHER,
            'number': 1,
        }
        if kind == ContentBox.KindChoices.ACTIONS:
            step_kwargs['action_type'] = action_type
        step = PhaseStep.objects.create(**step_kwargs)

        child = child_form.save(commit=False)
        child.step = step
        child.order = 1
        child.save()

    ensure_step_parent_fits(step, check_width=(kind == ContentBox.KindChoices.TRACK))
    box.annotated_steps = _annotate_steps(box.steps.order_by('number'))
    return render(request, 'the_forge/partials/content_box_row.html', {
        'box': box, 'inline_keywords': _inline_keywords(box.sheet),
        'inline_images': _inline_images_map(box.sheet),
    })


@player_required
@require_http_methods(["POST"])
def contentbox_edit(request, pk):
    box = get_object_or_404(ContentBox, pk=pk)
    if (resp := _forbid_if_not_editor(request, box.sheet.faction)):
        return resp
    form = ContentBoxForm(request.POST, instance=box)
    if not form.is_valid():
        return HttpResponseBadRequest(str(form.errors))
    form.save()
    box.annotated_steps = _annotate_steps(box.steps.order_by('number'))
    return render(request, 'the_forge/partials/content_box_row.html', {
        'box': box, 'inline_keywords': _inline_keywords(box.sheet),
    })


@player_required
@require_http_methods(["DELETE"])
def contentbox_delete(request, pk):
    box = get_object_or_404(ContentBox, pk=pk)
    if (resp := _forbid_if_not_editor(request, box.sheet.faction)):
        return resp
    sheet = box.sheet
    box.delete()
    for index, sibling in enumerate(sheet.content_boxes.order_by('order'), start=1):
        if sibling.order != index:
            ContentBox.objects.filter(pk=sibling.pk).update(order=index)
    return HttpResponse(status=204)


@player_required
@require_http_methods(["POST"])
def contentbox_reorder(request, sheet_pk):
    sheet = get_object_or_404(FactionSheet, pk=sheet_pk)
    if (resp := _forbid_if_not_editor(request, sheet.faction)):
        return resp
    data = json.loads(request.body)
    for index, bid in enumerate(data.get('order', []), start=1):
        ContentBox.objects.filter(id=bid, sheet=sheet).update(order=index)
    return HttpResponse(status=204)


# ---------- PhaseStep (child of FactionSheet) ----------

@player_required
@require_http_methods(["POST"])
def phasestep_add(request, sheet_pk):
    sheet = get_object_or_404(FactionSheet, pk=sheet_pk)
    if (resp := _forbid_if_not_editor(request, sheet.faction)):
        return resp
    form = PhaseStepForm(request.POST, sheet=sheet)
    if not form.is_valid():
        return HttpResponseBadRequest(str(form.errors))
    step = form.save(commit=False)
    step.sheet = sheet
    if step.phase != PhaseStep.PhaseChoices.OTHER:
        step.content_box = None
    if step.content_box and step.content_box.sheet_id != sheet.id:
        return HttpResponseBadRequest("content_box does not belong to this sheet")
    if step.content_box:
        step.number = sheet.phase_steps.filter(content_box=step.content_box).count() + 1
    else:
        step.number = sheet.phase_steps.filter(phase=step.phase, content_box__isnull=True).count() + 1
    step.save()
    ensure_step_parent_fits(step)
    return render(request, 'the_forge/partials/phase_step_row.html', {
        'step': _annotate_step(step), 'inline_keywords': _inline_keywords(step.sheet),
    })


@player_required
@require_http_methods(["POST"])
def phasestep_edit(request, pk):
    step = get_object_or_404(PhaseStep, pk=pk)
    if (resp := _forbid_if_not_editor(request, step.sheet.faction)):
        return resp
    post = request.POST.copy()
    post['phase'] = step.phase
    if step.content_box_id:
        post['content_box'] = step.content_box_id
    form = PhaseStepForm(post, instance=step, sheet=step.sheet)
    if not form.is_valid():
        return HttpResponseBadRequest(str(form.errors))
    form.save()
    ensure_step_parent_fits(step)
    return render(request, 'the_forge/partials/phase_step_row.html', {
        'step': _annotate_step(step), 'inline_keywords': _inline_keywords(step.sheet),
    })


@player_required
@require_http_methods(["DELETE"])
def phasestep_delete(request, pk):
    step = get_object_or_404(PhaseStep, pk=pk)
    if (resp := _forbid_if_not_editor(request, step.sheet.faction)):
        return resp
    sheet = step.sheet
    phase = step.phase
    content_box_id = step.content_box_id
    step.delete()
    if content_box_id:
        siblings = sheet.phase_steps.filter(content_box_id=content_box_id).order_by('number')
    else:
        siblings = sheet.phase_steps.filter(phase=phase, content_box__isnull=True).order_by('number')
    for index, sibling in enumerate(siblings, start=1):
        if sibling.number != index:
            PhaseStep.objects.filter(pk=sibling.pk).update(number=index)
    return HttpResponse(status=204)


@player_required
@require_http_methods(["POST"])
def phasestep_reorder(request, sheet_pk, phase):
    sheet = get_object_or_404(FactionSheet, pk=sheet_pk)
    if (resp := _forbid_if_not_editor(request, sheet.faction)):
        return resp
    data = json.loads(request.body)
    for index, sid in enumerate(data.get('order', []), start=1):
        PhaseStep.objects.filter(id=sid, sheet=sheet).update(number=index, phase=phase)
    return HttpResponse(status=204)


@player_required
@require_http_methods(["POST"])
def phasestep_reorder_in_box(request, content_box_pk):
    box = get_object_or_404(ContentBox, pk=content_box_pk)
    if (resp := _forbid_if_not_editor(request, box.sheet.faction)):
        return resp
    data = json.loads(request.body)
    for index, sid in enumerate(data.get('order', []), start=1):
        PhaseStep.objects.filter(id=sid, content_box=box).update(number=index)
    return HttpResponse(status=204)


# ---------- StepAction (child of PhaseStep) ----------

def _track_render_ctx(track):
    sheet = _sheet_from(track)
    return {
        'track': track,
        'inline_images': _inline_images_map(sheet),
        'inline_keywords': _inline_keywords(sheet),
        'inline_image_labels': _inline_image_labels(sheet),
    }


@player_required
@require_http_methods(["POST"])
def stepaction_add(request, step_pk):
    step = get_object_or_404(PhaseStep, pk=step_pk)
    if (resp := _forbid_if_not_editor(request, step.sheet.faction)):
        return resp
    form = StepActionForm(request.POST, request.FILES)
    if not form.is_valid():
        return HttpResponseBadRequest(str(form.errors))
    cost = form.cleaned_data.get('cost')
    if cost not in {v for v, _ in step.allowed_cost_choices()}:
        return HttpResponseBadRequest(f"Cost '{cost}' is not allowed for this step's action_type.")
    action = form.save(commit=False)
    action.step = step
    action.order = step.actions.count() + 1
    action.save()
    ensure_step_parent_fits(step)
    return render(request, 'the_forge/partials/step_action_row.html', {
        'action': action, 'inline_keywords': _inline_keywords(step.sheet),
    })


@player_required
@require_http_methods(["POST"])
def stepaction_edit(request, pk):
    action = get_object_or_404(StepAction, pk=pk)
    if (resp := _forbid_if_not_editor(request, action.step.sheet.faction)):
        return resp
    form = StepActionForm(request.POST, request.FILES, instance=action)
    if not form.is_valid():
        return HttpResponseBadRequest(str(form.errors))
    form.save()
    ensure_step_parent_fits(action.step)
    return render(request, 'the_forge/partials/step_action_row.html', {
        'action': action, 'inline_keywords': _inline_keywords(action.step.sheet),
    })


@player_required
@require_http_methods(["DELETE"])
def stepaction_delete(request, pk):
    action = get_object_or_404(StepAction, pk=pk)
    if (resp := _forbid_if_not_editor(request, action.step.sheet.faction)):
        return resp
    action.delete()
    return HttpResponse(status=204)


@player_required
@require_http_methods(["POST"])
def stepaction_reorder(request, step_pk):
    step = get_object_or_404(PhaseStep, pk=step_pk)
    if (resp := _forbid_if_not_editor(request, step.sheet.faction)):
        return resp
    data = json.loads(request.body)
    for index, aid in enumerate(data.get('order', []), start=1):
        StepAction.objects.filter(id=aid, step=step).update(order=index)
    return HttpResponse(status=204)


@player_required
@require_http_methods(["GET"])
def stepaction_form(request, step_pk):
    step = get_object_or_404(PhaseStep, pk=step_pk)
    if (resp := _forbid_if_not_editor(request, step.sheet.faction)):
        return resp
    return render(request, 'the_forge/partials/step_action_form.html', {
        'step': step, 'step_pk': step.pk, 'inline_keywords': _inline_keywords(step.sheet),
    })


@player_required
@require_http_methods(["POST"])
def phasestep_action_type_set(request, step_pk):
    step = get_object_or_404(PhaseStep, pk=step_pk)
    if (resp := _forbid_if_not_editor(request, step.sheet.faction)):
        return resp
    if step.actions.exists():
        return HttpResponseBadRequest("Cannot change action type while actions exist.")
    new_type = request.POST.get('action_type', '')
    valid = {c.value for c in PhaseStep.ActionType}
    if new_type not in valid:
        return HttpResponseBadRequest("Invalid action_type.")
    step.action_type = new_type
    step.save(update_fields=['action_type'])
    return render(request, 'the_forge/partials/phase_step_action_header.html', {'step': step})


@player_required
@require_http_methods(["POST"])
def phasestep_cost_image_set(request, step_pk):
    step = get_object_or_404(PhaseStep, pk=step_pk)
    if (resp := _forbid_if_not_editor(request, step.sheet.faction)):
        return resp
    form = PhaseStepCostImageForm(request.POST, request.FILES, instance=step)
    if not form.is_valid():
        return HttpResponseBadRequest(str(form.errors))
    form.save()
    step.refresh_from_db()
    return render(request, 'the_forge/partials/phase_step_action_header.html', {'step': step})


# ---------- BorderedBox (child of PhaseStep) ----------

@player_required
@require_http_methods(["POST"])
def borderedbox_add(request, step_pk):
    step = get_object_or_404(PhaseStep, pk=step_pk)
    if (resp := _forbid_if_not_editor(request, step.sheet.faction)):
        return resp
    form = BorderedBoxForm(request.POST)
    if not form.is_valid():
        return HttpResponseBadRequest(str(form.errors))
    box = form.save(commit=False)
    box.step = step
    box.order = step.boxes.count() + step.tracks.count() + 1
    box.save()
    ensure_step_parent_fits(step)
    return render(request, 'the_forge/partials/bordered_box_row.html', {
        'box': box, 'inline_keywords': _inline_keywords(step.sheet),
    })


@player_required
@require_http_methods(["POST"])
def borderedbox_edit(request, pk):
    box = get_object_or_404(BorderedBox, pk=pk)
    if (resp := _forbid_if_not_editor(request, box.step.sheet.faction)):
        return resp
    form = BorderedBoxForm(request.POST, instance=box)
    if not form.is_valid():
        return HttpResponseBadRequest(str(form.errors))
    form.save()
    ensure_step_parent_fits(box.step)
    return render(request, 'the_forge/partials/bordered_box_row.html', {
        'box': box, 'inline_keywords': _inline_keywords(box.step.sheet),
    })


@player_required
@require_http_methods(["DELETE"])
def borderedbox_delete(request, pk):
    box = get_object_or_404(BorderedBox, pk=pk)
    if (resp := _forbid_if_not_editor(request, box.step.sheet.faction)):
        return resp
    box.delete()
    return HttpResponse(status=204)


@player_required
@require_http_methods(["POST"])
def step_children_reorder(request, step_pk):
    """Reorder the merged boxes+tracks list for a step.
    Body: {"order": [{"kind": "box"|"track", "id": <pk>}, ...]}
    Each model's `order` field is rewritten so the merged list round-trips.
    """
    step = get_object_or_404(PhaseStep, pk=step_pk)
    if (resp := _forbid_if_not_editor(request, step.sheet.faction)):
        return resp
    data = json.loads(request.body)
    for index, item in enumerate(data.get('order', []), start=1):
        kind = item.get('kind')
        oid = item.get('id')
        if kind == 'box':
            BorderedBox.objects.filter(id=oid, step=step).update(order=index)
        elif kind == 'track':
            CardboardTrack.objects.filter(id=oid, step=step).update(order=index)
        elif kind == 'legend':
            Legend.objects.filter(id=oid, step=step).update(order=index)
        elif kind == 'scale':
            Scale.objects.filter(id=oid, step=step).update(order=index)
    return HttpResponse(status=204)


# ---------- CardboardTrack (child of PhaseStep) ----------

@player_required
@require_http_methods(["POST"])
def track_add(request, step_pk):
    step = get_object_or_404(PhaseStep, pk=step_pk)
    if (resp := _forbid_if_not_editor(request, step.sheet.faction)):
        return resp
    form = CardboardTrackForm(request.POST, request.FILES)
    if not form.is_valid():
        return HttpResponseBadRequest(str(form.errors))
    track = form.save(commit=False)
    track.step = step
    track.order = step.boxes.count() + step.tracks.count() + 1
    track.save()
    ensure_step_parent_fits(step, check_width=True)
    return render(request, 'the_forge/partials/track_row.html', _track_render_ctx(track))


@player_required
@require_http_methods(["POST"])
def track_edit(request, pk):
    track = get_object_or_404(CardboardTrack, pk=pk)
    if (resp := _forbid_if_not_editor(request, track.step.sheet.faction)):
        return resp
    prev_cols = track.num_columns
    form = CardboardTrackForm(request.POST, request.FILES, instance=track)
    if not form.is_valid():
        return HttpResponseBadRequest(str(form.errors))
    new_cols = form.cleaned_data.get('num_columns', track.num_columns)
    new_rows = form.cleaned_data.get('num_rows', track.num_rows)
    form.save()
    track.slots.filter(column__gte=new_cols).delete()
    track.slots.filter(row__gte=new_rows).delete()
    ensure_step_parent_fits(track.step, check_width=(new_cols > prev_cols))
    return render(request, 'the_forge/partials/track_row.html', _track_render_ctx(track))


@player_required
@require_http_methods(["DELETE"])
def track_delete(request, pk):
    track = get_object_or_404(CardboardTrack, pk=pk)
    if (resp := _forbid_if_not_editor(request, track.step.sheet.faction)):
        return resp
    track.delete()
    return HttpResponse(status=204)


# ---------- Legend (child of PhaseStep) ----------

def _next_step_child_order(step):
    """Next `order` for a new mixed-list child of a PhaseStep."""
    return (step.boxes.count() + step.tracks.count()
            + step.legends.count() + step.scales.count() + 1)


@player_required
@require_http_methods(["POST"])
def legend_add(request, step_pk):
    step = get_object_or_404(PhaseStep, pk=step_pk)
    if (resp := _forbid_if_not_editor(request, step.sheet.faction)):
        return resp
    form = LegendForm(request.POST)
    if not form.is_valid():
        return HttpResponseBadRequest(str(form.errors))
    legend = form.save(commit=False)
    legend.step = step
    legend.order = _next_step_child_order(step)
    legend.save()
    ensure_step_parent_fits(step)
    return render(request, 'the_forge/partials/legend_row.html', {
        'legend': legend, 'inline_keywords': _inline_keywords(step.sheet),
        'inline_images': _inline_images_map(step.sheet),
    })


@player_required
@require_http_methods(["POST"])
def legend_edit(request, pk):
    legend = get_object_or_404(Legend, pk=pk)
    if (resp := _forbid_if_not_editor(request, legend.step.sheet.faction)):
        return resp
    form = LegendForm(request.POST, instance=legend)
    if not form.is_valid():
        return HttpResponseBadRequest(str(form.errors))
    form.save()
    ensure_step_parent_fits(legend.step)
    return render(request, 'the_forge/partials/legend_row.html', {
        'legend': legend, 'inline_keywords': _inline_keywords(legend.step.sheet),
        'inline_images': _inline_images_map(legend.step.sheet),
    })


@player_required
@require_http_methods(["DELETE"])
def legend_delete(request, pk):
    legend = get_object_or_404(Legend, pk=pk)
    if (resp := _forbid_if_not_editor(request, legend.step.sheet.faction)):
        return resp
    legend.delete()
    return HttpResponse(status=204)


@player_required
@require_http_methods(["POST"])
def legend_row_add(request, legend_pk):
    legend = get_object_or_404(Legend, pk=legend_pk)
    if (resp := _forbid_if_not_editor(request, legend.step.sheet.faction)):
        return resp
    form = LegendRowForm(request.POST, request.FILES)
    if not form.is_valid():
        return HttpResponseBadRequest(str(form.errors))
    row = form.save(commit=False)
    row.legend = legend
    row.order = legend.rows.count() + 1
    row.save()
    ensure_step_parent_fits(legend.step)
    return render(request, 'the_forge/partials/legend_row_entry.html', {
        'row': row, 'inline_keywords': _inline_keywords(legend.step.sheet),
        'inline_images': _inline_images_map(legend.step.sheet),
    })


@player_required
@require_http_methods(["POST"])
def legend_row_edit(request, pk):
    row = get_object_or_404(LegendRow, pk=pk)
    if (resp := _forbid_if_not_editor(request, row.legend.step.sheet.faction)):
        return resp
    form = LegendRowForm(request.POST, request.FILES, instance=row)
    if not form.is_valid():
        return HttpResponseBadRequest(str(form.errors))
    form.save()
    ensure_step_parent_fits(row.legend.step)
    return render(request, 'the_forge/partials/legend_row_entry.html', {
        'row': row, 'inline_keywords': _inline_keywords(row.legend.step.sheet),
        'inline_images': _inline_images_map(row.legend.step.sheet),
    })


@player_required
@require_http_methods(["DELETE"])
def legend_row_delete(request, pk):
    row = get_object_or_404(LegendRow, pk=pk)
    if (resp := _forbid_if_not_editor(request, row.legend.step.sheet.faction)):
        return resp
    row.delete()
    return HttpResponse(status=204)


# ---------- Scale (child of PhaseStep) ----------

@player_required
@require_http_methods(["POST"])
def scale_add(request, step_pk):
    step = get_object_or_404(PhaseStep, pk=step_pk)
    if (resp := _forbid_if_not_editor(request, step.sheet.faction)):
        return resp
    form = ScaleForm(request.POST)
    if not form.is_valid():
        return HttpResponseBadRequest(str(form.errors))
    scale = form.save(commit=False)
    scale.step = step
    scale.order = _next_step_child_order(step)
    scale.save()
    ensure_step_parent_fits(step)
    return render(request, 'the_forge/partials/scale_row.html', {
        'scale': scale, 'inline_keywords': _inline_keywords(step.sheet),
        'inline_images': _inline_images_map(step.sheet),
        'inline_image_labels': _inline_image_labels(step.sheet),
    })


@player_required
@require_http_methods(["POST"])
def scale_edit(request, pk):
    scale = get_object_or_404(Scale, pk=pk)
    if (resp := _forbid_if_not_editor(request, scale.step.sheet.faction)):
        return resp
    form = ScaleForm(request.POST, instance=scale)
    if not form.is_valid():
        return HttpResponseBadRequest(str(form.errors))
    form.save()
    ensure_step_parent_fits(scale.step)
    return render(request, 'the_forge/partials/scale_row.html', {
        'scale': scale, 'inline_keywords': _inline_keywords(scale.step.sheet),
        'inline_images': _inline_images_map(scale.step.sheet),
        'inline_image_labels': _inline_image_labels(scale.step.sheet),
    })


@player_required
@require_http_methods(["DELETE"])
def scale_delete(request, pk):
    scale = get_object_or_404(Scale, pk=pk)
    if (resp := _forbid_if_not_editor(request, scale.step.sheet.faction)):
        return resp
    scale.delete()
    return HttpResponse(status=204)


@player_required
@require_http_methods(["POST"])
def scale_save(request, pk):
    """Save all of a Scale's rows at once.

    POST contains parallel arrays: row_id (blank for new), range, result —
    one entry per row. Existing rows missing from the payload are deleted.
    Order is taken from the order entries appear in POST.
    """
    scale = get_object_or_404(Scale, pk=pk)
    if (resp := _forbid_if_not_editor(request, scale.step.sheet.faction)):
        return resp
    row_ids = request.POST.getlist('row_id')
    ranges = request.POST.getlist('range')
    results = request.POST.getlist('result')
    if not (len(row_ids) == len(ranges) == len(results)):
        return HttpResponseBadRequest('row_id/range/result length mismatch')
    submitted_existing = {int(rid) for rid in row_ids if rid}
    # Delete rows that were removed client-side
    scale.rows.exclude(pk__in=submitted_existing).delete()
    # Upsert in submitted order
    for order, (rid, rng, res) in enumerate(zip(row_ids, ranges, results), start=1):
        rng = (rng or '').strip()
        res = (res or '').strip()
        if not rng or not res:
            return HttpResponseBadRequest('Range and result are required for every row')
        if rid:
            ScaleRow.objects.filter(pk=int(rid), scale=scale).update(
                range=rng, result=res, order=order)
        else:
            ScaleRow.objects.create(scale=scale, range=rng, result=res, order=order)
    ensure_step_parent_fits(scale.step, check_width=True)
    return render(request, 'the_forge/partials/scale_row.html', {
        'scale': scale, 'inline_keywords': _inline_keywords(scale.step.sheet),
        'inline_images': _inline_images_map(scale.step.sheet),
        'inline_image_labels': _inline_image_labels(scale.step.sheet),
    })


# ---------- CardboardSlot (child of CardboardTrack) ----------

@player_required
@require_http_methods(["POST"])
def slot_upsert(request, track_pk, row, col):
    """Create or update the slot at (row, col) for the given track.
    Used by the slot edit modal — returns the rendered slot cell partial."""
    track = get_object_or_404(CardboardTrack, pk=track_pk)
    if (resp := _forbid_if_not_editor(request, track.step.sheet.faction)):
        return resp
    if row >= track.num_rows or col >= track.num_columns:
        return HttpResponseBadRequest("Cell out of grid bounds.")
    slot, _ = CardboardSlot.objects.get_or_create(
        track=track, row=row, column=col,
        defaults={'number': track.slots.count() + 1},
    )
    form = CardboardSlotForm(request.POST, request.FILES, instance=slot)
    if not form.is_valid():
        return HttpResponseBadRequest(str(form.errors))
    form.save()
    return render(request, 'the_forge/partials/track_slot_cell.html', {
        'track': track, 'slot': slot, 'row': row, 'col': col,
        'inline_images': _inline_images_map(track.step.sheet),
    })


@player_required
@require_http_methods(["DELETE"])
def slot_delete(request, pk):
    slot = get_object_or_404(CardboardSlot, pk=pk)
    if (resp := _forbid_if_not_editor(request, slot.track.step.sheet.faction)):
        return resp
    slot.delete()
    return HttpResponse(status=204)


# ---------- DecreeSection + CardSlot ----------

@player_required
@require_http_methods(["POST"])
def decree_edit(request, pk):
    decree = get_object_or_404(DecreeSection, pk=pk)
    if (resp := _forbid_if_not_editor(request, decree.sheet.faction)):
        return resp
    form = DecreeSectionForm(request.POST, instance=decree)
    if not form.is_valid():
        return HttpResponseBadRequest(str(form.errors))
    form.save()
    return render(request, 'the_forge/partials/decree_title_form.html', {'decree': decree})


@player_required
@require_http_methods(["POST"])
def cardslot_add(request, decree_pk):
    decree = get_object_or_404(DecreeSection, pk=decree_pk)
    if (resp := _forbid_if_not_editor(request, decree.sheet.faction)):
        return resp
    if decree.card_slots.count() >= MAX_CARD_SLOTS:
        return HttpResponseBadRequest(f"Maximum of {MAX_CARD_SLOTS} card slots reached.")
    form = CardSlotForm(request.POST)
    if not form.is_valid():
        return HttpResponseBadRequest(str(form.errors))
    slot = form.save(commit=False)
    slot.decree = decree
    if not slot.number:
        slot.number = decree.card_slots.count() + 1
    slot.save()
    return render(request, 'the_forge/partials/card_slot_row.html', {
        'slot': slot, 'inline_keywords': _inline_keywords(decree.sheet),
    })


@player_required
@require_http_methods(["POST"])
def cardslot_reorder(request, decree_pk):
    decree = get_object_or_404(DecreeSection, pk=decree_pk)
    if (resp := _forbid_if_not_editor(request, decree.sheet.faction)):
        return resp
    data = json.loads(request.body)
    for index, sid in enumerate(data.get('order', []), start=1):
        CardSlot.objects.filter(id=sid, decree=decree).update(number=index)
    return HttpResponse(status=204)


@player_required
@require_http_methods(["POST"])
def cardslot_edit(request, pk):
    slot = get_object_or_404(CardSlot, pk=pk)
    if (resp := _forbid_if_not_editor(request, slot.decree.sheet.faction)):
        return resp
    form = CardSlotForm(request.POST, instance=slot)
    if not form.is_valid():
        return HttpResponseBadRequest(str(form.errors))
    form.save()
    return render(request, 'the_forge/partials/card_slot_row.html', {
        'slot': slot, 'inline_keywords': _inline_keywords(slot.decree.sheet),
    })


@player_required
@require_http_methods(["DELETE"])
def cardslot_delete(request, pk):
    slot = get_object_or_404(CardSlot, pk=pk)
    if (resp := _forbid_if_not_editor(request, slot.decree.sheet.faction)):
        return resp
    decree = slot.decree
    slot.delete()
    for index, sibling in enumerate(decree.card_slots.order_by('number'), start=1):
        if sibling.number != index:
            CardSlot.objects.filter(pk=sibling.pk).update(number=index)
    return HttpResponse(status=204)


# ---------- CardPile (child of FactionSheet) ----------

@player_required
@require_http_methods(["POST"])
def cardpile_add(request, sheet_pk):
    sheet = get_object_or_404(FactionSheet, pk=sheet_pk)
    if (resp := _forbid_if_not_editor(request, sheet.faction)):
        return resp
    if sheet.card_piles.count() >= MAX_CARD_PILES:
        return HttpResponseBadRequest(f"Maximum of {MAX_CARD_PILES} card piles reached.")
    form = CardPileForm(request.POST)
    if not form.is_valid():
        return HttpResponseBadRequest(str(form.errors))
    pile = form.save(commit=False)
    pile.sheet = sheet
    if not pile.number:
        pile.number = sheet.card_piles.count() + 1
    pile.save()
    return render(request, 'the_forge/partials/card_pile_row.html', {
        'pile': pile, 'inline_keywords': _inline_keywords(sheet),
    })


@player_required
@require_http_methods(["POST"])
def cardpile_edit(request, pk):
    pile = get_object_or_404(CardPile, pk=pk)
    if (resp := _forbid_if_not_editor(request, pile.sheet.faction)):
        return resp
    form = CardPileForm(request.POST, instance=pile)
    if not form.is_valid():
        return HttpResponseBadRequest(str(form.errors))
    form.save()
    return render(request, 'the_forge/partials/card_pile_row.html', {
        'pile': pile, 'inline_keywords': _inline_keywords(pile.sheet),
    })


@player_required
@require_http_methods(["DELETE"])
def cardpile_delete(request, pk):
    pile = get_object_or_404(CardPile, pk=pk)
    if (resp := _forbid_if_not_editor(request, pile.sheet.faction)):
        return resp
    sheet = pile.sheet
    pile.delete()
    for index, sibling in enumerate(sheet.card_piles.order_by('number'), start=1):
        if sibling.number != index:
            CardPile.objects.filter(pk=sibling.pk).update(number=index)
    return HttpResponse(status=204)


@player_required
@require_http_methods(["POST"])
def cardpile_reorder(request, sheet_pk):
    sheet = get_object_or_404(FactionSheet, pk=sheet_pk)
    if (resp := _forbid_if_not_editor(request, sheet.faction)):
        return resp
    data = json.loads(request.body)
    for index, pid in enumerate(data.get('order', []), start=1):
        CardPile.objects.filter(id=pid, sheet=sheet).update(number=index)
    return HttpResponse(status=204)


# ---------- CharacterImage ----------

@player_required
@require_http_methods(["POST"])
def character_image_add(request, sheet_pk):
    sheet = get_object_or_404(FactionSheet, pk=sheet_pk)
    if (resp := _forbid_if_not_editor(request, sheet.faction)):
        return resp
    if sheet.character_images.count() >= MAX_CHARACTER_IMAGES:
        return HttpResponseBadRequest(f"Maximum of {MAX_CHARACTER_IMAGES} character images reached.")
    form = CharacterImageForm(request.POST, request.FILES)
    if not form.is_valid():
        return HttpResponseBadRequest(str(form.errors))
    ci = form.save(commit=False)
    ci.sheet = sheet
    ci.order = sheet.character_images.count() + 1
    ci.save()
    return render(request, 'the_forge/partials/character_image_row.html', {'ci': ci})


@player_required
@require_http_methods(["POST"])
def character_image_edit(request, pk):
    ci = get_object_or_404(CharacterImage, pk=pk)
    if (resp := _forbid_if_not_editor(request, ci.sheet.faction)):
        return resp
    form = CharacterImageForm(request.POST, request.FILES, instance=ci)
    if not form.is_valid():
        return HttpResponseBadRequest(str(form.errors))
    form.save()
    return render(request, 'the_forge/partials/character_image_row.html', {'ci': ci})


@player_required
@require_http_methods(["POST", "DELETE"])
def character_image_delete(request, pk):
    ci = get_object_or_404(CharacterImage, pk=pk)
    if (resp := _forbid_if_not_editor(request, ci.sheet.faction)):
        return resp
    sheet = ci.sheet
    ci.delete()
    for index, sibling in enumerate(sheet.character_images.order_by('order'), start=1):
        if sibling.order != index:
            CharacterImage.objects.filter(pk=sibling.pk).update(order=index)
    return HttpResponse(status=204)


# ---------- CustomInlineImage ----------

def _next_free_custom_inline_slot(sheet):
    used = set(sheet.custom_inline_images.values_list('slot', flat=True))
    for n in range(MAX_CUSTOM_INLINE_IMAGES):
        if n not in used:
            return n
    return None


@player_required
@require_http_methods(["POST"])
def custom_inline_image_add(request, sheet_pk):
    sheet = get_object_or_404(FactionSheet, pk=sheet_pk)
    if (resp := _forbid_if_not_editor(request, sheet.faction)):
        return resp
    if sheet.custom_inline_images.count() >= MAX_CUSTOM_INLINE_IMAGES:
        return HttpResponseBadRequest(
            f"Maximum of {MAX_CUSTOM_INLINE_IMAGES} custom inline images reached."
        )
    slot = _next_free_custom_inline_slot(sheet)
    if slot is None:
        return HttpResponseBadRequest("No free slots available.")
    form = CustomInlineImageForm(request.POST, request.FILES)
    if not form.is_valid():
        return HttpResponseBadRequest(str(form.errors))
    ci = form.save(commit=False)
    ci.sheet = sheet
    ci.slot = slot
    ci.save()
    return render(request, 'the_forge/partials/custom_inline_image_row.html', {
        'ci': ci,
        'inline_images_map': _inline_images_map(sheet),
    })


@player_required
@require_http_methods(["POST"])
def custom_inline_image_edit(request, pk):
    ci = get_object_or_404(CustomInlineImage, pk=pk)
    if (resp := _forbid_if_not_editor(request, ci.sheet.faction)):
        return resp
    form = CustomInlineImageForm(request.POST, request.FILES, instance=ci)
    if not form.is_valid():
        return HttpResponseBadRequest(str(form.errors))
    form.save()
    return render(request, 'the_forge/partials/custom_inline_image_row.html', {
        'ci': ci,
        'inline_images_map': _inline_images_map(ci.sheet),
    })


@player_required
@require_http_methods(["GET"])
def sheet_inline_images_json(request, sheet_pk):
    """Return the sheet's full inline-image map (built-ins + per-sheet uploads).

    The editor JS calls this after a CustomInlineImage add/edit/delete so the
    open picker panels and the cached <script id="forge-inline-images"> map
    pick up the change without a full page reload. `labels` lets the picker
    show a user-friendly tooltip (the custom image's name) for per-sheet
    keywords; built-ins fall back to the keyword itself.
    """
    sheet = get_object_or_404(FactionSheet, pk=sheet_pk)
    if (resp := _forbid_if_not_editor(request, sheet.faction)):
        return resp
    labels = {ci.keyword: ci.name for ci in sheet.custom_inline_images.all()}
    return JsonResponse({
        'keywords': _inline_keywords(sheet),
        'images': _inline_images_map(sheet),
        'labels': labels,
    })


# ---------- PDF download ----------

def _pdf_file_response(data, filename):
    from io import BytesIO
    response = FileResponse(BytesIO(data), content_type='application/pdf')
    response['Content-Disposition'] = f'inline; filename="{filename}"'
    return response


def _webp_file_response(image_field, filename):
    response = FileResponse(image_field.open('rb'), content_type='image/webp')
    response['Content-Disposition'] = f'inline; filename="{filename}"'
    return response


def _maybe_save_image_preview(instance, pdf_bytes, fingerprint, field_prefix):
    if instance.preview_fingerprint == fingerprint and instance.image_preview:
        return
    from django.core.files.base import ContentFile
    from django.utils import timezone
    from .pdf_engine import pdf_bytes_to_webp_bytes
    try:
        webp = pdf_bytes_to_webp_bytes(pdf_bytes)
    except Exception:
        return
    if instance.image_preview:
        instance.image_preview.delete(save=False)
    filename = f'{field_prefix}_{instance.pk}.webp'
    instance.image_preview.save(filename, ContentFile(webp), save=False)
    instance.preview_fingerprint = fingerprint
    instance.last_generated = timezone.now()
    instance.save(update_fields=['image_preview', 'preview_fingerprint', 'last_generated'])


@login_required
def forgedfaction_pdf(request, pk):
    faction = get_object_or_404(ForgedFaction, pk=pk)
    if (resp := _forbid_if_not_editor(request, faction)):
        return resp
    from io import BytesIO
    from pypdf import PdfWriter, PdfReader
    from .pdf_engine import SheetLayoutEngine, FactionBackLayoutEngine, SetupCardLayoutEngine
    from .pdf_cache import (
        cache_key, fingerprint_sheet, fingerprint_back, fingerprint_setup_card, get_or_build,
    )

    parts = []

    sheet = getattr(faction, 'faction_sheet', None)
    if sheet:
        def build_sheet():
            buf = BytesIO()
            SheetLayoutEngine(sheet).build(buf)
            return buf.getvalue()
        sheet_fp = fingerprint_sheet(sheet)
        sheet_pdf = get_or_build(cache_key('sheet', sheet.pk, sheet_fp), build_sheet)
        _maybe_save_image_preview(sheet, sheet_pdf, sheet_fp, 'sheet')
        parts.append(sheet_pdf)

    back = getattr(faction, 'faction_back', None)
    if back:
        def build_back():
            buf = BytesIO()
            FactionBackLayoutEngine(back).build(buf)
            return buf.getvalue()
        back_fp = fingerprint_back(back)
        back_pdf = get_or_build(cache_key('back', back.pk, back_fp), build_back)
        _maybe_save_image_preview(back, back_pdf, back_fp, 'back')
        parts.append(back_pdf)

    card = getattr(faction, 'setup_card', None)
    if card:
        def build_card():
            buf = BytesIO()
            SetupCardLayoutEngine(card).build(buf)
            return buf.getvalue()
        card_fp = fingerprint_setup_card(card)
        card_pdf = get_or_build(cache_key('setup_card', card.pk, card_fp), build_card)
        _maybe_save_image_preview(card, card_pdf, card_fp, 'card')
        parts.append(card_pdf)

    if not parts:
        return HttpResponse(
            "No content yet — create a Front, Back, or Setup Card first.",
            status=404, content_type='text/plain',
        )

    writer = PdfWriter()
    for data in parts:
        for page in PdfReader(BytesIO(data)).pages:
            writer.add_page(page)
    out = BytesIO()
    writer.write(out)
    return _pdf_file_response(out.getvalue(), f'{faction.faction_name}.pdf')


@login_required
def factionsheet_pdf(request, pk):
    sheet = get_object_or_404(FactionSheet, pk=pk)
    if (resp := _forbid_if_not_editor(request, sheet.faction)):
        return resp
    from io import BytesIO
    from .pdf_engine import SheetLayoutEngine
    from .pdf_cache import cache_key, fingerprint_sheet, get_or_build
    fp = fingerprint_sheet(sheet)
    key = cache_key('sheet', sheet.pk, fp)
    def build():
        buffer = BytesIO()
        SheetLayoutEngine(sheet).build(buffer)
        return buffer.getvalue()
    data = get_or_build(key, build)
    _maybe_save_image_preview(sheet, data, fp, 'sheet')
    return _pdf_file_response(data, f'{sheet.faction.faction_name} - Front.pdf')


@login_required
def factionsheet_webp(request, pk):
    sheet = get_object_or_404(FactionSheet, pk=pk)
    if (resp := _forbid_if_not_editor(request, sheet.faction)):
        return resp
    from io import BytesIO
    from .pdf_engine import SheetLayoutEngine
    from .pdf_cache import cache_key, fingerprint_sheet, get_or_build
    fp = fingerprint_sheet(sheet)
    if sheet.preview_fingerprint != fp or not sheet.image_preview:
        def build():
            buffer = BytesIO()
            SheetLayoutEngine(sheet).build(buffer)
            return buffer.getvalue()
        data = get_or_build(cache_key('sheet', sheet.pk, fp), build)
        _maybe_save_image_preview(sheet, data, fp, 'sheet')
        sheet.refresh_from_db()
    if not sheet.image_preview:
        return HttpResponse("Preview unavailable.", status=404, content_type='text/plain')
    return _webp_file_response(sheet.image_preview, f'{sheet.faction.faction_name} - Front.webp')


@login_required
def factionback_pdf(request, pk):
    back = get_object_or_404(FactionBack, pk=pk)
    if (resp := _forbid_if_not_editor(request, back.faction)):
        return resp
    from io import BytesIO
    from .pdf_engine import FactionBackLayoutEngine
    from .pdf_cache import cache_key, fingerprint_back, get_or_build
    fp = fingerprint_back(back)
    key = cache_key('back', back.pk, fp)
    def build():
        buffer = BytesIO()
        FactionBackLayoutEngine(back).build(buffer)
        return buffer.getvalue()
    data = get_or_build(key, build)
    _maybe_save_image_preview(back, data, fp, 'back')
    return _pdf_file_response(data, f'{back.faction.faction_name} - Back.pdf')


@login_required
def factionback_webp(request, pk):
    back = get_object_or_404(FactionBack, pk=pk)
    if (resp := _forbid_if_not_editor(request, back.faction)):
        return resp
    from io import BytesIO
    from .pdf_engine import FactionBackLayoutEngine
    from .pdf_cache import cache_key, fingerprint_back, get_or_build
    fp = fingerprint_back(back)
    if back.preview_fingerprint != fp or not back.image_preview:
        def build():
            buffer = BytesIO()
            FactionBackLayoutEngine(back).build(buffer)
            return buffer.getvalue()
        data = get_or_build(cache_key('back', back.pk, fp), build)
        _maybe_save_image_preview(back, data, fp, 'back')
        back.refresh_from_db()
    if not back.image_preview:
        return HttpResponse("Preview unavailable.", status=404, content_type='text/plain')
    return _webp_file_response(back.image_preview, f'{back.faction.faction_name} - Back.webp')


@login_required
def setup_card_pdf(request, pk):
    card = get_object_or_404(SetupCard, pk=pk)
    if (resp := _forbid_if_not_editor(request, card.faction)):
        return resp
    from io import BytesIO
    from .pdf_engine import SetupCardLayoutEngine
    from .pdf_cache import cache_key, fingerprint_setup_card, get_or_build
    fp = fingerprint_setup_card(card)
    key = cache_key('setup_card', card.pk, fp)
    def build():
        buffer = BytesIO()
        SetupCardLayoutEngine(card).build(buffer)
        return buffer.getvalue()
    data = get_or_build(key, build)
    _maybe_save_image_preview(card, data, fp, 'card')
    return _pdf_file_response(data, f'{card.faction.faction_name} - Adset.pdf')


@login_required
def setup_card_webp(request, pk):
    card = get_object_or_404(SetupCard, pk=pk)
    if (resp := _forbid_if_not_editor(request, card.faction)):
        return resp
    from io import BytesIO
    from .pdf_engine import SetupCardLayoutEngine
    from .pdf_cache import cache_key, fingerprint_setup_card, get_or_build
    fp = fingerprint_setup_card(card)
    if card.preview_fingerprint != fp or not card.image_preview:
        def build():
            buffer = BytesIO()
            SetupCardLayoutEngine(card).build(buffer)
            return buffer.getvalue()
        data = get_or_build(cache_key('setup_card', card.pk, fp), build)
        _maybe_save_image_preview(card, data, fp, 'card')
        card.refresh_from_db()
    if not card.image_preview:
        return HttpResponse("Preview unavailable.", status=404, content_type='text/plain')
    return _webp_file_response(card.image_preview, f'{card.faction.faction_name} - Adset.webp')
