"""A map's setup card: a poker-sized card with the map's name, its setup
modifications, a diagram of the map (clearings, paths, rivers and forests of
little trees) and a legend for its special paths on the front; its special
rules on the back.

Shared by any map with the card fields (map_card, card_fingerprint,
card_image, card_2_image and their versions): a Forge map (ForgedMap) now, a
keep Map later. Nothing here reads a model's own fields beyond those: the
engine draws from a title, a builder layout (tts_builder) and the card's data,
and refresh_setup_card stores its images on whatever it's given.

The card's data (map_card):
    {"special_path_rule": str, "modifications": str, "raging_river": bool,
     "rules": [{"name": str, "text": str}]}
The special paths' name is the layout's special_path_name, and the card's
fixed wording is in the Forge's translations (pdf_engine.PDF_TEXT,
'map_setup_card'), in the map's language with English for anything missing.
"""
import hashlib
import json
import math
import re
from functools import lru_cache
from io import BytesIO
from types import SimpleNamespace

from .tts_map import PRINT_DIR, SUITS, river_extras, route_paths

SETUP_CARD_RENDER_VERSION = 13
CARD_DIR = PRINT_DIR / 'setup_card'

# ── The card's data ─────────────────────────────────────────────────────────
SPECIAL_RULE_MAX = 200
MODIFICATIONS_MAX = 300
RULES_MAX = 6
RULE_NAME_MAX = 40
RULE_TEXT_MAX = 300

_CONTROL = re.compile(r'[\x00-\x08\x0b-\x1f\x7f]')


def _text(value, limit):
    if not isinstance(value, str):
        return ''
    return _CONTROL.sub('', value).strip()[:limit]


def clean_setup_card(data):
    """The card's data, trimmed and held to its limits; empty rules dropped.
    (The stock Raging River rule isn't one of the rules: it's a switch.)"""
    data = data if isinstance(data, dict) else {}
    rules = []
    for rule in data.get('rules') if isinstance(data.get('rules'), list) else []:
        if not isinstance(rule, dict):
            continue
        name, text = _text(rule.get('name'), RULE_NAME_MAX), _text(rule.get('text'), RULE_TEXT_MAX)
        if name or text:
            rules.append({'name': name, 'text': text})
    return {
        'special_path_rule': _text(data.get('special_path_rule'), SPECIAL_RULE_MAX),
        'modifications': _text(data.get('modifications'), MODIFICATIONS_MAX),
        'raging_river': data.get('raging_river') is True,
        'rules': rules[:RULES_MAX],
    }


def layout_features(layout):
    """{'special': bool, 'river': bool}: which of the card's sections apply."""
    kinds = {p.get('kind') for p in (layout or {}).get('paths') or [] if isinstance(p, dict)}
    return {'special': 'special' in kinds, 'river': 'river' in kinds}


def applicable(layout, map_card):
    """The card's data with the sections the layout no longer has blanked (a
    special path rule without special paths, Raging River without rivers).
    They stay stored, so they come back with them."""
    card = clean_setup_card(map_card)
    features = layout_features(layout)
    if not features['special']:
        card['special_path_rule'] = ''
    if not features['river']:
        card['raging_river'] = False
    return card


def card_text(lang_code):
    """The card's fixed wording in the language (English for anything missing)."""
    from the_forge.pdf_engine import _pdf_text
    return _pdf_text('map_setup_card', lang_code)


def special_path_name(layout, lang_code):
    return (layout or {}).get('special_path_name') or card_text(lang_code).get('special_paths', '')


def _diagram_layout(layout):
    """The parts of a layout the card draws (not the logo, tracks, items or
    buildings): moving those mustn't make a new card."""
    layout = layout or {}
    out = {
        'clearings': [{'centre': c.get('centre'), 'number': c.get('number')} for c in layout.get('clearings') or []],
        'suits': _suits(layout),
        'forests': layout.get('forests') or [],
        'paths': layout.get('paths') or [],
    }
    if layout_features(layout)['special']:
        out['special_path_name'] = layout.get('special_path_name') or ''
    return out


def _suits(layout):
    """Each clearing's suit on a fixed-suit map (None for one without), else []."""
    layout = layout or {}
    if not layout.get('fixed'):
        return []
    return [c.get('suit') if c.get('suit') in SUITS else None for c in layout.get('clearings') or []]


