import json

from django.contrib.auth.decorators import login_required
from django.http import (
    HttpResponse,
    HttpResponseForbidden,
    HttpResponseBadRequest,
)
from django.shortcuts import get_object_or_404, redirect, render
from django.templatetags.static import static
from django.views.decorators.http import require_http_methods

from the_gatehouse.views import editor_required
from .inline_images import picker_image_map, picker_keywords

from .forms import (
    BorderedBoxForm,
    CardPileForm,
    CardSlotForm,
    CardboardSlotForm,
    CardboardTrackForm,
    ContentBoxForm,
    DecreeSectionForm,
    FactionAbilityForm,
    FactionBackForm,
    ForgedFactionForm,
    PhaseStepForm,
    PieceForm,
    SetupCardForm,
    SetupStepForm,
    StepActionForm,
)
from .models import (
    BorderedBox,
    CardPile,
    CardSlot,
    CardboardSlot,
    CardboardTrack,
    ContentBox,
    DecreeSection,
    FactionAbility,
    FactionBack,
    FactionSheet,
    ForgedFaction,
    PhaseStep,
    Piece,
    SetupCard,
    SetupStep,
    StepAction,
)


# ---------- Helpers ----------

def user_can_edit_forge(request, faction):
    """True if the request user is the faction's designer or an admin."""
    if not request.user.is_authenticated:
        return False
    profile = getattr(request.user, 'profile', None)
    if not profile:
        return False
    if getattr(profile, 'admin', False):
        return True
    return faction.designer_id == profile.id


def _inline_keywords():
    return picker_keywords()


def _inline_images_map():
    return picker_image_map()


def _step_svg_url(number):
    return static(f'pdf/svg/{(number or 0) % 10}.svg')


def _annotate_step(step):
    step.svg_url = _step_svg_url(step.number)
    return step


def _annotate_steps(steps):
    return [_annotate_step(s) for s in steps]


def _forbid_if_not_editor(request, faction):
    if not user_can_edit_forge(request, faction):
        return HttpResponseForbidden("You do not have permission to edit this faction.")
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


# ---------- ForgedFaction CRUD ----------

@login_required
def forgedfaction_list(request):
    profile = getattr(request.user, 'profile', None)
    factions = ForgedFaction.objects.filter(designer=profile).order_by('faction_name') if profile else []
    return render(request, 'the_forge/forgedfaction_list.html', {'factions': factions})


@editor_required
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


@login_required
def forgedfaction_detail(request, pk):
    faction = get_object_or_404(ForgedFaction, pk=pk)
    if not user_can_edit_forge(request, faction):
        return HttpResponseForbidden()
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
        'sheet': sheet,
        'back': back,
        'setup_card': setup_card,
    })


@editor_required
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


@editor_required
@require_http_methods(["POST"])
def forgedfaction_delete(request, pk):
    faction = get_object_or_404(ForgedFaction, pk=pk)
    if (resp := _forbid_if_not_editor(request, faction)):
        return resp
    faction.delete()
    return redirect('forge-faction-list')


# ---------- FactionSheet ----------

@editor_required
def factionsheet_create(request, faction_pk):
    faction = get_object_or_404(ForgedFaction, pk=faction_pk)
    if (resp := _forbid_if_not_editor(request, faction)):
        return resp
    sheet, _ = FactionSheet.objects.get_or_create(faction=faction)
    return redirect('forge-sheet-edit', pk=sheet.pk)


@editor_required
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
        'inline_keywords': _inline_keywords(),
        'inline_images_map': _inline_images_map(),
        'inline_images': _inline_images_map(),
        'box_height_choices': BorderedBox.BoxSize.choices,
        'ability_form': FactionAbilityForm(),
        'content_box_form': ContentBoxForm(),
        'phase_step_form': PhaseStepForm(sheet=sheet),
        'decree_form': DecreeSectionForm(),
        'card_pile_form': CardPileForm(),
    })


@editor_required
@require_http_methods(["POST"])
def factionsheet_delete(request, pk):
    sheet = get_object_or_404(FactionSheet, pk=pk)
    if (resp := _forbid_if_not_editor(request, sheet.faction)):
        return resp
    faction_pk = sheet.faction_id
    sheet.delete()
    return redirect('forge-faction-detail', pk=faction_pk)


