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

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.urls import reverse
from the_gatehouse.views import admin_onboard_required, forge_onboard_required, player_required
from .inline_images import picker_image_map, picker_keywords, sheet_inline_images, sheet_picker_keywords
from .layout_autogrow import ensure_step_parent_fits

from the_gatehouse.models import MessageChoices, UserNotification
from the_gatehouse.utils import build_absolute_uri
from the_keep.models import Faction, PostTranslation
from the_keep.utils import delete_old_image
from the_gatehouse.tasks import send_discord_message_task, send_rich_discord_message_task

from .forms_publish import (
    ForgedFactionLinkForm,
    ForgedFactionSubmitForm,
    ForgedFactionSyncForm,
    submit_prerequisites_missing,
)

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
    FactionMarkersForm,
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
from .limits import (
    MAX_ABILITY_BODY,
    MAX_ABILITY_TITLE,
    MAX_BACK_SETUP_STEPS,
    MAX_CARD_PILES,
    MAX_CARD_SETUP_STEPS,
    MAX_CARD_SLOTS,
    MAX_CHARACTER_IMAGES,
    MAX_CONTENT_SECTIONS,
    MAX_CUSTOM_INLINE_IMAGES,
    MAX_FACTION_ABILITIES,
    MAX_FLAVOR_TEXT,
    MAX_LEGEND_ROWS,
    MAX_PHASE_STEPS_PER_BOX,
    MAX_PHASE_STEPS_PER_PHASE,
    MAX_PHASE_STEP_TEXT,
    MAX_PIECES_PER_TYPE,
    MAX_PIECE_QUANTITY,
    MAX_SCALE_ROWS,
    MAX_SETUP_STEP_TEXT,
    MAX_STEP_ACTIONS,
    MAX_STEP_CHILDREN,
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
    faction_has_background,
    faction_secondary_in_use,
)


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


def _maybe_update_parent_paper_background(request, content_box):
    """Express child rows submit `paper_background` for their parent ContentBox."""
    if content_box is None or 'paper_background' not in request.POST:
        return
    new_val = request.POST.get('paper_background') == 'true'
    if content_box.paper_background != new_val:
        content_box.paper_background = new_val
        content_box.save(update_fields=['paper_background'])


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


def forge_element_guide(request):
    if request.user.is_authenticated:
        send_discord_message_task.delay(f'[{request.user}]({build_absolute_uri(request, request.user.profile.get_absolute_url())}) ({request.user.profile.group}) viewed The Forge Style Guide')

    return render(request, 'the_forge/forge_element_guide.html')


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
            .order_by('-last_updated')
        )
    else:
        factions = []
    return render(request, 'the_forge/forgedfaction_list.html', {'factions': factions})


@admin_onboard_required
def forgedfaction_admin_list(request):
    factions = (
        ForgedFaction.objects
        .select_related('designer', 'faction_sheet', 'faction_back', 'setup_card')
        .order_by('-last_updated')
    )
    return render(request, 'the_forge/forgedfaction_admin_list.html', {'factions': factions})