def card_fingerprint(title, layout, map_card, *, frame, lang_code):
    """What a card is drawn from: md5 hex (32 characters, the field's size)."""
    payload = {
        'v': SETUP_CARD_RENDER_VERSION,
        'title': title or '',
        'layout': _diagram_layout(layout),
        'card': applicable(layout, map_card),
        'frame': [round(float(frame[0]), 3), round(float(frame[1]), 3)],
        'lang': lang_code,
        'text': card_text(lang_code),
    }
    return hashlib.md5(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


# ── Drawing ─────────────────────────────────────────────────────────────────
MARGIN = 10.0                 # text from the card's sides (points)
TITLE_SIZE = 15.0
TITLE_MIN_SIZE = 9.0
TITLE_BASELINE = 27.0         # from the card's top
BODY_TOP = 36.0               # the first line of text, from the card's top
BOTTOM = 14.0                 # clear of the border's bottom edge
BODY_SIZE = 9.0
BODY_MIN_SIZE = 8.0           # a back's rules shrink to fit, this far
LEADING = 1.2
PARAGRAPH_GAP = 3.0
DIAGRAM_GAP = 4.0             # between the diagram and the text above / below it
DIAGRAM_MIN_H = 110.0         # the front's text must leave the diagram this much
DIAGRAM_MAX_H = 140.0         # the diagram is never taller than this (it's centred in any space left over);
                              # 200 is more than a front ever has, so as it stands it never limits it
DIAGRAM_MAX_STRETCH = 1.4     # a diagram may be stretched this much (either way) to fill its space

CLEARING_R = 13.29            # map_setup_clearing.svg's own size (9.38 mm across)
CLEARING_MIN_SCALE = 0.7      # crowded maps: smaller clearings, down to this share
CLEARING_FILL = '#f8e3a4'     # the card's paper (map_setup_card_border.svg)
NUMBER_FONT = 'Baskerville-Bold'
NUMBER_SIZE = 17.0
NUMBER_SUIT_NUDGE = 2       # a fixed-suit clearing's number, this much lower: clear of its suit icon
INK = '#110f0d'
PATH_WIDTH = 2.0
PATH_DASH = (2.0, 2.0)
SPECIAL_COLOUR = '#ed2731'    # tts_map.PATH_COLOURS['special']
RIVER_COLOUR = '#978ead'      # the example card's river: its blue, half over the paper
RIVER_WIDTH = 3.25           # half the example card's band
RAGING_RIVER_WIDTH = RIVER_WIDTH * 2
SUIT_ICON_H = 11.5            # a fixed-suit map's suit icon, on top of its clearing's circle
SUIT_ICONS = {'fox': 'fox_icon_black_outline.svg', 'rabbit': 'rabbit_icon_black_border.svg',
              'mouse': 'mouse_icon_black_outline.svg', 'bird': 'bird_icon_black_outline.svg',
              'city': 'city_icon_black_outline.svg'}
SUIT_ICON_SCALE = {'city': 1.3}   # three suits in one icon: drawn larger to read
LEGEND_INSET = 4.0            # the legend's special path sample, in from the card's text margin
LEGEND_SWATCH = 10.0          # its length
LEGEND_INDENT = LEGEND_INSET + LEGEND_SWATCH + 1.0   # then ": name", just after it

TREE_H = 5.3                  # map_setup_tree.svg, drawn this tall
TREE_SPACING = 8.0            # the trees' hex grid: the distance between neighbouring trees before they're nudged
TREE_GAP = 1.2                # from a clearing, a path (or a raging river) and the diagram's edge
TREE_JITTER = 1.8             # each tree nudged up to this far (any direction), so the grid doesn't show
TREE_SKIP = 0.08              # this share of spots left empty, for a few natural gaps


def _card_size():
    from the_forge.pdf_engine import CARD_SLOT_H, CARD_SLOT_W
    return CARD_SLOT_W, CARD_SLOT_H


@lru_cache(maxsize=None)
def _svg(name):
    from svglib.svglib import svg2rlg
    return svg2rlg(str(CARD_DIR / name))


def _escape(text):
    from the_forge.pdf_engine import _xml_escape
    return _xml_escape(text or '')


def _italic_parentheticals(markup):
    """Parentheticals in italic, as the printed cards set them."""
    return re.sub(r'\([^()]*\)', lambda m: f'<i>{m.group(0)}</i>', markup)


def run_in(heading, text):
    """'<b>Heading.</b> text', escaped, with its parentheticals in italic."""
    heading = (heading or '').strip()
    parts = []
    if heading:
        if heading[-1] not in '.!?:':
            heading += '.'
        parts.append(f'<b>{_escape(heading)}</b>')
    if text:
        parts.append(_italic_parentheticals(_escape(text)))
    return ' '.join(parts)


def _style(size, name='card'):
    from reportlab.lib.colors import HexColor
    from reportlab.lib.styles import ParagraphStyle
    return ParagraphStyle(name, fontName='Baskerville', fontSize=size, leading=size * LEADING,
                          textColor=HexColor(INK))


def _paragraph(markup, size):
    from reportlab.platypus import Paragraph
    return Paragraph(markup, _style(size))


def _height(paragraphs, width):
    total = 0.0
    for n, p in enumerate(paragraphs):
        total += p.wrap(width, 10_000)[1] + (PARAGRAPH_GAP if n else 0)
    return total


def _segments(points):
    return list(zip(points, points[1:]))


def _seg_distance(p, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    length2 = dx * dx + dy * dy
    t = 0.0 if not length2 else max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / length2))
    return math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy)


