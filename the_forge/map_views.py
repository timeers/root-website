"""Forge maps (forge/map/...): build a Root map from scratch with the map
builder the Keep uses for Map posts (the same template, script and layout code),
then download it as a PDF, a WebP image or TTS JSON.

There's no link to these pages from the Forge yet: designers reach them at
forge/map/."""
import json

from django.contrib import messages
from django.db import transaction
from django.contrib.auth.decorators import login_required
from django.http import FileResponse, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from the_gatehouse.views import forge_onboard_required
from the_keep.models import Post
from the_keep.services.tts_map import check_builder, clean_builder_state

from .models import ForgedFaction, ForgedMap
from .services.forge_map import (ArtRejected, default_state, map_builder_config, map_library_state,
                                 map_name_max, name_locked_note, refresh_map_image, remove_art, save_art,
                                 set_language, store_map_tts)
from .views import _forbid_if_not_editor, user_can_edit_forge, user_can_view_forge


def _forged_map(pk):
    """The Map draft and its ForgedMap (made on first use)."""
    faction = get_object_or_404(ForgedFaction, pk=pk, component=Post.ComponentChoices.MAP)
    forged_map, _ = ForgedMap.objects.get_or_create(faction=faction, defaults={'tts_builder': default_state()})
    forged_map.faction = faction   # one instance, so a stored tts_json shows on both
    return faction, forged_map


def _filename(faction, ext):
    return f'{faction.faction_name} - Map.{ext}'


@forge_onboard_required
def map_list(request):
    maps = (ForgedFaction.objects
            .filter(designer=request.user.profile, component=Post.ComponentChoices.MAP)
            .select_related('forged_map')
            .order_by('-last_updated'))
    return render(request, 'the_forge/map_list.html', {'maps': maps})


class _NotCreated(Exception):
    pass


@forge_onboard_required
def map_create(request):
    """New Map. A visit opens the builder on its Art step with nothing created
    yet; the step's Next posts the map's name and art here, and only then are
    the draft and its map made (so its slug, and with it its media folder,
    comes from its real name)."""
    if request.method != 'POST':
        return render(request, 'the_forge/map_builder.html', {
            'faction': None,
            'config': map_builder_config(None, profile=request.user.profile),
        })

    name = (request.POST.get('name') or '').strip()[:map_name_max()]
    if not name:
        return JsonResponse({'ok': False, 'field': 'name', 'error': 'Name your map.'}, status=400)
    upload = request.FILES.get('art')    # optional: without art, the map is printed on grey
    try:
        state = clean_builder_state(json.loads(request.POST.get('state') or ''), logo=True)
    except (ValueError, TypeError):
        state = default_state()

    try:
        with transaction.atomic():
            faction = ForgedFaction(designer=request.user.profile, faction_name=name,
                                    component=Post.ComponentChoices.MAP)
            set_language(faction, request.POST.get('language'))   # else the default (English)
            faction.save()
            forged_map = ForgedMap.objects.create(faction=faction, tts_builder=state)
            cropped = False
            if upload is not None:
                try:
                    cropped = save_art(forged_map, upload)
                except ArtRejected as err:
                    raise _NotCreated(str(err))
    except _NotCreated as err:
        return JsonResponse({'ok': False, 'field': 'art', 'error': str(err)}, status=400)
    return JsonResponse({
        'ok': True, 'pk': faction.pk, 'name': faction.faction_name,
        'image': f'{forged_map.art.url}?v={forged_map.art_version}' if forged_map.art else None, 'cropped': cropped,
        'save_url': reverse('forge-map-builder', kwargs={'pk': faction.pk}),
        'upload_url': reverse('forge-map-art', kwargs={'pk': faction.pk}),
        'builder_url': reverse('forge-map-builder', kwargs={'pk': faction.pk}),
        'detail_url': reverse('forge-map-detail', kwargs={'pk': faction.pk}),
    })


@forge_onboard_required
def map_detail(request, pk):
    faction, forged_map = _forged_map(pk)
    if not user_can_view_forge(request, faction):
        return redirect('forge-home')
    context = {
        'faction': faction,
        'forged_map': forged_map,
        'can_edit': user_can_edit_forge(request, faction),
        'stale': forged_map.preview_stale,
        'tts_state': map_library_state(faction, forged_map),
        'is_admin': bool(getattr(request.user.profile, 'admin', False)),
    }
    from .publishing.context import publish_context
    context.update(publish_context(request, faction))
    context['config'] = map_builder_config(forged_map, profile=request.user.profile, readonly=True)
    return render(request, 'the_forge/map_detail.html', context)


