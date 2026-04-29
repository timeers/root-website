# pdf_engine.py

import os
import re
import tempfile
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


def draw_faction_background(c, faction):
    """Fill the page with the faction's background.

    - No preset and no uploaded image  -> solid faction color fill.
    - Image + repeat_background_image  -> brick-tiled pattern.
    - Image + not repeating            -> cover-fill the page.
    """
    try:
        img_path = faction.get_background_path()
    except Exception:
        img_path = None

    if not img_path or not os.path.exists(img_path):
        color_hex = getattr(faction, 'color', None) or '#5B4A8A'
        c.saveState()
        c.setFillColor(HexColor(color_hex))
        c.rect(0, 0, PAGE_W, PAGE_H, stroke=0, fill=1)
        c.restoreState()
        return

    from reportlab.lib.utils import ImageReader
    img_reader = ImageReader(img_path)
    iw, ih = img_reader.getSize()

    if getattr(faction, 'repeat_background_image', False):
        max_h = PAGE_H / 3
        if ih > max_h:
            scale = max_h / ih
            draw_w = iw * scale
            draw_h = max_h
        else:
            draw_w = iw
            draw_h = ih
        row = 0
        y = PAGE_H - draw_h
        while y > -draw_h:
            x_offset = -(draw_w / 2) if row % 2 == 1 else 0
            x = x_offset
            while x < PAGE_W:
                c.drawImage(img_path, x, y, width=draw_w, height=draw_h)
                x += draw_w
            y -= draw_h
            row += 1
    else:
        scale = max(PAGE_W / iw, PAGE_H / ih)
        draw_w = iw * scale
        draw_h = ih * scale
        draw_x = (PAGE_W - draw_w) / 2
        draw_y = (PAGE_H - draw_h) / 2
        c.drawImage(img_path, draw_x, draw_y, width=draw_w, height=draw_h)


X_MARGIN = 0.25 * inch
TOP_MARGIN = 0.2 * inch
BOTTOM_MARGIN = 0.15 * inch

BODY_W = PAGE_W - (X_MARGIN * 2)

TITLE_BAR_H = 0.6 * inch
ABILITY_BAR_H = 0.9 * inch
ABILITY_GAP = 0.05 * inch
MIN_ABILITY_BOX_W = 1.5 * inch
MIN_FLAVOR_TEXT_W = 0.90 * inch
FLAVOR_TEXT_PADDING = 0.05 * inch
FLAVOR_TEXT_BASE_SIZE = 6
FLAVOR_TEXT_MAX_SIZE = 9

COLOR_BAR_W_RATIO = 0.95 # Controls width of content inside top bar in relation to top bar border
FACTION_TOP_BAR_NUDGE = 0.1 * inch
FACTION_NAME_Y_OFFSET = 0.15 * inch

PHASE_HEADER_H = 0.36 * inch
PHASE_INTERNAL_MARGIN = 0.17 * inch
PHASE_HEADER_MIN_W = 3.0 * inch           # minimum banner width for vertical layout
PHASE_HEADER_MIN_H = 0.35 * inch          # minimum banner height for vertical layout
PHASE_HEADER_LOCK_W = 4.25 * inch         # width at which banner height stops scaling

# Phase box background layout
PHASE_BOX_V_GAP = 0.01 * inch             # spacing between stacked phases in vertical box
PHASE_BOX_PAD_TOP = 0.12 * inch          # padding above phase content in phase box
PHASE_BOX_PAD_BOTTOM = 0.06 * inch     # padding below phase content in phase box

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

# Track slot rendering
TRACK_SLOT_SIZE = 0.67 * inch
TRACK_SLOT_GAP = 0.06 * inch
TRACK_ROW_TITLE_W = 0.75 * inch
TRACK_ROW_TITLE_VERTICAL_W = 0.22 * inch  # narrower column for vertically rotated row titles
TRACK_COL_HEADER_H = 0.30 * inch # Controls the header height for below and above track
TRACK_TITLE_SIZE = 11
TRACK_TITLE_GAP = 4
TRACK_BODY_GAP = 4
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

# Card-Slot.png is 63.5mm x 88mm (standard poker card size)
CARD_SLOT_W = 2.5 * inch
CARD_SLOT_H = 3.46 * inch

DECREE_MIN_OFFSET = 1.05 * inch  # minimum amount of decree visible on page
DECREE_MAX_OFFSET = 4.25 * inch    # maximum amount of decree visible on page
DECREE_TEXT_THRESHOLD = 30        # char count for small vs large text
DECREE_TITLE_Y_OFFSET = 4.3 * inch

# Individual decree slot image render size (565:793 aspect ratio)
DECREE_SLOT_W = 2.5 * inch
DECREE_SLOT_H = DECREE_SLOT_W * (793 / 565)
DECREE_SLOT_Y_OFFSET = 3.9 * inch  # distance from top of decree image to slot tops
DECREE_SLOT_MIN_GAP = 0.01 * inch  # minimum spacing between/around slots
DECREE_SLOT_TITLE_OFFSET = 3.3 * inch  # distance from top of slot image to title text

# Card pile layout (bottom-right stacks of cards)
CARD_PILE_TITLE_TOP_OFFSET = 0.4 * inch        # top of title text from top of card image (when pile fits fully on page)
CARD_PILE_TITLE_TOP_OFFSET_OVERFLOW = 0.10 * inch  # tighter offset when pile overflows off the page
CARD_PILE_TITLE_TO_BODY_GAP = 0.0 * inch       # vertical gap between bottom of title and top of body
CARD_PILE_PADDING = 0.18 * inch                 # horizontal text padding inside card
CARD_PILE_GAP = 0.20 * inch                     # horizontal margin between adjacent card piles
CARD_PILE_TITLE_SIZE = 20                       # title font size (Luminari)

# Image height for inline images like card draw and VP
INLINE_IMG_H = 14.5

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
ATTR_BAR_LEVEL_LABELS = {
    'N': 'NONE',
    'L': 'LOW',
    'M': 'MODERATE',
    'H': 'HIGH',
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
HOWTOPLAY_IMAGE_MAX_W = 2.25 * inch        # optional FactionBack image: max width
HOWTOPLAY_IMAGE_MAX_H = 3.5 * inch         # optional FactionBack image: max height
HOWTOPLAY_IMAGE_GAP = 0.10 * inch          # horizontal gap between text and the image

BACK_X_MARGIN = 0.7 * inch             # left/right page margin for the FactionBack
BACK_TOP_MARGIN = 0.75 * inch             # top page margin for the FactionBack
BACK_BOTTOM_MARGIN = 0.15 * inch         # bottom page margin for the FactionBack
BACK_BG_SCREEN_OPACITY = 0.70            # white screen opacity applied over the background to lighten it
BACK_COLUMN_GAP = 0.25 * inch
LEFT_COL_W_RATIO = 0.48

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

STATIC_DIR = os.path.join(os.path.dirname(__file__), '..', 'the_keep', 'static')

ABILITY_BERRY_SVG = os.path.join(STATIC_DIR, 'pdf/svg/ability_berry.svg')
PHASE_NUMBER_SVG_DIR = os.path.join(STATIC_DIR, 'pdf/svg')
FACTION_TOP_BAR_SVG = os.path.join(STATIC_DIR, 'pdf/boxes/Faction_Top_Bar.svg')
CRAFTED_ITEMS_SVG = os.path.join(STATIC_DIR, 'pdf/boxes/Crafted_Items_Box.svg')
CARD_SLOT_IMG = os.path.join(STATIC_DIR, 'pdf/images/Card-Slot.png')
CARD_PILE_SVG = os.path.join(STATIC_DIR, 'pdf/svg/card_pile.svg')
TOKEN_SLOT_IMG = os.path.join(STATIC_DIR, 'pdf/images/TokenSlot.png')
BUILDING_SLOT_IMG = os.path.join(STATIC_DIR, 'pdf/images/BuildingSlot.png')
DECREE_DIR = os.path.join(STATIC_DIR, 'pdf/decree')
PHASE_BOX_SVG = os.path.join(STATIC_DIR, 'pdf/boxes/Phase_Box.svg')
PHASE_BOX_TAN = '#f9e3b3'                 # tan fill color inside Phase_Box.svg
MEEPLE_SVG = os.path.join(STATIC_DIR, 'pdf/svg/meeple.svg')

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

PHASE_DISPLAY_NAMES = {
    'birdsong': 'Birdsong',
    'daylight': 'Daylight',
    'evening': 'Evening',
}


def _resolve_static_url(url):
    """Resolve a `static()` URL back to a filesystem path for ReportLab."""
    if not url:
        return None
    from django.contrib.staticfiles import finders
    rel = url.split('/static/', 1)[-1]
    return finders.find(rel)


def _inline_image_path(keyword):
    """Resolve an inline-image keyword (from FORGE_INLINE_IMAGES) to a path."""
    from .inline_images import FORGE_INLINE_IMAGES
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
    # Action (default faction icon)
    'action': static('pdf/inline/faction-lord of the hundreds.png'),
}


def _replace_inline_images(text, img_height=None):
    """Replace {{ keyword }} with ReportLab <img> tags.

    Expects already XML-escaped input. Unknown keywords are left as-is.
    """
    h = img_height or INLINE_IMG_H

    def image_replacer(match):
        keyword = match.group(1).strip()
        img_path = _inline_image_path(keyword)
        if not img_path or not os.path.exists(img_path):
            return match.group(0)
        from PIL import Image as PILImage
        pil_img = PILImage.open(img_path)
        iw, ih = pil_img.size
        aspect = iw / ih
        img_w = h * aspect
        return f'<img src="{img_path}" width="{img_w:.1f}" height="{h}" valign="middle"/>'

    return re.sub(r"\{\{\s*([^}]+?)\s*\}\}", image_replacer, text)