def _cross(o, a, b):
    return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])


def _segments_cross(p, q, a, b):
    d1, d2 = _cross(a, b, p), _cross(a, b, q)
    d3, d4 = _cross(p, q, a), _cross(p, q, b)
    return (d1 > 0) != (d2 > 0) and (d3 > 0) != (d4 > 0)


def forest_trees(frame, clearings, lines, forests, avoid=()):
    """Where a card's trees go: (x, y) of each, filling each forest's area.

    frame: (width, height), its corner at 0, 0. clearings: (x, y, r) each.
    lines: (polyline, half width) of everything that bounds a forest (paths,
    special paths, and a raging river's lines). forests: the forest points.
    avoid: (x, y, r) discs trees keep clear of but that don't bound a forest
    (the suit icons).

    Works on the trees' own hex grid, in two parts. The areas: neighbouring
    spots are joined unless a line or a clearing lies between them, so only
    those bound a forest; a narrow passage (between an icon and a path, say)
    still joins the two sides. The trees: each forest's area gets one on every
    spot with room for it (clear of the clearings, lines, avoided discs and the
    frame's edge), less a few left empty. Two forests in one area fill it once."""
    width, height = frame
    tree_half = TREE_H / 2
    row_h = TREE_SPACING * math.sqrt(3) / 2
    edge = tree_half + TREE_GAP
    segments = [(a, b, half) for points, half in lines for a, b in _segments(points)]

    def noise(i, j):
        """A spot's nudge (any direction, up to TREE_JITTER) and whether it's
        left empty: from its grid position, so a card always draws the same."""
        h = hashlib.md5(f'{i},{j}'.encode()).digest()
        angle, reach = h[0] / 256 * 2 * math.pi, math.sqrt(h[1] / 255) * TREE_JITTER
        return (math.cos(angle) * reach, math.sin(angle) * reach), h[2] / 256 < TREE_SKIP

    def has_room(p):
        if not (edge <= p[0] <= width - edge and edge <= p[1] <= height - edge):
            return False
        if any(math.hypot(p[0] - cx, p[1] - cy) < r + TREE_GAP + tree_half for cx, cy, r in (*clearings, *avoid)):
            return False
        return not any(_seg_distance(p, a, b) < half + TREE_GAP + tree_half for a, b, half in segments)

    # Every spot in the frame that isn't on a clearing or a line joins up the
    # areas; `drawn` are those that also get a tree.
    nodes, drawn = {}, set()
    rows = int(height / row_h) + 2
    cols = int(width / TREE_SPACING) + 2
    for j in range(-1, rows):
        for i in range(-1, cols):
            x = edge + i * TREE_SPACING + (TREE_SPACING / 2 if j % 2 else 0)
            y = edge + j * row_h
            (dx, dy), skip = noise(i, j)
            p = (x + dx, y + dy)
            if not (0 <= p[0] <= width and 0 <= p[1] <= height):
                continue
            if any(math.hypot(p[0] - cx, p[1] - cy) < r for cx, cy, r in clearings):
                continue
            if any(_seg_distance(p, a, b) < half for a, b, half in segments):
                continue
            nodes[(i, j)] = p
            if not skip and has_room(p):
                drawn.add((i, j))

    def blocked(p, q):
        if any(_seg_distance((cx, cy), p, q) < r for cx, cy, r in clearings):
            return True
        lo_x, hi_x, lo_y, hi_y = min(p[0], q[0]), max(p[0], q[0]), min(p[1], q[1]), max(p[1], q[1])
        for a, b, _half in segments:
            if max(a[0], b[0]) < lo_x or min(a[0], b[0]) > hi_x or max(a[1], b[1]) < lo_y or min(a[1], b[1]) > hi_y:
                continue
            if _segments_cross(p, q, a, b):
                return True
        return False

    def neighbours(i, j):
        shift = j % 2      # odd rows sit half a step right
        for di, dj in ((-1, 0), (1, 0), (-1 + shift, -1), (shift, -1), (-1 + shift, 1), (shift, 1)):
            yield i + di, j + dj

    filled = set()
    for fx, fy in forests:
        open_nodes = sorted(nodes, key=lambda k: math.hypot(nodes[k][0] - fx, nodes[k][1] - fy))
        # The nearest spot the forest point can reach without crossing a line.
        start = next((k for k in open_nodes[:12] if not blocked((fx, fy), nodes[k])), None)
        if start is None or start in filled:
            continue
        stack = [start]
        filled.add(start)
        while stack:
            k = stack.pop()
            for n in neighbours(*k):
                if n in nodes and n not in filled and not blocked(nodes[k], nodes[n]):
                    filled.add(n)
                    stack.append(n)
    return [nodes[k] for k in sorted(filled & drawn, key=lambda k: (k[1], k[0]))]


