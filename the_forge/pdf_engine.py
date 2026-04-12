# pdf_engine.py

import os
import re
import tempfile
from reportlab.lib.pagesizes import landscape, letter
from reportlab.lib.colors import HexColor
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, Frame, Image
from reportlab.pdfgen import canvas as rl_canvas
from reportlab.pdfbase import pdfmetrics
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

COLOR_BAR_W_RATIO = 0.90
FACTION_TOP_BAR_NUDGE = 0.1 * inch
FACTION_NAME_Y_OFFSET = 0.15 * inch

PHASE_HEADER_H = 0.36 * inch
PHASE_INTERNAL_MARGIN = 0.1 * inch

# Phase box background layout
PHASE_BOX_V_EXTRA_W = X_MARGIN * 2       # padding added to short header width for vertical box width
PHASE_BOX_V_GAP = 0.1 * inch             # spacing between stacked phases in vertical box
PHASE_BOX_H_PAD_TOP = TOP_MARGIN         # padding above tallest phase in horizontal box
PHASE_BOX_H_PAD_BOTTOM = BOTTOM_MARGIN   # padding below phase content in horizontal box

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
        'color': '#E8A838',
    },
    'daylight': {
        'long': os.path.join(STATIC_DIR, 'pdf/headers/DaylightBarLong.png'),
        'short': os.path.join(STATIC_DIR, 'pdf/headers/DaylightBarShort.png'),
        'color': '#6AB0D4',
    },
    'evening': {
        'long': os.path.join(STATIC_DIR, 'pdf/headers/EveningBarLong.png'),
        'short': os.path.join(STATIC_DIR, 'pdf/headers/EveningBarShort.png'),
        'color': '#7B6EA8',
    },
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
    INLINE_IMG_H = 14.5
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


def true_paragraph_height(para, width):
    """Wrap a Paragraph and return its true height, accounting for autoLeading.

    ReportLab's Paragraph.wrap() underreports height when autoLeading='max'
    is used with mixed font sizes. This inspects the internal line metrics
    to compute the actual rendered height.
    """
    _, h = para.wrap(width, 9999)
    if not hasattr(para, 'blPara') or not hasattr(para.blPara, 'lines'):
        return h

    base_leading = para.style.leading
    extra = 0
    for line in para.blPara.lines:
        ascent = getattr(line, 'ascent', None)
        descent = getattr(line, 'descent', None)
        if ascent is not None and descent is not None:
            line_h = ascent - descent  # descent is negative
            if line_h > base_leading:
                extra += line_h - base_leading
    return h + extra


