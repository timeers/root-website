"""Cache for SheetLayoutEngine.compute_layout() payloads.

The engine traverses the full sheet tree and runs the same heavy math used to
render the PDF. Preview pages don't need that work to repeat when nothing has
changed, so we fingerprint the inputs and memoize the JSON-serializable
payload in Django's default cache.

Cache keys are scoped by layout_mode, so the horizontal and vertical payloads
for a single sheet live in two independent cache entries.
"""
import hashlib
import json

from django.core.cache import cache

CACHE_PREFIX = 'forge:sheet_layout:v9'
CACHE_TIMEOUT = 60 * 60 * 24  # 24h; key is content-addressed so stale entries can't be wrong


def _fingerprint(sheet, layout_mode):
    """Build a stable fingerprint of every field that influences layout for the
    given layout_mode."""
    parts = [('mode', layout_mode)]

    parts.append(('sheet', sheet.pk,
                  sheet.include_crafted_items, sheet.include_decree,
                  sheet.flavor_text or '',
                  sheet.phase_box_x_h, sheet.phase_box_y_h, sheet.phase_box_w_h, sheet.phase_box_h_h,
                  sheet.phase_box_x_v, sheet.phase_box_y_v, sheet.phase_box_w_v, sheet.phase_box_h_v,
                  sheet.decree_y_h, sheet.decree_y_v))

    f = sheet.faction
    parts.append(('faction', f.pk, f.faction_name or '', f.color or ''))

    for a in sheet.abilities.order_by('order').values_list('id', 'order', 'title', 'body'):
        parts.append(('ability',) + a)

    for cb in sheet.content_boxes.order_by('order').values_list(
            'id', 'order', 'title', 'text',
            'x_h', 'y_h', 'w_h', 'h_h', 'x_v', 'y_v', 'w_v', 'h_v'):
        parts.append(('content_box',) + cb)

    for ps in sheet.phase_steps.order_by('phase', 'number').values_list(
            'id', 'phase', 'number', 'text', 'action_type', 'content_box_id'):
        parts.append(('phase_step',) + ps)

    from .models import StepAction, BorderedBox, CardboardTrack, CardboardSlot, CardSlot, Legend, LegendRow, Scale, ScaleRow

    for sa in StepAction.objects.filter(step__sheet=sheet).order_by('step_id', 'order').values_list(
            'id', 'step_id', 'order', 'text', 'cost'):
        parts.append(('step_action',) + sa)

    for bb in BorderedBox.objects.filter(step__sheet=sheet).order_by('step_id', 'order').values_list(
            'id', 'step_id', 'order', 'title', 'body', 'height'):
        parts.append(('bordered_box',) + bb)

    for tr in CardboardTrack.objects.filter(step__sheet=sheet).order_by('step_id', 'order').values_list(
            'id', 'step_id', 'order', 'title', 'body', 'type',
            'num_rows', 'num_columns', 'column_headers', 'row_titles', 'column_dividers',
            'header_position', 'header_title', 'row_title_orientation'):
        parts.append(('track',) + tr)

    for sl in CardboardSlot.objects.filter(track__step__sheet=sheet).order_by(
            'track_id', 'row', 'column').values_list(
            'id', 'track_id', 'row', 'column', 'number', 'content'):
        parts.append(('slot',) + sl)

    for lg in Legend.objects.filter(step__sheet=sheet).order_by('step_id', 'order').values_list(
            'id', 'step_id', 'order', 'title'):
        parts.append(('legend',) + lg)

    for lr in LegendRow.objects.filter(legend__step__sheet=sheet).order_by(
            'legend_id', 'order').values_list(
            'id', 'legend_id', 'order', 'title', 'image', 'body'):
        parts.append(('legend_row',) + lr)

    for sc in Scale.objects.filter(step__sheet=sheet).order_by('step_id', 'order').values_list(
            'id', 'step_id', 'order', 'title'):
        parts.append(('scale',) + sc)

    for sr in ScaleRow.objects.filter(scale__step__sheet=sheet).order_by(
            'scale_id', 'order').values_list(
            'id', 'scale_id', 'order', 'range', 'result'):
        parts.append(('scale_row',) + sr)

    for cp in sheet.card_piles.order_by('number').values_list(
            'id', 'number', 'title', 'body', 'x_h', 'y_h', 'x_v', 'y_v'):
        parts.append(('card_pile',) + cp)

    for d in sheet.decrees.values_list('id', 'title', 'body'):
        parts.append(('decree',) + d)

    for cs in CardSlot.objects.filter(decree__sheet=sheet).order_by(
            'decree_id', 'number').values_list('id', 'decree_id', 'number', 'title', 'body'):
        parts.append(('card_slot',) + cs)

    raw = json.dumps(parts, default=str, sort_keys=False).encode('utf-8')
    return hashlib.sha1(raw).hexdigest()