class _Diagram:
    """The map, laid out in the diagram's frame (points, y down, its corner at
    0, 0): clearings, paths routed as on the printed map, rivers (run off to
    the frame's edge and merged into each other as on the printed map) and the
    forests' trees."""

    def __init__(self, layout, frame, box, raging):
        self.box_w, self.box_h = box
        clearings = layout.get('clearings') or []
        fw, fh = frame
        points = [(c['centre'][0] * fw, c['centre'][1] * fh) for c in clearings]
        forests = [(f[0] * fw, f[1] * fh) for f in layout.get('forests') or []]
        everything = points + forests
        self.r = CLEARING_R
        self.river_width = RAGING_RIVER_WIDTH if raging else RIVER_WIDTH
        self.suits = _suits(layout)
        pad = CLEARING_R + 2
        # Room above the top clearings for their suit icons.
        pad_top = pad + max((SUIT_ICON_H * SUIT_ICON_SCALE.get(s, 1) / 2 for s in self.suits if s), default=0)
        inner_w, inner_h = self.box_w - 2 * pad, self.box_h - pad - pad_top
        if not points or inner_w <= 0 or inner_h <= 0:    # (no room: text saved before the layout changed)
            self.height = 0
            self.clearings, self.numbers, self.paths, self.rivers, self.trees = [], [], [], [], []
            return
        xs, ys = [p[0] for p in everything], [p[1] for p in everything]
        min_x, min_y = min(xs), min(ys)
        span_x, span_y = max(xs) - min_x, max(ys) - min_y
        sx = inner_w / span_x if span_x else None
        sy = inner_h / span_y if span_y else None
        # One scale (the board's shape), stretched a little either way to fill.
        if sx and sy:
            s = min(sx, sy)
            sx, sy = min(sx, s * DIAGRAM_MAX_STRETCH), min(sy, s * DIAGRAM_MAX_STRETCH)
        sx = sx or sy or 1.0
        sy = sy or sx
        used_h = span_y * sy + pad + pad_top
        self.height = min(self.box_h, used_h)
        ox = (self.box_w - span_x * sx) / 2
        oy = pad_top + (self.height - pad - pad_top - span_y * sy) / 2

        def place(p):
            return (ox + (p[0] - min_x) * sx, oy + (p[1] - min_y) * sy)

        centres = [place(p) for p in points]
        # Crowded clearings: smaller, so neighbours don't overlap.
        nearest = min((math.dist(a, b) for n, a in enumerate(centres) for b in centres[n + 1:]), default=None)
        if nearest is not None and nearest < 2 * CLEARING_R + 3:
            self.r = CLEARING_R * max(CLEARING_MIN_SCALE, (nearest - 3) / (2 * CLEARING_R))
        self.clearings = [(x, y, self.r) for x, y in centres]
        self.numbers = [c.get('number') for c in clearings]
        frame_obj = SimpleNamespace(width=self.box_w, height=self.height)

        # Routed as on the printed map (straight, unless that would cross
        # another clearing: then bent round it; a river and a path between
        # the same clearings bow apart), and cut at the clearings' circles.
        paths = layout.get('paths') or []
        ordered = route_paths(paths, self.clearings, RIVER_WIDTH / 2)
        width = self.river_width
        extras = river_extras(paths, ordered, self.clearings, width, frame_obj, join_clearings=False)
        # A river runs on into its clearings' centres (under them), so two
        # meeting at a clearing read as one, however wide.
        self.rivers = []
        for i, s in enumerate(ordered):
            if s['kind'] != 'river':
                continue
            points = list(extras['trimmed'].get(i, s['points']))
            if 'c' in paths[i]['a']:
                points.insert(0, centres[paths[i]['a']['c']])
            if 'c' in paths[i]['b']:
                points.append(centres[paths[i]['b']['c']])
            self.rivers.append(points)
        self.rivers += extras['links']
        self.paths = [s for s in ordered if s['kind'] != 'river']

        lines = [(s['points'], PATH_WIDTH / 2) for s in self.paths]
        if raging:
            lines += [(points, width / 2) for points in self.rivers]
        # Trees keep clear of the suit icons too (a disc round each, on top of its clearing).
        icons = [(x, y - r, SUIT_ICON_H * SUIT_ICON_SCALE.get(suit, 1) * self.r / CLEARING_R / 2)
                 for (x, y, r), suit in zip(self.clearings, self.suits) if suit]
        self.trees = forest_trees((self.box_w, self.height), self.clearings, lines,
                                  [place(f) for f in forests], avoid=icons)


