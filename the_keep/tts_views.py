"""The post's TTS JSON page: anyone who can edit the post can generate the JSON
from the post's data (maps through the map builder); admins can also upload a
file, review its warnings and confirm it. Either way the JSON is stored exactly
as given and served as is (see services/tts_store.py)."""
import json
from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.core.exceptions import ValidationError

from the_gatehouse.views import admin_required

from .models import Post, TTSJsonUpload, post_model_for_component
from .services.tts_inspect import inspect_tts_json
from .services.tts_map import (MapGeometry, apply_builder_counts, check_builder, highest_number,
                               number_tile_radius_px,
                               pieces, TRACK_CELLS, TRACK_ROW_GAP, TRACK_STEP, TRACK_X0)
from .services.tts_store import (clear_tts_json, forge_linked, generate_tts_json,
                                 generate_unavailable_reason, recheck_tts_info, store_tts_json)
from .utils import user_can_edit, validate_tts_save

MAX_UPLOAD_BYTES = 20 * 1024 * 1024
# Above this the page shows the start of the JSON and points to Download.
MAX_DISPLAY_CHARS = 1024 * 1024
PENDING_UPLOAD_LIFETIME = timedelta(days=1)


def _post_for(slug):
    """The post as its own subclass (a Map's fixed_clearings, a builder's fields)."""
    post = get_object_or_404(Post, slug=slug)
    model = post_model_for_component(post.component)
    return get_object_or_404(model, pk=post.pk) if model else post


def tts_status(post):
    """A short description of where the post's TTS JSON came from, for the
    settings hub card and the page's source badge."""
    if not post.tts_json:
        return {'source': None, 'label': 'No TTS JSON', 'detail': ''}
    info = post.tts_info or {}
    source = info.get('source')
    labels = {'generated': 'Generated', 'uploaded': 'Uploaded', 'forge': 'Synced from the Forge'}
    parts = []
    if source == 'uploaded' and info.get('filename'):
        parts.append(info['filename'])
    if info.get('updated_by'):
        parts.append(f"by {info['updated_by']}")
    updated = _parse_time(info.get('updated_at'))
    if updated:
        parts.append(f"on {timezone.localtime(updated):%b %-d, %Y}")
    return {
        'source': source or 'unknown',
        'label': labels.get(source, 'Unknown source'),
        'detail': ' '.join(parts),
        'object_count': len(post.tts_json.get('ObjectStates') or []),
    }


def _parse_time(value):
    try:
        return timezone.datetime.fromisoformat(value) if value else None
    except (TypeError, ValueError):
        return None


def _pretty_json(post):
    """(pretty-printed JSON, truncated) for the JSON window partial."""
    if not post.tts_json:
        return '', False
    pretty = json.dumps(post.tts_json, indent=2, ensure_ascii=False)
    if len(pretty) > MAX_DISPLAY_CHARS:
        return pretty[:MAX_DISPLAY_CHARS], True
    return pretty, False


def can_view_tts_json(request, post):
    """Anyone may view the JSON of a post the TTS Library lists (its public API
    serves exactly that JSON); otherwise only people who can edit the post."""
    from .api_views import TTS_LISTED_STATUSES
    if not post.tts_json:
        return False
    return post.status in TTS_LISTED_STATUSES or user_can_edit(request, post)


def _needs_acknowledgement(report):
    return bool(report['untrusted_hosts']) or any(not s['known'] for s in report['scripts'])


def _reject_constant(value):
    raise ValueError(f"{value} isn't allowed in JSON")


def _read_upload(upload):
    """(tts_json, error) from an uploaded file."""
    if upload is None:
        return None, "Choose a JSON file to upload."
    if upload.size > MAX_UPLOAD_BYTES:
        return None, f"The file is larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB."
    try:
        text = upload.read().decode('utf-8-sig')
    except UnicodeDecodeError:
        return None, "The file isn't UTF-8 text."
    try:
        # NaN / Infinity: Python accepts them, Postgres jsonb can't store them.
        data = json.loads(text, parse_constant=_reject_constant)
    except ValueError as exc:
        return None, f"The file isn't valid JSON: {exc}"
    try:
        validate_tts_save(data)
    except ValidationError as exc:
        return None, ' '.join(exc.messages)
    return data, None


# Actions on the manage page only admins may take.
ADMIN_ACTIONS = {'upload', 'remove', 'recheck'}


def _require_editor(request, post):
    if not user_can_edit(request, post):
        raise PermissionDenied