@editor_required
@require_http_methods(["POST"])
def sheet_flavor_edit(request, pk):
    sheet = get_object_or_404(FactionSheet, pk=pk)
    if (resp := _child_permission_check(request, sheet.faction)):
        return resp
    sheet.flavor_text = request.POST.get('flavor_text', '')
    sheet.save(update_fields=['flavor_text'])
    return render(request, 'the_forge/partials/sheet_flavor_form.html', {'sheet': sheet})


@editor_required
@require_http_methods(["POST"])
def sheet_crafted_toggle(request, pk):
    sheet = get_object_or_404(FactionSheet, pk=pk)
    if (resp := _child_permission_check(request, sheet.faction)):
        return resp
    sheet.include_crafted_items = request.POST.get('value') == 'true'
    sheet.save(update_fields=['include_crafted_items'])
    return HttpResponse(status=204)


@editor_required
@require_http_methods(["POST"])
def sheet_layout_toggle(request, pk):
    sheet = get_object_or_404(FactionSheet, pk=pk)
    if (resp := _child_permission_check(request, sheet.faction)):
        return resp
    value = request.POST.get('value')
    if value not in {c.value for c in FactionSheet.LayoutChoices}:
        return HttpResponseBadRequest('Invalid layout value')
    sheet.layout_mode = value
    sheet.save(update_fields=['layout_mode'])
    return HttpResponse(status=204)


@editor_required
@require_http_methods(["POST"])
def sheet_decree_toggle(request, pk):
    sheet = get_object_or_404(FactionSheet, pk=pk)
    if (resp := _child_permission_check(request, sheet.faction)):
        return resp
    turning_on = request.POST.get('value') == 'true'
    if turning_on:
        sheet.include_decree = True
        sheet.save(update_fields=['include_decree'])
        decree, _ = DecreeSection.objects.get_or_create(sheet=sheet)
        return render(request, 'the_forge/partials/decree_section.html', {
            'decree': decree,
            'inline_keywords': _inline_keywords(),
        })
    sheet.decrees.all().delete()
    sheet.include_decree = False
    sheet.save(update_fields=['include_decree'])
    return HttpResponse(status=204)


# ---------- FactionBack ----------

@editor_required
def factionback_create(request, faction_pk):
    faction = get_object_or_404(ForgedFaction, pk=faction_pk)
    if (resp := _forbid_if_not_editor(request, faction)):
        return resp
    back, _ = FactionBack.objects.get_or_create(faction=faction)
    return redirect('forge-back-edit', pk=back.pk)


@editor_required
def factionback_edit(request, pk):
    back = get_object_or_404(FactionBack, pk=pk)
    if (resp := _forbid_if_not_editor(request, back.faction)):
        return resp
    if request.method == 'POST':
        form = FactionBackForm(request.POST, request.FILES, instance=back)
        if form.is_valid():
            form.save()
            return redirect('forge-back-edit', pk=back.pk)
    else:
        form = FactionBackForm(instance=back)
    pieces_qs = list(back.pieces.order_by('id'))
    piece_sections = [
        ('W', 'Warriors', [p for p in pieces_qs if p.type == 'W']),
        ('B', 'Buildings', [p for p in pieces_qs if p.type == 'B']),
        ('T', 'Tokens', [p for p in pieces_qs if p.type == 'T']),
        ('C', 'Cards', [p for p in pieces_qs if p.type == 'C']),
        ('O', 'Other Pieces', [p for p in pieces_qs if p.type == 'O']),
    ]
    return render(request, 'the_forge/factionback_editor.html', {
        'back': back,
        'faction': back.faction,
        'form': form,
        'piece_sections': piece_sections,
        'setup_steps': _annotate_steps(back.setup_steps.order_by('number')),
        'piece_form': PieceForm(),
        'setup_step_form': SetupStepForm(),
        'inline_keywords': _inline_keywords(),
        'inline_images_map': _inline_images_map(),
        'attribute_fields': [
            ('complexity', 'Complexity'),
            ('aggression', 'Aggression'),
            ('card_wealth', 'Card Wealth'),
            ('crafting_ability', 'Crafting Ability'),
        ],
    })


# ---------- Child-row permission helper ----------

def _child_permission_check(request, faction):
    if not user_can_edit_forge(request, faction):
        return HttpResponseForbidden()
    return None


# ---------- Pieces (child of FactionBack) ----------