@forge_onboard_required
def map_builder(request, pk):
    faction, forged_map = _forged_map(pk)
    if (resp := _forbid_if_not_editor(request, faction)):
        return resp
    profile = request.user.profile

    if request.method == 'POST':
        try:
            payload = json.loads(request.body)
            state = clean_builder_state(payload['state'], logo=True)
        except (ValueError, KeyError, TypeError):
            return JsonResponse({'ok': False, 'errors': ["Couldn't read the layout."]}, status=400)
        forged_map.tts_builder = state
        if isinstance(state.get('fixed'), bool):
            forged_map.fixed_clearings = state['fixed']
        forged_map.save(update_fields=['tts_builder', 'fixed_clearings', 'last_updated'])
        errors, warnings = check_builder(forged_map, state, require_buildings=True)
        # The name and language, from the Start step. A map linked to a keep
        # post keeps both: only an admin can rename a published post.
        locked = bool(name_locked_note(faction))
        if not locked and set_language(faction, payload.get('language')):
            ForgedFaction.objects.filter(pk=faction.pk).update(language=faction.language)
        name = None if locked else payload.get('name')
        if isinstance(name, str):
            name = name.strip()[:map_name_max()]
            if not name:
                warnings.append(f'Your map needs a name: kept "{faction.faction_name}".')
            elif name != faction.faction_name:
                faction.faction_name = name
                ForgedFaction.objects.filter(pk=faction.pk).update(faction_name=name)
                # The stored TTS JSON carries the name (the map image doesn't):
                # rebuild it on the current image, so a map in the TTS Library
                # stays listed under its new name. With a layout that doesn't
                # pass the checks yet, the next Complete rebuilds it.
                if faction.tts_json and forged_map.image_preview:
                    store_map_tts(forged_map)
        # No Map post counts to update: the builder compares nothing.
        result = {'ok': True, 'saved': True, 'changes': [], 'errors': errors, 'warnings': warnings, 'counted': [],
                  'name': faction.faction_name}
        if payload.get('generate'):
            if errors:
                result['ok'] = False
                return JsonResponse(result)
            from .render_guard import RenderBusy, render_slot
            try:
                with render_slot():
                    refresh_map_image(forged_map)
            except RenderBusy:
                result.update(ok=False, message="The map generator is busy. Your layout is saved: try Complete again in a moment.")
                return JsonResponse(result, status=503)
            store_map_tts(forged_map)
            if not profile.map_builder_onboard:
                # Their first finished map: the step cards stop opening by themselves.
                type(profile).objects.filter(pk=profile.pk).update(map_builder_onboard=True)
            messages.success(request, "Generated your map.")
            result.update(generated=True, redirect=reverse('forge-map-detail', kwargs={'pk': faction.pk}))
        return JsonResponse(result)

    return render(request, 'the_forge/map_builder.html', {
        'faction': faction,
        'forged_map': forged_map,
        'config': map_builder_config(forged_map, profile=profile),
    })


@login_required
@require_POST
def map_art(request, pk):
    faction, forged_map = _forged_map(pk)
    if not user_can_edit_forge(request, faction):
        return JsonResponse({'ok': False, 'error': "You can't edit this map."}, status=403)
    if request.POST.get('remove') == '1':
        # The art is optional: without it the map is printed on a grey board.
        remove_art(forged_map)
        return JsonResponse({'ok': True, 'image': None, 'cropped': False})
    upload = request.FILES.get('art')
    if upload is None:
        return JsonResponse({'ok': False, 'error': "Choose an image to upload."}, status=400)
    try:
        cropped = save_art(forged_map, upload)
    except ArtRejected as err:
        return JsonResponse({'ok': False, 'error': str(err)}, status=400)
    return JsonResponse({'ok': True, 'image': forged_map.art.url + f'?v={forged_map.art_version}',
                         'width': forged_map.art.width, 'height': forged_map.art.height, 'cropped': cropped})


@login_required
def map_pdf(request, pk):
    faction, forged_map = _forged_map(pk)
    if (resp := _forbid_if_not_editor(request, faction)):
        return resp
    from .map_pdf_engine import MapLayoutEngine
    from .pdf_cache import cache_key, fingerprint_map, render_pdf
    from .render_guard import RenderBusy, busy_503, render_slot
    try:
        with render_slot():
            pdf = render_pdf(MapLayoutEngine, forged_map, cache_key('map', forged_map.pk, fingerprint_map(forged_map)))
    except RenderBusy:
        return busy_503()
    response = HttpResponse(pdf, content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="{_filename(faction, "pdf")}"'
    return response


@login_required
def map_webp(request, pk):
    faction, forged_map = _forged_map(pk)
    if (resp := _forbid_if_not_editor(request, faction)):
        return resp
    from .render_guard import RenderBusy, busy_503, render_slot
    if forged_map.preview_stale:
        try:
            with render_slot():
                refresh_map_image(forged_map)
        except RenderBusy:
            return busy_503()
    response = FileResponse(forged_map.image_preview.open('rb'), content_type='image/webp')
    response['Content-Disposition'] = f'attachment; filename="{_filename(faction, "webp")}"'
    return response


@login_required
def map_tts(request, pk):
    faction, forged_map = _forged_map(pk)
    if (resp := _forbid_if_not_editor(request, faction)):
        return resp
    from .render_guard import RenderBusy, busy_503, render_slot
    # The JSON shows the generated image: bring both up to date first.
    if forged_map.preview_stale or not faction.tts_json:
        try:
            with render_slot():
                refresh_map_image(forged_map)
        except RenderBusy:
            return busy_503()
        ok, errors = store_map_tts(forged_map)
        if not ok:
            return HttpResponse("Finish the map in the builder first: " + ' '.join(errors),
                                status=400, content_type='text/plain')
    response = HttpResponse(json.dumps(faction.tts_json, indent=2, ensure_ascii=False),
                            content_type='application/json')
    response['Content-Disposition'] = f'attachment; filename="{_filename(faction, "json")}"'
    return response