@login_required
def post_tts_json(request, slug):
    post = _post_for(slug)
    _require_editor(request, post)
    profile = request.user.profile
    is_admin = profile.admin
    TTSJsonUpload.objects.filter(confirmed_at__isnull=True,
                                 created_at__lt=timezone.now() - PENDING_UPLOAD_LIFETIME).delete()

    if request.method == 'POST':
        action = request.POST.get('action')
        if action in ADMIN_ACTIONS and not is_admin:
            raise PermissionDenied
        if action == 'generate':
            ok, message = generate_tts_json(post, profile=profile)
            (messages.success if ok else messages.error)(request, message)
        elif action == 'remove' and post.tts_json:
            clear_tts_json(post)
            messages.success(request, "Removed the TTS JSON. This post is no longer in the TTS Library.")
        elif action == 'recheck' and post.tts_json:
            recheck_tts_info(post)
            messages.success(request, "Rechecked the stored TTS JSON.")
        elif action == 'upload':
            upload = request.FILES.get('tts_file')
            data, error = _read_upload(upload)
            if error:
                messages.error(request, error)
            else:
                pending = TTSJsonUpload.objects.create(
                    post=post, filename=upload.name[:255], tts_json=data, uploaded_by=profile)
                return redirect('post-tts-json-review', slug=post.slug, upload_id=pending.pk)
        return redirect('manage-tts-json', slug=post.slug)

    pretty, truncated = _pretty_json(post)
    forged = forge_linked(post)
    status = tts_status(post)
    generate_confirm = ''
    if post.tts_json:
        current = f"{status['label']} {status['detail']}".strip()
        generate_confirm = f"This replaces the current JSON ({current}). Continue?"
    return render(request, 'the_keep/post_tts_json.html', {
        'object': post,
        'status': status,
        'generate_confirm': generate_confirm,
        'pretty_json': pretty,
        'truncated': truncated,
        'generate_reason': generate_unavailable_reason(post),
        'forged_faction': forged,
        'max_upload_mb': MAX_UPLOAD_BYTES // (1024 * 1024),
        'is_admin': is_admin,
        'is_map': post.component == Post.ComponentChoices.MAP,
    })


@admin_required
def post_tts_json_review(request, slug, upload_id):
    post = _post_for(slug)
    pending = TTSJsonUpload.objects.filter(pk=upload_id, post=post, confirmed_at__isnull=True).first()
    if pending is None:
        raise Http404("No pending upload.")
    report = inspect_tts_json(post, pending.tts_json,
                              fixed_clearings=bool(getattr(post, 'fixed_clearings', False)),
                              forge_linked=bool(forge_linked(post)))
    needs_ack = _needs_acknowledgement(report)

    if request.method == 'POST':
        if request.POST.get('action') == 'cancel':
            pending.delete()
            messages.info(request, "Upload cancelled. The current TTS JSON is unchanged.")
            return redirect('manage-tts-json', slug=post.slug)
        if request.POST.get('action') == 'confirm':
            if needs_ack and request.POST.get('acknowledge') != 'on':
                messages.error(request, "Tick the box to confirm you've reviewed the scripts and external files.")
            else:
                store_tts_json(post, pending.tts_json, source='uploaded',
                               profile=request.user.profile, filename=pending.filename)
                pending.confirmed_at = timezone.now()
                pending.warnings = report
                pending.save(update_fields=['confirmed_at', 'warnings'])
                messages.success(request, "Saved the uploaded TTS JSON. It's served exactly as uploaded.")
                return redirect('manage-tts-json', slug=post.slug)

    new_states = pending.tts_json.get('ObjectStates') or []
    return render(request, 'the_keep/post_tts_json_review.html', {
        'object': post,
        'pending': pending,
        'report': report,
        'needs_ack': needs_ack,
        'new_count': len(new_states),
        'new_size_kb': round(len(json.dumps(pending.tts_json)) / 1024),
        'status': tts_status(post),
    })


def post_tts_json_view(request, slug):
    """Read-only view of the stored JSON (see can_view_tts_json)."""
    post = _post_for(slug)
    if not can_view_tts_json(request, post):
        raise Http404("No TTS JSON.")
    pretty, truncated = _pretty_json(post)
    return render(request, 'the_keep/post_tts_json_view.html', {
        'object': post,
        'status': tts_status(post),
        'pretty_json': pretty,
        'truncated': truncated,
    })


def post_tts_json_download(request, slug):
    post = _post_for(slug)
    if not can_view_tts_json(request, post):
        raise Http404("No TTS JSON.")
    response = HttpResponse(json.dumps(post.tts_json, indent=2, ensure_ascii=False),
                            content_type='application/json')
    response['Content-Disposition'] = f'attachment; filename="{post.slug}.json"'
    return response