def format_step_markup(text):
    """Convert semi-markdown to ReportLab Paragraph XML.

    ##text## -> Baskerville 15pt (title size)
    ~~text~~ -> Luminari (decorative font)
    **text** -> bold
    _text_   -> italic
    {{ key }} -> inline image (safe fallback if no match)
    """
    if not text:
        return ""

    # Escape XML special chars in the raw input
    result = text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')

    # {{ keyword }} -> inline image (aspect-ratio preserved, fixed height)
    result = _replace_inline_images(result)

    # ##text## -> title-size font
    result = re.sub(r"##(.+?)##", r'<font name="Baskerville" size="15">\1</font>', result)

    # ~~text~~ -> Luminari (decorative)
    result = re.sub(r"~~(.+?)~~", r'<font name="Luminari">\1</font>', result)

    # Pre-pass: when two styled spans abut (no whitespace between), the
    # serializer emits sequences like `**__` / `__**` / `__` between
    # alphanumerics where one `_` is the close marker of the previous span
    # and one is the open marker of the next. Insert a ZWSP between them
    # so the regex engine sees clean boundaries; ZWSP is stripped at the
    # end so it doesn't render.
    result = re.sub(r"\*\*__", "**_\u200B_", result)
    result = re.sub(r"__\*\*", "_\u200B_**", result)
    result = re.sub(r"(?<=[A-Za-z0-9])__(?=[A-Za-z0-9])", "_\u200B_", result)

    # _**text**_ or **_text_** -> bold italic (must run before the individual
    # ** and _ rules so the combined markers aren't consumed first).
    # Boundary `(?<!_)…(?!_)` rejects ambiguous adjacent `_` (handled by the
    # pre-pass above) without blocking alphanumeric neighbors.
    result = re.sub(r"(?<!_)_\*\*(.+?)\*\*_(?!_)", r"<b><i>\1</i></b>", result)
    result = re.sub(r"\*\*_(.+?)_\*\*", r"<b><i>\1</i></b>", result)

    # **text** -> bold
    result = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", result)

    # _text_ -> italic
    result = re.sub(r"(?<!_)_(.+?)_(?!_)", r"<i>\1</i>", result)

    # Strip ZWSP separators inserted in the pre-pass.
    result = result.replace("\u200B", "")

    # Newlines -> line breaks
    result = result.replace('\n', '<br/>')

    return result


def format_inline_images(text):
    """Replace {{ keyword }} with inline images only — no other markup."""
    if not text:
        return ""
    result = text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
    result = _replace_inline_images(result)
    result = result.replace('\n', '<br/>')
    return result


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

    def __init__(self, title, body_markup, total_width, box_height, body_style):
        super().__init__()
        self._width = total_width
        self._height = box_height
        self.title = title
        self.body_markup = body_markup
        self.body_style = body_style

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

        # --- Draw border ---
        # Lines are extended by half_bw at each end so the stroke overlaps
        # at corners, producing clean mitered joints.
        c.setStrokeColorRGB(0, 0, 0)
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
        c.setFillColorRGB(0, 0, 0)
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


class TrackFlowable(Flowable):
    """Renders a CardboardTrack grid inline within phase step content.

    Supports column headers with cost icons, row titles, section dividers,
    and per-slot content images with background fills.
    """

    def __init__(self, track, slots, total_width, body_style, faction_color):
        super().__init__()
        self.track = track
        self.total_width = total_width
        self.body_style = body_style
        self.faction_color = faction_color

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
        self.has_headers = bool(track.column_headers)

        # Row titles now live on the track as a pipe-delimited string.
        self.row_titles = {}
        raw_row_titles = getattr(track, 'row_titles', '') or ''
        if raw_row_titles:
            for idx, title in enumerate(raw_row_titles.split('|')):
                if title:
                    self.row_titles[idx] = title
        self.has_row_titles = bool(self.row_titles) or bool(getattr(track, 'header_title', ''))

        # Parse dividers: set of column indices
        self.dividers = set()
        if track.column_dividers:
            for col_str in track.column_dividers.split(','):
                col_str = col_str.strip()
                if col_str:
                    self.dividers.add(int(col_str))

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
            body_markup = format_step_markup(self.track.body)
            para = Paragraph(body_markup, centered)
            _, self._body_h = para.wrap(self.total_width, 9999)
            self._body_h += TRACK_BODY_GAP

        # Header height
        self._header_h = TRACK_COL_HEADER_H if self.has_headers else 0

        # Row title width
        self._vertical_row_titles = (getattr(self.track, 'row_title_orientation', 'horizontal') == 'vertical')
        if self.has_row_titles:
            self._row_title_w = TRACK_ROW_TITLE_VERTICAL_W if self._vertical_row_titles else TRACK_ROW_TITLE_W
        else:
            self._row_title_w = 0

        # Slot size — fixed at design size
        self._slot_size = TRACK_SLOT_SIZE
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

        self._width = self.total_width
        self._height = self._title_h + self._body_h + self._header_h + self._grid_h

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
        grid_center_x = self._row_title_w + self._grid_w / 2
        c.drawCentredString(grid_center_x, cursor_y, self.track.title)
        cursor_y -= TRACK_TITLE_GAP

        # --- Body text (centered, if present) ---
        if self.track.body and self._body_h > 0:
            from reportlab.lib.enums import TA_CENTER
            from reportlab.lib.styles import ParagraphStyle
            centered = ParagraphStyle('TrackBody', parent=self.body_style, alignment=TA_CENTER)
            body_markup = format_step_markup(self.track.body)
            para = Paragraph(body_markup, centered)
            para_w, para_h = para.wrap(self._width, 9999)
            para.drawOn(c, 0, cursor_y - para_h)
            cursor_y -= para_h + TRACK_BODY_GAP

        # --- Column headers (above) ---
        headers_above = self.has_headers and getattr(self.track, 'header_position', 'above') == 'above'
        if headers_above:
            self._draw_column_headers(c, cursor_y)
            cursor_y -= self._header_h

        # Top of the slot grid
        grid_top_y = cursor_y
        headers_below = self.has_headers and getattr(self.track, 'header_position', 'above') == 'below'

        # --- Section dividers ---
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
                    title_markup = format_step_markup(title)
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
                    title_markup = format_step_markup(title)
                    para = Paragraph(title_markup, row_title_style)
                    para_w, para_h = para.wrap(self._row_title_w - 4, 9999)
                    para_y = slot_y + self._slot_size / 2 - para_h / 2
                    para.drawOn(c, 0, para_y)

        # --- Slots ---
        for row_idx in range(self.num_rows):
            for col_idx in range(self.num_cols):
                slot = self.grid.get(row_idx, {}).get(col_idx)
                x = self._col_x(col_idx)
                y = self._row_y(row_idx, grid_top_y) - self._col_y_offset(col_idx)
                self._draw_slot(c, x, y, slot)

        # --- Column headers (below) ---
        if headers_below:
            below_y = grid_top_y - self._grid_h - TRACK_HEADER_BELOW_PAD
            self._draw_column_headers(c, below_y)

        c.restoreState()

    def _draw_column_headers(self, c, top_y):
        """Draw column headers starting from top_y downward."""
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.lib.enums import TA_CENTER, TA_RIGHT

        # Draw header title in row-title column area
        header_title = getattr(self.track, 'header_title', '') or ''
        if header_title and self._row_title_w > 0:
            title_markup = format_step_markup(header_title)
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

        headers = self.track.column_headers.split('|')

        for col_idx in range(self.num_cols):
            x = self._col_x(col_idx)
            slot_center_x = x + self._slot_size / 2
            label = headers[col_idx] if col_idx < len(headers) else ''

            processed = _replace_inline_images(label, img_height=TRACK_HEADER_ICON_H)
            if '<img' in processed:
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
                label_y = top_y - self._header_h / 2 - TRACK_HEADER_FONT_SIZE * 0.35
                c.drawCentredString(slot_center_x, label_y, label)

    def _draw_slot(self, c, x, y, slot):
        """Draw a single slot at position (x, y) bottom-left."""
        s = self._slot_size
        is_token = self.track.type == 'token'

        # --- Background fill ---
        c.saveState()
        bg_drawn = False

        # Try slot background image
        if slot and slot.background_image:
            try:
                self._draw_bg_image(c, slot.background_image.path, x, y, s, is_token)
                bg_drawn = True
            except (ValueError, FileNotFoundError):
                pass

        # Try track background image
        if not bg_drawn and self.track.background_image:
            try:
                self._draw_bg_image(c, self.track.background_image.path, x, y, s, is_token)
                bg_drawn = True
            except (ValueError, FileNotFoundError):
                pass

        # Fallback: faction color at reduced opacity
        if not bg_drawn:
            c.setFillColor(self.faction_color)
            c.setFillAlpha(TRACK_SLOT_BG_OPACITY)
            if is_token:
                c.circle(x + s / 2, y + s / 2, s / 2, fill=1, stroke=0)
            else:
                c.roundRect(x, y, s, s, s * 0.15, fill=1, stroke=0)

        c.restoreState()

        # --- Content images ---
        if slot and slot.content:
            keywords = [k.strip() for k in slot.content.split('|') if k.strip()]
            images = []
            for kw in keywords:
                img_path = _inline_image_path(kw)
                if img_path and os.path.exists(img_path):
                    images.append(img_path)
            if images:
                self._draw_content_images(c, x, y, s, images, is_token)

    def _draw_bg_image(self, c, img_path, x, y, s, is_token):
        """Draw a background image at reduced opacity, clipped to slot shape."""
        c.saveState()
        # Clip to slot shape
        p = c.beginPath()
        if is_token:
            cx, cy = x + s / 2, y + s / 2
            p.circle(cx, cy, s / 2)
        else:
            p.roundRect(x, y, s, s, s * 0.15)
        c.clipPath(p, stroke=0, fill=0)
        c.setFillAlpha(TRACK_SLOT_BG_OPACITY)
        c.drawImage(img_path, x, y, width=s, height=s, mask='auto',
                    preserveAspectRatio=True, anchor='c')
        c.restoreState()

    def _draw_content_images(self, c, x, y, s, images, is_token):
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
            pil_img = PILImage.open(img_path)
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