@forge_onboard_required
def forgedfaction_name_check(request):
    from .services.name_conflicts import find_faction_name_conflicts
    name = request.GET.get('faction_name', '')
    conflicts = find_faction_name_conflicts(name, request.user.profile)
    return render(request, 'the_forge/partials/faction_name_check.html',
                  {'conflicts': conflicts})


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
    components = (
        list(back.pieces.filter(type__in=('B', 'T')).order_by('type', 'pk'))
        if back else []
    )
    can_edit = user_can_edit_forge(request, faction)
    publish_context = {}
    if can_edit:
        submit_missing = submit_prerequisites_missing(faction)
        submit_requirements = [
            {'label': 'Faction Board', 'done': sheet is not None},
            {'label': 'Faction Board Back', 'done': back is not None},
            {'label': 'Adset Card', 'done': setup_card is not None},
            {'label': 'Faction Icon', 'done': bool(faction.faction_icon)},
        ]
        link_candidate_exists = _link_candidate_exists(request.user.profile, faction)
        target, target_mode, target_label = _resolved_published(faction)
        if target_mode == 'faction':
            target_modified = target.date_modified
            target_url = target.get_absolute_url()
        elif target_mode == 'translation':
            target_modified = target.date_modified
            target_url = target.post.get_absolute_url()
        else:
            target_modified = None
            target_url = None
        sync_available = bool(
            target is not None
            and target_modified is not None
            and faction.last_updated > target_modified
        )
        publish_context = {
            'submit_ready': not submit_missing,
            'submit_missing': submit_missing,
            'submit_requirements': submit_requirements,
            'link_candidate_exists': link_candidate_exists,
            'publish_target': target,
            'publish_target_mode': target_mode,
            'publish_target_label': target_label,
            'publish_target_url': target_url,
            'sync_available': sync_available,
        }
    return render(request, 'the_forge/forgedfaction_detail.html', {
        'faction': faction,
        'can_edit': can_edit,
        'sheet': sheet,
        'back': back,
        'setup_card': setup_card,
        'components': components,
        **publish_context,
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


@forge_onboard_required
def forgedfaction_markers_edit(request, pk):
    faction = get_object_or_404(ForgedFaction, pk=pk)
    if (resp := _forbid_if_not_editor(request, faction)):
        return resp
    if request.method == 'POST':
        form = FactionMarkersForm(request.POST, request.FILES, instance=faction)
        if form.is_valid():
            faction = form.save()
            _save_marker_uploads(request, faction)
            return redirect('forge-faction-detail', pk=faction.pk)
    else:
        form = FactionMarkersForm(instance=faction)
    return render(request, 'the_forge/forgedfaction_markers_form.html', {
        'form': form,
        'faction': faction,
    })


def _save_marker_uploads(request, faction):
    """Persist the JS-generated VP and Relationship marker PNGs sent up as
    base64 data URLs alongside the markers form. The JS only attaches them
    when the icon or color actually changed, so a missing field is normal
    on no-op saves."""
    import base64
    from django.core.files.base import ContentFile
    pairs = (
        ('vp_marker_data', 'vp_marker', 'vp_marker'),
        ('relationship_marker_data', 'relationship_marker', 'relationship_marker'),
    )
    update_fields = []
    for post_key, field_name, filename_stem in pairs:
        data_url = request.POST.get(post_key, '').strip()
        if not data_url or ',' not in data_url:
            continue
        try:
            raw = base64.b64decode(data_url.split(',', 1)[1])
        except Exception:
            continue
        field = getattr(faction, field_name)
        if field:
            field.delete(save=False)
        field.save(f'{filename_stem}.png', ContentFile(raw), save=False)
        update_fields.append(field_name)
    if update_fields:
        faction.markers_version = (faction.markers_version or 0) + 1
        update_fields.append('markers_version')
        faction.save(update_fields=update_fields)


@login_required
@require_http_methods(["POST"])
def forgedfaction_markers_delete(request, pk):
    faction = get_object_or_404(ForgedFaction, pk=pk)
    if (resp := _forbid_if_not_editor(request, faction)):
        return resp
    update_fields = []
    if faction.vp_marker:
        faction.vp_marker.delete(save=False)
        update_fields.append('vp_marker')
    if faction.relationship_marker:
        faction.relationship_marker.delete(save=False)
        update_fields.append('relationship_marker')
    if update_fields:
        faction.markers_version = (faction.markers_version or 0) + 1
        update_fields.append('markers_version')
        faction.save(update_fields=update_fields)
    return redirect('forge-faction-detail', pk=faction.pk)

@login_required
def forgedfaction_vp_marker_png(request, pk):
    faction = get_object_or_404(ForgedFaction, pk=pk)
    if not user_can_view_forge(request, faction):
        return HttpResponseForbidden()
    if not faction.vp_marker:
        return HttpResponseBadRequest("No VP marker generated yet.")
    return _png_file_response(faction.vp_marker, f'{faction.faction_name} - VP Marker.png')

@login_required
def forgedfaction_relationship_marker_png(request, pk):
    faction = get_object_or_404(ForgedFaction, pk=pk)
    if not user_can_view_forge(request, faction):
        return HttpResponseForbidden()
    if not faction.relationship_marker:
        return HttpResponseBadRequest("No relationship marker generated yet.")
    return _png_file_response(faction.relationship_marker, f'{faction.faction_name} - Relationship Marker.png')


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
        'max_flavor_text': MAX_FLAVOR_TEXT,
        'max_ability_body': MAX_ABILITY_BODY,
        'max_ability_title': MAX_ABILITY_TITLE,
        'max_faction_abilities': MAX_FACTION_ABILITIES,
        'max_content_sections': MAX_CONTENT_SECTIONS,
        'max_phase_steps_per_phase': MAX_PHASE_STEPS_PER_PHASE,
        'max_phase_steps_per_box': MAX_PHASE_STEPS_PER_BOX,
        'max_phase_step_text': MAX_PHASE_STEP_TEXT,
        'max_step_actions': MAX_STEP_ACTIONS,
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
        'box_color_choices': BorderedBox.ElementColor.choices,
        'pile_color_choices': CardPile.ElementColor.choices,
        'secondary_visible': faction_secondary_in_use(sheet.faction),
        'pile_hide_faction': not faction_has_background(sheet.faction),
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


@login_required
@require_http_methods(["POST"])
def factionback_delete(request, pk):
    back = get_object_or_404(FactionBack, pk=pk)
    if (resp := _forbid_if_not_editor(request, back.faction)):
        return resp
    faction_pk = back.faction_id
    back.delete()
    return redirect('forge-faction-detail', pk=faction_pk)


@login_required
@require_http_methods(["POST"])
def setup_card_delete(request, pk):
    card = get_object_or_404(SetupCard, pk=pk)
    if (resp := _forbid_if_not_editor(request, card.faction)):
        return resp
    faction_pk = card.faction_id
    card.delete()
    return redirect('forge-faction-detail', pk=faction_pk)


@player_required
@require_http_methods(["POST"])
def sheet_flavor_edit(request, pk):
    sheet = get_object_or_404(FactionSheet, pk=pk)
    if (resp := _forbid_if_not_editor(request, sheet.faction)):
        return resp
    flavor = request.POST.get('flavor_text', '')
    if len(flavor) > MAX_FLAVOR_TEXT:
        return HttpResponseBadRequest(f"Flavor text limited to {MAX_FLAVOR_TEXT} characters.")
    sheet.flavor_text = flavor
    sheet.save(update_fields=['flavor_text'])
    return render(request, 'the_forge/partials/sheet_flavor_form.html', {
        'sheet': sheet,
        'max_flavor_text': MAX_FLAVOR_TEXT,
    })


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
        piece_clear_back_flags = request.POST.getlist('piece_clear_back')
        step_ids = request.POST.getlist('step_id')
        step_texts = request.POST.getlist('step_text')

        rows = list(zip(
            piece_ids, piece_types, piece_names, piece_quantities,
            piece_clear_flags, piece_clear_back_flags,
        ))
        type_counts = {t: 0 for t in valid_piece_types}
        for sid, ptype, _name, _qty, _cf, _cb in rows:
            if ptype in valid_piece_types:
                type_counts[ptype] += 1
        for ptype, count in type_counts.items():
            if count > MAX_PIECES_PER_TYPE:
                form.add_error(None, f"Maximum of {MAX_PIECES_PER_TYPE} {ptype} pieces.")
                break

        step_pairs = [
            (sid, txt) for sid, txt in zip(step_ids, step_texts)
            if (txt or '').strip()
        ]
        if len(step_pairs) > MAX_BACK_SETUP_STEPS:
            form.add_error(None, f"Maximum of {MAX_BACK_SETUP_STEPS} setup steps allowed.")
        if any(len(txt) > MAX_SETUP_STEP_TEXT for _sid, txt in step_pairs):
            form.add_error(None, f"Setup step text limited to {MAX_SETUP_STEP_TEXT} characters.")

        if form.is_valid():
            with transaction.atomic():
                form.save(back)

                existing_pieces = {p.pk: p for p in back.pieces.all()}
                kept_piece_ids = set()
                for index, (sid, ptype, name, qty, clear_front, clear_back) in enumerate(rows):
                    front_upload = request.FILES.get(f'piece_icon_{index}')
                    back_upload = request.FILES.get(f'piece_back_{index}')
                    name = (name or '').strip()
                    if ptype not in valid_piece_types:
                        continue
                    try:
                        quantity = max(1, min(MAX_PIECE_QUANTITY, int(qty or 1)))
                    except (TypeError, ValueError):
                        quantity = 1
                    has_new_front = bool(front_upload)
                    has_new_back = bool(back_upload)
                    should_clear_front = (clear_front == '1') and not has_new_front
                    should_clear_back = (clear_back == '1') and not has_new_back
                    if sid and sid.isdigit() and int(sid) in existing_pieces:
                        piece = existing_pieces[int(sid)]
                        kept_piece_ids.add(piece.pk)
                        piece.name = name
                        piece.quantity = quantity
                        piece.type = ptype
                        if has_new_front:
                            piece.small_icon = front_upload
                        elif should_clear_front:
                            piece.small_icon = None
                        if has_new_back:
                            piece.back_image = back_upload
                        elif should_clear_back:
                            piece.back_image = None
                        piece.save()
                    else:
                        new_piece = Piece(
                            parent=back,
                            name=name,
                            quantity=quantity,
                            type=ptype,
                        )
                        new_piece.save()
                        if has_new_front or has_new_back:
                            if has_new_front:
                                new_piece.small_icon = front_upload
                            if has_new_back:
                                new_piece.back_image = back_upload
                            new_piece.save()
                stale_piece_ids = set(existing_pieces) - kept_piece_ids
                if stale_piece_ids:
                    Piece.objects.filter(pk__in=stale_piece_ids).delete()

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
            return redirect('forge-faction-detail', pk=back.faction.pk)
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
        'max_pieces_per_type': MAX_PIECES_PER_TYPE,
        'max_setup_steps': MAX_BACK_SETUP_STEPS,
        'max_setup_step_text': MAX_SETUP_STEP_TEXT,
    })


