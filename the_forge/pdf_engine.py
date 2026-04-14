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

FONT_DIR = os.path.join(os.path.dirname(__file__), '..', 'the_keep', 'static', 'fonts')
pdfmetrics.registerFont(TTFont('Luminari', os.path.join(FONT_DIR, 'Luminari_edit.ttf')))
pdfmetrics.registerFont(TTFont('Baskerville', os.path.join(FONT_DIR, 'Baskerville10Pro.ttf')))
pdfmetrics.registerFont(TTFont('Baskerville-Bold', os.path.join(FONT_DIR, 'Baskerville10Pro-Bold.ttf')))
pdfmetrics.registerFont(TTFont('Baskerville-Italic', os.path.join(FONT_DIR, 'Baskerville10Pro-Italic.ttf')))
pdfmetrics.registerFontFamily('Baskerville', normal='Baskerville', bold='Baskerville-Bold', italic='Baskerville-Italic')

PAGE_W, PAGE_H = landscape(letter)  # 792 x 612 pts

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

# TokenSlot/BuildingSlot are 214x214 (square)
TRACK_SLOT_SIZE = 0.55 * inch
TRACK_SLOT_GAP = 0.1 * inch
TRACK_PANEL_W = 2.0 * inch
TRACK_ROW_H = TRACK_SLOT_SIZE + 0.15 * inch

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

# Image height for inline images like card draw and VP
INLINE_IMG_H = 14.5

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
TOKEN_SLOT_IMG = os.path.join(STATIC_DIR, 'pdf/images/TokenSlot.png')
BUILDING_SLOT_IMG = os.path.join(STATIC_DIR, 'pdf/images/BuildingSlot.png')
DECREE_DIR = os.path.join(STATIC_DIR, 'pdf/decree')
PHASE_BOX_SVG = os.path.join(STATIC_DIR, 'pdf/boxes/Phase_Box.svg')

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


INLINE_IMAGES_PDF = {
    'draw': os.path.join(STATIC_DIR, 'pdf/inline/card.png'),
    '1VP': os.path.join(STATIC_DIR, 'pdf/inline/1VP.png'),
    '2VP': os.path.join(STATIC_DIR, 'pdf/inline/2VP.png'),
    '3VP': os.path.join(STATIC_DIR, 'pdf/inline/3VP.png'),
    '4VP': os.path.join(STATIC_DIR, 'pdf/inline/4VP.png'),
    'VP': os.path.join(STATIC_DIR, 'pdf/inline/VP.png'),

    'bird': os.path.join(STATIC_DIR, 'pdf/inline/bird_card.png'),
    'mouse': os.path.join(STATIC_DIR, 'pdf/inline/mouse_card.png'),
    'fox': os.path.join(STATIC_DIR, 'pdf/inline/fox_card.png'),
    'rabbit': os.path.join(STATIC_DIR, 'pdf/inline/rabbit_card.png'),
    'bunny': os.path.join(STATIC_DIR, 'pdf/inline/rabbit_card.png'),

    'cards': os.path.join(STATIC_DIR, 'pdf/inline/other_cards.png'),

    'mouse tilt': os.path.join(STATIC_DIR, 'pdf/inline/mouse_tilt.png'),
    'fox tilt': os.path.join(STATIC_DIR, 'pdf/inline/fox_tilt.png'),
    'rabbit tilt': os.path.join(STATIC_DIR, 'pdf/inline/rabbit_tilt.png'),
    'bird tilt': os.path.join(STATIC_DIR, 'pdf/inline/bird_tilt.png'),
}