@editor_required
@require_http_methods(["POST"])
def piece_add(request, back_pk):
    back = get_object_or_404(FactionBack, pk=back_pk)
    if (resp := _child_permission_check(request, back.faction)):
        return resp
    form = PieceForm(request.POST, request.FILES)
    if not form.is_valid():
        return HttpResponseBadRequest(str(form.errors))
    piece = form.save(commit=False)
    piece.parent = back
    piece.save()
    return render(request, 'the_forge/partials/piece_row.html', {'piece': piece})


@editor_required
@require_http_methods(["POST"])
def piece_edit(request, pk):
    piece = get_object_or_404(Piece, pk=pk)
    if (resp := _child_permission_check(request, piece.parent.faction)):
        return resp
    form = PieceForm(request.POST, request.FILES, instance=piece)
    if not form.is_valid():
        return HttpResponseBadRequest(str(form.errors))
    form.save()
    return render(request, 'the_forge/partials/piece_row.html', {'piece': piece})


@editor_required
@require_http_methods(["DELETE"])
def piece_delete(request, pk):
    piece = get_object_or_404(Piece, pk=pk)
    if (resp := _child_permission_check(request, piece.parent.faction)):
        return resp
    piece.delete()
    return HttpResponse(status=204)


# ---------- SetupStep (child of FactionBack) ----------

@editor_required
@require_http_methods(["POST"])
def setup_step_add(request, back_pk):
    back = get_object_or_404(FactionBack, pk=back_pk)
    if (resp := _child_permission_check(request, back.faction)):
        return resp
    form = SetupStepForm(request.POST)
    if not form.is_valid():
        return HttpResponseBadRequest(str(form.errors))
    step = form.save(commit=False)
    step.faction_back = back
    step.number = back.setup_steps.count() + 1
    step.save()
    return render(request, 'the_forge/partials/setup_step_row.html', {
        'step': _annotate_step(step), 'inline_keywords': _inline_keywords(),
    })


@editor_required
@require_http_methods(["POST"])
def setup_step_edit(request, pk):
    step = get_object_or_404(SetupStep, pk=pk)
    faction = step.faction_back.faction if step.faction_back else step.card.faction
    if (resp := _child_permission_check(request, faction)):
        return resp
    form = SetupStepForm(request.POST, instance=step)
    if not form.is_valid():
        return HttpResponseBadRequest(str(form.errors))
    form.save()
    return render(request, 'the_forge/partials/setup_step_row.html', {
        'step': _annotate_step(step), 'inline_keywords': _inline_keywords(),
    })


@editor_required
@require_http_methods(["DELETE"])
def setup_step_delete(request, pk):
    step = get_object_or_404(SetupStep, pk=pk)
    faction = step.faction_back.faction if step.faction_back else step.card.faction
    if (resp := _child_permission_check(request, faction)):
        return resp
    step.delete()
    return HttpResponse(status=204)


@editor_required
@require_http_methods(["POST"])
def setup_step_reorder(request, back_pk):
    back = get_object_or_404(FactionBack, pk=back_pk)
    if (resp := _child_permission_check(request, back.faction)):
        return resp
    data = json.loads(request.body)
    for index, sid in enumerate(data.get('order', []), start=1):
        SetupStep.objects.filter(id=sid, faction_back=back).update(number=index)
    return HttpResponse(status=204)


# ---------- SetupCard (child of ForgedFaction) ----------

@editor_required
def setup_card_create(request, faction_pk):
    faction = get_object_or_404(ForgedFaction, pk=faction_pk)
    if (resp := _forbid_if_not_editor(request, faction)):
        return resp
    card, _ = SetupCard.objects.get_or_create(faction=faction)
    return redirect('forge-setup-card-edit', pk=card.pk)


@editor_required
def setup_card_edit(request, pk):
    card = get_object_or_404(SetupCard, pk=pk)
    if (resp := _forbid_if_not_editor(request, card.faction)):
        return resp
    if request.method == 'POST':
        form = SetupCardForm(request.POST, instance=card)
        if form.is_valid():
            form.save()
            return redirect('forge-setup-card-edit', pk=card.pk)
    else:
        form = SetupCardForm(instance=card)
    return render(request, 'the_forge/setup_card_editor.html', {
        'card': card,
        'faction': card.faction,
        'form': form,
        'setup_steps': _annotate_steps(card.setup_steps.order_by('number')),
        'setup_step_form': SetupStepForm(),
        'inline_keywords': _inline_keywords(),
        'inline_images_map': _inline_images_map(),
    })