@forge_onboard_required
def forgedfaction_cardboard_edit(request, pk):
    """Standalone editor for the buildings/tokens roster and the
    `print_component_backs` toggle. Mirrors the relevant slice of the
    FactionBack editor (the same Buildings & Tokens piece sections) without
    the back-side layout fields."""
    faction = get_object_or_404(ForgedFaction, pk=pk)
    if (resp := _forbid_if_not_editor(request, faction)):
        return resp
    back, _ = FactionBack.objects.get_or_create(faction=faction)
    cardboard_types = ('B', 'T')
    if request.method == 'POST':
        piece_ids = request.POST.getlist('piece_id')
        piece_types = request.POST.getlist('piece_type')
        piece_names = request.POST.getlist('piece_name')
        piece_quantities = request.POST.getlist('piece_quantity')
        piece_clear_flags = request.POST.getlist('piece_clear_icon')
        piece_clear_back_flags = request.POST.getlist('piece_clear_back')
        rows = list(zip(
            piece_ids, piece_types, piece_names, piece_quantities,
            piece_clear_flags, piece_clear_back_flags,
        ))
        type_counts = {t: 0 for t in cardboard_types}
        for _sid, ptype, _name, _qty, _cf, _cb in rows:
            if ptype in cardboard_types:
                type_counts[ptype] += 1
        for ptype, count in type_counts.items():
            if count > MAX_PIECES_PER_TYPE:
                return HttpResponseBadRequest(f"Maximum of {MAX_PIECES_PER_TYPE} {ptype} pieces.")
        with transaction.atomic():
            faction.print_component_backs = bool(request.POST.get('print_component_backs'))
            faction.save(update_fields=['print_component_backs'])

            existing_pieces = {p.pk: p for p in back.pieces.filter(type__in=cardboard_types)}
            kept_piece_ids = set()
            for index, (sid, ptype, name, qty, clear_front, clear_back) in enumerate(rows):
                if ptype not in cardboard_types:
                    continue
                front_upload = request.FILES.get(f'piece_icon_{index}')
                back_upload = request.FILES.get(f'piece_back_{index}')
                name = (name or '').strip()
                try:
                    quantity = max(1, min(MAX_PIECE_QUANTITY, int(qty or 1)))
                except (TypeError, ValueError):
                    quantity = 1
                has_new_front = bool(front_upload)
                has_new_back = bool(back_upload)
                should_clear_front = (clear_front == '1') and not has_new_front
                should_clear_back = (clear_back == '1') and not has_new_back
                if sid and sid.isdigit() and int(sid) in existing_pieces:
                    piece = existing_pieces[int(sid)]
                    kept_piece_ids.add(piece.pk)
                    piece.name = name
                    piece.quantity = quantity
                    piece.type = ptype
                    if has_new_front:
                        piece.small_icon = front_upload
                    elif should_clear_front:
                        piece.small_icon = None
                    if has_new_back:
                        piece.back_image = back_upload
                    elif should_clear_back:
                        piece.back_image = None
                    piece.save()
                else:
                    new_piece = Piece(parent=back, name=name, quantity=quantity, type=ptype)
                    new_piece.save()
                    if has_new_front or has_new_back:
                        if has_new_front:
                            new_piece.small_icon = front_upload
                        if has_new_back:
                            new_piece.back_image = back_upload
                        new_piece.save()
            stale_piece_ids = set(existing_pieces) - kept_piece_ids
            if stale_piece_ids:
                Piece.objects.filter(pk__in=stale_piece_ids).delete()
        return redirect('forge-faction-detail', pk=faction.pk)

    pieces_qs = list(back.pieces.filter(type__in=cardboard_types).order_by('id'))
    piece_sections = [
        ('B', 'Buildings', 'Building', [p for p in pieces_qs if p.type == 'B']),
        ('T', 'Tokens', 'Token', [p for p in pieces_qs if p.type == 'T']),
    ]
    return render(request, 'the_forge/cardboard_editor.html', {
        'faction': faction,
        'back': back,
        'piece_sections': piece_sections,
        'max_pieces_per_type': MAX_PIECES_PER_TYPE,
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
        if len(pairs) > MAX_CARD_SETUP_STEPS:
            form.add_error(None, f"Maximum of {MAX_CARD_SETUP_STEPS} setup steps allowed.")
        if any(len(txt) > MAX_SETUP_STEP_TEXT for _sid, txt in pairs):
            form.add_error(None, f"Setup step text limited to {MAX_SETUP_STEP_TEXT} characters.")
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
            return redirect('forge-faction-detail', pk=card.faction.pk)
    else:
        form = SetupCardForm(card=card)
    return render(request, 'the_forge/setup_card_editor.html', {
        'card': card,
        'faction': card.faction,
        'form': form,
        'setup_steps': _annotate_steps(card.setup_steps.order_by('number')),
        'inline_keywords': _inline_keywords(),
        'inline_images_map': _inline_images_map(),
        'max_setup_steps': MAX_CARD_SETUP_STEPS,
        'max_setup_step_text': MAX_SETUP_STEP_TEXT,
    })


# ---------- FactionAbility (child of FactionSheet) ----------

@player_required
@require_http_methods(["POST"])
def ability_add(request, sheet_pk):
    sheet = get_object_or_404(FactionSheet, pk=sheet_pk)
    if (resp := _forbid_if_not_editor(request, sheet.faction)):
        return resp
    if sheet.abilities.count() >= MAX_FACTION_ABILITIES:
        return HttpResponseBadRequest(f"Maximum of {MAX_FACTION_ABILITIES} abilities reached.")
    form = FactionAbilityForm(request.POST)
    if not form.is_valid():
        return HttpResponseBadRequest(str(form.errors))
    ability = form.save(commit=False)
    ability.sheet = sheet
    if not ability.order:
        ability.order = sheet.abilities.count() + 1
    ability.save()
    return render(request, 'the_forge/partials/ability_row.html', {
        'ability': ability,
        'inline_keywords': _inline_keywords(ability.sheet),
        'max_ability_body': MAX_ABILITY_BODY,
        'max_ability_title': MAX_ABILITY_TITLE,
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
        'ability': ability,
        'inline_keywords': _inline_keywords(ability.sheet),
        'max_ability_body': MAX_ABILITY_BODY,
        'max_ability_title': MAX_ABILITY_TITLE,
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
    if sheet.content_boxes.count() >= MAX_CONTENT_SECTIONS:
        return HttpResponseBadRequest(f"Maximum of {MAX_CONTENT_SECTIONS} content sections reached.")

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
            'max_phase_steps_per_box': MAX_PHASE_STEPS_PER_BOX,
            'max_phase_step_text': MAX_PHASE_STEP_TEXT,
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
        'max_phase_steps_per_box': MAX_PHASE_STEPS_PER_BOX,
        'max_phase_step_text': MAX_PHASE_STEP_TEXT,
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
        'max_phase_steps_per_box': MAX_PHASE_STEPS_PER_BOX,
        'max_phase_step_text': MAX_PHASE_STEP_TEXT,
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
        existing = sheet.phase_steps.filter(content_box=step.content_box).count()
        if existing >= MAX_PHASE_STEPS_PER_BOX:
            return HttpResponseBadRequest(f"Maximum of {MAX_PHASE_STEPS_PER_BOX} steps per content section.")
        step.number = existing + 1
    else:
        existing = sheet.phase_steps.filter(phase=step.phase, content_box__isnull=True).count()
        if existing >= MAX_PHASE_STEPS_PER_PHASE:
            return HttpResponseBadRequest(f"Maximum of {MAX_PHASE_STEPS_PER_PHASE} steps in {step.phase}.")
        step.number = existing + 1
    step.save()
    ensure_step_parent_fits(step)
    return render(request, 'the_forge/partials/phase_step_row.html', {
        'step': _annotate_step(step),
        'inline_keywords': _inline_keywords(step.sheet),
        'max_phase_step_text': MAX_PHASE_STEP_TEXT,
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
        'step': _annotate_step(step),
        'inline_keywords': _inline_keywords(step.sheet),
        'max_phase_step_text': MAX_PHASE_STEP_TEXT,
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
    if step.actions.count() >= MAX_STEP_ACTIONS:
        return HttpResponseBadRequest(f"Maximum of {MAX_STEP_ACTIONS} actions per step.")
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
    cost = form.cleaned_data.get('cost')
    if cost not in {v for v, _ in action.step.cost_choices_with(action.cost)}:
        return HttpResponseBadRequest(f"Cost '{cost}' is not allowed for this step's action_type.")
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
    return render(request, 'the_forge/partials/phase_step_action_header.html', {
        'step': step,
        'max_step_actions': MAX_STEP_ACTIONS,
    })


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
    return render(request, 'the_forge/partials/phase_step_action_header.html', {
        'step': step,
        'max_step_actions': MAX_STEP_ACTIONS,
    })


# ---------- BorderedBox (child of PhaseStep) ----------

@player_required
@require_http_methods(["POST"])
def borderedbox_add(request, step_pk):
    step = get_object_or_404(PhaseStep, pk=step_pk)
    if (resp := _forbid_if_not_editor(request, step.sheet.faction)):
        return resp
    if _step_at_child_cap(step):
        return HttpResponseBadRequest(f"Maximum of {MAX_STEP_CHILDREN} elements per step.")
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
        'secondary_visible': faction_secondary_in_use(step.sheet.faction),
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
    _maybe_update_parent_paper_background(request, box.step.content_box)
    ensure_step_parent_fits(box.step)
    return render(request, 'the_forge/partials/bordered_box_row.html', {
        'box': box, 'inline_keywords': _inline_keywords(box.step.sheet),
        'secondary_visible': faction_secondary_in_use(box.step.sheet.faction),
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
    if _step_at_child_cap(step):
        return HttpResponseBadRequest(f"Maximum of {MAX_STEP_CHILDREN} elements per step.")
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
    _maybe_update_parent_paper_background(request, track.step.content_box)
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


def _step_at_child_cap(step):
    return (step.boxes.count() + step.tracks.count()
            + step.legends.count() + step.scales.count()) >= MAX_STEP_CHILDREN


@player_required
@require_http_methods(["POST"])
def legend_add(request, step_pk):
    step = get_object_or_404(PhaseStep, pk=step_pk)
    if (resp := _forbid_if_not_editor(request, step.sheet.faction)):
        return resp
    if _step_at_child_cap(step):
        return HttpResponseBadRequest(f"Maximum of {MAX_STEP_CHILDREN} elements per step.")
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
        'inline_image_labels': _inline_image_labels(step.sheet),
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
    _maybe_update_parent_paper_background(request, legend.step.content_box)
    ensure_step_parent_fits(legend.step)
    return render(request, 'the_forge/partials/legend_row.html', {
        'legend': legend, 'inline_keywords': _inline_keywords(legend.step.sheet),
        'inline_images': _inline_images_map(legend.step.sheet),
        'inline_image_labels': _inline_image_labels(legend.step.sheet),
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
    if legend.rows.count() >= MAX_LEGEND_ROWS:
        return HttpResponseBadRequest(f"Maximum of {MAX_LEGEND_ROWS} rows per legend.")
    form = LegendRowForm(request.POST, request.FILES)
    if not form.is_valid():
        return HttpResponseBadRequest(str(form.errors))
    row = form.save(commit=False)
    row.legend = legend
    row.order = legend.rows.count() + 1
    if request.POST.get('display_kind') == 'image':
        row.icon = ''
    row.save()
    ensure_step_parent_fits(legend.step)
    return render(request, 'the_forge/partials/legend_row_entry.html', {
        'row': row, 'inline_keywords': _inline_keywords(legend.step.sheet),
        'inline_images': _inline_images_map(legend.step.sheet),
        'inline_image_labels': _inline_image_labels(legend.step.sheet),
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
    obj = form.save(commit=False)
    kind = request.POST.get('display_kind', 'image')
    if kind == 'icon' and obj.image:
        delete_old_image(obj.image)
        obj.image = None
    elif kind == 'image':
        obj.icon = ''
    obj.save()
    ensure_step_parent_fits(row.legend.step)
    return render(request, 'the_forge/partials/legend_row_entry.html', {
        'row': obj, 'inline_keywords': _inline_keywords(row.legend.step.sheet),
        'inline_images': _inline_images_map(row.legend.step.sheet),
        'inline_image_labels': _inline_image_labels(row.legend.step.sheet),
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
    if _step_at_child_cap(step):
        return HttpResponseBadRequest(f"Maximum of {MAX_STEP_CHILDREN} elements per step.")
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
    if len(row_ids) > MAX_SCALE_ROWS:
        return HttpResponseBadRequest(f"Maximum of {MAX_SCALE_ROWS} rows per scale.")
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
        'secondary_visible': faction_secondary_in_use(sheet.faction),
        'pile_hide_faction': not faction_has_background(sheet.faction),
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
        'secondary_visible': faction_secondary_in_use(pile.sheet.faction),
        'pile_hide_faction': not faction_has_background(pile.sheet.faction),
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


def _png_file_response(image_field, filename):
    response = FileResponse(image_field.open('rb'), content_type='image/png')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response


def _attach_preview_versions(response, **objects):
    """Stamp the response with current preview_version values so the page can
    refresh its <img> tags after an async download. Pass keyword args like
    sheet=..., back=..., card=... — None values are skipped.
    """
    import json
    versions = {}
    for kind, obj in objects.items():
        if obj is None:
            continue
        versions[kind] = {
            'pk': obj.pk,
            'version': obj.preview_version or 0,
        }
    if versions:
        response['X-Forge-Preview-Versions'] = json.dumps(versions)
        # Allow the custom header to be read by fetch() in case CORS ever applies.
        response['Access-Control-Expose-Headers'] = 'X-Forge-Preview-Versions'
    return response


def _maybe_save_image_preview(instance, pdf_bytes, fingerprint, field_prefix):
    if instance.preview_fingerprint == fingerprint and instance.image_preview:
        return
    from django.core.files.base import ContentFile
    from django.utils import timezone
    from .pdf_engine import pdf_bytes_to_webp_bytes
    # SetupCard previews are also embedded in the printable Components sheet,
    # so render them at higher DPI for crisp print output.
    dpi = 250 if field_prefix == 'card' else 150
    try:
        webp = pdf_bytes_to_webp_bytes(pdf_bytes, dpi=dpi)
    except Exception:
        return
    if instance.image_preview:
        instance.image_preview.delete(save=False)
    filename = f'{field_prefix}_{instance.pk}.webp'
    instance.image_preview.save(filename, ContentFile(webp), save=False)
    instance.preview_fingerprint = fingerprint
    instance.last_generated = timezone.now()
    instance.preview_version = (instance.preview_version or 0) + 1
    instance.save(update_fields=['image_preview', 'preview_fingerprint', 'last_generated', 'preview_version'])


def _ensure_sheet_preview(sheet):
    """Refresh sheet.image_preview and sheet.snap_points if the fingerprint is stale.

    Always runs the engine when the fingerprint is stale so that the latest
    snap-point coordinates can be captured directly from the layout. Cached
    PDF bytes wouldn't include snap points, so the cache is bypassed on this
    path; the cache still serves the regular pdf-download view.
    """
    from io import BytesIO
    from .pdf_engine import SheetLayoutEngine
    from .pdf_cache import fingerprint_sheet
    fp = fingerprint_sheet(sheet)
    if sheet.preview_fingerprint == fp and sheet.image_preview:
        return
    engine = SheetLayoutEngine(sheet)
    buffer = BytesIO()
    engine.build(buffer)
    data = buffer.getvalue()
    _maybe_save_image_preview(sheet, data, fp, 'sheet')
    sheet.snap_points = list(engine.collected_snap_points)
    sheet.decree_slide_pts = float(engine.decree_slide or 0.0)
    sheet.ability_bar_extra_h_pts = float(getattr(engine, 'ability_bar_extra_h', 0.0) or 0.0)
    sheet.save(update_fields=['snap_points', 'decree_slide_pts', 'ability_bar_extra_h_pts'])


def _ensure_decree_preview(sheet):
    from .pdf_cache import fingerprint_decree
    from django.core.files.base import ContentFile
    fp = fingerprint_decree(sheet)
    if sheet.decree_fingerprint == fp and (sheet.decree_preview or not sheet.include_decree):
        return
    if not sheet.include_decree:
        if sheet.decree_preview:
            sheet.decree_preview.delete(save=False)
        sheet.decree_fingerprint = fp
        sheet.save(update_fields=['decree_preview', 'decree_fingerprint'])
        return
    from .decree_preview import render_decree_preview
    webp = render_decree_preview(sheet)
    if not webp:
        if sheet.decree_preview:
            sheet.decree_preview.delete(save=False)
        sheet.decree_fingerprint = fp
        sheet.save(update_fields=['decree_preview', 'decree_fingerprint'])
        return
    if sheet.decree_preview:
        sheet.decree_preview.delete(save=False)
    sheet.decree_preview.save(f'decree_{sheet.pk}.webp', ContentFile(webp), save=False)
    sheet.decree_fingerprint = fp
    sheet.save(update_fields=['decree_preview', 'decree_fingerprint'])


def _ensure_back_preview(back):
    from io import BytesIO
    from .pdf_engine import FactionBackLayoutEngine
    from .pdf_cache import cache_key, fingerprint_back, get_or_build
    fp = fingerprint_back(back)
    if back.preview_fingerprint == fp and back.image_preview:
        return
    def build():
        buffer = BytesIO()
        FactionBackLayoutEngine(back).build(buffer)
        return buffer.getvalue()
    data = get_or_build(cache_key('back', back.pk, fp), build)
    _maybe_save_image_preview(back, data, fp, 'back')


def _ensure_setup_card_preview(card):
    from io import BytesIO
    from .pdf_engine import SetupCardLayoutEngine
    from .pdf_cache import cache_key, fingerprint_setup_card, get_or_build
    fp = fingerprint_setup_card(card)
    if card.preview_fingerprint == fp and card.image_preview:
        return
    def build():
        buffer = BytesIO()
        SetupCardLayoutEngine(card).build(buffer)
        return buffer.getvalue()
    data = get_or_build(cache_key('setup_card', card.pk, fp), build)
    _maybe_save_image_preview(card, data, fp, 'card')


def _refresh_all_element_previews(forged):
    """Render and save any stale element previews for the forge children that
    exist. No-op per child whose fingerprint already matches. Failures are
    swallowed so a broken layout doesn't block the calling view."""
    try:
        sheet = forged.faction_sheet
    except FactionSheet.DoesNotExist:
        sheet = None
    try:
        back = forged.faction_back
    except FactionBack.DoesNotExist:
        back = None
    try:
        setup_card = forged.setup_card
    except SetupCard.DoesNotExist:
        setup_card = None
    for related, ensure_fn in (
        (sheet, _ensure_sheet_preview),
        (back, _ensure_back_preview),
        (setup_card, _ensure_setup_card_preview),
    ):
        if related is None:
            continue
        try:
            ensure_fn(related)
        except Exception:
            pass


@login_required
def forgedfaction_pdf(request, pk):
    faction = get_object_or_404(ForgedFaction, pk=pk)
    if (resp := _forbid_if_not_editor(request, faction)):
        return resp
    from io import BytesIO
    from pypdf import PdfWriter, PdfReader
    from .pdf_engine import (
        SheetLayoutEngine, FactionBackLayoutEngine, SetupCardLayoutEngine,
        ComponentsSheetLayoutEngine,
    )
    from .pdf_cache import (
        cache_key, fingerprint_sheet, fingerprint_back, fingerprint_setup_card,
        fingerprint_components_sheet, get_or_build,
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
    has_components = bool(
        card or faction.vp_marker or faction.relationship_marker
        or (back and back.pieces.filter(type__in=('B', 'T')).exists())
    )
    if has_components:
        card_preview_path = None
        if card:
            card_fp = fingerprint_setup_card(card)
            # Refresh the SetupCard preview if its fingerprint is stale, so the
            # detail-page thumbnail updates as a side effect of Combined PDF.
            if card.preview_fingerprint != card_fp or not card.image_preview:
                card_buf = BytesIO()
                SetupCardLayoutEngine(card).build(card_buf)
                _maybe_save_image_preview(card, card_buf.getvalue(), card_fp, 'card')
            if card.image_preview:
                card_preview_path = card.image_preview.path

        def build_components():
            buf = BytesIO()
            ComponentsSheetLayoutEngine(faction, card_preview_path=card_preview_path).build(buf)
            return buf.getvalue()
        components_fp = fingerprint_components_sheet(faction)
        components_pdf = get_or_build(
            cache_key('components', faction.pk, components_fp), build_components,
        )
        parts.append(components_pdf)

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
    response = _pdf_file_response(out.getvalue(), f'{faction.faction_name}.pdf')
    return _attach_preview_versions(response, sheet=sheet, back=back, card=card)


@login_required
def forgedfaction_components_pdf(request, pk):
    """Render only the components page(s) — setup card, markers, and the
    building/token grid — without the front or back faction sheets."""
    faction = get_object_or_404(ForgedFaction, pk=pk)
    if (resp := _forbid_if_not_editor(request, faction)):
        return resp
    from io import BytesIO
    from .pdf_engine import SetupCardLayoutEngine, ComponentsSheetLayoutEngine
    from .pdf_cache import (
        cache_key, fingerprint_setup_card, fingerprint_components_sheet, get_or_build,
    )

    card = getattr(faction, 'setup_card', None)
    back = getattr(faction, 'faction_back', None)
    has_components = bool(
        card or faction.vp_marker or faction.relationship_marker
        or (back and back.pieces.filter(type__in=('B', 'T')).exists())
    )
    if not has_components:
        return HttpResponse(
            "No components yet — add a setup card, faction markers, or building/token pieces first.",
            status=404, content_type='text/plain',
        )

    card_preview_path = None
    if card:
        card_fp = fingerprint_setup_card(card)
        if card.preview_fingerprint != card_fp or not card.image_preview:
            card_buf = BytesIO()
            SetupCardLayoutEngine(card).build(card_buf)
            _maybe_save_image_preview(card, card_buf.getvalue(), card_fp, 'card')
        if card.image_preview:
            card_preview_path = card.image_preview.path

    def build_components():
        buf = BytesIO()
        ComponentsSheetLayoutEngine(faction, card_preview_path=card_preview_path).build(buf)
        return buf.getvalue()
    fp = fingerprint_components_sheet(faction)
    data = get_or_build(cache_key('components', faction.pk, fp), build_components)
    return _pdf_file_response(data, f'{faction.faction_name} - Components.pdf')


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
    response = _pdf_file_response(data, f'{sheet.faction.faction_name} - Front.pdf')
    return _attach_preview_versions(response, sheet=sheet)


@login_required
def factionsheet_pdf_layered(request, pk):
    sheet = get_object_or_404(FactionSheet, pk=pk)
    if (resp := _forbid_if_not_editor(request, sheet.faction)):
        return resp
    from io import BytesIO
    from .pdf_engine import SheetLayoutEngine
    from .pdf_cache import cache_key, fingerprint_sheet, get_or_build
    fp = fingerprint_sheet(sheet)
    key = cache_key('sheet-layered', sheet.pk, fp)
    def build():
        buffer = BytesIO()
        SheetLayoutEngine(sheet).build(buffer, layered=True)
        return buffer.getvalue()
    data = get_or_build(key, build)
    return _pdf_file_response(data, f'{sheet.faction.faction_name} - Front (Layers).pdf')


@login_required
def factionsheet_webp(request, pk):
    sheet = get_object_or_404(FactionSheet, pk=pk)
    if (resp := _forbid_if_not_editor(request, sheet.faction)):
        return resp
    _ensure_sheet_preview(sheet)
    sheet.refresh_from_db()
    if not sheet.image_preview:
        return HttpResponse("Preview unavailable.", status=404, content_type='text/plain')
    response = _webp_file_response(sheet.image_preview, f'{sheet.faction.faction_name} - Front.webp')
    return _attach_preview_versions(response, sheet=sheet)


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
    response = _pdf_file_response(data, f'{back.faction.faction_name} - Back.pdf')
    return _attach_preview_versions(response, back=back)


@login_required
def factionback_pdf_layered(request, pk):
    back = get_object_or_404(FactionBack, pk=pk)
    if (resp := _forbid_if_not_editor(request, back.faction)):
        return resp
    from io import BytesIO
    from .pdf_engine import FactionBackLayoutEngine
    from .pdf_cache import cache_key, fingerprint_back, get_or_build
    fp = fingerprint_back(back)
    key = cache_key('back-layered', back.pk, fp)
    def build():
        buffer = BytesIO()
        FactionBackLayoutEngine(back).build(buffer, layered=True)
        return buffer.getvalue()
    data = get_or_build(key, build)
    return _pdf_file_response(data, f'{back.faction.faction_name} - Back (Layers).pdf')


@login_required
def factionback_webp(request, pk):
    back = get_object_or_404(FactionBack, pk=pk)
    if (resp := _forbid_if_not_editor(request, back.faction)):
        return resp
    _ensure_back_preview(back)
    back.refresh_from_db()
    if not back.image_preview:
        return HttpResponse("Preview unavailable.", status=404, content_type='text/plain')
    response = _webp_file_response(back.image_preview, f'{back.faction.faction_name} - Back.webp')
    return _attach_preview_versions(response, back=back)


@login_required
def forgedfaction_tts(request, pk):
    faction = get_object_or_404(ForgedFaction, pk=pk)
    if (resp := _forbid_if_not_editor(request, faction)):
        return resp

    sheet = getattr(faction, 'faction_sheet', None)
    back = getattr(faction, 'faction_back', None)
    card = getattr(faction, 'setup_card', None)
    if not sheet and not back and not card:
        return HttpResponse(
            "No content yet — create a Front, Back, or Setup Card first.",
            status=404, content_type='text/plain',
        )

    if sheet:
        _ensure_sheet_preview(sheet)
        _ensure_decree_preview(sheet)
        sheet.refresh_from_db()
    if back:
        _ensure_back_preview(back)
        back.refresh_from_db()
    if card:
        from io import BytesIO
        from .pdf_engine import SetupCardLayoutEngine
        from .pdf_cache import cache_key, fingerprint_setup_card, get_or_build
        fp = fingerprint_setup_card(card)
        if card.preview_fingerprint != fp or not card.image_preview:
            def _build_card():
                buffer = BytesIO()
                SetupCardLayoutEngine(card).build(buffer)
                return buffer.getvalue()
            data = get_or_build(cache_key('setup_card', card.pk, fp), _build_card)
            _maybe_save_image_preview(card, data, fp, 'card')
            card.refresh_from_db()

    sheet_ready = bool(sheet and sheet.image_preview)
    back_ready = bool(back and back.image_preview)
    card_ready = bool(card and card.image_preview)
    if not (sheet_ready or back_ready or card_ready):
        return HttpResponse(
            "Preview unavailable.",
            status=404, content_type='text/plain',
        )

    from .services.tts import (
        TTSForgedFactionBoard, TTSForgedFactionDecree, load_tts_object,
        place_pieces_for_back, place_markers_for_faction,
    )
    from the_keep.services.tts import wrap_tts_save, TTSSingleCardDeck

    boards = []
    if sheet_ready or back_ready:
        boards.append(TTSForgedFactionBoard(faction, request=request).to_dict())
        if sheet and sheet.decree_preview:
            boards.append(TTSForgedFactionDecree(faction, request=request).to_dict())
        improvements_transform = {
            "posX": -16.0, "posY": -0.113604546, "posZ": 0.0,
            "rotX": 0.0, "rotY": 180.0, "rotZ": 0.0,
            "scaleX": 9.516764, "scaleY": 1.0, "scaleZ": 9.516764,
        }
        boards.append(load_tts_object('Improved Crafted Improvements.json', transform=improvements_transform))
        if back_ready:
            boards.extend(place_pieces_for_back(back, sheet=sheet, request=request))
        boards.extend(place_markers_for_faction(faction, request=request))
    if card_ready:
        from the_keep.services.tts import DEFAULT_CARD_TRANSFORM
        adset_transform = dict(DEFAULT_CARD_TRANSFORM)
        if sheet_ready or back_ready:
            # Center on the southernmost snap point of the Improved Crafted
            # Improvements tile. The tile sits at (-16, 0) and is rotated 180°
            # around Y, so its local +Z snap points map to world -Z. The
            # southernmost (largest world-negative Z) snap point is the one
            # with local z=+0.518260956.
            adset_transform["posX"] = -16.0 + (-0.00133301085 * 9.516764)
            adset_transform["posZ"] = 0.0 + (-0.518260956 * 9.516764)
            adset_transform["posY"] = 2.0
        adset = TTSSingleCardDeck(
            card.image_preview,
            static("images/ADSET.png"),
            deck_id=1,
            request=request,
            card_name="Adset Card",
            transform=adset_transform,
        )
        boards.append(adset.to_object())
    save_file = wrap_tts_save(boards, save_name=faction.faction_name)

    response = HttpResponse(
        json.dumps(save_file, indent=2),
        content_type='application/json',
    )
    safe_name = (faction.faction_name or faction.slug or str(faction.pk)).replace('"', '').replace('\\', '')
    filename = f'{safe_name}.json'
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return _attach_preview_versions(response, sheet=sheet, back=back, card=card)


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
    response = _pdf_file_response(data, f'{card.faction.faction_name} - Adset.pdf')
    return _attach_preview_versions(response, card=card)


@login_required
def setup_card_pdf_layered(request, pk):
    card = get_object_or_404(SetupCard, pk=pk)
    if (resp := _forbid_if_not_editor(request, card.faction)):
        return resp
    from io import BytesIO
    from .pdf_engine import SetupCardLayoutEngine
    from .pdf_cache import cache_key, fingerprint_setup_card, get_or_build
    fp = fingerprint_setup_card(card)
    key = cache_key('setup_card-layered', card.pk, fp)
    def build():
        buffer = BytesIO()
        SetupCardLayoutEngine(card).build(buffer, layered=True)
        return buffer.getvalue()
    data = get_or_build(key, build)
    return _pdf_file_response(data, f'{card.faction.faction_name} - Adset (Layers).pdf')


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
    response = _webp_file_response(card.image_preview, f'{card.faction.faction_name} - Adset.webp')
    return _attach_preview_versions(response, card=card)


# ---------- Publish / Link / Sync ----------

def _link_candidate_exists(profile, forged):
    """Cheap exists() check matching the three sources in ForgedFactionLinkForm."""
    if profile is None or forged is None:
        return False
    same_lang_faction = Faction.objects.filter(
        designer=profile,
        source_forged_faction__isnull=True,
        title__iexact=forged.faction_name,
        language=forged.language,
    ).exists()
    same_lang_translation = PostTranslation.objects.filter(
        post__designer=profile,
        source_forged_faction__isnull=True,
        translated_title__iexact=forged.faction_name,
        language=forged.language,
        post__component__in=['Faction', 'Clockwork'],
    ).exists()
    cross_lang_faction = Faction.objects.filter(
        designer=profile,
        title__iexact=forged.faction_name,
    ).exclude(language=forged.language).exclude(
        translations__language=forged.language,
    ).exists()
    return same_lang_faction or same_lang_translation or cross_lang_faction


def _resolved_published(forged):
    """Return (target, mode, label) for whichever publish link is set, or
    (None, None, None) if not linked."""
    if forged.published_faction_id:
        f = forged.published_faction
        return f, 'faction', f.title
    if forged.published_translation_id:
        t = forged.published_translation
        lang = t.language.name if t.language else '?'
        return t, 'translation', f'{t.translated_title or t.post.title} ({lang})'
    return None, None, None


@forge_onboard_required
def forgedfaction_submit(request, pk):
    forged = get_object_or_404(ForgedFaction, pk=pk)
    if (resp := _forbid_if_not_editor(request, forged)):
        return resp
    if forged.published_faction_id or forged.published_translation_id:
        messages.info(request, "This forge draft is already published.")
        return redirect('forge-faction-detail', pk=forged.pk)

    _refresh_all_element_previews(forged)
    missing = submit_prerequisites_missing(forged)
    if missing:
        messages.error(
            request,
            "Submit isn't ready yet — missing: " + ", ".join(missing) + ".",
        )
        return redirect('forge-faction-detail', pk=forged.pk)

    if request.method == 'POST':
        form = ForgedFactionSubmitForm(
            request.POST, request.FILES,
            forged_faction=forged,
            user=request.user,
        )
        if form.is_valid():
            profile = request.user.profile
            faction = form.save(commit=False)
            if not faction.designer_id:
                faction.designer = profile
            faction.submitted_by = profile
            if not profile.admin:
                faction.status = '9'

            sheet = _safe_related_for_view(forged, 'faction_sheet')
            back = _safe_related_for_view(forged, 'faction_back')
            card = _safe_related_for_view(forged, 'setup_card')
            if sheet and sheet.image_preview:
                faction.board_image = _file_from(sheet.image_preview)
                faction.board_image_version = sheet.preview_version or 0
            if back and back.image_preview:
                faction.board_2_image = _file_from(back.image_preview)
                faction.board_2_image_version = back.preview_version or 0
            if card and card.image_preview:
                faction.card_image = _file_from(card.image_preview)
                faction.card_image_version = card.preview_version or 0
            if not faction.small_icon and forged.faction_icon:
                faction.small_icon = _file_from(forged.faction_icon)

            faction.save()
            form.save_m2m()

            forged.published_faction = faction
            forged.save(update_fields=['published_faction'])

            fields = [{'name': 'Submitted by:', 'value': profile.name}]
            send_rich_discord_message_task.delay(
                f'[{faction.title}](https://therootdatabase.com{faction.get_absolute_url()})',
                category='report', title=f'Submitted {faction.component}', fields=fields,
            )
            messages.success(request, f"Submitted '{faction.title}' for review.")
            return redirect(faction.get_absolute_url())
    else:
        form = ForgedFactionSubmitForm(forged_faction=forged, user=request.user)

    return render(request, 'the_forge/forgedfaction_submit.html', {
        'faction': forged,
        'form': form,
        'art_by_hint': forged.art_by,
        'picture_options': form._picture_options,
    })


@forge_onboard_required
def forgedfaction_link(request, pk):
    forged = get_object_or_404(ForgedFaction, pk=pk)
    if (resp := _forbid_if_not_editor(request, forged)):
        return resp
    if forged.published_faction_id or forged.published_translation_id:
        messages.info(request, "This forge draft is already published.")
        return redirect('forge-faction-detail', pk=forged.pk)

    if request.method == 'POST':
        form = ForgedFactionLinkForm(request.POST, user=request.user, forged=forged)
        if form.is_valid():
            mode = form.resolved_mode
            target = form.resolved
            if mode == ForgedFactionLinkForm.PREFIX_FACTION:
                forged.published_faction = target
                forged.save(update_fields=['published_faction'])
                messages.success(request, f"Linked to existing Faction '{target.title}'.")
            elif mode == ForgedFactionLinkForm.PREFIX_TRANSLATION:
                forged.published_translation = target
                forged.save(update_fields=['published_translation'])
                messages.success(
                    request,
                    f"Linked to existing translation of '{target.post.title}'.",
                )
            elif mode == ForgedFactionLinkForm.PREFIX_NEW_TRANSLATION:
                new_translation = PostTranslation.objects.create(
                    post=target,
                    language=forged.language,
                    designer=forged.designer,
                    translated_title=forged.faction_name,
                )
                forged.published_translation = new_translation
                forged.save(update_fields=['published_translation'])
                messages.success(
                    request,
                    f"Started a {forged.language.name} translation of '{target.title}'. "
                    "Open Sync to fill it in.",
                )
            return redirect('forge-faction-detail', pk=forged.pk)
    else:
        form = ForgedFactionLinkForm(user=request.user, forged=forged)

    return render(request, 'the_forge/forgedfaction_link.html', {
        'faction': forged,
        'form': form,
    })


@forge_onboard_required
def forgedfaction_sync(request, pk):
    forged = get_object_or_404(ForgedFaction, pk=pk)
    if (resp := _forbid_if_not_editor(request, forged)):
        return resp

    _refresh_all_element_previews(forged)

    target, mode, target_label = _resolved_published(forged)
    if target is None:
        messages.error(request, "Link this forge draft to a Faction or translation first.")
        return redirect('forge-faction-detail', pk=forged.pk)

    profile = request.user.profile
    owner = target.designer if mode == 'faction' else target.post.designer
    if owner_id := getattr(owner, 'id', None):
        if owner_id != profile.id:
            return redirect('forge-home')

    if request.method == 'POST':
        form = ForgedFactionSyncForm(request.POST, forged=forged)
        if form.is_valid():
            form.save()
            if owner and owner.id != profile.id:
                UserNotification.create_notification(
                    profile=owner,
                    message=f"Forge updates were applied to '{target_label}'.",
                    message_type=MessageChoices.INFO,
                    related_url=(
                        target.get_absolute_url()
                        if mode == 'faction'
                        else target.post.get_absolute_url()
                    ),
                    sender=profile,
                )
            messages.success(request, "Selected changes applied.")
            return redirect('forge-faction-detail', pk=forged.pk)
    else:
        form = ForgedFactionSyncForm(forged=forged)

    field_changes = any(not row[5] for row in form.diff_rows)
    piece_changes = any(not entry['in_sync'] for entry in form.piece_plan)
    has_changes = field_changes or piece_changes
    return render(request, 'the_forge/forgedfaction_sync.html', {
        'faction': forged,
        'form': form,
        'mode': mode,
        'target': target,
        'target_label': target_label,
        'diff_rows': form.diff_rows,
        'piece_plan': form.piece_plan,
        'has_changes': has_changes,
    })


def _safe_related_for_view(forged, attr):
    try:
        return getattr(forged, attr)
    except (FactionSheet.DoesNotExist, FactionBack.DoesNotExist, SetupCard.DoesNotExist):
        return None


def _file_from(image_field):
    """Read an existing ImageField into a fresh upload-style File so saving
    on a different model writes to that model's own upload path."""
    from django.core.files.base import ContentFile
    image_field.open('rb')
    try:
        data = image_field.read()
    finally:
        image_field.close()
    name = image_field.name.rsplit('/', 1)[-1]
    return ContentFile(data, name=name)
