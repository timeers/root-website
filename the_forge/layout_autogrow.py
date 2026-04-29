"""Auto-grow saved layout heights when content additions overflow the override.

When a user has saved a manual layout (override `h_*` set) and later adds new
content (steps, actions, bordered boxes, tracks, track columns), the engine
flows the content into the fixed-height box and silently clips the overflow.

This module bumps the override height *just enough* to fit, so the manual
layout (x/y/w) is preserved and only the height grows.

Width is never modified — only height. If no override is set the function is
a no-op (engine handles auto layout already).
"""
from reportlab.lib.units import inch

from .pdf_engine import (
    BODY_W,
    CONTENT_BOX_INTERNAL_MARGIN,
    CONTENT_BOX_PAD_BOTTOM,
    CONTENT_BOX_PAD_TOP,
    PAGE_W,
    PHASE_BOX_PAD_BOTTOM,
    PHASE_BOX_PAD_TOP,
    PHASE_BOX_V_GAP,
    PHASE_INTERNAL_MARGIN,
    SheetLayoutEngine,
    X_MARGIN,
)


PHASE_ORDER = ['birdsong', 'daylight', 'evening']


def _required_phase_box_height(engine, mode, sheet):
    """Min phase-box height (inches) needed to fit all step content for the
    given layout mode, using whatever width override is set or the engine
    default."""
    if mode == 'horizontal':
        # Horizontal: 3 columns sized from BODY_W. Each column needs to fit its
        # phase's step list independently; required = max across phases.
        n = len(PHASE_ORDER)
        col_w = (BODY_W - PHASE_INTERNAL_MARGIN * 2 - PHASE_INTERNAL_MARGIN * (n - 1)) / n
        header_h = engine._banner_height_for_width(col_w)
        max_content_h = max(
            engine.measure_phase_height(engine.phases_grouped.get(pk, []), col_w, header_h=header_h)
            for pk in PHASE_ORDER
        )
        required_pts = max_content_h + PHASE_BOX_PAD_TOP + PHASE_BOX_PAD_BOTTOM
        return required_pts / inch

    # Vertical: width from override (or BODY_W default), 3 stacked phases.
    ov_w = sheet.phase_box_w_v
    box_w = (ov_w * inch) if ov_w is not None else BODY_W
    content_w = box_w - PHASE_INTERNAL_MARGIN * 2
    header_h = engine._banner_height_for_width(content_w)
    n = len(PHASE_ORDER)
    total_content_h = sum(
        engine.measure_phase_height(engine.phases_grouped.get(pk, []), content_w, header_h=header_h)
        for pk in PHASE_ORDER
    )
    required_pts = (
        total_content_h
        + n * 4
        + (n - 1) * PHASE_BOX_V_GAP
        + PHASE_BOX_PAD_TOP
        + PHASE_BOX_PAD_BOTTOM
    )
    return required_pts / inch


def _required_content_box_width(engine, content_box):
    """Min content-box width (inches) needed for the widest track in this box."""
    track_pts = engine._min_track_width_for_content_box(content_box)
    if track_pts <= 0:
        return 0.0
    required_pts = track_pts + CONTENT_BOX_INTERNAL_MARGIN * 2
    return required_pts / inch


def _required_phase_box_width_v(engine):
    """Min phase-box width (inches) for vertical layout to fit the widest phase track."""
    required_pts = engine._preferred_phase_track_width(PHASE_ORDER)
    if required_pts <= 0:
        return 0.0
    return required_pts / inch


def _required_content_box_height(engine, content_box, mode):
    """Min content-box height (inches) needed for the given content box at its
    current width override (or PAGE_W minus margins as a fallback)."""
    ov_w = content_box.w_h if mode == 'horizontal' else content_box.w_v
    if ov_w is not None:
        box_w = ov_w * inch
    else:
        # No width override — caller should not bump in this case, but compute
        # something sensible for safety.
        box_w = PAGE_W - (X_MARGIN * 2)
    content_w = box_w - CONTENT_BOX_INTERNAL_MARGIN * 2
    content_h = engine.measure_content_box_height(content_box, content_w)
    required_pts = content_h + CONTENT_BOX_PAD_TOP + CONTENT_BOX_PAD_BOTTOM
    return required_pts / inch


def ensure_phase_box_fits(sheet, *, check_width=False):
    """Bump phase_box_h_h / phase_box_h_v if below required height. When
    `check_width` is True, also bump phase_box_w_v if a track demands more
    width (vertical layout only — horizontal phase row is engine-controlled).
    Growth is anchored to the top-left: lowering y by the height delta keeps
    the top edge fixed (PDF coords are bottom-left). No-op when the relevant
    overrides are null."""
    engine = None
    changed = False
    for mode in ('horizontal', 'vertical'):
        h_attr = 'phase_box_h_h' if mode == 'horizontal' else 'phase_box_h_v'
        y_attr = 'phase_box_y_h' if mode == 'horizontal' else 'phase_box_y_v'
        current_h = getattr(sheet, h_attr)
        if current_h is None:
            continue
        if engine is None:
            engine = SheetLayoutEngine(sheet)
        required_h = _required_phase_box_height(engine, mode, sheet)
        if required_h > current_h:
            current_y = getattr(sheet, y_attr)
            if current_y is not None:
                setattr(sheet, y_attr, current_y - (required_h - current_h))
            setattr(sheet, h_attr, required_h)
            changed = True
    if check_width and sheet.phase_box_w_v is not None:
        if engine is None:
            engine = SheetLayoutEngine(sheet)
        required_w = _required_phase_box_width_v(engine)
        if required_w > sheet.phase_box_w_v:
            sheet.phase_box_w_v = required_w
            changed = True
    if changed:
        sheet.save()


def ensure_content_box_fits(content_box, *, check_width=False):
    """Bump content_box.h_h / h_v if below required height. When `check_width`
    is True, also bump w_h / w_v if a track demands more width. Height growth
    lowers y by the same delta so the top-left corner stays locked (PDF coords
    are bottom-left). No-op when the relevant overrides are null."""
    engine = None
    changed = False
    for mode in ('horizontal', 'vertical'):
        h_attr = 'h_h' if mode == 'horizontal' else 'h_v'
        w_attr = 'w_h' if mode == 'horizontal' else 'w_v'
        y_attr = 'y_h' if mode == 'horizontal' else 'y_v'
        current_h = getattr(content_box, h_attr)
        current_w = getattr(content_box, w_attr)
        if current_h is None and (not check_width or current_w is None):
            continue
        if engine is None:
            engine = SheetLayoutEngine(content_box.sheet)
        if check_width and current_w is not None:
            required_w = _required_content_box_width(engine, content_box)
            if required_w > current_w:
                setattr(content_box, w_attr, required_w)
                current_w = required_w
                changed = True
        if current_h is not None:
            required_h = _required_content_box_height(engine, content_box, mode)
            if required_h > current_h:
                current_y = getattr(content_box, y_attr)
                if current_y is not None:
                    setattr(content_box, y_attr, current_y - (required_h - current_h))
                setattr(content_box, h_attr, required_h)
                changed = True
    if changed:
        content_box.save()


def ensure_step_parent_fits(step, *, check_width=False):
    """Dispatch to the right parent based on the step's phase.
    Set `check_width=True` when a track gained columns (or a track was added)."""
    if step.phase == 'other':
        if step.content_box_id:
            ensure_content_box_fits(step.content_box, check_width=check_width)
    else:
        ensure_phase_box_fits(step.sheet, check_width=check_width)