class SheetLayoutEngine:

    def __init__(self, faction_sheet):
        self.sheet = faction_sheet
        self.faction_color = HexColor(self.sheet.color or '#5B4A8A')
        self.steps = list(faction_sheet.phase_steps.all())
        self.phases_grouped = {
            phase: list(steps)
            for phase, steps in groupby(self.steps, key=lambda s: s.phase)
        }

        self.tracks = list(faction_sheet.tracks.prefetch_related('slots').all())

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
        self._ability_icon = self._load_colored_svg(ABILITY_BERRY_SVG, self.sheet.color or '#5B4A8A', 0.5 * inch)

        # Preload numbered SVGs (0-9) for phase steps, at natural size
        color_hex = self.sheet.color or '#5B4A8A'
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
        self.faction_name_font = 'Luminari'
        self.faction_name_font_size = 30
        self.faction_name_color = HexColor('#FFFFFF')

    def measure_phase_height(self, steps, width):
        """Calculate total height needed for a phase's steps at given width."""
        total = self._header_height()
        single_step = len(steps) == 1
        for step in steps:
            total += self.measure_step_height(step, width, single_step=single_step)
        return total

    def measure_step_height(self, step, width, single_step=False):
        from reportlab.platypus import Table
        ICON_COL_W = 0.325 * inch
        ICON_TEXT_GAP = 0.015 * inch
        TEXT_COL_X = ICON_COL_W + ICON_TEXT_GAP

        text_col_w = PHASE_INTERNAL_MARGIN if single_step else TEXT_COL_X
        markup = format_step_markup(step.text)

        # Get true paragraph height (accounts for autoLeading)
        para = Paragraph(markup, self.step_body_style)
        _, wrap_h = para.wrap(width - text_col_w, 9999)
        extra_h = true_paragraph_height(para, width - text_col_w) - wrap_h

        # Measure via Table to include table overhead
        para2 = Paragraph(markup, self.step_body_style)
        t = Table([['', para2]], colWidths=[text_col_w, None])
        _, table_h = t.wrap(width, 9999)
        return table_h + extra_h

    def determine_layout(self):
        if self.sheet.layout_mode != 'auto':
            return self.sheet.layout_mode

        n = len(self.phases_grouped)
        col_width = BODY_W / n
        max_phase_h = max(
            self.measure_phase_height(steps, col_width)
            for steps in self.phases_grouped.values()
        )
        # If tallest column needs more than 50% of available phase height, go horizontal
        phase_h = self.phases_top_y - self.phases_bottom_y
        return 'horizontal' if max_phase_h > phase_h * 0.5 else 'vertical'

    def build(self, output_path):
        c = rl_canvas.Canvas(output_path, pagesize=landscape(letter))
        layout = self.determine_layout()

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
        col_w = BODY_W / n
        phase_h = self.phases_top_y - self.phases_bottom_y

        # Phase box background (rotated 90° CW)
        max_content_h = max(
            self.measure_phase_height(self.phases_grouped.get(pk, []), col_w - 4)
            for pk in phase_order
        )
        box_h = min(max_content_h + PHASE_BOX_H_PAD_TOP + PHASE_BOX_H_PAD_BOTTOM, phase_h)
        box_w = BODY_W
        box_x = X_MARGIN
        box_y = self.phases_top_y - box_h
        self._draw_phase_box(c, box_x, box_y, box_w, box_h, rotated=True)

        for i, phase_key in enumerate(phase_order):
            steps = self.phases_grouped.get(phase_key, [])
            x = X_MARGIN + (i * col_w)
            frame = Frame(x, self.phases_bottom_y, col_w - 4, phase_h, showBoundary=0)
            story = self._build_phase_story(phase_key, steps, layout='horizontal')
            frame.addFromList(story, c)

    def _draw_vertical_phases(self, c):
        phase_order = ['birdsong', 'daylight', 'evening']
        n = len(phase_order)
        phase_h = self.phases_top_y - self.phases_bottom_y
        row_h = phase_h / n
        phase_w = BODY_W - TRACK_PANEL_W if self.tracks else BODY_W

        # Phase box background (portrait orientation)
        from PIL import Image as PILImage
        sample_header = PHASE_HEADERS['birdsong']['short']
        pil_img = PILImage.open(sample_header)
        header_w = PHASE_HEADER_H * (pil_img.size[0] / pil_img.size[1])
        box_w = header_w + PHASE_BOX_V_EXTRA_W

        # Height based on stacked phase content + gaps
        total_content_h = sum(
            self.measure_phase_height(self.phases_grouped.get(pk, []), phase_w)
            for pk in phase_order
        )
        box_h = total_content_h + (n - 1) * PHASE_BOX_V_GAP + PHASE_BOX_H_PAD_TOP + PHASE_BOX_H_PAD_BOTTOM
        max_h = self.phases_top_y - BOTTOM_MARGIN
        if box_h > max_h:
            print(f"WARNING: Phase box height ({box_h:.1f}pts) exceeds available space ({max_h:.1f}pts), clamping.")
            box_h = max_h

        box_x = X_MARGIN
        box_y = self.phases_top_y - box_h
        self._draw_phase_box(c, box_x, box_y, box_w, box_h, rotated=False)

        for i, phase_key in enumerate(phase_order):
            steps = self.phases_grouped.get(phase_key, [])
            y = self.phases_bottom_y + ((n - 1 - i) * row_h)
            frame = Frame(X_MARGIN, y, phase_w, row_h - 4, showBoundary=0)
            story = self._build_phase_story(phase_key, steps, layout='vertical')
            frame.addFromList(story, c)



    def _build_phase_story(self, phase_key, steps, layout='horizontal'):
        from reportlab.platypus import Table, TableStyle

        story = []
        header_config = PHASE_HEADERS[phase_key]
        header_variant = 'long' if layout == 'horizontal' else 'short'
        header_path = header_config[header_variant]

        if os.path.exists(header_path):
            from PIL import Image as PILImage
            pil_img = PILImage.open(header_path)
            target_h = PHASE_HEADER_H
            aspect = pil_img.size[0] / pil_img.size[1]
            target_w = target_h * aspect
            img = Image(header_path, width=target_w, height=target_h)
            img.hAlign = 'LEFT'
            story.append(img)

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
            probe = Paragraph(markup, self.step_body_style)
            _, wrap_h = probe.wrap(BODY_W - text_w, 9999)
            extra_h = true_paragraph_height(probe, BODY_W - text_w) - wrap_h

            # DEBUG: red border around step
            debug_border = ('BOX', (0, 0), (-1, -1), 0.5, HexColor('#FF0000'))

            if single_step:
                # No number icon — indent text from left side of box
                t = Table([['', para]], colWidths=[SINGLE_STEP_INDENT, None])
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
                    t = Table([[svg_drawing, para]], colWidths=[TEXT_COL_X, None])
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

        return story

    def _draw_background(self, c):
        img_path = self.sheet.get_background_path()
        from reportlab.lib.utils import ImageReader
        img_reader = ImageReader(img_path)
        iw, ih = img_reader.getSize()

        if self.sheet.repeat_background_image:
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
        c.drawCentredString(PAGE_W / 2, self.title_bar_y + FACTION_NAME_Y_OFFSET, self.sheet.faction_name)

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