class MapSetupCardEngine:
    """Draws a map's setup card: draw_front(c) / draw_back(c) on a page the
    card's size (CARD_SLOT_W x CARD_SLOT_H), or render() both to PDF bytes.

    title: the map's name. layout: its builder layout (tts_builder). map_card:
    the card's data. frame: the (width, height) the layout's 0-1 coordinates
    are fractions of (FORGE_FRAME for a Forge map, the board image's size for
    a keep Map). lang_code: the card's fixed wording's language."""

    def __init__(self, title, layout, map_card, *, frame, lang_code='en'):
        import the_forge.pdf_engine  # noqa: F401  (registers the card's fonts)
        self.title = title or ''
        self.layout = layout or {}
        self.card = applicable(self.layout, map_card)
        self.frame = frame
        self.lang_code = lang_code
        self.text = card_text(lang_code)
        self.width, self.height = _card_size()
        self.text_w = self.width - 2 * MARGIN
        # The setup modifications go on the back, before the rules; only if
        # they don't fit there, but do on the front (above the diagram, leaving
        # it room), on the front. Fitting nowhere, they stay on the back, and
        # fits() says so.
        self.modifications_on_back = bool(self.card['modifications'])
        if self.modifications_on_back and self._back_size() is None:
            self.modifications_on_back = False
            if not (self._front_fits() and self._back_size() is not None):
                self.modifications_on_back = True

    # ── What goes on each side ──
    @property
    def raging(self):
        return self.card['raging_river']

    def back_items(self):
        """(heading, text) of each paragraph on the back: the setup
        modifications (when moved there), what the special paths do, the stock
        Raging River rule, then the rules."""
        items = []
        if self.modifications_on_back:
            items.append((self.text.get('modifications', ''), self.card['modifications']))
        if self.card['special_path_rule']:
            items.append((special_path_name(self.layout, self.lang_code), self.card['special_path_rule']))
        if self.raging:
            items.append((self.text.get('raging_river', ''), self.text.get('raging_river_text', '')))
        items += [(rule['name'], rule['text']) for rule in self.card['rules']]
        return items

    @property
    def has_back(self):
        return bool(self.back_items())

    def _front_top(self, size=BODY_SIZE):
        paragraphs = []
        if self.card['modifications'] and not self.modifications_on_back:
            paragraphs.append(_paragraph(run_in(self.text.get('modifications', ''), self.card['modifications']), size))
        return paragraphs

    def _legend(self, size=BODY_SIZE):
        if not layout_features(self.layout)['special']:
            return None
        # Just the name beside its red line: what they do is a rule, on the back.
        return _paragraph(': ' + _escape(special_path_name(self.layout, self.lang_code)), size)

    def _legend_width(self):
        return self.text_w - LEGEND_INDENT

    def _front_space(self):
        """(top text height, legend height, the diagram's height)."""
        top = _height(self._front_top(), self.text_w)
        legend = self._legend()
        legend_h = legend.wrap(self._legend_width(), 10_000)[1] if legend else 0.0
        available = self.height - BODY_TOP - BOTTOM - top - legend_h
        available -= (DIAGRAM_GAP if top else 0) + (DIAGRAM_GAP if legend_h else 0)
        return top, legend_h, available

    def _back_size(self):
        """The largest size (BODY_SIZE down to BODY_MIN_SIZE) the back's rules fit at, or None."""
        available = self.height - BODY_TOP - BOTTOM
        size = BODY_SIZE
        while size >= BODY_MIN_SIZE - 1e-6:
            if _height([_paragraph(run_in(h, t), size) for h, t in self.back_items()], self.text_w) <= available:
                return size
            size -= 0.25
        return None

    def _front_fits(self):
        return self._front_space()[2] >= DIAGRAM_MIN_H

    def fits(self):
        """{'front': bool, 'back': bool}: the front leaves the diagram
        DIAGRAM_MIN_H, and the back's text (the setup modifications too, unless
        they've moved to the front) fits at BODY_MIN_SIZE or more."""
        return {'front': self._front_fits(),
                'back': not self.has_back or self._back_size() is not None}

    # ── Drawing ──
    def _border_and_title(self, c):
        from reportlab.graphics import renderPDF
        from reportlab.lib.colors import HexColor
        from reportlab.pdfbase.pdfmetrics import stringWidth
        border = _svg('map_setup_card_border.svg')
        c.saveState()
        c.scale(self.width / border.width, self.height / border.height)
        renderPDF.draw(border, c, 0, 0)
        c.restoreState()
        size = TITLE_SIZE
        while size > TITLE_MIN_SIZE and stringWidth(self.title, 'Luminari', size) > self.text_w:
            size -= 0.5
        c.setFillColor(HexColor(INK))
        c.setFont('Luminari', size)
        c.drawCentredString(self.width / 2, self.height - TITLE_BASELINE, self.title)

    def _draw_paragraphs(self, c, paragraphs, top, width, x=MARGIN):
        """Paragraphs down from `top` (from the card's top); returns where they end."""
        y = top
        for n, p in enumerate(paragraphs):
            if n:
                y += PARAGRAPH_GAP
            h = p.wrap(width, 10_000)[1]
            p.drawOn(c, x, self.height - y - h)
            y += h
        return y

    def draw_front(self, c):
        from reportlab.lib.colors import HexColor
        self._border_and_title(c)
        top_paragraphs = self._front_top()
        top_h, legend_h, available = self._front_space()
        y = self._draw_paragraphs(c, top_paragraphs, BODY_TOP, self.text_w)
        if top_paragraphs:
            y += DIAGRAM_GAP
        box_h = min(max(available, 0), DIAGRAM_MAX_H)
        diagram = _Diagram(self.layout, self.frame, (self.width - 2 * MARGIN, box_h), self.raging)
        # The diagram, centred in its space (all of it, when DIAGRAM_MAX_H holds it smaller).
        frame_top = y + (max(available, 0) - diagram.height) / 2
        self._draw_diagram(c, diagram, MARGIN, frame_top)
        legend = self._legend()
        if legend is not None:
            legend_top = self.height - BOTTOM - legend_h
            legend.wrap(self._legend_width(), 10_000)
            x = MARGIN + LEGEND_INDENT
            legend.drawOn(c, x, self.height - legend_top - legend_h)
            # The swatch, beside the legend's first line.
            line_y = self.height - legend_top - BODY_SIZE * LEADING / 2 - 1
            c.saveState()
            c.setStrokeColor(HexColor(SPECIAL_COLOUR))
            c.setLineWidth(PATH_WIDTH)
            c.setDash(list(PATH_DASH), 0)
            start = MARGIN + LEGEND_INSET
            c.line(start, line_y, start + LEGEND_SWATCH, line_y)
            c.restoreState()

    def draw_back(self, c):
        self._border_and_title(c)
        items = self.back_items()
        size = self._back_size()
        if size is not None:
            self._draw_paragraphs(c, [_paragraph(run_in(h, t), size) for h, t in items], BODY_TOP, self.text_w)
            return
        # Too long (saved before its layout changed, say): as much as fits, then "…".
        available = self.height - BODY_TOP - BOTTOM - BODY_MIN_SIZE * LEADING - PARAGRAPH_GAP
        kept = []
        for h, t in items:
            trial = kept + [_paragraph(run_in(h, t), BODY_MIN_SIZE)]
            if _height(trial, self.text_w) > available:
                break
            kept = trial
        kept.append(_paragraph('…', BODY_MIN_SIZE))
        self._draw_paragraphs(c, kept, BODY_TOP, self.text_w)

    def _draw_diagram(self, c, diagram, left, top):
        """The diagram, its frame's corner at (left, top) from the card's top left."""
        from reportlab.lib.colors import HexColor
        if not diagram.clearings:
            return
        H = self.height

        def at(p):
            return left + p[0], H - top - p[1]

        def polyline(points):
            path = c.beginPath()
            path.moveTo(*at(points[0]))
            for p in points[1:]:
                path.lineTo(*at(p))
            c.drawPath(path, stroke=1, fill=0)

        c.saveState()
        clip = c.beginPath()
        clip.rect(left, H - top - diagram.height, diagram.box_w, diagram.height)
        c.clipPath(clip, stroke=0, fill=0)

        # Rivers (trees go over them, unless the river is raging: then they keep clear).
        c.saveState()
        c.setStrokeColor(HexColor(RIVER_COLOUR))
        c.setLineWidth(diagram.river_width)
        c.setLineCap(1)       # round: ends under a clearing or past the frame's (clipped) edge
        c.setLineJoin(1)
        for points in diagram.rivers:
            if len(points) >= 2:
                polyline(points)
        c.restoreState()

        self._draw_trees(c, [at(p) for p in diagram.trees])

        # Special paths, then paths (tts_map.PATH_DRAW_ORDER).
        for kind, colour in (('special', SPECIAL_COLOUR), ('path', INK)):
            c.saveState()
            c.setStrokeColor(HexColor(colour))
            c.setLineWidth(PATH_WIDTH)
            c.setDash(list(PATH_DASH), 0)
            for shape in diagram.paths:
                if shape['kind'] == kind and shape['points'][0] != shape['points'][-1]:
                    polyline(shape['points'])
            c.restoreState()

        # Clearings: the paper inside, the ring, the number.
        from reportlab.graphics import renderPDF
        ring = _svg('map_setup_clearing.svg')
        scale = 2 * diagram.r / ring.width
        number_size = NUMBER_SIZE * diagram.r / CLEARING_R
        for (x, y, r), number in zip(diagram.clearings, diagram.numbers):
            cx, cy = at((x, y))
            c.saveState()
            c.setFillColor(HexColor(CLEARING_FILL))
            c.circle(cx, cy, r, stroke=0, fill=1)
            c.translate(cx - r, cy - r)
            c.scale(scale, scale)
            renderPDF.draw(ring, c, 0, 0)
            c.restoreState()
            if isinstance(number, int):
                nudge = NUMBER_SUIT_NUDGE * diagram.r / CLEARING_R if any(diagram.suits) else 0
                c.setFillColor(HexColor(INK))
                c.setFont(NUMBER_FONT, number_size)
                c.drawCentredString(cx, cy - number_size * 0.33 - nudge, str(number))
        # Fixed suits: each clearing's suit icon, centred on the top of its
        # circle (over any neighbour it reaches, so drawn after them all).
        for (x, y, r), suit in zip(diagram.clearings, diagram.suits):
            if not suit:
                continue
            icon = _svg(f'suits/{SUIT_ICONS[suit]}')
            icon_h = SUIT_ICON_H * SUIT_ICON_SCALE.get(suit, 1) * diagram.r / CLEARING_R
            k = icon_h / icon.height
            cx, cy = at((x, y - r))
            c.saveState()
            c.translate(cx - icon.width * k / 2, cy - icon_h / 2)
            c.scale(k, k)
            renderPDF.draw(icon, c, 0, 0)
            c.restoreState()
        c.restoreState()

    def _draw_trees(self, c, spots):
        """Each tree is the same PDF form, drawn once per canvas and placed by reference."""
        if not spots:
            return
        from reportlab.graphics import renderPDF
        tree = _svg('map_setup_tree.svg')
        scale = TREE_H / tree.height
        w = tree.width * scale
        if not getattr(c, '_map_setup_tree', False):
            c.beginForm('mapSetupTree')
            c.scale(scale, scale)
            renderPDF.draw(tree, c, 0, 0)
            c.endForm()
            c._map_setup_tree = True
        for x, y in spots:
            c.saveState()
            c.translate(x - w / 2, y - TREE_H / 2)
            c.doForm('mapSetupTree')
            c.restoreState()

    def render(self):
        """(front PDF bytes, back PDF bytes or None), a page each."""
        from reportlab.pdfgen import canvas as rl_canvas

        def page(draw):
            buf = BytesIO()
            c = rl_canvas.Canvas(buf, pagesize=(self.width, self.height))
            c.setTitle(self.title)
            draw(c)
            c.showPage()
            c.save()
            return buf.getvalue()

        return page(self.draw_front), (page(self.draw_back) if self.has_back else None)


