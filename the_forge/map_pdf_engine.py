"""Print a Forge map: the map's art (or, without art, a grey board with its
rivers, paths and clearing circles) with the VP track, items, Root logo,
buildings, ruins and (fixed-suit maps) each clearing's suit printed on it, where
the map builder placed them.

Every position comes from the_keep/services/tts_map.print_layout, which the
builder's underlays use too, so the printed map matches what the designer laid
out and the TTS pieces land on their printed spots.
"""
import logging
from functools import lru_cache
from pathlib import Path

from reportlab.graphics import renderPDF
from reportlab.lib.colors import HexColor
from reportlab.lib.units import inch, mm
from reportlab.pdfgen import canvas as rl_canvas
from svglib.svglib import svg2rlg

from the_keep.services.tts_map import (CLEARING_OUTLINE, FORGE_FRAME, MAP_BLANK, MAX_ART_WIDTH, OFFICIAL_RATIO,
                                       PATH_COLOURS, PATH_DASH, PATH_DRAW_ORDER, PATH_GAP, PATH_LANES, PATH_STROKE,
                                       PRINT_DIR, RIVER_WIDTH, SUIT_LINE_CENTRE_MM, SUIT_LINE_RADIUS_MM, MapGeometry,
                                       path_lanes, print_art, print_layout)

from .pdf_engine import PRINT_DPI, _scaled_for_canvas, _tag_canvas_dpi

logger = logging.getLogger(__name__)

# A Root board: 22 in wide, at the official maps' shape.
PAGE_W = 22 * inch
PAGE_H = PAGE_W / OFFICIAL_RATIO
SUITS_DIR = Path(PRINT_DIR) / 'suits'
# Between a suit's line and its icon, which sits just outside it.
ICON_GAP = 1 * mm


@lru_cache(maxsize=None)
def _suit_drawing(suit, part):
    """The suit's 'line' or 'icon' SVG as a ReportLab drawing (None if missing)."""
    path = SUITS_DIR / f'root_map_{suit}_{part}.svg'
    if not path.exists():
        logger.warning("Forge map: no %s art for suit %r (%s)", part, suit, path.name)
        return None
    return svg2rlg(str(path))