def _measure_action_widths(action, icon_w, icon_h, icon_path, cost, action_body_style):
    """Measure natural and wrap-once widths/heights for a StepAction.

    Returns (natural_w, natural_h, wrap_once_w, wrap_once_h) where:
      natural_w/h  = dimensions for single-line rendering (no text wrapping)
      wrap_once_w/h = dimensions allowing one extra line of wrapping (max 3 lines total)
      wrap_once_w is None if wrapping would exceed 3 lines
    """
    # Icon space
    if cost.startswith('card_'):
        icon_space = MIN_CARD_ICON_W if icon_path else 0
    else:
        icon_space = icon_w if icon_path else 0

    arrow_w = _arrow_total_width_for_cost(cost)
    fixed_w = icon_space + arrow_w  # non-text portion
    icon_h_eff = icon_h if icon_path else 0

    # Measure text natural width by wrapping at a huge width
    markup = format_step_markup(action.text)
    PROBE_W = 10000
    para = Paragraph(markup, action_body_style)
    para.wrap(PROBE_W, 9999)

    text_natural_w = 0
    if hasattr(para, 'blPara') and hasattr(para.blPara, 'lines') and para.blPara.lines:
        for line in para.blPara.lines:
            # FragLine stores maxWidth and extraSpace; used width = maxWidth - extraSpace
            max_w = getattr(line, 'maxWidth', 0)
            extra = getattr(line, 'extraSpace', 0)
            if max_w:
                lw = max_w - extra
            else:
                lw = getattr(line, 'currentWidth', 0)
            if lw > text_natural_w:
                text_natural_w = lw

    natural_w = fixed_w + text_natural_w + 2  # 2pt buffer
    natural_text_h = true_paragraph_height(para, text_natural_w)
    natural_h = max(natural_text_h, icon_h_eff)

    # Determine baseline line count at natural width
    baseline_lines = len(para.blPara.lines) if hasattr(para, 'blPara') and hasattr(para.blPara, 'lines') else 1

    # Only allow wrapping if result would be <= 3 lines
    target_lines = baseline_lines + 1
    if target_lines > 3:
        return natural_w, natural_h, None, None

    # Binary search for wrap-once width: narrowest width that keeps lines <= target
    min_text_w = para.minWidth() if hasattr(para, 'minWidth') else text_natural_w * 0.3
    lo, hi = min_text_w, text_natural_w

    for _ in range(12):
        mid = (lo + hi) / 2
        probe = Paragraph(markup, action_body_style)
        probe.wrap(mid, 9999)
        n_lines = len(probe.blPara.lines) if hasattr(probe, 'blPara') and hasattr(probe.blPara, 'lines') else 1
        if n_lines <= target_lines:
            hi = mid
        else:
            lo = mid

    wrap_once_w = fixed_w + hi + 2  # 2pt buffer
    # Measure actual height at wrap-once width
    probe = Paragraph(markup, action_body_style)
    probe.wrap(hi + 2, 9999)
    wrap_once_text_h = true_paragraph_height(probe, hi + 2)
    wrap_once_h = max(wrap_once_text_h, icon_h_eff)

    return natural_w, natural_h, wrap_once_w, wrap_once_h


class StepActionFlowable(Flowable):
    """Renders a single StepAction row: [cost_icon] → [action_text]"""

    def __init__(self, icon_path, text_paragraph, icon_w, icon_h, total_width, cost):
        super().__init__()
        self.icon_path = icon_path
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
        # Calculate text area width
        # For card costs, use MIN_CARD_ICON_W as the icon column width so all
        # card actions share the same text x-position regardless of icon width.
        # Wider icons overflow left into the margin.
        if self.is_card:
            icon_space = MIN_CARD_ICON_W if icon_path else 0
        else:
            icon_space = self.icon_w if icon_path else 0
        self.icon_space = icon_space
        self.text_w = total_width - icon_space - self.total_arrow_w
        _, self.wrap_h = text_paragraph.wrap(self.text_w, 9999)
        self.true_h = true_paragraph_height(text_paragraph, self.text_w)
        self.wrap_h = text_paragraph.height  # updated by tightening in true_paragraph_height

        # Determine first line height for icon/arrow vertical alignment
        self.first_line_h = self._get_first_line_height(text_paragraph)

        # If the icon is taller than the first line, we need extra padding above
        # so the text drops down to align its first line center with the icon center
        icon_h_eff = self.icon_h if icon_path else 0
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

        # Draw cost icon (centered on first line)
        if self.icon_path:
            if self.is_card:
                # Center-align card icon on MIN_CARD_ICON_W midpoint;
                # wider icons extend left (negative x) into margin
                center_x = MIN_CARD_ICON_W / 2
                icon_x = center_x - self.icon_w / 2
            else:
                icon_x = (icon_space - self.icon_w) / 2
            icon_y = first_line_center_y - self.icon_h / 2
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

        # DEBUG: red border around action
        c.setStrokeColor(HexColor('#FF0000'))
        c.setLineWidth(0.5)
        c.rect(0, 0, self.total_width, self._height)

        c.restoreState()