@editor_required
@require_http_methods(["POST"])
def setup_card_step_add(request, card_pk):
    card = get_object_or_404(SetupCard, pk=card_pk)
    if (resp := _child_permission_check(request, card.faction)):
        return resp
    form = SetupStepForm(request.POST)
    if not form.is_valid():
        return HttpResponseBadRequest(str(form.errors))
    step = form.save(commit=False)
    step.card = card
    step.number = card.setup_steps.count() + 1
    step.save()
    return render(request, 'the_forge/partials/setup_step_row.html', {
        'step': _annotate_step(step), 'inline_keywords': _inline_keywords(),
    })


@editor_required
@require_http_methods(["POST"])
def setup_card_step_reorder(request, card_pk):
    card = get_object_or_404(SetupCard, pk=card_pk)
    if (resp := _child_permission_check(request, card.faction)):
        return resp
    data = json.loads(request.body)
    for index, sid in enumerate(data.get('order', []), start=1):
        SetupStep.objects.filter(id=sid, card=card).update(number=index)
    return HttpResponse(status=204)


# ---------- FactionAbility (child of FactionSheet) ----------

@editor_required
@require_http_methods(["POST"])
def ability_add(request, sheet_pk):
    sheet = get_object_or_404(FactionSheet, pk=sheet_pk)
    if (resp := _child_permission_check(request, sheet.faction)):
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
        'ability': ability, 'inline_keywords': _inline_keywords(),
    })


@editor_required
@require_http_methods(["POST"])
def ability_edit(request, pk):
    ability = get_object_or_404(FactionAbility, pk=pk)
    if (resp := _child_permission_check(request, ability.sheet.faction)):
        return resp
    form = FactionAbilityForm(request.POST, instance=ability)
    if not form.is_valid():
        return HttpResponseBadRequest(str(form.errors))
    form.save()
    return render(request, 'the_forge/partials/ability_row.html', {
        'ability': ability, 'inline_keywords': _inline_keywords(),
    })


@editor_required
@require_http_methods(["DELETE"])
def ability_delete(request, pk):
    ability = get_object_or_404(FactionAbility, pk=pk)
    if (resp := _child_permission_check(request, ability.sheet.faction)):
        return resp
    ability.delete()
    return HttpResponse(status=204)


@editor_required
@require_http_methods(["POST"])
def ability_reorder(request, sheet_pk):
    sheet = get_object_or_404(FactionSheet, pk=sheet_pk)
    if (resp := _child_permission_check(request, sheet.faction)):
        return resp
    data = json.loads(request.body)
    for index, aid in enumerate(data.get('order', []), start=1):
        FactionAbility.objects.filter(id=aid, sheet=sheet).update(order=index)
    return HttpResponse(status=204)


# ---------- ContentBox (child of FactionSheet) ----------

@editor_required
@require_http_methods(["POST"])
def contentbox_add(request, sheet_pk):
    sheet = get_object_or_404(FactionSheet, pk=sheet_pk)
    if (resp := _child_permission_check(request, sheet.faction)):
        return resp
    form = ContentBoxForm(request.POST)
    if not form.is_valid():
        return HttpResponseBadRequest(str(form.errors))
    box = form.save(commit=False)
    box.sheet = sheet
    if not box.order:
        box.order = sheet.content_boxes.count() + 1
    box.save()
    box.annotated_steps = []
    return render(request, 'the_forge/partials/content_box_row.html', {
        'box': box, 'inline_keywords': _inline_keywords(),
    })


@editor_required
@require_http_methods(["POST"])
def contentbox_edit(request, pk):
    box = get_object_or_404(ContentBox, pk=pk)
    if (resp := _child_permission_check(request, box.sheet.faction)):
        return resp
    form = ContentBoxForm(request.POST, instance=box)
    if not form.is_valid():
        return HttpResponseBadRequest(str(form.errors))
    form.save()
    box.annotated_steps = _annotate_steps(box.steps.order_by('number'))
    return render(request, 'the_forge/partials/content_box_row.html', {
        'box': box, 'inline_keywords': _inline_keywords(),
    })