class MapLayoutEngine:
    def __init__(self, forged_map):
        self.map = forged_map
        self.geometry = MapGeometry(*FORGE_FRAME)
        # Builder pixels (FORGE_FRAME) -> points.
        self.k = PAGE_W / FORGE_FRAME[0]

    def build(self, output_path, target_dpi=PRINT_DPI):
        c = rl_canvas.Canvas(output_path, pagesize=(PAGE_W, PAGE_H))
        c.setTitle(self.map.title)
        _tag_canvas_dpi(c, target_dpi)
        layout = print_layout(self.geometry, self.map.tts_builder or {})
        art = print_art()

        # Bottom to top. A map's own art already shows its clearings, paths
        # and rivers; without art they're printed on a grey board: rivers
        # (joined through their clearings, or run off the map's edge), then
        # paths, then the clearings' circles.
        if self.map.art:
            self._draw_image(c, self.map.art.path, (0, 0) + FORGE_FRAME)
        else:
            c.setFillColor(HexColor(MAP_BLANK))
            c.rect(0, 0, PAGE_W, PAGE_H, stroke=0, fill=1)
            for link in layout['river_links']:
                self._draw_path(c, {'kind': 'river', 'colour': PATH_COLOURS['river'], 'points': link},
                                layout['path_width'])
            # Rivers, then special paths, then paths, where they cross (stable within a kind).
            # A river that merges into another is printed cut back where its merge curve begins.
            for i, path in sorted(enumerate(layout['paths']), key=lambda ip: PATH_DRAW_ORDER.index(ip[1]['kind'])):
                if i in layout['river_trimmed']:
                    path = {**path, 'points': layout['river_trimmed'][i]}
                self._draw_path(c, path, layout['path_width'])
            for circle in layout['clearings']:
                self._draw_clearing(c, circle, layout['clearing_outline'])
        for suit in layout['suits']:
            self._draw_suit(c, suit)
        for building in layout['buildings']:
            name = 'root_map_ruin.png' if building['ruin'] else 'root_map_building.png'
            self._draw_image(c, str(Path(PRINT_DIR) / name), building['box'])
        # Always on top, Root logo highest: item track < VP track < logo.
        self._draw_image(c, art['items']['path'], layout['items'])
        self._draw_image(c, art['vp']['path'], layout['vp'])
        self._draw_image(c, art['logo']['path'], layout['logo'])
        c.showPage()
        c.save()

    def _rect(self, box):
        """A (x, y, w, h) box in builder pixels (y down) -> PDF points (y up)."""
        x, y, w, h = box
        return x * self.k, PAGE_H - (y + h) * self.k, w * self.k, h * self.k

    def _draw_image(self, c, path, box):
        x, y, w, h = self._rect(box)
        c.drawImage(_scaled_for_canvas(c, path, w, h), x, y, w, h, mask='auto')

    def _draw_path(self, c, path, width):
        """A path as PATH_LANES rows of pill-shaped dashes, every other row
        shifted half a dash: cobblestones. A river is one solid line. (As the
        builder draws them.)"""
        c.saveState()
        c.setStrokeColor(HexColor(path['colour']))
        c.setLineCap(1)                     # round
        c.setLineJoin(1)
        if path['kind'] == 'river':
            # Flat ends: a river starts right at the clearing's edge.
            c.setLineCap(0)
            c.setLineWidth(RIVER_WIDTH * width * self.k)
            self._polyline(c, path['points'])
        else:
            lane = width / PATH_LANES * self.k
            dash, gap = PATH_DASH * lane, PATH_GAP * lane
            c.setLineWidth(PATH_STROKE * lane)
            for i, points in enumerate(path_lanes(path['points'], width)):
                c.setDash([dash, gap], (dash + gap) / 2 if i % 2 else 0)
                self._polyline(c, points)
        c.restoreState()

    def _draw_clearing(self, c, circle, width):
        """A clearing's circle (centre to marker), as an outline: rivers and
        paths show through it."""
        x, y, r = circle
        c.saveState()
        c.setStrokeColor(HexColor(CLEARING_OUTLINE))
        c.setLineWidth(width * self.k)
        c.circle(x * self.k, PAGE_H - y * self.k, r * self.k, stroke=1, fill=0)
        c.restoreState()

    def _polyline(self, c, points):
        line = c.beginPath()
        x, y = points[0]
        line.moveTo(x * self.k, PAGE_H - y * self.k)
        for x, y in points[1:]:
            line.lineTo(x * self.k, PAGE_H - y * self.k)
        c.drawPath(line, stroke=1, fill=0)

    def _draw_suit(self, c, suit):
        """The suit's arc around the clearing, turned to the marker's side and
        scaled so its band runs along the clearing's circle, and its icon on the
        marker spot: always upright, and always the same size (the SVG's own,
        drawn for a Root board), whatever the clearing's size."""
        line = _suit_drawing(suit['suit'], 'line')
        icon = _suit_drawing(suit['suit'], 'icon')
        scale = suit['radius'] * self.k / (SUIT_LINE_RADIUS_MM * mm)
        if line is not None:
            cx, cy = suit['centre']
            # The arc's centre in the drawing (points, y up from its bottom edge).
            ax = SUIT_LINE_CENTRE_MM[0] * mm
            ay = line.height - SUIT_LINE_CENTRE_MM[1] * mm
            c.saveState()
            c.translate(cx * self.k, PAGE_H - cy * self.k)
            # The art's arc sits at 6 o'clock (90 degrees on the image, y down);
            # PDF angles turn the other way.
            c.rotate(90 - suit['angle'])
            c.scale(scale, scale)
            renderPDF.draw(line, c, -ax, -ay)
            c.restoreState()
        if icon is not None:
            # Just outside the arc, where the clearing's marker goes: out from
            # the marker spot (away from the clearing's centre) by half the
            # icon plus a gap, so it clears the line.
            mx, my = suit['marker']
            cx, cy = suit['centre']
            ux, uy = (mx - cx) / suit['radius'], (my - cy) / suit['radius']
            out = max(icon.width, icon.height) / 2 + ICON_GAP
            c.saveState()
            c.translate(mx * self.k + ux * out, PAGE_H - (my * self.k + uy * out))
            renderPDF.draw(icon, c, -icon.width / 2, -icon.height / 2)
            c.restoreState()


def render_map_pdf(forged_map, target_dpi=PRINT_DPI):
    from io import BytesIO
    buf = BytesIO()
    try:
        MapLayoutEngine(forged_map).build(buf, target_dpi=target_dpi)
        return buf.getvalue()
    finally:
        buf.close()


def preview_dpi(forged_map):
    """The DPI the map's image is rasterised at: always MAX_ART_WIDTH wide
    (186 dpi on the 22 in board), whatever the art's size. The printed suits,
    track, items and logo are vectors or large art, so smaller art still gets
    sharp clearing icons and lines; the art itself is never upscaled past its
    own pixels (it's only resampled to fit)."""
    return MAX_ART_WIDTH / (PAGE_W / 72)