def cache_key(sheet, layout_mode):
    return f'{CACHE_PREFIX}:{sheet.pk}:{layout_mode}:{_fingerprint(sheet, layout_mode)}'


def _is_fully_overridden(sheet, layout_mode):
    """All editable elements have non-null override coords for the given layout."""
    s = layout_mode[0]  # 'h' or 'v'
    if any(getattr(sheet, f'phase_box_{k}_{s}') is None for k in ('x', 'y', 'w', 'h')):
        return False
    if sheet.include_decree and getattr(sheet, f'decree_y_{s}') is None:
        return False
    for cb in sheet.content_boxes.all():
        if any(getattr(cb, f'{k}_{s}') is None for k in ('x', 'y', 'w', 'h')):
            return False
    for cp in sheet.card_piles.all():
        if any(getattr(cp, f'{k}_{s}') is None for k in ('x', 'y')):
            return False
    return True


def _fast_path_payload(sheet, layout_mode):
    """Build a layout payload directly from the override fields, bypassing the
    engine entirely. Only includes editable elements (phase box, content boxes,
    card piles, decree) — decorative elements like phase headers and phase
    steps require the engine. Returns None if the sheet isn't fully overridden.
    """
    if not _is_fully_overridden(sheet, layout_mode):
        return None
    from .pdf_engine import PAGE_W, PAGE_H, CARD_SLOT_W, CARD_SLOT_H, DECREE_MIN_OFFSET, DECREE_MAX_OFFSET
    from reportlab.lib.units import inch
    s = layout_mode[0]
    elements = []
    elements.append({
        'kind': 'phase_box',
        'x': getattr(sheet, f'phase_box_x_{s}'),
        'y': getattr(sheet, f'phase_box_y_{s}'),
        'w': getattr(sheet, f'phase_box_w_{s}'),
        'h': getattr(sheet, f'phase_box_h_{s}'),
    })
    for cb in sheet.content_boxes.all():
        elements.append({
            'kind': 'content_box', 'id': cb.id, 'title': cb.title or '',
            'x': getattr(cb, f'x_{s}'),
            'y': getattr(cb, f'y_{s}'),
            'w': getattr(cb, f'w_{s}'),
            'h': getattr(cb, f'h_{s}'),
        })
    piles = list(sheet.card_piles.all())
    if piles:
        from .pdf_engine import SheetLayoutEngine
        _eng = SheetLayoutEngine(sheet)  # built lazily so the no-pile path stays cheap
    else:
        _eng = None
    for cp in piles:
        elements.append({
            'kind': 'card_pile', 'id': cp.id, 'number': cp.number, 'title': cp.title or '',
            'x': getattr(cp, f'x_{s}'),
            'y': getattr(cp, f'y_{s}'),
            'w': CARD_SLOT_W / inch,
            'h': CARD_SLOT_H / inch,
            'y_min': _eng._card_pile_min_y(cp) / inch,
        })
    if sheet.include_decree:
        elements.append({
            'kind': 'decree',
            'x': 0, 'y': getattr(sheet, f'decree_y_{s}'),
            'w': PAGE_W / inch, 'h': 0,  # height comes from the decree image; engine fills it
            'y_min': (PAGE_H - DECREE_MAX_OFFSET) / inch,
            'y_max': (PAGE_H - DECREE_MIN_OFFSET) / inch,
        })
    return {
        'page': {'w': PAGE_W / inch, 'h': PAGE_H / inch},
        'layout_mode': layout_mode,
        'elements': elements,
        'fast_path': True,
    }


def get_or_compute_layout(sheet, compute_fn, layout_mode=None, allow_fast_path=False):
    """Return a cached layout payload, computing it via compute_fn() on miss.

    If allow_fast_path=True and the sheet is fully overridden for the given
    layout_mode, returns a DB-only payload with just the editable elements
    (no decorative phase headers, steps, or card slots).
    """
    mode = layout_mode or sheet.layout_mode
    if allow_fast_path:
        fp = _fast_path_payload(sheet, mode)
        if fp is not None:
            return fp
    key = cache_key(sheet, mode)
    payload = cache.get(key)
    if payload is None:
        payload = compute_fn()
        cache.set(key, payload, CACHE_TIMEOUT)
    return payload