@editor_required
@require_http_methods(["DELETE"])
def contentbox_delete(request, pk):
    box = get_object_or_404(ContentBox, pk=pk)
    if (resp := _child_permission_check(request, box.sheet.faction)):
        return resp
    sheet = box.sheet
    box.delete()
    for index, sibling in enumerate(sheet.content_boxes.order_by('order'), start=1):
        if sibling.order != index:
            ContentBox.objects.filter(pk=sibling.pk).update(order=index)
    return HttpResponse(status=204)


@editor_required
@require_http_methods(["POST"])
def contentbox_reorder(request, sheet_pk):
    sheet = get_object_or_404(FactionSheet, pk=sheet_pk)
    if (resp := _child_permission_check(request, sheet.faction)):
        return resp
    data = json.loads(request.body)
    for index, bid in enumerate(data.get('order', []), start=1):
        ContentBox.objects.filter(id=bid, sheet=sheet).update(order=index)
    return HttpResponse(status=204)


# ---------- PhaseStep (child of FactionSheet) ----------

@editor_required
@require_http_methods(["POST"])
def phasestep_add(request, sheet_pk):
    sheet = get_object_or_404(FactionSheet, pk=sheet_pk)
    if (resp := _child_permission_check(request, sheet.faction)):
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
    return render(request, 'the_forge/partials/phase_step_row.html', {
        'step': _annotate_step(step), 'inline_keywords': _inline_keywords(),
    })


@editor_required
@require_http_methods(["POST"])
def phasestep_edit(request, pk):
    step = get_object_or_404(PhaseStep, pk=pk)
    if (resp := _child_permission_check(request, step.sheet.faction)):
        return resp
    post = request.POST.copy()
    post['phase'] = step.phase
    if step.content_box_id:
        post['content_box'] = step.content_box_id
    form = PhaseStepForm(post, instance=step, sheet=step.sheet)
    if not form.is_valid():
        return HttpResponseBadRequest(str(form.errors))
    form.save()
    return render(request, 'the_forge/partials/phase_step_row.html', {
        'step': _annotate_step(step), 'inline_keywords': _inline_keywords(),
    })


@editor_required
@require_http_methods(["DELETE"])
def phasestep_delete(request, pk):
    step = get_object_or_404(PhaseStep, pk=pk)
    if (resp := _child_permission_check(request, step.sheet.faction)):
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


@editor_required
@require_http_methods(["POST"])
def phasestep_reorder(request, sheet_pk, phase):
    sheet = get_object_or_404(FactionSheet, pk=sheet_pk)
    if (resp := _child_permission_check(request, sheet.faction)):
        return resp
    data = json.loads(request.body)
    for index, sid in enumerate(data.get('order', []), start=1):
        PhaseStep.objects.filter(id=sid, sheet=sheet).update(number=index, phase=phase)
    return HttpResponse(status=204)


@editor_required
@require_http_methods(["POST"])
def phasestep_reorder_in_box(request, content_box_pk):
    box = get_object_or_404(ContentBox, pk=content_box_pk)
    if (resp := _child_permission_check(request, box.sheet.faction)):
        return resp
    data = json.loads(request.body)
    for index, sid in enumerate(data.get('order', []), start=1):
        PhaseStep.objects.filter(id=sid, content_box=box).update(number=index)
    return HttpResponse(status=204)


# ---------- StepAction (child of PhaseStep) ----------

def _track_render_ctx(track):
    return {
        'track': track,
        'inline_images': _inline_images_map(),
        'inline_keywords': _inline_keywords(),
    }


@editor_required
@require_http_methods(["POST"])
def stepaction_add(request, step_pk):
    step = get_object_or_404(PhaseStep, pk=step_pk)
    if (resp := _child_permission_check(request, step.sheet.faction)):
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
    return render(request, 'the_forge/partials/step_action_row.html', {
        'action': action, 'inline_keywords': _inline_keywords(),
    })


@editor_required
@require_http_methods(["POST"])
def stepaction_edit(request, pk):
    action = get_object_or_404(StepAction, pk=pk)
    if (resp := _child_permission_check(request, action.step.sheet.faction)):
        return resp
    form = StepActionForm(request.POST, request.FILES, instance=action)
    if not form.is_valid():
        return HttpResponseBadRequest(str(form.errors))
    form.save()
    return render(request, 'the_forge/partials/step_action_row.html', {
        'action': action, 'inline_keywords': _inline_keywords(),
    })


