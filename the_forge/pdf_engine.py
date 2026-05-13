# pdf_engine.py

import hashlib
import logging
import math
import os
import re
import tempfile

# svglib emits "Unable to find a suitable font for 'font-family:Luminari'..."
# warnings every time it walks an SVG with a custom font. We register Luminari
# at the ReportLab level so the actual rendering is correct; the warning is
# noise, so silence svglib's own logger.
logging.getLogger('svglib.svglib').setLevel(logging.ERROR)
from html.parser import HTMLParser
from reportlab.lib.pagesizes import landscape, letter
from reportlab.lib.colors import HexColor
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, Frame, Image, Flowable
from reportlab.pdfgen import canvas as rl_canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.pdfmetrics import getFont
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.graphics import renderPDF
from svglib.svglib import svg2rlg
from itertools import groupby
from django.templatetags.static import static

FONT_DIR = os.path.join(os.path.dirname(__file__), '..', 'the_keep', 'static', 'fonts')
pdfmetrics.registerFont(TTFont('Luminari', os.path.join(FONT_DIR, 'Luminari_edit.ttf')))
pdfmetrics.registerFont(TTFont('Baskerville', os.path.join(FONT_DIR, 'Baskerville10Pro.ttf')))
pdfmetrics.registerFont(TTFont('Baskerville-Bold', os.path.join(FONT_DIR, 'Baskerville10Pro-Bold.ttf')))
pdfmetrics.registerFont(TTFont('Baskerville-Italic', os.path.join(FONT_DIR, 'Baskerville10Pro-Italic.ttf')))
pdfmetrics.registerFont(TTFont('Baskerville-BoldItalic', os.path.join(FONT_DIR, 'Baskerville10Pro-BoldItalic.ttf')))
pdfmetrics.registerFontFamily('Baskerville', normal='Baskerville', bold='Baskerville-Bold', italic='Baskerville-Italic', boldItalic='Baskerville-BoldItalic')

PAGE_W, PAGE_H = landscape(letter)  # 792 x 612 pts

# TTS snap-point calibration. Maps PDF absolute coordinates (pts) to
# Tabletop Simulator Custom_Tile local coordinates. Calibrated by hand
# against a reference faction; tweak if the printed image's extent on
# the rendered tile changes (e.g. tile transform / WidthScale changes).
TTS_SNAP_SCALE = 2.59


def pdf_to_tts_local(abs_x_pts, abs_y_pts):
    """Convert absolute PDF coordinates (pts) to TTS Custom_Tile local (x, z).

    Tile uses Transform.rotY = 180, so local-x is mirrored relative to the
    printed image; local-z follows PDF y directly.
    """
    fx = abs_x_pts / PAGE_W - 0.5
    fz_unit = 0.5 - abs_y_pts / PAGE_H
    aspect_z = PAGE_H / PAGE_W
    return -fx * TTS_SNAP_SCALE, fz_unit * aspect_z * TTS_SNAP_SCALE


def pdf_y_delta_to_tts_z_delta(delta_pts):
    """Convert a vertical PDF offset (pts, positive = downward on the printed
    page) into the matching TTS local-z delta (positive = downward on the
    printed face)."""
    aspect_z = PAGE_H / PAGE_W
    return (delta_pts / PAGE_H) * aspect_z * TTS_SNAP_SCALE


def pdf_bytes_to_webp_bytes(pdf_bytes, dpi=150, quality=85):
    import fitz  # PyMuPDF
    from io import BytesIO
    from PIL import Image as PILImage
    doc = fitz.open(stream=pdf_bytes, filetype='pdf')
    try:
        page = doc.load_page(0)
        zoom = dpi / 72.0
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
        try:
            img = PILImage.frombytes('RGB', (pix.width, pix.height), pix.samples)
        finally:
            pix = None  # release native pixmap memory before encoding
        try:
            out = BytesIO()
            img.save(out, format='WEBP', quality=quality, method=4)
            return out.getvalue()
        finally:
            img.close()
    finally:
        doc.close()


class _NoOpCanvas:
    """A duck-typed Canvas replacement that discards drawing calls.

    Used by SheetLayoutEngine.compute_layout() to skip painting/rasterizing
    while still letting placement math and Paragraph.wrap traverse the document
    tree. Flowable.drawOn is short-circuited via _SKIP_FLOWABLE_DRAW below so
    flowables never attempt to write to this canvas.
    """
    _is_noop = True

    def __init__(self, *args, **kwargs):
        pass

    def __getattr__(self, name):
        def _noop(*a, **kw):
            return self
        return _noop

    def saveState(self): return None
    def restoreState(self): return None
    def setPageSize(self, *a, **kw): return None
    def showPage(self): return None
    def save(self): return None
    def getPageNumber(self): return 1


class LayerFilterCanvas:
    """A canvas proxy that forwards drawing primitives only when the current
    layer tag is in the active layer set. Used to render the same draw pipeline
    multiple times onto different pages of one PDF, with each page showing only
    a subset of the content (background / boxes / foreground).

    State methods (saveState, transforms, color/font setters) always forward —
    they are cheap and harmless on filtered draws. Drawing primitives forward
    only when self._current_tag is in self._active_layers.

    Callers tag intent by calling c.set_layer_tag('background' | 'box' | 'fg')
    immediately before the draws they want grouped. Default tag is 'fg'.
    """
    _is_layer_filter = True

    _DRAW_METHODS = frozenset({
        'drawImage', 'drawInlineImage', 'rect', 'roundRect',
        'drawString', 'drawCentredString', 'drawCenteredString',
        'drawRightString', 'drawText', 'line', 'lines',
        'bezier', 'circle', 'ellipse', 'wedge', 'arc', 'drawPath',
    })

    def __init__(self, real_canvas):
        self._real = real_canvas
        self._active_layers = frozenset({'background', 'box', 'fg'})
        self._current_tag = 'fg'

    def set_layer_tag(self, tag):
        self._current_tag = tag

    def _is_active(self):
        return self._current_tag in self._active_layers

    def __getattr__(self, name):
        attr = getattr(self._real, name)
        if name in self._DRAW_METHODS and callable(attr):
            def _gated(*args, **kwargs):
                if self._is_active():
                    return attr(*args, **kwargs)
                return None
            return _gated
        return attr


# Patch Flowable.drawOn so it skips painting when handed a _NoOpCanvas, or when
# handed a LayerFilterCanvas whose active layer set excludes 'fg' (Paragraphs
# and other Flowables always belong on the foreground layer). In both cases
# Frame.addFromList still runs wrap() so measurement stays consistent; only the
# actual draw step is skipped.
_orig_drawOn = Flowable.drawOn

def _drawOn_skip_on_noop(self, canvas, x, y, _sW=0):
    if getattr(canvas, '_is_noop', False):
        return None
    if getattr(canvas, '_is_layer_filter', False):
        if 'fg' not in canvas._active_layers:
            return None
        return _orig_drawOn(self, canvas._real, x, y, _sW=_sW)
    return _orig_drawOn(self, canvas, x, y, _sW=_sW)

Flowable.drawOn = _drawOn_skip_on_noop


# Add a no-op set_layer_tag to the real ReportLab Canvas so draw functions can
# call c.set_layer_tag(...) unconditionally without an attribute check. The
# LayerFilterCanvas overrides this with a real implementation.
def _canvas_set_layer_tag_noop(self, tag):
    return None

rl_canvas.Canvas.set_layer_tag = _canvas_set_layer_tag_noop


_TILE_DPI = 150  # px-per-inch the resized tile is rasterized at (150/72 ≈ 2.08 px/pt)


def _prepare_tile_image(src_path, draw_w_pt, draw_h_pt):
    """Resize src once to the target tile pixel size at _TILE_DPI and cache by
    content hash + dims. ReportLab embeds an image asset once per file path and
    references it elsewhere, so reusing a single small file across many drawImage
    calls keeps the PDF small even when tiling densely."""
    from PIL import Image as PILImage
    target_px_w = max(1, int(round(draw_w_pt * _TILE_DPI / 72)))
    target_px_h = max(1, int(round(draw_h_pt * _TILE_DPI / 72)))
    with open(src_path, 'rb') as f:
        digest = hashlib.md5(f.read()).hexdigest()[:12]
    cache_dir = os.path.join(tempfile.gettempdir(), 'forge_tiles')
    os.makedirs(cache_dir, exist_ok=True)
    out_path = os.path.join(cache_dir, f'{digest}_{target_px_w}x{target_px_h}.png')
    if not os.path.exists(out_path):
        with PILImage.open(src_path) as im:
            mode = 'RGBA' if im.mode in ('P', 'LA', 'RGBA') else 'RGB'
            im = im.convert(mode)
            im.thumbnail((target_px_w, target_px_h), PILImage.LANCZOS)
            im.save(out_path, optimize=True)
    return out_path


def draw_faction_background(c, faction):
    """Fill the page with the faction's background.

    Always paint the faction color first so any translucent areas of an
    overlaid image let the brand color show through.

    - Image + repeat_background_image  -> brick-tiled pattern over color fill.
    - Image + not repeating            -> cover-fill image over color fill.
    - No image                         -> just the color fill.
    """
    color_hex = getattr(faction, 'color', None) or '#5B4A8A'
    c.set_layer_tag('background')
    c.saveState()
    c.setFillColor(HexColor(color_hex))
    c.rect(0, 0, PAGE_W, PAGE_H, stroke=0, fill=1)
    c.restoreState()

    try:
        img_path = faction.get_background_path()
    except Exception:
        img_path = None

    if not img_path or not os.path.exists(img_path):
        return

    from reportlab.lib.utils import ImageReader
    img_reader = ImageReader(img_path)
    iw, ih = img_reader.getSize()

    if getattr(faction, 'repeat_background_image', False):
        pct = max(5, min(50, int(getattr(faction, 'background_tile_size', 33) or 33))) / 100.0
        max_h = PAGE_H * pct
        if ih > max_h:
            scale = max_h / ih
            draw_w = iw * scale
            draw_h = max_h
        else:
            draw_w = iw
            draw_h = ih
        try:
            tile_path = _prepare_tile_image(img_path, draw_w, draw_h)
        except Exception:
            tile_path = img_path
        row = 0
        y = PAGE_H - draw_h
        while y > -draw_h:
            x_offset = -(draw_w / 2) if row % 2 == 1 else 0
            x = x_offset
            while x < PAGE_W:
                c.drawImage(tile_path, x, y, width=draw_w, height=draw_h, mask='auto')
                x += draw_w
            y -= draw_h
            row += 1
    else:
        scale = max(PAGE_W / iw, PAGE_H / ih)
        draw_w = iw * scale
        draw_h = ih * scale
        draw_x = (PAGE_W - draw_w) / 2
        draw_y = (PAGE_H - draw_h) / 2
        c.drawImage(img_path, draw_x, draw_y, width=draw_w, height=draw_h, mask='auto')


X_MARGIN = 0.25 * inch
TOP_MARGIN = 0.2 * inch
BOTTOM_MARGIN = 0.15 * inch

# Bottom-right credits (pnp_version, art_by) padding from page edges.
CREDITS_PAD_RIGHT = 0.6 * inch
CREDITS_PAD_BOTTOM = 0.12 * inch
CREDITS_FONT_SIZE = 8
CREDITS_INTER_GAP = 0.18 * inch  # horizontal gap between art_by and pnp_version

# Forge logo (bottom-right) sizing/padding. Drawn last on top of everything,
# tinted with the faction color, then overlaid with the opposite of
# title_text_color. Both passes are drawn at low opacity so the logo reads
# as a soft watermark instead of an opaque stamp.
FORGE_LOGO_W = 0.174 * inch
FORGE_LOGO_PAD_X = 0.12 * inch
FORGE_LOGO_PAD_BOTTOM = 0.08 * inch
FORGE_LOGO_BASE_OPACITY = 0.35     # alpha for the faction-color pass
FORGE_LOGO_OVERLAY_OPACITY = 0.2  # alpha for the opposite-color overlay

BODY_W = PAGE_W - (X_MARGIN * 2)

TITLE_BAR_H = 0.6 * inch
ABILITY_BAR_H = 0.9 * inch
ABILITY_GAP = 0.05 * inch
MIN_ABILITY_BOX_W = 1.3 * inch
MIN_FLAVOR_TEXT_W = 0.90 * inch
FLAVOR_TEXT_PADDING = 0.05 * inch
FLAVOR_TEXT_BASE_SIZE = 6
FLAVOR_TEXT_MAX_SIZE = 11

ABILITY_BODY_MAX_SIZE = 11
ABILITY_BODY_MIN_SIZE = 6
ABILITY_BODY_HARD_FLOOR = 5
ABILITY_TITLE_MAX_SIZE = 14
ABILITY_TITLE_MIN_SIZE = 8
ABILITY_OUTLIER_RATIO = 1.1
ABILITY_BAR_MAX_H = 1.5 * inch
ABILITY_BAR_MIN_PAD = 0.06 * inch
ABILITY_BAR_BOTTOM_EXTRA = 0.08 * inch  # extra usable height extending below box_y, no impact on top of bar or downstream elements

COLOR_BAR_W_RATIO = 0.95 # Controls width of content inside top bar in relation to top bar border
FACTION_TOP_BAR_NUDGE = 0.1 * inch
FACTION_NAME_Y_OFFSET = 0.15 * inch

PHASE_HEADER_H = 0.36 * inch
PHASE_INTERNAL_MARGIN = 0.17 * inch
SINGLE_STEP_PHASE_INDENT = 0.05 * inch   # left indent for single-step phase body text

PHASE_HEADER_MIN_W = 3.0 * inch           # minimum banner width for vertical layout
PHASE_HEADER_MIN_H = 0.35 * inch          # minimum banner height for vertical layout
PHASE_HEADER_LOCK_W = 4.25 * inch         # width at which banner height stops scaling

# Phase box background layout
PHASE_BOX_V_GAP = 0.01 * inch             # spacing between stacked phases in vertical box
PHASE_BOX_PAD_TOP = 0.12 * inch          # padding above phase content in phase box
PHASE_BOX_PAD_BOTTOM = 0.06 * inch     # padding below phase content in phase box
# Extra top pad applied to a single-step phase/content-box when its first line
# is base-font-only and contains an inline icon. Without this, the icon sits
# higher than the cap-height of the small text and overlaps the header above.
SINGLE_STEP_INLINE_ICON_PAD_TOP = 4

# ContentBox layout
CONTENT_BOX_GAP = 0.10 * inch              # gap between adjacent content boxes
CONTENT_BOX_TITLE_SIZE = 13                # Luminari font size for content box title
CONTENT_BOX_TITLE_PAD_TOP = 6             # padding above title text inside box
CONTENT_BOX_TITLE_PAD_BOTTOM = 4          # padding below title text
CONTENT_BOX_TEXT_SIZE = 9                  # Baskerville font size for content box body text
CONTENT_BOX_TEXT_PAD_BOTTOM = 4           # padding below text before steps
CONTENT_BOX_PAD_TOP = PHASE_BOX_PAD_TOP
CONTENT_BOX_PAD_BOTTOM = PHASE_BOX_PAD_BOTTOM
CONTENT_BOX_INTERNAL_MARGIN = PHASE_INTERNAL_MARGIN
CONTENT_BOX_MIN_W = 2.0 * inch            # minimum content box width

# BorderedBox layout
BORDERED_BOX_HEIGHTS = {
    'small': 0.85 * inch,
    'medium': 1.33 * inch,
    'large': 2.25 * inch,
}
BORDERED_BOX_BORDER_W = 1.0            # thin black border stroke width (pts)
BORDERED_BOX_TITLE_SIZE = 13           # Luminari title font size (pts)
BORDERED_BOX_BODY_PAD = 6             # padding inside box for body text (pts)
BORDERED_BOX_TITLE_GAP = 4            # gap between border line and title text on each side (pts)

# Crafted Items dynamic overlay (drawn on top of CRAFTED_ITEMS_SVG for fine-tuning).
# Mirrors the SVG bounds at default nudges so visual alignment is the baseline;
# tune *_NUDGE / W / H / *_SIZE to refine before retiring the SVG.
CRAFTED_OVERLAY_ENABLED = True
CRAFTED_OVERLAY_W = 2.545 * inch   # mirrors current SVG width
CRAFTED_OVERLAY_H = 0.657 * inch                    # mirrors current SVG height
CRAFTED_OVERLAY_X_NUDGE = 0.0
CRAFTED_OVERLAY_Y_NUDGE = -3.52
CRAFTED_OVERLAY_BORDER_W = 1.0
CRAFTED_OVERLAY_TITLE_SIZE = 16
CRAFTED_OVERLAY_TITLE_GAP = 8.02
CRAFTED_OVERLAY_BODY_SIZE = 9
CRAFTED_OVERLAY_BODY_PAD = 0
CRAFTED_OVERLAY_BODY_LEADING_RATIO = 1.1
CRAFTED_OVERLAY_COLOR_HEX = '#000000'  # red while iterating, so the overlay is easy to compare against the SVG

# All translatable PDF text, keyed first by language code, then by element key.
# Drop new element keys (e.g. 'decree', 'setup_card') into each language entry
# as new dynamic boxes are added — no need for a separate top-level map per
# element. Use _pdf_text(element_key, lang_code) to look up; missing language
# falls back to PDF_TEXT_FALLBACK, missing element returns {}.
PDF_TEXT_FALLBACK = 'en'
PDF_TEXT = {
    'en': {
        'crafted_items': {
            'title': 'Crafted Items',
            'body': 'Vagabond can give you cards to take these items.',
        },
        'phases': {
            'birdsong': 'Birdsong',
            'daylight': 'Daylight',
            'evening': 'Evening',
        },
        'back': {
            'manifest': 'Faction Component Manifest',
            'warriors': 'Warriors',
            'tokens': 'Tokens',
            'buildings': 'Buildings',
            'other': 'Other Pieces',
            'complexity': 'Complexity',
            'aggression': 'Aggression',
            'card_wealth': 'Card Wealth',
            'crafting_ability': 'Crafting Ability',
            'setup': 'Setup',
            'playing': 'Playing the',
            'none': 'none',
        },
        'attr_levels': {
            'N': 'NONE',
            'L': 'LOW',
            'M': 'MODERATE',
            'H': 'HIGH',
        },
        'adset': {
            'title': 'ADVANCED SETUP'
        },
    },
    'fr': {
        'crafted_items': {
            'title': 'Objets Fabriqués',
            'body': 'Un Vagabond peut vous donner des <br/> cartes pour prendre ces objets.',
        },
        'phases': {
            'birdsong': 'Aurore',
            'daylight': 'Jour',
            'evening': 'Crépuscule',
        },
        'back': {
            'manifest': 'Liste des pièces de la Faction',
            'warriors': 'Guerriers',
            'tokens': 'Jetons',
            'buildings': 'Bâtiments',
            'other': 'Autres Pièces',
            'complexity': 'Difficulté',
            'aggression': 'Agressivité',
            'card_wealth': 'Main de cartes',
            'crafting_ability': 'Fabrication',
            'setup': 'Mise en place',
            'playing': 'Jouer le',
            'none': 'aucun',
        },
        'attr_levels': {
            'N': 'AUCUN',
            'L': 'FAIBLE',
            'M': 'MODÉRÉE',
            'H': 'ÉLEVÉE',
        },
        'adset': {
            'title': 'MISE EN PLACE AVANCÉE'
        },
    },
    'es': {
        'crafted_items': {
            'title': 'Objetos Fabricados',
            'body': 'El vagabundo puede darte cartas para tomar estos objectos.',
        },
        'phases': {
            'birdsong': 'Alba',
            'daylight': 'Día',
            'evening': 'Noche',
        },
        'back': {
            'manifest': 'Componentes de Facción',
            'warriors': 'Guerreros',
            'tokens': 'Fichas',
            'buildings': 'Edificios',
            'other': 'Otras Piezas',
            'complexity': 'Complejidad',
            'aggression': 'Agresividad',
            'card_wealth': 'Abundancia de Cartas',
            'crafting_ability': 'Capacidad de Fabricación',
            'setup': 'Preparación Inicial',
            'playing': 'Jugando con',
            'none': 'ninguno',
        },
        'attr_levels': {
            'N': 'NINGUNO',
            'L': 'BAJA',
            'M': 'MODERADA',
            'H': 'ALTA',
        },
        'adset': {
            'title': 'CONFIGURACIÓN AVANZADA'
        },
    },
    # 'es', 'nl', 'pl', 'ru', 'de', 'pt' to be filled in later.
}

def _pdf_text(element_key, lang_code):
    lang = PDF_TEXT.get(lang_code) or PDF_TEXT[PDF_TEXT_FALLBACK]
    if element_key in lang:
        return lang[element_key]
    return PDF_TEXT[PDF_TEXT_FALLBACK].get(element_key, {})


def _lang_code_for(obj):
    """Resolve the language code for any object that has (or whose .faction has) a `language`."""
    faction = getattr(obj, 'faction', obj)
    lang = getattr(faction, 'language', None)
    return lang.code if lang else PDF_TEXT_FALLBACK


def _pdf_label(element_key, label_key, obj, default=''):
    """One-shot lookup: resolve language from `obj`, fetch element block, return label."""
    return _pdf_text(element_key, _lang_code_for(obj)).get(label_key, default)


def _numbered_svg_path(base_dir, n, lang_code):
    """Resolve the numbered-SVG path for a given language. Non-English languages
    use the shared `pdf/svg/international/<n>.svg` set; English uses the
    caller-specified `base_dir/<n>.svg`. Falls back to `base_dir` if the
    international file is missing."""
    if lang_code and lang_code != PDF_TEXT_FALLBACK:
        intl = os.path.join(STATIC_DIR, 'pdf/svg/international', f'{n}.svg')
        if os.path.exists(intl):
            return intl
    return os.path.join(base_dir, f'{n}.svg')

# Track slot rendering
TRACK_SLOT_SIZE = 0.67 * inch
TRACK_SLOT_GAP = 0.06 * inch
TRACK_ROW_TITLE_W = 0.75 * inch
TRACK_ROW_TITLE_MAX_W = 1.5 * inch  # cap for horizontal row-title growth
TRACK_ROW_TITLE_VERTICAL_W = 0.22 * inch  # narrower column for vertically rotated row titles
TRACK_ROW_TITLE_VERTICAL_MAX_W = 0.6 * inch  # cap for vertical row-title (rotated thickness)
TRACK_COL_HEADER_H = 0.30 * inch # Controls the header height for below and above track
TRACK_TITLE_SIZE = 16 # Was 11
TRACK_TITLE_GAP = 6 # Was 4
TRACK_BODY_GAP = 6
TRACK_HEADER_FONT_SIZE = 16 # Controls the font size of the track headers 1VP, 2 etc.
TRACK_ROW_TITLE_FONT_SIZE = 7
TRACK_SLOT_BG_OPACITY = 0.20          # Opacity for slot backgrounds (faction color & images). Adjust to taste.
TRACK_HEADER_BELOW_PAD = 4
TRACK_BOTTOM_PAD = 4
TRACK_HEADER_ICON_H = 0.30 * inch   # Controls how tall header icons are
TRACK_DIVIDER_W = 0.05 * inch
TRACK_OVERLAP_MAX_V_OFFSET = 0.65 * inch   # Max vertical zigzag shift for non-touching tokens
TRACK_OVERLAP_MIN_H_STEP = 0.30 * inch     # Minimum horizontal step (below this = warning)
TRACK_OVERLAP_DIVIDER_W = 0.02 * inch      # Reduced divider width when overlapping
TRACK_OVERLAP_CLEARANCE = 0.03 * inch      # Minimum gap between circle edges when zigzagging
TRACK_COUNTER_SIZE = 0.45 * inch           # Counter slot diameter
TRACK_COUNTER_STROKE_W = 1             # Counter outline stroke width (pts)

# Legend rendering — title-over-image left column, body right column
LEGEND_BLOCK_TITLE_SIZE = 16               # centered Luminari header above the rows (large)
LEGEND_BLOCK_TITLE_GAP = 6                 # gap below the centered block title
LEGEND_BLOCK_BODY_GAP = 0                  # gap between the centered block body and the first row
LEGEND_ROW_TITLE_FONT_SIZE = 11            # Luminari row title (centered over image)
LEGEND_ROW_TITLE_GAP = 2                   # gap between row title baseline and image top
LEGEND_BODY_FONT_SIZE = 10                 # body text font size (controls leading too)
LEGEND_IMAGE_MAX_W = 0.5 * inch            # image hard cap (width)
LEGEND_IMAGE_MAX_H = 0.5 * inch            # image hard cap (height)
LEGEND_IMAGE_BODY_GAP = 8                  # gap between image and body text
LEGEND_LEFT_COL_W = 0.75 * inch            # left column width (room for centered title over 0.5" image)
LEGEND_LEFT_TITLE_BREATHING = 4            # min space between a centered no-visual title and the column edge
LEGEND_NOVISUAL_BODY_NUDGE = 3.2           # small upward bump so a no-visual body aligns visually with the title (math is baseline-exact, but the eye reads slightly low)
LEGEND_ROW_GAP = 8                         # vertical gap between legend rows
LEGEND_MIN_WIDTH = 3.0 * inch              # auto-width minimum for the legend container

# Scale rendering — horizontal "1-2:{{1VP}}   3-4:{{2VP}}" layout
SCALE_FONT_SIZE = 10
SCALE_ENTRY_GAP = 14                       # horizontal space between entries
SCALE_ROW_GAP = 6                          # vertical gap between wrapped rows
SCALE_BLOCK_TITLE_SIZE = 12
SCALE_TOP_PAD = 2                          # padding above the block (and gap below title when present)
SCALE_BOTTOM_PAD = 2                       # padding below the block
SCALE_INLINE_IMG_H = 15                    # height of inline-image icons in scale results

# Card-Slot.png is 63.5mm x 88mm (standard poker card size)
CARD_SLOT_W = 2.5 * inch
CARD_SLOT_H = 3.46 * inch

DECREE_MIN_OFFSET = 1.05 * inch  # minimum amount of decree visible on page
DECREE_MIN_OFFSET_NO_TEXT = 0.65 * inch  # tighter minimum when section has no title/body — pulls slots up too
DECREE_MAX_OFFSET = 4.25 * inch    # maximum amount of decree visible on page
DECREE_TEXT_THRESHOLD = 30        # char count for small vs large text
DECREE_TITLE_Y_OFFSET = 4.3 * inch

# Individual decree slot image render size (565:793 aspect ratio)
DECREE_SLOT_W = 2.5 * inch
DECREE_SLOT_H = DECREE_SLOT_W * (793 / 565)
DECREE_SLOT_Y_OFFSET = 3.9 * inch  # distance from top of decree image to slot tops
DECREE_SLOT_MIN_GAP = 0.01 * inch  # minimum spacing between/around slots
DECREE_SLOT_TITLE_OFFSET = 3.375 * inch  # distance from top of slot image to title text

DECREE_TITLE_SIZE = 20                       # decree title font size
DECREE_BODY_SIZE = 8                        # decree section body font size (italic)
DECREE_BODY_GAP = 0.12 * inch                # vertical gap from title baseline to body baseline
DECREE_TITLE_BODY_PAIR_LIFT = 2               # lift (pts) applied to both title and body when the section has both
DECREE_SLOT_TITLE_SIZE = 18                  # slot title font size (Baskerville)
DECREE_SLOT_BODY_OFFSET = 0.226 * inch        # vertical drop from slot title baseline to slot body baseline
DECREE_SLOT_BODY_SIZE = 10                   # slot body font size (italic)
DECREE_SLOT_BODY_MAX_W = 1.6 * inch           # max width for slot body line before wrapping to two lines
DECREE_SLOT_BODY_LINE_GAP = 0.15 * inch       # baseline drop from line 1 to line 2 of wrapped slot body
DECREE_SLOT_WRAP_SHIFT = 0.18 * inch           # vertical shift applied to slot row when any slot body wraps
DECREE_SLOT_WIDE_TITLE_W = 1.15 * inch        # title width threshold that forces large-text.png on every titled slot

# Card pile layout (bottom-right stacks of cards)
CARD_PILE_TITLE_TOP_OFFSET = 0.4 * inch        # top of title text from top of card image (when pile fits fully on page)
CARD_PILE_TITLE_TOP_OFFSET_OVERFLOW = 0.10 * inch  # tighter offset when pile overflows off the page
CARD_PILE_TITLE_TO_BODY_GAP = 0.0 * inch       # vertical gap between bottom of title and top of body
CARD_PILE_PADDING = 0.18 * inch                 # horizontal text padding inside card
CARD_PILE_GAP = 0.20 * inch                     # horizontal margin between adjacent card piles
CARD_PILE_TITLE_SIZE = 20                       # title font size (Luminari)
CARD_PILE_NO_TEXT_OVERHANG = 3.25 * inch        # max edge overhang for piles with no title or body

# Image height for inline images like card draw and VP
INLINE_IMG_H = 14.5
ABILITY_INLINE_IMG_H = 12          # smaller inline icons inside FactionAbility bodies

# --- FactionBack layout constants ---
MANIFEST_BOX_H = 2.4 * inch
MANIFEST_TITLE_SIZE = 18
MANIFEST_TITLE_CHAR_SPACE = 0.3        # extra spacing (pts) between letters of the manifest title
MANIFEST_COLUMN_HEADER_SIZE = 14
MANIFEST_COLUMN_HEADER_CHAR_SPACE = 0.3  # extra spacing (pts) between letters of column headers
MANIFEST_PIECE_LABEL_SIZE = 11
MANIFEST_INNER_PAD = 0.0 * inch
MANIFEST_DIVIDER_INSET_TOP = 0.35 * inch      # distance from top of band to top of column divider
MANIFEST_DIVIDER_INSET_BOTTOM = 0.05 * inch   # distance from bottom of band to bottom of column divider
MANIFEST_DIVIDER_W = 1.25                       # stroke width (pts) of the vertical column dividers
MANIFEST_ICON_MAX_H = 0.55 * inch
MANIFEST_ICON_MAX_W = 0.9 * inch
MANIFEST_PIECE_GAP = 0.10 * inch
MANIFEST_PIECE_ICON_TEXT_GAP = 6              # gap (pts) between piece icon and its label
MANIFEST_PIECE_LABEL_H_PAD = 4                # horizontal padding on each side of piece label within column
MANIFEST_PIECE_V_PAD = 3                      # vertical padding on top/bottom of each piece within its slot
MANIFEST_PIECE_COL_H_PAD = 5                  # horizontal gap between pieces and the manifest border/dividers
MANIFEST_PIECE_NAME_LEADING = 1.05            # line-height multiplier when piece name wraps
# When a piece has both front and back images, render them as an offset stack.
# Each image scales to STACK_SHRINK of the icon envelope; the diagonal offset
# spans the remaining (1 - STACK_SHRINK) span so the stack still fills the box.
MANIFEST_PIECE_STACK_SHRINK = 0.78
MANIFEST_PIECE_STACK_OFFSET_FRAC = 0.38        # offset = STACK_OFFSET_FRAC * shrunk image size

ATTR_BLOCK_TOP_PAD = 0.08 * inch       # padding above first attribute row
ATTR_LABEL_SIZE = 13                    # italic label ("Complexity") font size
ATTR_LABEL_CHAR_SPACE = 0.3             # extra spacing (pts) between letters of the label
ATTR_LABEL_GAP = 8.0                    # gap (pts) between label and bar
ATTR_BAR_H = 0.32 * inch                # height of the fill bar
ATTR_BAR_BORDER_LEFT_W = 1.2            # black left border stroke width (like .bar-container)
ATTR_BAR_BORDER_LEFT_PAD = 0.6          # gap between border and bar start
ATTR_BAR_BORDER_TOP_EXT = 0.01 * inch    # how far the black border extends above the first row's label
ATTR_BAR_BORDER_BOTTOM_EXT = 0.08 * inch # how far the black border extends below the last row's bar
ATTR_LABEL_BORDER_LEFT_PAD = 2.3        # gap between border and label start
ATTR_BAR_FILL_RATIOS = {                # matches CSS .bar-background-{low/moderate/high/none}
    'N': 0.02,
    'L': 0.28,
    'M': 0.55,
    'H': 0.8,
}
ATTR_BAR_LEVEL_FONT_SIZE = 9.5            # font size of "HIGH"/"MODERATE" text inside bar
ATTR_BAR_LEVEL_CHAR_SPACE = 0.45         # extra spacing (pts) between letters of the level label
ATTR_BAR_LEVEL_TEXT_X_PAD = 5           # left padding of level label inside bar
ATTR_BAR_LEVEL_TEXT_COLOR_LIGHT = '#FFFFFF'   # text color when white is legible on fill
ATTR_BAR_LEVEL_TEXT_COLOR_DARK = '#000000'    # fallback when white is not legible
ATTR_BAR_LEVEL_N_TEXT_COLOR = '#000000'       # N always uses black (bar is barely filled)
ATTR_ROW_GAP = 0.14 * inch              # vertical gap between attribute rows
ATTR_WHITE_TEXT_MIN_CONTRAST = 1.9      # mirrors JS isWhiteTextLegible threshold (large text)
ATTR_BLOCK_INDENT = 35                      # left indent for the whole attribute bar section


SETUP_TITLE_SIZE = 22
SETUP_TITLE_GAP = 0.13 * inch
SETUP_MARKER_SIZE = 0.32 * inch            # width of the reserved marker slot (for text alignment)
SETUP_MARKER_HEIGHT = 0.17 * inch          # rendered height of each setup-step number SVG
SETUP_MARKER_TEXT_GAP = 0.04 * inch          # Gap between setup number and text
SETUP_STEP_GAP = 0.2 * inch                # Gap between each step in setup
SETUP_BODY_SIZE = 11                       # setup step body text size
SETUP_STEP_INDENT = 0.1 * inch            # left indent applied to each setup step (marker + text)

HOWTOPLAY_TITLE_SIZE = 22
HOWTOPLAY_TITLE_GAP = 0.13 * inch
HOWTOPLAY_BODY_SIZE = 10
HOWTOPLAY_IMAGE_GAP = 0.10 * inch          # horizontal gap between text and the image

BACK_X_MARGIN = 0.7 * inch             # left/right page margin for the FactionBack
BACK_TOP_MARGIN = 0.75 * inch             # top page margin for the FactionBack
BACK_BOTTOM_MARGIN = 0.15 * inch         # bottom page margin for the FactionBack
BACK_BG_SCREEN_OPACITY = 0.70            # white screen opacity applied over the background to lighten it
BACK_COLUMN_GAP = 0.25 * inch
LEFT_COL_W_RATIO = 0.48

# --- SetupCard layout (poker-card-sized PDF) ---
# Page = (CARD_SLOT_W, CARD_SLOT_H) — same as cardpiles. All measurements
# below are in points relative to that page.
SETUP_CARD_REACH_FONT_SIZE = 18
SETUP_CARD_REACH_RIGHT_INSET = 0.35 * inch    # distance from right edge of card to reach text
SETUP_CARD_REACH_BOTTOM_INSET = 0.3 * inch   # distance from bottom edge of card to reach baseline

SETUP_CARD_BAND_X_INSET = 0.217 * inch         # left/right inset of the faction-color band
SETUP_CARD_BAND_TOP_INSET = 0.336 * inch       # inset from top edge of card to top of band
SETUP_CARD_BAND_HEIGHT = 0.3491 * inch
SETUP_CARD_BAND_TEXT_PADDING = 0.06 * inch    # horizontal padding inside the band for the faction name

SETUP_CARD_NAME_SIZE_LARGE = 12
SETUP_CARD_NAME_SIZE_SMALL = 10
SETUP_CARD_NAME_LINE_GAP = 1.5                # extra leading between two wrapped lines (pts)
SETUP_CARD_NAME_LEFT_PADDING = 0.06 * inch    # left padding inside the band for the faction name

SETUP_CARD_TITLE_TEXT = 'ADVANCED SETUP'
SETUP_CARD_TITLE_FONT_SIZE = 9.5               # font size (pt) of the "ADVANCED SETUP" title
SETUP_CARD_TITLE_TOP_MARGIN = 0.833 * inch     # distance from top of card to title baseline
SETUP_CARD_TITLE_CHAR_SPACING = 0.35           # extra pts of space between each character in the title
SETUP_CARD_TITLE_MAX_W = CARD_SLOT_W - 2 * SETUP_CARD_BAND_X_INSET  # title must fit within the band's horizontal extent
SETUP_CARD_TITLE_MIN_FONT_SIZE = 6.5            # don't shrink the localized title below this

# Header image hangs upward from the bottom-left of the band
SETUP_CARD_HEADER_MAX_H = 1.4 * inch          # don't let the header overflow upward forever

# Setup-step body rectangle (the white usable area inside background.png).
# Defined as insets from each edge of the card. Tune these to match the
# white area in background.png.
SETUP_CARD_BODY_X_INSET = 0.32 * inch        # inset from left and right edges
SETUP_CARD_BODY_TOP_INSET = 1 * inch      # inset from top edge
SETUP_CARD_BODY_BOTTOM_INSET = 0.25 * inch   # inset from bottom edge

# Step rendering — smaller than FactionBack since the card is tiny
SETUP_CARD_MARKER_SIZE = 0.18 * inch          # reserved marker slot width for alignment
SETUP_CARD_MARKER_HEIGHT = 0.16 * inch        # rendered SVG height
SETUP_CARD_MARKER_TEXT_GAP = 0.1 * inch      # horizontal gap between number marker and step text
SETUP_CARD_STEP_GAP = 0.06 * inch             # vertical gap between consecutive steps
SETUP_CARD_STEP_BODY_SIZE = 7.5               # font size (pt) of step description text
SETUP_CARD_STEP_INDENT = 0.0 * inch           # left indent applied to each step block
SETUP_CARD_STEP_TEXT_Y_OFFSET = 2.5            # pts to nudge text up so its visual top aligns with the number marker (compensates for font line-box padding)

# StepAction layout
ACTION_ITEM_H = 0.26 * inch       # item icon height (width scales proportionally)
ACTION_CARD_H = 0.337 * inch      # card icon height (width scales proportionally)
ACTION_CARDS_W = 0.57 * inch      # nonbird/other_cards icon width (height scales proportionally)
ACTION_DEFAULT_W = 0.2 * inch     # action & other cost icon width (height scales proportionally)
# Arrows from Item to action
ITEM_ARROW_W = 11.3                  # total space reserved for arrow region
ITEM_ARROW_ICON_GAP = 4.8           # gap between icon and arrow start
ITEM_ARROW_TEXT_GAP = 1.9           # gap between arrow end and text
ITEM_ARROW_HEAD_SIZE = 6.5         # arrowhead length
ITEM_ARROW_HEAD_SPREAD = 0.57      # arrowhead width multiplier
# Arrows from spent cards to action
CARD_ARROW_W = 40.75                # total space reserved for arrow region
CARD_ARROW_ICON_GAP = 2           # gap between icon and arrow start
CARD_ARROW_TEXT_GAP = 3           # gap between arrow end and text
CARD_ARROW_HEAD_SIZE = 5           # arrowhead length
CARD_ARROW_HEAD_SPREAD = 0.57      # arrowhead width multiplier

ACTION_ICON_TEXT_GAP = 4           # padding between icon and text for action/other costs (no arrow)
ACTION_ICON_Y_NUDGE = 0.2         # nudge icon/arrow down as fraction of first line height
ACTION_ROW_GAP = 1                # vertical gap between action rows in points
SIDE_BY_SIDE_GAP = 6              # horizontal gap between side-by-side actions (pts)
MAX_ACTIONS_PER_ROW = 4           # max actions packed on one line

# Minimum card icon width (single-card icons like fox/mouse/rabbit/bird)
# Used as the centering reference — wider icons shift left into margin
MIN_CARD_ICON_W = ACTION_CARD_H * (486 / 673)  # ~17.52 pts

# Half the width difference between other_cards.png (card_nonbird, the wide
# icon) and the per-suit card icons (mouse/fox/rabbit/bird). When a step
# contains a card_nonbird action, every card-cost row gets pushed right by
# this amount so the wide icon's leftward overflow doesn't clip the section.
# Precomputed so the math doesn't repeat at render time.
CARD_NONBIRD_WIDTH_OVERHANG = (ACTION_CARDS_W - MIN_CARD_ICON_W) / 2

# TEMP DEBUG: outline the MIN_CARD_ICON_W column on card-action rows in red.
DEBUG_CARD_ICON_COLUMN = True

STATIC_DIR = os.path.join(os.path.dirname(__file__), '..', 'the_keep', 'static')
FORGE_STATIC_DIR = os.path.join(os.path.dirname(__file__), 'static', 'the_forge')
FORGE_LOGO_SVG = os.path.join(FORGE_STATIC_DIR, 'Forge_Logo.svg')

# Components sheet (Combined PDF print-out page) geometry — landscape letter
# to match the Front (FactionSheet) and Back (FactionBack) page orientation.
COMPONENTS_PAGE_W, COMPONENTS_PAGE_H = landscape(letter)  # 792 x 612 pts
COMPONENTS_PAGE_MARGIN = 0.5 * inch
COMPONENTS_GRID_GAP = 0.25 * inch                  # horizontal & vertical gap between cells
COMPONENTS_ADSET_STATIC = os.path.join(STATIC_DIR, 'images', 'ADSET.png')

ABILITY_BERRY_SVG = os.path.join(STATIC_DIR, 'pdf/svg/ability_berry.svg')
PHASE_NUMBER_SVG_DIR = os.path.join(STATIC_DIR, 'pdf/svg')
SETUP_CARD_NUMBER_SVG_DIR = os.path.join(STATIC_DIR, 'pdf/svg/adset')
FACTION_TOP_BAR_SVG = os.path.join(STATIC_DIR, 'pdf/boxes/Faction_Top_Bar.svg')
CRAFTED_ITEMS_SVG = os.path.join(STATIC_DIR, 'pdf/boxes/Crafted_Items_Box.svg')
CARD_SLOT_IMG = os.path.join(STATIC_DIR, 'pdf/images/Card-Slot.png')
CARD_PILE_SVG = os.path.join(STATIC_DIR, 'pdf/svg/card_pile.svg')
MEEPLE_SVG = os.path.join(STATIC_DIR, 'pdf/svg/meeple.svg')
TOKEN_SLOT_IMG = os.path.join(STATIC_DIR, 'pdf/images/TokenSlot.png')
BUILDING_SLOT_IMG = os.path.join(STATIC_DIR, 'pdf/images/BuildingSlot.png')
DECREE_DIR = os.path.join(STATIC_DIR, 'pdf/decree')
PHASE_BOX_SVG = os.path.join(STATIC_DIR, 'pdf/boxes/Phase_Box.svg')
PHASE_BOX_TAN = '#f9e3b3'                 # tan fill color inside Phase_Box.svg

# Candidate tan-box SVGs. The picker chooses the one whose natural aspect
# (or 90°-rotated aspect) is closest to a target rect, minimizing visible
# stretch when the asset is non-uniformly scaled to fit.
#
# Each tuple is (path, is_large_asset, is_shape_specialized). Large-asset
# SVGs (Phase_Box, Faction_Top_Bar) carry detail tuned for big footprints —
# texture and torn-edge motifs that compress unattractively when shrunk to
# small boxes. The small-target filter excludes them by default. However,
# shape-specialized assets like Faction_Top_Bar exist *because* of their
# extreme aspect; for very long-thin targets the asset's shape is the whole
# point, so the filter spares them.
_BOX_SVG_CANDIDATES = [
    (os.path.join(STATIC_DIR, 'pdf/boxes/Phase_Box.svg'),       True,  False),
    (os.path.join(STATIC_DIR, 'pdf/boxes/Medium_Box.svg'),      False, False),
    (os.path.join(STATIC_DIR, 'pdf/boxes/Small_Box.svg'),       False, False),
    (os.path.join(STATIC_DIR, 'pdf/boxes/Faction_Top_Bar.svg'), True,  True),
]
SMALL_BOX_PREFER_THRESHOLD = 2.5 * inch
# Targets whose aspect (in either orientation) exceeds this ratio are treated
# as "long-thin" — shape-specialized large assets stay in the pool for them
# even when the target is below SMALL_BOX_PREFER_THRESHOLD.
LONG_THIN_ASPECT = 4.0


def _box_svg_aspects():
    """Return [(path, natural_aspect_w_over_h, is_large, is_shape_specialized)]
    for available candidate boxes. Cached on first call; svg2rlg parses are
    cheap but not free, and this list is consulted on every box draw.
    """
    cache = getattr(_box_svg_aspects, '_cache', None)
    if cache is not None:
        return cache
    cache = []
    for p, is_large, is_shape_specialized in _BOX_SVG_CANDIDATES:
        if not os.path.exists(p):
            continue
        try:
            d = svg2rlg(p)
        except Exception:
            d = None
        if d is None or d.width <= 0 or d.height <= 0:
            continue
        cache.append((p, d.width / d.height, is_large, is_shape_specialized))
    _box_svg_aspects._cache = cache
    return cache


def _pick_box_svg(target_w, target_h):
    """Pick the candidate _Box.svg whose aspect (or 90°-rotated aspect) most
    closely matches target_w / target_h. Returns (path, rotated_bool).

    Score is |log(target_aspect) - log(candidate_aspect)| so a 2x mismatch on
    either side counts the same. Rotation is considered for every candidate.

    Small-target filter: when min(target_w, target_h) < SMALL_BOX_PREFER_THRESHOLD,
    large assets are excluded — their detail compresses badly into small
    footprints. Shape-specialized large assets (Faction_Top_Bar) are spared
    when the target is also long-thin (aspect >= LONG_THIN_ASPECT in either
    orientation), because for those shapes the asset's silhouette is the
    whole point regardless of total size. Returns (PHASE_BOX_SVG, False) if
    no candidates are usable at all.
    """
    candidates = _box_svg_aspects()
    if not candidates or target_w <= 0 or target_h <= 0:
        return PHASE_BOX_SVG, False
    if min(target_w, target_h) < SMALL_BOX_PREFER_THRESHOLD:
        target_aspect = target_w / target_h
        long_thin = (target_aspect >= LONG_THIN_ASPECT
                     or target_aspect <= 1.0 / LONG_THIN_ASPECT)
        filtered = [c for c in candidates
                    if not c[2] or (long_thin and c[3])]
        if filtered:
            candidates = filtered
    target = math.log(target_w / target_h)
    best = None
    best_score = None
    for path, aspect, _is_large, _is_shape in candidates:
        for rotated in (False, True):
            cand_aspect = (1.0 / aspect) if rotated else aspect
            score = abs(target - math.log(cand_aspect))
            if best_score is None or score < best_score:
                best_score = score
                best = (path, rotated)
    return best
MEEPLE_SVG = os.path.join(STATIC_DIR, 'pdf/svg/meeple.svg')

ADSET_DIR = os.path.join(STATIC_DIR, 'pdf/adset')
SETUP_CARD_BG_PNG = os.path.join(ADSET_DIR, 'background.png')
SETUP_CARD_MILITANT_PNG = os.path.join(ADSET_DIR, 'militant3.png')
SETUP_CARD_INSURGENT_PNG = os.path.join(ADSET_DIR, 'insurgent2.png')
SETUP_CARD_SWORD_PNG = os.path.join(ADSET_DIR, 'Adset_Sword.png')
SETUP_CARD_SWORD_SIZE = 0.162 * inch              # rendered size (square) of the militant sword next to the title
SETUP_CARD_SWORD_TITLE_GAP = 0.017 * inch         # horizontal gap between sword and title
SETUP_CARD_SWORD_Y_NUDGE = -1.42                  # pts to shift the sword vertically (negative = down)
SETUP_CARD_SWORD_MIN_LEFT_INSET = 0.25 * inch     # sword's left edge can't go any closer to the card edge than this — keeps it from drifting too far out and forces the title to shrink earlier

PHASE_HEADERS = {
    'birdsong': {
        'long': os.path.join(STATIC_DIR, 'pdf/headers/BirdsongBarLong.png'),
        'short': os.path.join(STATIC_DIR, 'pdf/headers/BirdsongBarShort.png'),
        'banner': os.path.join(STATIC_DIR, 'pdf/headers/BirdsongBanner.png'),
        'color': '#E8A838',
    },
    'daylight': {
        'long': os.path.join(STATIC_DIR, 'pdf/headers/DaylightBarLong.png'),
        'short': os.path.join(STATIC_DIR, 'pdf/headers/DaylightBarShort.png'),
        'banner': os.path.join(STATIC_DIR, 'pdf/headers/DaylightBanner.png'),
        'color': '#6AB0D4',
    },
    'evening': {
        'long': os.path.join(STATIC_DIR, 'pdf/headers/EveningBarLong.png'),
        'short': os.path.join(STATIC_DIR, 'pdf/headers/EveningBarShort.png'),
        'banner': os.path.join(STATIC_DIR, 'pdf/headers/EveningBanner.png'),
        'color': '#7B6EA8',
    },
}

def _resolve_static_url(url):
    """Resolve a `static()` URL back to a filesystem path for ReportLab.
    `static()` URL-encodes special characters (e.g. '+' → '%2B'), so we
    decode before handing the relative path to staticfiles finders."""
    if not url:
        return None
    from django.contrib.staticfiles import finders
    from urllib.parse import unquote
    rel = unquote(url.split('/static/', 1)[-1])
    return finders.find(rel)


def _sheet_of(obj):
    """Walk the parent chain of a forge model object to find its FactionSheet.

    Returns None if no sheet is reachable. Used to resolve `custom_image_N`
    keywords during PDF render — those are scoped per-sheet, but the
    Flowables operate on child objects (track, legend, action, etc.).
    """
    if obj is None:
        return None
    if hasattr(obj, 'sheet') and obj.sheet is not None:
        return obj.sheet
    for parent_attr in ('step', 'legend', 'scale', 'track', 'box'):
        parent = getattr(obj, parent_attr, None)
        if parent is not None:
            sheet = _sheet_of(parent)
            if sheet is not None:
                return sheet
    return None


def _inline_image_path(keyword, sheet=None):
    """Resolve an inline-image keyword to a filesystem path.

    `custom_image_N` keywords resolve against `sheet.custom_inline_images`
    (slot N) when a sheet is supplied. Everything else falls through to
    FORGE_INLINE_IMAGES + the staticfiles finder.
    """
    from .inline_images import FORGE_INLINE_IMAGES, CUSTOM_IMAGE_PREFIX
    if sheet is not None and keyword and keyword.startswith(CUSTOM_IMAGE_PREFIX):
        slot_str = keyword[len(CUSTOM_IMAGE_PREFIX):]
        if slot_str.isdigit():
            ci = sheet.custom_inline_images.filter(slot=int(slot_str)).first()
            if ci and ci.image:
                try:
                    return ci.image.path
                except (ValueError, NotImplementedError):
                    return None
        return None
    return _resolve_static_url(FORGE_INLINE_IMAGES.get(keyword))


def _cost_icon_path(cost):
    """Resolve a StepAction cost (from COST_ICON_PATHS) to a path."""
    return _resolve_static_url(COST_ICON_PATHS.get(cost))

# StepAction.cost -> static URL. Keys must match StepAction.CostChoices values
# in models.py. Resolved to a filesystem path via `_cost_icon_path`.
COST_ICON_PATHS = {
    # Items (_flip.png variants)
    'item_sword': static('items/sword_flip.png'),
    'item_hammer': static('items/hammer_flip.png'),
    'item_crossbow': static('items/crossbow_flip.png'),
    'item_coins': static('items/coins_flip.png'),
    'item_boots': static('items/boots_flip.png'),
    'item_tea': static('items/tea_flip.png'),
    'item_bag': static('items/bag_flip.png'),
    'item_torch': static('items/torch_flip.png'),
    'item_any': static('items/any_flip.png'),
    # Cards
    'card_fox': static('pdf/inline/fox_card.png'),
    'card_mouse': static('pdf/inline/mouse_card.png'),
    'card_rabbit': static('pdf/inline/rabbit_card.png'),
    'card_bird': static('pdf/inline/bird_card.png'),
    'card_nonbird': static('pdf/inline/other_cards.png'),
    'card_vertical': static('pdf/inline/card_vertical.png'),
    # Action (default faction icon)
    'action': static('pdf/inline/faction-lord of the hundreds.png'),
}


def _xml_escape(s):
    return s.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def _replace_inline_images(text, img_height=None, sheet=None):
    """Replace `{{ key }}` tokens with ReportLab <img/> tags.

    Used for fields edited via the token-based icon picker (forge_editor.js)
    rather than the rich-text editor — currently track column headers and
    row titles. XML-escapes surrounding text so `&`/`<`/`>` are safe.
    """
    if not text:
        return ''
    out = []
    i = 0
    while i < len(text):
        start = text.find('{{', i)
        if start < 0:
            out.append(_xml_escape(text[i:]))
            break
        end = text.find('}}', start + 2)
        if end < 0:
            out.append(_xml_escape(text[i:]))
            break
        out.append(_xml_escape(text[i:start]))
        key = text[start + 2:end].strip()
        if key:
            out.append(_inline_image_tag(key, img_height, sheet=sheet))
        i = end + 2
    return ''.join(out)


def _inline_image_tag(key, img_height=None, sheet=None):
    """Render <img data-forge-image="KEY"> as a ReportLab Paragraph <img/> tag.

    Returns '' if the key has no entry or the resolved path doesn't exist.
    """
    img_path = _inline_image_path(key, sheet=sheet)
    if not img_path or not os.path.exists(img_path):
        return ''
    h = img_height or INLINE_IMG_H
    from PIL import Image as PILImage
    with PILImage.open(img_path) as pil_img:
        iw, ih = pil_img.size
    aspect = iw / ih
    img_w = h * aspect
    return f'<img src="{img_path}" width="{img_w:.1f}" height="{h}" valign="middle"/>'


class _ForgePdfSanitizer(HTMLParser):
    """Translate forge rich-text storage HTML to ReportLab Paragraph XML.

    Allowlist (matches the storage format produced by forge_richtext.js):
      <strong>/<b>                   -> <b>
      <em>/<i>                       -> <i>
      <span data-forge="header">     -> <font name="Baskerville" size="15">
      <span data-forge="luminari">   -> <font name="Luminari">
      <img data-forge-image="KEY">   -> <img src=...> resolved from
                                        FORGE_INLINE_IMAGES (dropped if
                                        unknown / missing on disk)
      <br>                           -> <br/>
      Text                           -> XML-escaped passthrough
    """

    _TAG_MAP = {
        'strong': ('<b>', '</b>'),
        'b':      ('<b>', '</b>'),
        'em':     ('<i>', '</i>'),
        'i':      ('<i>', '</i>'),
    }

    def __init__(self, img_height=None, sheet=None):
        super().__init__(convert_charrefs=True)
        self._out = []
        self._stack = []
        self._img_height = img_height
        self._sheet = sheet

    def handle_starttag(self, tag, attrs):
        attr_map = dict(attrs)
        tag = tag.lower()
        if tag == 'br':
            self._out.append('<br/>')
            return
        if tag == 'img':
            key = attr_map.get('data-forge-image')
            if not key:
                return
            rendered = _inline_image_tag(key, self._img_height, sheet=self._sheet)
            if rendered:
                self._out.append(rendered)
            return
        if tag == 'span':
            forge = attr_map.get('data-forge')
            if forge == 'header':
                self._out.append('<font name="Baskerville" size="15">')
                self._stack.append('</font>')
                return
            if forge == 'luminari':
                self._out.append('<font name="Luminari">')
                self._stack.append('</font>')
                return
            self._stack.append(None)
            return
        mapped = self._TAG_MAP.get(tag)
        if mapped:
            self._out.append(mapped[0])
            self._stack.append(mapped[1])
            return
        self._stack.append(None)

    def handle_startendtag(self, tag, attrs):
        tag = tag.lower()
        if tag in ('br', 'img'):
            self.handle_starttag(tag, attrs)
            return

    def handle_endtag(self, tag):
        if tag.lower() == 'br':
            return
        if not self._stack:
            return
        close = self._stack.pop()
        if close is not None:
            self._out.append(close)

    def handle_data(self, data):
        if data:
            self._out.append(_xml_escape(data))

    def result(self):
        while self._stack:
            close = self._stack.pop()
            if close is not None:
                self._out.append(close)
        return ''.join(self._out)


def format_step_markup(text, sheet=None):
    """Translate forge rich-text storage HTML to ReportLab Paragraph XML.

    Storage is produced by the rich-text editor's serializer (see
    the_forge/static/the_forge/forge_richtext.js). This function parses
    the allowlisted HTML and emits the equivalent ReportLab markup
    (<b>/<i>/<font>/<img>/<br>) ready to be passed to a Paragraph.

    `sheet` is forwarded to the inline-image resolver so per-sheet
    `custom_image_N` keywords resolve to the user's uploads.
    """
    if not text:
        return ""
    parser = _ForgePdfSanitizer(sheet=sheet)
    parser.feed(str(text))
    parser.close()
    return parser.result()


def format_inline_images(text, img_height=None, sheet=None):
    """Like format_step_markup but suppresses bold/italic/header/luminari.

    Used for fields whose toolbar exposes only the inline-image picker
    (e.g. ability bodies). Allowlist for those fields is text + <img
    data-forge-image="KEY"> + <br>; any other tags would be styling the
    user can't have applied, but we sanitize them away defensively rather
    than emit them.
    """
    if not text:
        return ""
    parser = _ForgePdfSanitizer(img_height=img_height, sheet=sheet)
    parser.feed(str(text))
    parser.close()
    return parser.result()


def tighten_large_font_lines(para):
    """Reduce descent on lines whose largest font exceeds the style's base size.

    With autoLeading='max', a 15pt fragment inflates the line's descent to
    15pt-scale metrics even when the large text has no descenders (e.g. "Build").
    This tightens those lines to use the base font's descent, removing the
    excess gap before the next line.  Must be called after wrap().
    """
    if not hasattr(para, 'blPara') or not hasattr(para.blPara, 'lines'):
        return
    lines = para.blPara.lines
    base_size = para.style.fontSize
    leading = para.style.leading
    face = getFont(para.style.fontName).face
    base_descent = face.descent * base_size / 1000.0  # negative

    adjusted = False
    for line in lines:
        if not hasattr(line, 'words'):
            continue
        max_fs = 0
        for frag in line.words:
            cb = getattr(frag, 'cbDefn', None)
            if cb and getattr(cb, 'kind', None) == 'img':
                continue
            fs = getattr(frag, 'fontSize', 0)
            if fs > max_fs:
                max_fs = fs
        if max_fs > base_size:
            line.descent = base_descent
            adjusted = True

    if adjusted:
        para.height = sum(max(l.ascent - l.descent, leading) for l in lines)


def first_line_has_inline_image(para):
    """True if the paragraph's first wrapped line contains any inline image
    fragment. Must be called after wrap()."""
    if not hasattr(para, 'blPara') or not hasattr(para.blPara, 'lines'):
        return False
    lines = para.blPara.lines
    if not lines or not hasattr(lines[0], 'words'):
        return False
    for frag in lines[0].words:
        cb = getattr(frag, 'cbDefn', None)
        if cb and getattr(cb, 'kind', None) == 'img':
            return True
    return False


def first_line_has_inline_icon_only_base_font(para):
    """True if the paragraph's first wrapped line contains an inline image
    fragment AND no fragment exceeds the style's base font size.

    Used to detect the case where a single-step phase paragraph would otherwise
    place an icon above the cap-height of small text, overlapping the header
    band above. Must be called after wrap().
    """
    if not hasattr(para, 'blPara') or not hasattr(para.blPara, 'lines'):
        return False
    lines = para.blPara.lines
    if not lines:
        return False
    first = lines[0]
    if not hasattr(first, 'words'):
        return False
    base_size = para.style.fontSize
    has_img = False
    max_text_fs = 0
    for frag in first.words:
        cb = getattr(frag, 'cbDefn', None)
        if cb and getattr(cb, 'kind', None) == 'img':
            has_img = True
            continue
        fs = getattr(frag, 'fontSize', 0)
        if fs > max_text_fs:
            max_text_fs = fs
    return has_img and max_text_fs <= base_size


def true_paragraph_height(para, width):
    """Wrap a Paragraph and return its true rendered height.

    With autoLeading='max', ReportLab's wrap() can underreport height because
    the rendering algorithm (_putFragLine) uses effective ascent/descent with
    minimums of 5/6 and 1/6 of base leading, which can push the last line's
    bottom below the reported height. This simulates the exact rendering
    positions to return the true extent.
    """
    _, h = para.wrap(width, 9999)
    tighten_large_font_lines(para)
    h = para.height  # may have been updated by tightening
    if not hasattr(para, 'blPara') or not hasattr(para.blPara, 'lines'):
        return h

    lines = para.blPara.lines
    if not lines:
        return h

    leading = para.style.leading
    _56 = 5.0 / 6.0
    _16 = 1.0 / 6.0

    cur_y = None
    olb = None  # old line bottom
    oleading = leading

    for i, line in enumerate(lines):
        a_raw = getattr(line, 'ascent', None)
        d_raw = getattr(line, 'descent', None)

        if a_raw is not None and d_raw is not None:
            # autoLeading='max' effective values (matches _putFragLine)
            ascent = max(_56 * leading, a_raw)
            descent = max(_16 * leading, -d_raw)
            line_leading = ascent + descent
        else:
            # Tuple-style line (kind==0, no per-line metrics)
            return h

        if i == 0:
            cur_y = h - ascent
        else:
            if olb is not None:
                xcy = olb - ascent
                if oleading != line_leading:
                    cur_y += line_leading - oleading
                if abs(xcy - cur_y) > 1e-8:
                    cur_y = xcy

        olb = cur_y - descent
        oleading = line_leading

    # olb is the bottom of the last line; if negative, text overflows
    if olb is not None and olb < 0:
        h = h + abs(olb)

    # Add margin for lines with enlarged fonts (e.g. ##text## at 15pt).
    # The exact rendering height leaves descenders touching the boundary;
    # this adds proportional breathing room when mixed sizes are present.
    base_size = para.style.fontSize
    max_line_size = base_size
    for line in lines:
        if hasattr(line, 'words'):
            for frag in line.words:
                cb = getattr(frag, 'cbDefn', None)
                if cb and getattr(cb, 'kind', None) == 'img':
                    continue
                fs = getattr(frag, 'fontSize', 0)
                if fs > max_line_size:
                    max_line_size = fs
    if max_line_size > base_size:
        h += (max_line_size - base_size) * 1 # Extra padding at bottom of section to compensate for large text 

    return h


class BannerWithText(Flowable):
    """Banner image with overlaid phase name text."""

    def __init__(self, image_path, width, height, text, font_name='Luminari',
                 font_color=HexColor('#FFFFFF')):
        super().__init__()
        self.image_path = image_path
        self._width = width
        self._height = height
        self.text = text
        self.font_name = font_name
        self.font_color = font_color

    def wrap(self, availWidth, availHeight):
        return self._width, self._height

    def draw(self):
        c = self.canv
        c.saveState()
        c.drawImage(self.image_path, 0, 0, width=self._width, height=self._height)
        font_size = self._height * 0.72
        left_pad = self._height * 0.15
        text_y = (self._height - font_size) / 2 + font_size * 0.2
        c.setFont(self.font_name, font_size)
        c.setFillColor(self.font_color)
        txt = c.beginText(left_pad, text_y)
        txt.setCharSpace(font_size * -0.04)
        txt.textLine(self.text)
        c.drawText(txt)
        c.restoreState()


class BorderedBoxFlowable(Flowable):
    """A bordered box with a Luminari title centered on the top border.

    The top border breaks around the title text. Body text (with sudo markdown)
    is rendered inside the box. If the body overflows, it is clipped and "..."
    is drawn at the bottom.
    """

    def __init__(self, title, body_markup, total_width, box_height, body_style, element_color_hex='#000000'):
        super().__init__()
        self._width = total_width
        self._height = box_height
        self.title = title
        self.body_markup = body_markup
        self.body_style = body_style
        self.element_color_hex = element_color_hex

    def wrap(self, availWidth, availHeight):
        return self._width, self._height

    def draw(self):
        c = self.canv
        c.saveState()

        w = self._width
        h = self._height
        bw = BORDERED_BOX_BORDER_W
        half_bw = bw / 2  # extend lines by half stroke width so corners meet flush

        # --- Title measurement ---
        title_font_size = BORDERED_BOX_TITLE_SIZE
        title_w = pdfmetrics.stringWidth(self.title, 'Luminari', title_font_size)
        gap = BORDERED_BOX_TITLE_GAP
        title_block_w = title_w + gap * 2  # total gap in top border

        # Center the title horizontally
        title_x_start = (w - title_block_w) / 2
        title_x_end = title_x_start + title_block_w

        # Top border y (ReportLab origin is bottom-left, so top = h)
        top_y = h

        element_color = HexColor(self.element_color_hex)

        # --- Draw border ---
        # Lines are extended by half_bw at each end so the stroke overlaps
        # at corners, producing clean mitered joints.
        c.setStrokeColor(element_color)
        c.setLineWidth(bw)

        # Bottom border (extend left and right by half_bw)
        c.line(-half_bw, 0, w + half_bw, 0)
        # Left border (extend top and bottom by half_bw)
        c.line(0, -half_bw, 0, top_y + half_bw)
        # Right border (extend top and bottom by half_bw)
        c.line(w, -half_bw, w, top_y + half_bw)
        # Top border — two segments with gap for title (extend outer ends by half_bw)
        c.line(-half_bw, top_y, title_x_start, top_y)
        c.line(title_x_end, top_y, w + half_bw, top_y)

        # --- Draw title (centered vertically on top border) ---
        # Position baseline so the cap-height midpoint sits on the border line.
        # Cap height ≈ 70% of font size for most fonts; baseline = top_y - capH/2
        cap_h = title_font_size * 0.70
        title_y = top_y - cap_h / 2
        c.setFont('Luminari', title_font_size)
        c.setFillColor(element_color)
        c.drawCentredString(w / 2, title_y, self.title)

        # --- Draw body text (if any) ---
        if self.body_markup:
            from reportlab.lib.styles import ParagraphStyle
            from reportlab.lib.enums import TA_CENTER

            pad = BORDERED_BOX_BODY_PAD
            # Body area starts below the title and has padding on all sides
            title_area_h = title_font_size * 0.6  # space reserved below top border for title
            body_x = pad
            body_w = w - pad * 2
            body_top_y = top_y - title_area_h - pad
            body_available_h = body_top_y - pad  # from body top to bottom + padding

            # Center body text horizontally
            centered_style = ParagraphStyle(
                'BorderedBoxBody',
                parent=self.body_style,
                alignment=TA_CENTER,
                textColor=element_color,
            )

            para = Paragraph(self.body_markup, centered_style)
            para_w, para_h = para.wrap(body_w, 9999)
            tighten_large_font_lines(para)
            para_h = para.height

            overflow = para_h > body_available_h

            if overflow:
                print(f"WARNING: BorderedBox '{self.title}' body text overflows "
                      f"(needs {para_h:.1f}pt, available {body_available_h:.1f}pt)")
                # Clip to box interior
                c.saveState()
                p = c.beginPath()
                p.rect(body_x, pad, body_w, body_top_y - pad)
                c.clipPath(p, stroke=0, fill=0)
                para.drawOn(c, body_x, body_top_y - para_h)
                c.restoreState()
            else:
                para.drawOn(c, body_x, body_top_y - para_h)

        c.restoreState()


def draw_crafted_items_overlay(c, x, y, w, h, title, body, color_hex='#000000'):
    """Bordered box with title-on-top-border + centered body, drawn directly on canvas.

    Mirrors BorderedBoxFlowable's stroke/title math but at absolute canvas coords
    (x, y = bottom-left). Used to overlay the Crafted Items SVG for fine-tuning.
    """
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.enums import TA_CENTER

    c.saveState()
    c.set_layer_tag('fg')

    bw = CRAFTED_OVERLAY_BORDER_W
    half_bw = bw / 2
    color = HexColor(color_hex)

    title_font_size = CRAFTED_OVERLAY_TITLE_SIZE
    title_w = pdfmetrics.stringWidth(title, 'Luminari', title_font_size)
    gap = CRAFTED_OVERLAY_TITLE_GAP
    title_block_w = title_w + gap * 2

    # Box edges in absolute canvas coords
    left = x
    right = x + w
    bottom = y
    top = y + h

    title_x_start = left + (w - title_block_w) / 2
    title_x_end = title_x_start + title_block_w

    c.setStrokeColor(color)
    c.setLineWidth(bw)
    # Bottom
    c.line(left - half_bw, bottom, right + half_bw, bottom)
    # Left
    c.line(left, bottom - half_bw, left, top + half_bw)
    # Right
    c.line(right, bottom - half_bw, right, top + half_bw)
    # Top — two segments with gap for title
    c.line(left - half_bw, top, title_x_start, top)
    c.line(title_x_end, top, right + half_bw, top)

    # Title centered on the top border (cap-height midpoint sits on the line)
    cap_h = title_font_size * 0.70
    title_y = top - cap_h / 2
    c.setFont('Luminari', title_font_size)
    c.setFillColor(color)
    c.drawCentredString(left + w / 2, title_y, title)

    # Body — vertically centered in the full interior between the two
    # horizontal borders (pad applied symmetrically top and bottom).
    if body:
        pad = CRAFTED_OVERLAY_BODY_PAD
        body_size = CRAFTED_OVERLAY_BODY_SIZE
        body_x = left + pad
        body_w = w - pad * 2
        body_available_h = (top - bottom) - pad * 2

        style = ParagraphStyle(
            'CraftedItemsOverlayBody',
            fontName='Baskerville-Italic',
            fontSize=body_size,
            leading=body_size * CRAFTED_OVERLAY_BODY_LEADING_RATIO,
            alignment=TA_CENTER,
            textColor=color,
        )
        para = Paragraph(body, style)
        para.wrap(body_w, 9999)
        para_h = para.height

        if para_h > body_available_h:
            print(f"WARNING: CraftedItemsOverlay '{title}' body text overflows "
                  f"(needs {para_h:.1f}pt, available {body_available_h:.1f}pt)")

        # Centered: split leftover vertical space evenly above and below.
        body_y = bottom + pad + (body_available_h - para_h) / 2
        para.drawOn(c, body_x, body_y)

    c.restoreState()


class TrackFlowable(Flowable):
    """Renders a CardboardTrack grid inline within phase step content.

    Supports column headers with cost icons, row titles, section dividers,
    and per-slot content images with background fills.
    """

    def __init__(self, track, slots, total_width, body_style, faction_color, engine=None):
        super().__init__()
        self.track = track
        self.total_width = total_width
        self.body_style = body_style
        self.faction_color = faction_color
        self.engine = engine

        # Build grid: grid[row][col] = slot or None
        self.grid = {}
        max_slot_row = 0
        for slot in slots:
            r, c = slot.row, slot.column
            if r not in self.grid:
                self.grid[r] = {}
            self.grid[r][c] = slot
            if r + 1 > max_slot_row:
                max_slot_row = r + 1

        # Prefer the explicit `num_rows` field on the track when available;
        # fall back to the largest row referenced by an existing slot so older
        # tracks that pre-date the field still render.
        explicit_rows = getattr(track, 'num_rows', 0) or 0
        self.num_rows = max(explicit_rows, max_slot_row, 1)

        self.num_cols = track.num_columns
        # Read column headers via the model helper (JSONField, falling back
        # to the legacy pipe-delimited CharField for unmigrated records).
        self._column_headers = list(track.get_column_headers_list()) if hasattr(track, 'get_column_headers_list') else []
        self.has_headers = any(bool(h) for h in self._column_headers)

        # Row titles via the model helper (JSONField; legacy pipe-split fallback).
        self.row_titles = {}
        row_titles_list = list(track.get_row_titles_list()) if hasattr(track, 'get_row_titles_list') else []
        for idx, title in enumerate(row_titles_list):
            if title:
                self.row_titles[idx] = title
        self.has_row_titles = bool(self.row_titles) or bool(getattr(track, 'header_title', ''))

        # Parse column dividers: stored values represent "divider AFTER column
        # N" (set by the editor's spacer buttons, between column N and N+1).
        # Convert to "divider BEFORE column N+1" for our placement loop.
        self.dividers = set()
        if track.column_dividers:
            for col_str in track.column_dividers.split(','):
                col_str = col_str.strip()
                if col_str:
                    n = int(col_str)
                    if 0 <= n < self.num_cols - 1:
                        self.dividers.add(n + 1)

        # Parse row dividers: same convention as column dividers — "AFTER
        # row N", converted to "BEFORE row N+1" for the draw loop.
        self.row_divider_indices = set()
        raw_row_dividers = getattr(track, 'row_dividers', '') or ''
        if raw_row_dividers:
            for row_str in raw_row_dividers.split(','):
                row_str = row_str.strip()
                if row_str:
                    n = int(row_str)
                    if 0 <= n < self.num_rows - 1:
                        self.row_divider_indices.add(n + 1)

        # Count dividers that appear before each column to calculate offset
        self._divider_offsets = {}
        divider_count = 0
        for col_idx in range(self.num_cols):
            if col_idx in self.dividers:
                divider_count += 1
            self._divider_offsets[col_idx] = divider_count

        self.total_dividers = divider_count

        # Track each column's position for zigzag (continuous across dividers)
        self._pos_in_segment = {}
        for col_idx in range(self.num_cols):
            self._pos_in_segment[col_idx] = col_idx

        # Calculate dimensions
        self._calc_dimensions()

    def _calc_dimensions(self):
        # Title height
        self._title_h = TRACK_TITLE_SIZE + TRACK_TITLE_GAP

        # Body height
        self._body_h = 0
        if self.track.body:
            from reportlab.lib.enums import TA_CENTER
            from reportlab.lib.styles import ParagraphStyle
            centered = ParagraphStyle('TrackBody', parent=self.body_style, alignment=TA_CENTER)
            body_markup = format_step_markup(self.track.body, sheet=_sheet_of(self.track))
            para = Paragraph(body_markup, centered)
            _, self._body_h = para.wrap(self.total_width, 9999)
            self._body_h += TRACK_BODY_GAP

        # Header height
        self._header_h = TRACK_COL_HEADER_H if self.has_headers else 0

        # Row title width — start from the per-orientation default, then grow
        # to fit the actual content so titles don't overlap the grid.
        # Horizontal: measure the natural single-line width of each title
        #   (and the in-grid header_title) and bump _row_title_w to fit the
        #   widest, capped at TRACK_ROW_TITLE_MAX_W. Wrapping kicks in only
        #   when text exceeds the cap.
        # Vertical: rotated text's perpendicular thickness equals para_h from
        #   wrap(slot_size, ...). Bump _row_title_w to fit the largest such
        #   thickness, capped at TRACK_ROW_TITLE_VERTICAL_MAX_W.
        self._vertical_row_titles = (getattr(self.track, 'row_title_orientation', 'horizontal') == 'vertical')
        if self.has_row_titles:
            self._row_title_w = self._measure_row_title_width()
        else:
            self._row_title_w = 0

        # Slot size — counter tracks use a smaller diameter
        self._slot_size = TRACK_COUNTER_SIZE if self.track.type == 'counter' else TRACK_SLOT_SIZE
        self._slot_gap = TRACK_SLOT_GAP
        self._divider_w = TRACK_DIVIDER_W

        # Grid dimensions (initial, non-overlapping)
        divider_space = self.total_dividers * self._divider_w
        self._grid_w = (self.num_cols * self._slot_size +
                        (self.num_cols - 1) * self._slot_gap +
                        divider_space)

        # Overlap detection (token tracks only)
        available_w = self.total_width - self._row_title_w
        num_segments = self.total_dividers + 1
        cols_that_step = self.num_cols - num_segments  # columns after the first in each segment

        if (self.track.type == 'token' and self._grid_w > available_w
                and cols_that_step > 0):
            self._is_overlapping = True
            effective_divider_w = TRACK_OVERLAP_DIVIDER_W
            h_step = ((available_w - num_segments * self._slot_size
                       - self.total_dividers * effective_divider_w)
                      / cols_that_step)
            self._overflow_warning = False
            if h_step < TRACK_OVERLAP_MIN_H_STEP:
                self._overflow_warning = True
                h_step = TRACK_OVERLAP_MIN_H_STEP
            self._h_step = h_step
            # Vertical offset so circles don't touch: dy = sqrt(d^2 - dx^2) + clearance
            import math
            d = self._slot_size + TRACK_OVERLAP_CLEARANCE  # effective diameter with clearance
            if h_step < d:
                self._v_zigzag = math.sqrt(d * d - h_step * h_step)
            else:
                self._v_zigzag = 0
            self._v_zigzag = min(self._v_zigzag, TRACK_OVERLAP_MAX_V_OFFSET)
        else:
            self._is_overlapping = False
            self._overflow_warning = False
            self._h_step = self._slot_size + self._slot_gap
            self._v_zigzag = 0
            effective_divider_w = self._divider_w

        # Precompute column x-positions
        self._col_positions = []
        x = self._row_title_w
        for col_idx in range(self.num_cols):
            if col_idx in self.dividers:
                x += effective_divider_w
            self._col_positions.append(x)
            if col_idx < self.num_cols - 1:
                if self._is_overlapping and (col_idx + 1) not in self.dividers:
                    x += self._h_step
                elif self._is_overlapping:
                    x += self._slot_size
                else:
                    x += self._slot_size + self._slot_gap

        # Recalculate grid width from actual positions
        self._grid_w = (self._col_positions[-1] + self._slot_size
                        - self._row_title_w) if self.num_cols > 0 else 0

        self._grid_h = (self.num_rows * self._slot_size +
                        max(0, self.num_rows - 1) * self._slot_gap +
                        self._v_zigzag)

        # Headers stay at a fixed height (no zigzag), no extra padding needed

        # Center the track horizontally when its natural footprint (row titles
        # + grid) is narrower than total_width. Title, headers, dividers, row
        # titles, and slots all shift right by this amount; the body Paragraph
        # already centers itself in self._width and is unaffected.
        natural_track_w = self._row_title_w + self._grid_w
        self._left_pad = max(0, (self.total_width - natural_track_w) / 2)

        self._width = self.total_width
        self._height = self._title_h + self._body_h + self._header_h + self._grid_h

    def _measure_row_title_width(self):
        """Compute the row-title column width needed so titles don't overlap
        the grid. Mirrors the wrap call in `draw()` / `_draw_column_headers()`
        so the measurement matches what gets rendered.

        Horizontal: the natural single-line width of the widest title.
        Vertical: the largest rotated-thickness (perpendicular to reading
        direction) over all rows.

        Includes the in-grid `header_title`, which lives in the same column.
        """
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.lib.enums import TA_CENTER, TA_RIGHT

        slot_size = TRACK_COUNTER_SIZE if self.track.type == 'counter' else TRACK_SLOT_SIZE
        sheet = _sheet_of(self.track)

        titles = list(self.row_titles.values())
        header_title = getattr(self.track, 'header_title', '') or ''
        if header_title:
            titles.append(header_title)
        if not titles:
            return TRACK_ROW_TITLE_VERTICAL_W if self._vertical_row_titles else TRACK_ROW_TITLE_W

        if self._vertical_row_titles:
            style = ParagraphStyle(
                'TrackRowTitleMeasure', parent=self.body_style,
                fontName='Baskerville',
                fontSize=TRACK_ROW_TITLE_FONT_SIZE,
                leading=TRACK_ROW_TITLE_FONT_SIZE + 2,
                alignment=TA_CENTER,
            )
            # The header_title (when vertical) wraps at _header_h instead of
            # slot_size — see _draw_column_headers. Measure it separately.
            row_wrap_w = slot_size
            header_wrap_w = self._header_h
            max_thickness = TRACK_ROW_TITLE_VERTICAL_W
            for t in self.row_titles.values():
                markup = format_step_markup(t, sheet=sheet)
                _, h = Paragraph(markup, style).wrap(row_wrap_w, 9999)
                if h > max_thickness:
                    max_thickness = h
            if header_title:
                markup = format_step_markup(header_title, sheet=sheet)
                _, h = Paragraph(markup, style).wrap(header_wrap_w, 9999)
                if h > max_thickness:
                    max_thickness = h
            return min(max_thickness, TRACK_ROW_TITLE_VERTICAL_MAX_W)

        # Horizontal orientation. Measure each title's natural single-line
        # width (no wrapping) and cap at TRACK_ROW_TITLE_MAX_W. Beyond the
        # cap, draw() will wrap inside the column — but the column has been
        # sized so titles never overflow into the grid for typical content.
        style = ParagraphStyle(
            'TrackRowTitleMeasure', parent=self.body_style,
            fontName='Baskerville',
            fontSize=TRACK_ROW_TITLE_FONT_SIZE,
            leading=TRACK_ROW_TITLE_FONT_SIZE + 2,
            alignment=TA_RIGHT,
        )
        widest = TRACK_ROW_TITLE_W
        # +4 mirrors the inner padding draw() reserves (para.wrap(_row_title_w - 4, ...)).
        budget = 4
        # Probe at a very wide width so the paragraph collapses to one line,
        # then read the line's used width via (maxWidth - extraSpace). This
        # matches the natural-width pattern used elsewhere in this engine
        # (see legend entry sizing around line 2310). For multi-token text,
        # this gives the *whole title's* single-line width — which is what
        # the column needs to reserve to avoid overflow.
        PROBE = TRACK_ROW_TITLE_MAX_W * 8
        for t in titles:
            markup = format_step_markup(t, sheet=sheet)
            probe = Paragraph(markup, style)
            try:
                probe.wrap(PROBE, 9999)
            except Exception:
                continue
            line_widths = []
            if hasattr(probe, 'blPara') and hasattr(probe.blPara, 'lines'):
                for ln in probe.blPara.lines:
                    extra = getattr(ln, 'extraSpace', None)
                    max_w = getattr(ln, 'maxWidth', PROBE)
                    if extra is not None:
                        line_widths.append(max_w - extra)
            try:
                min_w = probe.minWidth()
            except Exception:
                min_w = 0
            natural = max(line_widths) if line_widths else min_w
            if natural + budget > widest:
                widest = min(natural + budget, TRACK_ROW_TITLE_MAX_W)
        return widest

    def wrap(self, availWidth, availHeight):
        return self._width, self._height

    def _col_x(self, col_idx):
        """X position for a column, accounting for row titles, dividers, and overlap."""
        return self._col_positions[col_idx]

    def _col_y_offset(self, col_idx):
        """Vertical zigzag offset for overlapping token tracks."""
        if not self._is_overlapping:
            return 0
        return self._v_zigzag if (self._pos_in_segment[col_idx] % 2 == 1) else 0

    def _row_y(self, row_idx, top_of_grid):
        """Y position (bottom of slot) for a row, from top of grid downward."""
        return top_of_grid - (row_idx + 1) * self._slot_size - row_idx * self._slot_gap

    def draw(self):
        if getattr(self, '_overflow_warning', False):
            print(f"WARNING: Token track '{self.track.title}' cannot fit even with "
                  f"maximum overlap. h_step={self._h_step / inch:.3f}in, "
                  f"minimum={TRACK_OVERLAP_MIN_H_STEP / inch:.3f}in.")
        c = self.canv
        c.saveState()

        h = self._height
        cursor_y = h  # start from top

        # --- Track title (centered) ---
        cursor_y -= TRACK_TITLE_SIZE
        c.setFont('Luminari', TRACK_TITLE_SIZE)
        c.setFillColorRGB(0, 0, 0)
        c.drawCentredString(self.total_width / 2, cursor_y, self.track.title)
        cursor_y -= TRACK_TITLE_GAP

        # --- Body text (centered, if present) ---
        if self.track.body and self._body_h > 0:
            from reportlab.lib.enums import TA_CENTER
            from reportlab.lib.styles import ParagraphStyle
            centered = ParagraphStyle('TrackBody', parent=self.body_style, alignment=TA_CENTER)
            body_markup = format_step_markup(self.track.body, sheet=_sheet_of(self.track))
            para = Paragraph(body_markup, centered)
            para_w, para_h = para.wrap(self._width, 9999)
            para.drawOn(c, 0, cursor_y - para_h)
            cursor_y -= para_h + TRACK_BODY_GAP

        # Center the grid (and everything that uses grid-local x) when the
        # track's natural footprint is narrower than the available width.
        # Title and body are not affected — they're already centered in
        # self._width via their own draw paths above.
        c.saveState()
        if self._left_pad:
            c.translate(self._left_pad, 0)

        # --- Column headers (above) ---
        headers_above = self.has_headers and getattr(self.track, 'header_position', 'above') == 'above'
        if headers_above:
            self._draw_column_headers(c, cursor_y)
            cursor_y -= self._header_h

        # Top of the slot grid
        grid_top_y = cursor_y
        headers_below = self.has_headers and getattr(self.track, 'header_position', 'above') == 'below'

        # --- Section dividers (vertical) ---
        for col_idx in self.dividers:
            if self._is_overlapping:
                prev_right = self._col_x(col_idx - 1) + self._slot_size
                this_left = self._col_x(col_idx)
                div_x = (prev_right + this_left) / 2
            else:
                div_x = self._col_x(col_idx) - self._divider_w / 2 - self._slot_gap / 2
            div_top = grid_top_y + (self._header_h if headers_above else 0)
            div_bottom = grid_top_y - self._grid_h - (TRACK_HEADER_BELOW_PAD + self._header_h if headers_below else 0)
            c.setStrokeColorRGB(0, 0, 0)
            c.setLineWidth(1.0)
            c.line(div_x, div_top, div_x, div_bottom)

        # --- Section dividers (horizontal). Index `n` means "BEFORE row n",
        # i.e. between row n-1 and row n. Span from the left of the row-title
        # column to the right of the grid. Y is the midpoint of the gap
        # between bottom of row n-1 and top of row n.
        for row_idx in self.row_divider_indices:
            bottom_of_upper = self._row_y(row_idx - 1, grid_top_y)
            top_of_lower = self._row_y(row_idx, grid_top_y) + self._slot_size
            div_y = (bottom_of_upper + top_of_lower) / 2
            div_x_left = 0
            div_x_right = self._row_title_w + self._grid_w
            c.setStrokeColorRGB(0, 0, 0)
            c.setLineWidth(1.0)
            c.line(div_x_left, div_y, div_x_right, div_y)

        # --- Row titles ---
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.lib.enums import TA_RIGHT, TA_CENTER
        if self._vertical_row_titles:
            row_title_style = ParagraphStyle(
                'TrackRowTitle', parent=self.body_style,
                fontName='Baskerville',
                fontSize=TRACK_ROW_TITLE_FONT_SIZE,
                leading=TRACK_ROW_TITLE_FONT_SIZE + 2,
                alignment=TA_CENTER,
            )
            for row_idx in range(self.num_rows):
                title = self.row_titles.get(row_idx, '')
                if title:
                    slot_y = self._row_y(row_idx, grid_top_y)
                    title_markup = format_step_markup(title, sheet=_sheet_of(self.track))
                    para = Paragraph(title_markup, row_title_style)
                    # Wrap at slot height so text flows along the rotated axis
                    para_w, para_h = para.wrap(self._slot_size, 9999)
                    c.saveState()
                    # Rotate 90° CCW: text reads bottom-to-top
                    center_x = self._row_title_w / 2
                    center_y = slot_y + self._slot_size / 2
                    c.translate(center_x, center_y)
                    c.rotate(90)
                    para.drawOn(c, -para_w / 2, -para_h / 2)
                    c.restoreState()
        else:
            row_title_style = ParagraphStyle(
                'TrackRowTitle', parent=self.body_style,
                fontName='Baskerville',
                fontSize=TRACK_ROW_TITLE_FONT_SIZE,
                leading=TRACK_ROW_TITLE_FONT_SIZE + 2,
                alignment=TA_RIGHT,
            )
            for row_idx in range(self.num_rows):
                title = self.row_titles.get(row_idx, '')
                if title:
                    slot_y = self._row_y(row_idx, grid_top_y)
                    title_markup = format_step_markup(title, sheet=_sheet_of(self.track))
                    para = Paragraph(title_markup, row_title_style)
                    para_w, para_h = para.wrap(self._row_title_w - 4, 9999)
                    # When the line contains an inline image with valign="middle",
                    # the image is anchored to the text baseline midpoint (low
                    # within the inflated line box), so geometric centering of
                    # the para makes the visible content sit too high. Nudge it
                    # down by the offset between baseline-mid and box-mid.
                    para_y = slot_y + self._slot_size / 2 - para_h / 2
                    if first_line_has_inline_image(para):
                        # The inline image with valign="middle" anchors below the
                        # geometric line center, so the visible content sits
                        # high in the para box. Shift down by half the gap
                        # between image height and the nominal text leading.
                        para_y -= (TRACK_HEADER_ICON_H - row_title_style.leading) / 2
                    para.drawOn(c, 0, para_y)

        # --- Slots ---
        for row_idx in range(self.num_rows):
            for col_idx in range(self.num_cols):
                slot = self.grid.get(row_idx, {}).get(col_idx)
                x = self._col_x(col_idx)
                y = self._row_y(row_idx, grid_top_y) - self._col_y_offset(col_idx)
                self._draw_slot(c, x, y, slot)
                if self.engine is not None:
                    cx_local = x + self._slot_size / 2
                    cy_local = y + self._slot_size / 2
                    abs_x, abs_y = c.absolutePosition(cx_local, cy_local)
                    self.engine.record_slot_snap_point(
                        abs_x, abs_y,
                        track=self.track,
                        row_idx=row_idx,
                        row_title=self.row_titles.get(row_idx),
                    )

        # --- Column headers (below) ---
        if headers_below:
            below_y = grid_top_y - self._grid_h - TRACK_HEADER_BELOW_PAD
            self._draw_column_headers(c, below_y)

        c.restoreState()  # close grid-centering translate

        c.restoreState()

    def _draw_column_headers(self, c, top_y):
        """Draw column headers starting from top_y downward."""
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.lib.enums import TA_CENTER, TA_RIGHT

        # Draw header title in row-title column area
        header_title = getattr(self.track, 'header_title', '') or ''
        if header_title and self._row_title_w > 0:
            title_markup = format_step_markup(header_title, sheet=_sheet_of(self.track))
            if self._vertical_row_titles:
                title_style = ParagraphStyle(
                    'TrackHeaderTitle', parent=self.body_style,
                    fontName='Baskerville',
                    fontSize=TRACK_ROW_TITLE_FONT_SIZE,
                    leading=TRACK_ROW_TITLE_FONT_SIZE + 2,
                    alignment=TA_CENTER,
                )
                para = Paragraph(title_markup, title_style)
                para_w, para_h = para.wrap(self._header_h, 9999)
                c.saveState()
                center_x = self._row_title_w / 2
                center_y = top_y - self._header_h / 2
                c.translate(center_x, center_y)
                c.rotate(90)
                para.drawOn(c, -para_w / 2, -para_h / 2)
                c.restoreState()
            else:
                title_style = ParagraphStyle(
                    'TrackHeaderTitle', parent=self.body_style,
                    fontName='Baskerville',
                    fontSize=TRACK_ROW_TITLE_FONT_SIZE,
                    leading=TRACK_ROW_TITLE_FONT_SIZE + 2,
                    alignment=TA_RIGHT,
                )
                para = Paragraph(title_markup, title_style)
                para_w, para_h = para.wrap(self._row_title_w - 4, 9999)
                para_y = top_y - self._header_h / 2 - para_h / 2
                para.drawOn(c, 0, para_y)

        headers = self._column_headers

        def header_at(idx):
            return headers[idx] if idx < len(headers) else ''

        segments = []
        current = [0]
        for col_idx in range(1, self.num_cols):
            if col_idx in self.dividers:
                segments.append(current)
                current = [col_idx]
            else:
                current.append(col_idx)
        segments.append(current)

        consolidate = bool(self.dividers) and all(
            len({header_at(i) for i in seg}) == 1 for seg in segments
        )

        label_y = top_y - self._header_h / 2 - TRACK_HEADER_FONT_SIZE * 0.35

        if consolidate:
            for seg in segments:
                label = header_at(seg[0])
                left = self._col_x(seg[0])
                right = self._col_x(seg[-1]) + self._slot_size
                center_x = (left + right) / 2
                seg_w = right - left

                processed = format_step_markup(label, sheet=_sheet_of(self.track))
                if '<' in processed:
                    header_style = ParagraphStyle(
                        'TrackHeader', parent=self.body_style,
                        fontName='Baskerville-Bold',
                        fontSize=TRACK_HEADER_FONT_SIZE,
                        leading=TRACK_HEADER_FONT_SIZE + 2,
                        alignment=TA_CENTER,
                    )
                    para = Paragraph(processed, header_style)
                    para_w, para_h = para.wrap(seg_w, 9999)
                    para.drawOn(c, center_x - para_w / 2,
                                top_y - self._header_h / 2 - para_h / 2)
                else:
                    c.setFont('Baskerville-Bold', TRACK_HEADER_FONT_SIZE)
                    c.setFillColorRGB(0, 0, 0)
                    c.drawCentredString(center_x, label_y, label)
        else:
            for col_idx in range(self.num_cols):
                x = self._col_x(col_idx)
                slot_center_x = x + self._slot_size / 2
                label = header_at(col_idx)

                processed = format_step_markup(label, sheet=_sheet_of(self.track))
                if '<' in processed:
                    header_style = ParagraphStyle(
                        'TrackHeader', parent=self.body_style,
                        fontName='Baskerville-Bold',
                        fontSize=TRACK_HEADER_FONT_SIZE,
                        leading=TRACK_HEADER_FONT_SIZE + 2,
                        alignment=TA_CENTER,
                    )
                    para = Paragraph(processed, header_style)
                    para_w, para_h = para.wrap(self._slot_size, 9999)
                    para_x = x
                    para_y = top_y - self._header_h / 2 - para_h / 2
                    para.drawOn(c, para_x, para_y)
                else:
                    c.setFont('Baskerville-Bold', TRACK_HEADER_FONT_SIZE)
                    c.setFillColorRGB(0, 0, 0)
                    c.drawCentredString(slot_center_x, label_y, label)

    def _draw_slot(self, c, x, y, slot):
        """Draw a single slot at position (x, y) bottom-left."""
        s = self._slot_size
        ttype = self.track.type
        is_circle = ttype in ('token', 'counter')

        # --- Background fill ---
        c.saveState()
        bg_drawn = False

        # Try slot background image
        if slot and slot.background_image:
            try:
                self._draw_bg_image(c, slot.background_image.path, x, y, s, is_circle)
                bg_drawn = True
            except (ValueError, FileNotFoundError):
                pass

        # Try track background image
        if not bg_drawn and self.track.background_image:
            try:
                self._draw_bg_image(c, self.track.background_image.path, x, y, s, is_circle)
                bg_drawn = True
            except (ValueError, FileNotFoundError):
                pass

        # Fallback: counter draws a black outline; others fill with faction color
        if not bg_drawn:
            if ttype == 'counter':
                c.setStrokeColorRGB(0, 0, 0)
                c.setLineWidth(TRACK_COUNTER_STROKE_W)
                c.circle(x + s / 2, y + s / 2, s / 2, fill=0, stroke=1)
            else:
                c.setFillColor(self.faction_color)
                c.setFillAlpha(TRACK_SLOT_BG_OPACITY)
                if is_circle:
                    c.circle(x + s / 2, y + s / 2, s / 2, fill=1, stroke=0)
                else:
                    c.roundRect(x, y, s, s, s * 0.15, fill=1, stroke=0)

        c.restoreState()

        # --- Content images ---
        if slot and slot.content:
            keywords = [k.strip() for k in slot.content.split('|') if k.strip()]
            images = []
            slot_sheet = _sheet_of(slot)
            for kw in keywords:
                img_path = _inline_image_path(kw, sheet=slot_sheet)
                if img_path and os.path.exists(img_path):
                    images.append(img_path)
            if images:
                self._draw_content_images(c, x, y, s, images, is_circle)

        # --- Centered text ---
        text = (getattr(slot, 'centered_text', '') or '').strip() if slot else ''
        if text:
            font_name = 'Baskerville'
            max_w = s * 0.80
            font_size = s * 0.5
            text_w = c.stringWidth(text, font_name, font_size)
            if text_w > max_w:
                font_size *= max_w / text_w
            c.saveState()
            c.setFillColorRGB(0, 0, 0)
            c.setFillAlpha(1.0)
            c.setFont(font_name, font_size)
            # Visual-midline shim: cap height is ~0.7 of font size,
            # so dropping the baseline by ~0.35× font size centers it.
            text_y = y + s / 2 - font_size * 0.35
            c.drawCentredString(x + s / 2, text_y, text)
            c.restoreState()

    def _draw_bg_image(self, c, img_path, x, y, s, is_circle):
        """Draw a background image at reduced opacity, clipped to slot shape."""
        c.saveState()
        # Clip to slot shape
        p = c.beginPath()
        if is_circle:
            cx, cy = x + s / 2, y + s / 2
            p.circle(cx, cy, s / 2)
        else:
            p.roundRect(x, y, s, s, s * 0.15)
        c.clipPath(p, stroke=0, fill=0)
        c.setFillAlpha(TRACK_SLOT_BG_OPACITY)
        c.drawImage(img_path, x, y, width=s, height=s, mask='auto',
                    preserveAspectRatio=True, anchor='c')
        c.restoreState()

    def _draw_content_images(self, c, x, y, s, images, is_circle):
        """Draw 1-4 content images positioned within the slot."""
        from PIL import Image as PILImage
        n = len(images)
        pad = s * 0.12  # padding from slot edge
        area = s - 2 * pad

        if n == 1:
            # Centered
            img_size = area * 0.7
            positions = [(x + s / 2 - img_size / 2, y + s / 2 - img_size / 2)]
        elif n == 2:
            # Top-left and bottom-right (diagonal)
            img_size = area * 0.48
            positions = [
                (x + pad, y + s - pad - img_size),           # top-left
                (x + s - pad - img_size, y + pad),           # bottom-right
            ]
        elif n == 3:
            # Top-center, bottom-left, bottom-right
            img_size = area * 0.42
            positions = [
                (x + s / 2 - img_size / 2, y + s - pad - img_size),  # top-center
                (x + pad, y + pad),                                    # bottom-left
                (x + s - pad - img_size, y + pad),                     # bottom-right
            ]
        else:
            # 2x2 grid
            img_size = area * 0.42
            gap = (area - 2 * img_size) / 1
            positions = [
                (x + pad, y + s - pad - img_size),                     # top-left
                (x + pad + img_size + gap, y + s - pad - img_size),    # top-right
                (x + pad, y + pad),                                    # bottom-left
                (x + pad + img_size + gap, y + pad),                   # bottom-right
            ]

        for i, img_path in enumerate(images[:len(positions)]):
            ix, iy = positions[i]
            # Preserve aspect ratio
            with PILImage.open(img_path) as pil_img:
                iw, ih = pil_img.size
            aspect = iw / ih
            if aspect >= 1:
                draw_w = img_size
                draw_h = img_size / aspect
            else:
                draw_h = img_size
                draw_w = img_size * aspect
            # Center within allocated position
            offset_x = (img_size - draw_w) / 2
            offset_y = (img_size - draw_h) / 2
            c.drawImage(img_path, ix + offset_x, iy + offset_y,
                        width=draw_w, height=draw_h, mask='auto')


def _arrow_total_width_for_cost(cost):
    """Return total space for arrow region (icon_gap + arrow + text_gap), or icon-text gap for no arrow."""
    if cost.startswith('item_'):
        return ITEM_ARROW_ICON_GAP + ITEM_ARROW_W + ITEM_ARROW_TEXT_GAP
    if cost.startswith('card_'):
        return CARD_ARROW_ICON_GAP + CARD_ARROW_W + CARD_ARROW_TEXT_GAP
    return ACTION_ICON_TEXT_GAP  # 'action' and 'other' get padding only, no arrow


ACTION_PACK_MAX_LINES = 4  # max wrapped lines allowed when packing actions side-by-side


def _measure_action_widths(action, icon_w, icon_h, icon_path, cost, action_body_style, icon_drawing=None):
    """Measure natural width/height + reusable metadata for a StepAction.

    Returns a dict with:
      natural_w, natural_h:     single-line (no wrap) dimensions
      fixed_w:                  non-text portion (icon_space + arrow_w)
      icon_h_eff:               icon contribution to row height
      markup:                   formatted markup string (reuse to build paragraphs)
      min_text_w:               narrowest the paragraph can render without breaking words
      text_natural_w:           text-only width when not wrapped
    """
    has_icon = bool(icon_path or icon_drawing)
    if cost.startswith('card_'):
        icon_space = MIN_CARD_ICON_W if has_icon else 0
    else:
        icon_space = icon_w if has_icon else 0

    arrow_w = _arrow_total_width_for_cost(cost)
    fixed_w = icon_space + arrow_w
    icon_h_eff = icon_h if has_icon else 0

    markup = format_step_markup(action.text, sheet=_sheet_of(action))
    PROBE_W = 10000
    para = Paragraph(markup, action_body_style)
    para.wrap(PROBE_W, 9999)

    # ReportLab returns one of two line shapes depending on which paragraph
    # backend ran. Plain text uses fast tuples (extraSpace, [words]); rich
    # markup uses FragLine-like objects with maxWidth/extraSpace attributes.
    text_natural_w = 0
    if hasattr(para, 'blPara') and hasattr(para.blPara, 'lines') and para.blPara.lines:
        for line in para.blPara.lines:
            if isinstance(line, tuple):
                extra = line[0] if line else 0
                lw = PROBE_W - extra
            else:
                max_w = getattr(line, 'maxWidth', 0)
                extra = getattr(line, 'extraSpace', 0)
                if max_w:
                    lw = max_w - extra
                else:
                    lw = getattr(line, 'currentWidth', 0)
            if lw > text_natural_w:
                text_natural_w = lw

    natural_w = fixed_w + text_natural_w + 2
    natural_text_h = true_paragraph_height(para, text_natural_w)
    natural_h = max(natural_text_h, icon_h_eff)

    min_text_w = para.minWidth() if hasattr(para, 'minWidth') else text_natural_w * 0.3

    return {
        'natural_w': natural_w,
        'natural_h': natural_h,
        'fixed_w': fixed_w,
        'icon_h_eff': icon_h_eff,
        'markup': markup,
        'min_text_w': min_text_w,
        'text_natural_w': text_natural_w,
    }


def _measure_action_at_width(meta, alloc_w, action_body_style, max_lines=ACTION_PACK_MAX_LINES):
    """Measure an action's rendered height at a specific allocated total width.

    Returns (line_count, total_h) — total_h includes icon_h_eff. Returns
    (None, None) if the text won't fit in `max_lines` at the available text
    width (i.e. would need to break mid-word or exceed the line cap).
    """
    text_w = alloc_w - meta['fixed_w']
    if text_w < meta['min_text_w']:
        return None, None
    probe = Paragraph(meta['markup'], action_body_style)
    probe.wrap(text_w, 9999)
    n_lines = len(probe.blPara.lines) if hasattr(probe, 'blPara') and hasattr(probe.blPara, 'lines') else 1
    if n_lines > max_lines:
        return None, None
    text_h = true_paragraph_height(probe, text_w)
    return n_lines, max(text_h, meta['icon_h_eff'])


class LegendFlowable(Flowable):
    """Renders a Legend block: optional title above two-column rows.

    Each row: left column = Luminari title above image; right column = body
    paragraph. The title color is the faction color when legible against the
    tan page background, otherwise black.
    """

    def __init__(self, legend, total_width, body_style, title_color_hex):
        super().__init__()
        self.legend = legend
        self.total_width = total_width
        self.body_style = body_style
        self.title_color_hex = title_color_hex
        self._rows_data = []   # list of (title, img_path, img_w, img_h, body_para, body_h, row_h)
        self._block_title_h = 0
        self._block_body_para = None
        self._block_body_h = 0
        self._height = 0

    def _required_left_col_width(self):
        """Left-column width needed to fit any no-visual title centered with breathing room.

        Rows that have an image or icon are constrained by the visual width
        (capped at LEGEND_IMAGE_MAX_W), so they never push the column wider.
        Title-only rows can have arbitrary text, so we measure each and pick a
        column wide enough to keep the centered title clear of the body gap.
        """
        widest = 0.0
        for r in self.legend.rows.all():
            title = (r.title or '').strip()
            if not title:
                continue
            has_visual = bool(r.icon) or bool(r.image)
            if has_visual:
                continue
            w = pdfmetrics.stringWidth(title, 'Luminari', LEGEND_ROW_TITLE_FONT_SIZE)
            if w > widest:
                widest = w
        needed = widest + 2 * LEGEND_LEFT_TITLE_BREATHING
        return max(LEGEND_LEFT_COL_W, needed)

    def _measure(self):
        if self._rows_data:
            return
        from reportlab.lib.styles import ParagraphStyle
        left_w = self._required_left_col_width()
        right_w = self.total_width - left_w - LEGEND_IMAGE_BODY_GAP
        if right_w < 0.5 * inch:
            right_w = 0.5 * inch

        body_style = ParagraphStyle(
            'LegendBody',
            parent=self.body_style,
            fontSize=LEGEND_BODY_FONT_SIZE,
            leading=LEGEND_BODY_FONT_SIZE * 1.15,
        )

        rows = list(self.legend.rows.all())
        sheet = _sheet_of(self.legend)
        for r in rows:
            title = (r.title or '').strip()
            # Resolve a single left-column visual: icon takes priority over image.
            img_path = None
            img_w = img_h = 0
            if r.icon:
                candidate = _inline_image_path(r.icon, sheet=sheet)
                if candidate and os.path.exists(candidate):
                    img_path = candidate
            elif r.image:
                try:
                    candidate = r.image.path
                    if os.path.exists(candidate):
                        img_path = candidate
                except Exception:
                    img_path = None
            if img_path:
                try:
                    from PIL import Image as PILImage
                    with PILImage.open(img_path) as pil:
                        aspect = pil.size[0] / pil.size[1] if pil.size[1] else 1
                    img_h = LEGEND_IMAGE_MAX_H
                    img_w = img_h * aspect
                    if img_w > LEGEND_IMAGE_MAX_W:
                        img_w = LEGEND_IMAGE_MAX_W
                        img_h = img_w / aspect if aspect else LEGEND_IMAGE_MAX_H
                except Exception:
                    img_path = None
                    img_w = img_h = 0

            body_markup = format_step_markup(r.body, sheet=sheet) if r.body else ''
            body_para = Paragraph(body_markup or '&nbsp;', body_style)
            body_para.wrap(right_w, 9999)
            body_h = true_paragraph_height(body_para, right_w)

            title_h = LEGEND_ROW_TITLE_FONT_SIZE * 1.1 if title else 0
            title_block_h = title_h + (LEGEND_ROW_TITLE_GAP if title else 0)
            has_left_visual = bool(img_path and img_h)
            if has_left_visual:
                row_h = title_block_h + max(img_h, body_h)
            else:
                # No visual: title and body's first line share a baseline.
                # Vertical extent below row_top is title_cap_h plus body lines
                # that fall below the first baseline.
                title_cap_h = LEGEND_ROW_TITLE_FONT_SIZE * 0.70 if title else 0
                body_cap_h = LEGEND_BODY_FONT_SIZE * 0.70
                row_h = max(title_block_h, title_cap_h + body_h - body_cap_h)
            self._rows_data.append((title, img_path, img_w, img_h, body_para, body_h, row_h, right_w, left_w, title_block_h, has_left_visual))

        block_title = (self.legend.title or '').strip()
        self._block_title_h = (LEGEND_BLOCK_TITLE_SIZE * 1.1) if block_title else 0

        block_body = (self.legend.body or '').strip()
        if block_body:
            from reportlab.lib.enums import TA_CENTER
            centered_body_style = ParagraphStyle(
                'LegendBlockBody',
                parent=self.body_style,
                fontSize=LEGEND_BODY_FONT_SIZE,
                leading=LEGEND_BODY_FONT_SIZE * 1.15,
                alignment=TA_CENTER,
            )
            body_markup = format_step_markup(self.legend.body, sheet=sheet)
            self._block_body_para = Paragraph(body_markup or '&nbsp;', centered_body_style)
            self._block_body_para.wrap(self.total_width, 9999)
            self._block_body_h = true_paragraph_height(self._block_body_para, self.total_width)
        else:
            self._block_body_para = None
            self._block_body_h = 0

        # Gap between title and body, only when both exist.
        self._title_body_gap = LEGEND_BLOCK_BODY_GAP if (block_title and self._block_body_para) else 0
        # Gap below the last header element (body if present, else title) before the first row.
        self._header_bottom_gap = LEGEND_BLOCK_TITLE_GAP if (block_title or self._block_body_para) else 0

        total_rows_h = sum(r[6] for r in self._rows_data)
        gap_h = max(0, len(self._rows_data) - 1) * LEGEND_ROW_GAP
        self._height = (self._block_title_h + self._title_body_gap + self._block_body_h
                        + self._header_bottom_gap + total_rows_h + gap_h)

    def wrap(self, availWidth, availHeight):
        # Always honor the width the parent gives us. LEGEND_MIN_WIDTH is
        # advisory and is enforced upstream by _min_track_width_for_content_box,
        # which sizes the parent content box wide enough. If the parent is still
        # narrower (e.g. phase boxes that don't consult that contract), wrap
        # gracefully to fit instead of overflowing.
        width = min(availWidth, self.total_width) if availWidth else self.total_width
        self.total_width = width
        self._rows_data = []
        self._block_body_para = None
        self._block_body_h = 0
        self._measure()
        return self.total_width, self._height

    def draw(self):
        self._measure()
        c = self.canv
        c.saveState()
        title_color = HexColor(self.title_color_hex)

        y = self._height
        block_title = (self.legend.title or '').strip()
        if block_title:
            c.setFont('Luminari', LEGEND_BLOCK_TITLE_SIZE)
            c.setFillColor(title_color)
            cap_h = LEGEND_BLOCK_TITLE_SIZE * 0.70
            c.drawCentredString(self.total_width / 2.0, y - cap_h, block_title)
            y -= self._block_title_h + self._title_body_gap

        if self._block_body_para is not None:
            self._block_body_para.drawOn(c, 0, y - self._block_body_h)
            y -= self._block_body_h

        y -= self._header_bottom_gap

        for i, (title, img_path, img_w, img_h, body_para, body_h, row_h, right_w, left_w, title_block_h, has_left_visual) in enumerate(self._rows_data):
            row_top = y
            # Row title centered over the image (within left column)
            if title:
                c.setFont('Luminari', LEGEND_ROW_TITLE_FONT_SIZE)
                c.setFillColor(title_color)
                cap_h = LEGEND_ROW_TITLE_FONT_SIZE * 0.70
                c.drawCentredString(left_w / 2.0, row_top - cap_h, title)

            # Image (or icon): centered horizontally in left column, below the title
            image_top_y = row_top - title_block_h
            if has_left_visual:
                image_x = (left_w - img_w) / 2.0
                c.drawImage(img_path, image_x, image_top_y - img_h, width=img_w, height=img_h,
                            preserveAspectRatio=True, mask='auto')

            # Body placement:
            # - With a left visual: top of first body line aligns with image top.
            # - Without: bottom of first body line sits on the title's baseline,
            #   so the title and body's first line share a baseline.
            right_x = left_w + LEGEND_IMAGE_BODY_GAP
            body_leading = LEGEND_BODY_FONT_SIZE * 1.15
            body_cap_h = LEGEND_BODY_FONT_SIZE * 0.70
            body_top_offset = body_leading - body_cap_h
            if has_left_visual:
                body_y = image_top_y - body_h + body_top_offset
            else:
                title_baseline_y = row_top - (LEGEND_ROW_TITLE_FONT_SIZE * 0.70)
                # Paragraph.drawOn places the paragraph's bottom at y. The
                # first line's baseline sits one line up from the bottom: at
                # (y + body_h - body_cap_h) when the leading == cap_h plus
                # descent. Solve for y so first-line baseline == title baseline,
                # then nudge up slightly so the result reads as visually aligned
                # (titles use Luminari, body uses Baskerville; their visual
                # centers don't quite match even at identical baselines).
                body_y = title_baseline_y - body_h + body_cap_h + LEGEND_NOVISUAL_BODY_NUDGE
            body_para.drawOn(c, right_x, body_y)

            y = row_top - row_h
            if i < len(self._rows_data) - 1:
                y -= LEGEND_ROW_GAP

        c.restoreState()


class ScaleFlowable(Flowable):
    """Renders a Scale block horizontally: "1-2:{{1VP}}   3-4:{{2VP}}".

    The colon separator is drawn automatically; the user only enters range and
    result. Result text passes through format_step_markup so {{KEY}} tokens
    render as inline images. Entries flow horizontally and wrap to a new line
    when they overflow availWidth.
    """

    def __init__(self, scale, total_width, body_style, title_color_hex, centered=False):
        super().__init__()
        self.scale = scale
        self.total_width = total_width
        self.body_style = body_style
        self.title_color_hex = title_color_hex
        self.centered = centered
        self._lines = []   # list of list of paragraphs (each line = a list)
        self._line_heights = []
        self._block_title_h = 0
        self._height = 0
        self._entry_paras = []   # parallel: (range_para, result_para, range_w, result_w, h)

    def _measure(self):
        if self._lines:
            return
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.lib.enums import TA_LEFT
        entry_style = ParagraphStyle(
            'ScaleEntry',
            parent=self.body_style,
            fontName='Baskerville',
            fontSize=SCALE_FONT_SIZE,
            leading=SCALE_FONT_SIZE * 1.15,
            alignment=TA_LEFT,
        )

        rows = list(self.scale.rows.all())
        # Build paragraph for each entry: "<range>: <result_markup>"
        for r in rows:
            rng = (r.range or '').strip()
            res_markup = _replace_inline_images(r.result, img_height=SCALE_INLINE_IMG_H, sheet=_sheet_of(self.scale)) if r.result else ''
            entry_markup = (
                f'<font face="Baskerville">{_xml_escape(rng)}:</font>&nbsp;{res_markup}'
            )
            # Measure natural single-line width on a probe paragraph (wrap at a
            # huge width and read maxWidth - extraSpace), then build the actual
            # paragraph at that width so range+result stay inline.
            PROBE = 9999
            probe = Paragraph(entry_markup, entry_style)
            probe.wrap(PROBE, 9999)
            line_widths = []
            if hasattr(probe, 'blPara') and hasattr(probe.blPara, 'lines'):
                for ln in probe.blPara.lines:
                    extra = getattr(ln, 'extraSpace', None)
                    max_w = getattr(ln, 'maxWidth', PROBE)
                    if extra is not None:
                        line_widths.append(max_w - extra)
            w_natural = max(line_widths) if line_widths else probe.minWidth()
            # Add a small safety margin so the wrapper doesn't push to 2 lines
            w_natural = min(w_natural + 1, self.total_width)
            para = Paragraph(entry_markup, entry_style)
            _, h_natural = para.wrap(w_natural, 9999)
            self._entry_paras.append((para, w_natural, h_natural))

        # Pack entries into lines that fit in total_width
        avail = self.total_width
        line = []
        line_w = 0
        line_h = 0
        for para, w, h in self._entry_paras:
            add_w = w if not line else (w + SCALE_ENTRY_GAP)
            if line and line_w + add_w > avail:
                self._lines.append(line)
                self._line_heights.append(line_h)
                line = [(para, w, h)]
                line_w = w
                line_h = h
            else:
                line.append((para, w, h))
                line_w += add_w
                line_h = max(line_h, h)
        if line:
            self._lines.append(line)
            self._line_heights.append(line_h)

        block_title = (self.scale.title or '').strip()
        self._block_title_h = (SCALE_BLOCK_TITLE_SIZE * 1.1 + SCALE_TOP_PAD) if block_title else 0

        total_lines_h = sum(self._line_heights)
        gap_h = max(0, len(self._lines) - 1) * SCALE_ROW_GAP
        self._height = self._block_title_h + total_lines_h + gap_h + SCALE_BOTTOM_PAD

    def wrap(self, availWidth, availHeight):
        self.total_width = min(availWidth, self.total_width) if availWidth else self.total_width
        self._lines = []
        self._line_heights = []
        self._entry_paras = []
        self._measure()
        return self.total_width, self._height

    def draw(self):
        self._measure()
        c = self.canv
        c.saveState()

        y = self._height
        block_title = (self.scale.title or '').strip()
        if block_title:
            c.setFont('Luminari', SCALE_BLOCK_TITLE_SIZE)
            c.setFillColor(HexColor(self.title_color_hex))
            cap_h = SCALE_BLOCK_TITLE_SIZE * 0.70
            if self.centered:
                c.drawCentredString(self.total_width / 2, y - cap_h, block_title)
            else:
                c.drawString(0, y - cap_h, block_title)
            y -= self._block_title_h

        for line, line_h in zip(self._lines, self._line_heights):
            line_w = sum(w for _, w, _ in line) + max(0, len(line) - 1) * SCALE_ENTRY_GAP
            x = (self.total_width - line_w) / 2 if self.centered else 0
            for i, (para, w, h) in enumerate(line):
                if i > 0:
                    x += SCALE_ENTRY_GAP
                para.drawOn(c, x, y - line_h)
                x += w
            y -= line_h + SCALE_ROW_GAP

        c.restoreState()


class StepActionFlowable(Flowable):
    """Renders a single StepAction row: [cost_icon] → [action_text]"""

    def __init__(self, icon_path, text_paragraph, icon_w, icon_h, total_width, cost, icon_drawing=None):
        super().__init__()
        self.icon_path = icon_path
        self.icon_drawing = icon_drawing
        self.text_paragraph = text_paragraph
        self.icon_w = icon_w
        self.icon_h = icon_h
        self.total_width = total_width
        self.total_arrow_w = _arrow_total_width_for_cost(cost)
        self.draw_arrow = cost.startswith('item_') or cost.startswith('card_')
        if cost.startswith('item_'):
            self.icon_gap = ITEM_ARROW_ICON_GAP
            self.arrow_w = ITEM_ARROW_W
            self.text_gap = ITEM_ARROW_TEXT_GAP
            self.head_size = ITEM_ARROW_HEAD_SIZE
            self.head_spread = ITEM_ARROW_HEAD_SPREAD
        elif cost.startswith('card_'):
            self.icon_gap = CARD_ARROW_ICON_GAP
            self.arrow_w = CARD_ARROW_W
            self.text_gap = CARD_ARROW_TEXT_GAP
            self.head_size = CARD_ARROW_HEAD_SIZE
            self.head_spread = CARD_ARROW_HEAD_SPREAD
        else:
            self.icon_gap = 0
            self.arrow_w = 0
            self.text_gap = 0
            self.head_size = 0
            self.head_spread = 0
        self.is_card = cost.startswith('card_')
        has_icon = bool(icon_path or icon_drawing)
        # Calculate text area width
        # For card costs, use MIN_CARD_ICON_W as the icon column width so all
        # card actions share the same text x-position regardless of icon width.
        # Wider icons overflow left into the margin.
        if self.is_card:
            icon_space = MIN_CARD_ICON_W if has_icon else 0
        else:
            icon_space = self.icon_w if has_icon else 0
        self.icon_space = icon_space
        self.text_w = total_width - icon_space - self.total_arrow_w
        _, self.wrap_h = text_paragraph.wrap(self.text_w, 9999)
        self.true_h = true_paragraph_height(text_paragraph, self.text_w)
        self.wrap_h = text_paragraph.height  # updated by tightening in true_paragraph_height

        # Determine first line height for icon/arrow vertical alignment
        self.first_line_h = self._get_first_line_height(text_paragraph)

        # If the icon is taller than the first line, we need extra padding above
        # so the text drops down to align its first line center with the icon center
        icon_h_eff = self.icon_h if has_icon else 0
        self.top_pad = max(0, (icon_h_eff - self.first_line_h) / 2)

        # Total height must fit both the text (with top padding) and the full icon + nudge
        nudge = self.first_line_h * ACTION_ICON_Y_NUDGE
        self._height = max(self.top_pad + self.true_h, icon_h_eff + nudge)

    @staticmethod
    def _get_first_line_height(para):
        """Get the text-only height of the first line from a wrapped paragraph.

        Ignores inline image contributions so arrows target the visual center
        of the text, not the inflated line box. Accounts for mixed font sizes
        (e.g. ##title## markup at 15pt alongside 8pt body text).
        """
        if hasattr(para, 'blPara') and hasattr(para.blPara, 'lines') and para.blPara.lines:
            line = para.blPara.lines[0]
            # FragLine (autoLeading='max') — scan text fragments for max font size
            if hasattr(line, 'words'):
                max_size = 0
                for frag in line.words:
                    # Skip inline images (they have cbDefn with kind='img')
                    cb = getattr(frag, 'cbDefn', None)
                    if cb and getattr(cb, 'kind', None) == 'img':
                        continue
                    fs = getattr(frag, 'fontSize', 0)
                    if fs > max_size:
                        max_size = fs
                if max_size > 0:
                    return max_size
            # Tuple-style line (simple paragraphs without autoLeading)
            ascent = getattr(line, 'ascent', None)
            descent = getattr(line, 'descent', None)
            if ascent is not None and descent is not None:
                return ascent - descent
            # Plain (kind=0) line: tuple (width, [words]). ReportLab collapses
            # paragraphs with a single uniform style to this form even with
            # autoLeading='max'. Use the paragraph's own fontSize so a header-
            # only run reports its enlarged size, not the base style leading.
            blp_size = getattr(para.blPara, 'fontSize', None)
            if blp_size:
                return blp_size
        return para.style.leading

    def wrap(self, availWidth, availHeight):
        return self.total_width, self._height

    def draw(self):
        c = self.canv
        c.saveState()

        icon_space = self.icon_space

        # First line center Y (measured from bottom of flowable)
        # Nudge down by a fraction of the first line height to compensate for font ascender padding
        nudge = self.first_line_h * ACTION_ICON_Y_NUDGE
        first_line_center_y = self._height - self.top_pad - self.first_line_h / 2 - nudge

        # TEMP DEBUG: outline the card-icon column for card actions.
        if self.is_card and DEBUG_CARD_ICON_COLUMN:
            c.saveState()
            c.setStrokeColorRGB(1, 0, 0)
            c.setLineWidth(0.5)
            c.rect(0, 0, MIN_CARD_ICON_W, self._height, stroke=1, fill=0)
            c.restoreState()

        # Draw cost icon (centered on first line)
        if self.icon_path or self.icon_drawing:
            if self.is_card:
                # Center-align card icon on MIN_CARD_ICON_W midpoint;
                # wider icons extend left (negative x) into margin
                center_x = MIN_CARD_ICON_W / 2
                icon_x = center_x - self.icon_w / 2
            else:
                icon_x = (icon_space - self.icon_w) / 2
            icon_y = first_line_center_y - self.icon_h / 2
            if self.icon_drawing is not None:
                renderPDF.draw(self.icon_drawing, c, icon_x, icon_y)
            else:
                c.drawImage(self.icon_path, icon_x, icon_y,
                            width=self.icon_w, height=self.icon_h, mask='auto')

        # Draw arrow centered on first line (only for item and card costs)
        if self.draw_arrow:
            if self.is_card:
                # Arrow starts from icon right edge; shorter for wider icons
                center_x = MIN_CARD_ICON_W / 2
                arrow_start_x = center_x + self.icon_w / 2 + self.icon_gap
                dynamic_arrow_w = self.arrow_w - (self.icon_w - MIN_CARD_ICON_W) / 2
                arrow_end_x = arrow_start_x + dynamic_arrow_w
            else:
                arrow_start_x = icon_space + self.icon_gap
                arrow_end_x = arrow_start_x + self.arrow_w
            arrow_mid_y = first_line_center_y

            c.setStrokeColorRGB(0.15, 0.15, 0.15)
            c.setLineWidth(2.3)

            # Straight line
            c.line(arrow_start_x, arrow_mid_y, arrow_end_x - self.head_size, arrow_mid_y)

            # Arrowhead triangle
            c.setFillColorRGB(0.15, 0.15, 0.15)
            p2 = c.beginPath()
            p2.moveTo(arrow_end_x, arrow_mid_y)
            p2.lineTo(arrow_end_x - self.head_size, arrow_mid_y + self.head_size * self.head_spread)
            p2.lineTo(arrow_end_x - self.head_size, arrow_mid_y - self.head_size * self.head_spread)
            p2.close()
            c.drawPath(p2, fill=1, stroke=0)

        # Draw text paragraph
        text_x = icon_space + self.total_arrow_w
        text_y = self._height - self.top_pad - self.wrap_h
        self.text_paragraph.drawOn(c, text_x, text_y)

        c.restoreState()


class CardGroupFlowable(Flowable):
    """Renders a group of card actions sharing the same cost icon.

    One icon on the left with bracket-style arrows branching to each action's text.
    """

    def __init__(self, icon_path, text_paragraphs, icon_w, icon_h, total_width, cost, icon_drawing=None):
        super().__init__()
        self.icon_path = icon_path
        self.icon_drawing = icon_drawing
        self.icon_w = icon_w
        self.icon_h = icon_h
        self.total_width = total_width
        self.cost = cost
        self.n = len(text_paragraphs)

        # Layout: use MIN_CARD_ICON_W as column width for all card types
        self.icon_space = MIN_CARD_ICON_W
        self.total_arrow_w = CARD_ARROW_ICON_GAP + CARD_ARROW_W + CARD_ARROW_TEXT_GAP
        self.text_w = total_width - self.icon_space - self.total_arrow_w

        # Wrap each paragraph and compute per-row metrics
        self.paragraphs = text_paragraphs
        self.wrap_heights = []
        self.true_heights = []
        self.first_line_heights = []
        for para in text_paragraphs:
            _, wh = para.wrap(self.text_w, 9999)
            th = true_paragraph_height(para, self.text_w)
            wh = para.height  # updated by tightening in true_paragraph_height
            flh = StepActionFlowable._get_first_line_height(para)
            self.wrap_heights.append(wh)
            self.true_heights.append(th)
            self.first_line_heights.append(flh)

        # Total height: sum of text block heights + gaps between them
        text_total = sum(self.true_heights) + ACTION_ROW_GAP * (self.n - 1)
        # The icon must also fit; use the max of text total vs icon height
        self._height = max(text_total, self.icon_h)

    def wrap(self, availWidth, availHeight):
        return self.total_width, self._height

    def draw(self):
        c = self.canv
        c.saveState()

        text_x = self.icon_space + self.total_arrow_w

        # TEMP DEBUG: outline the card-icon column (whole grouped block).
        if DEBUG_CARD_ICON_COLUMN:
            c.saveState()
            c.setStrokeColorRGB(1, 0, 0)
            c.setLineWidth(0.5)
            c.rect(0, 0, MIN_CARD_ICON_W, self._height, stroke=1, fill=0)
            c.restoreState()

        # Compute Y positions for each text block (top-to-bottom stacking)
        # and the first-line center for each (used for arrow targeting)
        block_top_y = self._height  # start from top
        first_line_centers = []
        text_draw_positions = []

        for i in range(self.n):
            nudge = self.first_line_heights[i] * ACTION_ICON_Y_NUDGE
            flc_y = block_top_y - self.first_line_heights[i] / 2 - nudge
            first_line_centers.append(flc_y)
            text_draw_positions.append(block_top_y - self.wrap_heights[i])
            # Move down for next block
            block_top_y -= self.true_heights[i] + ACTION_ROW_GAP

        # Determine icon vertical center
        if self.n % 2 == 1:
            # Odd: icon center aligns with middle action's first line center
            mid_idx = self.n // 2
            icon_center_y = first_line_centers[mid_idx]
        else:
            # Even: icon center between two middle actions' first line centers
            upper_idx = self.n // 2 - 1
            lower_idx = self.n // 2
            icon_center_y = (first_line_centers[upper_idx] + first_line_centers[lower_idx]) / 2

        # Draw icon (centered horizontally on MIN_CARD_ICON_W midpoint)
        if self.icon_path or self.icon_drawing:
            center_x = MIN_CARD_ICON_W / 2
            icon_x = center_x - self.icon_w / 2
            icon_y = icon_center_y - self.icon_h / 2
            if self.icon_drawing is not None:
                renderPDF.draw(self.icon_drawing, c, icon_x, icon_y)
            else:
                c.drawImage(self.icon_path, icon_x, icon_y,
                            width=self.icon_w, height=self.icon_h, mask='auto')

        # Arrow geometry
        center_x = MIN_CARD_ICON_W / 2
        arrow_start_x = center_x + self.icon_w / 2 + CARD_ARROW_ICON_GAP
        dynamic_arrow_w = CARD_ARROW_W - (self.icon_w - MIN_CARD_ICON_W) / 2
        arrow_end_x = arrow_start_x + dynamic_arrow_w

        # Bracket arrow layout:
        #   [horiz start] -> [quarter curve] -> [vertical] -> [quarter curve] -> [flat end -> head]
        # All arrows share the same vertical turn x so they visually align.
        # Left flat is a ratio of arrow width (0.1242 gives 0.05" for non-bird arrows).
        LEFT_FLAT_RATIO = 0.1242
        MAX_CURVE_R = 7.5             # max quarter-circle radius for bends
        left_flat = dynamic_arrow_w * LEFT_FLAT_RATIO

        c.setStrokeColorRGB(0.15, 0.15, 0.15)
        c.setLineWidth(2.3)
        c.setFillColorRGB(0.15, 0.15, 0.15)

        # Find the max y-distance any arrow travels (for scaling curve_r)
        max_y_dist = max(
            abs(first_line_centers[i] - icon_center_y)
            for i in range(self.n)
            if not (self.n % 2 == 1 and i == self.n // 2)
        ) if self.n > 1 else 0

        for i in range(self.n):
            target_y = first_line_centers[i]
            is_straight = (self.n % 2 == 1 and i == self.n // 2)

            if is_straight:
                # Straight arrow for middle action (odd count)
                c.line(arrow_start_x, target_y, arrow_end_x - CARD_ARROW_HEAD_SIZE, target_y)
            else:
                # Bracket-style arrow: horiz → curve → vert → curve → horiz
                dy = target_y - icon_center_y
                sign = 1 if dy > 0 else -1
                abs_dy = abs(dy)

                # Scale curve_r based on y-distance relative to the max
                # Smaller distances get tighter curves
                if max_y_dist > 0:
                    curve_r = min(MAX_CURVE_R, MAX_CURVE_R * (abs_dy / max_y_dist))
                else:
                    curve_r = MAX_CURVE_R
                # Don't let the radius exceed half the y-distance
                curve_r = min(curve_r, abs_dy / 2)

                turn_x = arrow_start_x + left_flat + curve_r

                path = c.beginPath()
                # Start horizontal from icon at icon_center_y
                path.moveTo(arrow_start_x, icon_center_y)
                # Horizontal segment to left curve start
                horiz_end_x = turn_x - curve_r
                path.lineTo(horiz_end_x, icon_center_y)
                # Quarter-circle: horizontal → vertical (toward target)
                # Bezier approx of quarter circle: control points at ~0.5523 * r
                k = 0.5523 * curve_r
                path.curveTo(
                    horiz_end_x + k, icon_center_y,
                    turn_x, icon_center_y + sign * (curve_r - k),
                    turn_x, icon_center_y + sign * curve_r,
                )
                # Vertical segment
                vert_end_y = target_y - sign * curve_r
                path.lineTo(turn_x, vert_end_y)
                # Quarter-circle: vertical → horizontal (toward text)
                flat_start_x = turn_x + curve_r
                path.curveTo(
                    turn_x, vert_end_y + sign * (curve_r - k),
                    turn_x + (curve_r - k), target_y,
                    flat_start_x, target_y,
                )
                # Flat end to arrowhead
                path.lineTo(arrow_end_x - CARD_ARROW_HEAD_SIZE, target_y)
                c.drawPath(path, fill=0, stroke=1)

            # Arrowhead triangle
            p2 = c.beginPath()
            p2.moveTo(arrow_end_x, target_y)
            p2.lineTo(arrow_end_x - CARD_ARROW_HEAD_SIZE,
                      target_y + CARD_ARROW_HEAD_SIZE * CARD_ARROW_HEAD_SPREAD)
            p2.lineTo(arrow_end_x - CARD_ARROW_HEAD_SIZE,
                      target_y - CARD_ARROW_HEAD_SIZE * CARD_ARROW_HEAD_SPREAD)
            p2.close()
            c.drawPath(p2, fill=1, stroke=0)

        # Draw each text paragraph
        for i in range(self.n):
            self.paragraphs[i].drawOn(c, text_x, text_draw_positions[i])

        c.restoreState()


def _rects_overlap(x1, y1, w1, h1, x2, y2, w2, h2):
    """Return True if two axis-aligned rectangles overlap."""
    return not (x1 + w1 <= x2 or x2 + w2 <= x1 or y1 + h1 <= y2 or y2 + h2 <= y1)


def _relative_luminance(hex_color):
    """WCAG relative luminance for a hex color. Returns None on parse failure."""
    h = hex_color.lstrip('#')
    if len(h) == 3:
        h = ''.join(ch * 2 for ch in h)
    try:
        r = int(h[0:2], 16) / 255.0
        g = int(h[2:4], 16) / 255.0
        b = int(h[4:6], 16) / 255.0
    except (ValueError, IndexError):
        return None

    def channel(v):
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4

    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def _contrast_ratio(hex_a, hex_b):
    """WCAG contrast ratio between two hex colors (>= 1.0)."""
    la = _relative_luminance(hex_a)
    lb = _relative_luminance(hex_b)
    if la is None or lb is None:
        return 1.0
    lighter, darker = max(la, lb), min(la, lb)
    return (lighter + 0.05) / (darker + 0.05)


def _is_color_legible_on(fg_hex, bg_hex, min_ratio=1.9):
    """True if fg_hex has sufficient contrast against bg_hex."""
    return _contrast_ratio(fg_hex, bg_hex) >= min_ratio


def _pick_legible_on(primary_hex, secondary_hex, bg_hex, min_ratio=1.9, fallback='#000000'):
    """Return primary if legible on bg; else secondary if legible; else fallback."""
    if primary_hex and _is_color_legible_on(primary_hex, bg_hex, min_ratio):
        return primary_hex
    if secondary_hex and _is_color_legible_on(secondary_hex, bg_hex, min_ratio):
        return secondary_hex
    return fallback


def _is_white_text_legible(hex_color, min_ratio=1.9):
    """Mirror of the JS isWhiteTextLegible() in faction_attributes.html.

    Returns True if white text on hex_color background meets the contrast
    threshold (WCAG-style relative luminance ratio).
    """
    lum = _relative_luminance(hex_color)
    if lum is None:
        return True
    ratio = (1.0 + 0.05) / (lum + 0.05)
    return ratio >= min_ratio


class SheetLayoutEngine:

    def __init__(self, faction_sheet):
        self.sheet = faction_sheet
        self.faction_color = HexColor(self.sheet.faction.color or '#5B4A8A')
        self.collected_snap_points = []
        from the_forge.models import CardboardTrack, FactionSheet, PhaseStep, StepAction
        from django.db.models import Prefetch
        if isinstance(faction_sheet, FactionSheet):
            all_steps = list(faction_sheet.phase_steps.prefetch_related(
                Prefetch('actions', queryset=StepAction.objects.order_by('order')),
                Prefetch('tracks', queryset=CardboardTrack.objects.prefetch_related('slots').order_by('order')),
            ).all())
        else:
            all_steps = list(faction_sheet.phase_steps.all())
        self.steps = [s for s in all_steps if s.phase != 'other']
        self.phases_grouped = {
            phase: list(steps)
            for phase, steps in groupby(self.steps, key=lambda s: s.phase)
        }

        # Load content boxes with their 'other' phase steps
        self.content_boxes = list(
            faction_sheet.content_boxes.prefetch_related(
                Prefetch('steps',
                    queryset=PhaseStep.objects.filter(phase='other').prefetch_related(
                        Prefetch('actions', queryset=StepAction.objects.order_by('order')),
                        Prefetch('tracks', queryset=CardboardTrack.objects.prefetch_related('slots').order_by('order')),
                        'boxes',
                    ).order_by('number')
                )
            ).order_by('order')
        )

        decree_sections = list(faction_sheet.decrees.prefetch_related('card_slots').all())
        self.decree_section = next(iter(decree_sections), None)
        self.card_piles = list(faction_sheet.card_piles.all())

        # If any slot body wraps, the slot row shifts up — the minimum amount of
        # the decree visible on the page must grow by the same shift so the
        # lifted slots stay on the page.
        self.decree_wrap_shift = 0.0
        if self.decree_section:
            for slot in self.decree_section.card_slots.all():
                if slot.body and len(self._wrap_slot_body(slot.body)) > 1:
                    self.decree_wrap_shift = DECREE_SLOT_WRAP_SHIFT
                    break
        # Tighten the minimum when the section has no title and no body — there's
        # no text to keep visible, so the decree can hide further off-page.
        # The slot row is anchored to the decree image's top (slot_y =
        # decree_img_top - DECREE_SLOT_Y_OFFSET), so shrinking decree_slide pulls
        # the slots up too. We don't want that — the slots should stay roughly
        # where they'd be at the standard offset. _decree_slot_extra_drop carries
        # the compensation: slot draw subtracts it so slots move back down.
        self._decree_slot_extra_drop = 0.0
        any_slot_body = bool(self.decree_section and any(
            slot.body for slot in self.decree_section.card_slots.all()
        ))
        if self.decree_section and not (self.decree_section.title or self.decree_section.body):
            base_min_offset = DECREE_MIN_OFFSET_NO_TEXT
            full_drop = DECREE_MIN_OFFSET - DECREE_MIN_OFFSET_NO_TEXT
            # If any slot has body text, give back vertical space below the slot
            # so body lines don't clip past the page bottom. Wrapped bodies need
            # an extra line-gap of headroom on top of that.
            give_back = 0.0
            if any_slot_body:
                give_back += DECREE_SLOT_BODY_OFFSET
                if self.decree_wrap_shift:
                    give_back += DECREE_SLOT_BODY_LINE_GAP
            self._decree_slot_extra_drop = max(0.0, full_drop - give_back)
        else:
            base_min_offset = DECREE_MIN_OFFSET
        self.decree_min_offset = base_min_offset + self.decree_wrap_shift

        # decree_slide = how far the decree image slides down onto the page
        # (0 = fully hidden above page, draw_h = fully visible)
        self.decree_slide = self.decree_min_offset if self.decree_section else 0.0
        # If a decree_y override is set, derive the slide from it so the header
        # band, title bar, and phase area all anchor to the overridden position.
        if self.decree_section:
            ov_decree_y = self._override(self.sheet, 'decree_y_h', 'decree_y_v')
            if ov_decree_y is not None:
                self.decree_slide = PAGE_H - ov_decree_y * inch

        # Faction Top Bar image top edge starts at TOP_MARGIN (pushed down by decree)
        self.faction_top_bar_top = PAGE_H - TOP_MARGIN - self.decree_slide
        self.faction_top_bar_w = PAGE_W - 0.7 * inch
        self.faction_top_bar_h = self.faction_top_bar_w * (370 / 2106)

        # Color bar: same top-down calculation as the SVG, then nudge below it
        self.title_bar_y = PAGE_H - self.decree_slide - TOP_MARGIN - FACTION_TOP_BAR_NUDGE - TITLE_BAR_H

        # Phase area: top is below the Faction Top Bar image, bottom is at margin
        self.phases_top_y = self.faction_top_bar_top - self.faction_top_bar_h
        self.phases_bottom_y = BOTTOM_MARGIN

        # Ability-bar dynamic height delta (set by _draw_ability_boxes).
        # Positive: bar grew; negative: bar shrank. Shifts phases_top_y in lockstep.
        self.ability_bar_extra_h = 0.0
        self.ability_bar_h_actual = ABILITY_BAR_H

        self._placed_boxes = []
        self._phases_rect = None

        self._init_styles()
        # Step numbers and ability berries sit on the Phase_Box.svg tan fill;
        # if the faction color fails contrast against that tan, fall back to the
        # secondary_color, then to black.
        faction_color_hex = self.sheet.faction.color or '#5B4A8A'
        secondary_hex = self.sheet.faction.secondary_color or None
        on_tan_hex = _pick_legible_on(faction_color_hex, secondary_hex, PHASE_BOX_TAN)
        self._on_tan_hex = on_tan_hex
        self._ability_icon = self._load_colored_svg(ABILITY_BERRY_SVG, on_tan_hex, 0.5 * inch)
        self._meeple_action_drawing = None

        # Preload numbered SVGs (0-9) for phase steps, at natural size
        self._phase_number_svgs = {}
        lang_code = _lang_code_for(self.sheet)
        for n in range(10):
            svg_path = _numbered_svg_path(PHASE_NUMBER_SVG_DIR, n, lang_code)
            if os.path.exists(svg_path):
                self._phase_number_svgs[n] = self._load_colored_svg(svg_path, on_tan_hex)

    def _resolve_element_color(self, choice):
        """Map an ElementColor choice value to a hex string."""
        from the_forge.models import ElementColor
        if choice == ElementColor.WHITE:
            return '#FFFFFF'
        if choice == ElementColor.BLACK:
            return '#000000'
        if choice == ElementColor.FACTION:
            return self.sheet.faction.color or '#5B4A8A'
        if choice == ElementColor.SECONDARY:
            return self.sheet.faction.secondary_color or '#000000'
        return '#000000'

    def _resolve_card_pile_screen_color(self, pile):
        """Hex color for a CardPile background screen, picked to contrast
        with the resolved element_color: dark text → white screen, pale
        text → black screen."""
        text_hex = self._resolve_element_color(pile.element_color)
        return '#FFFFFF' if _is_color_legible_on(text_hex, '#FFFFFF') else '#000000'

    def _load_colored_svg(self, svg_path, color_hex, fit_size=None):
        with open(svg_path, 'r') as f:
            svg_content = f.read()
        svg_content = svg_content.replace('#000000', color_hex)
        with tempfile.NamedTemporaryFile(suffix='.svg', mode='w', delete=False) as tmp:
            tmp.write(svg_content)
            tmp_path = tmp.name
        drawing = svg2rlg(tmp_path)
        os.unlink(tmp_path)
        if drawing and fit_size:
            scale = min(fit_size / drawing.width, fit_size / drawing.height)
            drawing.width *= scale
            drawing.height *= scale
            drawing.scale(scale, scale)
        return drawing

    def _get_meeple_action_icon(self):
        """Return a cached vector Drawing of meeple.svg recolored with
        self._on_tan_hex, scaled uniformly to ACTION_DEFAULT_W width.
        Returns (drawing, draw_w, draw_h) or (None, 0, 0) if SVG can't load.
        """
        if self._meeple_action_drawing is not None:
            d = self._meeple_action_drawing
            return d, d.width, d.height

        with open(MEEPLE_SVG, 'r') as f:
            svg_content = f.read()
        svg_content = svg_content.replace('#000000', self._on_tan_hex)
        with tempfile.NamedTemporaryFile(suffix='.svg', mode='w', delete=False) as tmp:
            tmp.write(svg_content)
            tmp_svg_path = tmp.name
        drawing = svg2rlg(tmp_svg_path)
        os.unlink(tmp_svg_path)
        if drawing is None:
            return None, 0, 0

        scale = ACTION_DEFAULT_W / drawing.width
        drawing.width *= scale
        drawing.height *= scale
        drawing.scale(scale, scale)
        self._meeple_action_drawing = drawing
        return drawing, drawing.width, drawing.height

    def _load_phase_box_svg(self, target_w, target_h, path=None):
        """Load a tan-box SVG stretched to target_w x target_h (non-uniform scaling).

        path defaults to PHASE_BOX_SVG; pass an alternate candidate to load it
        instead (used by the aspect-matching picker).
        """
        drawing = svg2rlg(path or PHASE_BOX_SVG)
        if drawing is None:
            return None
        sx = target_w / drawing.width
        sy = target_h / drawing.height
        drawing.width = target_w
        drawing.height = target_h
        drawing.scale(sx, sy)
        return drawing

    def _load_card_pile_svg(self, color_hex, target_w, target_h):
        """Load card_pile.svg recolored to color_hex and stretched to target size."""
        drawing = self._load_colored_svg(CARD_PILE_SVG, color_hex)
        if drawing is None:
            return None
        sx = target_w / drawing.width
        sy = target_h / drawing.height
        drawing.width = target_w
        drawing.height = target_h
        drawing.scale(sx, sy)
        return drawing

    def _draw_phase_box(self, c, x, y, target_w, target_h, rotated=None):
        """Draw a tan-box background at (x, y) bottom-left filling target_w x
        target_h. The candidate _Box.svg whose natural (or 90°-rotated) aspect
        is closest to target_w/target_h is chosen automatically to minimize
        visible stretch from non-uniform scaling.

        The legacy `rotated` keyword is accepted but ignored — orientation is
        now picked from aspect.
        """
        c.set_layer_tag('box')
        path, use_rotated = _pick_box_svg(target_w, target_h)
        if not use_rotated:
            drawing = self._load_phase_box_svg(target_w, target_h, path=path)
            if drawing:
                renderPDF.draw(drawing, c, x, y)
        else:
            # Load in the SVG's natural orientation (target dims swapped),
            # then rotate 90° CW on the canvas so it lands as target_w x target_h.
            drawing = self._load_phase_box_svg(target_h, target_w, path=path)
            if drawing:
                c.saveState()
                c.translate(x, y + target_h)
                c.rotate(-90)
                renderPDF.draw(drawing, c, 0, 0)
                c.restoreState()

    def _init_styles(self):
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.lib.enums import TA_LEFT
        from reportlab.lib import colors

        self.step_body_style = ParagraphStyle(
            'StepBody',
            fontName='Baskerville',
            fontSize=9,
            leading=9,
            autoLeading='max',
            textColor=colors.black,
            alignment=TA_LEFT,
            spaceAfter=4,
        )
        self.ability_title_style = ParagraphStyle(
            'AbilityTitle',
            fontName='Baskerville',
            fontSize=10,
            leading=10,
            spaceAfter=2,
        )
        self.ability_body_style = ParagraphStyle(
            'AbilityBody',
            fontName='Baskerville',
            fontSize=7,
            leading=9,
        )
        self.flavor_text_style = ParagraphStyle(
            'FlavorText',
            fontName='Baskerville-Italic',
            fontSize=7,
            leading=9,
            textColor=colors.black,
            alignment=TA_LEFT,
        )
        self.action_body_style = ParagraphStyle(
            'ActionBody',
            fontName='Baskerville',
            fontSize=8,
            leading=9,
            autoLeading='max',
            textColor=colors.black,
            alignment=TA_LEFT,
            spaceAfter=1,
        )
        self.faction_name_font = 'Luminari'
        self.faction_name_font_size = 30
        faction_hex = self.sheet.faction.color or '#FFFFFF'
        from the_forge.models import FactionSheet
        title_choice = self.sheet.title_text_color
        if title_choice == FactionSheet.TitleTextColor.WHITE:
            self.ink_on_faction_hex = '#FFFFFF'
        elif title_choice == FactionSheet.TitleTextColor.BLACK:
            self.ink_on_faction_hex = '#000000'
        else:
            self.ink_on_faction_hex = '#FFFFFF' if _is_white_text_legible(faction_hex) else '#000000'
        self.ink_on_faction = HexColor(self.ink_on_faction_hex)
        self.faction_name_color = self.ink_on_faction

        from reportlab.lib.enums import TA_CENTER
        self.content_box_title_style = ParagraphStyle(
            'ContentBoxTitle',
            fontName='Luminari',
            fontSize=CONTENT_BOX_TITLE_SIZE,
            leading=CONTENT_BOX_TITLE_SIZE + 2,
            textColor=colors.black,
            alignment=TA_CENTER,
            spaceAfter=CONTENT_BOX_TITLE_PAD_BOTTOM,
        )
        self.content_box_text_style = ParagraphStyle(
            'ContentBoxText',
            fontName='Baskerville',
            fontSize=CONTENT_BOX_TEXT_SIZE,
            leading=CONTENT_BOX_TEXT_SIZE + 2,
            autoLeading='max',
            textColor=colors.black,
            alignment=TA_CENTER,
            spaceAfter=CONTENT_BOX_TEXT_PAD_BOTTOM,
        )

    def _resolve_cost_icon(self, action):
        """Resolve icon source and draw dimensions for a StepAction's cost type.

        Returns (icon_path, icon_drawing, draw_w, draw_h). At most one of
        icon_path / icon_drawing is non-None. Returns (None, None, 0, 0) if no icon.
        """
        from PIL import Image as PILImage

        cost = action.cost
        if cost == 'other':
            icon_path = action.cost_image.path if action.cost_image else None
        elif cost == 'action':
            step = action.step
            if step.step_cost_image:
                icon_path = step.step_cost_image.path
            else:
                drawing, draw_w, draw_h = self._get_meeple_action_icon()
                return None, drawing, draw_w, draw_h
        elif cost.startswith('card_custom_image_'):
            inline_keyword = cost[len('card_'):]  # strip routing prefix → 'custom_image_<n>'
            icon_path = _inline_image_path(inline_keyword, sheet=action.step.sheet)
        else:
            icon_path = _cost_icon_path(cost)

        if not icon_path or not os.path.exists(icon_path):
            return None, None, 0, 0

        with PILImage.open(icon_path) as pil_img:
            iw, ih = pil_img.size
        aspect = iw / ih

        if cost.startswith('item_'):
            # Fix height, scale width
            draw_h = ACTION_ITEM_H
            draw_w = draw_h * aspect
        elif cost == 'card_nonbird':
            # Fix width, scale height
            draw_w = ACTION_CARDS_W
            draw_h = draw_w / aspect
        elif cost.startswith('card_'):
            # Fix height, scale width
            draw_h = ACTION_CARD_H
            draw_w = draw_h * aspect
        else:
            # action, other — fix width, scale height
            draw_w = ACTION_DEFAULT_W
            draw_h = draw_w / aspect

        return icon_path, None, draw_w, draw_h

    @staticmethod
    def _group_actions(actions):
        """Group consecutive card actions with the same cost type together.

        Non-card actions stay individual. Returns [(cost_or_None, [action, ...]), ...].
        """
        groups = []
        for action in actions:
            if action.cost.startswith('card_'):
                if groups and groups[-1][0] == action.cost:
                    groups[-1][1].append(action)
                else:
                    groups.append((action.cost, [action]))
            else:
                groups.append((None, [action]))
        return groups

    def _pack_action_rows(self, groups, avail_action_w):
        """Pack non-card actions into rows using greedy bin-packing.

        Card groups pass through unchanged. Consecutive eligible actions
        (action, other, item_*) are packed side-by-side when they fit.
        Allows text to wrap once (up to 3 lines max) to save horizontal space,
        but only if the side-by-side row is shorter than stacking vertically.

        Returns list of row descriptors:
          ('single_card', action)
          ('card_group', cost_type, [actions])
          ('row', [(action, alloc_w), ...])
        """
        rows = []
        # pending entries: dicts with keys: action, natural_w, natural_h,
        # fixed_w, icon_h_eff, markup, min_text_w, text_natural_w
        pending = []

        def _try_pack_run(run):
            """Try to pack a contiguous run of actions side-by-side.

            Strategy: start with N = len(run). Each action gets its fair share
            (avail_action_w / N). Any action whose natural width is <= its fair
            share is "narrow" — it takes only what it needs and donates the
            rest. The remaining actions split the leftover space evenly.

            Repeat until no more narrow actions are found, then verify each
            remaining action fits at its allocated width within
            ACTION_PACK_MAX_LINES without breaking mid-word. If any fails,
            return None.

            Returns ('row', [(action, alloc_w), ...]) or None.
            """
            n = len(run)
            if n < 2:
                return None
            total_gaps = SIDE_BY_SIDE_GAP * (n - 1)
            usable_w = avail_action_w - total_gaps
            if usable_w <= 0:
                return None

            # Iteratively pull out narrow actions (natural_w <= fair share).
            allocated = {}  # idx -> alloc_w (final)
            remaining_idx = list(range(n))
            remaining_w = usable_w
            while remaining_idx:
                share = remaining_w / len(remaining_idx)
                narrow = [i for i in remaining_idx if run[i]['natural_w'] <= share]
                if not narrow:
                    # Everyone left needs at least their fair share
                    for i in remaining_idx:
                        allocated[i] = share
                    break
                for i in narrow:
                    allocated[i] = run[i]['natural_w']
                    remaining_w -= run[i]['natural_w']
                    remaining_idx.remove(i)

            # Reject if any allocation can't fit the paragraph's narrowest
            # unbreakable token. Below this threshold ReportLab splits per
            # character, producing one letter per line.
            for i, meta in enumerate(run):
                if allocated[i] < meta['fixed_w'] + meta['min_text_w']:
                    return None

            # Verify each action fits at its allocation (line cap + no mid-word breaks)
            row_items = []
            row_h = 0
            stacked_h = 0
            for i, meta in enumerate(run):
                alloc_w = allocated[i]
                if alloc_w >= meta['natural_w']:
                    h_at_alloc = meta['natural_h']
                else:
                    n_lines, h_at_alloc = _measure_action_at_width(
                        meta, alloc_w, self.action_body_style
                    )
                    if h_at_alloc is None:
                        return None
                row_items.append((meta['action'], alloc_w))
                if h_at_alloc > row_h:
                    row_h = h_at_alloc
                stacked_h += meta['natural_h']
            stacked_h += ACTION_ROW_GAP * (n - 1)

            # Only commit if side-by-side is actually shorter than stacking
            if row_h >= stacked_h:
                return None
            return ('row', row_items)

        def _flush_pending():
            if not pending:
                return

            i = 0
            run_results = []  # list of (start_idx, row_descriptor)
            while i < len(pending):
                # Greedy: try the largest run first, shrink until packing succeeds
                # or we fall back to a single action on its own line.
                max_run = min(MAX_ACTIONS_PER_ROW, len(pending) - i)
                packed = None
                for run_size in range(max_run, 1, -1):
                    run = pending[i:i + run_size]
                    packed = _try_pack_run(run)
                    if packed is not None:
                        break
                if packed is not None:
                    run_results.append((i, packed))
                    i += run_size
                else:
                    meta = pending[i]
                    run_results.append((i, ('row', [(meta['action'], meta['natural_w'])])))
                    i += 1

            for _, row_desc in run_results:
                rows.append(row_desc)

        for cost_type, group_actions in groups:
            if cost_type is not None:
                # Card group — flush pending non-card actions first
                _flush_pending()
                pending = []
                if len(group_actions) == 1:
                    rows.append(('single_card', group_actions[0]))
                else:
                    rows.append(('card_group', cost_type, group_actions))
            else:
                # Non-card single action — candidate for side-by-side
                action = group_actions[0]
                icon_path, icon_drawing, icon_w, icon_h = self._resolve_cost_icon(action)
                meta = _measure_action_widths(
                    action, icon_w, icon_h, icon_path, action.cost, self.action_body_style,
                    icon_drawing=icon_drawing,
                )
                meta['action'] = action
                pending.append(meta)

        _flush_pending()
        return rows

    def _build_action_flowables(self, step, avail_w, indent):
        """Build flowable objects for a step's StepActions.

        Returns a list of Table-wrapped flowable objects (StepActionFlowable
        for single actions, CardGroupFlowable for grouped card actions),
        or an empty list if the step has no actions.
        """
        from reportlab.platypus import Table, TableStyle

        if not hasattr(step, 'actions'):
            return []
        actions = step.actions.order_by('order') if hasattr(step.actions, 'order_by') else list(step.actions.all())
        if not actions:
            return []

        flowables = []
        action_w = avail_w - indent
        groups = self._group_actions(actions)
        packed_rows = self._pack_action_rows(groups, action_w)

        # Card-cost rows ignore the multi-step bullet-column offset (offset 2)
        # so they always sit at the same x relative to the content-area edge,
        # regardless of single vs. multi-step layout. When the step also
        # contains a card_nonbird (the wide other_cards.png icon), all
        # card-cost rows get pushed right by half the width difference so the
        # wide icon's leftward overflow stays inside the section. Non-card
        # rows are unaffected.
        step_has_nonbird = any(a.cost == 'card_nonbird' for a in actions)
        nonbird_push = CARD_NONBIRD_WIDTH_OVERHANG
        card_extra = -indent + nonbird_push
        card_action_w = action_w - card_extra

        for row_info in packed_rows:
            is_card_row = row_info[0] in ('single_card', 'card_group')
            row_indent = indent + card_extra if is_card_row else indent
            row_width = card_action_w if is_card_row else action_w

            if row_info[0] == 'single_card':
                action = row_info[1]
                icon_path, icon_drawing, icon_w, icon_h = self._resolve_cost_icon(action)
                markup = format_step_markup(action.text, sheet=_sheet_of(action))
                para = Paragraph(markup, self.action_body_style)
                flowable = StepActionFlowable(
                    icon_path=icon_path, icon_drawing=icon_drawing, text_paragraph=para,
                    icon_w=icon_w, icon_h=icon_h,
                    total_width=row_width, cost=action.cost,
                )

            elif row_info[0] == 'card_group':
                cost_type = row_info[1]
                group_actions = row_info[2]
                icon_path, icon_drawing, icon_w, icon_h = self._resolve_cost_icon(group_actions[0])
                paragraphs = []
                for action in group_actions:
                    markup = format_step_markup(action.text, sheet=_sheet_of(action))
                    para = Paragraph(markup, self.action_body_style)
                    paragraphs.append(para)
                flowable = CardGroupFlowable(
                    icon_path=icon_path, icon_drawing=icon_drawing, text_paragraphs=paragraphs,
                    icon_w=icon_w, icon_h=icon_h,
                    total_width=row_width, cost=cost_type,
                )

            elif row_info[0] == 'row':
                action_items = row_info[1]  # [(action, alloc_w), ...]

                if len(action_items) == 1:
                    # Single non-card action — full width
                    action, _ = action_items[0]
                    icon_path, icon_drawing, icon_w, icon_h = self._resolve_cost_icon(action)
                    markup = format_step_markup(action.text, sheet=_sheet_of(action))
                    para = Paragraph(markup, self.action_body_style)
                    flowable = StepActionFlowable(
                        icon_path=icon_path, icon_drawing=icon_drawing, text_paragraph=para,
                        icon_w=icon_w, icon_h=icon_h,
                        total_width=action_w, cost=action.cost,
                    )
                else:
                    # Multiple side-by-side actions — each gets the width the
                    # packer allocated and validated. Don't recompute: doing so
                    # can drive a column below min_text_w and trigger
                    # per-character wrapping in ReportLab.
                    sub_flowables = []
                    col_widths = []
                    for action, alloc_w in action_items:
                        icon_path, icon_drawing, icon_w, icon_h = self._resolve_cost_icon(action)
                        markup = format_step_markup(action.text, sheet=_sheet_of(action))
                        para = Paragraph(markup, self.action_body_style)
                        af = StepActionFlowable(
                            icon_path=icon_path, icon_drawing=icon_drawing, text_paragraph=para,
                            icon_w=icon_w, icon_h=icon_h,
                            total_width=alloc_w, cost=action.cost,
                        )
                        sub_flowables.append(af)
                        col_widths.append(alloc_w)

                    # Build single-row table with gap columns between actions
                    row_cells = []
                    final_col_widths = []
                    for i, (af, cw) in enumerate(zip(sub_flowables, col_widths)):
                        if i > 0:
                            row_cells.append('')
                            final_col_widths.append(SIDE_BY_SIDE_GAP)
                        row_cells.append(af)
                        final_col_widths.append(cw)

                    flowable = Table([row_cells], colWidths=final_col_widths)
                    flowable.hAlign = 'CENTER' if step.content_box_id else 'LEFT'
                    flowable.setStyle(TableStyle([
                        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                        ('LEFTPADDING', (0, 0), (-1, -1), 0),
                        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
                        ('TOPPADDING', (0, 0), (-1, -1), 0),
                        ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
                    ]))

            if row_indent > 0:
                # Wrap in a table to apply the left indent
                t = Table([['', flowable]], colWidths=[row_indent, row_width])
                t.setStyle(TableStyle([
                    ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                    ('LEFTPADDING', (0, 0), (-1, -1), 0),
                    ('RIGHTPADDING', (0, 0), (-1, -1), 0),
                    ('TOPPADDING', (0, 0), (-1, -1), ACTION_ROW_GAP),
                    ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
                ]))
                flowables.append(t)
            else:
                flowables.append(flowable)

        return flowables

    def measure_phase_height(self, steps, width, header_h=None):
        """Calculate total height needed for a phase's steps at given width."""
        total = header_h if header_h is not None else self._header_height()
        single_step = len(steps) == 1
        for step in steps:
            total += self.measure_step_height(step, width, single_step=single_step,
                                              phase_indent=SINGLE_STEP_PHASE_INDENT)
        return total

    def measure_content_box_height(self, content_box, width):
        """Calculate total content height for a content box at given content width.
        Does NOT include box padding — that is handled by the Frame's topPadding/bottomPadding."""
        total = 0
        if content_box.title:
            p = Paragraph(content_box.title, self.content_box_title_style)
            _, h = p.wrap(width, 9999)
            total += h + self.content_box_title_style.spaceAfter
        if content_box.text:
            markup = format_step_markup(content_box.text, sheet=self.sheet)
            p = Paragraph(markup, self.content_box_text_style)
            _, h = p.wrap(width, 9999)
            total += h + self.content_box_text_style.spaceAfter
        steps = list(content_box.steps.all())
        single_step = len(steps) == 1
        step_style = self.content_box_text_style if single_step else self.step_body_style
        for step in steps:
            total += self.measure_step_height(step, width, single_step=single_step, body_style=step_style)
        return total

    def _content_box_dims_for_width(self, content_box, box_w):
        """Calculate content box height for a given box width."""
        content_w = box_w - (CONTENT_BOX_INTERNAL_MARGIN * 2)
        content_h = self.measure_content_box_height(content_box, content_w)
        box_h = content_h + CONTENT_BOX_PAD_TOP + CONTENT_BOX_PAD_BOTTOM
        return box_w, box_h, content_w

    def _content_box_fits(self, x, y, w, h, top_y=None):
        """Check if a content box at (x, y) with dimensions (w, h) fits on the page.

        ``top_y`` defaults to ``self.phases_top_y`` (the strict bound used for
        manually-overridden boxes anchored under the title band). The overflow
        placer passes the full page top so it can search the entire usable
        page area for an open slot, falling back to the cascade if none exists.
        """
        if x < X_MARGIN or x + w > PAGE_W - X_MARGIN:
            return False
        if top_y is None:
            top_y = self.phases_top_y
        if y < BOTTOM_MARGIN or y + h > top_y:
            return False
        # Check overlap with phases box
        if hasattr(self, '_phases_rect') and self._phases_rect is not None:
            px, py, pw, ph = self._phases_rect
            if _rects_overlap(x, y, w, h, px, py, pw, ph):
                return False
        # Check overlap with previously placed content boxes
        for bx, by, bw, bh in self._placed_boxes:
            if _rects_overlap(x, y, w, h, bx, by, bw, bh):
                return False
        return True

    def _min_track_width_for_content_box(self, content_box):
        """Return the minimum content width needed for all tracks in a content box to fit.

        For building tracks, returns the natural (non-overlapping) grid width.
        For token tracks, returns the minimum overlapping width using TRACK_OVERLAP_MIN_H_STEP.
        Includes the step indent and row title width. Returns 0 if no tracks.
        """
        SINGLE_STEP_INDENT = PHASE_INTERNAL_MARGIN
        steps = list(content_box.steps.all())
        single_step = len(steps) == 1
        indent = SINGLE_STEP_INDENT if single_step else (0.325 * inch + 0.015 * inch)

        max_needed = 0
        for step in steps:
            if not hasattr(step, 'tracks'):
                continue
            tracks = step.tracks.order_by('order')
            for track in tracks:
                num_cols = track.num_columns
                if num_cols == 0:
                    continue

                # Parse dividers (stored values are "after column N"; only
                # valid positions 0..num_cols-2 contribute to total).
                dividers = set()
                if track.column_dividers:
                    for col_str in track.column_dividers.split(','):
                        col_str = col_str.strip()
                        if col_str:
                            n = int(col_str)
                            if 0 <= n < num_cols - 1:
                                dividers.add(n + 1)
                total_dividers = len(dividers)

                # Row title width
                row_titles_list = track.get_row_titles_list() if hasattr(track, 'get_row_titles_list') else []
                has_row_titles = any(t.strip() for t in row_titles_list)
                if getattr(track, 'header_title', '') and track.header_title:
                    has_row_titles = True
                vertical_titles = getattr(track, 'row_title_orientation', 'horizontal') == 'vertical'
                if has_row_titles:
                    row_title_w = TRACK_ROW_TITLE_VERTICAL_W if vertical_titles else TRACK_ROW_TITLE_W
                else:
                    row_title_w = 0

                if track.type == 'token':
                    # Minimum overlapping width: each segment's first slot at full size,
                    # remaining slots at TRACK_OVERLAP_MIN_H_STEP
                    num_segments = total_dividers + 1
                    cols_that_step = num_cols - num_segments
                    min_grid_w = (num_segments * TRACK_SLOT_SIZE
                                  + cols_that_step * TRACK_OVERLAP_MIN_H_STEP
                                  + total_dividers * TRACK_OVERLAP_DIVIDER_W)
                    track_w = min_grid_w + row_title_w + indent
                else:
                    # Building / counter track: needs full natural width (no overlap)
                    slot_size = TRACK_COUNTER_SIZE if track.type == 'counter' else TRACK_SLOT_SIZE
                    divider_space = total_dividers * TRACK_DIVIDER_W
                    natural_grid_w = (num_cols * slot_size
                                      + (num_cols - 1) * TRACK_SLOT_GAP
                                      + divider_space)
                    track_w = natural_grid_w + row_title_w + indent

                if track_w > max_needed:
                    max_needed = track_w

            # Also consider scales — minimum width is the widest single entry
            # so at least one entry can fit per line.
            for scale in step.scales.all():
                sf = ScaleFlowable(
                    scale=scale,
                    total_width=9999,
                    body_style=self.step_body_style,
                    title_color_hex=self._on_tan_hex,
                )
                sf.wrap(9999, 9999)
                widest = max((w for _, w, _ in sf._entry_paras), default=0)
                scale_w = widest + indent
                if scale_w > max_needed:
                    max_needed = scale_w

        return max_needed

    def _natural_track_width_for_steps(self, steps, indent):
        """Return the natural (non-overlapped) width needed for all tracks in the given steps.

        Always uses full slot size + gap for all track types (no overlap assumption).
        Returns 0 if no tracks.
        """
        max_needed = 0
        for step in steps:
            if not hasattr(step, 'tracks'):
                continue
            tracks = step.tracks.order_by('order')
            for track in tracks:
                num_cols = track.num_columns
                if num_cols == 0:
                    continue

                # Parse dividers — stored values are "after column N"; only positions 0..num_cols-2 contribute
                dividers = set()
                if track.column_dividers:
                    for col_str in track.column_dividers.split(','):
                        col_str = col_str.strip()
                        if col_str:
                            n = int(col_str)
                            if 0 <= n < num_cols - 1:
                                dividers.add(n + 1)
                total_dividers = len(dividers)

                # Row title width
                row_titles_list = track.get_row_titles_list() if hasattr(track, 'get_row_titles_list') else []
                has_row_titles = any(t.strip() for t in row_titles_list)
                if getattr(track, 'header_title', '') and track.header_title:
                    has_row_titles = True
                vertical_titles = getattr(track, 'row_title_orientation', 'horizontal') == 'vertical'
                if has_row_titles:
                    row_title_w = TRACK_ROW_TITLE_VERTICAL_W if vertical_titles else TRACK_ROW_TITLE_W
                else:
                    row_title_w = 0

                slot_size = TRACK_COUNTER_SIZE if track.type == 'counter' else TRACK_SLOT_SIZE
                divider_space = total_dividers * TRACK_DIVIDER_W
                natural_grid_w = (num_cols * slot_size
                                  + (num_cols - 1) * TRACK_SLOT_GAP
                                  + divider_space)
                track_w = natural_grid_w + row_title_w + indent

                if track_w > max_needed:
                    max_needed = track_w

            for scale in step.scales.all():
                sf = ScaleFlowable(
                    scale=scale,
                    total_width=9999,
                    body_style=self.step_body_style,
                    title_color_hex=self._on_tan_hex,
                )
                sf.wrap(9999, 9999)
                widest = max((w for _, w, _ in sf._entry_paras), default=0)
                scale_w = widest + indent
                if scale_w > max_needed:
                    max_needed = scale_w

        return max_needed

    def _preferred_track_width_for_content_box(self, content_box):
        """Return the natural (non-overlapped) content width needed for all tracks in a content box."""
        SINGLE_STEP_INDENT = PHASE_INTERNAL_MARGIN
        steps = list(content_box.steps.all())
        single_step = len(steps) == 1
        indent = SINGLE_STEP_INDENT if single_step else (0.325 * inch + 0.015 * inch)
        return self._natural_track_width_for_steps(steps, indent)

    def _min_legend_width_for_content_box(self, content_box):
        """Return the minimum content width needed for any legend in a content box.

        Each legend asks for at least LEGEND_MIN_WIDTH for its own column. If a
        legend has any no-visual rows whose centered title would exceed the
        default LEGEND_LEFT_COL_W, we grow the request by the overflow so the
        container is wide enough to fit the title without cramping the body.
        The content box adds its indent so the legend gets that width after the
        icon/text indent is removed. Returns 0 if no legends.
        """
        SINGLE_STEP_INDENT = PHASE_INTERNAL_MARGIN
        steps = list(content_box.steps.all())
        single_step = len(steps) == 1
        indent = SINGLE_STEP_INDENT if single_step else (0.325 * inch + 0.015 * inch)
        max_needed = 0
        for step in steps:
            for legend in step.legends.all():
                widest_no_visual = 0.0
                for r in legend.rows.all():
                    title = (r.title or '').strip()
                    if not title or r.icon or r.image:
                        continue
                    tw = pdfmetrics.stringWidth(title, 'Luminari', LEGEND_ROW_TITLE_FONT_SIZE)
                    if tw > widest_no_visual:
                        widest_no_visual = tw
                required_left = max(LEGEND_LEFT_COL_W, widest_no_visual + 2 * LEGEND_LEFT_TITLE_BREATHING)
                extra = max(0, required_left - LEGEND_LEFT_COL_W)
                w = LEGEND_MIN_WIDTH + extra + indent
                if w > max_needed:
                    max_needed = w
        return max_needed

    def _natural_text_width_for_steps(self, steps, indent):
        """Return the longest natural (single-line) width across all step body paragraphs."""
        PROBE_W = 10000
        max_needed = 0
        for step in steps:
            markup = format_step_markup(step.text, sheet=self.sheet)
            para = Paragraph(markup, self.step_body_style)
            para.wrap(PROBE_W, 9999)
            text_natural_w = 0
            if hasattr(para, 'blPara') and hasattr(para.blPara, 'lines') and para.blPara.lines:
                for line in para.blPara.lines:
                    max_w = getattr(line, 'maxWidth', 0)
                    extra = getattr(line, 'extraSpace', 0)
                    lw = (max_w - extra) if max_w else getattr(line, 'currentWidth', 0)
                    if lw > text_natural_w:
                        text_natural_w = lw
            total_w = text_natural_w + indent
            if total_w > max_needed:
                max_needed = total_w
        return max_needed

    def _preferred_phase_track_width(self, phase_order):
        """Return the phase box width needed to avoid track staggering in any phase step.

        Also factors in the natural width of the step body text so that a narrow track
        does not force the box narrower than the text would naturally want — which
        otherwise forces text to wrap into many lines and pushes the binary search
        in `_draw_vertical_phases` all the way to full page width.
        """
        ICON_COL_W = 0.325 * inch
        ICON_TEXT_GAP = 0.015 * inch
        TEXT_COL_X = ICON_COL_W + ICON_TEXT_GAP

        max_track = 0
        max_text = 0
        for pk in phase_order:
            steps = self.phases_grouped.get(pk, [])
            single_step = len(steps) == 1
            indent = 0 if single_step else TEXT_COL_X
            tw = self._natural_track_width_for_steps(steps, indent)
            if tw > max_track:
                max_track = tw
            txw = self._natural_text_width_for_steps(steps, indent)
            if txw > max_text:
                max_text = txw

        max_needed = max(max_track, max_text)
        if max_needed == 0:
            return 0
        return max_needed + (PHASE_INTERNAL_MARGIN * 2)

    def measure_step_height(self, step, width, single_step=False, body_style=None, phase_indent=0):
        from reportlab.platypus import Table, TableStyle
        ICON_COL_W = 0.325 * inch
        ICON_TEXT_GAP = 0.015 * inch
        TEXT_COL_X = ICON_COL_W + ICON_TEXT_GAP
        ICON_NUDGE_DOWN = 4

        style = body_style or self.step_body_style
        # Content-box single-step (signalled by content_box_text_style) keeps its
        # centered, zero-indent layout; phase single-step uses phase_indent.
        is_centered_content_box = (style is self.content_box_text_style)
        single_indent = phase_indent if (single_step and not is_centered_content_box) else 0
        text_col_w = single_indent if single_step else TEXT_COL_X
        text_content_w = width - text_col_w
        markup = format_step_markup(step.text, sheet=self.sheet)

        # Extra padding to compensate for autoLeading underreporting (matches _build_phase_story)
        probe = Paragraph(markup, style)
        _, wrap_h = probe.wrap(text_content_w, 9999)
        extra_h = true_paragraph_height(probe, text_content_w) - wrap_h

        # Build table matching _build_phase_story exactly
        para = Paragraph(markup, style)
        para.wrap(text_content_w, 9999)
        tighten_large_font_lines(para)
        if single_step:
            if single_indent:
                t = Table([['', para]], colWidths=[single_indent, text_content_w])
                t.setStyle(TableStyle([
                    ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                    ('LEFTPADDING', (0, 0), (-1, -1), 0),
                    ('RIGHTPADDING', (0, 0), (-1, -1), 0),
                    ('TOPPADDING', (0, 0), (-1, -1), 0),
                    ('BOTTOMPADDING', (0, 0), (-1, -1), extra_h),
                ]))
                _, table_h = t.wrap(width, 9999)
            else:
                _, table_h = para.wrap(width, 9999)
                table_h += extra_h
            if first_line_has_inline_icon_only_base_font(para):
                table_h += SINGLE_STEP_INLINE_ICON_PAD_TOP
        else:
            svg_drawing = self._phase_number_svgs.get(step.number % 10)
            first_col = svg_drawing if svg_drawing else ''
            t = Table([[first_col, para]], colWidths=[text_col_w, text_content_w])
            t.setStyle(TableStyle([
                ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                ('ALIGN', (0, 0), (0, -1), 'RIGHT'),
                ('LEFTPADDING', (0, 0), (-1, -1), 0),
                ('RIGHTPADDING', (0, 0), (0, -1), TEXT_COL_X - ICON_COL_W),
                ('RIGHTPADDING', (1, 0), (1, -1), 0),
                ('TOPPADDING', (0, 0), (0, -1), ICON_NUDGE_DOWN),
                ('TOPPADDING', (1, 0), (1, -1), 0),
                ('BOTTOMPADDING', (0, 0), (-1, -1), extra_h),
            ]))
            _, table_h = t.wrap(width, 9999)

        # Add height for StepAction rows by wrapping the same flowables that
        # _build_steps_story will append to the story. This guarantees the
        # measured height matches what the build path actually consumes.
        action_flowables = self._build_action_flowables(step, width, text_col_w)
        if action_flowables:
            # When actions follow the step-text paragraph in single_step mode,
            # _build_steps_story appends `para` directly so the Frame consumes
            # the paragraph style's spaceAfter as a gap before the first action.
            # (In multi-step mode the para is wrapped in a Table, so spaceAfter
            # is absorbed and we don't need to count it.)
            if single_step:
                table_h += style.spaceAfter
            for fl in action_flowables:
                _, fh = fl.wrap(width, 9999)
                table_h += fh

        # Add bordered box + track heights, iterated in the user's intermixed
        # order from the editor (boxes and tracks share an `order` sequence).
        child_w = width - text_col_w
        for child in step.ordered_children:
            obj = child['obj']
            if child['kind'] == 'box':
                table_h += BORDERED_BOX_HEIGHTS.get(obj.height, BORDERED_BOX_HEIGHTS['medium']) + BORDERED_BOX_TITLE_SIZE / 2
            elif child['kind'] == 'track':
                slots = list(obj.slots.all())
                tf = TrackFlowable(
                    track=obj,
                    slots=slots,
                    total_width=child_w,
                    body_style=self.step_body_style,
                    faction_color=self.faction_color,
                )
                _, track_h = tf.wrap(child_w, 9999)
                table_h += track_h + TRACK_TITLE_GAP + TRACK_BOTTOM_PAD
            elif child['kind'] == 'legend':
                lf = LegendFlowable(
                    legend=obj,
                    total_width=child_w,
                    body_style=self.step_body_style,
                    title_color_hex=self._on_tan_hex,
                )
                _, lh = lf.wrap(child_w, 9999)
                table_h += lh + LEGEND_BLOCK_TITLE_GAP + LEGEND_ROW_GAP
            elif child['kind'] == 'scale':
                sf = ScaleFlowable(
                    scale=obj,
                    total_width=child_w,
                    body_style=self.step_body_style,
                    title_color_hex=self._on_tan_hex,
                )
                _, sh = sf.wrap(child_w, 9999)
                table_h += sh + SCALE_TOP_PAD + SCALE_BOTTOM_PAD

        return table_h

    # def determine_layout(self):
    #     n = len(self.phases_grouped)
    #     col_width = (BODY_W - (PHASE_INTERNAL_MARGIN * 2) - (PHASE_INTERNAL_MARGIN * (n - 1))) / n
    #     header_h = self._banner_height_for_width(col_width)
    #     max_phase_h = max(
    #         self.measure_phase_height(steps, col_width, header_h=header_h)
    #         for steps in self.phases_grouped.values()
    #     )
    #     # If tallest column needs more than 50% of available phase height, go horizontal
    #     phase_h = self.phases_top_y - self.phases_bottom_y
    #     return 'horizontal' if max_phase_h > phase_h * 0.5 else 'vertical'

    def _override(self, obj, field_h, field_v):
        """Return the layout-mode-appropriate override value or None.

        `_h` fields apply when sheet.layout_mode == 'horizontal', `_v` otherwise.
        Values are stored in inches; callers are responsible for *inch conversion."""
        attr = field_h if self.sheet.layout_mode == 'horizontal' else field_v
        return getattr(obj, attr, None)

    def _record_phase_breakdown(self, phase_key, steps, content_x, content_top_y, content_w, header_h, content_bottom_y=None):
        """Preview-only: record a colored phase header bar and one rect per step.
        If content_bottom_y is provided, the last step is extended down to it so the
        preview shows no visual gap between phases (matching the PDF's flush layout).
        No-op outside compute_layout()."""
        if not getattr(self, '_recording', False):
            return
        cfg = PHASE_HEADERS.get(phase_key, {})
        banner_y = content_top_y - header_h
        self._record_element(kind='phase_header_bar',
                             x=content_x, y=banner_y, w=content_w, h=header_h,
                             phase=phase_key,
                             label=_pdf_label('phases', phase_key, self.sheet, default=phase_key.title()),
                             fill=cfg.get('color', '#888888'))
        if not steps:
            return
        single_step = len(steps) == 1
        cursor_y = banner_y
        last_idx = len(steps) - 1
        for i, step in enumerate(steps):
            step_h = self.measure_step_height(step, content_w, single_step=single_step,
                                              phase_indent=SINGLE_STEP_PHASE_INDENT)
            step_y = cursor_y - step_h
            if i == last_idx and content_bottom_y is not None and step_y > content_bottom_y:
                step_h = cursor_y - content_bottom_y
                step_y = content_bottom_y
            self._record_element(kind='phase_step', id=step.id,
                                 x=content_x, y=step_y, w=content_w, h=step_h,
                                 number=step.number, text=step.text or '',
                                 single_step=single_step,
                                 phase=phase_key)
            self._record_step_children(step, content_x, step_y, step_h, content_w, single_step,
                                       phase_indent=SINGLE_STEP_PHASE_INDENT)
            cursor_y = step_y

    def _record_content_box_breakdown(self, content_box, box_x, box_y, box_w, box_h):
        """Preview-only: record bordered_box and track rects inside a content
        box's steps. Mirrors the layout in _build_content_box_story / Frame."""
        if not getattr(self, '_recording', False):
            return
        content_w = box_w - (CONTENT_BOX_INTERNAL_MARGIN * 2)
        content_x = box_x + CONTENT_BOX_INTERNAL_MARGIN
        content_top_y = box_y + box_h - CONTENT_BOX_PAD_TOP

        cursor_top = content_top_y
        if content_box.title:
            p = Paragraph(content_box.title, self.content_box_title_style)
            _, h = p.wrap(content_w, 9999)
            cursor_top -= h + self.content_box_title_style.spaceAfter
        if content_box.text:
            markup = format_step_markup(content_box.text, sheet=self.sheet)
            p = Paragraph(markup, self.content_box_text_style)
            _, h = p.wrap(content_w, 9999)
            cursor_top -= h + self.content_box_text_style.spaceAfter

        steps = list(content_box.steps.all())
        single_step = len(steps) == 1
        step_style = self.content_box_text_style if single_step else self.step_body_style
        for step in steps:
            step_h = self.measure_step_height(step, content_w, single_step=single_step,
                                              body_style=step_style)
            step_y = cursor_top - step_h
            self._record_step_children(step, content_x, step_y, step_h, content_w, single_step)
            cursor_top = step_y

    def _record_step_children(self, step, step_x, step_y, step_h, step_w, single_step, phase_indent=0):
        """Preview-only: record bordered_box and track rects inside a phase step.
        Children stack at the bottom of the step (after text+actions) and are drawn
        in `ordered_children` order. No-op outside compute_layout()."""
        if not getattr(self, '_recording', False):
            return
        if step.phase == 'other' and step.content_box_id:
            parent_key = f'content_box_{step.content_box_id}'
        else:
            parent_key = 'phase_box'
        children = list(getattr(step, 'ordered_children', []))
        if not children:
            return

        ICON_COL_W = 0.325 * inch
        ICON_TEXT_GAP = 0.015 * inch
        TEXT_COL_X = ICON_COL_W + ICON_TEXT_GAP
        indent = (phase_indent if single_step else TEXT_COL_X)
        avail_w = step_w - indent

        # Compute each child's height + top pad in points, in order.
        sized = []
        total_block_h = 0.0
        for child in children:
            obj = child['obj']
            if child['kind'] == 'box':
                box_h = BORDERED_BOX_HEIGHTS.get(obj.height, BORDERED_BOX_HEIGHTS['medium'])
                top_pad = BORDERED_BOX_TITLE_SIZE / 2
                sized.append({'kind': 'bordered_box', 'obj': obj,
                              'h': box_h, 'top_pad': top_pad})
                total_block_h += box_h + top_pad
            elif child['kind'] == 'track':
                slots = list(obj.slots.all())
                tf = TrackFlowable(track=obj, slots=slots, total_width=avail_w,
                                   body_style=self.step_body_style,
                                   faction_color=self.faction_color,
                                   engine=self)
                _, track_h = tf.wrap(avail_w, 9999)
                top_pad = TRACK_TITLE_GAP
                bot_pad = TRACK_BOTTOM_PAD
                sized.append({'kind': 'track', 'obj': obj, 'h': track_h,
                              'top_pad': top_pad, 'bot_pad': bot_pad,
                              'flowable': tf})
                total_block_h += track_h + top_pad + bot_pad
            elif child['kind'] == 'legend':
                lf = LegendFlowable(legend=obj, total_width=avail_w,
                                    body_style=self.step_body_style,
                                    title_color_hex=self._on_tan_hex)
                _, lh = lf.wrap(avail_w, 9999)
                top_pad = LEGEND_BLOCK_TITLE_GAP
                bot_pad = LEGEND_ROW_GAP
                sized.append({'kind': 'legend', 'obj': obj, 'h': lh,
                              'top_pad': top_pad, 'bot_pad': bot_pad})
                total_block_h += lh + top_pad + bot_pad
            elif child['kind'] == 'scale':
                sf = ScaleFlowable(scale=obj, total_width=avail_w,
                                   body_style=self.step_body_style,
                                   title_color_hex=self._on_tan_hex)
                _, sh = sf.wrap(avail_w, 9999)
                top_pad = SCALE_TOP_PAD
                bot_pad = SCALE_BOTTOM_PAD
                sized.append({'kind': 'scale', 'obj': obj, 'h': sh,
                              'top_pad': top_pad, 'bot_pad': bot_pad})
                total_block_h += sh + top_pad + bot_pad

        # Children block sits at the bottom of the step rect (text/actions above).
        # In PDF coords, step_y is the bottom; block occupies [step_y, step_y + total_block_h].
        cursor_top = step_y + min(total_block_h, step_h)
        for entry in sized:
            obj = entry['obj']
            top_pad_pts = entry['top_pad']
            child_h = entry['h']
            top_y = cursor_top - top_pad_pts
            child_y = top_y - child_h
            if entry['kind'] == 'bordered_box':
                self._record_element(kind='bordered_box', id=obj.id,
                                     x=step_x + indent, y=child_y,
                                     w=avail_w, h=child_h,
                                     title=obj.title or '',
                                     step_id=step.id,
                                     parent_key=parent_key)
                cursor_top = child_y
            elif entry['kind'] == 'legend':
                self._record_element(kind='legend', id=obj.id,
                                     x=step_x + indent, y=child_y,
                                     w=avail_w, h=child_h,
                                     title=obj.title or '',
                                     step_id=step.id,
                                     parent_key=parent_key)
                cursor_top = child_y - entry.get('bot_pad', 0)
            elif entry['kind'] == 'scale':
                self._record_element(kind='scale', id=obj.id,
                                     x=step_x + indent, y=child_y,
                                     w=avail_w, h=child_h,
                                     title=obj.title or '',
                                     step_id=step.id,
                                     parent_key=parent_key)
                cursor_top = child_y - entry.get('bot_pad', 0)
            else:
                # Track: collect per-slot rects so JS can draw circles/squares
                # using the same geometry as the PDF (including token zigzag).
                tf = entry['flowable']
                dividers = sorted(tf.dividers)
                # Flowable-local: origin at bottom-left of flowable. Convert to
                # top-left-origin offsets in inches for the preview.
                grid_top_y = (tf._height - tf._title_h - tf._body_h
                              - (tf._header_h if (tf.has_headers and getattr(obj, 'header_position', 'above') == 'above') else 0))
                slot_size = tf._slot_size
                slot_rects = []
                for r in range(tf.num_rows):
                    for c2 in range(tf.num_cols):
                        sx = tf._col_x(c2)
                        sy_bottom = tf._row_y(r, grid_top_y) - tf._col_y_offset(c2)
                        # Convert to top-left origin: top = total_h - (sy_bottom + slot_size)
                        top_offset = (tf._height - (sy_bottom + slot_size))
                        slot_rects.append({
                            'x': sx / inch,
                            'y': top_offset / inch,
                            'size': slot_size / inch,
                            'row': r,
                            'col': c2,
                        })
                # Divider line x-positions (top-left origin, in inches), full grid height.
                divider_lines = []
                for col_idx in dividers:
                    if tf._is_overlapping:
                        prev_right = tf._col_x(col_idx - 1) + slot_size
                        this_left = tf._col_x(col_idx)
                        div_x = (prev_right + this_left) / 2
                    else:
                        div_x = tf._col_x(col_idx) - tf._divider_w / 2 - tf._slot_gap / 2
                    divider_lines.append(div_x / inch)
                # Row divider line y-positions (top-left origin, in inches),
                # spanning the row-title column + full grid width — matches
                # the PDF render path.
                row_divider_lines = []
                for row_idx in sorted(tf.row_divider_indices):
                    bottom_of_upper = tf._row_y(row_idx - 1, grid_top_y)
                    top_of_lower = tf._row_y(row_idx, grid_top_y) + slot_size
                    div_y = (bottom_of_upper + top_of_lower) / 2
                    top_offset = (tf._height - div_y) / inch
                    row_divider_lines.append(top_offset)
                row_divider_left = 0.0
                row_divider_width = (tf._row_title_w + tf._grid_w) / inch
                grid_top_offset = (tf._height - grid_top_y) / inch
                grid_h_in = tf._grid_h / inch
                self._record_element(kind='track', id=obj.id,
                                     x=step_x + indent, y=child_y,
                                     w=avail_w, h=child_h,
                                     title=obj.title or '',
                                     track_type=obj.type,
                                     num_rows=obj.num_rows,
                                     num_columns=obj.num_columns,
                                     slots=slot_rects,
                                     divider_lines=divider_lines,
                                     row_divider_lines=row_divider_lines,
                                     row_divider_left=row_divider_left,
                                     row_divider_width=row_divider_width,
                                     grid_top=grid_top_offset,
                                     grid_h=grid_h_in,
                                     step_id=step.id,
                                     parent_key=parent_key)
                cursor_top = child_y - entry.get('bot_pad', 0)

    def _record_element(self, **fields):
        """Record an element placement for compute_layout(). Coordinates passed
        in here are in points; converted to inches at the boundary."""
        if not getattr(self, '_recording', False):
            return
        out = dict(fields)
        for key in ('x', 'y', 'w', 'h'):
            if key in out and out[key] is not None:
                out[key] = out[key] / inch
        self._layout_elements.append(out)

    def compute_layout(self, layout_mode=None):
        """Run the same placement math as build() but return a structured
        payload of element rects (in inches) instead of producing a PDF.

        If layout_mode is provided, the sheet's layout_mode is temporarily
        overridden for this computation (the database value is not changed).
        Returns a dict with: page (w/h in inches), layout_mode, elements list."""
        from io import BytesIO
        self._recording = True
        self._skip_drawing = True
        self._layout_elements = []
        original_mode = self.sheet.layout_mode
        if layout_mode is not None:
            self.sheet.layout_mode = layout_mode
        try:
            self.build(BytesIO())
        finally:
            self._recording = False
            self._skip_drawing = False
            self.sheet.layout_mode = original_mode
        elements = self._layout_elements
        # Reset for any subsequent real build() call so we don't accidentally
        # carry recording state forward.
        self._layout_elements = []
        return {
            'page': {'w': PAGE_W / inch, 'h': PAGE_H / inch},
            'layout_mode': layout_mode or original_mode,
            'elements': elements,
        }

    def record_slot_snap_point(self, abs_x_pts, abs_y_pts,
                                track=None, row_idx=None, row_title=None,
                                pile=None):
        local_x, local_z = pdf_to_tts_local(abs_x_pts, abs_y_pts)
        entry = {
            "Position": {"x": local_x, "y": 0.1, "z": local_z},
            "Rotation": {"x": 0.0, "y": 0.0, "z": 0.0},
        }
        if track is not None:
            from django.utils.html import strip_tags
            entry["track_id"] = track.pk
            entry["track_title"] = strip_tags(track.title or "").strip()
            entry["track_type"] = track.type
            if row_idx is not None:
                entry["row_index"] = row_idx
            if row_title:
                entry["row_title"] = strip_tags(row_title).strip()
        if pile is not None:
            from django.utils.html import strip_tags
            entry["pile_id"] = pile.pk
            entry["pile_title"] = strip_tags(pile.title or "").strip()
        self.collected_snap_points.append(entry)

    LAYER_GROUPS = (
        frozenset({'background'}),
        frozenset({'box'}),
        frozenset({'fg'}),
    )

    def build(self, output_path, layered=False):
        self.collected_snap_points = []
        if getattr(self, '_skip_drawing', False):
            c = _NoOpCanvas()
            self._run_draw_pipeline(c)
            c.save()
            return

        if not layered:
            c = rl_canvas.Canvas(output_path, pagesize=landscape(letter))
            self._run_draw_pipeline(c)
            c.save()
            return

        real = rl_canvas.Canvas(output_path, pagesize=landscape(letter))
        proxy = LayerFilterCanvas(real)
        # _prepare_ability_bar_layout (called from _draw_top_band) mutates
        # self.phases_top_y in place. Snapshot it so each layered pass starts
        # from identical geometry; otherwise the mutation compounds and box
        # vs. fg layers drift out of registration.
        snap_phases_top_y = self.phases_top_y
        for i, group in enumerate(self.LAYER_GROUPS):
            if i > 0:
                self.phases_top_y = snap_phases_top_y
                self.collected_snap_points = []
            proxy._active_layers = group
            self._run_draw_pipeline(proxy)
            real.showPage()
        real.save()

    def _run_draw_pipeline(self, c):
        layout = self.sheet.layout_mode

        self._draw_background(c)
        self._draw_character_images(c, in_front=False)
        self._draw_top_band(c)

        if layout == 'horizontal':
            self._draw_horizontal_phases(c)
        else:
            self._draw_vertical_phases(c)

        self._draw_card_slots(c)

        if self.content_boxes:
            if layout == 'horizontal':
                leftovers = self._draw_horizontal_content_boxes(c)
            else:
                leftovers = self._draw_vertical_content_boxes(c)
            self._draw_overflow_content_boxes(c, leftovers)

        self._draw_card_piles(c)
        self._draw_character_images(c, in_front=True)
        self._draw_credits(c)
        self._draw_forge_logo(c)

    def _draw_horizontal_phases(self, c):
        phase_order = ['birdsong', 'daylight', 'evening']
        n = len(phase_order)
        # Left/right margins + gaps between columns
        col_w = (BODY_W - (PHASE_INTERNAL_MARGIN * 2) - (PHASE_INTERNAL_MARGIN * (n - 1))) / n
        phase_h = self.phases_top_y - self.phases_bottom_y

        # Phase box background (rotated 90° CW)
        header_h = self._banner_height_for_width(col_w)
        max_content_h = max(
            self.measure_phase_height(self.phases_grouped.get(pk, []), col_w, header_h=header_h)
            for pk in phase_order
        )
        box_h = min(max_content_h + PHASE_BOX_PAD_TOP + PHASE_BOX_PAD_BOTTOM, phase_h)
        box_w = BODY_W
        box_x = X_MARGIN
        box_y = self.phases_top_y - box_h

        # Apply overrides. `phase_box_w_h` is stored for forward compat but the
        # horizontal phase row still uses the full BODY_W today.
        ov_x = self.sheet.phase_box_x_h
        ov_y = self.sheet.phase_box_y_h
        ov_h = self.sheet.phase_box_h_h
        if ov_x is not None:
            box_x = ov_x * inch
        if ov_y is not None:
            box_y = ov_y * inch
        if ov_h is not None:
            box_h = ov_h * inch

        self._draw_phase_box(c, box_x, box_y, box_w, box_h, rotated=True)
        self._phases_rect = (box_x, box_y, box_w, box_h)
        self._record_element(kind='phase_box', x=box_x, y=box_y, w=box_w, h=box_h)

        for i, phase_key in enumerate(phase_order):
            steps = self.phases_grouped.get(phase_key, [])
            x = box_x + PHASE_INTERNAL_MARGIN + i * (col_w + PHASE_INTERNAL_MARGIN)
            frame = Frame(x, box_y, col_w, box_h,
                         leftPadding=0, rightPadding=0, topPadding=PHASE_BOX_PAD_TOP, bottomPadding=0,
                         showBoundary=0)
            story = self._build_phase_story(phase_key, steps, content_width=col_w)
            frame.addFromList(story, c)
            content_top_y = box_y + box_h - PHASE_BOX_PAD_TOP
            self._record_phase_breakdown(phase_key, steps, x, content_top_y, col_w, header_h,
                                         content_bottom_y=box_y)

    def _vertical_box_dims_for_width(self, box_w, phase_order):
        """Calculate phase box height for a given box width, using scaled banners."""
        content_w = box_w - (PHASE_INTERNAL_MARGIN * 2)
        header_h = self._banner_height_for_width(content_w)
        n = len(phase_order)
        total_content_h = sum(
            self.measure_phase_height(self.phases_grouped.get(pk, []), content_w, header_h=header_h)
            for pk in phase_order
        )
        box_h = total_content_h + n * 4 + (n - 1) * PHASE_BOX_V_GAP + PHASE_BOX_PAD_TOP + PHASE_BOX_PAD_BOTTOM
        return box_w, box_h, content_w

    def _draw_vertical_phases(self, c):
        phase_order = ['birdsong', 'daylight', 'evening']
        max_h = self.phases_top_y - BOTTOM_MARGIN

        # If all four override fields are set, skip the auto-sizing search and
        # use the rect directly.
        ov_x = self.sheet.phase_box_x_v
        ov_y = self.sheet.phase_box_y_v
        ov_w = self.sheet.phase_box_w_v
        ov_h = self.sheet.phase_box_h_v
        if all(v is not None for v in (ov_x, ov_y, ov_w, ov_h)):
            box_x = ov_x * inch
            box_y = ov_y * inch
            box_w = ov_w * inch
            box_h = ov_h * inch
            content_w = box_w - (PHASE_INTERNAL_MARGIN * 2)
            content_x = box_x + PHASE_INTERNAL_MARGIN
            self._draw_phase_box(c, box_x, box_y, box_w, box_h, rotated=False)
            self._phases_rect = (box_x, box_y, box_w, box_h)
            self._record_element(kind='phase_box', x=box_x, y=box_y, w=box_w, h=box_h)
            header_h = self._banner_height_for_width(content_w)
            cursor_y = box_y + box_h - PHASE_BOX_PAD_TOP
            for phase_key in phase_order:
                steps = self.phases_grouped.get(phase_key, [])
                content_h = self.measure_phase_height(steps, content_w, header_h=header_h)
                frame_h = content_h + 4
                frame_y = cursor_y - frame_h
                if frame_y < box_y:
                    frame_y = box_y
                    frame_h = cursor_y - box_y
                frame = Frame(content_x, frame_y, content_w, frame_h,
                             leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0,
                             showBoundary=0)
                story = self._build_phase_story(phase_key, steps, content_width=content_w)
                frame.addFromList(story, c)
                self._record_phase_breakdown(phase_key, steps, content_x, frame_y + frame_h, content_w, header_h,
                                             content_bottom_y=frame_y)
                cursor_y = frame_y - PHASE_BOX_V_GAP
            return

        min_w = PHASE_HEADER_MIN_W + (PHASE_INTERNAL_MARGIN * 2)
        max_w = BODY_W

        # Preferred width to avoid track staggering
        preferred_w = self._preferred_phase_track_width(phase_order)
        if self.content_boxes:
            # Reserve enough space for the widest content box (including its
            # track and legend needs)
            max_cb_min_w = CONTENT_BOX_MIN_W
            for cb in self.content_boxes:
                min_tw = self._min_track_width_for_content_box(cb)
                min_lw = self._min_legend_width_for_content_box(cb)
                cb_w = CONTENT_BOX_MIN_W
                if min_tw > 0:
                    cb_w = max(cb_w, min_tw + CONTENT_BOX_INTERNAL_MARGIN * 2)
                if min_lw > 0:
                    cb_w = max(cb_w, min_lw + CONTENT_BOX_INTERNAL_MARGIN * 2)
                max_cb_min_w = max(max_cb_min_w, cb_w)
            max_phase_w_for_cb = BODY_W - CONTENT_BOX_GAP - max_cb_min_w
            preferred_w = min(preferred_w, max_phase_w_for_cb)
        preferred_w = max(preferred_w, min_w)

        # Check if preferred width already fits
        _, box_h_at_pref, _ = self._vertical_box_dims_for_width(preferred_w, phase_order)
        if box_h_at_pref <= max_h:
            box_w, box_h, content_w = preferred_w, box_h_at_pref, preferred_w - (PHASE_INTERNAL_MARGIN * 2)
        else:
            # Binary search for smallest width where content fits
            lo, hi = preferred_w, max_w
            while hi - lo > 1:
                mid = (lo + hi) / 2
                _, mid_h, _ = self._vertical_box_dims_for_width(mid, phase_order)
                if mid_h <= max_h:
                    hi = mid
                else:
                    lo = mid
            box_w, box_h, content_w = self._vertical_box_dims_for_width(hi, phase_order)

            if box_h > max_h:
                print(f"WARNING: Phase box height ({box_h:.1f}pts) exceeds available space ({max_h:.1f}pts), clamping.")
                box_h = max_h

        content_x = X_MARGIN + PHASE_INTERNAL_MARGIN
        box_x = X_MARGIN
        box_y = self.phases_top_y - box_h
        self._draw_phase_box(c, box_x, box_y, box_w, box_h, rotated=False)
        self._phases_rect = (box_x, box_y, box_w, box_h)
        self._record_element(kind='phase_box', x=box_x, y=box_y, w=box_w, h=box_h)

        # Stack frames top-to-bottom with content-aware heights
        header_h = self._banner_height_for_width(content_w)
        cursor_y = self.phases_top_y - PHASE_BOX_PAD_TOP

        for i, phase_key in enumerate(phase_order):
            steps = self.phases_grouped.get(phase_key, [])
            content_h = self.measure_phase_height(steps, content_w, header_h=header_h)
            # Add a small buffer so the last flowable isn't dropped by the frame
            frame_h = content_h + 4
            frame_y = cursor_y - frame_h
            # Clamp frame to stay within the phase box
            if frame_y < box_y:
                frame_y = box_y
                frame_h = cursor_y - box_y
            frame = Frame(content_x, frame_y, content_w, frame_h,
                         leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0,
                         showBoundary=0)
            story = self._build_phase_story(phase_key, steps, content_width=content_w)
            frame.addFromList(story, c)
            self._record_phase_breakdown(phase_key, steps, content_x, frame_y + frame_h, content_w, header_h,
                                         content_bottom_y=frame_y)
            cursor_y = frame_y - PHASE_BOX_V_GAP

    def _draw_overridden_content_box(self, c, cb):
        """Render a ContentBox at its layout-mode override coords if all four are set.
        Returns True if the box was drawn (and should be skipped from auto-packing)."""
        ov_x = self._override(cb, 'x_h', 'x_v')
        ov_y = self._override(cb, 'y_h', 'y_v')
        ov_w = self._override(cb, 'w_h', 'w_v')
        ov_h = self._override(cb, 'h_h', 'h_v')
        if not all(v is not None for v in (ov_x, ov_y, ov_w, ov_h)):
            return False
        box_x = ov_x * inch
        box_y = ov_y * inch
        box_w = ov_w * inch
        box_h = ov_h * inch
        content_w = box_w - (CONTENT_BOX_INTERNAL_MARGIN * 2)
        if cb.paper_background:
            self._draw_phase_box(c, box_x, box_y, box_w, box_h, rotated=False)
        content_x = box_x + CONTENT_BOX_INTERNAL_MARGIN
        frame = Frame(content_x, box_y, content_w, box_h,
                     leftPadding=0, rightPadding=0,
                     topPadding=CONTENT_BOX_PAD_TOP, bottomPadding=CONTENT_BOX_PAD_BOTTOM,
                     showBoundary=0)
        story = self._build_content_box_story(cb, content_w)
        frame.addFromList(story, c)
        self._placed_boxes.append((box_x, box_y, box_w, box_h))
        self._record_element(kind='content_box', id=cb.id, title=cb.title or '',
                             x=box_x, y=box_y, w=box_w, h=box_h,
                             paper_background=cb.paper_background)
        self._record_content_box_breakdown(cb, box_x, box_y, box_w, box_h)
        return True

    def _draw_vertical_content_boxes(self, c):
        """Draw content boxes to the right of (and below) the phases box in vertical layout.

        Returns the list of ContentBoxes that the primary right-strip pack could
        not fit (so a secondary pass + overflow placer can handle them).
        """
        self._placed_boxes = []
        px, py, pw, ph = self._phases_rect
        target_h = self.phases_top_y - BOTTOM_MARGIN

        # Pull out any boxes with a full set of overrides; render them
        # immediately and exclude from the packing pass.
        auto_boxes = []
        for cb in self.content_boxes:
            if self._draw_overridden_content_box(c, cb):
                continue
            auto_boxes.append(cb)
        if not auto_boxes:
            return []

        # Available space to the right of the phases box
        right_x = px + pw + CONTENT_BOX_GAP
        right_w = (PAGE_W - X_MARGIN) - right_x

        if right_w < CONTENT_BOX_MIN_W:
            return list(auto_boxes)

        # Pre-compute minimum/preferred track widths and minimum legend widths
        # for each content box
        min_track_widths = {}
        preferred_track_widths = {}
        min_legend_widths = {}
        for cb in auto_boxes:
            min_track_widths[id(cb)] = self._min_track_width_for_content_box(cb)
            preferred_track_widths[id(cb)] = self._preferred_track_width_for_content_box(cb)
            min_legend_widths[id(cb)] = self._min_legend_width_for_content_box(cb)

        remaining = list(auto_boxes)
        leftovers = []
        cursor_y_top = self.phases_top_y
        row_start_x = right_x
        row_avail_w = right_w

        while remaining:
            # Try to fit as many as possible in this row at even widths.
            # Every box in the row (including the last/single one) must fit
            # vertically without clamping; if the only remaining box doesn't
            # fit naturally, defer it to the overflow placer instead of
            # squishing it.
            row_count = len(remaining)
            while row_count > 0:
                total_gaps = CONTENT_BOX_GAP * (row_count - 1)
                even_w = (row_avail_w - total_gaps) / row_count
                if even_w >= CONTENT_BOX_MIN_W:
                    content_w_at_even = even_w - (CONTENT_BOX_INTERNAL_MARGIN * 2)
                    all_fit = True
                    for cb in remaining[:row_count]:
                        _, h, _ = self._content_box_dims_for_width(cb, even_w)
                        if h > target_h:
                            all_fit = False
                            break
                        # Check tracks fit at this content width
                        min_tw = min_track_widths[id(cb)]
                        if min_tw > 0 and content_w_at_even < min_tw:
                            all_fit = False
                            break
                        # Check legends fit at this content width
                        min_lw = min_legend_widths[id(cb)]
                        if min_lw > 0 and content_w_at_even < min_lw:
                            all_fit = False
                            break
                    if all_fit:
                        break
                row_count -= 1

            if row_count == 0:
                # Can't fit any more boxes in this strip — defer to caller
                leftovers.extend(remaining)
                break

            # Place this row of boxes
            row_boxes = remaining[:row_count]
            remaining = remaining[row_count:]
            total_gaps = CONTENT_BOX_GAP * (row_count - 1)
            even_w = (row_avail_w - total_gaps) / row_count

            # Calculate natural width for each box (narrowest that fits height)
            box_widths = []
            for cb in row_boxes:
                min_tw = min_track_widths[id(cb)]
                pref_tw = preferred_track_widths[id(cb)]
                min_lw = min_legend_widths[id(cb)]
                # Minimum box width to fit tracks (with overlap)
                min_w_for_tracks = (min_tw + CONTENT_BOX_INTERNAL_MARGIN * 2) if min_tw > 0 else CONTENT_BOX_MIN_W
                # Minimum box width to fit legends (legend asks for LEGEND_MIN_WIDTH)
                min_w_for_legends = (min_lw + CONTENT_BOX_INTERNAL_MARGIN * 2) if min_lw > 0 else CONTENT_BOX_MIN_W
                fallback_min = max(CONTENT_BOX_MIN_W, min_w_for_tracks, min_w_for_legends)
                # Preferred box width to avoid staggering (legends use the same min as preferred)
                pref_w_for_tracks = (pref_tw + CONTENT_BOX_INTERNAL_MARGIN * 2) if pref_tw > 0 else CONTENT_BOX_MIN_W
                preferred_min = max(CONTENT_BOX_MIN_W, pref_w_for_tracks, min_w_for_legends)

                # Binary search for narrowest width that fits height
                _, h_at_even, _ = self._content_box_dims_for_width(cb, even_w)
                if h_at_even > target_h:
                    # Use even_w (clamped height)
                    box_widths.append(even_w)
                else:
                    # Try preferred (non-staggered) width first, fall back to minimum
                    lo = preferred_min
                    if lo > even_w:
                        lo = fallback_min
                    else:
                        _, h_at_pref, _ = self._content_box_dims_for_width(cb, lo)
                        if h_at_pref > target_h:
                            lo = fallback_min
                    lo, hi = lo, even_w
                    while hi - lo > 1:
                        mid = (lo + hi) / 2
                        _, mid_h, _ = self._content_box_dims_for_width(cb, mid)
                        if mid_h <= target_h:
                            hi = mid
                        else:
                            lo = mid
                    box_widths.append(hi)

            # Distribute extra space evenly before, between, and after boxes
            total_box_w = sum(box_widths)
            extra_space = row_avail_w - total_box_w - total_gaps
            # n boxes create n+1 slots (before first, between each, after last)
            padding = extra_space / (row_count + 1)
            extra_per_gap = padding  # added to each gap between boxes
            cursor_x = row_start_x + padding  # offset before first box

            row_bottom_y = cursor_y_top  # track the lowest bottom in this row
            for i, (cb, box_w) in enumerate(zip(row_boxes, box_widths)):
                _, box_h, content_w = self._content_box_dims_for_width(cb, box_w)
                box_y = cursor_y_top - box_h

                if cb.paper_background:
                    self._draw_phase_box(c, cursor_x, box_y, box_w, box_h, rotated=False)
                content_x = cursor_x + CONTENT_BOX_INTERNAL_MARGIN
                frame = Frame(content_x, box_y, content_w, box_h,
                             leftPadding=0, rightPadding=0,
                             topPadding=CONTENT_BOX_PAD_TOP, bottomPadding=CONTENT_BOX_PAD_BOTTOM,
                             showBoundary=0)
                story = self._build_content_box_story(cb, content_w)
                frame.addFromList(story, c)
                self._placed_boxes.append((cursor_x, box_y, box_w, box_h))
                self._record_element(kind='content_box', id=cb.id, title=cb.title or '',
                                     x=cursor_x, y=box_y, w=box_w, h=box_h,
                                     paper_background=cb.paper_background)
                self._record_content_box_breakdown(cb, cursor_x, box_y, box_w, box_h)

                if box_y < row_bottom_y:
                    row_bottom_y = box_y
                gap = CONTENT_BOX_GAP + (extra_per_gap if row_count > 1 else 0)
                cursor_x += box_w + gap

            # Next row starts below the tallest box in this row
            cursor_y_top = row_bottom_y - CONTENT_BOX_GAP
            target_h = cursor_y_top - BOTTOM_MARGIN
            if target_h <= 0:
                leftovers.extend(remaining)
                break

        return leftovers

    def _draw_horizontal_content_boxes(self, c):
        """Draw content boxes below the phases box using column-first packing.

        Boxes are stacked top-to-bottom in a column, overflowing to the next
        column to the right when vertical space runs out.  Mirrors the logic
        of _draw_vertical_content_boxes but transposed (columns instead of rows).

        Returns the list of ContentBoxes that the primary below-phases pack
        could not fit (so a secondary pass + overflow placer can handle them).
        """
        self._placed_boxes = []
        px, py, pw, ph = self._phases_rect

        # Pull out any boxes with a full set of overrides; render them
        # immediately and exclude from the packing pass.
        auto_boxes = []
        for cb in self.content_boxes:
            if self._draw_overridden_content_box(c, cb):
                continue
            auto_boxes.append(cb)
        if not auto_boxes:
            return []

        below_top = py - CONTENT_BOX_GAP
        avail_h = below_top - BOTTOM_MARGIN
        if avail_h <= 0:
            return list(auto_boxes)

        # Pre-compute track + legend width constraints
        min_track_widths = {}
        preferred_track_widths = {}
        min_legend_widths = {}
        for cb in auto_boxes:
            min_track_widths[id(cb)] = self._min_track_width_for_content_box(cb)
            preferred_track_widths[id(cb)] = self._preferred_track_width_for_content_box(cb)
            min_legend_widths[id(cb)] = self._min_legend_width_for_content_box(cb)

        remaining = list(auto_boxes)
        leftovers = []
        col_start_x = X_MARGIN
        col_avail_w = BODY_W

        while remaining:
            # Try to fit col_count boxes stacked in the current column
            col_count = len(remaining)
            while col_count > 0:
                total_gaps = CONTENT_BOX_GAP * (col_count - 1)
                even_h = (avail_h - total_gaps) / col_count
                content_w_at_col = col_avail_w - (CONTENT_BOX_INTERNAL_MARGIN * 2)
                all_fit = True
                for cb in remaining[:col_count]:
                    # Every box (including a sole survivor) must fit naturally;
                    # otherwise defer it to the overflow placer which renders
                    # at its desired size.
                    _, h, _ = self._content_box_dims_for_width(cb, col_avail_w)
                    if h > even_h:
                        all_fit = False
                        break
                    # Check tracks fit at this content width
                    min_tw = min_track_widths[id(cb)]
                    if min_tw > 0 and content_w_at_col < min_tw:
                        all_fit = False
                        break
                    # Check legends fit at this content width
                    min_lw = min_legend_widths[id(cb)]
                    if min_lw > 0 and content_w_at_col < min_lw:
                        all_fit = False
                        break
                if all_fit:
                    break
                col_count -= 1

            if col_count == 0:
                leftovers.extend(remaining)
                break

            # Determine boxes for this column
            col_boxes = remaining[:col_count]
            remaining = remaining[col_count:]
            total_gaps = CONTENT_BOX_GAP * (col_count - 1)
            even_h = (avail_h - total_gaps) / col_count

            # Binary search for narrowest column width where all boxes fit within even_h
            # Lower bound: max of CONTENT_BOX_MIN_W and track/legend-required widths
            lo = CONTENT_BOX_MIN_W
            for cb in col_boxes:
                min_tw = min_track_widths[id(cb)]
                pref_tw = preferred_track_widths[id(cb)]
                min_lw = min_legend_widths[id(cb)]
                if pref_tw > 0:
                    min_w_for_tracks = pref_tw + CONTENT_BOX_INTERNAL_MARGIN * 2
                elif min_tw > 0:
                    min_w_for_tracks = min_tw + CONTENT_BOX_INTERNAL_MARGIN * 2
                else:
                    min_w_for_tracks = CONTENT_BOX_MIN_W
                lo = max(lo, min_w_for_tracks)
                if min_lw > 0:
                    lo = max(lo, min_lw + CONTENT_BOX_INTERNAL_MARGIN * 2)

            # Check if preferred width is feasible; fall back to minimum tracks
            if lo > col_avail_w:
                lo = CONTENT_BOX_MIN_W
                for cb in col_boxes:
                    min_tw = min_track_widths[id(cb)]
                    min_lw = min_legend_widths[id(cb)]
                    if min_tw > 0:
                        lo = max(lo, min_tw + CONTENT_BOX_INTERNAL_MARGIN * 2)
                    if min_lw > 0:
                        lo = max(lo, min_lw + CONTENT_BOX_INTERNAL_MARGIN * 2)

            hi = col_avail_w
            # Binary search: find narrowest width where all boxes fit in even_h
            while hi - lo > 1:
                mid = (lo + hi) / 2
                fits = True
                for cb in col_boxes:
                    _, h, _ = self._content_box_dims_for_width(cb, mid)
                    if h > even_h:
                        fits = False
                        break
                if fits:
                    hi = mid
                else:
                    lo = mid
            col_w = hi

            # Calculate actual box heights at col_w (no clamping — the row-fit
            # check above already guarantees each box fits in even_h)
            box_heights = []
            for cb in col_boxes:
                _, box_h, _ = self._content_box_dims_for_width(cb, col_w)
                box_heights.append(box_h)

            # Distribute extra vertical space evenly before, between, and after boxes
            total_box_h = sum(box_heights)
            extra_space = avail_h - total_box_h - total_gaps
            padding = extra_space / (col_count + 1)
            cursor_y = below_top - padding  # offset before first box

            col_right_x = col_start_x  # track rightmost edge for next column
            for cb, box_h in zip(col_boxes, box_heights):
                box_y = cursor_y - box_h
                _, _, content_w = self._content_box_dims_for_width(cb, col_w)

                if cb.paper_background:
                    self._draw_phase_box(c, col_start_x, box_y, col_w, box_h, rotated=False)
                content_x = col_start_x + CONTENT_BOX_INTERNAL_MARGIN
                frame = Frame(content_x, box_y, content_w, box_h,
                             leftPadding=0, rightPadding=0,
                             topPadding=CONTENT_BOX_PAD_TOP, bottomPadding=CONTENT_BOX_PAD_BOTTOM,
                             showBoundary=0)
                story = self._build_content_box_story(cb, content_w)
                frame.addFromList(story, c)
                self._placed_boxes.append((col_start_x, box_y, col_w, box_h))
                self._record_element(kind='content_box', id=cb.id, title=cb.title or '',
                                     x=col_start_x, y=box_y, w=col_w, h=box_h,
                                     paper_background=cb.paper_background)
                self._record_content_box_breakdown(cb, col_start_x, box_y, col_w, box_h)

                gap = CONTENT_BOX_GAP + (padding if col_count > 1 else 0)
                cursor_y = box_y - gap

            # Next column starts to the right
            col_start_x += col_w + CONTENT_BOX_GAP
            col_avail_w -= col_w + CONTENT_BOX_GAP
            if col_avail_w < CONTENT_BOX_MIN_W:
                leftovers.extend(remaining)
                break

        return leftovers

    def _overflow_box_dims(self, cb):
        """Pick a natural box size for an overflow content box.

        Width: clamp the preferred track / minimum legend width into the page
        body. Height: whatever the content needs at that width (no clamp —
        overflow boxes render at their desired size and may overlap).
        """
        pref_tw = self._preferred_track_width_for_content_box(cb)
        min_lw = self._min_legend_width_for_content_box(cb)
        target_content_w = max(pref_tw, min_lw,
                               CONTENT_BOX_MIN_W - CONTENT_BOX_INTERNAL_MARGIN * 2)
        box_w = target_content_w + CONTENT_BOX_INTERNAL_MARGIN * 2
        max_box_w = PAGE_W - X_MARGIN * 2
        if box_w > max_box_w:
            box_w = max_box_w
        _, box_h, _ = self._content_box_dims_for_width(cb, box_w)
        return box_w, box_h

    def _find_open_slot(self, w, h, step=0.25 * inch):
        """Scan on a coarse grid for the first (x, y) where a ``w × h`` rect
        doesn't overlap the phase box or any already-placed content box.
        Returns ``(x, y)`` or ``None`` if no slot is found.

        Bounded above by ``self.phases_top_y`` so overflow boxes never collide
        with the faction title / header band — same constraint the Phase box
        respects.
        """
        top_y = self.phases_top_y
        x_lo = X_MARGIN
        x_hi = PAGE_W - X_MARGIN - w
        y_lo = BOTTOM_MARGIN
        y_hi = top_y - h
        if x_hi < x_lo or y_hi < y_lo:
            return None
        # Top-down, left-to-right so overflow boxes appear near the top first.
        y = y_hi
        while y >= y_lo:
            x = x_lo
            while x <= x_hi:
                if self._content_box_fits(x, y, w, h, top_y=top_y):
                    return x, y
                x += step
            y -= step
        return None

    def _draw_overflow_content_boxes(self, c, leftovers):
        """Render content boxes that the auto-packer could not fit.

        For each leftover, first try to find an open non-overlapping slot
        anywhere on the page. If none exists, render the box at its desired
        natural size, horizontally centered on the page (allowing overlap
        with other boxes). Vertical position cascades downward so multiple
        unfit boxes don't sit directly on top of each other.

        Boxes are recorded via ``_record_element`` exactly like normal boxes
        so the editor / preview pick them up automatically.
        """
        if not leftovers:
            return

        center_y_top = self.phases_top_y - 0.5 * inch
        center_step = 0.25 * inch
        center_index = 0

        for cb in leftovers:
            box_w, box_h = self._overflow_box_dims(cb)

            slot = self._find_open_slot(box_w, box_h)
            if slot is not None:
                box_x, box_y = slot
            else:
                # No fit — render at desired size, horizontally centered,
                # cascading down so subsequent boxes are individually grabbable.
                box_x = (PAGE_W - box_w) / 2
                offset = center_step * center_index
                box_y = center_y_top - offset - box_h
                center_index += 1
                # Keep the top edge under the header band when possible.
                max_top = self.phases_top_y
                if box_y + box_h > max_top:
                    box_y = max_top - box_h

            content_w = box_w - CONTENT_BOX_INTERNAL_MARGIN * 2
            if cb.paper_background:
                self._draw_phase_box(c, box_x, box_y, box_w, box_h, rotated=False)
            content_x = box_x + CONTENT_BOX_INTERNAL_MARGIN
            frame = Frame(content_x, box_y, content_w, box_h,
                         leftPadding=0, rightPadding=0,
                         topPadding=CONTENT_BOX_PAD_TOP, bottomPadding=CONTENT_BOX_PAD_BOTTOM,
                         showBoundary=0)
            story = self._build_content_box_story(cb, content_w)
            frame.addFromList(story, c)
            self._placed_boxes.append((box_x, box_y, box_w, box_h))
            self._record_element(kind='content_box', id=cb.id, title=cb.title or '',
                                 x=box_x, y=box_y, w=box_w, h=box_h,
                                 paper_background=cb.paper_background)
            self._record_content_box_breakdown(cb, box_x, box_y, box_w, box_h)

    def _build_steps_story(self, steps, avail_w, centered=False, phase_indent=0):
        """Build flowables for a list of PhaseSteps (no header). Reused by phases and content boxes.

        ``phase_indent`` shifts the body, actions, and child flowables of a
        single-step entry inward from the left edge. Used for phase boxes
        (birdsong/daylight/evening) so a lone step doesn't sit flush left.
        Content boxes pass 0 (the default) to keep their centered layout.
        """
        from reportlab.platypus import Table, TableStyle

        story = []
        ICON_COL_W = 0.325 * inch   # SVGs right-aligned within this width
        ICON_TEXT_GAP = 0.015 * inch
        TEXT_COL_X = ICON_COL_W + ICON_TEXT_GAP
        single_step = len(steps) == 1
        single_indent = phase_indent if (single_step and not centered) else 0

        if centered and single_step:
            body_style = self.content_box_text_style
        else:
            body_style = self.step_body_style

        for step in steps:
            markup = format_step_markup(step.text, sheet=self.sheet)
            para = Paragraph(markup, body_style)

            # Compute extra bottom padding to compensate for autoLeading underreporting
            text_w = single_indent if single_step else TEXT_COL_X
            content_w = avail_w - text_w
            probe = Paragraph(markup, body_style)
            _, wrap_h = probe.wrap(content_w, 9999)
            extra_h = true_paragraph_height(probe, content_w) - wrap_h

            # Tighten the rendered paragraph's large-font line spacing
            para.wrap(content_w, 9999)
            tighten_large_font_lines(para)

            if single_step:
                # No number icon — text takes (avail_w - single_indent).
                # If the first line is base-font-only AND has an inline icon,
                # the icon would sit above the cap-height of the small text and
                # overlap the header band above. Pad it down a few points.
                if first_line_has_inline_icon_only_base_font(para):
                    from reportlab.platypus import Spacer
                    story.append(Spacer(1, SINGLE_STEP_INLINE_ICON_PAD_TOP))
                if single_indent:
                    t = Table([['', para]], colWidths=[single_indent, content_w])
                    t.setStyle(TableStyle([
                        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                        ('LEFTPADDING', (0, 0), (-1, -1), 0),
                        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
                        ('TOPPADDING', (0, 0), (-1, -1), 0),
                        ('BOTTOMPADDING', (0, 0), (-1, -1), extra_h),
                    ]))
                    story.append(t)
                else:
                    story.append(para)
            else:
                svg_drawing = self._phase_number_svgs.get(step.number % 10)
                if svg_drawing:
                    ICON_NUDGE_DOWN = 4
                    t = Table([[svg_drawing, para]], colWidths=[TEXT_COL_X, avail_w - TEXT_COL_X])
                    t.setStyle(TableStyle([
                        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                        ('ALIGN', (0, 0), (0, -1), 'RIGHT'),
                        ('LEFTPADDING', (0, 0), (-1, -1), 0),
                        ('RIGHTPADDING', (0, 0), (0, -1), TEXT_COL_X - ICON_COL_W),
                        ('RIGHTPADDING', (1, 0), (1, -1), 0),
                        ('TOPPADDING', (0, 0), (0, -1), ICON_NUDGE_DOWN),
                        ('TOPPADDING', (1, 0), (1, -1), 0),
                        ('BOTTOMPADDING', (0, 0), (-1, -1), extra_h),
                    ]))
                    story.append(t)
                else:
                    story.append(para)

            # Append StepAction flowables below the step
            indent = single_indent if single_step else TEXT_COL_X
            action_flowables = self._build_action_flowables(step, avail_w, indent)
            story.extend(action_flowables)

            # Append BorderedBox + Track flowables below the actions, in the
            # user's intermixed order from the editor (boxes and tracks share
            # an `order` sequence).
            for child in step.ordered_children:
                obj = child['obj']
                if child['kind'] == 'box':
                    box_h = BORDERED_BOX_HEIGHTS.get(obj.height, BORDERED_BOX_HEIGHTS['medium'])
                    body_markup = format_step_markup(obj.body, sheet=self.sheet) if obj.body else ''
                    bf = BorderedBoxFlowable(
                        title=obj.title,
                        body_markup=body_markup,
                        total_width=avail_w - indent,
                        box_height=box_h,
                        body_style=self.step_body_style,
                        element_color_hex=self._resolve_element_color(obj.element_color),
                    )
                    if indent:
                        t = Table([['', bf]], colWidths=[indent, avail_w - indent])
                        t.setStyle(TableStyle([
                            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                            ('LEFTPADDING', (0, 0), (-1, -1), 0),
                            ('RIGHTPADDING', (0, 0), (-1, -1), 0),
                            ('TOPPADDING', (0, 0), (-1, -1), BORDERED_BOX_TITLE_SIZE / 2),
                            ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
                        ]))
                        story.append(t)
                    else:
                        story.append(bf)
                elif child['kind'] == 'track':
                    slots = list(obj.slots.all())
                    tf = TrackFlowable(
                        track=obj,
                        slots=slots,
                        total_width=avail_w - indent,
                        body_style=self.step_body_style,
                        faction_color=self.faction_color,
                        engine=self,
                    )
                    if indent:
                        t = Table([['', tf]], colWidths=[indent, avail_w - indent])
                        t.setStyle(TableStyle([
                            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                            ('LEFTPADDING', (0, 0), (-1, -1), 0),
                            ('RIGHTPADDING', (0, 0), (-1, -1), 0),
                            ('TOPPADDING', (0, 0), (-1, -1), TRACK_TITLE_GAP),
                            ('BOTTOMPADDING', (0, 0), (-1, -1), TRACK_BOTTOM_PAD),
                        ]))
                        story.append(t)
                    else:
                        story.append(tf)
                elif child['kind'] == 'legend':
                    lf = LegendFlowable(
                        legend=obj,
                        total_width=avail_w - indent,
                        body_style=self.step_body_style,
                        title_color_hex=self._on_tan_hex,
                    )
                    if indent:
                        t = Table([['', lf]], colWidths=[indent, avail_w - indent])
                        t.setStyle(TableStyle([
                            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                            ('LEFTPADDING', (0, 0), (-1, -1), 0),
                            ('RIGHTPADDING', (0, 0), (-1, -1), 0),
                            ('TOPPADDING', (0, 0), (-1, -1), LEGEND_BLOCK_TITLE_GAP),
                            ('BOTTOMPADDING', (0, 0), (-1, -1), LEGEND_ROW_GAP),
                        ]))
                        story.append(t)
                    else:
                        story.append(lf)
                elif child['kind'] == 'scale':
                    sf = ScaleFlowable(
                        scale=obj,
                        total_width=avail_w - indent,
                        body_style=self.step_body_style,
                        title_color_hex=self._on_tan_hex,
                        centered=centered,
                    )
                    if indent:
                        t = Table([['', sf]], colWidths=[indent, avail_w - indent])
                        t.setStyle(TableStyle([
                            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                            ('LEFTPADDING', (0, 0), (-1, -1), 0),
                            ('RIGHTPADDING', (0, 0), (-1, -1), 0),
                            ('TOPPADDING', (0, 0), (-1, -1), SCALE_TOP_PAD),
                            ('BOTTOMPADDING', (0, 0), (-1, -1), SCALE_BOTTOM_PAD),
                        ]))
                        story.append(t)
                    else:
                        story.append(sf)

        return story

    def _build_phase_story(self, phase_key, steps, content_width=None):
        story = []
        header_config = PHASE_HEADERS[phase_key]

        # Available width for content wrapping
        avail_w = content_width if content_width else BODY_W

        # Use banner image scaled to fill content width
        header_path = header_config['banner']
        if os.path.exists(header_path):
            from PIL import Image as PILImage
            with PILImage.open(header_path) as pil_img:
                aspect = pil_img.size[0] / pil_img.size[1]
            target_w = avail_w
            if target_w <= PHASE_HEADER_LOCK_W:
                target_h = max(target_w / aspect, PHASE_HEADER_MIN_H)
            else:
                target_h = PHASE_HEADER_LOCK_W / aspect
            phase_name = _pdf_label('phases', phase_key, self.sheet, default=phase_key.title())
            banner = BannerWithText(header_path, target_w, target_h, phase_name)
            banner.hAlign = 'LEFT'
            story.append(banner)

        story.extend(self._build_steps_story(steps, avail_w, phase_indent=SINGLE_STEP_PHASE_INDENT))
        return story

    def _build_content_box_story(self, content_box, content_width):
        """Build flowables for a content box: centered title, text, then steps."""
        story = []
        if content_box.title:
            story.append(Paragraph(content_box.title, self.content_box_title_style))
        if content_box.text:
            markup = format_step_markup(content_box.text, sheet=self.sheet)
            story.append(Paragraph(markup, self.content_box_text_style))
        steps = list(content_box.steps.all())
        story.extend(self._build_steps_story(steps, content_width, centered=True))
        return story

    def _draw_background(self, c):
        draw_faction_background(c, self.sheet.faction)


    def _draw_top_band(self, c):
        # Resolve ability-bar layout before drawing anything so the title bar
        # SVG can stretch to match the (possibly grown or shrunk) ability bar.
        self._prepare_ability_bar_layout()
        self._draw_title_bar(c)
        self._draw_ability_boxes(c)
        # Record the whole header region as a non-editable container for the preview.
        header_top = self.faction_top_bar_top
        header_bottom = self.title_bar_y - FACTION_TOP_BAR_NUDGE - (self.ability_bar_h_actual - FACTION_TOP_BAR_NUDGE)
        header_h = header_top - header_bottom
        self._record_element(kind='header_bar',
                             x=X_MARGIN, y=header_bottom,
                             w=BODY_W, h=header_h,
                             label=self.sheet.faction.faction_name)

    def _prepare_ability_bar_layout(self):
        """Resolve ability widths/sizes/box_h and apply downstream Y shifts.

        Sets self._ability_layout (consumed by _draw_ability_boxes), updates
        self.ability_bar_extra_h / self.ability_bar_h_actual, and shifts
        self.phases_top_y so the phases area moves with the bar.
        """
        abilities = list(self.sheet.abilities.order_by('order'))
        bar_w = BODY_W * COLOR_BAR_W_RATIO
        crafted_h = 0.767 * inch
        crafted_w = 0
        has_crafted = self.sheet.include_crafted_items and os.path.exists(CRAFTED_ITEMS_SVG)
        if has_crafted:
            crafted_w = crafted_h * (64.93 / 19.46)

        # Match the resolver's default so delta_h is 0 when no extension is
        # needed — ABILITY_BAR_BOTTOM_EXTRA is "free" usable space that doesn't
        # count as bar growth.
        default_box_h = ABILITY_BAR_H - FACTION_TOP_BAR_NUDGE + ABILITY_BAR_BOTTOM_EXTRA
        flavor_text = (self.sheet.flavor_text or '').strip()
        icon_w = (self._ability_icon.width + 4) if self._ability_icon else 0

        layout = self._resolve_ability_layout(
            abilities, bar_w, crafted_w, icon_w,
            has_crafted=has_crafted, flavor_text=flavor_text,
        )
        widths = layout['widths']
        sizes = layout['sizes']
        box_h = layout['box_h']
        flavor_w = layout['flavor_w']
        flavor_style = layout['flavor_style'] or self.flavor_text_style
        title_size = layout.get('title_size')

        delta_h = box_h - default_box_h
        # ability_bar_h_actual is the bar's "tracked" height — what downstream
        # geometry (header_bottom, title bar SVG stretch) follows. The bottom
        # extra is invisible to those callers; only growth past the default
        # counts.
        self.ability_bar_h_actual = (box_h - ABILITY_BAR_BOTTOM_EXTRA) + FACTION_TOP_BAR_NUDGE
        self.ability_bar_extra_h = delta_h
        self.phases_top_y -= delta_h

        self._ability_layout = {
            'abilities': abilities,
            'widths': widths,
            'sizes': sizes,
            'title_size': title_size,
            'box_h': box_h,
            'bar_w': bar_w,
            'icon_w': icon_w,
            'crafted_w': crafted_w,
            'crafted_h': crafted_h,
            'flavor_w': flavor_w,
            'flavor_style': flavor_style,
            'flavor_text': flavor_text,
            'delta_h': delta_h,
        }

    def _draw_title_bar(self, c):
        img_w = self.faction_top_bar_w
        # Stretch the top bar SVG by the ability-bar delta so its bottom edge
        # follows the (possibly grown or shrunk) ability bar.
        img_h = self.faction_top_bar_h + self.ability_bar_extra_h
        img_x = (PAGE_W - img_w) / 2
        bar_w = BODY_W * COLOR_BAR_W_RATIO
        bar_x = (PAGE_W - bar_w) / 2

        # Faction top bar SVG — top edge at self.faction_top_bar_top
        c.set_layer_tag('box')
        if os.path.exists(FACTION_TOP_BAR_SVG):
            drawing = svg2rlg(FACTION_TOP_BAR_SVG)
            if drawing:
                sx = img_w / drawing.width
                sy = img_h / drawing.height
                drawing.width = img_w
                drawing.height = img_h
                drawing.scale(sx, sy)
                img_y = self.faction_top_bar_top - img_h
                renderPDF.draw(drawing, c, img_x, img_y)

        # Color bar on top
        c.setFillColor(self.faction_color)
        c.rect(bar_x, self.title_bar_y, bar_w, TITLE_BAR_H, fill=1, stroke=0)

        # Optional header image — same width as the color bar, bottom-aligned with
        # the bar bottom, allowed to overflow upward. Drawn before the faction
        # name so the text sits on top.
        c.set_layer_tag('box')
        if self.sheet.header_image:
            try:
                hdr_path = self.sheet.header_image.path
            except (ValueError, NotImplementedError):
                hdr_path = None
            if hdr_path and os.path.exists(hdr_path):
                try:
                    from reportlab.lib.utils import ImageReader
                    hdr_reader = ImageReader(hdr_path)
                    hiw, hih = hdr_reader.getSize()
                    if hiw > 0 and hih > 0:
                        hdr_h = bar_w * hih / hiw
                        c.drawImage(hdr_path, bar_x, self.title_bar_y,
                                    width=bar_w, height=hdr_h, mask='auto')
                        try:
                            header_image_url = self.sheet.header_image.url
                        except (ValueError, NotImplementedError):
                            header_image_url = ''
                        self._record_element(kind='header_image',
                                             x=bar_x, y=self.title_bar_y,
                                             w=bar_w, h=hdr_h,
                                             image_url=header_image_url)
                except Exception:
                    pass

        # Draw faction name centered
        c.set_layer_tag('fg')
        c.setFillColor(self.faction_name_color)
        c.setFont(self.faction_name_font, self.faction_name_font_size)
        c.drawCentredString(PAGE_W / 2, self.title_bar_y + FACTION_NAME_Y_OFFSET, self.sheet.faction.faction_name)

        # Record color bar + faction name for preview
        faction_hex = self.sheet.faction.color or '#5B4A8A'
        self._record_element(kind='header_color_bar',
                             x=bar_x, y=self.title_bar_y,
                             w=bar_w, h=TITLE_BAR_H,
                             label=self.sheet.faction.faction_name,
                             fill=faction_hex,
                             text_color=self.ink_on_faction_hex)

    def _calculate_ability_widths(self, abilities, available_w, icon_w, size_weights=None):
        """Calculate proportional box widths based on body text length.

        size_weights: optional list of multipliers (one per ability) applied to
        char count. Pass body_size/MAX_BODY to give shrunk abilities less width
        so neighbors get more room.

        Equal-width fast-path is preserved here for callers that pass only
        abilities — when flavor competes alongside abilities (see
        _calculate_item_widths) the fast-path isn't useful because flavor's
        weight differs from abilities' and a flat split would over-allocate.
        """
        n = len(abilities)
        total_gap = ABILITY_GAP * (n - 1)
        distributable_w = available_w - total_gap

        if n == 0:
            return []
        if n == 1:
            return [distributable_w]

        if size_weights is None:
            size_weights = [1.0] * n

        char_counts = [len(a.body) * size_weights[i] for i, a in enumerate(abilities)]

        min_widths = []
        for a in abilities:
            title_w = pdfmetrics.stringWidth(a.title, 'Baskerville', 8)
            min_widths.append(max(MIN_ABILITY_BOX_W, title_w + icon_w + 4))

        # Equal-width default: if every ability's title+body fits comfortably at
        # equal width, just split evenly. Only valid for unweighted calls — when
        # weights are non-uniform, the caller wants proportional allocation.
        total_chars = sum(char_counts)
        if total_chars > 0:
            uniform_weights = all(w == size_weights[0] for w in size_weights)
            equal_w = distributable_w / n
            box_h = ABILITY_BAR_H - FACTION_TOP_BAR_NUDGE
            if uniform_weights and all(equal_w >= mw for mw in min_widths):
                all_fit = True
                for a in abilities:
                    story = [
                        Paragraph(f"<b>{a.title}</b>", self.ability_title_style),
                        Paragraph(format_inline_images(a.body, img_height=ABILITY_INLINE_IMG_H, sheet=self.sheet), self.ability_body_style),
                    ]
                    used_h = 0
                    for flowable in story:
                        _, h = flowable.wrap(equal_w - icon_w, 9999)
                        used_h += h
                    if used_h > box_h:
                        all_fit = False
                        break
                if all_fit:
                    return [equal_w] * n

        items = [{'char_weight': c, 'min_w': mw}
                 for c, mw in zip(char_counts, min_widths)]
        return self._calculate_item_widths(items, available_w)

    def _calculate_item_widths(self, items, available_w):
        """Generalized proportional width allocator. Used by the abilities
        path and by the "flavor competes alongside abilities" path so flavor
        goes through the same proportional logic as an ability.

        items: list of dicts with keys ``char_weight`` (already adjusted for
        font size / italic / no-title vertical savings as appropriate) and
        ``min_w`` (already includes any per-item icon width or title width).
        Returns a list of widths in the same order as ``items``.
        """
        n = len(items)
        if n == 0:
            return []
        total_gap = ABILITY_GAP * (n - 1)
        distributable_w = available_w - total_gap
        if n == 1:
            return [distributable_w]

        char_counts = [it['char_weight'] for it in items]
        min_widths = [it['min_w'] for it in items]
        total_chars = sum(char_counts)

        if total_chars == 0:
            return [max(distributable_w / n, mw) for mw in min_widths]

        widths = [(count / total_chars) * distributable_w for count in char_counts]

        # Iteratively clamp below-minimum widths and redistribute
        for _ in range(n):
            clamped_total = 0.0
            unclamped_indices = []
            unclamped_chars = 0

            for i, w in enumerate(widths):
                if w < min_widths[i]:
                    widths[i] = min_widths[i]
                    clamped_total += min_widths[i]
                else:
                    unclamped_indices.append(i)
                    unclamped_chars += char_counts[i]

            if not unclamped_indices or clamped_total == 0:
                break

            remaining_w = distributable_w - clamped_total
            if unclamped_chars == 0:
                break

            all_fit = True
            for i in unclamped_indices:
                new_w = (char_counts[i] / unclamped_chars) * remaining_w
                if new_w < min_widths[i]:
                    all_fit = False
                widths[i] = new_w

            if all_fit:
                break

        return widths

    def _calculate_flavor_text_layout(self, text, max_w, box_h):
        """Find the best font size and narrowest width for flavor text.

        Tries font sizes from FLAVOR_TEXT_MAX_SIZE down to FLAVOR_TEXT_BASE_SIZE.
        Larger sizes are only selected if the text fits at MIN_FLAVOR_TEXT_W.
        At the base size, binary-searches for the narrowest width that fits.
        Returns (width, style).
        """
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.lib.enums import TA_LEFT
        from reportlab.lib import colors

        pad = FLAVOR_TEXT_PADDING * 2
        usable_h = box_h - pad

        for size in range(FLAVOR_TEXT_MAX_SIZE, FLAVOR_TEXT_BASE_SIZE, -1):
            leading = size + 2
            style = ParagraphStyle(
                f'FlavorText_{size}',
                fontName='Baskerville-Italic',
                fontSize=size,
                leading=leading,
                textColor=colors.black,
                alignment=TA_LEFT,
            )
            p = Paragraph(text, style)
            _, h = p.wrap(MIN_FLAVOR_TEXT_W - pad, 9999)
            if h <= usable_h:
                return MIN_FLAVOR_TEXT_W, style

        style = self.flavor_text_style
        p = Paragraph(text, style)
        _, h = p.wrap(max_w - pad, 9999)
        if h > usable_h:
            return max_w, style

        lo = MIN_FLAVOR_TEXT_W
        hi = max_w
        while hi - lo > 0.5:
            mid = (lo + hi) / 2
            p = Paragraph(text, style)
            _, h = p.wrap(mid - pad, 9999)
            if h <= usable_h:
                hi = mid
            else:
                lo = mid

        return hi, style

    def _flavor_style_at_size(self, size):
        """Build a flavor ParagraphStyle for the given size."""
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.lib.enums import TA_LEFT
        from reportlab.lib import colors
        return ParagraphStyle(
            f'FlavorText_{size}',
            fontName='Baskerville-Italic',
            fontSize=size,
            leading=size + 2,
            textColor=colors.black,
            alignment=TA_LEFT,
        )

    def _flavor_layout_at_size(self, text, size, max_w, box_h):
        """Layout flavor text at a fixed size. Returns (width, style, used_h).

        Width is the narrowest column at which the text fits in box_h, capped
        at max_w. If the text doesn't fit in box_h even at max_w, the returned
        width is max_w and used_h reports the actual (overflowing) height.
        """
        pad = FLAVOR_TEXT_PADDING * 2
        usable_h = box_h - pad
        style = self._flavor_style_at_size(size)

        # Try at MIN_FLAVOR_TEXT_W first — most compact.
        p = Paragraph(text, style)
        _, h_min = p.wrap(MIN_FLAVOR_TEXT_W - pad, 9999)
        if h_min <= usable_h:
            return MIN_FLAVOR_TEXT_W, style, h_min

        # Doesn't fit at minimum width — search for narrowest fitting width.
        p = Paragraph(text, style)
        _, h_max = p.wrap(max_w - pad, 9999)
        if h_max > usable_h:
            return max_w, style, h_max

        lo = MIN_FLAVOR_TEXT_W
        hi = max_w
        h_at_hi = h_max
        while hi - lo > 0.5:
            mid = (lo + hi) / 2
            p = Paragraph(text, style)
            _, h = p.wrap(mid - pad, 9999)
            if h <= usable_h:
                hi = mid
                h_at_hi = h
            else:
                lo = mid
        return hi, style, h_at_hi

    def _ability_title_size_for_body(self, body_size):
        """Map a body size to the matching title size, clamped to title bounds."""
        title_size = ABILITY_TITLE_MAX_SIZE - (ABILITY_BODY_MAX_SIZE - body_size)
        return max(ABILITY_TITLE_MIN_SIZE, min(ABILITY_TITLE_MAX_SIZE, title_size))

    def _flavor_char_weight(self, text, body_size, flavor_size, box_h):
        """Effective character weight for flavor when it competes for width
        alongside abilities in the proportional allocator.

        Two adjustments are applied to the raw character count:

        1. **Italic font density**: Baskerville-Italic at ``flavor_size`` is
           typically a bit narrower than Baskerville (roman) at ``body_size``.
           The ratio of measured "M" widths captures that — flavor needs less
           width per character.
        2. **No-title vertical savings**: an ability spends part of ``box_h``
           on its title; flavor spends none. The fraction of vertical space
           still available for body in an ability cell scales the weight down
           — with more vertical room per char, flavor needs less width.

        Both factors are then softened toward 1.0 by FLAVOR_WEIGHT_BIAS so
        flavor still gets a reasonable share of the bar even with long
        ability bodies competing for width.
        """
        char_count = len(text)
        if char_count == 0:
            return 0

        # Bias toward 1.0: 0 = full reduction (font_ratio * body_fraction),
        # 1 = no reduction (raw char count). 0.5 splits the difference.
        FLAVOR_WEIGHT_BIAS = 0.5

        # Italic vs roman width ratio at the relevant sizes. Use a short
        # sample string and divide so we get an average char width that
        # accounts for both font shape and font size differences.
        sample = 'M' * 20
        try:
            italic_w = pdfmetrics.stringWidth(sample, 'Baskerville-Italic', flavor_size) / len(sample)
            roman_w = pdfmetrics.stringWidth(sample, 'Baskerville', body_size) / len(sample)
            font_ratio = italic_w / roman_w if roman_w > 0 else 1.0
        except Exception:
            font_ratio = 1.0

        # Title-area savings: an ability's title occupies ~title_size leading
        # + 2pt spaceAfter (matches _make_ability_styles). Flavor has no
        # title so it gets all of box_h for body. The body-only fraction tells
        # us how much *less* width flavor needs for the same char count.
        title_size = self._ability_title_size_for_body(body_size)
        title_h = title_size + 2  # leading == title_size, spaceAfter == 2
        title_fraction = min(0.5, max(0.0, title_h / box_h)) if box_h > 0 else 0.0
        body_fraction = 1.0 - title_fraction

        font_ratio = font_ratio + (1.0 - font_ratio) * FLAVOR_WEIGHT_BIAS
        body_fraction = body_fraction + (1.0 - body_fraction) * FLAVOR_WEIGHT_BIAS

        return char_count * font_ratio * body_fraction

    def _make_ability_styles(self, body_size, title_size=None):
        """Build (title_style, body_style). title_size defaults to the size
        derived from body_size; pass an explicit value to keep all titles
        in a row uniform when body sizes differ."""
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.lib.enums import TA_LEFT
        from reportlab.lib import colors

        if title_size is None:
            title_size = self._ability_title_size_for_body(body_size)
        title_style = ParagraphStyle(
            f'AbilityTitle_{title_size}',
            fontName='Baskerville',
            fontSize=title_size,
            leading=title_size,
            spaceAfter=2,
        )
        body_style = ParagraphStyle(
            f'AbilityBody_{body_size}',
            fontName='Baskerville',
            fontSize=body_size,
            leading=body_size + 2,
            textColor=colors.black,
            alignment=TA_LEFT,
        )
        return title_style, body_style

    def _measure_ability(self, ability, width, title_style, body_style):
        """Return total rendered height of title + body at the given width."""
        title_p = Paragraph(f"<b>{ability.title}</b>", title_style)
        body_p = Paragraph(format_inline_images(ability.body, img_height=ABILITY_INLINE_IMG_H, sheet=self.sheet), body_style)
        _, th = title_p.wrap(width, 9999)
        _, bh = body_p.wrap(width, 9999)
        return th + bh

    def _all_fit(self, abilities, widths, icon_w, box_h, sizes):
        """Check whether every ability fits at its assigned size."""
        for i, a in enumerate(abilities):
            ts, bs = self._make_ability_styles(sizes[i])
            h = self._measure_ability(a, widths[i] - icon_w, ts, bs)
            if h > box_h:
                return False
        return True

    def _resolve_ability_layout(self, abilities, bar_w, crafted_w, icon_w,
                                has_crafted, flavor_text):
        """Joint flavor + ability layout solver.

        Returns dict with keys: widths, sizes, box_h, flavor_w, flavor_style,
        flavor_size. Walks strategies 0-4 in order. Body and flavor sizes
        shrink together with intermediate flavor-only half-steps.
        """
        n = len(abilities)
        # Add ABILITY_BAR_BOTTOM_EXTRA to give abilities more usable height.
        # This extends the bar's bottom edge down into the otherwise-unused
        # gap above the phases area; phases_top_y is unaffected because
        # delta_h (the downstream shift) is computed against the same default.
        default_box_h = ABILITY_BAR_H - FACTION_TOP_BAR_NUDGE + ABILITY_BAR_BOTTOM_EXTRA
        max_box_h = ABILITY_BAR_MAX_H - FACTION_TOP_BAR_NUDGE + ABILITY_BAR_BOTTOM_EXTRA
        has_flavor = bool(flavor_text and flavor_text.strip())
        flavor_text_clean = flavor_text.strip() if has_flavor else ''

        # Identify "outlier" abilities — bodies that are significantly longer
        # than the median. The interleaved Strategy 1 outlier probe (and the
        # fallback Strategy 2b) shrink only these so the rest of the row can
        # stay at a larger, more readable size.
        if n:
            ability_char_counts = [len(a.body) for a in abilities]
            _sorted_counts = sorted(ability_char_counts)
            _median = (
                _sorted_counts[n // 2] if n % 2 == 1 else
                (_sorted_counts[n // 2 - 1] + _sorted_counts[n // 2]) / 2
            )
            outlier_idx = {
                i for i, c in enumerate(ability_char_counts)
                if _median > 0 and c > ABILITY_OUTLIER_RATIO * _median
            }
        else:
            outlier_idx = set()

        def reserve_flavor(flavor_size, box_h):
            """Return (flavor_w, flavor_style, available_w_for_abilities,
            flavor_used_h) for the given flavor size and bar height."""
            if not has_flavor:
                avail = bar_w
                if crafted_w:
                    avail -= (crafted_w + ABILITY_GAP)
                return 0, None, avail, 0
            max_flavor_w = max((bar_w - crafted_w) * 0.4, MIN_FLAVOR_TEXT_W)
            fw, fs, fh = self._flavor_layout_at_size(
                flavor_text_clean, flavor_size, max_flavor_w, box_h
            )
            avail = bar_w - (fw + ABILITY_GAP)
            if crafted_w:
                avail -= (crafted_w + ABILITY_GAP)
            return fw, fs, avail, fh

        # Strategy 1 walk is body-only. Flavor's font size is derived from the
        # row's max body size (clamped to flavor's own MIN/MAX), so the two
        # shrink in lockstep — flavor never grows larger than the largest
        # ability in the row. A separate bump-up after pack() can grow flavor
        # back up if the assigned flavor_w / box_h have headroom.
        def _coupled_flavor_size(row_max_body):
            return max(
                FLAVOR_TEXT_BASE_SIZE,
                min(FLAVOR_TEXT_MAX_SIZE, row_max_body),
            )

        body_sizes = list(range(ABILITY_BODY_MAX_SIZE, ABILITY_BODY_MIN_SIZE - 1, -1))
        walk = [(bs, _coupled_flavor_size(bs)) for bs in body_sizes]

        def _try_alloc_with_sizes(sizes_per_ability, flavor_size, box_h):
            """Shared Strategy 1 inner step. Lays out abilities at the given
            per-ability body sizes against ``box_h``, with flavor competing
            in the same proportional allocator. Pass uniform sizes for the
            uniform-fit path or mixed sizes for an outlier variant.

            char-weight per ability is scaled by its body size relative to
            MAX so shrunk abilities claim proportionally less horizontal
            space (matches the existing size_weights logic in
            _calculate_ability_widths).

            Returns (ability_widths, sizes, flavor_w, flavor_style,
            flavor_used_h, fits).
            """
            base_available = bar_w
            if crafted_w:
                base_available -= (crafted_w + ABILITY_GAP)

            flavor_style = self._flavor_style_at_size(flavor_size) if has_flavor else None
            row_max_for_flavor = (
                max(sizes_per_ability) if sizes_per_ability else ABILITY_BODY_MAX_SIZE
            )
            # size_weights matter only for non-uniform rows (outlier variants)
            # — in the uniform case every ability has the same size so the
            # weight is the same multiplier and cancels out, but keeping the
            # weight at 1.0 also matches the equal-split fast-path historical
            # behavior in _calculate_ability_widths.
            uniform_row = (
                not sizes_per_ability
                or all(s == sizes_per_ability[0] for s in sizes_per_ability)
            )

            items = []
            for i, a in enumerate(abilities):
                title_w = pdfmetrics.stringWidth(a.title, 'Baskerville', 8)
                if uniform_row or not sizes_per_ability:
                    weight = 1.0
                else:
                    weight = sizes_per_ability[i] / ABILITY_BODY_MAX_SIZE
                items.append({
                    'char_weight': len(a.body) * weight,
                    'min_w': max(MIN_ABILITY_BOX_W, title_w + icon_w + 4),
                })
            if has_flavor:
                items.append({
                    'char_weight': self._flavor_char_weight(
                        flavor_text_clean, row_max_for_flavor, flavor_size, box_h,
                    ),
                    'min_w': MIN_FLAVOR_TEXT_W,
                })

            widths_all = self._calculate_item_widths(items, base_available)
            ability_widths = widths_all[:n]
            flavor_w = widths_all[n] if has_flavor else 0

            ability_fit = (n == 0) or self._all_fit(
                abilities, ability_widths, icon_w, box_h, sizes_per_ability
            )
            flavor_used_h = 0
            flavor_fit = True
            if has_flavor:
                pad = FLAVOR_TEXT_PADDING * 2
                p = Paragraph(flavor_text_clean, flavor_style)
                _, flavor_used_h = p.wrap(max(flavor_w - pad, 1), 9999)
                flavor_fit = flavor_used_h <= (box_h - pad)

            return (
                ability_widths, list(sizes_per_ability), flavor_w, flavor_style,
                flavor_used_h, ability_fit and flavor_fit,
            )

        def try_step_alloc(body_size, flavor_size, box_h):
            """Uniform-body Strategy 1 probe."""
            return _try_alloc_with_sizes([body_size] * n, flavor_size, box_h)

        def try_outlier_alloc(non_size, out_size, flavor_size, box_h):
            """Outlier-shrink Strategy 1 probe: non-outliers at ``non_size``,
            outliers at ``out_size`` (must be < non_size)."""
            sizes = [out_size if i in outlier_idx else non_size for i in range(n)]
            return _try_alloc_with_sizes(sizes, flavor_size, box_h)

        def bump_up_sizes(widths, sizes, box_h):
            """For each ability, find the largest integer size up to MAX
            that still fits at its assigned width and box_h. Returns a new
            list."""
            bumped = list(sizes)
            for i, a in enumerate(abilities):
                cur = bumped[i]
                avail_w = widths[i] - icon_w
                for trial in range(ABILITY_BODY_MAX_SIZE, cur, -1):
                    ts, bs = self._make_ability_styles(trial)
                    if self._measure_ability(a, avail_w, ts, bs) <= box_h:
                        bumped[i] = trial
                        break
            return bumped

        faction_label = getattr(getattr(self, 'sheet', None), 'faction', None)
        faction_label = getattr(faction_label, 'faction_name', '?')

        def _log(strategy, body_size=None, flavor_size=None, box_h=None,
                 extra=''):
            parts = [f'[ability-bar] {faction_label!r} → {strategy}']
            if body_size is not None:
                parts.append(f'body={body_size}')
            if flavor_size is not None:
                parts.append(f'flavor={flavor_size}')
            if box_h is not None:
                parts.append(f'box_h={box_h:.1f}')
            if extra:
                parts.append(extra)
            print(' '.join(parts))

        def pack(widths, sizes, box_h, flavor_w, flavor_style, flavor_size):
            # Per-ability bump-up: for each ability, find the largest body
            # size up to ABILITY_BODY_MAX_SIZE that still fits at its assigned
            # width. Runs whenever there are abilities — short abilities may
            # have headroom even when the row-min sat above MIN.
            if sizes:
                before = list(sizes)
                bumped = bump_up_sizes(widths, sizes, box_h)
                sizes = bumped
                # After bump-up, re-validate with the unified title size
                # (titles all match the largest body in the row). A larger
                # unified title may push a previously-fitting ability over —
                # walk back individually until each fits with that title.
                walked_back = False
                while sizes:
                    row_max = max(sizes)
                    unified_title = self._ability_title_size_for_body(row_max)
                    overflow = False
                    for i, a in enumerate(abilities):
                        ts, bs = self._make_ability_styles(sizes[i], title_size=unified_title)
                        if self._measure_ability(a, widths[i] - icon_w, ts, bs) > box_h:
                            if sizes[i] > ABILITY_BODY_HARD_FLOOR:
                                sizes[i] -= 1
                                overflow = True
                                walked_back = True
                                break
                    if not overflow:
                        break
                if sizes == before:
                    _log('bump-up: no change (already at best fit)',
                         extra=f'sizes={sizes}')
                elif walked_back:
                    if any(s > b for s, b in zip(sizes, before)):
                        _log('bump-up: partial (some raised, some walked back)',
                             extra=f'before={before} after={sizes}')
                    else:
                        _log('bump-up: failed (raises rolled back below baseline)',
                             extra=f'before={before} after={sizes}')
                else:
                    _log('bump-up: success',
                         extra=f'before={before} after={sizes}')

            # Flavor bump-up: after abilities settle, see if flavor can grow
            # within its already-assigned column (flavor_w × box_h). Coupling
            # flavor to body during the walk means flavor often sat below
            # FLAVOR_TEXT_MAX_SIZE; if the column has headroom, raise it.
            # Same half-step probe as the ability bump-up.
            if has_flavor and flavor_w > 0 and flavor_size < FLAVOR_TEXT_MAX_SIZE:
                pad = FLAVOR_TEXT_PADDING * 2
                usable_w = max(flavor_w - pad, 1)
                usable_h = box_h - pad
                before_flavor = flavor_size

                for trial in range(FLAVOR_TEXT_MAX_SIZE, flavor_size, -1):
                    trial_style = self._flavor_style_at_size(trial)
                    p = Paragraph(flavor_text_clean, trial_style)
                    _, h = p.wrap(usable_w, 9999)
                    if h <= usable_h:
                        flavor_style = trial_style
                        flavor_size = trial
                        break
                if flavor_size != before_flavor:
                    _log('flavor bump-up: success',
                         extra=f'before={before_flavor} after={flavor_size}')
                else:
                    _log('flavor bump-up: no change',
                         extra=f'flavor_size={flavor_size}')

            title_size = (
                self._ability_title_size_for_body(max(sizes)) if sizes else None
            )
            return {
                'widths': widths,
                'sizes': sizes,
                'box_h': box_h,
                'flavor_w': flavor_w,
                'flavor_style': flavor_style,
                'flavor_size': flavor_size,
                'title_size': title_size,
            }

        if n == 0 and not has_flavor:
            _log('empty (no abilities, no flavor)', box_h=default_box_h)
            return pack([], [], default_box_h, 0, None, FLAVOR_TEXT_MAX_SIZE)

        # Strategy 1 — joint NN walk at default box_h. At each (body, flavor)
        # step we first try uniform body sizes; if uniform fails and outliers
        # exist, we probe outlier-shrink variants at the SAME non-outlier
        # size with the outlier shrunk by 1 or 2 (MAX_OUTLIER_GAP = 2). This
        # interleaves the outlier search with the NN walk so we keep the
        # majority of abilities at the largest readable size whenever the
        # row has a single long outlier dragging the uniform size down.
        MAX_OUTLIER_GAP = 2

        def _try_step1_variants(body_size, flavor_size, box_h):
            """Return (variant, fits) where variant is the result tuple from
            _try_alloc_with_sizes and fits is True if any variant at this
            step fits. Prefers uniform; falls back to gap-1 then gap-2
            outlier shrinks only when uniform fails and outliers exist."""
            uniform = try_step_alloc(body_size, flavor_size, box_h)
            if uniform[5]:  # uniform fits
                return uniform, 'uniform'
            if not outlier_idx:
                return uniform, None
            for gap in range(1, MAX_OUTLIER_GAP + 1):
                out_size = body_size - gap
                if out_size < ABILITY_BODY_MIN_SIZE:
                    break
                variant = try_outlier_alloc(body_size, out_size, flavor_size, box_h)
                if variant[5]:
                    return variant, f'outlier (non={body_size}, out={out_size}, gap={gap})'
            return uniform, None

        for step_idx, (body_size, flavor_size) in enumerate(walk):
            variant, label = _try_step1_variants(body_size, flavor_size, default_box_h)
            widths, sizes, fw, fs, fh, fits = variant
            if fits:
                # Strategy 0 — only at the very first step, with no crafted
                # items AND a uniform fit: shrink box_h down to what's
                # actually used so phases can move up.
                if step_idx == 0 and not has_crafted and label == 'uniform':
                    used_abil = max(
                        (self._measure_ability(
                            a, widths[i] - icon_w, *self._make_ability_styles(body_size)
                        ) for i, a in enumerate(abilities)),
                        default=0,
                    )
                    used = max(used_abil, fh)
                    needed = used + 2 * ABILITY_BAR_MIN_PAD
                    if needed < default_box_h:
                        _log('Strategy 0 (reclaim box_h)',
                             body_size=body_size, flavor_size=flavor_size, box_h=needed)
                        return pack(widths, sizes, needed, fw, fs, flavor_size)
                _log(f'Strategy 1 NN walk fit (step {step_idx}, {label})',
                     body_size=sizes, flavor_size=flavor_size, box_h=default_box_h)
                return pack(widths, sizes, default_box_h, fw, fs, flavor_size)

        # Strategy 2b — last-resort outlier shrink at default box_h. The
        # interleaved Strategy 1 outlier probe (with gap cap) covers the
        # quality cases; this fallback path uses the legacy reserve_flavor
        # allocator and is only reached when Strategy 1 and Strategy 3 both
        # fail outright.

        def try_outlier(min_outlier_size):
            """Search outlier-shrink combinations with outlier floor = min_outlier_size.
            Returns a packed result if a fit is found, else None."""
            for non_size in range(ABILITY_BODY_MAX_SIZE, ABILITY_BODY_MIN_SIZE - 1, -1):
                for out_size in range(non_size - 1, min_outlier_size - 1, -1):
                    flavor_size = max(
                        min(non_size - 1, FLAVOR_TEXT_MAX_SIZE),
                        FLAVOR_TEXT_BASE_SIZE,
                    )
                    fw_, fs_, avail_, fh_ = reserve_flavor(flavor_size, default_box_h)
                    sizes_ = [out_size if i in outlier_idx else non_size for i in range(n)]
                    weights = [s / ABILITY_BODY_MAX_SIZE for s in sizes_]
                    new_widths = self._calculate_ability_widths(
                        abilities, avail_, icon_w, size_weights=weights
                    )
                    flavor_fit = (not has_flavor) or fh_ <= (default_box_h - 2 * FLAVOR_TEXT_PADDING)
                    if flavor_fit and self._all_fit(
                        abilities, new_widths, icon_w, default_box_h, sizes_
                    ):
                        return pack(new_widths, sizes_, default_box_h, fw_, fs_, flavor_size)
            return None

        # (Strategy 2a is now interleaved into Strategy 1 above; the
        # standalone outlier ≥ MIN pass is no longer needed.)

        # Strategy 3 — extend box_h. Flavor at its floor since we're spending
        # vertical real estate already.
        flavor_size = FLAVOR_TEXT_BASE_SIZE
        sizes = [ABILITY_BODY_MIN_SIZE] * n
        # Use max_box_h for the flavor reservation so the layout function
        # doesn't reject sizes that need taller boxes.
        fw, fs, avail, fh = reserve_flavor(flavor_size, max_box_h)
        widths = self._calculate_ability_widths(abilities, avail, icon_w)
        ts, bs = self._make_ability_styles(ABILITY_BODY_MIN_SIZE)
        used_abil = max(
            (self._measure_ability(a, widths[i] - icon_w, ts, bs)
             for i, a in enumerate(abilities)),
            default=0,
        )
        needed = max(used_abil, fh) + 2 * ABILITY_BAR_MIN_PAD
        new_box_h = min(needed, max_box_h)
        if self._all_fit(abilities, widths, icon_w, new_box_h, sizes):
            _log('Strategy 3 (extend box_h)',
                 body_size=ABILITY_BODY_MIN_SIZE,
                 flavor_size=flavor_size, box_h=new_box_h)
            return pack(widths, sizes, new_box_h, fw, fs, flavor_size)

        # Strategy 2b — last resort: outlier below MIN, down to HARD_FLOOR.
        # Even with the bar extended, MIN didn't fit; try aggressive outlier
        # shrink at default box_h before accepting overflow at the cap.
        if outlier_idx:
            result = try_outlier(ABILITY_BODY_HARD_FLOOR)
            if result is not None:
                _log('Strategy 2b (outlier shrink, ≥ HARD_FLOOR)',
                     body_size=result.get('sizes'),
                     flavor_size=getattr(result.get('flavor_style'), 'fontSize', None),
                     box_h=result.get('box_h'))
                return result

        # Strategy 4 — accept overflow at the cap.
        _log('Strategy 4 (overflow at cap)',
             body_size=ABILITY_BODY_MIN_SIZE,
             flavor_size=flavor_size, box_h=max_box_h)
        return pack(widths, sizes, max_box_h, fw, fs, flavor_size)

    def _draw_ability_boxes(self, c):
        # Layout was resolved by _prepare_ability_bar_layout (called from
        # _draw_top_band). Pull the precomputed values here.
        layout = self._ability_layout
        abilities = layout['abilities']
        widths = layout['widths']
        sizes = layout['sizes']
        title_size = layout.get('title_size')
        box_h = layout['box_h']
        bar_w = layout['bar_w']
        icon_w = layout['icon_w']
        crafted_w = layout['crafted_w']
        crafted_h = layout['crafted_h']
        flavor_w = layout['flavor_w']
        flavor_style = layout['flavor_style']
        flavor_text = layout['flavor_text']
        delta_h = layout['delta_h']

        bar_x = (PAGE_W - bar_w) / 2
        box_y = self.title_bar_y - FACTION_TOP_BAR_NUDGE - box_h

        # Draw flavor text box left-aligned with the color bar
        if flavor_w:
            flavor_x = bar_x
            flavor_y = box_y
            story = [Paragraph(flavor_text, flavor_style)]
            frame = Frame(
                flavor_x, flavor_y,
                flavor_w, box_h,
                leftPadding=FLAVOR_TEXT_PADDING,
                rightPadding=FLAVOR_TEXT_PADDING,
                topPadding=FLAVOR_TEXT_PADDING,
                bottomPadding=FLAVOR_TEXT_PADDING,
                showBoundary=0,
            )
            frame.addFromList(story, c)
            self._record_element(kind='header_flavor',
                                 x=flavor_x, y=flavor_y,
                                 w=flavor_w, h=box_h,
                                 text=flavor_text)

        if abilities:
            x = bar_x + (flavor_w + ABILITY_GAP if flavor_w else 0)
            for i, ability in enumerate(abilities):
                box_w = widths[i]
                title_style, body_style = self._make_ability_styles(sizes[i], title_size=title_size)

                self._record_element(kind='header_ability',
                                     x=x, y=box_y, w=box_w, h=box_h,
                                     title=ability.title or '',
                                     body=ability.body or '')

                # Ability icon (SVG colored with faction color)
                if self._ability_icon:
                    icon_x = x + 2
                    icon_y = box_y + box_h - self._ability_icon.height - 2
                    c.set_layer_tag('fg')
                    renderPDF.draw(self._ability_icon, c, icon_x, icon_y)

                # Flow title + body text to the right of the icon
                story = [
                    Paragraph(f"<b>{ability.title}</b>", title_style),
                    Paragraph(format_inline_images(ability.body, img_height=ABILITY_INLINE_IMG_H, sheet=self.sheet), body_style),
                ]
                frame = Frame(
                    x + icon_w, box_y,
                    box_w - icon_w, box_h,
                    leftPadding=0, rightPadding=0,
                    topPadding=0, bottomPadding=0,
                    showBoundary=0
                )
                frame.addFromList(story, c)

                x += box_w + ABILITY_GAP

        # Draw crafted items SVG right-aligned with the color bar, vertically
        # centered in the (possibly extended) ability bar.
        if crafted_w:
            crafted_x = bar_x + bar_w - crafted_w
            # When the bar grows, shift crafted items down by delta_h/2 so they
            # stay vertically centered in the new band.
            crafted_y = self.title_bar_y - FACTION_TOP_BAR_NUDGE - crafted_h - (delta_h / 2)

            # Dynamic overlay drawn first so the SVG paints over it (handy for
            # checking which features the SVG is masking versus revealing).
            if CRAFTED_OVERLAY_ENABLED:
                txt = _pdf_text('crafted_items', _lang_code_for(self.sheet))
                overlay_w = CRAFTED_OVERLAY_W
                overlay_h = CRAFTED_OVERLAY_H
                overlay_x = crafted_x + (crafted_w - overlay_w) / 2 + CRAFTED_OVERLAY_X_NUDGE
                overlay_y = crafted_y + (crafted_h - overlay_h) / 2 + CRAFTED_OVERLAY_Y_NUDGE
                draw_crafted_items_overlay(
                    c, overlay_x, overlay_y, overlay_w, overlay_h,
                    txt['title'], txt['body'],
                    color_hex=CRAFTED_OVERLAY_COLOR_HEX,
                )

            # Legacy SVG draw — kept for reference while iterating on the
            # dynamic overlay above. Flip to True to render the SVG again.
            if False:
                drawing = svg2rlg(CRAFTED_ITEMS_SVG)
                if drawing:
                    sx = crafted_w / drawing.width
                    sy = crafted_h / drawing.height
                    drawing.width = crafted_w
                    drawing.height = crafted_h
                    drawing.scale(sx, sy)
                    c.set_layer_tag('fg')
                    renderPDF.draw(drawing, c, crafted_x, crafted_y)
            self._record_element(kind='header_crafted',
                                 x=crafted_x, y=crafted_y,
                                 w=crafted_w, h=crafted_h)

    def _get_decree_bg_path(self):
        filename = 'title.png' if (self.decree_section.title or self.decree_section.body) else 'no-title.png'
        return os.path.join(DECREE_DIR, filename)

    def _get_slot_image_path(self, slot, force_large_for_titled=False):
        title = slot.title or ''
        if not title:
            return os.path.join(DECREE_DIR, 'no-text.png')
        if force_large_for_titled:
            return os.path.join(DECREE_DIR, 'large-text.png')
        if len(title) < DECREE_TEXT_THRESHOLD:
            return os.path.join(DECREE_DIR, 'small-text.png')
        return os.path.join(DECREE_DIR, 'large-text.png')

    def _wrap_slot_body(self, text):
        """Return slot body as 1 or 2 centered, balanced lines.

        Splits on whitespace and picks the split that minimizes the width
        difference between the two lines, rejecting any split where either
        line still exceeds DECREE_SLOT_BODY_MAX_W.
        """
        font = 'Baskerville-Italic'
        size = DECREE_SLOT_BODY_SIZE
        if pdfmetrics.stringWidth(text, font, size) <= DECREE_SLOT_BODY_MAX_W:
            return [text]
        words = text.split()
        if len(words) < 2:
            return [text]
        best = None
        best_diff = None
        for k in range(1, len(words)):
            line1 = ' '.join(words[:k])
            line2 = ' '.join(words[k:])
            w1 = pdfmetrics.stringWidth(line1, font, size)
            w2 = pdfmetrics.stringWidth(line2, font, size)
            if w1 > DECREE_SLOT_BODY_MAX_W or w2 > DECREE_SLOT_BODY_MAX_W:
                continue
            diff = abs(w1 - w2)
            if best_diff is None or diff < best_diff:
                best = (line1, line2)
                best_diff = diff
        if best is None:
            return [text]
        return [best[0], best[1]]

    def _draw_card_slots(self, c):
        # Decree: background overlay + individual slot images
        if self.decree_section:
            slots = list(self.decree_section.card_slots.all())
            slot_lines = [self._wrap_slot_body(slot.body) if slot.body else [] for slot in slots]
            any_wrapped = any(len(lines) > 1 for lines in slot_lines)
            extra_h = DECREE_SLOT_WRAP_SHIFT if any_wrapped else 0

            # Draw title/no-title background
            # Image starts hidden above page, slides down by decree_slide
            bg_img = self._get_decree_bg_path()
            if os.path.exists(bg_img):
                from reportlab.lib.utils import ImageReader
                ir = ImageReader(bg_img)
                iw, ih = ir.getSize()
                scale = PAGE_W / iw
                draw_h = ih * scale
                ov_decree_y = self._override(self.sheet, 'decree_y_h', 'decree_y_v')
                if ov_decree_y is not None:
                    draw_y = ov_decree_y * inch
                else:
                    draw_y = PAGE_H - self.decree_slide
                decree_img_top = draw_y + draw_h
                self._record_element(kind='decree', x=0, y=draw_y, w=PAGE_W, h=draw_h,
                                     title=self.decree_section.title or '',
                                     y_min=(PAGE_H - DECREE_MAX_OFFSET) / inch,
                                     y_max=(PAGE_H - self.decree_min_offset) / inch)
                c.set_layer_tag('box')
                c.drawImage(bg_img, 0, draw_y,
                            width=PAGE_W, height=draw_h, mask='auto')
            else:
                draw_h = 0
                decree_img_top = PAGE_H

            # Decree section title + body text
            has_body = bool(self.decree_section.body)
            # When both title and body are present, lift the pair slightly so
            # they sit a bit higher in the decree band.
            pair_lift = DECREE_TITLE_BODY_PAIR_LIFT if (self.decree_section.title and has_body) else 0
            base_title_y = decree_img_top - DECREE_TITLE_Y_OFFSET + pair_lift
            c.set_layer_tag('fg')
            if self.decree_section.title:
                c.setFillColor(HexColor('#FFFFFF'))
                c.setFont('Luminari', DECREE_TITLE_SIZE)
                c.drawCentredString(PAGE_W / 2, base_title_y, self.decree_section.title)

            if has_body:
                body_y = base_title_y - DECREE_BODY_GAP if self.decree_section.title else base_title_y
                c.setFillColor(HexColor('#FFFFFF'))
                c.setFont('Baskerville-Italic', DECREE_BODY_SIZE)
                c.drawCentredString(PAGE_W / 2, body_y, self.decree_section.body)

            # Individual slot images — evenly spaced horizontally, centered
            n = len(slots)
            if n > 0:
                gap = max((PAGE_W - n * DECREE_SLOT_W) / (n + 1), DECREE_SLOT_MIN_GAP)
                total_w = n * DECREE_SLOT_W + (n - 1) * gap
                start_x = (PAGE_W - total_w) / 2.0
                slot_y = decree_img_top - DECREE_SLOT_Y_OFFSET + extra_h - self._decree_slot_extra_drop

                any_wide_title = any(
                    slot.title and pdfmetrics.stringWidth(slot.title, 'Baskerville', DECREE_SLOT_TITLE_SIZE) > DECREE_SLOT_WIDE_TITLE_W
                    for slot in slots
                )

                for i, slot in enumerate(slots):
                    x = start_x + i * (DECREE_SLOT_W + gap)
                    slot_img = self._get_slot_image_path(slot, force_large_for_titled=any_wide_title)
                    if os.path.exists(slot_img):
                        c.set_layer_tag('box')
                        c.drawImage(slot_img, x, slot_y,
                                    width=DECREE_SLOT_W, height=DECREE_SLOT_H, mask='auto')

                    title_y = slot_y + DECREE_SLOT_H - DECREE_SLOT_TITLE_OFFSET
                    c.set_layer_tag('fg')
                    if slot.title:
                        c.setFillColor(HexColor('#FFFFFF'))
                        c.setFont('Baskerville', DECREE_SLOT_TITLE_SIZE)
                        c.drawCentredString(x + DECREE_SLOT_W / 2, title_y, slot.title)

                    lines = slot_lines[i]
                    if lines:
                        c.setFillColor(HexColor('#FFFFFF'))
                        c.setFont('Baskerville-Italic', DECREE_SLOT_BODY_SIZE)
                        body_top_y = title_y - DECREE_SLOT_BODY_OFFSET
                        for j, line in enumerate(lines):
                            c.drawCentredString(x + DECREE_SLOT_W / 2,
                                                body_top_y - j * DECREE_SLOT_BODY_LINE_GAP,
                                                line)

                    self._record_element(kind='card_slot', id=slot.id,
                                         x=x, y=slot_y,
                                         w=DECREE_SLOT_W, h=DECREE_SLOT_H,
                                         title=slot.title or '')

    def _draw_credits(self, c):
        """Render PnP version and art-by credits in the bottom-right corner on a
        single line: "Art by <name>   Version: <ver>" — art_by sits to the left
        of pnp_version separated by a small gap. Font color matches the
        title-text-color calculation (self.ink_on_faction). Skips silently when
        both fields are empty.

        Also records each credit string as a layout element ("credit") so the
        preview canvas can show them at the same relative position. Records use
        the right-edge-of-string anchor: x = right edge minus string width."""
        faction = self.sheet.faction
        pnp = (getattr(faction, 'pnp_version', '') or '').strip()
        art = (getattr(faction, 'art_by', '') or '').strip()
        if not pnp and not art:
            return
        c.set_layer_tag('fg')
        c.setFillColor(self.ink_on_faction)
        c.setFont('Baskerville', CREDITS_FONT_SIZE)
        x_right = PAGE_W - CREDITS_PAD_RIGHT
        y = CREDITS_PAD_BOTTOM
        pnp_text = f'Version: {pnp}' if pnp else ''
        art_text = f'Art by {art}' if art else ''
        pnp_w = pdfmetrics.stringWidth(pnp_text, 'Baskerville', CREDITS_FONT_SIZE) if pnp_text else 0
        art_w = pdfmetrics.stringWidth(art_text, 'Baskerville', CREDITS_FONT_SIZE) if art_text else 0
        # Approximate text height for layout-record bounding box.
        text_h = CREDITS_FONT_SIZE * 1.2
        if pnp_text:
            c.drawRightString(x_right, y, pnp_text)
            self._record_element(
                kind='credit', label=pnp_text,
                x=x_right - pnp_w, y=y, w=pnp_w, h=text_h,
                text_color=self.ink_on_faction_hex,
            )
        if art_text:
            art_right = x_right - pnp_w - CREDITS_INTER_GAP if pnp_text else x_right
            c.drawRightString(art_right, y, art_text)
            self._record_element(
                kind='credit', label=art_text,
                x=art_right - art_w, y=y, w=art_w, h=text_h,
                text_color=self.ink_on_faction_hex,
            )

    def _apply_drawing_opacity(self, drawing, alpha):
        """Walk an svglib-parsed Drawing and stamp fillOpacity/strokeOpacity on
        every shape. ReportLab's canvas-level setFillAlpha doesn't propagate
        into renderPDF.draw because the Drawing emits its own paint-state
        directives — baking opacity into shape attributes is what actually
        affects the rendered output."""
        from reportlab.graphics.shapes import Group
        def walk(node):
            if hasattr(node, 'fillOpacity'):
                node.fillOpacity = alpha
            if hasattr(node, 'strokeOpacity'):
                node.strokeOpacity = alpha
            contents = getattr(node, 'contents', None)
            if contents:
                for child in contents:
                    walk(child)
        walk(drawing)

    def _draw_forge_logo(self, c):
        """Stamp the Forge logo in the bottom-right corner. First pass uses the
        faction color; second pass overlays the same SVG in the opposite of the
        title-text color. Both passes are baked at low opacity so the logo
        reads as a soft watermark. Called last in build() so the logo sits on
        top of everything."""
        if not os.path.exists(FORGE_LOGO_SVG):
            return
        faction_hex = self.sheet.faction.color or '#5B4A8A'
        # Opposite of the title-text color: white when ink is black, black
        # when ink is white. Anything else falls back to white.
        opposite_hex = '#FFFFFF' if self.ink_on_faction_hex.upper() == '#000000' else '#000000'

        base_drawing = self._load_colored_svg(FORGE_LOGO_SVG, faction_hex)
        if base_drawing is None:
            return
        scale = FORGE_LOGO_W / base_drawing.width
        target_h = base_drawing.height * scale
        base_drawing.width = FORGE_LOGO_W
        base_drawing.height = target_h
        base_drawing.scale(scale, scale)
        self._apply_drawing_opacity(base_drawing, FORGE_LOGO_BASE_OPACITY)

        x = PAGE_W - FORGE_LOGO_PAD_X - FORGE_LOGO_W
        y = FORGE_LOGO_PAD_BOTTOM
        c.set_layer_tag('fg')
        renderPDF.draw(base_drawing, c, x, y)

        overlay = self._load_colored_svg(FORGE_LOGO_SVG, opposite_hex)
        if overlay is None:
            return
        overlay.width = FORGE_LOGO_W
        overlay.height = target_h
        overlay.scale(scale, scale)
        self._apply_drawing_opacity(overlay, FORGE_LOGO_OVERLAY_OPACITY)
        renderPDF.draw(overlay, c, x, y)

    def _draw_character_images(self, c, in_front=False):
        """Render decorative CharacterImages.

        Called twice per build: once with in_front=False (after the background,
        before other elements) and once with in_front=True (after everything
        else, so flagged images sit on top of the rest of the sheet).

        Defaults: walk leftward from the bottom-right corner with a small gap
        between each image. Default size caps the larger of width/height at 4".
        Per-instance overrides (x_h/y_h/width_h or x_v/y_v/width_v) replace the
        default placement; height is always derived from the image aspect ratio.
        """
        images = [img for img in self.sheet.character_images.all().order_by('order')
                  if bool(img.in_front) == in_front]
        if not images:
            return
        c.set_layer_tag('fg')
        from reportlab.lib.utils import ImageReader
        gap = 0.15 * inch
        cursor_x = PAGE_W - gap
        default_y = gap
        for img in images:
            try:
                path = img.image.path
            except (ValueError, NotImplementedError):
                continue
            if not path or not os.path.exists(path):
                continue
            try:
                reader = ImageReader(path)
                iw, ih = reader.getSize()
            except Exception:
                continue
            if iw <= 0 or ih <= 0:
                continue
            aspect = iw / ih

            ov_w = self._override(img, 'width_h', 'width_v')
            if ov_w is not None and ov_w > 0:
                w_in = ov_w
            else:
                w_in = 4.0 if aspect >= 1 else 4.0 * aspect
            h_in = w_in / aspect
            w = w_in * inch
            h = h_in * inch

            ov_x = self._override(img, 'x_h', 'x_v')
            if ov_x is not None:
                x = ov_x * inch
            else:
                x = cursor_x - w
                cursor_x = x - gap

            ov_y = self._override(img, 'y_h', 'y_v')
            y = ov_y * inch if ov_y is not None else default_y

            try:
                c.drawImage(path, x, y, width=w, height=h, mask='auto')
            except Exception:
                pass

            try:
                image_url = img.image.url
            except (ValueError, NotImplementedError):
                image_url = ''
            self._record_element(kind='character_image', id=img.id,
                                 order=img.order, x=x, y=y, w=w, h=h,
                                 image_url=image_url)

    def _pile_orientation(self, pile):
        """Active-layout orientation for a pile: 'bottom' | 'left' | 'right'."""
        attr = 'orientation_h' if self.sheet.layout_mode == 'horizontal' else 'orientation_v'
        return getattr(pile, attr, 'bottom') or 'bottom'

    def _record_card_pile(self, pile, orientation, x, y):
        """Record a card_pile element in the layout payload. Recorded w/h are
        always the unrotated CARD_SLOT_W/H — the preview rotates visually.
        x_max is the rightmost anchor.x for a 'right'-oriented pile, where
        the rotated footprint is CARD_SLOT_H wide (not W)."""
        slack_in = self._card_pile_edge_slack(pile) / inch
        page_w_in = PAGE_W / inch
        slot_h_in = CARD_SLOT_H / inch
        self._record_element(
            kind='card_pile', id=pile.id,
            title=pile.title or '', number=pile.number,
            x=x, y=y, w=CARD_SLOT_W, h=CARD_SLOT_H,
            orientation=orientation,
            y_min=-slack_in,
            x_min=-slack_in,
            x_max=(page_w_in - slot_h_in) + slack_in,
        )
        # World footprint is rotated for left/right orientation (H wide × W tall);
        # upright is W wide × H tall.
        if orientation in ('left', 'right'):
            cx = x + CARD_SLOT_H / 2
            cy = y + CARD_SLOT_W / 2
        else:
            cx = x + CARD_SLOT_W / 2
            cy = y + CARD_SLOT_H / 2
        self.record_slot_snap_point(cx, cy, pile=pile)

    def _draw_card_piles(self, c):
        if not self.card_piles:
            return
        rightmost_x = PAGE_W - X_MARGIN - CARD_SLOT_W
        auto_index_bottom = 0
        auto_index_left = 0
        auto_index_right = 0
        for pile in self.card_piles:
            orientation = self._pile_orientation(pile)
            ov_x = self._override(pile, 'x_h', 'x_v')
            ov_y = self._override(pile, 'y_h', 'y_v')

            if orientation == 'left':
                # Default-anchor at left edge, stacking upward if no override.
                if ov_x is None:
                    x = X_MARGIN
                else:
                    x = ov_x * inch
                if ov_y is None:
                    y = BOTTOM_MARGIN + auto_index_left * (CARD_SLOT_W + CARD_PILE_GAP)
                    auto_index_left += 1
                else:
                    y = ov_y * inch
                self._draw_card_pile_rotated_at(c, pile, x, y, direction='cw')
                self._record_card_pile(pile, orientation, x, y)
                continue

            if orientation == 'right':
                # Default-anchor at right edge so the rotated H-wide footprint
                # ends flush with the page right edge minus margin.
                if ov_x is None:
                    x = PAGE_W - X_MARGIN - CARD_SLOT_H
                else:
                    x = ov_x * inch
                if ov_y is None:
                    y = BOTTOM_MARGIN + auto_index_right * (CARD_SLOT_W + CARD_PILE_GAP)
                    auto_index_right += 1
                else:
                    y = ov_y * inch
                self._draw_card_pile_rotated_at(c, pile, x, y, direction='ccw')
                self._record_card_pile(pile, orientation, x, y)
                continue

            # 'bottom' — existing behavior.
            if ov_x is not None and ov_y is not None:
                self._draw_card_pile_at(c, pile, ov_x * inch, ov_y * inch)
                continue
            if ov_x is not None:
                x = ov_x * inch
            else:
                x = rightmost_x - auto_index_bottom * (CARD_SLOT_W + CARD_PILE_GAP)
                auto_index_bottom += 1
            self._place_and_draw_card_pile(c, pile, x)

    def _card_pile_body_paragraph(self, pile, body_text, text_w):
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.lib.enums import TA_CENTER
        html = format_step_markup(body_text, sheet=self.sheet)
        style = ParagraphStyle(
            name='CardPileBody',
            fontName='Baskerville',
            fontSize=9,
            leading=9 * 1.2,
            alignment=TA_CENTER,
            textColor=HexColor(self._resolve_element_color(pile.element_color)),
        )
        para = Paragraph(html, style)
        text_h = true_paragraph_height(para, text_w)
        return para, text_h

    def _card_pile_title_paragraph(self, pile, title_text, text_w):
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.lib.enums import TA_CENTER
        # Escape XML special chars in the raw input (no markup support for titles)
        html = (title_text.replace('&', '&amp;')
                          .replace('<', '&lt;')
                          .replace('>', '&gt;'))
        style = ParagraphStyle(
            name='CardPileTitle',
            fontName='Luminari',
            fontSize=CARD_PILE_TITLE_SIZE,
            leading=CARD_PILE_TITLE_SIZE * 1.1,
            alignment=TA_CENTER,
            textColor=HexColor(self._resolve_element_color(pile.element_color)),
        )
        para = Paragraph(html, style)
        text_h = true_paragraph_height(para, text_w)
        return para, text_h

    def _card_pile_obstructions(self):
        # Content boxes get an extra vertical margin below them so piles don't sit flush.
        rects = [(bx, by - CONTENT_BOX_GAP, bw, bh + CONTENT_BOX_GAP)
                 for (bx, by, bw, bh) in self._placed_boxes]
        if self._phases_rect:
            rects.append(self._phases_rect)
        return rects

    @staticmethod
    def _rects_overlap(a, b):
        ax, ay, aw, ah = a
        bx, by, bw, bh = b
        return not (ax + aw <= bx or bx + bw <= ax or ay + ah <= by or by + bh <= ay)

    def _rect_is_clear(self, rect, obstructions):
        return all(not self._rects_overlap(rect, obs) for obs in obstructions)

    def _card_pile_edge_slack(self, pile):
        """Maximum distance the pile's anchor can sit past a page edge while its
        text zone (title+body block) stays fully on-page. Returns inches >= 0.
        For 'bottom' orientation this is |y_min| (anchor below 0). For 'left'/
        'right' it's the horizontal equivalent.

        Measured from where the title/body text actually renders (using the
        standard CARD_PILE_TITLE_TOP_OFFSET, not the squished overflow offset)
        plus a BOTTOM_MARGIN buffer so text doesn't sit flush with the page
        edge. Piles with neither title nor body fall back to
        CARD_PILE_NO_TEXT_OVERHANG."""
        if not (pile.title or pile.body):
            return CARD_PILE_NO_TEXT_OVERHANG
        text_w = CARD_SLOT_W - 2 * CARD_PILE_PADDING
        title_h = body_h = 0.0
        title_para = body_para = None
        if pile.title:
            title_para, title_h = self._card_pile_title_paragraph(pile, pile.title, text_w)
        if pile.body:
            body_para, body_h = self._card_pile_body_paragraph(pile, pile.body, text_w)
        gap = CARD_PILE_TITLE_TO_BODY_GAP if (title_para and body_para) else 0.0
        zone_h = CARD_PILE_TITLE_TOP_OFFSET + title_h + gap + body_h
        return max(0.0, CARD_SLOT_H - zone_h - BOTTOM_MARGIN)

    def _card_pile_min_y(self, pile):
        """Lowest y at which the pile's tight text zone still fits on-page.
        At this y, the bottom portion of the card image hangs below y=0."""
        return -self._card_pile_edge_slack(pile)

    def _draw_card_pile_at(self, c, pile, x, y):
        """Draw a card pile upright at exactly (x, y), skipping collision search.
        Used when both x/y overrides are present."""
        text_w = CARD_SLOT_W - 2 * CARD_PILE_PADDING
        title_para = title_h = None
        body_para = body_h = None
        if pile.title:
            title_para, title_h = self._card_pile_title_paragraph(pile, pile.title, text_w)
        else:
            title_h = 0.0
        if pile.body:
            body_para, body_h = self._card_pile_body_paragraph(pile, pile.body, text_w)
        else:
            body_h = 0.0
        self._draw_card_pile_upright(c, pile, x, y,
                                     title_para, title_h, body_para, body_h,
                                     CARD_PILE_TITLE_TOP_OFFSET)
        self._record_card_pile(pile, 'bottom', x, y)

    def _draw_card_pile_rotated_at(self, c, pile, x, y, direction):
        """Draw a rotated card pile at exactly (x, y) — for explicit left/right
        orientation. Anchor is bottom-left of the rotated world footprint."""
        text_w = CARD_SLOT_W - 2 * CARD_PILE_PADDING
        title_para = title_h = None
        body_para = body_h = None
        if pile.title:
            title_para, title_h = self._card_pile_title_paragraph(pile, pile.title, text_w)
        else:
            title_h = 0.0
        if pile.body:
            body_para, body_h = self._card_pile_body_paragraph(pile, pile.body, text_w)
        else:
            body_h = 0.0
        self._draw_card_pile_rotated(c, pile, x, y,
                                     title_para, title_h, body_para, body_h,
                                     CARD_PILE_TITLE_TOP_OFFSET, direction=direction)

    def _place_and_draw_card_pile(self, c, pile, x):
        text_w = CARD_SLOT_W - 2 * CARD_PILE_PADDING

        title_para = None
        title_h = 0.0
        if pile.title:
            title_para, title_h = self._card_pile_title_paragraph(pile, pile.title, text_w)

        body_para = None
        body_h = 0.0
        if pile.body:
            body_para, body_h = self._card_pile_body_paragraph(pile, pile.body, text_w)

        # Text zone height as a function of title-top offset. The body-start offset
        # scales 1:1 with the title-top offset (title/body block is a rigid unit).
        def text_zone_h_for(title_top_offset):
            if title_para is None and body_para is None:
                return 0.0
            body_top_offset = title_top_offset + title_h + (
                CARD_PILE_TITLE_TO_BODY_GAP if title_para and body_para else 0.0
            )
            return body_top_offset + body_h

        obstructions = self._card_pile_obstructions()
        default_y = BOTTOM_MARGIN

        def max_offset_for(candidate_y):
            """Largest title_top_offset in [OVERFLOW, FULL] that keeps the text zone
            on-page and clear of obstructions at this y. Returns None if even the
            tightest offset doesn't fit."""
            card_top = candidate_y + CARD_SLOT_H
            # At offset O, text-zone top = card_top - O, text-zone bottom =
            # card_top - O - (text_zone_h_for(O) - O) = card_top - text_zone_h_for(O).
            # Because text_zone_h_for(O) = O + (title_h + gap + body_h), the bottom
            # depends only on (title_h + gap + body_h), i.e. is independent of O.
            # So on-page / obstruction constraints on the BOTTOM don't vary with O.
            # The TOP of the text zone does vary: larger O pushes text down.
            # Constraints: text_zone_bottom >= 0, and rect clears obstructions.
            zone_h_tight = text_zone_h_for(CARD_PILE_TITLE_TOP_OFFSET_OVERFLOW)
            zone_h_full = text_zone_h_for(CARD_PILE_TITLE_TOP_OFFSET)
            # Test the tight placement first (smallest zone → easiest to fit).
            tight_bottom = card_top - zone_h_tight
            if tight_bottom < 0:
                return None
            tight_rect = (x, tight_bottom, CARD_SLOT_W, zone_h_tight)
            if not self._rect_is_clear(tight_rect, obstructions):
                return None
            # Tight fits — now try the full placement.
            full_bottom = card_top - zone_h_full
            if full_bottom >= 0:
                full_rect = (x, full_bottom, CARD_SLOT_W, zone_h_full)
                if self._rect_is_clear(full_rect, obstructions):
                    return CARD_PILE_TITLE_TOP_OFFSET
            # Binary-search the largest offset in (OVERFLOW, FULL) that fits.
            lo, hi = CARD_PILE_TITLE_TOP_OFFSET_OVERFLOW, CARD_PILE_TITLE_TOP_OFFSET
            for _ in range(12):  # 12 iterations → sub-0.001" precision
                mid = (lo + hi) / 2
                zone_h_mid = text_zone_h_for(mid)
                mid_bottom = card_top - zone_h_mid
                if mid_bottom >= 0 and self._rect_is_clear(
                    (x, mid_bottom, CARD_SLOT_W, zone_h_mid), obstructions
                ):
                    lo = mid
                else:
                    hi = mid
            return lo

        # --- Try upright: sweep y from default downward; first y with a valid offset wins. ---
        zone_h_tight = text_zone_h_for(CARD_PILE_TITLE_TOP_OFFSET_OVERFLOW)
        y_min = zone_h_tight - CARD_SLOT_H  # lowest y keeping tight text on-page
        step = 1.0
        candidate_y = default_y
        while candidate_y >= y_min:
            offset = max_offset_for(candidate_y)
            if offset is not None:
                self._draw_card_pile_upright(c, pile, x, candidate_y,
                                             title_para, title_h, body_para, body_h,
                                             offset)
                self._record_card_pile(pile, 'bottom', x, candidate_y)
                return
            candidate_y -= step

        # --- Rotated fallback: 90 CCW, overflow off right edge, tight offset. ---
        rot_bottom = BOTTOM_MARGIN
        rot_text_rect = (x, rot_bottom, zone_h_tight, CARD_SLOT_W)
        text_fits_on_page = (x + zone_h_tight) <= PAGE_W and rot_bottom + CARD_SLOT_W <= PAGE_H
        if text_fits_on_page and self._rect_is_clear(rot_text_rect, obstructions):
            self._draw_card_pile_rotated(c, pile, x, rot_bottom,
                                         title_para, title_h, body_para, body_h,
                                         CARD_PILE_TITLE_TOP_OFFSET_OVERFLOW)
            self._record_card_pile(pile, 'right', x, rot_bottom)
            return

        # --- Final fallback — upright at BOTTOM_MARGIN, text possibly cut off. ---
        print(f"ERROR: CardPile '{pile.title or pile.number}' cannot render with title/body fully visible. Rendering with text cut off.")
        self._draw_card_pile_upright(c, pile, x, default_y,
                                     title_para, title_h, body_para, body_h,
                                     CARD_PILE_TITLE_TOP_OFFSET)
        self._record_card_pile(pile, 'bottom', x, default_y)

    def _draw_card_pile_screen(self, c, pile, x, y):
        """Draw a contrast screen behind a card pile's text. Drawn before the
        SVG border so the rounded border covers any corner overdraw."""
        if not pile.background_screen:
            return
        h = self._resolve_card_pile_screen_color(pile).lstrip('#')
        r, g, b = int(h[0:2], 16) / 255.0, int(h[2:4], 16) / 255.0, int(h[4:6], 16) / 255.0
        inset = 4.0
        radius = 8.0
        c.set_layer_tag('box')
        c.saveState()
        c.setFillColorRGB(r, g, b, alpha=BACK_BG_SCREEN_OPACITY)
        c.setStrokeColorRGB(r, g, b, alpha=0)
        c.roundRect(x + inset, y + inset,
                    CARD_SLOT_W - 2 * inset, CARD_SLOT_H - 2 * inset,
                    radius, stroke=0, fill=1)
        c.restoreState()

    def _draw_card_pile_upright(self, c, pile, x, y,
                                title_para, title_h, body_para, body_h,
                                title_top_offset):
        self._draw_card_pile_screen(c, pile, x, y)
        drawing = self._load_card_pile_svg(self._resolve_element_color(pile.element_color),
                                           CARD_SLOT_W, CARD_SLOT_H)
        if drawing:
            c.set_layer_tag('box')
            renderPDF.draw(drawing, c, x, y)
        self._draw_card_pile_text(c, x, y, title_para, title_h, body_para, body_h,
                                  title_top_offset)

    def _draw_card_pile_rotated(self, c, pile, x, y,
                                title_para, title_h, body_para, body_h,
                                title_top_offset, direction='ccw'):
        """Draw a rotated card pile. Anchor (x, y) is the bottom-left of the
        rotated world-space footprint (H wide × W tall) regardless of direction.
          'ccw' (90 CCW): card bottom faces page right  → 'right' orientation.
          'cw'  (90 CW):  card bottom faces page left   → 'left' orientation."""
        c.saveState()
        c.translate(x, y)
        if direction == 'cw':
            c.rotate(-90)
            card_local_x = -CARD_SLOT_W
            card_local_y = 0
        else:
            c.rotate(90)
            card_local_x = 0
            card_local_y = -CARD_SLOT_H
        self._draw_card_pile_screen(c, pile, card_local_x, card_local_y)
        drawing = self._load_card_pile_svg(self._resolve_element_color(pile.element_color),
                                           CARD_SLOT_W, CARD_SLOT_H)
        if drawing:
            c.set_layer_tag('box')
            renderPDF.draw(drawing, c, card_local_x, card_local_y)
        self._draw_card_pile_text(c, card_local_x, card_local_y,
                                  title_para, title_h, body_para, body_h,
                                  title_top_offset)
        c.restoreState()

    def _draw_card_pile_text(self, c, x, y, title_para, title_h, body_para, body_h,
                             title_top_offset):
        """Draw the title and body paragraphs onto a card whose bottom-left is at (x, y)."""
        text_w = CARD_SLOT_W - 2 * CARD_PILE_PADDING
        card_top = y + CARD_SLOT_H

        if title_para is not None:
            title_top = card_top - title_top_offset
            title_para.wrapOn(c, text_w, title_h)
            title_para.drawOn(c, x + CARD_PILE_PADDING, title_top - title_h)

        if body_para is not None:
            gap = CARD_PILE_TITLE_TO_BODY_GAP if title_para is not None else 0.0
            body_top_offset = title_top_offset + title_h + gap
            body_top = card_top - body_top_offset
            body_para.wrapOn(c, text_w, body_h)
            body_para.drawOn(c, x + CARD_PILE_PADDING, body_top - body_h)

    def _header_height(self):
        return PHASE_HEADER_H

    def _banner_height_for_width(self, content_w):
        """Banner height when scaled to fill content_w, with height lock above PHASE_HEADER_LOCK_W."""
        from PIL import Image as PILImage
        pil_img = PILImage.open(PHASE_HEADERS['birdsong']['banner'])
        aspect = pil_img.size[0] / pil_img.size[1]
        if content_w <= PHASE_HEADER_LOCK_W:
            return max(content_w / aspect, PHASE_HEADER_MIN_H)
        else:
            return PHASE_HEADER_LOCK_W / aspect


class FactionBackLayoutEngine:
    """Renders the back side of a faction sheet (FactionBack model).

    Sections (top to bottom):
      1. Background image (same as FactionSheet front)
      2. Component Manifest band — pieces grouped by type (W/B/T/Other)
      3. Left column: attribute bars + Setup section with numbered SVG markers
      4. Right column: How to play title + body text
    """

    PIECE_COLUMNS = [
        ('warriors', ('W',)),
        ('buildings', ('B',)),
        ('tokens', ('T',)),
        ('other', ('C', 'O')),
    ]

    def __init__(self, faction_back):
        self.back = faction_back
        self.faction = faction_back.faction
        self.color_hex = self.faction.color or '#5B4A8A'
        self.faction_color = HexColor(self.color_hex)
        self._lang_code = _lang_code_for(self.faction)

        pieces = self._resolve_pieces(self.faction)
        self._pieces_by_col = []
        for label_key, types in self.PIECE_COLUMNS:
            title = self._label(label_key, label_key.title())
            col_pieces = [p for p in pieces if p.type in types]
            self._pieces_by_col.append((title, col_pieces))

        self._setup_steps = self._resolve_setup_steps(faction_back)

        self._setup_marker_svgs = {}
        for n in range(10):
            svg_path = _numbered_svg_path(PHASE_NUMBER_SVG_DIR, n, self._lang_code)
            if os.path.exists(svg_path):
                self._setup_marker_svgs[n] = self._load_colored_svg(
                    svg_path, '#000000', fit_size=SETUP_MARKER_HEIGHT,
                    fit_height_only=True,
                )

        # Faction-colored meeple used as a fallback icon for Warrior pieces
        # that have no small_icon attached.
        self._warrior_fallback_svg = None
        if os.path.exists(MEEPLE_SVG):
            try:
                self._warrior_fallback_svg = self._load_colored_svg(
                    MEEPLE_SVG, self.color_hex
                )
            except Exception:
                self._warrior_fallback_svg = None

        self._init_styles()

    def _label(self, key, default=''):
        return _pdf_text('back', self._lang_code).get(key, default)

    # ---------- Data resolution (works with real models or SimpleNamespace) ----------

    def _resolve_pieces(self, back):
        pieces_attr = getattr(back, 'pieces', None)
        if pieces_attr is None:
            return []
        if hasattr(pieces_attr, 'all'):
            return list(pieces_attr.all())
        return list(pieces_attr)

    def _resolve_setup_steps(self, back):
        steps_attr = getattr(back, 'setup_steps', None)
        if steps_attr is None:
            return []
        if hasattr(steps_attr, 'order_by'):
            try:
                return list(steps_attr.order_by('number'))
            except Exception:
                pass
        if hasattr(steps_attr, 'all'):
            items = list(steps_attr.all())
        else:
            items = list(steps_attr)
        return sorted(items, key=lambda s: getattr(s, 'number', 0))

    # ---------- Helpers reused from SheetLayoutEngine patterns ----------

    def _load_colored_svg(self, svg_path, color_hex, fit_size=None, fit_height_only=False):
        with open(svg_path, 'r') as f:
            svg_content = f.read()
        svg_content = svg_content.replace('#000000', color_hex)
        with tempfile.NamedTemporaryFile(suffix='.svg', mode='w', delete=False) as tmp:
            tmp.write(svg_content)
            tmp_path = tmp.name
        drawing = svg2rlg(tmp_path)
        os.unlink(tmp_path)
        if drawing and fit_size:
            if fit_height_only:
                scale = fit_size / drawing.height
            else:
                scale = min(fit_size / drawing.width, fit_size / drawing.height)
            drawing.width *= scale
            drawing.height *= scale
            drawing.scale(scale, scale)
        return drawing

    def _init_styles(self):
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.lib.enums import TA_LEFT
        from reportlab.lib import colors

        self.setup_step_style = ParagraphStyle(
            'BackSetupStep',
            fontName='Baskerville',
            fontSize=SETUP_BODY_SIZE,
            leading=SETUP_BODY_SIZE + 2,
            autoLeading='max',
            textColor=colors.black,
            alignment=TA_LEFT,
            spaceAfter=0,
        )
        self.howtoplay_body_style = ParagraphStyle(
            'BackHowToPlayBody',
            fontName='Baskerville',
            fontSize=HOWTOPLAY_BODY_SIZE,
            leading=HOWTOPLAY_BODY_SIZE + 2.5,
            autoLeading='max',
            textColor=colors.black,
            alignment=TA_LEFT,
            spaceAfter=HOWTOPLAY_BODY_SIZE * 0.5,
        )
        self.piece_label_style = ParagraphStyle(
            'BackPieceLabel',
            fontName='Baskerville',
            fontSize=MANIFEST_PIECE_LABEL_SIZE,
            leading=MANIFEST_PIECE_LABEL_SIZE + 1,
            textColor=colors.black,
        )

    # ---------- Entry point ----------

    LAYER_GROUPS = (
        frozenset({'background'}),
        frozenset({'box', 'fg'}),
    )

    def build(self, output_path, layered=False):
        if not layered:
            c = rl_canvas.Canvas(output_path, pagesize=landscape(letter))
            self._run_draw_pipeline(c)
            c.save()
            return

        real = rl_canvas.Canvas(output_path, pagesize=landscape(letter))
        proxy = LayerFilterCanvas(real)
        for group in self.LAYER_GROUPS:
            proxy._active_layers = group
            self._run_draw_pipeline(proxy)
            real.showPage()
        real.save()

    def _run_draw_pipeline(self, c):
        self._draw_background(c)

        # Lighten the background with a white screen at configured opacity.
        if BACK_BG_SCREEN_OPACITY > 0:
            c.set_layer_tag('background')
            c.saveState()
            c.setFillColorRGB(1, 1, 1, alpha=BACK_BG_SCREEN_OPACITY)
            c.setStrokeColorRGB(1, 1, 1, alpha=0)
            c.rect(0, 0, PAGE_W, PAGE_H, stroke=0, fill=1)
            c.restoreState()

        back_body_w = PAGE_W - (BACK_X_MARGIN * 2)

        # All remaining content (manifest, attribute bars, setup, how-to-play,
        # logo) is foreground for layered output — boxes and text are combined
        # into the same page on the back engine.
        c.set_layer_tag('fg')

        manifest_y = PAGE_H - BACK_TOP_MARGIN - MANIFEST_BOX_H
        self._draw_component_manifest(c, BACK_X_MARGIN, manifest_y, back_body_w, MANIFEST_BOX_H)

        columns_top = manifest_y - 0.18 * inch
        columns_bottom = BACK_BOTTOM_MARGIN
        columns_h = columns_top - columns_bottom

        left_w = (back_body_w - BACK_COLUMN_GAP) * LEFT_COL_W_RATIO
        right_w = back_body_w - BACK_COLUMN_GAP - left_w
        left_x = BACK_X_MARGIN
        right_x = left_x + left_w + BACK_COLUMN_GAP

        attrs_bottom = self._draw_attribute_bars(c, left_x, columns_top, left_w)
        self._draw_setup_section(c, left_x, attrs_bottom - 0.12 * inch, left_w, columns_bottom)

        self._draw_how_to_play(c, right_x, columns_top, right_w, columns_h)

        self._draw_forge_logo(c)

    def _apply_drawing_opacity(self, drawing, alpha):
        """Mirror of SheetLayoutEngine._apply_drawing_opacity."""
        from reportlab.graphics.shapes import Group
        def walk(node):
            if hasattr(node, 'fillOpacity'):
                node.fillOpacity = alpha
            if hasattr(node, 'strokeOpacity'):
                node.strokeOpacity = alpha
            contents = getattr(node, 'contents', None)
            if contents:
                for child in contents:
                    walk(child)
        walk(drawing)

    def _draw_forge_logo(self, c):
        """Stamp the Forge logo in the bottom-LEFT corner of the FactionBack.
        Two-pass watermark: faction color underneath, opposite of the
        faction-readable ink color overlaid on top, both at low opacity."""
        if not os.path.exists(FORGE_LOGO_SVG):
            return
        ink_on_faction_hex = '#FFFFFF' if _is_white_text_legible(self.color_hex) else '#000000'
        opposite_hex = '#FFFFFF' # if ink_on_faction_hex == '#000000' else '#000000'

        base_drawing = self._load_colored_svg(FORGE_LOGO_SVG, self.color_hex)
        if base_drawing is None:
            return
        scale = FORGE_LOGO_W / base_drawing.width
        target_h = base_drawing.height * scale
        base_drawing.width = FORGE_LOGO_W
        base_drawing.height = target_h
        base_drawing.scale(scale, scale)
        self._apply_drawing_opacity(base_drawing, FORGE_LOGO_BASE_OPACITY)

        x = FORGE_LOGO_PAD_X
        y = FORGE_LOGO_PAD_BOTTOM
        renderPDF.draw(base_drawing, c, x, y)

        overlay = self._load_colored_svg(FORGE_LOGO_SVG, opposite_hex)
        if overlay is None:
            return
        overlay.width = FORGE_LOGO_W
        overlay.height = target_h
        overlay.scale(scale, scale)
        self._apply_drawing_opacity(overlay, FORGE_LOGO_OVERLAY_OPACITY)
        renderPDF.draw(overlay, c, x, y)

    # ---------- Background (mirrors SheetLayoutEngine._draw_background) ----------

    def _draw_background(self, c):
        draw_faction_background(c, self.faction)

    # ---------- Component manifest band ----------

    def _draw_component_manifest(self, c, x, y, w, h):
        c.saveState()
        c.setStrokeColorRGB(0, 0, 0)
        c.setFillColorRGB(0, 0, 0)
        border_w = 1.0
        c.setLineWidth(border_w)
        half_bw = border_w / 2  # extend lines by half stroke width so corners meet flush

        title = self._label('manifest', 'Faction Component Manifest')
        title_size = MANIFEST_TITLE_SIZE
        # Tracked width = base width + (n-1) * charSpace extra
        title_w = (pdfmetrics.stringWidth(title, 'Luminari', title_size)
                   + max(len(title) - 1, 0) * MANIFEST_TITLE_CHAR_SPACE)
        gap = 6
        title_x_start = x + (w - title_w) / 2 - gap
        title_x_end = title_x_start + title_w + gap * 2
        top_y = y + h

        # Draw borders: bottom, left, right, and the top in two segments that skip the title.
        # Extend horizontal/vertical lines by half_bw at each end so strokes overlap at corners.
        c.line(x - half_bw, y, x + w + half_bw, y)                      # bottom
        c.line(x, y - half_bw, x, top_y + half_bw)                      # left
        c.line(x + w, y - half_bw, x + w, top_y + half_bw)              # right
        c.line(x - half_bw, top_y, title_x_start, top_y)                # top-left segment
        c.line(title_x_end, top_y, x + w + half_bw, top_y)              # top-right segment

        c.setFillColorRGB(0, 0, 0)
        cap_h = title_size * 0.70
        title_x = title_x_start + gap
        title_baseline = top_y - cap_h / 2
        txt = c.beginText(title_x, title_baseline)
        txt.setFont('Luminari', title_size)
        txt.setCharSpace(MANIFEST_TITLE_CHAR_SPACE)
        txt.textLine(title)
        c.drawText(txt)

        pad = MANIFEST_INNER_PAD
        inner_x = x + pad
        inner_y = y + pad
        inner_w = w - pad * 2
        inner_h = h - pad - title_size * 0.6

        col_w = inner_w / len(self.PIECE_COLUMNS)
        header_size = MANIFEST_COLUMN_HEADER_SIZE
        header_baseline_y = inner_y + inner_h - header_size

        for i, (col_title, pieces) in enumerate(self._pieces_by_col):
            col_x = inner_x + i * col_w
            c.setFillColorRGB(0, 0, 0)
            header_w = (pdfmetrics.stringWidth(col_title, 'Luminari', header_size)
                        + max(len(col_title) - 1, 0) * MANIFEST_COLUMN_HEADER_CHAR_SPACE)
            header_x = col_x + (col_w - header_w) / 2
            txt = c.beginText(header_x, header_baseline_y)
            txt.setFont('Luminari', header_size)
            txt.setCharSpace(MANIFEST_COLUMN_HEADER_CHAR_SPACE)
            txt.textLine(col_title)
            c.drawText(txt)

            if i > 0:
                divider_x = col_x
                c.setStrokeColorRGB(0, 0, 0)
                c.setLineWidth(MANIFEST_DIVIDER_W)
                band_top = y + h
                c.line(divider_x,
                       y + MANIFEST_DIVIDER_INSET_BOTTOM,
                       divider_x,
                       band_top - MANIFEST_DIVIDER_INSET_TOP)

            pieces_top = header_baseline_y - header_size * 0.4
            pieces_bottom = inner_y
            # Piece area spans from box-edge (or previous divider) to next divider (or box-edge),
            # inset by MANIFEST_PIECE_COL_H_PAD so text never touches the border or dividers.
            piece_area_left = (x if i == 0 else col_x) + MANIFEST_PIECE_COL_H_PAD
            piece_area_right = ((x + w) if i == len(self.PIECE_COLUMNS) - 1
                                else col_x + col_w) - MANIFEST_PIECE_COL_H_PAD
            self._draw_manifest_column_pieces(c, piece_area_left, pieces_bottom,
                                              piece_area_right - piece_area_left,
                                              pieces_top - pieces_bottom, pieces)
        c.restoreState()

    def _draw_manifest_column_pieces(self, c, x, y, w, h, pieces):
        if not pieces:
            c.saveState()
            c.setFont('Baskerville', MANIFEST_PIECE_LABEL_SIZE)
            c.setFillColorRGB(0, 0, 0)
            c.drawCentredString(x + w / 2, y + h / 2, f"({self._label('none', 'none')})")
            c.restoreState()
            return
        n = len(pieces)
        slot_h = h / n
        for idx, piece in enumerate(pieces):
            slot_top = y + h - idx * slot_h
            slot_bottom = slot_top - slot_h
            self._draw_piece(c, piece, x, slot_bottom, w, slot_h)

    def _draw_piece(self, c, piece, x, y, w, h):
        def _resolve_image(attr):
            if not attr:
                return None
            path_val = getattr(attr, 'path', None)
            if path_val and os.path.exists(path_val):
                return path_val
            if isinstance(attr, str) and os.path.exists(attr):
                return attr
            return None

        icon_path = _resolve_image(getattr(piece, 'small_icon', None))
        back_path = _resolve_image(getattr(piece, 'back_image', None))
        # Back image only stacks when there's also a front; on its own it falls
        # through into the existing single-image path.
        if back_path and not icon_path:
            icon_path = back_path
            back_path = None
        stacked = bool(icon_path and back_path)

        quantity = getattr(piece, 'quantity', 1) or 1
        name = getattr(piece, 'name', '') or ''
        qty_text = f'\u00d7{quantity}'

        label_size = MANIFEST_PIECE_LABEL_SIZE
        qty_size = label_size + 1
        icon_text_gap = MANIFEST_PIECE_ICON_TEXT_GAP

        # Icon sizing
        icon_max_h = min(MANIFEST_ICON_MAX_H, h - label_size * 1.1)
        icon_max_w = min(MANIFEST_ICON_MAX_W, w * 0.40)

        draw_w = 0
        draw_h = 0
        svg_fallback = None
        shape_fallback = None  # 'building' | 'token' | 'card'
        # Per-image draw sizes when stacked (front overlaps back). When not
        # stacked, front_*/back_* mirror draw_*/(0,0) so the draw block below
        # stays uniform.
        front_w = front_h = 0
        back_w = back_h = 0
        stack_off_x = stack_off_y = 0
        if icon_path:
            from reportlab.lib.utils import ImageReader
            try:
                if stacked:
                    shrink = MANIFEST_PIECE_STACK_SHRINK
                    img_max_w = icon_max_w * shrink
                    img_max_h = icon_max_h * shrink
                    iw, ih = ImageReader(icon_path).getSize()
                    fscale = min(img_max_w / iw, img_max_h / ih)
                    front_w = iw * fscale
                    front_h = ih * fscale
                    bw, bh = ImageReader(back_path).getSize()
                    bscale = min(img_max_w / bw, img_max_h / bh)
                    back_w = bw * bscale
                    back_h = bh * bscale
                    # Offset is a fraction of the larger image's size, so the
                    # back image always peeks out by a consistent amount.
                    ref_w = max(front_w, back_w)
                    ref_h = max(front_h, back_h)
                    stack_off_x = ref_w * MANIFEST_PIECE_STACK_OFFSET_FRAC
                    stack_off_y = ref_h * MANIFEST_PIECE_STACK_OFFSET_FRAC
                    # Envelope = bounding box of the offset stack.
                    draw_w = max(front_w, back_w) + stack_off_x
                    draw_h = max(front_h, back_h) + stack_off_y
                else:
                    iw, ih = ImageReader(icon_path).getSize()
                    scale = min(icon_max_w / iw, icon_max_h / ih)
                    draw_w = iw * scale
                    draw_h = ih * scale
                    front_w, front_h = draw_w, draw_h
            except Exception:
                icon_path = None
                stacked = False
                draw_w = 0
                draw_h = 0
                front_w = front_h = 0
                back_w = back_h = 0
                stack_off_x = stack_off_y = 0

        # Fallbacks: if no icon was supplied, dispatch on piece type.
        if not icon_path:
            ptype = getattr(piece, 'type', None)
            if ptype == 'W' and self._warrior_fallback_svg is not None:
                svg_fallback = self._warrior_fallback_svg
                base_w = svg_fallback.width or 1
                base_h = svg_fallback.height or 1
                scale = min(icon_max_w / base_w, icon_max_h / base_h)
                draw_w = base_w * scale
                draw_h = base_h * scale
            elif ptype == 'B':
                shape_fallback = 'building'
                side = min(icon_max_w, icon_max_h)
                draw_w = draw_h = side
            elif ptype == 'T':
                shape_fallback = 'token'
                side = min(icon_max_w, icon_max_h)
                draw_w = draw_h = side
            elif ptype == 'C':
                shape_fallback = 'card'
                card_ratio = 65.8 / 90.4  # card_pile.svg aspect (portrait)
                scaled_h = min(icon_max_w / card_ratio, icon_max_h)
                draw_h = scaled_h
                draw_w = scaled_h * card_ratio
            # 'O' (Other): TODO: add fallback for 'O' (Other) pieces here when needed

        # Available width for the label block (×qty + name), respecting horizontal padding
        h_pad = MANIFEST_PIECE_LABEL_H_PAD
        v_pad = MANIFEST_PIECE_V_PAD
        inner_x = x + h_pad
        inner_w = w - h_pad * 2
        inner_y = y + v_pad
        inner_h = h - v_pad * 2
        has_icon = bool(icon_path) or svg_fallback is not None or shape_fallback is not None
        available_w = inner_w - (draw_w + icon_text_gap if has_icon else 0)
        if available_w < 20:
            available_w = 20

        # Build the wrapped name paragraph. Prefix "×N " inline with the name so
        # it wraps as one block and stays italicized after the quantity.
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.lib.enums import TA_LEFT
        from reportlab.lib import colors
        style = ParagraphStyle(
            'ManifestPieceLabel',
            fontName='Baskerville',
            fontSize=label_size,
            leading=label_size * MANIFEST_PIECE_NAME_LEADING,
            textColor=colors.black,
            alignment=TA_LEFT,
            spaceAfter=0,
        )
        # Quantity in upright Baskerville, name in italic.
        qty_markup = f'<font name="Baskerville" size="{qty_size}">{qty_text}</font>'
        if name:
            safe_name = name.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
            markup = f'{qty_markup}&nbsp;{safe_name}'
        else:
            markup = qty_markup
        para = Paragraph(markup, style)
        para.wrap(available_w, 9999)
        tighten_large_font_lines(para)
        para_h = para.height
        # Actual rendered width is the widest line. FragLine stores maxWidth (the
        # wrap box it was given) and extraSpace (unused trailing space), so the
        # real line width is maxWidth - extraSpace.
        try:
            widths = []
            for ln in para.blPara.lines:
                if hasattr(ln, 'maxWidth'):
                    widths.append(ln.maxWidth - getattr(ln, 'extraSpace', 0))
                elif isinstance(ln, tuple) and len(ln) >= 2:
                    # older Paragraph line format: (extraSpace, words)
                    widths.append(available_w - ln[0])
            para_w = max(widths) if widths else available_w
        except Exception:
            para_w = available_w
        para_w = max(0, min(para_w, available_w))

        # Horizontally center icon+label group within the padded inner column.
        total_w = (draw_w + icon_text_gap if has_icon else 0) + para_w
        start_x = inner_x + (inner_w - total_w) / 2
        mid_y = inner_y + inner_h / 2

        if icon_path:
            icon_x = start_x
            icon_y = mid_y - draw_h / 2
            ptype = getattr(piece, 'type', None)
            is_building = ptype == 'B'
            is_token = ptype == 'T'

            def _draw_rounded(img, ix, iy, iw_, ih_):
                """Buildings clip to a 15%-radius rounded rect; tokens clip to a
                circle inscribed in the image bounds. Other piece types draw
                unclipped."""
                if is_building:
                    radius = min(iw_, ih_) * 0.15
                    c.saveState()
                    p = c.beginPath()
                    p.roundRect(ix, iy, iw_, ih_, radius)
                    c.clipPath(p, stroke=0, fill=0)
                    c.drawImage(img, ix, iy, width=iw_, height=ih_,
                                preserveAspectRatio=True, mask='auto')
                    c.restoreState()
                elif is_token:
                    side = min(iw_, ih_)
                    cx = ix + iw_ / 2
                    cy = iy + ih_ / 2
                    c.saveState()
                    p = c.beginPath()
                    p.circle(cx, cy, side / 2)
                    c.clipPath(p, stroke=0, fill=0)
                    c.drawImage(img, ix, iy, width=iw_, height=ih_,
                                preserveAspectRatio=True, mask='auto')
                    c.restoreState()
                else:
                    c.drawImage(img, ix, iy, width=iw_, height=ih_,
                                preserveAspectRatio=True, mask='auto')

            if stacked:
                # Back image at bottom-right, front image at top-left so the
                # front (small_icon) covers the lower-right corner of the back.
                back_x = icon_x + draw_w - back_w
                back_y = icon_y
                _draw_rounded(back_path, back_x, back_y, back_w, back_h)
                front_x = icon_x
                front_y = icon_y + draw_h - front_h
                _draw_rounded(icon_path, front_x, front_y, front_w, front_h)
            else:
                _draw_rounded(icon_path, icon_x, icon_y, draw_w, draw_h)
            label_x = icon_x + draw_w + icon_text_gap
        elif svg_fallback is not None:
            icon_x = start_x
            icon_y = mid_y - draw_h / 2
            # _load_colored_svg without fit_size doesn't pre-scale, so apply scale now.
            base_w = svg_fallback.width or 1
            base_h = svg_fallback.height or 1
            sx = draw_w / base_w
            sy = draw_h / base_h
            c.saveState()
            c.translate(icon_x, icon_y)
            c.scale(sx, sy)
            renderPDF.draw(svg_fallback, c, 0, 0)
            c.restoreState()
            label_x = icon_x + draw_w + icon_text_gap
        elif shape_fallback is not None:
            icon_x = start_x
            icon_y = mid_y - draw_h / 2
            c.saveState()
            c.setFillColor(self.faction_color)
            c.setFillAlpha(1.0)
            if shape_fallback == 'building':
                s = draw_w
                c.roundRect(icon_x, icon_y, s, s, s * 0.15, fill=1, stroke=0)
            elif shape_fallback == 'token':
                s = draw_w
                c.circle(icon_x + s / 2, icon_y + s / 2, s / 2, fill=1, stroke=0)
            elif shape_fallback == 'card':
                c.roundRect(icon_x, icon_y, draw_w, draw_h, draw_w * 0.10, fill=1, stroke=0)
            c.restoreState()
            label_x = icon_x + draw_w + icon_text_gap
        else:
            label_x = start_x

        # Vertically center the paragraph on the row midpoint.
        para.drawOn(c, label_x, mid_y - para_h / 2)

    # ---------- Attribute bars ----------

    ATTR_FIELDS = [
        ('complexity', 'complexity'),
        ('card_wealth', 'card_wealth'),
        ('aggression', 'aggression'),
        ('crafting_ability', 'crafting_ability'),
    ]

    def _draw_attribute_bars(self, c, x, top_y, w):
        """Draws two columns of two bars (like the reference). Returns bottom y of block."""
        x = x + ATTR_BLOCK_INDENT
        w = max(w - ATTR_BLOCK_INDENT, 40)
        col_gap = 0.2 * inch
        col_w = (w - col_gap) / 2
        cursor_y = top_y - ATTR_BLOCK_TOP_PAD

        pairs = [
            (self.ATTR_FIELDS[0], self.ATTR_FIELDS[1]),
            (self.ATTR_FIELDS[2], self.ATTR_FIELDS[3]),
        ]

        row_h = ATTR_LABEL_SIZE + ATTR_LABEL_GAP + ATTR_BAR_H
        first_row_top = cursor_y                               # top_y of first row for border
        for left, right in pairs:
            self._draw_attribute_row(c, x, cursor_y, col_w, left[0], left[1])
            self._draw_attribute_row(c, x + col_w + col_gap, cursor_y, col_w, right[0], right[1])
            cursor_y -= row_h + ATTR_ROW_GAP
        last_row_bar_bottom = cursor_y + ATTR_ROW_GAP           # bar bottom of final row

        # Single continuous left border per column, extended above the first label
        # and below the last bar per the *_EXT constants.
        border_top = first_row_top + ATTR_BAR_BORDER_TOP_EXT
        border_bottom = last_row_bar_bottom - ATTR_BAR_BORDER_BOTTOM_EXT
        c.saveState()
        c.setStrokeColorRGB(0, 0, 0)
        c.setLineWidth(ATTR_BAR_BORDER_LEFT_W)
        c.line(x, border_bottom, x, border_top)
        c.line(x + col_w + col_gap, border_bottom, x + col_w + col_gap, border_top)
        c.restoreState()

        return cursor_y + ATTR_ROW_GAP

    def _draw_attribute_row(self, c, x, top_y, w, field, label_key):
        """Draw a single attribute row (italic label + filled bar with level text).

        Mirrors the web partial: left black border, grey track, faction-color fill
        sized by ATTR_BAR_FILL_RATIOS, white level text (or black when white is
        not legible on the faction color).
        """
        value = getattr(self.back, field, 'N') or 'N'
        label = self._label(label_key, label_key.replace('_', ' ').title())

        c.saveState()

        # Italic label above the bar ("Complexity", etc.)
        c.setFillColorRGB(0, 0, 0)
        label_baseline = top_y - ATTR_LABEL_SIZE
        label_x = x + ATTR_LABEL_BORDER_LEFT_PAD
        txt = c.beginText(label_x, label_baseline)
        txt.setFont('Baskerville-Italic', ATTR_LABEL_SIZE)
        txt.setCharSpace(ATTR_LABEL_CHAR_SPACE)
        txt.textLine(label)
        c.drawText(txt)

        # Bar geometry
        bar_top = label_baseline - ATTR_LABEL_GAP
        bar_y = bar_top - ATTR_BAR_H
        bar_x = x + ATTR_BAR_BORDER_LEFT_PAD
        bar_w = w - ATTR_BAR_BORDER_LEFT_PAD

        # Resolve the level label up front so the fill can grow to keep it inside.
        # 'N' keeps its tiny sliver fill (text renders in black outside the color).
        level_label = _pdf_text('attr_levels', self._lang_code).get(value, '')

        # Faction-color fill sized by level (no track — empty portion is transparent).
        # For L/M/H, expand fill to fit the level text + padding when the translated
        # label is wider than the geometric ratio would allow.
        fill_ratio = ATTR_BAR_FILL_RATIOS.get(value, 0.0)
        fill_w = bar_w * fill_ratio
        if level_label and value != 'N':
            text_w = (pdfmetrics.stringWidth(level_label, 'Baskerville-Bold', ATTR_BAR_LEVEL_FONT_SIZE)
                      + max(len(level_label) - 1, 0) * ATTR_BAR_LEVEL_CHAR_SPACE)
            min_fill_w = text_w + ATTR_BAR_LEVEL_TEXT_X_PAD * 2
            fill_w = min(max(fill_w, min_fill_w), bar_w)
        if fill_w > 0:
            c.setFillColor(self.faction_color)
            c.rect(bar_x, bar_y, fill_w, ATTR_BAR_H, stroke=0, fill=1)

        # Level text inside bar — white when legible on faction color, else black.
        # 'N' always uses black since the fill is barely visible.
        if level_label:
            if value == 'N':
                text_color = ATTR_BAR_LEVEL_N_TEXT_COLOR
            elif _is_white_text_legible(self.color_hex, ATTR_WHITE_TEXT_MIN_CONTRAST):
                text_color = ATTR_BAR_LEVEL_TEXT_COLOR_LIGHT
            else:
                text_color = ATTR_BAR_LEVEL_TEXT_COLOR_DARK
            c.setFillColor(HexColor(text_color))
            text_y = bar_y + (ATTR_BAR_H - ATTR_BAR_LEVEL_FONT_SIZE) / 2 + ATTR_BAR_LEVEL_FONT_SIZE * 0.18
            txt = c.beginText(bar_x + ATTR_BAR_LEVEL_TEXT_X_PAD, text_y)
            txt.setFont('Baskerville-Bold', ATTR_BAR_LEVEL_FONT_SIZE)
            txt.setCharSpace(ATTR_BAR_LEVEL_CHAR_SPACE)
            txt.textLine(level_label)
            c.drawText(txt)

        c.restoreState()

    # ---------- Setup section ----------

    def _draw_setup_section(self, c, x, top_y, w, bottom_y):
        setup_order = getattr(self.back, 'setup_order', '') or ''
        setup_word = self._label('setup', 'Setup')
        title = f'{setup_word} ({setup_order})' if setup_order else setup_word
        c.saveState()
        c.setFont('Baskerville', SETUP_TITLE_SIZE)
        c.setFillColorRGB(0, 0, 0)
        title_baseline = top_y - SETUP_TITLE_SIZE
        c.drawString(x, title_baseline, title)
        c.restoreState()

        cursor_y = title_baseline - SETUP_TITLE_GAP

        step_x = x + SETUP_STEP_INDENT
        step_w = max(w - SETUP_STEP_INDENT, 40)
        for step in self._setup_steps:
            number = getattr(step, 'number', 0)
            text = getattr(step, 'text', '') or ''
            cursor_y = self._draw_setup_step(c, step_x, cursor_y, step_w, number, text)
            cursor_y -= SETUP_STEP_GAP
            if cursor_y < bottom_y:
                break

    def _draw_setup_step(self, c, x, top_y, w, number, text):
        marker = self._setup_marker_svgs.get(number)
        # Reserve a uniform slot width for the marker (= SETUP_MARKER_SIZE) so
        # body text lines up across steps, even though individual digit widths vary.
        slot_w = SETUP_MARKER_SIZE
        marker_w = marker.width if marker is not None else SETUP_MARKER_SIZE
        marker_h = marker.height if marker is not None else SETUP_MARKER_SIZE

        text_x = x + slot_w + SETUP_MARKER_TEXT_GAP
        text_w = w - (slot_w + SETUP_MARKER_TEXT_GAP)
        if text_w < 40:
            text_w = 40

        markup = format_step_markup(text)
        para = Paragraph(markup, self.setup_step_style)
        _, para_h = para.wrap(text_w, 9999)
        tighten_large_font_lines(para)
        para_h = para.height

        block_h = max(marker_h, para_h)
        block_bottom = top_y - block_h

        # Vertically center the marker on the paragraph's midline.
        para_mid_y = top_y - para_h / 2
        marker_x = x + (slot_w - marker_w) / 2
        marker_y = para_mid_y - marker_h / 2

        if marker is not None:
            renderPDF.draw(marker, c, marker_x, marker_y)
        else:
            c.saveState()
            c.setFillColor(self.faction_color)
            c.circle(marker_x + marker_w / 2, para_mid_y, marker_w / 2, stroke=0, fill=1)
            c.setFont('Baskerville-Bold', marker_h * 0.6)
            c.setFillColorRGB(1, 1, 1)
            c.drawCentredString(marker_x + marker_w / 2, para_mid_y - marker_h * 0.2, str(number))
            c.restoreState()

        para.drawOn(c, text_x, top_y - para_h)

        return block_bottom

    # ---------- How to play ----------

    def _draw_how_to_play(self, c, x, top_y, w, h):
        suffix = getattr(self.back, 'how_to_play_title', '') or 'Faction'
        title = f"{self._label('playing', 'Playing the')} {suffix}"
        body = getattr(self.back, 'how_to_play_text', '') or ''

        c.saveState()
        c.setFont('Baskerville', HOWTOPLAY_TITLE_SIZE)
        c.setFillColorRGB(0, 0, 0)
        title_baseline = top_y - HOWTOPLAY_TITLE_SIZE
        c.drawString(x, title_baseline, title)
        c.restoreState()

        body_top = title_baseline - HOWTOPLAY_TITLE_GAP
        body_bottom = top_y - h
        body_h = body_top - body_bottom

        # Resolve optional back_image path, scaled by user back_image_size against
        # the available text envelope (column width × body height after title).
        img_path, img_w, img_h = self._resolve_back_image(w_avail=w, h_avail=body_h)

        # Draw the image (if any) regardless of whether body text exists.
        if img_path:
            img_x = PAGE_W - img_w
            img_y = 0
            c.drawImage(img_path, img_x, img_y, width=img_w, height=img_h,
                        preserveAspectRatio=True, mask='auto')

        if body_h <= 0 or not body:
            return

        markup = format_step_markup(body)
        para = Paragraph(markup, self.howtoplay_body_style)

        if not img_path:
            para.wrap(w, body_h)
            tighten_large_font_lines(para)
            para_h = para.height
            para.drawOn(c, x, body_top - para_h)
            return

        # Variable-width wrap within a single Paragraph. Passing a per-line
        # widthList to breakLines lets the width change mid-paragraph at
        # img_top, so running prose continues seamlessly from wide to narrow.
        img_top = img_y + img_h
        img_left = img_x  # left edge of the image on the page
        right_edge = x + w
        full_w = w
        # Narrow width runs from text x to the image's left edge (minus gap).
        # If the image starts to the right of the text column, only the overlap
        # eats into the text; if it starts inside the column, the text is
        # clamped to stop before the image.
        narrow_w = max(min(right_edge, img_left - HOWTOPLAY_IMAGE_GAP) - x, 20)

        # Estimate how many leading lines fit above img_top at full width, then
        # transition one line earlier so the narrow section starts before the
        # last full-width line crowds the image's top edge.
        leading = para.style.leading
        wide_space = max(body_top - img_top, 0)
        n_wide = int(wide_space // leading) if leading > 0 else 0
        n_wide = max(n_wide - 1, 0)

        # Cap against the total number of lines this body will produce at full
        # width, so we never promise more wide lines than exist.
        total_lines = len(para.breakLines([full_w]).lines)
        n_wide = min(n_wide, total_lines)

        widths = [full_w] * n_wide + [narrow_w] * max(total_lines - n_wide, 0)
        # Pad with narrow widths so breakLines never runs out (harmless if unused).
        if len(widths) < total_lines + 8:
            widths += [narrow_w] * (total_lines + 8 - len(widths))

        para = Paragraph(markup, self.howtoplay_body_style)
        para.blPara = para.breakLines(widths)
        para.width = full_w

        def _line_h(line):
            a = getattr(line, 'ascent', None)
            d = getattr(line, 'descent', None)
            if a is None or d is None:
                return leading
            return max(a - d, leading)

        para.height = sum(_line_h(line) for line in para.blPara.lines)
        tighten_large_font_lines(para)
        para.drawOn(c, x, body_top - para.height)

    def _resolve_back_image(self, w_avail, h_avail):
        """Returns (path, draw_w, draw_h) for the optional FactionBack image,
        aspect-preserving-fit into (w_avail, h_avail), then scaled by
        self.back.back_image_size as a percentage (0–100). Returns
        (None, 0, 0) if no image is set or the envelope is non-positive."""
        img_attr = getattr(self.back, 'back_image', None)
        if not img_attr or w_avail <= 0 or h_avail <= 0:
            return None, 0, 0
        path_val = getattr(img_attr, 'path', None)
        if not path_val and isinstance(img_attr, str):
            path_val = img_attr
        if not path_val or not os.path.exists(path_val):
            return None, 0, 0
        try:
            from reportlab.lib.utils import ImageReader
            iw, ih = ImageReader(path_val).getSize()
        except Exception:
            return None, 0, 0
        scale_max = min(w_avail / iw, h_avail / ih)
        pct = getattr(self.back, 'back_image_size', 75) / 100.0
        pct = max(0.10, min(1.0, pct))
        scale = scale_max * pct
        return path_val, iw * scale, ih * scale


class SetupCardLayoutEngine:
    """Renders a single SetupCard as a poker-card-sized PDF.

    Layer order (bottom to top):
      1. adset/background.png (full canvas)
      2. adset/militant3.png or adset/insurgent2.png (full canvas)
      3. Reach number (white Luminari, bottom-right)
      4. Faction-color band (above the bottom strip)
      5. Optional header image (bottom-left aligned to band, hangs upward)
      6. Faction name (Luminari, two-tier sizing + wrap fallback)
      7. Numbered setup steps inside the white usable area
    """

    def __init__(self, card):
        self.card = card
        self.faction = card.faction
        self.color_hex = self.faction.color or '#5B4A8A'
        self.faction_color = HexColor(self.color_hex)

        self._setup_steps = self._resolve_setup_steps(card)

        self._setup_marker_svgs = {}
        lang_code = _lang_code_for(self.faction)
        for n in range(10):
            svg_path = _numbered_svg_path(SETUP_CARD_NUMBER_SVG_DIR, n, lang_code)
            if os.path.exists(svg_path):
                self._setup_marker_svgs[n] = self._load_colored_svg(
                    svg_path, '#000000', fit_size=SETUP_CARD_MARKER_HEIGHT,
                    fit_height_only=True,
                )

        self._init_styles()

    # ---------- Helpers ----------

    def _resolve_setup_steps(self, card):
        steps_attr = getattr(card, 'setup_steps', None)
        if steps_attr is None:
            return []
        if hasattr(steps_attr, 'order_by'):
            try:
                return list(steps_attr.order_by('number'))
            except Exception:
                pass
        if hasattr(steps_attr, 'all'):
            items = list(steps_attr.all())
        else:
            items = list(steps_attr)
        return sorted(items, key=lambda s: getattr(s, 'number', 0))

    def _load_colored_svg(self, svg_path, color_hex, fit_size=None, fit_height_only=False):
        with open(svg_path, 'r') as f:
            svg_content = f.read()
        svg_content = svg_content.replace('#000000', color_hex)
        with tempfile.NamedTemporaryFile(suffix='.svg', mode='w', delete=False) as tmp:
            tmp.write(svg_content)
            tmp_path = tmp.name
        drawing = svg2rlg(tmp_path)
        os.unlink(tmp_path)
        if drawing and fit_size:
            if fit_height_only:
                scale = fit_size / drawing.height
            else:
                scale = min(fit_size / drawing.width, fit_size / drawing.height)
            drawing.width *= scale
            drawing.height *= scale
            drawing.scale(scale, scale)
        return drawing

    def _init_styles(self):
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.lib.enums import TA_LEFT
        from reportlab.lib import colors

        self.setup_step_style = ParagraphStyle(
            'SetupCardStep',
            fontName='Baskerville',
            fontSize=SETUP_CARD_STEP_BODY_SIZE,
            leading=SETUP_CARD_STEP_BODY_SIZE + 1.5,
            autoLeading='max',
            textColor=colors.black,
            alignment=TA_LEFT,
            spaceAfter=0,
        )

    def _header_image_path(self):
        img = getattr(self.card, 'header_image', None)
        if not img:
            return None
        path_val = getattr(img, 'path', None)
        if not path_val and isinstance(img, str):
            path_val = img
        if not path_val or not os.path.exists(path_val):
            return None
        return path_val

    def _type_backdrop_path(self):
        return SETUP_CARD_MILITANT_PNG if self.card.type == 'M' else SETUP_CARD_INSURGENT_PNG

    # ---------- Faction name fitting ----------

    def _fit_faction_name(self, name, max_w):
        """Returns (lines, font_size). Tries large/single, small/single,
        small/two-line wrap (best mid-split), in that order."""
        large = SETUP_CARD_NAME_SIZE_LARGE
        small = SETUP_CARD_NAME_SIZE_SMALL

        if pdfmetrics.stringWidth(name, 'Luminari', large) <= max_w:
            return [name], large
        if pdfmetrics.stringWidth(name, 'Luminari', small) <= max_w:
            return [name], small

        # Try wrapping at the whitespace split closest to the middle
        # that keeps both lines within max_w at the smaller size.
        words = name.split()
        if len(words) >= 2:
            mid = len(words) / 2
            # Sort split points by distance from middle (closest first).
            candidates = sorted(range(1, len(words)), key=lambda i: abs(i - mid))
            for i in candidates:
                line1 = ' '.join(words[:i])
                line2 = ' '.join(words[i:])
                if (pdfmetrics.stringWidth(line1, 'Luminari', small) <= max_w
                        and pdfmetrics.stringWidth(line2, 'Luminari', small) <= max_w):
                    return [line1, line2], small

        # Degenerate: a single word too long for the band. Just draw small.
        return [name], small

    # ---------- Entry point ----------

    LAYER_GROUPS = (
        frozenset({'background', 'box'}),
        frozenset({'fg'}),
    )

    def build(self, output_path, layered=False):
        if not layered:
            c = rl_canvas.Canvas(output_path, pagesize=(CARD_SLOT_W, CARD_SLOT_H))
            self._run_draw_pipeline(c)
            c.showPage()
            c.save()
            return

        real = rl_canvas.Canvas(output_path, pagesize=(CARD_SLOT_W, CARD_SLOT_H))
        proxy = LayerFilterCanvas(real)
        for group in self.LAYER_GROUPS:
            proxy._active_layers = group
            self._run_draw_pipeline(proxy)
            real.showPage()
        real.save()

    def _run_draw_pipeline(self, c):
        # Layers 1-2: backgrounds
        c.set_layer_tag('background')
        if os.path.exists(SETUP_CARD_BG_PNG):
            c.drawImage(SETUP_CARD_BG_PNG, 0, 0, CARD_SLOT_W, CARD_SLOT_H,
                        preserveAspectRatio=False, mask='auto')
        backdrop = self._type_backdrop_path()
        if os.path.exists(backdrop):
            c.drawImage(backdrop, 0, 0, CARD_SLOT_W, CARD_SLOT_H,
                        preserveAspectRatio=False, mask='auto')

        # Layer 3: reach (white Luminari, bottom-right)
        c.set_layer_tag('fg')
        self._draw_reach(c)

        # Title: "ADVANCED SETUP" (localized) centered near top. Some
        # languages (e.g. French "MISE EN PLACE AVANCÉE") are much longer
        # than the English original — shrink in place rather than wrap.
        # Militant factions get a sword icon to the left of the title; the
        # available text width is reduced by 2x the sword size so the title
        # stays visually centered (sword on the left, matching gutter on
        # the right).
        title_text = _pdf_label('adset', 'title', self.faction, default=SETUP_CARD_TITLE_TEXT)
        title_font_size = SETUP_CARD_TITLE_FONT_SIZE
        title_char_spacing = SETUP_CARD_TITLE_CHAR_SPACING
        is_militant = self.card.type == 'M'
        title_max_w = SETUP_CARD_TITLE_MAX_W
        if is_militant:
            # The sword's left edge is clamped to SETUP_CARD_SWORD_MIN_LEFT_INSET
            # from the card edge, with a mirrored gutter on the right so the
            # title stays visually centered. The title window is whatever is
            # left between sword + gap on each side. This caps how far the
            # sword can slide outward — once the title gets long enough to
            # bump into this window, the title shrinks instead of the sword
            # drifting further left.
            militant_max_w = CARD_SLOT_W - 2 * (
                SETUP_CARD_SWORD_MIN_LEFT_INSET + SETUP_CARD_SWORD_SIZE + SETUP_CARD_SWORD_TITLE_GAP
            )
            title_max_w = min(title_max_w, militant_max_w)

        def _title_width(font_size, char_spacing):
            return (
                pdfmetrics.stringWidth(title_text, 'Baskerville-Bold', font_size)
                + char_spacing * max(len(title_text) - 1, 0)
            )

        title_w = _title_width(title_font_size, title_char_spacing)
        if title_w > title_max_w:
            scale = max(
                SETUP_CARD_TITLE_MIN_FONT_SIZE / SETUP_CARD_TITLE_FONT_SIZE,
                title_max_w / title_w,
            )
            title_font_size = SETUP_CARD_TITLE_FONT_SIZE * scale
            title_char_spacing = SETUP_CARD_TITLE_CHAR_SPACING * scale
            title_w = _title_width(title_font_size, title_char_spacing)

        title_x = (CARD_SLOT_W - title_w) / 2
        title_baseline_y = CARD_SLOT_H - SETUP_CARD_TITLE_TOP_MARGIN

        c.saveState()
        c.setFillColorRGB(1, 1, 1)
        txt = c.beginText(title_x, title_baseline_y)
        txt.setFont('Baskerville-Bold', title_font_size)
        txt.setCharSpace(title_char_spacing)
        txt.textLine(title_text)
        c.drawText(txt)
        c.restoreState()

        # Militant sword to the left of the title. Vertical position is
        # locked to the unscaled title font size — long localized titles
        # may shrink the text, but the sword stays put and only shifts
        # horizontally to track the title's left edge.
        if is_militant and os.path.exists(SETUP_CARD_SWORD_PNG):
            sword_size = SETUP_CARD_SWORD_SIZE
            sword_x = max(
                title_x - SETUP_CARD_SWORD_TITLE_GAP - sword_size,
                SETUP_CARD_SWORD_MIN_LEFT_INSET,
            )
            sword_y = title_baseline_y + (SETUP_CARD_TITLE_FONT_SIZE / 2) - (sword_size / 2) + SETUP_CARD_SWORD_Y_NUDGE
            c.drawImage(
                SETUP_CARD_SWORD_PNG, sword_x, sword_y, sword_size, sword_size,
                preserveAspectRatio=True, mask='auto',
            )

        # Layer 4: faction-color band
        band_x = SETUP_CARD_BAND_X_INSET
        band_y = CARD_SLOT_H - SETUP_CARD_BAND_TOP_INSET - SETUP_CARD_BAND_HEIGHT
        band_w = CARD_SLOT_W - 2 * SETUP_CARD_BAND_X_INSET
        band_h = SETUP_CARD_BAND_HEIGHT
        c.set_layer_tag('box')
        c.saveState()
        c.setFillColor(self.faction_color)
        c.setStrokeColorRGB(0, 0, 0, alpha=0)
        c.rect(band_x, band_y, band_w, band_h, stroke=0, fill=1)
        c.restoreState()

        # Layer 5: header image (optional)
        c.set_layer_tag('box')
        header_path = self._header_image_path()
        has_header = self._draw_header_image(c, header_path, band_x, band_y, band_w)

        # Layer 6: faction name
        c.set_layer_tag('fg')
        self._draw_faction_name(c, band_x, band_y, band_w, band_h, has_header)

        # Layer 7: setup steps inside the white usable area
        self._draw_setup_steps(c)

        # Forge logo watermark (bottom-left, drawn last so it sits on top)
        self._draw_forge_logo(c)

    # ---------- Drawing helpers ----------

    def _apply_drawing_opacity(self, drawing, alpha):
        from reportlab.graphics.shapes import Group
        def walk(node):
            if hasattr(node, 'fillOpacity'):
                node.fillOpacity = alpha
            if hasattr(node, 'strokeOpacity'):
                node.strokeOpacity = alpha
            contents = getattr(node, 'contents', None)
            if contents:
                for child in contents:
                    walk(child)
        walk(drawing)

    def _draw_forge_logo(self, c):
        """Soft Forge-logo watermark in the bottom-left corner. Single pass in
        a cool neutral grey (midpoint of #C0C5CC and #EAEEF6)."""
        if not os.path.exists(FORGE_LOGO_SVG):
            return

        drawing = self._load_colored_svg(FORGE_LOGO_SVG, '#bfc3cb') #D5D9E1
        if drawing is None:
            return
        logo_w = 0.08 * inch
        scale = logo_w / drawing.width
        target_h = drawing.height * scale
        drawing.width = logo_w
        drawing.height = target_h
        drawing.scale(scale, scale)
        self._apply_drawing_opacity(drawing, 0.15)

        x = CARD_SLOT_W - 0.03 * inch - logo_w
        y = 0.02 * inch
        c.set_layer_tag('fg')
        renderPDF.draw(drawing, c, x, y)

    def _draw_reach(self, c):
        c.saveState()
        c.setFont('Luminari', SETUP_CARD_REACH_FONT_SIZE)
        c.setFillColorRGB(1, 1, 1)
        c.drawCentredString(
            CARD_SLOT_W - SETUP_CARD_REACH_RIGHT_INSET,
            SETUP_CARD_REACH_BOTTOM_INSET,
            str(self.card.reach),
        )
        c.restoreState()

    def _draw_header_image(self, c, path, band_x, band_y, band_w):
        if not path:
            return False
        try:
            from reportlab.lib.utils import ImageReader
            iw, ih = ImageReader(path).getSize()
        except Exception:
            return False
        if iw <= 0 or ih <= 0:
            return False
        draw_w = band_w
        draw_h = ih * (draw_w / iw)
        if draw_h > SETUP_CARD_HEADER_MAX_H:
            draw_h = SETUP_CARD_HEADER_MAX_H
            draw_w = iw * (draw_h / ih)
        # Bottom-left of image aligned to bottom-left of band
        c.drawImage(path, band_x, band_y, draw_w, draw_h,
                    preserveAspectRatio=True, mask='auto')
        return True

    def _draw_faction_name(self, c, band_x, band_y, band_w, band_h, has_header):
        name = self.faction.faction_name or ''
        if not name:
            return

        usable_w = band_w - 2 * SETUP_CARD_BAND_TEXT_PADDING
        lines, size = self._fit_faction_name(name, usable_w)

        # Color: white if header overlays the band OR white is legible on faction color
        if has_header or _is_white_text_legible(self.color_hex):
            fill = (1, 1, 1)
        else:
            fill = (0, 0, 0)

        leading = size + SETUP_CARD_NAME_LINE_GAP
        total_h = size + leading * (len(lines) - 1)
        # Vertically center the text block within the band.
        # Approximate cap-height as 0.72 * size for centering.
        cap_h = size * 0.72
        first_baseline_y = band_y + (band_h + total_h) / 2 - cap_h - (size - cap_h) / 2

        c.saveState()
        c.setFont('Luminari', size)
        c.setFillColorRGB(*fill)
        text_x = band_x + SETUP_CARD_NAME_LEFT_PADDING
        for i, line in enumerate(lines):
            y = first_baseline_y - i * leading
            c.drawString(text_x, y, line)
        c.restoreState()

    def _draw_setup_steps(self, c):
        body_x = SETUP_CARD_BODY_X_INSET
        body_w = CARD_SLOT_W - 2 * SETUP_CARD_BODY_X_INSET
        cursor_y = CARD_SLOT_H - SETUP_CARD_BODY_TOP_INSET
        bottom_y = SETUP_CARD_BODY_BOTTOM_INSET

        step_x = body_x + SETUP_CARD_STEP_INDENT
        step_w = max(body_w - SETUP_CARD_STEP_INDENT, 40)
        for step in self._setup_steps:
            number = getattr(step, 'number', 0)
            text = getattr(step, 'text', '') or ''
            cursor_y = self._draw_setup_step(c, step_x, cursor_y, step_w, number, text)
            cursor_y -= SETUP_CARD_STEP_GAP
            if cursor_y < bottom_y:
                break

    def _draw_setup_step(self, c, x, top_y, w, number, text):
        marker = self._setup_marker_svgs.get(number)
        slot_w = SETUP_CARD_MARKER_SIZE
        marker_w = marker.width if marker is not None else SETUP_CARD_MARKER_SIZE
        marker_h = marker.height if marker is not None else SETUP_CARD_MARKER_SIZE

        text_x = x + slot_w + SETUP_CARD_MARKER_TEXT_GAP
        text_w = w - (slot_w + SETUP_CARD_MARKER_TEXT_GAP)
        if text_w < 30:
            text_w = 30

        markup = format_step_markup(text)
        para = Paragraph(markup, self.setup_step_style)
        _, _ = para.wrap(text_w, 9999)
        tighten_large_font_lines(para)
        para_h = para.height

        block_h = max(marker_h, para_h)
        block_bottom = top_y - block_h

        para_mid_y = top_y - para_h / 2
        marker_x = x + (slot_w - marker_w) / 2
        marker_y = top_y - marker_h
        marker_center_y = top_y - marker_h / 2

        if marker is not None:
            renderPDF.draw(marker, c, marker_x, marker_y)
        else:
            c.saveState()
            c.setFillColor(self.faction_color)
            c.circle(marker_x + marker_w / 2, marker_center_y, marker_w / 2, stroke=0, fill=1)
            c.setFont('Baskerville-Bold', marker_h * 0.6)
            c.setFillColorRGB(1, 1, 1)
            c.drawCentredString(marker_x + marker_w / 2, marker_center_y - marker_h * 0.2, str(number))
            c.restoreState()

        para.drawOn(c, text_x, top_y - para_h + SETUP_CARD_STEP_TEXT_Y_OFFSET)

        return block_bottom


def _resolve_image_path(attr):
    if not attr:
        return None
    path_val = getattr(attr, 'path', None)
    if path_val and os.path.exists(path_val):
        return path_val
    if isinstance(attr, str) and os.path.exists(attr):
        return attr
    return None


class ComponentsSheetLayoutEngine:
    """Letter-portrait print-out page combining the setup card, faction
    markers, and every individual building/token slot. Optionally emits
    interleaved back pages with mirrored x positions for duplex printing."""

    def __init__(self, faction, card_preview_path=None):
        self.faction = faction
        self.card = getattr(faction, 'setup_card', None)
        self.back = getattr(faction, 'faction_back', None)
        self.print_backs = bool(getattr(faction, 'print_component_backs', False))
        self.card_preview_path = card_preview_path
        self.faction_color = HexColor(faction.color or '#5B4A8A')

    def _build_grid_slots(self):
        """Slot order: VP marker, relationship marker, then each B/T piece
        repeated `quantity` times. The setup card is placed separately and
        is not part of the grid list."""
        slots = []
        vp_path = _resolve_image_path(getattr(self.faction, 'vp_marker', None))
        if vp_path:
            slots.append({'kind': 'marker', 'front': vp_path, 'back': vp_path})
        rel_path = _resolve_image_path(getattr(self.faction, 'relationship_marker', None))
        if rel_path:
            slots.append({'kind': 'marker', 'front': rel_path, 'back': rel_path})
        for piece in self.faction.pieces.filter(type__in=('B', 'T')).order_by('type', 'pk'):
            front = _resolve_image_path(getattr(piece, 'small_icon', None))
            back = _resolve_image_path(getattr(piece, 'back_image', None)) or front
            qty = getattr(piece, 'quantity', 1) or 1
            for _ in range(qty):
                slots.append({
                    'kind': 'piece',
                    'piece_type': piece.type,
                    'front': front,
                    'back': back,
                })
        return slots

    def _paginate(self, slots):
        """Return a list of page layouts. Each layout is a list of (slot, x, y).
        Page 0 also reserves the top-left card region; remaining pages use the
        full body for a uniform grid."""
        page_w = COMPONENTS_PAGE_W
        page_h = COMPONENTS_PAGE_H
        margin = COMPONENTS_PAGE_MARGIN
        gap = COMPONENTS_GRID_GAP
        cell = TRACK_SLOT_SIZE
        pitch = cell + gap

        card_x = margin
        card_y = page_h - margin - CARD_SLOT_H
        has_card = self.card is not None and self.card_preview_path is not None

        def grid_positions(x0, x1, y_top, y_bottom):
            """Yield (x, y) positions for cells in left-to-right, top-to-bottom
            order inside the rectangle [x0..x1] x [y_bottom..y_top]. Cells are
            anchored at their bottom-left."""
            width = x1 - x0
            height = y_top - y_bottom
            if width < cell or height < cell:
                return
            cols = int((width + gap) // pitch)
            rows = int((height + gap) // pitch)
            for r in range(rows):
                for c in range(cols):
                    yield (x0 + c * pitch, y_top - cell - r * pitch)

        pages = []
        idx = 0
        while idx < len(slots):
            page_items = []
            if not pages and has_card:
                # Region A: right of card, vertically aligned with card
                a_x0 = card_x + CARD_SLOT_W + gap
                a_x1 = page_w - margin
                a_top = card_y + CARD_SLOT_H
                a_bot = card_y
                positions = list(grid_positions(a_x0, a_x1, a_top, a_bot))
                # Region B: full body width below the card
                b_x0 = margin
                b_x1 = page_w - margin
                b_top = card_y - gap
                b_bot = margin
                positions += list(grid_positions(b_x0, b_x1, b_top, b_bot))
            else:
                # Continuation page (or page 1 with no card): full body
                c_x0 = margin
                c_x1 = page_w - margin
                c_top = page_h - margin
                c_bot = margin
                positions = list(grid_positions(c_x0, c_x1, c_top, c_bot))

            if not positions:
                break  # no room for cells — stop to avoid infinite loop

            for x, y in positions:
                if idx >= len(slots):
                    break
                page_items.append((slots[idx], x, y))
                idx += 1
            pages.append(page_items)

        # Edge case: only setup card, no grid slots — still need page 1.
        if not pages and has_card:
            pages.append([])

        return pages, has_card, card_x, card_y

    def _draw_card_with_rounded_corners(self, c, path, x, y):
        """Draw a 2.5"×3.46" card image clipped to a slightly-rounded rect."""
        try:
            radius = min(CARD_SLOT_W, CARD_SLOT_H) * 0.03
            c.saveState()
            p = c.beginPath()
            p.roundRect(x, y, CARD_SLOT_W, CARD_SLOT_H, radius)
            c.clipPath(p, stroke=0, fill=0)
            c.drawImage(path, x, y, width=CARD_SLOT_W, height=CARD_SLOT_H,
                        preserveAspectRatio=True, mask='auto')
            c.restoreState()
        except Exception:
            try:
                c.restoreState()
            except Exception:
                pass

    def _draw_image_in_cell(self, c, path, x, y, circular=False, piece_type=None):
        """Draw `path` centered inside a TRACK_SLOT_SIZE cell anchored at (x, y),
        preserving aspect ratio. Tokens (`circular=True`) clip to a circle;
        everything else clips to a 15%-radius rounded rect. When `path` is
        missing and `piece_type` is 'B' or 'T', render a solid faction-colored
        placeholder of the appropriate shape."""
        cell = TRACK_SLOT_SIZE
        if not path:
            if piece_type in ('B', 'T'):
                self._draw_piece_placeholder(c, x, y, cell, circular=circular)
            return
        from reportlab.lib.utils import ImageReader
        try:
            iw, ih = ImageReader(path).getSize()
        except Exception:
            if piece_type in ('B', 'T'):
                self._draw_piece_placeholder(c, x, y, cell, circular=circular)
            return
        if iw <= 0 or ih <= 0:
            if piece_type in ('B', 'T'):
                self._draw_piece_placeholder(c, x, y, cell, circular=circular)
            return
        scale = min(cell / iw, cell / ih)
        dw = iw * scale
        dh = ih * scale
        dx = x + (cell - dw) / 2
        dy = y + (cell - dh) / 2
        c.saveState()
        p = c.beginPath()
        if circular:
            side = min(dw, dh)
            p.circle(dx + dw / 2, dy + dh / 2, side / 2)
        else:
            radius = min(dw, dh) * 0.15
            p.roundRect(dx, dy, dw, dh, radius)
        c.clipPath(p, stroke=0, fill=0)
        c.drawImage(path, dx, dy, width=dw, height=dh,
                    preserveAspectRatio=True, mask='auto')
        c.restoreState()

    def _draw_piece_placeholder(self, c, x, y, cell, circular=False):
        c.saveState()
        c.setFillColor(self.faction_color)
        if circular:
            c.circle(x + cell / 2, y + cell / 2, cell / 2, stroke=0, fill=1)
        else:
            radius = cell * 0.15
            c.roundRect(x, y, cell, cell, radius, stroke=0, fill=1)
        c.restoreState()

    def build(self, output_path):
        slots = self._build_grid_slots()
        pages, has_card, card_x, card_y = self._paginate(slots)

        # If literally nothing to draw, still emit a blank page so the caller's
        # PDF concat doesn't break — but the view-side guard already prevents
        # this case in practice.
        if not pages:
            pages = [[]]

        c = rl_canvas.Canvas(output_path, pagesize=(COMPONENTS_PAGE_W, COMPONENTS_PAGE_H))
        page_w = COMPONENTS_PAGE_W

        adset_path = COMPONENTS_ADSET_STATIC if os.path.exists(COMPONENTS_ADSET_STATIC) else None

        for i, page_items in enumerate(pages):
            # Front page i
            if i == 0 and has_card:
                self._draw_card_with_rounded_corners(c, self.card_preview_path, card_x, card_y)
            for slot, x, y in page_items:
                piece_type = slot.get('piece_type')
                circular = piece_type == 'T'
                self._draw_image_in_cell(c, slot['front'], x, y, circular=circular, piece_type=piece_type)
            c.showPage()

            if not self.print_backs:
                continue

            # Back page i — mirrored horizontally
            if i == 0 and has_card and adset_path:
                xb = page_w - COMPONENTS_PAGE_MARGIN - CARD_SLOT_W
                self._draw_card_with_rounded_corners(c, adset_path, xb, card_y)
            for slot, x, y in page_items:
                xb = page_w - x - TRACK_SLOT_SIZE
                piece_type = slot.get('piece_type')
                circular = piece_type == 'T'
                self._draw_image_in_cell(c, slot['back'], xb, y, circular=circular, piece_type=piece_type)
            c.showPage()

        c.save()


# ---------- Forged custom-deck cards PDF ----------

class ForgedCardsLayoutEngine:
    """One uploaded card front per page, at the same physical dimensions as
    SetupCardLayoutEngine (poker card / mini-tarot size). Reads ForgedCard
    rows for every ForgedDeckGroup attached to a faction.
    """

    def __init__(self, faction):
        self.faction = faction

    def _iter_card_image_paths(self):
        from .models import ForgedDeckGroup
        groups = (
            ForgedDeckGroup.objects
            .filter(piece__faction=self.faction, piece__type='C')
            .order_by('piece__pk')
        )
        for group in groups:
            for card in group.cards.all().order_by('order'):
                img = card.front_image
                if not img:
                    continue
                path_val = getattr(img, 'path', None) or (img if isinstance(img, str) else None)
                if path_val and os.path.exists(path_val):
                    yield path_val

    def has_cards(self):
        return any(True for _ in self._iter_card_image_paths())

    def _draw_card(self, c, path):
        radius = min(CARD_SLOT_W, CARD_SLOT_H) * 0.03
        c.saveState()
        try:
            p = c.beginPath()
            p.roundRect(0, 0, CARD_SLOT_W, CARD_SLOT_H, radius)
            c.clipPath(p, stroke=0, fill=0)
            c.drawImage(
                path, 0, 0,
                width=CARD_SLOT_W, height=CARD_SLOT_H,
                preserveAspectRatio=True, mask='auto',
            )
        finally:
            c.restoreState()

    def build(self, output_path):
        c = rl_canvas.Canvas(output_path, pagesize=(CARD_SLOT_W, CARD_SLOT_H))
        any_drawn = False
        for path in self._iter_card_image_paths():
            self._draw_card(c, path)
            c.showPage()
            any_drawn = True
        if not any_drawn:
            # Reportlab requires at least one page; emit a blank one.
            c.showPage()
        c.save()