@login_required
def map_builder(request, slug):
    """The visual map builder: lay out a Map post's VP track, items, clearings,
    forests and buildings on its board image, then generate its TTS JSON."""
    post = _post_for(slug)
    _require_editor(request, post)
    if post.component != Post.ComponentChoices.MAP:
        raise Http404("The map builder is for maps.")

    if request.method == 'POST':
        if not post.board_image:
            return JsonResponse({'ok': False, 'errors': ["Add a board image to this map first."]}, status=400)
        try:
            payload = json.loads(request.body)
            state = payload['state']
        except (ValueError, KeyError, TypeError):
            return JsonResponse({'ok': False, 'errors': ["Couldn't read the layout."]}, status=400)
        state = _clean_state(state)
        post.tts_builder = state
        type(post).objects.filter(pk=post.pk).update(tts_builder=state)
        changes = apply_builder_counts(post, state)
        errors, warnings = check_builder(post, state)
        result = {'ok': True, 'saved': True, 'changes': changes, 'errors': errors, 'warnings': warnings}
        if payload.get('generate'):
            if errors:
                result['ok'] = False
            else:
                ok, message = generate_tts_json(post, profile=request.user.profile)
                result.update(ok=ok, generated=ok, message=message)
                if ok:
                    messages.success(request, "Generated the map's TTS JSON.")
                    result['redirect'] = reverse_manage(post)
        return JsonResponse(result)

    context = {'object': post, 'has_image': bool(post.board_image)}
    if post.board_image:
        width, height = post.board_image.width, post.board_image.height
        geometry = MapGeometry(width, height)
        state = post.tts_builder or geometry.defaults()
        errors, warnings = check_builder(post, state)
        grid = pieces()['item_grid']
        ratio = width / height
        context.update({
            'config': {
                'image': post.board_image.url,
                'width': width,
                'height': height,
                'state': state,
                'fixed': bool(post.fixed_clearings),
                'expected': {'clearings': post.clearings, 'forests': post.forests,
                             'buildings': post.building_slots, 'ruins': post.ruins},
                'numbers': highest_number(),
                # Geometry in pixels, so the editor draws pieces where TTS will put them.
                'px_per_local': geometry.px_per_local,
                'track': {'cells': TRACK_CELLS, 'x0': TRACK_X0, 'step': TRACK_STEP, 'row_gap': TRACK_ROW_GAP,
                          'half_width': geometry.hx, 'half_height': geometry.hz},
                'item_grid': grid,
                'number_radius_px': number_tile_radius_px(geometry),
                'save_url': request.path,
                'manage_url': reverse_manage(post),
            },
            'ratio_warning': not (1.0 <= ratio <= 1.2),
            'errors': errors,
            'warnings': warnings,
        })
    return render(request, 'the_keep/map_builder.html', context)


def reverse_manage(post):
    from django.urls import reverse
    return reverse('manage-tts-json', kwargs={'slug': post.slug})


def _point(value):
    """A [u, v] pair clamped to the image, or None."""
    try:
        u, v = float(value[0]), float(value[1])
    except (TypeError, ValueError, IndexError):
        return None
    if u != u or v != v:   # NaN
        return None
    return [min(max(u, 0.0), 1.0), min(max(v, 0.0), 1.0)]


def _clean_state(state):
    """Keep only the builder's own keys, with numbers where numbers belong."""
    state = state if isinstance(state, dict) else {}
    clean = {'version': 1}
    try:
        clean['track_y'] = min(max(float(state.get('track_y')), 0.0), 1.0)
    except (TypeError, ValueError):
        pass
    items = _point(state.get('items'))
    if items:
        clean['items'] = items
    clearings = []
    for c in state.get('clearings') or []:
        if not isinstance(c, dict):
            continue
        centre = _point(c.get('centre'))
        if centre is None:
            continue
        entry = {'centre': centre, 'marker': _point(c.get('marker'))}
        try:
            entry['number'] = int(c.get('number'))
        except (TypeError, ValueError):
            entry['number'] = None
        try:
            entry['number_angle'] = float(c.get('number_angle'))
        except (TypeError, ValueError):
            pass
        if c.get('suit') in ('fox', 'rabbit', 'mouse', 'bird'):
            entry['suit'] = c['suit']
        clearings.append(entry)
    clean['clearings'] = clearings
    clean['forests'] = [p for p in (_point(f) for f in state.get('forests') or []) if p]
    clean['buildings'] = [{'at': p, 'ruin': bool(b.get('ruin'))}
                          for b in state.get('buildings') or [] if isinstance(b, dict)
                          for p in [_point(b.get('at'))] if p]
    return clean
