import os
from io import BytesIO

from PIL import Image as PILImage

from .pdf_engine import (
    DECREE_DIR,
    DECREE_SLOT_MIN_GAP,
    DECREE_SLOT_W,
    PAGE_W,
)


DECREE_PREVIEW_DPI = 150
BLACK_OVERLAY_ALPHA = 191  # 75% of 255
PRESET_DIR = os.path.join(
    os.path.dirname(__file__), '..', 'the_keep', 'static', 'pdf', 'background'
)


def _px(pts, px_per_pt):
    return int(round(pts * px_per_pt))


def _paint_background(canvas, faction):
    """Paint the canvas with the faction background, mirroring
    pdf_engine.draw_faction_background."""
    color_hex = (getattr(faction, 'color', None) or '#5B4A8A').lstrip('#')
    if len(color_hex) == 3:
        color_hex = ''.join(ch * 2 for ch in color_hex)
    try:
        r, g, b = (int(color_hex[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        r, g, b = 91, 74, 138
    color_layer = PILImage.new('RGBA', canvas.size, (r, g, b, 255))
    canvas.alpha_composite(color_layer)

    try:
        img_path = faction.get_background_path()
    except Exception:
        img_path = None
    if not img_path or not os.path.exists(img_path):
        return

    try:
        bg = PILImage.open(img_path).convert('RGBA')
    except Exception:
        return

    cw, ch = canvas.size
    iw, ih = bg.size

    if getattr(faction, 'repeat_background_image', False):
        # Tile horizontally with brick-style row offset, matching the engine.
        pct = max(5, min(50, int(getattr(faction, 'background_tile_size', 33) or 33))) / 100.0
        # Engine sizes tiles relative to PAGE_H; the band is shorter than the page,
        # so tiles are correspondingly shorter here. Use canvas height as the
        # local analogue of PAGE_H so proportions still feel right inside the band.
        max_h = ch * pct
        if ih > max_h:
            scale = max_h / ih
            tile_w = max(1, int(round(iw * scale)))
            tile_h = max(1, int(round(max_h)))
        else:
            tile_w, tile_h = iw, ih
        tile = bg.resize((tile_w, tile_h), PILImage.LANCZOS)
        row = 0
        y = ch - tile_h
        while y > -tile_h:
            x_offset = -(tile_w // 2) if row % 2 == 1 else 0
            x = x_offset
            while x < cw:
                canvas.alpha_composite(tile, (x, y))
                x += tile_w
            y -= tile_h
            row += 1
    else:
        scale = max(cw / iw, ch / ih)
        draw_w = max(1, int(round(iw * scale)))
        draw_h = max(1, int(round(ih * scale)))
        scaled = bg.resize((draw_w, draw_h), PILImage.LANCZOS)
        x = (cw - draw_w) // 2
        y = (ch - draw_h) // 2
        canvas.alpha_composite(scaled, (x, y))


def _slot_layout(n):
    """Return (canvas_w, canvas_h, slot_w, slot_h, [slot_left_px, ...], stack_img)
    for a decree band with `n` slots. Shared by the renderer and the snap-point
    generator so both agree on slot centers.

    The native decree-stack-crop.png (565px wide) is too large to fit multiple
    stacks side by side at our render DPI, so we scale it to DECREE_SLOT_W
    (2.5") to match where slots would appear on the PDF.
    """
    stack_path = os.path.join(DECREE_DIR, 'decree-stack-crop.png')
    stack_img = PILImage.open(stack_path).convert('RGBA')
    px_per_pt = DECREE_PREVIEW_DPI / 72.0
    canvas_w = _px(PAGE_W, px_per_pt)

    target_slot_w = _px(DECREE_SLOT_W, px_per_pt)
    scale = target_slot_w / stack_img.width
    target_slot_h = int(round(stack_img.height * scale))
    stack_img = stack_img.resize((target_slot_w, target_slot_h), PILImage.LANCZOS)

    canvas_h = stack_img.height
    slot_w, slot_h = stack_img.size

    if n <= 0:
        return canvas_w, canvas_h, slot_w, slot_h, [], stack_img

    min_gap = max(1, _px(DECREE_SLOT_MIN_GAP, px_per_pt))
    gap = max((canvas_w - n * slot_w) / (n + 1), min_gap)
    total_w = n * slot_w + (n - 1) * gap
    start_x = (canvas_w - total_w) / 2.0
    lefts = [start_x + i * (slot_w + gap) for i in range(n)]
    return canvas_w, canvas_h, slot_w, slot_h, lefts, stack_img


def render_decree_preview(sheet) -> bytes:
    """Render a WebP preview of the decree band: faction background, a 75%
    black wash, and N decree-stack-crop.png images for the configured slot
    count. Returns empty bytes when there are no slots to render."""
    decree = sheet.decrees.first()
    n = decree.card_slots.count() if decree else 0
    if n == 0:
        return b''

    canvas_w, canvas_h, slot_w, slot_h, lefts, stack_img = _slot_layout(n)

    canvas = PILImage.new('RGBA', (canvas_w, canvas_h), (0, 0, 0, 0))
    _paint_background(canvas, sheet.faction)

    overlay = PILImage.new('RGBA', canvas.size, (0, 0, 0, BLACK_OVERLAY_ALPHA))
    canvas.alpha_composite(overlay)

    y = (canvas_h - slot_h) // 2
    for left in lefts:
        canvas.alpha_composite(stack_img, (int(round(left)), y))

    out = BytesIO()
    canvas.save(out, format='WEBP', quality=85, method=6)
    return out.getvalue()


SAMPLE_SNAP_Y = 0.1

# Number of snap-point rows per column.
DECREE_SNAP_ROWS = 6

# Vertical spacing between consecutive snap points, in TTS local-z. Larger
# value spreads the rows further apart on the tile.
DECREE_SNAP_ROW_SPACING_Z = 0.285

# Local-z of the bottom-most snap point in each column. Larger value pushes
# the whole column further toward the front edge of the tile (positive z).
# All other rows are computed by stepping up from this by DECREE_SNAP_ROW_SPACING_Z.
DECREE_SNAP_BOTTOM_LOCAL_Z = 1.25

# Horizontal spread multiplier applied to each column's local-x. 1.0 keeps
# columns centered on each rendered slot stack at the same tile-local-x as
# the faction sheet's snap-point calibration. >1 pushes columns further
# from the tile's center; <1 pulls them toward center.
DECREE_SNAP_X_SPREAD = 1.8


def decree_snap_points(n, decree_scale_x, decree_scale_z):
    """Generate AttachedSnapPoints for a decree tile with `n` slots.

    Horizontal placement reuses the same calibration the faction sheet uses
    (`pdf_to_tts_local`) so columns line up with rendered decree-stack
    centers at the right tile-local spacing. Vertical placement is uniform:
    DECREE_SNAP_ROWS rows stepping up from DECREE_SNAP_BOTTOM_LOCAL_Z by
    DECREE_SNAP_ROW_SPACING_Z each.
    """
    if n <= 0 or not decree_scale_x or not decree_scale_z:
        return []
    from .pdf_engine import pdf_to_tts_local
    _, _, slot_w, _, lefts, _ = _slot_layout(n)
    px_per_pt = DECREE_PREVIEW_DPI / 72.0

    # Build z values from the bottom up. i=0 is the bottom row.
    z_local = sorted(
        DECREE_SNAP_BOTTOM_LOCAL_Z - i * DECREE_SNAP_ROW_SPACING_Z
        for i in range(DECREE_SNAP_ROWS)
    )

    points = []
    for left in lefts:
        slot_cx_px = left + slot_w / 2.0
        slot_cx_pts = slot_cx_px / px_per_pt
        local_x, _ = pdf_to_tts_local(slot_cx_pts, 0)
        local_x *= DECREE_SNAP_X_SPREAD
        for local_z in z_local:
            points.append({
                "Position": {
                    "x": local_x,
                    "y": SAMPLE_SNAP_Y,
                    "z": local_z,
                },
                "Rotation": {"x": 0.0, "y": 0.0, "z": 0.0},
            })
    return points
