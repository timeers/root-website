"""The post's TTS JSON page (admin only): generate the JSON from the post's
data, or upload a file, review its warnings and confirm it. Either way the JSON
is stored exactly as given and served as is (see services/tts_store.py)."""
import json
from datetime import timedelta

from django.contrib import messages
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.core.exceptions import ValidationError

from the_gatehouse.views import admin_required

from .models import Post, TTSJsonUpload, post_model_for_component
from .services.tts_inspect import inspect_tts_json
from .services.tts_store import (clear_tts_json, forge_linked, generate_tts_json,
                                 generate_unavailable_reason, recheck_tts_info, store_tts_json)
from .utils import validate_tts_save

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


@admin_required
def post_tts_json(request, slug):
    post = _post_for(slug)
    profile = request.user.profile
    TTSJsonUpload.objects.filter(confirmed_at__isnull=True,
                                 created_at__lt=timezone.now() - PENDING_UPLOAD_LIFETIME).delete()

    if request.method == 'POST':
        action = request.POST.get('action')
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
        return redirect('post-tts-json', slug=post.slug)

    pretty = ''
    truncated = False
    if post.tts_json:
        pretty = json.dumps(post.tts_json, indent=2, ensure_ascii=False)
        if len(pretty) > MAX_DISPLAY_CHARS:
            pretty, truncated = pretty[:MAX_DISPLAY_CHARS], True
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
            return redirect('post-tts-json', slug=post.slug)
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
                return redirect('post-tts-json', slug=post.slug)

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


@admin_required
def post_tts_json_download(request, slug):
    post = _post_for(slug)
    if not post.tts_json:
        raise Http404("No TTS JSON.")
    response = HttpResponse(json.dumps(post.tts_json, indent=2, ensure_ascii=False),
                            content_type='application/json')
    response['Content-Disposition'] = f'attachment; filename="{post.slug}.json"'
    return response