COST_ICON_PATHS = {
    # Items (_flip.png variants)
    'item_sword': os.path.join(STATIC_DIR, 'items/sword_flip.png'),
    'item_hammer': os.path.join(STATIC_DIR, 'items/hammer_flip.png'),
    'item_crossbow': os.path.join(STATIC_DIR, 'items/crossbow_flip.png'),
    'item_coins': os.path.join(STATIC_DIR, 'items/coins_flip.png'),
    'item_boots': os.path.join(STATIC_DIR, 'items/boots_flip.png'),
    'item_tea': os.path.join(STATIC_DIR, 'items/tea_flip.png'),
    'item_bag': os.path.join(STATIC_DIR, 'items/bag_flip.png'),
    'item_torch': os.path.join(STATIC_DIR, 'items/torch_flip.png'),
    'item_any': os.path.join(STATIC_DIR, 'items/any_flip.png'),
    # Cards
    'card_fox': os.path.join(STATIC_DIR, 'pdf/inline/fox_card.png'),
    'card_mouse': os.path.join(STATIC_DIR, 'pdf/inline/mouse_card.png'),
    'card_rabbit': os.path.join(STATIC_DIR, 'pdf/inline/rabbit_card.png'),
    'card_bird': os.path.join(STATIC_DIR, 'pdf/inline/bird_card.png'),
    'card_nonbird': os.path.join(STATIC_DIR, 'pdf/inline/other_cards.png'),
    # Action (default faction icon)
    'action': os.path.join(STATIC_DIR, 'pdf/inline/default_faction_icon.png'),
}


def format_step_markup(text):
    """Convert semi-markdown to ReportLab Paragraph XML.

    ##text## -> Baskerville 15pt (title size)
    **text** -> bold
    _text_   -> italic
    {{ key }} -> inline image (safe fallback if no match)
    """
    if not text:
        return ""

    # Escape XML special chars in the raw input
    result = text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')

    # {{ keyword }} -> inline image (aspect-ratio preserved, fixed height)
    
    def image_replacer(match):
        keyword = match.group(1).strip()
        img_path = INLINE_IMAGES_PDF.get(keyword)
        if not img_path or not os.path.exists(img_path):
            return match.group(0)
        from PIL import Image as PILImage
        pil_img = PILImage.open(img_path)
        iw, ih = pil_img.size
        aspect = iw / ih
        img_w = INLINE_IMG_H * aspect
        return f'<img src="{img_path}" width="{img_w:.1f}" height="{INLINE_IMG_H}" valign="middle"/>'

    result = re.sub(r"\{\{\s*([^}]+?)\s*\}\}", image_replacer, result)

    # ##text## -> title-size font
    result = re.sub(r"##(.+?)##", r'<font name="Baskerville" size="15">\1</font>', result)

    # **text** -> bold
    result = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", result)

    # _text_ -> italic (word-boundary aware)
    result = re.sub(r"(?<!\w)_(.+?)_(?!\w)", r"<i>\1</i>", result)

    # Newlines -> line breaks
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