@editor_required
@require_http_methods(["DELETE"])
def stepaction_delete(request, pk):
    action = get_object_or_404(StepAction, pk=pk)
    if (resp := _child_permission_check(request, action.step.sheet.faction)):
        return resp
    action.delete()
    return HttpResponse(status=204)


@editor_required
@require_http_methods(["POST"])
def stepaction_reorder(request, step_pk):
    step = get_object_or_404(PhaseStep, pk=step_pk)
    if (resp := _child_permission_check(request, step.sheet.faction)):
        return resp
    data = json.loads(request.body)
    for index, aid in enumerate(data.get('order', []), start=1):
        StepAction.objects.filter(id=aid, step=step).update(order=index)
    return HttpResponse(status=204)


@editor_required
@require_http_methods(["GET"])
def stepaction_form(request, step_pk):
    step = get_object_or_404(PhaseStep, pk=step_pk)
    if (resp := _child_permission_check(request, step.sheet.faction)):
        return resp
    return render(request, 'the_forge/partials/step_action_form.html', {
        'step': step, 'step_pk': step.pk,
    })


@editor_required
@require_http_methods(["POST"])
def phasestep_action_type_set(request, step_pk):
    step = get_object_or_404(PhaseStep, pk=step_pk)
    if (resp := _child_permission_check(request, step.sheet.faction)):
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


# ---------- BorderedBox (child of PhaseStep) ----------

@editor_required
@require_http_methods(["POST"])
def borderedbox_add(request, step_pk):
    step = get_object_or_404(PhaseStep, pk=step_pk)
    if (resp := _child_permission_check(request, step.sheet.faction)):
        return resp
    form = BorderedBoxForm(request.POST)
    if not form.is_valid():
        return HttpResponseBadRequest(str(form.errors))
    box = form.save(commit=False)
    box.step = step
    box.order = step.boxes.count() + step.tracks.count() + 1
    box.save()
    return render(request, 'the_forge/partials/bordered_box_row.html', {
        'box': box, 'inline_keywords': _inline_keywords(),
    })


@editor_required
@require_http_methods(["POST"])
def borderedbox_edit(request, pk):
    box = get_object_or_404(BorderedBox, pk=pk)
    if (resp := _child_permission_check(request, box.step.sheet.faction)):
        return resp
    form = BorderedBoxForm(request.POST, instance=box)
    if not form.is_valid():
        return HttpResponseBadRequest(str(form.errors))
    form.save()
    return render(request, 'the_forge/partials/bordered_box_row.html', {
        'box': box, 'inline_keywords': _inline_keywords(),
    })


@editor_required
@require_http_methods(["DELETE"])
def borderedbox_delete(request, pk):
    box = get_object_or_404(BorderedBox, pk=pk)
    if (resp := _child_permission_check(request, box.step.sheet.faction)):
        return resp
    box.delete()
    return HttpResponse(status=204)


@editor_required
@require_http_methods(["POST"])
def step_children_reorder(request, step_pk):
    """Reorder the merged boxes+tracks list for a step.
    Body: {"order": [{"kind": "box"|"track", "id": <pk>}, ...]}
    Each model's `order` field is rewritten so the merged list round-trips.
    """
    step = get_object_or_404(PhaseStep, pk=step_pk)
    if (resp := _child_permission_check(request, step.sheet.faction)):
        return resp
    data = json.loads(request.body)
    for index, item in enumerate(data.get('order', []), start=1):
        kind = item.get('kind')
        oid = item.get('id')
        if kind == 'box':
            BorderedBox.objects.filter(id=oid, step=step).update(order=index)
        elif kind == 'track':
            CardboardTrack.objects.filter(id=oid, step=step).update(order=index)
    return HttpResponse(status=204)


# ---------- CardboardTrack (child of PhaseStep) ----------

@editor_required
@require_http_methods(["POST"])
def track_add(request, step_pk):
    step = get_object_or_404(PhaseStep, pk=step_pk)
    if (resp := _child_permission_check(request, step.sheet.faction)):
        return resp
    form = CardboardTrackForm(request.POST, request.FILES)
    if not form.is_valid():
        return HttpResponseBadRequest(str(form.errors))
    track = form.save(commit=False)
    track.step = step
    track.order = step.boxes.count() + step.tracks.count() + 1
    track.save()
    return render(request, 'the_forge/partials/track_row.html', _track_render_ctx(track))