def setup_card_fits(title, layout, map_card, *, frame, lang_code):
    """{'front': bool, 'back': bool} (measured, nothing drawn)."""
    return MapSetupCardEngine(title, layout, map_card, frame=frame, lang_code=lang_code).fits()


# ── Storing a card on a map ─────────────────────────────────────────────────
def _stored_bytes(field):
    if not field:
        return None
    try:
        field.open('rb')
        try:
            return field.read()
        finally:
            field.close()
    except (OSError, ValueError):
        return None


def refresh_setup_card(owner, *, title, layout, lang_code, frame):
    """Render `owner`'s setup card if it's out of date, and store it: any map
    with map_card, card_fingerprint, card_image / card_2_image and their
    versions. Only an image that actually changed is saved (and its version
    bumped): a back that appears, changes or goes. Returns True if anything
    changed. Renders: call it inside render_slot()."""
    from django.core.files.base import ContentFile
    from django.db.models import F

    from the_forge.pdf_engine import CARD_PREVIEW_DPI, pdf_bytes_to_webp_bytes

    fingerprint = card_fingerprint(title, layout, owner.map_card, frame=frame, lang_code=lang_code)
    if owner.card_image and owner.card_fingerprint == fingerprint:
        return False
    front_pdf, back_pdf = MapSetupCardEngine(title, layout, owner.map_card, frame=frame,
                                             lang_code=lang_code).render()
    front = pdf_bytes_to_webp_bytes(front_pdf, dpi=CARD_PREVIEW_DPI, quality=90)
    back = pdf_bytes_to_webp_bytes(back_pdf, dpi=CARD_PREVIEW_DPI, quality=90) if back_pdf else None

    updates = {'card_fingerprint': fingerprint}
    if front != _stored_bytes(owner.card_image):
        owner.card_image.save('card_front.webp', ContentFile(front), save=False)
        updates['card_image'] = owner.card_image.name
        updates['card_image_version'] = F('card_image_version') + 1
    if back is None:
        if owner.card_2_image:
            owner.card_2_image.delete(save=False)
            updates['card_2_image'] = ''
            updates['card_2_image_version'] = F('card_2_image_version') + 1
    elif back != _stored_bytes(owner.card_2_image):
        owner.card_2_image.save('card_back.webp', ContentFile(back), save=False)
        updates['card_2_image'] = owner.card_2_image.name
        updates['card_2_image_version'] = F('card_2_image_version') + 1
    type(owner).objects.filter(pk=owner.pk).update(**updates)
    owner.card_fingerprint = fingerprint
    owner.refresh_from_db(fields=['card_image_version', 'card_2_image_version'])
    return True