class CardGroupFlowable(Flowable):
    """Renders a group of card actions sharing the same cost icon.

    One icon on the left with bracket-style arrows branching to each action's text.
    """

    def __init__(self, icon_path, text_paragraphs, icon_w, icon_h, total_width, cost):
        super().__init__()
        self.icon_path = icon_path
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
        if self.icon_path:
            center_x = MIN_CARD_ICON_W / 2
            icon_x = center_x - self.icon_w / 2
            icon_y = icon_center_y - self.icon_h / 2
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
            # DEBUG: red border around individual action text
            c.setStrokeColor(HexColor('#FF0000'))
            c.setLineWidth(0.5)
            c.rect(text_x, text_draw_positions[i], self.text_w, self.wrap_heights[i])

        # DEBUG: red border around group
        c.setStrokeColor(HexColor('#FF0000'))
        c.setLineWidth(0.5)
        c.rect(0, 0, self.total_width, self._height)

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

        # decree_slide = how far the decree image slides down onto the page
        # (0 = fully hidden above page, draw_h = fully visible)
        self.decree_slide = DECREE_MIN_OFFSET if self.decree_section else 0.0

        # Faction Top Bar image top edge starts at TOP_MARGIN (pushed down by decree)
        self.faction_top_bar_top = PAGE_H - TOP_MARGIN - self.decree_slide
        self.faction_top_bar_w = PAGE_W - 0.7 * inch
        self.faction_top_bar_h = self.faction_top_bar_w * (370 / 2106)

        # Color bar: same top-down calculation as the SVG, then nudge below it
        self.title_bar_y = PAGE_H - self.decree_slide - TOP_MARGIN - FACTION_TOP_BAR_NUDGE - TITLE_BAR_H

        # Phase area: top is below the Faction Top Bar image, bottom is at margin
        self.phases_top_y = self.faction_top_bar_top - self.faction_top_bar_h
        self.phases_bottom_y = BOTTOM_MARGIN

        self._placed_boxes = []
        self._phases_rect = None

        self._init_styles()
        # Step numbers and ability berries sit on the Phase_Box.svg tan fill;
        # if the faction color fails contrast against that tan, use black.
        faction_color_hex = self.sheet.faction.color or '#5B4A8A'
        on_tan_hex = (faction_color_hex
                      if _is_color_legible_on(faction_color_hex, PHASE_BOX_TAN)
                      else '#000000')
        self._ability_icon = self._load_colored_svg(ABILITY_BERRY_SVG, on_tan_hex, 0.5 * inch)

        # Preload numbered SVGs (0-9) for phase steps, at natural size
        self._phase_number_svgs = {}
        for n in range(10):
            svg_path = os.path.join(PHASE_NUMBER_SVG_DIR, f'{n}.svg')
            if os.path.exists(svg_path):
                self._phase_number_svgs[n] = self._load_colored_svg(svg_path, on_tan_hex)

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

    def _load_phase_box_svg(self, target_w, target_h):
        """Load Phase_Box.svg stretched to target_w x target_h (non-uniform scaling)."""
        drawing = svg2rlg(PHASE_BOX_SVG)
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

    def _draw_phase_box(self, c, x, y, target_w, target_h, rotated=False):
        """
        Draw Phase_Box.svg background at (x, y) bottom-left with given dimensions.
        If rotated=True, the SVG is rotated 90° clockwise (for horizontal layout).
        """
        if not rotated:
            drawing = self._load_phase_box_svg(target_w, target_h)
            if drawing:
                renderPDF.draw(drawing, c, x, y)
        else:
            # Load in portrait orientation (swap w/h), then rotate on canvas
            drawing = self._load_phase_box_svg(target_h, target_w)
            if drawing:
                c.saveState()
                c.translate(x, y + target_h)
                c.rotate(-90)
                renderPDF.draw(drawing, c, 0, 0)
                c.restoreState()
        # DEBUG: red border around phase box
        c.saveState()
        c.setStrokeColor(HexColor('#FF0000'))
        c.setLineWidth(0.5)
        c.rect(x, y, target_w, target_h, fill=0, stroke=1)
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
        """Resolve icon path and draw dimensions for a StepAction's cost type.

        Returns (icon_path, draw_w, draw_h) or (None, 0, 0) if no icon.
        """
        from PIL import Image as PILImage

        cost = action.cost
        if cost == 'other':
            icon_path = action.cost_image.path if action.cost_image else None
        else:
            icon_path = _cost_icon_path(cost)

        if not icon_path or not os.path.exists(icon_path):
            return None, 0, 0

        pil_img = PILImage.open(icon_path)
        iw, ih = pil_img.size
        aspect = iw / ih

        if cost.startswith('item_'):
            # Fix height, scale width
            draw_h = ACTION_ITEM_H
            draw_w = draw_h * aspect
        elif cost in ('card_fox', 'card_mouse', 'card_rabbit', 'card_bird'):
            # Fix height, scale width
            draw_h = ACTION_CARD_H
            draw_w = draw_h * aspect
        elif cost == 'card_nonbird':
            # Fix width, scale height
            draw_w = ACTION_CARDS_W
            draw_h = draw_w / aspect
        else:
            # action, other — fix width, scale height
            draw_w = ACTION_DEFAULT_W
            draw_h = draw_w / aspect

        return icon_path, draw_w, draw_h

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
        # pending entries: (action, nat_w, nat_h, wrap_w_or_None, wrap_h_or_None)
        pending = []

        def _flush_pending():
            if not pending:
                return

            # --- Pass 1: greedily pack using natural (no-wrap) widths ---
            # Actions that fit on one row without wrapping, up to MAX_ACTIONS_PER_ROW.
            packed = set()
            result_slots = []  # (position_key, row_descriptor)

            pass1_groups = []  # each entry: [index, ...]
            current_indices = []
            current_w = 0

            for i, (action, nat_w, nat_h, wrap_w, wrap_h) in enumerate(pending):
                needed = nat_w + (SIDE_BY_SIDE_GAP if current_indices else 0)
                if not current_indices or (current_w + needed <= avail_action_w
                                           and len(current_indices) < MAX_ACTIONS_PER_ROW):
                    current_indices.append(i)
                    current_w += needed
                else:
                    pass1_groups.append(current_indices)
                    current_indices = [i]
                    current_w = nat_w
            if current_indices:
                pass1_groups.append(current_indices)

            # Only commit groups with 2+ actions
            for group in pass1_groups:
                if len(group) > 1:
                    row_items = [(pending[k][0], pending[k][1]) for k in group]
                    result_slots.append((group[0], ('row', row_items)))
                    packed.update(group)

            # --- Pass 2: try wrapping to pair consecutive unpacked actions (max 2 per row) ---
            leftovers = [(i, pending[i]) for i in range(len(pending)) if i not in packed]
            j = 0
            while j < len(leftovers):
                idx_a, (action_a, nat_w_a, nat_h_a, wrap_w_a, wrap_h_a) = leftovers[j]
                paired = False

                if j + 1 < len(leftovers):
                    idx_b, (action_b, nat_w_b, nat_h_b, wrap_w_b, wrap_h_b) = leftovers[j + 1]

                    # Use wrap widths where available, natural otherwise
                    w_a = wrap_w_a if wrap_w_a is not None else nat_w_a
                    h_a = wrap_h_a if wrap_w_a is not None else nat_h_a
                    w_b = wrap_w_b if wrap_w_b is not None else nat_w_b
                    h_b = wrap_h_b if wrap_w_b is not None else nat_h_b

                    pair_w = w_a + SIDE_BY_SIDE_GAP + w_b
                    if pair_w <= avail_action_w:
                        # Height check: side-by-side must be shorter than stacking
                        side_by_side_h = max(h_a, h_b)
                        stacked_h = nat_h_a + nat_h_b + ACTION_ROW_GAP

                        if side_by_side_h < stacked_h:
                            result_slots.append((idx_a, ('row', [(action_a, w_a), (action_b, w_b)])))
                            j += 2
                            paired = True

                if not paired:
                    result_slots.append((idx_a, ('row', [(action_a, nat_w_a)])))
                    j += 1

            # Sort by original position to preserve action order
            result_slots.sort(key=lambda s: s[0])
            for _, row_desc in result_slots:
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
                icon_path, icon_w, icon_h = self._resolve_cost_icon(action)
                nat_w, nat_h, wrap_w, wrap_h = _measure_action_widths(
                    action, icon_w, icon_h, icon_path, action.cost, self.action_body_style
                )
                pending.append((action, nat_w, nat_h, wrap_w, wrap_h))

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

        for row_info in packed_rows:
            if row_info[0] == 'single_card':
                action = row_info[1]
                icon_path, icon_w, icon_h = self._resolve_cost_icon(action)
                markup = format_step_markup(action.text)
                para = Paragraph(markup, self.action_body_style)
                flowable = StepActionFlowable(
                    icon_path=icon_path, text_paragraph=para,
                    icon_w=icon_w, icon_h=icon_h,
                    total_width=action_w, cost=action.cost,
                )

            elif row_info[0] == 'card_group':
                cost_type = row_info[1]
                group_actions = row_info[2]
                icon_path, icon_w, icon_h = self._resolve_cost_icon(group_actions[0])
                paragraphs = []
                for action in group_actions:
                    markup = format_step_markup(action.text)
                    para = Paragraph(markup, self.action_body_style)
                    paragraphs.append(para)
                flowable = CardGroupFlowable(
                    icon_path=icon_path, text_paragraphs=paragraphs,
                    icon_w=icon_w, icon_h=icon_h,
                    total_width=action_w, cost=cost_type,
                )

            elif row_info[0] == 'row':
                action_items = row_info[1]  # [(action, alloc_w), ...]

                if len(action_items) == 1:
                    # Single non-card action — full width
                    action, _ = action_items[0]
                    icon_path, icon_w, icon_h = self._resolve_cost_icon(action)
                    markup = format_step_markup(action.text)
                    para = Paragraph(markup, self.action_body_style)
                    flowable = StepActionFlowable(
                        icon_path=icon_path, text_paragraph=para,
                        icon_w=icon_w, icon_h=icon_h,
                        total_width=action_w, cost=action.cost,
                    )
                else:
                    # Multiple side-by-side actions — each gets its measured width,
                    # last action expands to fill remaining row space
                    total_gaps = SIDE_BY_SIDE_GAP * (len(action_items) - 1)
                    used_w = sum(w for _, w in action_items[:-1]) + total_gaps
                    last_w = action_w - used_w  # remaining space for last action

                    sub_flowables = []
                    col_widths = []
                    for idx, (action, base_w) in enumerate(action_items):
                        alloc_w = last_w if idx == len(action_items) - 1 else base_w
                        icon_path, icon_w, icon_h = self._resolve_cost_icon(action)
                        markup = format_step_markup(action.text)
                        para = Paragraph(markup, self.action_body_style)
                        af = StepActionFlowable(
                            icon_path=icon_path, text_paragraph=para,
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
                    flowable.setStyle(TableStyle([
                        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                        ('LEFTPADDING', (0, 0), (-1, -1), 0),
                        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
                        ('TOPPADDING', (0, 0), (-1, -1), 0),
                        ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
                    ]))

            if indent:
                # Wrap in a table to apply the left indent
                t = Table([['', flowable]], colWidths=[indent, action_w])
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
            total += self.measure_step_height(step, width, single_step=single_step)
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
            markup = format_step_markup(content_box.text)
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

    def _content_box_fits(self, x, y, w, h):
        """Check if a content box at (x, y) with dimensions (w, h) fits on the page."""
        if x < X_MARGIN or x + w > PAGE_W - X_MARGIN:
            return False
        if y < BOTTOM_MARGIN or y + h > self.phases_top_y:
            return False
        # Check overlap with phases box
        if hasattr(self, '_phases_rect'):
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

                # Parse dividers
                dividers = set()
                if track.column_dividers:
                    for col_str in track.column_dividers.split(','):
                        col_str = col_str.strip()
                        if col_str:
                            dividers.add(int(col_str))
                total_dividers = len(dividers)

                # Row title width
                has_row_titles = any(t.strip() for t in (track.row_titles or '').split('|'))
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
                    # Building track: needs full natural width
                    divider_space = total_dividers * TRACK_DIVIDER_W
                    natural_grid_w = (num_cols * TRACK_SLOT_SIZE
                                      + (num_cols - 1) * TRACK_SLOT_GAP
                                      + divider_space)
                    track_w = natural_grid_w + row_title_w + indent

                if track_w > max_needed:
                    max_needed = track_w

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

                # Parse dividers
                dividers = set()
                if track.column_dividers:
                    for col_str in track.column_dividers.split(','):
                        col_str = col_str.strip()
                        if col_str:
                            dividers.add(int(col_str))
                total_dividers = len(dividers)

                # Row title width
                has_row_titles = any(t.strip() for t in (track.row_titles or '').split('|'))
                if getattr(track, 'header_title', '') and track.header_title:
                    has_row_titles = True
                vertical_titles = getattr(track, 'row_title_orientation', 'horizontal') == 'vertical'
                if has_row_titles:
                    row_title_w = TRACK_ROW_TITLE_VERTICAL_W if vertical_titles else TRACK_ROW_TITLE_W
                else:
                    row_title_w = 0

                divider_space = total_dividers * TRACK_DIVIDER_W
                natural_grid_w = (num_cols * TRACK_SLOT_SIZE
                                  + (num_cols - 1) * TRACK_SLOT_GAP
                                  + divider_space)
                track_w = natural_grid_w + row_title_w + indent

                if track_w > max_needed:
                    max_needed = track_w

        return max_needed

    def _preferred_track_width_for_content_box(self, content_box):
        """Return the natural (non-overlapped) content width needed for all tracks in a content box."""
        SINGLE_STEP_INDENT = PHASE_INTERNAL_MARGIN
        steps = list(content_box.steps.all())
        single_step = len(steps) == 1
        indent = SINGLE_STEP_INDENT if single_step else (0.325 * inch + 0.015 * inch)
        return self._natural_track_width_for_steps(steps, indent)

    def _natural_text_width_for_steps(self, steps, indent):
        """Return the longest natural (single-line) width across all step body paragraphs."""
        PROBE_W = 10000
        max_needed = 0
        for step in steps:
            markup = format_step_markup(step.text)
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

    def measure_step_height(self, step, width, single_step=False, body_style=None):
        from reportlab.platypus import Table, TableStyle
        ICON_COL_W = 0.325 * inch
        ICON_TEXT_GAP = 0.015 * inch
        TEXT_COL_X = ICON_COL_W + ICON_TEXT_GAP
        ICON_NUDGE_DOWN = 4

        style = body_style or self.step_body_style
        text_col_w = 0 if single_step else TEXT_COL_X
        text_content_w = width - text_col_w
        markup = format_step_markup(step.text)

        # Extra padding to compensate for autoLeading underreporting (matches _build_phase_story)
        probe = Paragraph(markup, style)
        _, wrap_h = probe.wrap(text_content_w, 9999)
        extra_h = true_paragraph_height(probe, text_content_w) - wrap_h

        # Build table matching _build_phase_story exactly
        para = Paragraph(markup, style)
        para.wrap(text_content_w, 9999)
        tighten_large_font_lines(para)
        if single_step:
            _, table_h = para.wrap(width, 9999)
            table_h += extra_h
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

        # Add height for StepAction rows (grouped and packed for side-by-side)
        if hasattr(step, 'actions'):
            actions = step.actions.order_by('order') if hasattr(step.actions, 'order_by') else list(step.actions.all())
            action_content_w = width - text_col_w
            groups = self._group_actions(actions)
            packed_rows = self._pack_action_rows(groups, action_content_w)

            for row_info in packed_rows:
                if row_info[0] == 'single_card':
                    action = row_info[1]
                    icon_path, icon_w, icon_h = self._resolve_cost_icon(action)
                    markup_a = format_step_markup(action.text)
                    para_a = Paragraph(markup_a, self.action_body_style)
                    icon_space = MIN_CARD_ICON_W if icon_path else 0
                    arrow_w = _arrow_total_width_for_cost(action.cost)
                    text_w_a = action_content_w - icon_space - arrow_w
                    para_a.wrap(text_w_a, 9999)
                    text_h_a = true_paragraph_height(para_a, text_w_a)
                    row_h = max(text_h_a, icon_h if icon_path else 0) + ACTION_ROW_GAP
                    table_h += row_h

                elif row_info[0] == 'card_group':
                    cost_type = row_info[1]
                    group_actions = row_info[2]
                    icon_path, icon_w, icon_h = self._resolve_cost_icon(group_actions[0])
                    icon_space = MIN_CARD_ICON_W
                    arrow_w = CARD_ARROW_ICON_GAP + CARD_ARROW_W + CARD_ARROW_TEXT_GAP
                    text_w_a = action_content_w - icon_space - arrow_w
                    group_h = 0
                    for action in group_actions:
                        markup_a = format_step_markup(action.text)
                        para_a = Paragraph(markup_a, self.action_body_style)
                        para_a.wrap(text_w_a, 9999)
                        th = true_paragraph_height(para_a, text_w_a)
                        group_h += th
                    group_h += ACTION_ROW_GAP * (len(group_actions) - 1)
                    group_h = max(group_h, icon_h)
                    table_h += group_h + ACTION_ROW_GAP

                elif row_info[0] == 'row':
                    action_items = row_info[1]
                    if len(action_items) == 1:
                        action, _ = action_items[0]
                        icon_path, icon_w, icon_h = self._resolve_cost_icon(action)
                        markup_a = format_step_markup(action.text)
                        para_a = Paragraph(markup_a, self.action_body_style)
                        icon_space = icon_w if icon_path else 0
                        arrow_w = _arrow_total_width_for_cost(action.cost)
                        text_w_a = action_content_w - icon_space - arrow_w
                        para_a.wrap(text_w_a, 9999)
                        text_h_a = true_paragraph_height(para_a, text_w_a)
                        row_h = max(text_h_a, icon_h if icon_path else 0) + ACTION_ROW_GAP
                        table_h += row_h
                    else:
                        # Side-by-side: row height = max of individual heights
                        # Last action gets remaining width (matches _build_action_flowables)
                        total_gaps = SIDE_BY_SIDE_GAP * (len(action_items) - 1)
                        used_w = sum(w for _, w in action_items[:-1]) + total_gaps
                        last_w = action_content_w - used_w
                        max_h = 0
                        for idx, (action, base_w) in enumerate(action_items):
                            alloc_w = last_w if idx == len(action_items) - 1 else base_w
                            icon_path, icon_w, icon_h = self._resolve_cost_icon(action)
                            markup_a = format_step_markup(action.text)
                            para_a = Paragraph(markup_a, self.action_body_style)
                            icon_space = icon_w if icon_path else 0
                            arrow_w = _arrow_total_width_for_cost(action.cost)
                            text_w_a = alloc_w - icon_space - arrow_w
                            para_a.wrap(text_w_a, 9999)
                            text_h_a = true_paragraph_height(para_a, text_w_a)
                            h = max(text_h_a, icon_h if icon_path else 0)
                            if h > max_h:
                                max_h = h
                        table_h += max_h + ACTION_ROW_GAP

        # Add bordered box + track heights, iterated in the user's intermixed
        # order from the editor (boxes and tracks share an `order` sequence).
        for child in step.ordered_children:
            obj = child['obj']
            if child['kind'] == 'box':
                table_h += BORDERED_BOX_HEIGHTS.get(obj.height, BORDERED_BOX_HEIGHTS['medium']) + BORDERED_BOX_TITLE_SIZE / 2
            else:
                slots = list(obj.slots.all())
                tf = TrackFlowable(
                    track=obj,
                    slots=slots,
                    total_width=width - text_col_w,
                    body_style=self.step_body_style,
                    faction_color=self.faction_color,
                )
                _, track_h = tf.wrap(width - text_col_w, 9999)
                table_h += track_h + TRACK_TITLE_GAP + TRACK_BOTTOM_PAD

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

    def build(self, output_path):
        c = rl_canvas.Canvas(output_path, pagesize=landscape(letter))
        layout = self.sheet.layout_mode

        self._draw_background(c)
        self._draw_top_band(c)

        if layout == 'horizontal':
            self._draw_horizontal_phases(c)
        else:
            self._draw_vertical_phases(c)

        self._draw_card_slots(c)

        if self.content_boxes:
            if layout == 'horizontal':
                self._draw_horizontal_content_boxes(c)
            else:
                self._draw_vertical_content_boxes(c)

        self._draw_card_piles(c)

        c.save()

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
        self._draw_phase_box(c, box_x, box_y, box_w, box_h, rotated=True)
        self._phases_rect = (box_x, box_y, box_w, box_h)

        for i, phase_key in enumerate(phase_order):
            steps = self.phases_grouped.get(phase_key, [])
            x = X_MARGIN + PHASE_INTERNAL_MARGIN + i * (col_w + PHASE_INTERNAL_MARGIN)
            frame = Frame(x, self.phases_bottom_y, col_w, phase_h,
                         leftPadding=0, rightPadding=0, topPadding=PHASE_BOX_PAD_TOP, bottomPadding=0,
                         showBoundary=0)
            story = self._build_phase_story(phase_key, steps, content_width=col_w)
            frame.addFromList(story, c)

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

        min_w = PHASE_HEADER_MIN_W + (PHASE_INTERNAL_MARGIN * 2)
        max_w = BODY_W

        # Preferred width to avoid track staggering
        preferred_w = self._preferred_phase_track_width(phase_order)
        if self.content_boxes:
            # Reserve enough space for the widest content box (including its track needs)
            max_cb_min_w = CONTENT_BOX_MIN_W
            for cb in self.content_boxes:
                min_tw = self._min_track_width_for_content_box(cb)
                if min_tw > 0:
                    cb_w = min_tw + CONTENT_BOX_INTERNAL_MARGIN * 2
                else:
                    cb_w = CONTENT_BOX_MIN_W
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
            cursor_y = frame_y - PHASE_BOX_V_GAP

    def _draw_vertical_content_boxes(self, c):
        """Draw content boxes to the right of (and below) the phases box in vertical layout."""
        self._placed_boxes = []
        px, py, pw, ph = self._phases_rect
        target_h = self.phases_top_y - BOTTOM_MARGIN

        # Available space to the right of the phases box
        right_x = px + pw + CONTENT_BOX_GAP
        right_w = (PAGE_W - X_MARGIN) - right_x

        if right_w < CONTENT_BOX_MIN_W:
            return

        # Pre-compute minimum and preferred track widths for each content box
        min_track_widths = {}
        preferred_track_widths = {}
        for cb in self.content_boxes:
            min_track_widths[id(cb)] = self._min_track_width_for_content_box(cb)
            preferred_track_widths[id(cb)] = self._preferred_track_width_for_content_box(cb)

        remaining = list(self.content_boxes)
        cursor_y_top = self.phases_top_y
        row_start_x = right_x
        row_avail_w = right_w

        while remaining:
            # Try to fit as many as possible in this row at even widths
            row_count = len(remaining)
            while row_count > 0:
                total_gaps = CONTENT_BOX_GAP * (row_count - 1)
                even_w = (row_avail_w - total_gaps) / row_count
                if even_w >= CONTENT_BOX_MIN_W:
                    content_w_at_even = even_w - (CONTENT_BOX_INTERNAL_MARGIN * 2)
                    all_fit = True
                    for cb in remaining[:row_count]:
                        # Check height fits
                        _, h, _ = self._content_box_dims_for_width(cb, even_w)
                        if h > target_h:
                            all_fit = False
                            break
                        # Check tracks fit at this content width
                        min_tw = min_track_widths[id(cb)]
                        if min_tw > 0 and content_w_at_even < min_tw:
                            all_fit = False
                            break
                    if all_fit:
                        break
                row_count -= 1

            if row_count == 0:
                # Can't fit any more boxes
                for cb in remaining:
                    print(f"WARNING: ContentBox '{cb.title}' does not fit, skipping.")
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
                # Minimum box width to fit tracks (with overlap)
                min_w_for_tracks = (min_tw + CONTENT_BOX_INTERNAL_MARGIN * 2) if min_tw > 0 else CONTENT_BOX_MIN_W
                fallback_min = max(CONTENT_BOX_MIN_W, min_w_for_tracks)
                # Preferred box width to avoid staggering
                pref_w_for_tracks = (pref_tw + CONTENT_BOX_INTERNAL_MARGIN * 2) if pref_tw > 0 else CONTENT_BOX_MIN_W
                preferred_min = max(CONTENT_BOX_MIN_W, pref_w_for_tracks)

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
                if box_h > target_h:
                    box_h = target_h
                box_y = cursor_y_top - box_h

                self._draw_phase_box(c, cursor_x, box_y, box_w, box_h, rotated=False)
                content_x = cursor_x + CONTENT_BOX_INTERNAL_MARGIN
                frame = Frame(content_x, box_y, content_w, box_h,
                             leftPadding=0, rightPadding=0,
                             topPadding=CONTENT_BOX_PAD_TOP, bottomPadding=CONTENT_BOX_PAD_BOTTOM,
                             showBoundary=0)
                story = self._build_content_box_story(cb, content_w)
                frame.addFromList(story, c)
                self._placed_boxes.append((cursor_x, box_y, box_w, box_h))

                if box_y < row_bottom_y:
                    row_bottom_y = box_y
                gap = CONTENT_BOX_GAP + (extra_per_gap if row_count > 1 else 0)
                cursor_x += box_w + gap

            # Next row starts below the tallest box in this row
            cursor_y_top = row_bottom_y - CONTENT_BOX_GAP
            target_h = cursor_y_top - BOTTOM_MARGIN
            if target_h <= 0:
                for cb in remaining:
                    print(f"WARNING: ContentBox '{cb.title}' does not fit, skipping.")
                break

    def _draw_horizontal_content_boxes(self, c):
        """Draw content boxes below the phases box using column-first packing.

        Boxes are stacked top-to-bottom in a column, overflowing to the next
        column to the right when vertical space runs out.  Mirrors the logic
        of _draw_vertical_content_boxes but transposed (columns instead of rows).
        """
        self._placed_boxes = []
        px, py, pw, ph = self._phases_rect

        below_top = py - CONTENT_BOX_GAP
        avail_h = below_top - BOTTOM_MARGIN
        if avail_h <= 0:
            return

        # Pre-compute track width constraints
        min_track_widths = {}
        preferred_track_widths = {}
        for cb in self.content_boxes:
            min_track_widths[id(cb)] = self._min_track_width_for_content_box(cb)
            preferred_track_widths[id(cb)] = self._preferred_track_width_for_content_box(cb)

        remaining = list(self.content_boxes)
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
                    # Check height fits within even share (allow clamping for single-box columns)
                    _, h, _ = self._content_box_dims_for_width(cb, col_avail_w)
                    if h > even_h and col_count > 1:
                        all_fit = False
                        break
                    # Check tracks fit at this content width
                    min_tw = min_track_widths[id(cb)]
                    if min_tw > 0 and content_w_at_col < min_tw:
                        all_fit = False
                        break
                if all_fit:
                    break
                col_count -= 1

            if col_count == 0:
                for cb in remaining:
                    print(f"WARNING: ContentBox '{cb.title}' does not fit, skipping.")
                break

            # Determine boxes for this column
            col_boxes = remaining[:col_count]
            remaining = remaining[col_count:]
            total_gaps = CONTENT_BOX_GAP * (col_count - 1)
            even_h = (avail_h - total_gaps) / col_count

            # Binary search for narrowest column width where all boxes fit within even_h
            # Lower bound: max of CONTENT_BOX_MIN_W and track-required widths
            lo = CONTENT_BOX_MIN_W
            for cb in col_boxes:
                min_tw = min_track_widths[id(cb)]
                pref_tw = preferred_track_widths[id(cb)]
                if pref_tw > 0:
                    min_w_for_tracks = pref_tw + CONTENT_BOX_INTERNAL_MARGIN * 2
                elif min_tw > 0:
                    min_w_for_tracks = min_tw + CONTENT_BOX_INTERNAL_MARGIN * 2
                else:
                    min_w_for_tracks = CONTENT_BOX_MIN_W
                lo = max(lo, min_w_for_tracks)

            # Check if preferred width is feasible; fall back to minimum tracks
            if lo > col_avail_w:
                lo = CONTENT_BOX_MIN_W
                for cb in col_boxes:
                    min_tw = min_track_widths[id(cb)]
                    if min_tw > 0:
                        lo = max(lo, min_tw + CONTENT_BOX_INTERNAL_MARGIN * 2)

            hi = col_avail_w
            # Check whether any box will be height-clamped even at full width
            any_clamped = any(
                self._content_box_dims_for_width(cb, col_avail_w)[1] > even_h
                for cb in col_boxes
            )
            if any_clamped:
                # Width can't help height-clamped boxes — use the minimum width
                col_w = min(lo, col_avail_w)
            else:
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

            # Calculate actual box heights at col_w
            box_heights = []
            for cb in col_boxes:
                _, box_h, _ = self._content_box_dims_for_width(cb, col_w)
                if box_h > even_h:
                    box_h = even_h
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

                self._draw_phase_box(c, col_start_x, box_y, col_w, box_h, rotated=False)
                content_x = col_start_x + CONTENT_BOX_INTERNAL_MARGIN
                frame = Frame(content_x, box_y, content_w, box_h,
                             leftPadding=0, rightPadding=0,
                             topPadding=CONTENT_BOX_PAD_TOP, bottomPadding=CONTENT_BOX_PAD_BOTTOM,
                             showBoundary=0)
                story = self._build_content_box_story(cb, content_w)
                frame.addFromList(story, c)
                self._placed_boxes.append((col_start_x, box_y, col_w, box_h))

                gap = CONTENT_BOX_GAP + (padding if col_count > 1 else 0)
                cursor_y = box_y - gap

            # Next column starts to the right
            col_start_x += col_w + CONTENT_BOX_GAP
            col_avail_w -= col_w + CONTENT_BOX_GAP
            if col_avail_w < CONTENT_BOX_MIN_W:
                for cb in remaining:
                    print(f"WARNING: ContentBox '{cb.title}' does not fit, skipping.")
                break

    def _build_steps_story(self, steps, avail_w, centered=False):
        """Build flowables for a list of PhaseSteps (no header). Reused by phases and content boxes."""
        from reportlab.platypus import Table, TableStyle

        story = []
        ICON_COL_W = 0.325 * inch   # SVGs right-aligned within this width
        ICON_TEXT_GAP = 0.015 * inch
        TEXT_COL_X = ICON_COL_W + ICON_TEXT_GAP
        single_step = len(steps) == 1

        if centered and single_step:
            body_style = self.content_box_text_style
        else:
            body_style = self.step_body_style

        for step in steps:
            markup = format_step_markup(step.text)
            para = Paragraph(markup, body_style)

            # Compute extra bottom padding to compensate for autoLeading underreporting
            text_w = 0 if single_step else TEXT_COL_X
            content_w = avail_w - text_w
            probe = Paragraph(markup, body_style)
            _, wrap_h = probe.wrap(content_w, 9999)
            extra_h = true_paragraph_height(probe, content_w) - wrap_h

            # Tighten the rendered paragraph's large-font line spacing
            para.wrap(content_w, 9999)
            tighten_large_font_lines(para)

            # DEBUG: red border around step
            debug_border = ('BOX', (0, 0), (-1, -1), 0.5, HexColor('#FF0000'))

            if single_step:
                # No number icon — text takes full width
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
                        debug_border,
                    ]))
                    story.append(t)
                else:
                    story.append(para)

            # Append StepAction flowables below the step
            indent = 0 if single_step else TEXT_COL_X
            action_flowables = self._build_action_flowables(step, avail_w, indent)
            story.extend(action_flowables)

            # Append BorderedBox + Track flowables below the actions, in the
            # user's intermixed order from the editor (boxes and tracks share
            # an `order` sequence).
            for child in step.ordered_children:
                obj = child['obj']
                if child['kind'] == 'box':
                    box_h = BORDERED_BOX_HEIGHTS.get(obj.height, BORDERED_BOX_HEIGHTS['medium'])
                    body_markup = format_step_markup(obj.body) if obj.body else ''
                    bf = BorderedBoxFlowable(
                        title=obj.title,
                        body_markup=body_markup,
                        total_width=avail_w - indent,
                        box_height=box_h,
                        body_style=self.step_body_style,
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
                else:
                    slots = list(obj.slots.all())
                    tf = TrackFlowable(
                        track=obj,
                        slots=slots,
                        total_width=avail_w - indent,
                        body_style=self.step_body_style,
                        faction_color=self.faction_color,
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
            pil_img = PILImage.open(header_path)
            aspect = pil_img.size[0] / pil_img.size[1]
            target_w = avail_w
            if target_w <= PHASE_HEADER_LOCK_W:
                target_h = max(target_w / aspect, PHASE_HEADER_MIN_H)
            else:
                target_h = PHASE_HEADER_LOCK_W / aspect
            phase_name = PHASE_DISPLAY_NAMES.get(phase_key, phase_key.title())
            banner = BannerWithText(header_path, target_w, target_h, phase_name)
            banner.hAlign = 'LEFT'
            story.append(banner)

        story.extend(self._build_steps_story(steps, avail_w))
        return story

    def _build_content_box_story(self, content_box, content_width):
        """Build flowables for a content box: centered title, text, then steps."""
        story = []
        if content_box.title:
            story.append(Paragraph(content_box.title, self.content_box_title_style))
        if content_box.text:
            markup = format_step_markup(content_box.text)
            story.append(Paragraph(markup, self.content_box_text_style))
        steps = list(content_box.steps.all())
        story.extend(self._build_steps_story(steps, content_width, centered=True))
        return story

    def _draw_background(self, c):
        draw_faction_background(c, self.sheet.faction)


    def _draw_top_band(self, c):
        self._draw_title_bar(c)
        self._draw_ability_boxes(c)

    def _draw_title_bar(self, c):
        img_w = self.faction_top_bar_w
        img_h = self.faction_top_bar_h
        img_x = (PAGE_W - img_w) / 2
        bar_w = BODY_W * COLOR_BAR_W_RATIO
        bar_x = (PAGE_W - bar_w) / 2

        # Faction top bar SVG — top edge at self.faction_top_bar_top
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
            # DEBUG: red border around faction top bar
            c.saveState()
            c.setStrokeColor(HexColor('#FF0000'))
            c.setLineWidth(0.5)
            c.rect(img_x, img_y, img_w, img_h, fill=0, stroke=1)
            c.restoreState()

        # Color bar on top
        c.setFillColor(self.faction_color)
        c.rect(bar_x, self.title_bar_y, bar_w, TITLE_BAR_H, fill=1, stroke=0)

        # Draw faction name centered
        c.setFillColor(self.faction_name_color)
        c.setFont(self.faction_name_font, self.faction_name_font_size)
        c.drawCentredString(PAGE_W / 2, self.title_bar_y + FACTION_NAME_Y_OFFSET, self.sheet.faction.faction_name)

    def _calculate_ability_widths(self, abilities, available_w, icon_w):
        """Calculate proportional box widths based on body text length."""
        n = len(abilities)
        total_gap = ABILITY_GAP * (n - 1)
        distributable_w = available_w - total_gap

        if n == 1:
            return [distributable_w]

        # Body character counts as proxy for space needs
        char_counts = [len(a.body) for a in abilities]
        total_chars = sum(char_counts)

        # Per-ability minimum: max of global minimum or title rendered width
        min_widths = []
        for a in abilities:
            title_w = pdfmetrics.stringWidth(a.title, 'Baskerville', 8)
            min_widths.append(max(MIN_ABILITY_BOX_W, title_w + icon_w + 4))

        # If all bodies are empty, fall back to equal widths
        if total_chars == 0:
            return [max(distributable_w / n, mw) for mw in min_widths]

        # Initial proportional allocation
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
            # Only use a larger size if it fits at minimum width
            p = Paragraph(text, style)
            _, h = p.wrap(MIN_FLAVOR_TEXT_W - pad, 9999)
            if h <= usable_h:
                return MIN_FLAVOR_TEXT_W, style

        # Base size: binary search for narrowest width
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

    def _draw_ability_boxes(self, c):
        abilities = list(self.sheet.abilities.order_by('order'))

        box_h = ABILITY_BAR_H - FACTION_TOP_BAR_NUDGE
        box_y = self.title_bar_y - FACTION_TOP_BAR_NUDGE - box_h

        # Ability area aligns with the color bar edges
        bar_w = BODY_W * COLOR_BAR_W_RATIO
        bar_x = (PAGE_W - bar_w) / 2

        # Reserve space for crafted items SVG on the right
        # Crafted_Items_Box.svg is 64.93mm x 19.46mm (~3.34:1 aspect ratio)
        crafted_h = 0.767 * inch
        crafted_w = 0
        if self.sheet.include_crafted_items and os.path.exists(CRAFTED_ITEMS_SVG):
            crafted_w = crafted_h * (64.93 / 19.46)

        # Reserve space for flavor text on the left
        flavor_w = 0
        flavor_style = self.flavor_text_style
        flavor_text = self.sheet.flavor_text
        if flavor_text and flavor_text.strip():
            flavor_text = flavor_text.strip()
            max_flavor_w = max((bar_w - crafted_w) * 0.4, MIN_FLAVOR_TEXT_W)
            flavor_w, flavor_style = self._calculate_flavor_text_layout(flavor_text, max_flavor_w, box_h)

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
            # DEBUG: red border around flavor text box
            c.saveState()
            c.setStrokeColor(HexColor('#FF0000'))
            c.setLineWidth(0.5)
            c.rect(flavor_x, flavor_y, flavor_w, box_h, fill=0, stroke=1)
            c.restoreState()

        # Calculate available width for abilities
        available_w = bar_w
        if crafted_w:
            available_w -= (crafted_w + ABILITY_GAP)
        if flavor_w:
            available_w -= (flavor_w + ABILITY_GAP)

        if abilities:
            # Calculate icon width for width estimation
            icon_w = (self._ability_icon.width + 4) if self._ability_icon else 0

            # Proportional widths based on body text length, with per-ability minimums
            widths = self._calculate_ability_widths(abilities, available_w, icon_w)

            x = bar_x + (flavor_w + ABILITY_GAP if flavor_w else 0)
            for i, ability in enumerate(abilities):
                box_w = widths[i]

                # DEBUG: red border around ability box
                c.saveState()
                c.setStrokeColor(HexColor('#FF0000'))
                c.setLineWidth(0.5)
                c.rect(x, box_y, box_w, box_h, fill=0, stroke=1)
                c.restoreState()

                # Ability icon (SVG colored with faction color)
                if self._ability_icon:
                    icon_x = x + 2
                    icon_y = box_y + box_h - self._ability_icon.height - 2
                    renderPDF.draw(self._ability_icon, c, icon_x, icon_y)

                # Flow title + body text to the right of the icon
                story = [
                    Paragraph(f"<b>{ability.title}</b>", self.ability_title_style),
                    Paragraph(format_inline_images(ability.body), self.ability_body_style),
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

        # Draw crafted items SVG right-aligned with the color bar, below it by NUDGE
        if crafted_w:
            crafted_x = bar_x + bar_w - crafted_w
            crafted_y = self.title_bar_y - FACTION_TOP_BAR_NUDGE - crafted_h
            drawing = svg2rlg(CRAFTED_ITEMS_SVG)
            if drawing:
                sx = crafted_w / drawing.width
                sy = crafted_h / drawing.height
                drawing.width = crafted_w
                drawing.height = crafted_h
                drawing.scale(sx, sy)
                renderPDF.draw(drawing, c, crafted_x, crafted_y)
            # DEBUG: red border around crafted items box
            c.saveState()
            c.setStrokeColor(HexColor('#FF0000'))
            c.setLineWidth(0.5)
            c.rect(crafted_x, crafted_y, crafted_w, crafted_h, fill=0, stroke=1)
            c.restoreState()

    def _get_decree_bg_path(self):
        filename = 'title.png' if (self.decree_section.title or self.decree_section.body) else 'no-title.png'
        return os.path.join(DECREE_DIR, filename)

    def _get_slot_image_path(self, slot):
        text_len = len(slot.title or '') + len(slot.body or '')
        if text_len == 0:
            return os.path.join(DECREE_DIR, 'no-text.png')
        elif text_len < DECREE_TEXT_THRESHOLD:
            return os.path.join(DECREE_DIR, 'small-text.png')
        else:
            return os.path.join(DECREE_DIR, 'large-text.png')

    def _draw_card_slots(self, c):
        # Decree: background overlay + individual slot images
        if self.decree_section:
            # Draw title/no-title background
            # Image starts hidden above page, slides down by decree_slide
            bg_img = self._get_decree_bg_path()
            if os.path.exists(bg_img):
                from reportlab.lib.utils import ImageReader
                ir = ImageReader(bg_img)
                iw, ih = ir.getSize()
                scale = PAGE_W / iw
                draw_h = ih * scale
                draw_y = PAGE_H - self.decree_slide
                decree_img_top = draw_y + draw_h
                c.drawImage(bg_img, 0, draw_y,
                            width=PAGE_W, height=draw_h, mask='auto')
            else:
                draw_h = 0
                decree_img_top = PAGE_H

            # Decree section title text
            if self.decree_section.title:
                title_y = decree_img_top - DECREE_TITLE_Y_OFFSET
                c.setFillColor(HexColor('#FFFFFF'))
                c.setFont('Luminari', 20)
                c.drawCentredString(PAGE_W / 2, title_y, self.decree_section.title)

            # Individual slot images — evenly spaced horizontally, centered
            slots = list(self.decree_section.card_slots.all())
            n = len(slots)
            if n > 0:
                gap = max((PAGE_W - n * DECREE_SLOT_W) / (n + 1), DECREE_SLOT_MIN_GAP)
                total_w = n * DECREE_SLOT_W + (n - 1) * gap
                start_x = (PAGE_W - total_w) / 2.0
                slot_y = decree_img_top - DECREE_SLOT_Y_OFFSET

                for i, slot in enumerate(slots):
                    x = start_x + i * (DECREE_SLOT_W + gap)
                    slot_img = self._get_slot_image_path(slot)
                    if os.path.exists(slot_img):
                        c.drawImage(slot_img, x, slot_y,
                                    width=DECREE_SLOT_W, height=DECREE_SLOT_H, mask='auto')

                    # Slot title centered above the slot image
                    if slot.title:
                        c.setFillColor(HexColor('#FFFFFF'))
                        c.setFont('Luminari', 12)
                        c.drawCentredString(x + DECREE_SLOT_W / 2, slot_y + DECREE_SLOT_H - DECREE_SLOT_TITLE_OFFSET, slot.title)

    def _draw_card_piles(self, c):
        if not self.card_piles:
            return
        rightmost_x = PAGE_W - X_MARGIN - CARD_SLOT_W
        for i, pile in enumerate(self.card_piles):
            x = rightmost_x - i * (CARD_SLOT_W + CARD_PILE_GAP)
            self._place_and_draw_card_pile(c, pile, x)

    def _card_pile_body_paragraph(self, body_text, text_w):
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.lib.enums import TA_CENTER
        html = format_step_markup(body_text)
        style = ParagraphStyle(
            name='CardPileBody',
            fontName='Baskerville',
            fontSize=9,
            leading=9 * 1.2,
            alignment=TA_CENTER,
            textColor=self.ink_on_faction,
        )
        para = Paragraph(html, style)
        text_h = true_paragraph_height(para, text_w)
        return para, text_h

    def _card_pile_title_paragraph(self, title_text, text_w):
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
            textColor=self.ink_on_faction,
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

    def _place_and_draw_card_pile(self, c, pile, x):
        text_w = CARD_SLOT_W - 2 * CARD_PILE_PADDING

        title_para = None
        title_h = 0.0
        if pile.title:
            title_para, title_h = self._card_pile_title_paragraph(pile.title, text_w)

        body_para = None
        body_h = 0.0
        if pile.body:
            body_para, body_h = self._card_pile_body_paragraph(pile.body, text_w)

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
            return

        # --- Final fallback — upright at BOTTOM_MARGIN, text possibly cut off. ---
        print(f"ERROR: CardPile '{pile.title or pile.number}' cannot render with title/body fully visible. Rendering with text cut off.")
        self._draw_card_pile_upright(c, pile, x, default_y,
                                     title_para, title_h, body_para, body_h,
                                     CARD_PILE_TITLE_TOP_OFFSET)

    def _draw_card_pile_upright(self, c, pile, x, y,
                                title_para, title_h, body_para, body_h,
                                title_top_offset):
        drawing = self._load_card_pile_svg(self.ink_on_faction_hex,
                                           CARD_SLOT_W, CARD_SLOT_H)
        if drawing:
            renderPDF.draw(drawing, c, x, y)
        self._draw_card_pile_text(c, x, y, title_para, title_h, body_para, body_h,
                                  title_top_offset)

    def _draw_card_pile_rotated(self, c, pile, x, y,
                                title_para, title_h, body_para, body_h,
                                title_top_offset):
        c.saveState()
        c.translate(x, y)
        c.rotate(90)
        card_local_x = 0
        card_local_y = -CARD_SLOT_H
        drawing = self._load_card_pile_svg(self.ink_on_faction_hex,
                                           CARD_SLOT_W, CARD_SLOT_H)
        if drawing:
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
        ('Warriors', ('W',)),
        ('Buildings', ('B',)),
        ('Tokens', ('T',)),
        ('Other Pieces', ('C', 'O')),
    ]

    def __init__(self, faction_back):
        self.back = faction_back
        self.faction = faction_back.faction
        self.color_hex = self.faction.color or '#5B4A8A'
        self.faction_color = HexColor(self.color_hex)

        pieces = self._resolve_pieces(faction_back)
        self._pieces_by_col = []
        for title, types in self.PIECE_COLUMNS:
            col_pieces = [p for p in pieces if p.type in types]
            self._pieces_by_col.append((title, col_pieces))

        self._setup_steps = self._resolve_setup_steps(faction_back)

        self._setup_marker_svgs = {}
        for n in range(10):
            svg_path = os.path.join(PHASE_NUMBER_SVG_DIR, f'{n}.svg')
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

    def build(self, output_path):
        c = rl_canvas.Canvas(output_path, pagesize=landscape(letter))

        self._draw_background(c)

        # Lighten the background with a white screen at configured opacity.
        if BACK_BG_SCREEN_OPACITY > 0:
            c.saveState()
            c.setFillColorRGB(1, 1, 1, alpha=BACK_BG_SCREEN_OPACITY)
            c.setStrokeColorRGB(1, 1, 1, alpha=0)
            c.rect(0, 0, PAGE_W, PAGE_H, stroke=0, fill=1)
            c.restoreState()

        back_body_w = PAGE_W - (BACK_X_MARGIN * 2)

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

        c.save()

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

        title = 'Faction Component Manifest'
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
            # DEBUG: red lines marking MANIFEST_PIECE_COL_H_PAD bounds
            c.saveState()
            c.setStrokeColorRGB(1, 0, 0)
            c.setLineWidth(0.5)
            c.line(piece_area_left, pieces_bottom, piece_area_left, pieces_top)
            c.line(piece_area_right, pieces_bottom, piece_area_right, pieces_top)
            c.restoreState()
            self._draw_manifest_column_pieces(c, piece_area_left, pieces_bottom,
                                              piece_area_right - piece_area_left,
                                              pieces_top - pieces_bottom, pieces)
        c.restoreState()

    def _draw_manifest_column_pieces(self, c, x, y, w, h, pieces):
        if not pieces:
            c.saveState()
            c.setFont('Baskerville-Italic', MANIFEST_PIECE_LABEL_SIZE)
            c.setFillColorRGB(0.45, 0.45, 0.45)
            c.drawCentredString(x + w / 2, y + h / 2, '(none)')
            c.restoreState()
            return
        n = len(pieces)
        slot_h = h / n
        for idx, piece in enumerate(pieces):
            slot_top = y + h - idx * slot_h
            slot_bottom = slot_top - slot_h
            self._draw_piece(c, piece, x, slot_bottom, w, slot_h)

    def _draw_piece(self, c, piece, x, y, w, h):
        icon_path = None
        icon_attr = getattr(piece, 'small_icon', None)
        if icon_attr:
            path_val = getattr(icon_attr, 'path', None)
            if path_val and os.path.exists(path_val):
                icon_path = path_val
            elif isinstance(icon_attr, str) and os.path.exists(icon_attr):
                icon_path = icon_attr

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
        if icon_path:
            from reportlab.lib.utils import ImageReader
            try:
                iw, ih = ImageReader(icon_path).getSize()
                scale = min(icon_max_w / iw, icon_max_h / ih)
                draw_w = iw * scale
                draw_h = ih * scale
            except Exception:
                icon_path = None
                draw_w = 0
                draw_h = 0

        # Warrior fallback: if no icon was supplied, draw the faction-colored meeple.
        if not icon_path and getattr(piece, 'type', None) == 'W' and self._warrior_fallback_svg is not None:
            svg_fallback = self._warrior_fallback_svg
            base_w = svg_fallback.width or 1
            base_h = svg_fallback.height or 1
            scale = min(icon_max_w / base_w, icon_max_h / base_h)
            draw_w = base_w * scale
            draw_h = base_h * scale

        # Available width for the label block (×qty + name), respecting horizontal padding
        h_pad = MANIFEST_PIECE_LABEL_H_PAD
        v_pad = MANIFEST_PIECE_V_PAD
        inner_x = x + h_pad
        inner_w = w - h_pad * 2
        inner_y = y + v_pad
        inner_h = h - v_pad * 2
        has_icon = bool(icon_path) or svg_fallback is not None
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
        safe_name = name.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
        markup = (f'<font name="Baskerville" size="{qty_size}">{qty_text}</font>'
                  f'&nbsp;{safe_name}')
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
            c.drawImage(icon_path, icon_x, icon_y, width=draw_w, height=draw_h,
                        preserveAspectRatio=True, mask='auto')
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
        else:
            label_x = start_x

        # Vertically center the paragraph on the row midpoint.
        para.drawOn(c, label_x, mid_y - para_h / 2)

    # ---------- Attribute bars ----------

    ATTR_FIELDS = [
        ('complexity', 'Complexity'),
        ('card_wealth', 'Card Wealth'),
        ('aggression', 'Aggression'),
        ('crafting_ability', 'Crafting Ability'),
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

    def _draw_attribute_row(self, c, x, top_y, w, field, label):
        """Draw a single attribute row (italic label + filled bar with level text).

        Mirrors the web partial: left black border, grey track, faction-color fill
        sized by ATTR_BAR_FILL_RATIOS, white level text (or black when white is
        not legible on the faction color).
        """
        value = getattr(self.back, field, 'N') or 'N'

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

        # Faction-color fill sized by level (no track — empty portion is transparent)
        fill_ratio = ATTR_BAR_FILL_RATIOS.get(value, 0.0)
        fill_w = bar_w * fill_ratio
        if fill_w > 0:
            c.setFillColor(self.faction_color)
            c.rect(bar_x, bar_y, fill_w, ATTR_BAR_H, stroke=0, fill=1)

        # Level text inside bar — white when legible on faction color, else black.
        # 'N' always uses black since the fill is barely visible.
        level_label = ATTR_BAR_LEVEL_LABELS.get(value, '')
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
        title = f'Setup ({setup_order})' if setup_order else 'Setup'
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
        title = getattr(self.back, 'how_to_play_title', '') or 'Playing the Faction'
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
        if body_h <= 0 or not body:
            return

        # Resolve optional back_image path and natural-scale within max bounds.
        img_path, img_w, img_h = self._resolve_back_image()

        markup = format_step_markup(body)
        para = Paragraph(markup, self.howtoplay_body_style)

        if not img_path:
            para.wrap(w, body_h)
            tighten_large_font_lines(para)
            para_h = para.height
            para.drawOn(c, x, body_top - para_h)
            return

        # Anchor the image to the PDF's bottom-right corner exactly.
        img_x = PAGE_W - img_w
        img_y = 0
        c.drawImage(img_path, img_x, img_y, width=img_w, height=img_h,
                    preserveAspectRatio=True, mask='auto')

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
        para.height = sum(
            max(line.ascent - line.descent, leading)
            for line in para.blPara.lines
        )
        tighten_large_font_lines(para)
        para.drawOn(c, x, body_top - para.height)

    def _resolve_back_image(self):
        """Returns (path, draw_w, draw_h) for the optional FactionBack image,
        natural-scaled to fit within HOWTOPLAY_IMAGE_MAX_W × HOWTOPLAY_IMAGE_MAX_H.
        Returns (None, 0, 0) if no image is set."""
        img_attr = getattr(self.back, 'back_image', None)
        if not img_attr:
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
        scale = min(HOWTOPLAY_IMAGE_MAX_W / iw, HOWTOPLAY_IMAGE_MAX_H / ih, 1.0)
        return path_val, iw * scale, ih * scale