@editor_required
@require_http_methods(["POST"])
def track_edit(request, pk):
    track = get_object_or_404(CardboardTrack, pk=pk)
    if (resp := _child_permission_check(request, track.step.sheet.faction)):
        return resp
    form = CardboardTrackForm(request.POST, request.FILES, instance=track)
    if not form.is_valid():
        return HttpResponseBadRequest(str(form.errors))
    new_cols = form.cleaned_data.get('num_columns', track.num_columns)
    new_rows = form.cleaned_data.get('num_rows', track.num_rows)
    form.save()
    track.slots.filter(column__gte=new_cols).delete()
    track.slots.filter(row__gte=new_rows).delete()
    return render(request, 'the_forge/partials/track_row.html', _track_render_ctx(track))


@editor_required
@require_http_methods(["DELETE"])
def track_delete(request, pk):
    track = get_object_or_404(CardboardTrack, pk=pk)
    if (resp := _child_permission_check(request, track.step.sheet.faction)):
        return resp
    track.delete()
    return HttpResponse(status=204)


# ---------- CardboardSlot (child of CardboardTrack) ----------

@editor_required
@require_http_methods(["POST"])
def slot_upsert(request, track_pk, row, col):
    """Create or update the slot at (row, col) for the given track.
    Used by the slot edit modal — returns the rendered slot cell partial."""
    track = get_object_or_404(CardboardTrack, pk=track_pk)
    if (resp := _child_permission_check(request, track.step.sheet.faction)):
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
        'inline_images': _inline_images_map(),
    })


@editor_required
@require_http_methods(["DELETE"])
def slot_delete(request, pk):
    slot = get_object_or_404(CardboardSlot, pk=pk)
    if (resp := _child_permission_check(request, slot.track.step.sheet.faction)):
        return resp
    slot.delete()
    return HttpResponse(status=204)


# ---------- DecreeSection + CardSlot ----------

@editor_required
@require_http_methods(["POST"])
def decree_edit(request, pk):
    decree = get_object_or_404(DecreeSection, pk=pk)
    if (resp := _child_permission_check(request, decree.sheet.faction)):
        return resp
    form = DecreeSectionForm(request.POST, instance=decree)
    if not form.is_valid():
        return HttpResponseBadRequest(str(form.errors))
    form.save()
    return render(request, 'the_forge/partials/decree_title_form.html', {'decree': decree})


@editor_required
@require_http_methods(["POST"])
def cardslot_add(request, decree_pk):
    decree = get_object_or_404(DecreeSection, pk=decree_pk)
    if (resp := _child_permission_check(request, decree.sheet.faction)):
        return resp
    form = CardSlotForm(request.POST)
    if not form.is_valid():
        return HttpResponseBadRequest(str(form.errors))
    slot = form.save(commit=False)
    slot.decree = decree
    if not slot.number:
        slot.number = decree.card_slots.count() + 1
    slot.save()
    return render(request, 'the_forge/partials/card_slot_row.html', {
        'slot': slot, 'inline_keywords': _inline_keywords(),
    })


@editor_required
@require_http_methods(["POST"])
def cardslot_reorder(request, decree_pk):
    decree = get_object_or_404(DecreeSection, pk=decree_pk)
    if (resp := _child_permission_check(request, decree.sheet.faction)):
        return resp
    data = json.loads(request.body)
    for index, sid in enumerate(data.get('order', []), start=1):
        CardSlot.objects.filter(id=sid, decree=decree).update(number=index)
    return HttpResponse(status=204)


@editor_required
@require_http_methods(["POST"])
def cardslot_edit(request, pk):
    slot = get_object_or_404(CardSlot, pk=pk)
    if (resp := _child_permission_check(request, slot.decree.sheet.faction)):
        return resp
    form = CardSlotForm(request.POST, instance=slot)
    if not form.is_valid():
        return HttpResponseBadRequest(str(form.errors))
    form.save()
    return render(request, 'the_forge/partials/card_slot_row.html', {
        'slot': slot, 'inline_keywords': _inline_keywords(),
    })