class SheetLayoutEngine:

    def __init__(self, faction_sheet):
        self.sheet = faction_sheet
        self.faction_color = HexColor(self.sheet.faction.color or '#5B4A8A')
        from the_forge.models import CardboardTrack, FactionSheet, StepAction
        from django.db.models import Prefetch
        if isinstance(faction_sheet, FactionSheet):
            all_steps = list(faction_sheet.phase_steps.prefetch_related(
                Prefetch('actions', queryset=StepAction.objects.order_by('order'))
            ).all())
        else:
            all_steps = list(faction_sheet.phase_steps.all())
        self.steps = [s for s in all_steps if s.phase != 'other']
        self.phases_grouped = {
            phase: list(steps)
            for phase, steps in groupby(self.steps, key=lambda s: s.phase)
        }
        if isinstance(faction_sheet, FactionSheet):
            self.tracks = list(
                CardboardTrack.objects.filter(
                    step__sheet=faction_sheet,
                    step__phase__in=['birdsong', 'daylight', 'evening']
                ).prefetch_related('slots').order_by('order')
            )
        else:
            self.tracks = []

        decree_sections = list(faction_sheet.decrees.prefetch_related('card_slots').all())
        self.decree_section = next((d for d in decree_sections if d.type == 'decree'), None)
        self.single_section = next((d for d in decree_sections if d.type == 'single'), None)

        # decree_slide = how far the decree image slides down onto the page
        # (0 = fully hidden above page, draw_h = fully visible)
        self.decree_slide = DECREE_MIN_OFFSET if self.decree_section else 0.0

        # Faction Top Bar image top edge starts at TOP_MARGIN (pushed down by decree)
        self.faction_top_bar_top = PAGE_H - TOP_MARGIN - self.decree_slide
        self.faction_top_bar_w = PAGE_W - 0.7 * inch
        self.faction_top_bar_h = self.faction_top_bar_w * (370 / 2106)

        # Color bar: same top-down calculation as the SVG, then nudge below it
        self.title_bar_y = PAGE_H - self.decree_slide - TOP_MARGIN - FACTION_TOP_BAR_NUDGE - TITLE_BAR_H

        # Phase area: top is below the Faction Top Bar image, bottom is above tracks
        self.phases_top_y = self.faction_top_bar_top - self.faction_top_bar_h
        tracks_h = len(self.tracks) * TRACK_ROW_H if self.tracks else 0
        self.phases_bottom_y = BOTTOM_MARGIN + tracks_h

        self._init_styles()
        self._ability_icon = self._load_colored_svg(ABILITY_BERRY_SVG, self.sheet.faction.color or '#5B4A8A', 0.5 * inch)

        # Preload numbered SVGs (0-9) for phase steps, at natural size
        color_hex = self.sheet.faction.color or '#5B4A8A'
        self._phase_number_svgs = {}
        for n in range(10):
            svg_path = os.path.join(PHASE_NUMBER_SVG_DIR, f'{n}.svg')
            if os.path.exists(svg_path):
                self._phase_number_svgs[n] = self._load_colored_svg(svg_path, color_hex)

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
        self.faction_name_color = HexColor('#FFFFFF')

    def _resolve_cost_icon(self, action):
        """Resolve icon path and draw dimensions for a StepAction's cost type.

        Returns (icon_path, draw_w, draw_h) or (None, 0, 0) if no icon.
        """
        from PIL import Image as PILImage

        cost = action.cost
        if cost == 'other':
            icon_path = action.cost_image.path if action.cost_image else None
        else:
            icon_path = COST_ICON_PATHS.get(cost)

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

        return flowables

    def measure_phase_height(self, steps, width, header_h=None):
        """Calculate total height needed for a phase's steps at given width."""
        total = header_h if header_h is not None else self._header_height()
        single_step = len(steps) == 1
        for step in steps:
            total += self.measure_step_height(step, width, single_step=single_step)
        return total

    def measure_step_height(self, step, width, single_step=False):
        from reportlab.platypus import Table, TableStyle
        ICON_COL_W = 0.325 * inch
        ICON_TEXT_GAP = 0.015 * inch
        TEXT_COL_X = ICON_COL_W + ICON_TEXT_GAP
        ICON_NUDGE_DOWN = 4

        text_col_w = PHASE_INTERNAL_MARGIN if single_step else TEXT_COL_X
        text_content_w = width - text_col_w
        markup = format_step_markup(step.text)

        # Extra padding to compensate for autoLeading underreporting (matches _build_phase_story)
        probe = Paragraph(markup, self.step_body_style)
        _, wrap_h = probe.wrap(text_content_w, 9999)
        extra_h = true_paragraph_height(probe, text_content_w) - wrap_h

        # Build table matching _build_phase_story exactly
        para = Paragraph(markup, self.step_body_style)
        para.wrap(text_content_w, 9999)
        tighten_large_font_lines(para)
        if single_step:
            t = Table([['', para]], colWidths=[text_col_w, text_content_w])
            t.setStyle(TableStyle([
                ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                ('LEFTPADDING', (0, 0), (-1, -1), 0),
                ('RIGHTPADDING', (0, 0), (-1, -1), 0),
                ('TOPPADDING', (0, 0), (-1, -1), 0),
                ('BOTTOMPADDING', (0, 0), (-1, -1), extra_h),
            ]))
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

        self._draw_tracks(c, layout)
        self._draw_card_slots(c)
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
        box_h = total_content_h + (n - 1) * PHASE_BOX_V_GAP + PHASE_BOX_PAD_TOP + PHASE_BOX_PAD_BOTTOM
        return box_w, box_h, content_w

    def _draw_vertical_phases(self, c):
        phase_order = ['birdsong', 'daylight', 'evening']
        max_h = self.phases_top_y - BOTTOM_MARGIN

        min_w = PHASE_HEADER_MIN_W + (PHASE_INTERNAL_MARGIN * 2)
        max_w = BODY_W

        # Check if minimum width already fits
        _, box_h_at_min, _ = self._vertical_box_dims_for_width(min_w, phase_order)
        if box_h_at_min <= max_h:
            box_w, box_h, content_w = min_w, box_h_at_min, min_w - (PHASE_INTERNAL_MARGIN * 2)
        else:
            # Binary search for smallest width where content fits
            lo, hi = min_w, max_w
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

        # Stack frames top-to-bottom with content-aware heights
        header_h = self._banner_height_for_width(content_w)
        cursor_y = self.phases_top_y - PHASE_BOX_PAD_TOP

        for i, phase_key in enumerate(phase_order):
            steps = self.phases_grouped.get(phase_key, [])
            content_h = self.measure_phase_height(steps, content_w, header_h=header_h)
            frame_y = cursor_y - content_h
            # Clamp frame to stay within the phase box
            if frame_y < box_y:
                frame_y = box_y
                content_h = cursor_y - box_y
            frame = Frame(content_x, frame_y, content_w, content_h,
                         leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0,
                         showBoundary=0)
            story = self._build_phase_story(phase_key, steps, content_width=content_w)
            frame.addFromList(story, c)
            cursor_y = frame_y - PHASE_BOX_V_GAP



    def _build_phase_story(self, phase_key, steps, content_width=None):
        from reportlab.platypus import Table, TableStyle

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

        ICON_COL_W = 0.325 * inch   # SVGs right-aligned within this width
        ICON_TEXT_GAP = 0.015 * inch
        TEXT_COL_X = ICON_COL_W + ICON_TEXT_GAP
        single_step = len(steps) == 1
        SINGLE_STEP_INDENT = PHASE_INTERNAL_MARGIN

        for step in steps:
            markup = format_step_markup(step.text)
            para = Paragraph(markup, self.step_body_style)

            # Compute extra bottom padding to compensate for autoLeading underreporting
            text_w = SINGLE_STEP_INDENT if single_step else TEXT_COL_X
            content_w = avail_w - text_w
            probe = Paragraph(markup, self.step_body_style)
            _, wrap_h = probe.wrap(content_w, 9999)
            extra_h = true_paragraph_height(probe, content_w) - wrap_h

            # Tighten the rendered paragraph's large-font line spacing
            para.wrap(content_w, 9999)
            tighten_large_font_lines(para)

            # DEBUG: red border around step
            debug_border = ('BOX', (0, 0), (-1, -1), 0.5, HexColor('#FF0000'))

            if single_step:
                # No number icon — indent text from left side of box
                t = Table([['', para]], colWidths=[SINGLE_STEP_INDENT, avail_w - SINGLE_STEP_INDENT])
                t.setStyle(TableStyle([
                    ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                    ('LEFTPADDING', (0, 0), (-1, -1), 0),
                    ('RIGHTPADDING', (0, 0), (-1, -1), 0),
                    ('TOPPADDING', (0, 0), (-1, -1), 0),
                    ('BOTTOMPADDING', (0, 0), (-1, -1), extra_h),
                    debug_border,
                ]))
                story.append(t)
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
            indent = SINGLE_STEP_INDENT if single_step else TEXT_COL_X
            action_flowables = self._build_action_flowables(step, avail_w, indent)
            story.extend(action_flowables)

        return story

    def _draw_background(self, c):
        img_path = self.sheet.get_background_path()
        from reportlab.lib.utils import ImageReader
        img_reader = ImageReader(img_path)
        iw, ih = img_reader.getSize()

        if self.sheet.faction.repeat_background_image:
            # Cap height to 1/3 of page so we get at least 3 rows
            max_h = PAGE_H / 3
            if ih > max_h:
                scale = max_h / ih
                draw_w = iw * scale
                draw_h = max_h
            else:
                draw_w = iw
                draw_h = ih

            # Brick pattern tile — alternating rows offset by 50%
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
            # Cover fill — scale to fill page, preserving aspect ratio
            scale = max(PAGE_W / iw, PAGE_H / ih)
            draw_w = iw * scale
            draw_h = ih * scale
            draw_x = (PAGE_W - draw_w) / 2
            draw_y = (PAGE_H - draw_h) / 2
            c.drawImage(img_path, draw_x, draw_y, width=draw_w, height=draw_h)


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
                    Paragraph(ability.body, self.ability_body_style),
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

        # Single slot: bottom-right corner, fully on page
        if self.single_section:
            x = PAGE_W - X_MARGIN - CARD_SLOT_W
            y = BOTTOM_MARGIN
            c.drawImage(CARD_SLOT_IMG, x, y,
                        width=CARD_SLOT_W, height=CARD_SLOT_H, mask='auto')

    def _draw_tracks(self, c, layout):
        if not self.tracks:
            return

        for track_idx, track in enumerate(self.tracks):
            slots = list(track.slots.all())
            img_path = TOKEN_SLOT_IMG if track.type == 'token' else BUILDING_SLOT_IMG

            if layout == 'horizontal':
                self._draw_track_horizontal(c, track, slots, img_path, track_idx)
            else:
                self._draw_track_vertical(c, track, slots, img_path, track_idx)

    def _draw_track_horizontal(self, c, track, slots, img_path, track_idx):
        # Tracks stacked upward from BOTTOM_MARGIN
        base_y = BOTTOM_MARGIN + (track_idx * TRACK_ROW_H)

        # Track title
        c.setFont('Luminari', 9)
        c.setFillColor(HexColor('#000000'))
        c.drawString(X_MARGIN, base_y + TRACK_SLOT_SIZE + 0.02 * inch, track.title)

        # Draw slots horizontally
        for i, slot in enumerate(slots):
            x = X_MARGIN + i * (TRACK_SLOT_SIZE + TRACK_SLOT_GAP)
            c.drawImage(img_path, x, base_y,
                        width=TRACK_SLOT_SIZE, height=TRACK_SLOT_SIZE, mask='auto')
            c.setFont('Baskerville', 6)
            c.drawCentredString(x + TRACK_SLOT_SIZE / 2, base_y - 8, slot.title)

    def _draw_track_vertical(self, c, track, slots, img_path, track_idx):
        # Tracks on the right side panel
        panel_x = PAGE_W - X_MARGIN - TRACK_PANEL_W

        track_block_h = TRACK_SLOT_SIZE + 0.25 * inch
        base_y = self.phases_top_y - (track_idx * track_block_h) - 0.2 * inch

        # Track title
        c.setFont('Luminari', 9)
        c.setFillColor(HexColor('#000000'))
        c.drawString(panel_x, base_y, track.title)

        # Draw slots horizontally within the panel
        for i, slot in enumerate(slots):
            x = panel_x + i * (TRACK_SLOT_SIZE + TRACK_SLOT_GAP)
            y = base_y - TRACK_SLOT_SIZE - 0.02 * inch
            c.drawImage(img_path, x, y,
                        width=TRACK_SLOT_SIZE, height=TRACK_SLOT_SIZE, mask='auto')
            c.setFont('Baskerville', 6)
            c.drawCentredString(x + TRACK_SLOT_SIZE / 2, y - 8, slot.title)

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