@editor_required
@require_http_methods(["DELETE"])
def cardslot_delete(request, pk):
    slot = get_object_or_404(CardSlot, pk=pk)
    if (resp := _child_permission_check(request, slot.decree.sheet.faction)):
        return resp
    decree = slot.decree
    slot.delete()
    for index, sibling in enumerate(decree.card_slots.order_by('number'), start=1):
        if sibling.number != index:
            CardSlot.objects.filter(pk=sibling.pk).update(number=index)
    return HttpResponse(status=204)


# ---------- CardPile (child of FactionSheet) ----------

@editor_required
@require_http_methods(["POST"])
def cardpile_add(request, sheet_pk):
    sheet = get_object_or_404(FactionSheet, pk=sheet_pk)
    if (resp := _child_permission_check(request, sheet.faction)):
        return resp
    form = CardPileForm(request.POST)
    if not form.is_valid():
        return HttpResponseBadRequest(str(form.errors))
    pile = form.save(commit=False)
    pile.sheet = sheet
    if not pile.number:
        pile.number = sheet.card_piles.count() + 1
    pile.save()
    return render(request, 'the_forge/partials/card_pile_row.html', {
        'pile': pile, 'inline_keywords': _inline_keywords(),
    })


@editor_required
@require_http_methods(["POST"])
def cardpile_edit(request, pk):
    pile = get_object_or_404(CardPile, pk=pk)
    if (resp := _child_permission_check(request, pile.sheet.faction)):
        return resp
    form = CardPileForm(request.POST, instance=pile)
    if not form.is_valid():
        return HttpResponseBadRequest(str(form.errors))
    form.save()
    return render(request, 'the_forge/partials/card_pile_row.html', {
        'pile': pile, 'inline_keywords': _inline_keywords(),
    })


@editor_required
@require_http_methods(["DELETE"])
def cardpile_delete(request, pk):
    pile = get_object_or_404(CardPile, pk=pk)
    if (resp := _child_permission_check(request, pile.sheet.faction)):
        return resp
    sheet = pile.sheet
    pile.delete()
    for index, sibling in enumerate(sheet.card_piles.order_by('number'), start=1):
        if sibling.number != index:
            CardPile.objects.filter(pk=sibling.pk).update(number=index)
    return HttpResponse(status=204)


@editor_required
@require_http_methods(["POST"])
def cardpile_reorder(request, sheet_pk):
    sheet = get_object_or_404(FactionSheet, pk=sheet_pk)
    if (resp := _child_permission_check(request, sheet.faction)):
        return resp
    data = json.loads(request.body)
    for index, pid in enumerate(data.get('order', []), start=1):
        CardPile.objects.filter(id=pid, sheet=sheet).update(number=index)
    return HttpResponse(status=204)


# ---------- PDF download ----------

@login_required
def forgedfaction_pdf(request, pk):
    faction = get_object_or_404(ForgedFaction, pk=pk)
    if (resp := _child_permission_check(request, faction)):
        return resp
    from . import pdf_engine
    generator = getattr(pdf_engine, 'generate_pdf', None)
    if generator is None:
        return HttpResponse(
            "PDF generation is not wired up yet — pdf_engine.generate_pdf() is missing.",
            status=501, content_type='text/plain',
        )
    buffer = generator(faction)
    data = buffer.getvalue() if hasattr(buffer, 'getvalue') else buffer
    response = HttpResponse(data, content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="{faction.faction_name}.pdf"'
    return response


@login_required
def factionsheet_pdf(request, pk):
    sheet = get_object_or_404(FactionSheet, pk=pk)
    if (resp := _child_permission_check(request, sheet.faction)):
        return resp
    from io import BytesIO
    from .pdf_engine import SheetLayoutEngine
    buffer = BytesIO()
    SheetLayoutEngine(sheet).build(buffer)
    response = HttpResponse(buffer.getvalue(), content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="{sheet.faction.faction_name} - Front.pdf"'
    return response


@login_required
def factionback_pdf(request, pk):
    back = get_object_or_404(FactionBack, pk=pk)
    if (resp := _child_permission_check(request, back.faction)):
        return resp
    from io import BytesIO
    from .pdf_engine import FactionBackLayoutEngine
    buffer = BytesIO()
    FactionBackLayoutEngine(back).build(buffer)
    response = HttpResponse(buffer.getvalue(), content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="{back.faction.faction_name} - Back.pdf"'
    return response
