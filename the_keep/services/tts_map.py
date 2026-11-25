"""Generate a Map post's TTS JSON from the map builder's layout (Map.tts_builder).

The map is built the way the RTT mod builds its six official maps (measured from
its coordinator, Workshop 3800311405), so it behaves like one on the RTT table:

- The board is a Custom_Token at scale 12.9794, rotated 180 degrees, carrying
  the snap points: the 3 x 31 VP track the Box Score reads, the 2 x 6 item
  grid, one "Enclave" snap per suit-marker spot, and one per building slot.
- Clearing centres and forests aren't snap points: they go in BoardLayout,
  like the coordinator's own tables (see tts/BOARD_LAYOUT.md).
- Item tiles, ruin tiles, suit markers and clearing number tiles are copied
  from tts/rtt_map_pieces.json (art in the_keep/static/images/tts/assets/).

The builder stores points in 0-1 image coordinates (u right, v down).
"""
import copy
import json
import math
from functools import lru_cache
from pathlib import Path

from django.conf import settings
from django.templatetags.static import static

from .tts import SiteURLRequest, _guids, generate_tts_guid, tts_image_url, wrap_tts_save

PIECES_PATH = Path(settings.BASE_DIR) / 'tts' / 'rtt_map_pieces.json'
ASSETS_DIR = Path(settings.BASE_DIR) / 'the_keep' / 'static' / 'images' / 'tts' / 'assets'
ASSETS_STATIC = 'images/tts/assets/'

BOARD_SCALE = 12.9794          # every official board
# Local units from the board's centre to its image's left/right edge. Fitted on
# all six official boards (their VP track snaps against the track printed on
# their art): 1.947-1.963, mean 1.957. An error here grows towards the edges.
HALF_WIDTH = 1.957
SNAP_Y = 0.05

# VP track: 31 cells (scores 0-30) in 3 rows, centred on the board, at the
# spacing of the official boards' snaps (0.1202-0.1209), which sit on the track
# printed on their art.
TRACK_CELLS = 31
TRACK_STEP = 0.1205
TRACK_X0 = -TRACK_STEP * (TRACK_CELLS - 1) / 2
TRACK_ROW_GAP = 0.112
DEFAULT_TRACK_Z = 1.632        # centre of the track printed on the official boards (local z)
DEFAULT_ITEMS = (1.078, -1.673)   # the Summer map's top-left item slot (local x, z)

# A clearing's number tile is a round tile at scale 1.0; it sits just outside
# the clearing circle.
NUMBER_TILE_RADIUS = 1.0       # table units

# Heights above the board, as in the official catalogue.
HEIGHT_MARKER = 0.11
HEIGHT_TILE = 0.05
HEIGHT_NUMBER = 0.03
BOARD_Y = 1.0

# 'city' is the Lost City: a clearing that counts as fox, rabbit and mouse.
SUITS = ('fox', 'rabbit', 'mouse', 'bird', 'city')
# GM note on ruins past the fourth, whose items the loader draws at spawn.
RUIN_EXTRA_NOTE = 'rdb:ruin:extra'
SHUFFLED_SUITS = ('fox', 'rabbit', 'mouse')


@lru_cache(maxsize=1)
def pieces():
    return json.loads(PIECES_PATH.read_text())


@lru_cache(maxsize=1)
def marker_templates():
    """Suit marker templates by suit: the template file's (fox, rabbit, mouse,
    bird), plus one for every other "<suit>_marker.png" in the assets folder
    (e.g. city), built from the fox marker with that texture -- every marker
    shares one mesh, only the texture differs."""
    markers = dict(pieces()['markers'])
    for path in ASSETS_DIR.glob('*_marker.png'):
        suit = path.name[:-len('_marker.png')]
        if suit not in markers:
            marker = copy.deepcopy(markers['fox'])
            marker['CustomMesh']['DiffuseURL'] = 'static:' + ASSETS_STATIC + path.name
            markers[suit] = marker
    return markers


@lru_cache(maxsize=1)
def number_templates():
    """Number tile templates by number: the mod's 1-12 from the template file,
    plus one for every other "<n>_number.png" in the assets folder (built from
    tile 1 with that image), so adding a number only takes its image."""
    numbers = {int(n): obj for n, obj in pieces()['numbers'].items()}
    base = numbers[min(numbers)]
    for path in ASSETS_DIR.glob('*_number.png'):
        prefix = path.name[:-len('_number.png')]
        if prefix.isdigit() and int(prefix) not in numbers:
            tile = copy.deepcopy(base)
            tile['CustomImage']['ImageURL'] = 'static:' + ASSETS_STATIC + path.name
            numbers[int(prefix)] = tile
    return dict(sorted(numbers.items()))


def highest_number():
    """The highest clearing number with a tile, counting up from 1 without gaps."""
    n = 0
    while n + 1 in number_templates():
        n += 1
    return n


class MapGeometry:
    """Image (u, v in 0-1) <-> board-local (x, z) <-> table offsets.

    Image left is local +x and image top is local -z (the board lies rotated
    180 degrees); a board image spans HALF_WIDTH local units either side of centre
    across its width. Table offsets are from the board's centre, along the
    table's axes, for the board at its 180-degree rotation."""

    def __init__(self, width, height):
        self.width, self.height = width, height
        self.hx = HALF_WIDTH
        self.hz = HALF_WIDTH * height / width
        # Pixels per local unit, the same along both axes.
        self.px_per_local = width / (2 * self.hx)

    def to_local(self, u, v):
        return self.hx * (1 - 2 * u), self.hz * (2 * v - 1)

    def to_image(self, x, z):
        return (1 - x / self.hx) / 2, (1 + z / self.hz) / 2

    def to_table(self, u, v):
        x, z = self.to_local(u, v)
        return -x * BOARD_SCALE, -z * BOARD_SCALE

    def to_pixels(self, u, v):
        return u * self.width, v * self.height

    def from_pixels(self, px, py):
        return px / self.width, py / self.height

    def defaults(self):
        """A new layout: the Summer map's track and item grid on this image."""
        return {
            'version': 1,
            'track_y': self.to_image(0, DEFAULT_TRACK_Z)[1],
            'items': list(self.to_image(*DEFAULT_ITEMS)),
            'clearings': [], 'forests': [], 'buildings': [],
        }

    def number_tile_centre(self, clearing):
        """Where a clearing's number tile sits: outside the clearing circle
        (centre -> marker), touching it, at the clearing's number_angle."""
        cx, cy = self.to_pixels(*clearing['centre'])
        mx, my = self.to_pixels(*clearing['marker'])
        r = math.hypot(mx - cx, my - cy)
        tile = NUMBER_TILE_RADIUS / BOARD_SCALE * self.px_per_local
        a = clearing.get('number_angle', math.atan2(cy - my, cx - mx))
        return self.from_pixels(cx + (r + tile) * math.cos(a), cy + (r + tile) * math.sin(a))


def number_tile_radius_px(geometry):
    return NUMBER_TILE_RADIUS / BOARD_SCALE * geometry.px_per_local


# ── The builder page (shared by the Keep and the Forge) ─────────────────────

MARKER_SVG = 'images/map_builder/clearing_marker.svg'
FOREST_ICON = 'pdf/maps/root_map_green_forest.svg'   # the summary card's forest icon (a printed map's forest)
RUIN_ICON = 'images/tts/assets/ruin.png'             # ...and its ruin (the builder's ruin tile)

# The official boards' shape (about 1.09 wide to 1 tall). Forge maps are cropped
# to it, and the Forge builder always works in this frame, whatever the art's
# pixel size: the layout is stored in 0-1 image coordinates anyway.
OFFICIAL_RATIO = 1.09
RATIO_TOLERANCE = 0.005        # closer than this (relative) isn't cropped
FORGE_FRAME = (2180, 2000)
MAX_ART_WIDTH = 4096


def _point(value):
    """A [u, v] pair clamped to the image, or None."""
    try:
        u, v = float(value[0]), float(value[1])
    except (TypeError, ValueError, IndexError):
        return None
    if u != u or v != v:   # NaN
        return None
    return [min(max(u, 0.0), 1.0), min(max(v, 0.0), 1.0)]


def clean_builder_state(state, *, logo=False):
    """Keep only the builder's own keys, with numbers where numbers belong.
    `logo`: keep the Root logo's position (Forge maps print it; the Keep's
    don't)."""
    state = state if isinstance(state, dict) else {}
    clean = {'version': 1}
    try:
        clean['track_y'] = min(max(float(state.get('track_y')), 0.0), 1.0)
    except (TypeError, ValueError):
        pass
    items = _point(state.get('items'))
    if items:
        clean['items'] = items
    if logo:
        at = _point(state.get('logo'))
        if at:
            clean['logo'] = at
    clearings = []
    for c in state.get('clearings') or []:
        if not isinstance(c, dict):
            continue
        centre = _point(c.get('centre'))
        if centre is None:
            continue
        entry = {'centre': centre, 'marker': _point(c.get('marker'))}
        try:
            entry['number'] = int(c.get('number'))
        except (TypeError, ValueError):
            entry['number'] = None
        try:
            entry['number_angle'] = float(c.get('number_angle'))
        except (TypeError, ValueError):
            pass
        if c.get('suit') in SUITS:
            entry['suit'] = c['suit']
        clearings.append(entry)
    clean['clearings'] = clearings
    clean['forests'] = [p for p in (_point(f) for f in state.get('forests') or []) if p]
    clean['visited'] = sorted({v for v in state.get('visited') or [] if v in STAGES})
    if isinstance(state.get('fixed'), bool):
        clean['fixed'] = state['fixed']
    clean['buildings'] = [{'at': p, 'ruin': bool(b.get('ruin'))}
                          for b in state.get('buildings') or [] if isinstance(b, dict)
                          for p in [_point(b.get('at'))] if p]
    paths = clean_paths(state.get('paths'), len(clearings))
    if paths:
        clean['paths'] = paths
    if any(p['kind'] in WATER_KINDS for p in paths):
        shapes = clean_lake_shapes(state.get('lake_shapes'))
        if shapes:
            clean['lake_shapes'] = shapes
    # The name the builder used to give its (red) special paths: kept, as the
    # red ones' name when the setup card hasn't one (map_setup_card.special_types).
    name = state.get('special_path_name')
    if isinstance(name, str) and name.strip():
        clean['special_path_name'] = name.strip()[:SPECIAL_PATH_NAME_MAX]
    return clean


def clean_lake_shapes(raw):
    """The builder's lake outlines (state['lake_shapes']), held to sane sizes:
    rings of at least 3 [u, v] points, numbers clamped just past the frame and
    rounded. Malformed: [] (the lakes are then drawn as wide rivers)."""
    if not isinstance(raw, list):
        return []
    rings, total = [], 0
    for ring in raw[:LAKE_RINGS_MAX]:
        if not isinstance(ring, list) or len(ring) > LAKE_RING_POINTS_MAX:
            return []
        points = []
        for point in ring:
            if not (isinstance(point, (list, tuple)) and len(point) == 2):
                return []
            try:
                u, v = (round(min(max(float(n), -0.05), 1.05), 4) for n in point)
            except (TypeError, ValueError):
                return []
            if u != u or v != v:         # NaN
                return []
            points.append([u, v])
        if len(points) >= 3:
            total += len(points)
            if total > LAKE_POINTS_MAX:
                return []
            rings.append(points)
    return rings


def suit_icons():
    """{suit: its tag icon's URL}: the builder's suit buttons and the summary card."""
    return {suit: static(f'images/tags/{suit.capitalize()}_Tag.png') for suit in SUITS}


def builder_config(image_url, width, height, state, *, fixed, expected, save_url, show_intros, **extra):
    """The map builder page's config (read by map_builder.js). `width` and
    `height` are the frame the builder draws in: the board image's pixels on
    the Keep, FORGE_FRAME in the Forge. `extra` goes in as is (the Forge's
    upload step, print underlays, read-only view...)."""
    from django.contrib.staticfiles import finders

    geometry = MapGeometry(width, height)
    config = {
        'image': image_url,
        'width': width,
        'height': height,
        'state': state,
        'fixed': bool(fixed),
        'expected': expected,
        'numbers': highest_number(),
        # Geometry in pixels, so the editor draws pieces where TTS will put them.
        'px_per_local': geometry.px_per_local,
        'track': {'cells': TRACK_CELLS, 'x0': TRACK_X0, 'step': TRACK_STEP, 'row_gap': TRACK_ROW_GAP,
                  'half_width': geometry.hx, 'half_height': geometry.hz},
        'item_grid': pieces()['item_grid'],
        'building_size': BUILDING_SIZE,
        'number_radius_px': number_tile_radius_px(geometry),
        # An average clearing's radius: the Buildings step's zoom never shows less than one.
        'default_radius_px': DEFAULT_CLEARING_RADIUS * geometry.px_per_local,
        'suit_icons': suit_icons(),
        # The suit marker's outline (tts/make_marker_svg.py); round dots without it.
        'marker_svg': static(MARKER_SVG) if finders.find(MARKER_SVG) else None,
        # The summary card's forest and ruin icons (on the review step).
        'forest_icon': static(FOREST_ICON) if finders.find(FOREST_ICON) else None,
        'ruin_icon': static(RUIN_ICON) if finders.find(RUIN_ICON) else None,
        # The marker mesh's footprint (mesh units x its 1.3 scale), in pixels.
        'marker_px': [3.164 * 1.3 / BOARD_SCALE * geometry.px_per_local,
                      1.575 * 1.3 / BOARD_SCALE * geometry.px_per_local],
        'save_url': save_url,
        # Step cards open by themselves until the user's first finished map.
        'show_intros': bool(show_intros),
        'random_icon': static('images/ClearingsRandom.png'),
    }
    config.update(extra)
    return config


def crop_to_official(image):
    """(image, cropped): a PIL image centre-cropped to the official boards'
    shape (never stretched) and capped at MAX_ART_WIDTH wide."""
    width, height = image.size
    ratio = width / height
    cropped = abs(ratio / OFFICIAL_RATIO - 1) > RATIO_TOLERANCE
    if cropped:
        if ratio > OFFICIAL_RATIO:
            new_w = round(height * OFFICIAL_RATIO)
            left = (width - new_w) // 2
            image = image.crop((left, 0, left + new_w, height))
        else:
            new_h = round(width / OFFICIAL_RATIO)
            top = (height - new_h) // 2
            image = image.crop((0, top, width, top + new_h))
    if image.width > MAX_ART_WIDTH:
        from PIL import Image
        image = image.resize((MAX_ART_WIDTH, round(MAX_ART_WIDTH * image.height / image.width)), Image.LANCZOS)
    return image, cropped


# ── Printed map art (Forge maps) ────────────────────────────────────────────
# The pieces the PDF engine prints onto a Forge map's art, sized in board-local
# units so the builder's underlays and the printed map match exactly.

PRINT_STATIC = 'pdf/maps/'
PRINT_DIR = Path(settings.BASE_DIR) / 'the_keep' / 'static' / 'pdf' / 'maps'
# Building slots: the builder's squares and the printed building / ruin art.
BUILDING_SIZE = 0.1
# The Root logo's printed width; its height follows the art.
LOGO_WIDTH = 0.9
DEFAULT_LOGO = (0.8, 0.07)     # the logo's centre (u, v) on a new Forge map
# root_map_items.png: 6 x 2 item boxes, 262 px apart across (its centre is the
# middle of the boxes); scaled so they sit on the item grid's slots.
ITEMS_ART_PITCH_PX = 262.0

# The suit art (pdf/maps/suits/): root_map_<suit>_line.svg is an arc of the
# clearing's circle at 6 o'clock; root_map_<suit>_icon.svg is drawn upright on
# the marker spot. Measured from the line SVG's path (in its mm user units, y
# down): the arc's centre and the radius of the middle of its coloured band.
SUIT_LINE_CENTRE_MM = (13.91, -25.38)
SUIT_LINE_RADIUS_MM = 29.27


@lru_cache(maxsize=None)
def _print_art_size(name):
    from PIL import Image
    with Image.open(PRINT_DIR / name) as image:
        return image.size


def print_art():
    """The printed pieces' art and sizes in local units: {name: {url, path,
    w, h}} for 'vp', 'items' and 'logo'."""
    grid = pieces()['item_grid']
    vp_w = TRACK_CELLS * TRACK_STEP   # the strip is the 31 cells, edge to edge
    items_scale = grid['step_x'] / ITEMS_ART_PITCH_PX
    sizes = {
        'vp': ('root_map_vp.png', lambda w, h: (vp_w, vp_w * h / w)),
        'items': ('root_map_items.png', lambda w, h: (w * items_scale, h * items_scale)),
        'logo': ('root_map_logo.png', lambda w, h: (LOGO_WIDTH, LOGO_WIDTH * h / w)),
    }
    art = {}
    for key, (name, size) in sizes.items():
        w, h = size(*_print_art_size(name))
        art[key] = {'url': static(PRINT_STATIC + name), 'path': str(PRINT_DIR / name), 'w': w, 'h': h}
    return art


def print_layout(geometry, state):
    """Where everything printed on a Forge map goes, in the geometry's pixels
    (x right, y down): boxes are (x, y, w, h) of the art's top-left corner and
    size. Suits only on fixed-suit maps, for clearings with a marker and a suit;
    each has the clearing's centre, its radius (centre to marker), the marker
    spot and the marker's angle (degrees, measured on the image: 90 = straight
    down, where the line art's arc already sits)."""
    defaults = geometry.defaults()
    ppl = geometry.px_per_local
    art = print_art()

    def box(name, cx, cy):
        w, h = art[name]['w'] * ppl, art[name]['h'] * ppl
        return (cx - w / 2, cy - h / 2, w, h)

    track_y = state.get('track_y', defaults['track_y'])
    layout = {'vp': box('vp', geometry.to_image(0, 0)[0] * geometry.width, track_y * geometry.height)}
    grid = pieces()['item_grid']
    x0, z0 = geometry.to_local(*(state.get('items') or defaults['items']))
    middle = geometry.to_pixels(*geometry.to_image(x0 - (grid['cols'] - 1) / 2 * grid['step_x'],
                                                   z0 + (grid['rows'] - 1) / 2 * grid['step_z']))
    layout['items'] = box('items', *middle)
    layout['logo'] = box('logo', *geometry.to_pixels(*(state.get('logo') or DEFAULT_LOGO)))

    size = BUILDING_SIZE * ppl
    layout['buildings'] = []
    for building in state.get('buildings') or []:
        bx, by = geometry.to_pixels(*building['at'])
        layout['buildings'].append({'box': (bx - size / 2, by - size / 2, size, size), 'ruin': bool(building.get('ruin'))})

    layout['suits'] = []
    if state.get('fixed'):
        for clearing in state.get('clearings') or []:
            if not clearing.get('marker') or clearing.get('suit') not in SUITS:
                continue
            cx, cy = geometry.to_pixels(*clearing['centre'])
            mx, my = geometry.to_pixels(*clearing['marker'])
            radius = math.hypot(mx - cx, my - cy)
            if radius <= 0:
                continue
            layout['suits'].append({
                'suit': clearing['suit'], 'centre': (cx, cy), 'marker': (mx, my), 'radius': radius,
                'angle': math.degrees(math.atan2(my - cy, mx - cx)),
            })
    # Paths & Rivers, and the clearings' circles: the engine prints them only
    # on a map without art.
    layout['paths'] = path_shapes(geometry, state)
    layout['path_width'] = _path_px(geometry)['width']
    rivers = print_rivers(geometry, state, layout['paths'])
    layout['river_links'] = rivers['links']
    layout['river_trimmed'] = rivers['trimmed']
    layout['river_springs'] = [spring['at'] for spring in rivers['springs']]   # rounded river ends
    # Lakes: the builder's outlines, in pixels (filled even-odd). A layout with
    # lakes but no outline draws them as wide rivers instead ('lake_lines').
    layout['lakes'] = [[geometry.to_pixels(u, v) for u, v in ring] for ring in state.get('lake_shapes') or []]
    layout['lake_lines'] = not layout['lakes']
    inset = CLEARING_OUTLINE_INSET * geometry.width / FORGE_FRAME[0]
    layout['clearings'] = [(x, y, max(r - inset, r / 2))     # (never below half a very small clearing)
                           for c, (x, y, r) in zip(state.get('clearings') or [], _circles(geometry, state))
                           if c.get('marker')]
    layout['clearing_outline'] = CLEARING_OUTLINE_WIDTH * ppl
    return layout


# ── Paths & Rivers (the Forge builder's Paths step) ─────────────────────────
# state['paths'] = [{'kind', 'a', 'b'}]: each end is {'c': clearing index} or
# {'p': path index} (that path's middle: a river joining a river). The builder
# (map_builder.js) mirrors this geometry, so what's printed is what was drawn.

PATH_KINDS = ('path', 'special', 'river', 'lake', 'ocean')
# Water drawn as an area (map_lakes.js works out its outline): a lake, always
# inland; an ocean, the sea round the outside of the map.
WATER_KINDS = ('lake', 'ocean')
# Bottom to top, where they cross: lakes, rivers, then special paths, then
# paths (the builder draws them the same way, via path_config).
PATH_DRAW_ORDER = ('ocean', 'lake', 'river', 'special', 'path')
# A river and a path (or special path) may join the same two ends; two of a
# family may not. Where a river and a path share both ends they bow apart, each
# by this many (river + path) half-widths at the middle, so they don't overlap.
SHARED_BEND = 1.0
SPECIAL_PATH_NAME_MAX = 24       # the name a map gives a type of its special paths (its setup card)


def path_family(kind):
    """'river' (water: rivers and lakes) or 'land' (paths and special paths):
    one of each may share two ends."""
    return 'river' if kind in ('river', 'lake', 'ocean') else 'land'
PATH_COLOURS = {'path': '#ffffff', 'special': '#ed2731', 'river': '#3e55a5', 'lake': '#3e55a5', 'ocean': '#3e55a5'}
# The special paths' types, by colour, in order: a path {'kind': 'special',
# 'special': key} (no key: the first, red, as every map had before). Their
# names and rules are the map's setup card's (map_card['special_paths']). A
# type is one more entry here.
SPECIAL_PATH_TYPES = (
    {'key': 'red', 'colour': '#ed2731', 'label': 'Red'},
    {'key': 'green', 'colour': '#2f9e44', 'label': 'Green'},
    {'key': 'orange', 'colour': '#f08c00', 'label': 'Orange'},
)
SPECIAL_KEYS = tuple(t['key'] for t in SPECIAL_PATH_TYPES)
SPECIAL_DEFAULT = SPECIAL_KEYS[0]


def special_key(path):
    """A special path's type (its key; the first for one without)."""
    key = (path or {}).get('special')
    return key if key in SPECIAL_KEYS else SPECIAL_DEFAULT


def special_colour(key):
    return next((t['colour'] for t in SPECIAL_PATH_TYPES if t['key'] == key), PATH_COLOURS['special'])


def path_colour(path):
    """A path's colour: its kind's, or for a special path its type's."""
    return special_colour(special_key(path)) if path.get('kind') == 'special' else PATH_COLOURS[path['kind']]
# Lakes: drawn from clearing to clearing like a river, printed as an area of
# water filling the space around them halfway to the land (other clearings,
# paths, rivers, forests, buildings). The builder works the outline out
# (map_lakes.js) and saves it with the layout: state['lake_shapes'], rings of
# [u, v] (0-1), drawn even-odd (a ring can be an island's hole).
LAKE_GRID = 180                  # the lake outline's grid: cells across the frame
LAKE_FOREST_ISLAND = 0.5         # a forest in a lake or sea is an island this share of an average clearing's radius
LAKE_SHORE_MARGIN = 0.35         # a sea keeps this share of an average clearing's radius clear of paths (beyond their width)
LAKE_CLEARING_OVERLAP = 0.25     # a lake overlaps this share of its clearings' radius (their edge); the rest stays dry
LAKE_SMOOTH = 0.35               # spikes and points of water narrower than twice this (x a clearing's radius) are rounded off
LAKE_CROSS_REACH = 1.0           # a path an inland lake is forced across isn't land this far (x a clearing's radius) from the crossing
LAKE_TOUCH = 0.92               # an inland lake just touches each of its clearings this share of its radius from the centre
LAKE_LENS = 0.3                  # a lake between two clearings is a lens this share of the gap between them wide (each side)
LAKE_FUNNEL = 0.35               # where a river ends at a lake clearing, the lake runs in this share of its radius wide, to the river's width at its middle
LAKE_VARIATION = 0.12            # lake shores vary gently in and out by up to this share of a clearing's radius
LAKE_ROUND = 0.6                 # shores are softened (every corner rounded) over this share of a clearing's radius
LAKE_MIN_HALF = 1.5              # a lake is at least this many river half-widths wide along its line
LAKE_RINGS_MAX = 40
LAKE_RING_POINTS_MAX = 400
LAKE_POINTS_MAX = 4000
MAP_BLANK = '#808080'            # a map without art: medium grey under everything
PATH_WIDTH = 0.05                # local units (~a sixth of a clearing's radius)
PATH_LANES = 3                   # parallel rows of pill-shaped dashes: cobblestones
PATH_STROKE = 0.8                # each dash's thickness, in lane widths
PATH_DASH = 1.6                  # dash length (lane widths), before its round caps
PATH_GAP = 1.4                   # gap (lane widths), before the caps eat into it
RIVER_WIDTH = 2.0                # a river is one solid line, this share of PATH_WIDTH (twice a path)
CLEARING_OUTLINE = '#ffffff'     # a map without art prints each clearing's circle as an outline
CLEARING_OUTLINE_WIDTH = 0.008   # local units
# The printed circle sits this far inside the clearing's radius: pixels of the
# Forge frame (FORGE_FRAME, 2180 wide), about 2.3 mm on the 22 in board. (5% of
# the radius, averaged over a real map's clearings and rounded up.)
CLEARING_OUTLINE_INSET = 9
SPRING_RATIO = 1.0               # a loose river end whose edge ahead is this many times farther than the one behind is a spring
RIVER_LINK_REACH = 0.6          # a river's curve through a clearing: control points this share of its radius in
MERGE_REACH = 1.5                # a river merging into another: starts (and ends) this many river widths from the junction
RUN_OFF_MAX_TURN = 100.0         # a lone river at a clearing whose way straight off the map is blocked may turn up to this (degrees) in it
RUN_OFF_TURN_COST = 0.5          # ...choosing the shortest way off, each 90° of turn counting as this share more length
DEFAULT_CLEARING_RADIUS = 0.3    # local units, for a clearing without its marker yet
MAX_PATHS = 300
PATH_SAMPLES = 32
_BEND_STEPS = 12
_BEND_MAX = 0.8                  # the furthest a path bends, as a share of its length
ROUTE_AROUND_MAX = 2.5           # ...or one dragged round clearings (in the builder): this share


def _end(value, clearings, paths):
    """('c', i) or ('p', j) for a path end, or None."""
    if not isinstance(value, dict):
        return None
    for key, limit in (('c', clearings), ('p', paths)):
        if key in value:
            try:
                index = int(value[key])
            except (TypeError, ValueError):
                return None
            return (key, index) if 0 <= index < limit else None
    return None


def clean_paths(raw, clearing_count):
    """The valid paths, in order. Drops unknown kinds, ends out of range,
    a path from an end to itself, a second path of the same family between the
    same two ends (a river and a path may share them), and
    paths that attach (through others) to themselves; a path attached to a
    dropped one goes too. The rest are renumbered, and so are the {'p': j}
    references to them."""
    raw = [p for p in (raw if isinstance(raw, list) else [])[:MAX_PATHS]]
    parsed, bows, specials, bends = {}, {}, {}, {}
    for i, p in enumerate(raw):
        if not isinstance(p, dict) or p.get('kind') not in PATH_KINDS:
            continue
        a, b = _end(p.get('a'), clearing_count, len(raw)), _end(p.get('b'), clearing_count, len(raw))
        if a and b and a != b:
            parsed[i] = (p['kind'], a, b)
            if p['kind'] == 'special' and special_key(p) != SPECIAL_DEFAULT:
                specials[i] = special_key(p)
            # Dragged by the user: the side it bends to, the clearings it goes
            # round (not its own ends) and how far it bends at least.
            if p.get('side') in (1, -1) and not isinstance(p.get('side'), bool):
                own = {end[1] for end in (a, b) if end[0] == 'c'}
                around = sorted({k for k in (p.get('around') if isinstance(p.get('around'), list) else [])
                                 if isinstance(k, int) and not isinstance(k, bool)
                                 and 0 <= k < clearing_count and k not in own})
                bend = {'side': int(p['side'])}
                if around:
                    bend['around'] = around
                bulge = p.get('bulge')
                if isinstance(bulge, (int, float)) and not isinstance(bulge, bool) and 0 < bulge <= ROUTE_AROUND_MAX:
                    bend['bulge'] = round(float(bulge), 3)
                bends[i] = bend
            if p.get('bow') in (1, -1) and not isinstance(p.get('bow'), bool):
                bows[i] = int(p['bow'])

    alive = set(parsed)
    changed = True
    while changed:
        changed = False
        # Attached to a path that's gone (or to itself), or to a lake's middle
        # (nothing can be), or a lake not joining two clearings.
        for i in sorted(alive):
            if any(key == 'p' and (j not in alive or j == i or parsed[j][0] in WATER_KINDS) for key, j in parsed[i][1:]) \
                    or (parsed[i][0] in WATER_KINDS and any(key == 'p' for key, _j in parsed[i][1:])):
                alive.discard(i)
                changed = True
        # A second path of the same family (rivers; paths and special paths)
        # between the same two ends: the first is kept.
        seen = set()
        for i in sorted(alive):
            ends = (frozenset(parsed[i][1:]), path_family(parsed[i][0]))
            if ends in seen:
                alive.discard(i)
                changed = True
            seen.add(ends)
        # Loops: a path that leads back to itself through the paths it attaches to.
        for i in sorted(alive):
            stack, visited = [j for key, j in parsed[i][1:] if key == 'p'], set()
            while stack:
                j = stack.pop()
                if j == i:
                    alive.discard(i)
                    changed = True
                    break
                if j in visited or j not in alive:
                    continue
                visited.add(j)
                stack.extend(k for key, k in parsed[j][1:] if key == 'p')

    order = sorted(alive)
    renumber = {old: new for new, old in enumerate(order)}
    out = []
    for i in order:
        kind, a, b = parsed[i]
        out.append({'kind': kind, 'a': {a[0]: renumber[a[1]] if a[0] == 'p' else a[1]},
                    'b': {b[0]: renumber[b[1]] if b[0] == 'p' else b[1]}})
        if i in specials:  # a special path's type (none: the first)
            out[-1]['special'] = specials[i]
        if i in bends:     # how the user bent it (_route_around)
            out[-1].update(bends[i])
        if i in bows:      # which side it bows to, when it shares its ends (_pair_side)
            out[-1]['bow'] = bows[i]
    return out


def _path_px(geometry):
    """The path constants in the geometry's pixels."""
    ppl = geometry.px_per_local
    width = PATH_WIDTH * ppl
    return {'width': width, 'lane': width / PATH_LANES, 'default_radius': DEFAULT_CLEARING_RADIUS * ppl}


def path_config(geometry):
    """The builder's copy of the path constants (pixels), so it draws as the PDF does."""
    from .map_setup_card import builder_card_config
    px = _path_px(geometry)
    return {'colours': PATH_COLOURS, 'specials': [dict(t) for t in SPECIAL_PATH_TYPES],
            'width': px['width'], 'lanes': PATH_LANES, 'stroke': PATH_STROKE,
            'dash': PATH_DASH, 'gap': PATH_GAP, 'river_width': RIVER_WIDTH, 'default_radius': px['default_radius'],
            'draw_order': list(PATH_DRAW_ORDER), 'shared_bend': SHARED_BEND, 'link_reach': RIVER_LINK_REACH,
            'samples': PATH_SAMPLES, 'bend_steps': _BEND_STEPS, 'bend_max': _BEND_MAX, 'blank': MAP_BLANK,
            'around_max': ROUTE_AROUND_MAX,
            # Lakes (map_lakes.js): their grid, a forest's island, and the lake shapes.
            'lake': {'grid': LAKE_GRID,
                     'touch': LAKE_TOUCH, 'lens': LAKE_LENS, 'funnel': LAKE_FUNNEL, 'variation': LAKE_VARIATION,
                     'forest_r': LAKE_FOREST_ISLAND * DEFAULT_CLEARING_RADIUS * geometry.px_per_local,
                     'shore_margin': LAKE_SHORE_MARGIN * DEFAULT_CLEARING_RADIUS * geometry.px_per_local,
                     'clearing_overlap': LAKE_CLEARING_OVERLAP,
                     'smooth': LAKE_SMOOTH * DEFAULT_CLEARING_RADIUS * geometry.px_per_local,
                     'round': LAKE_ROUND * DEFAULT_CLEARING_RADIUS * geometry.px_per_local,
                     'cross_reach': LAKE_CROSS_REACH * DEFAULT_CLEARING_RADIUS * geometry.px_per_local,
                     'min_half': LAKE_MIN_HALF * px['width'] * RIVER_WIDTH / 2},
            # The setup card's diagram fit: lakes reach a clearing as far as it is on the card.
            'card': builder_card_config()}


def _circles(geometry, state):
    """(x, y, radius) of each clearing, in pixels (radius: centre to marker)."""
    default = _path_px(geometry)['default_radius']
    out = []
    for c in state.get('clearings') or []:
        cx, cy = geometry.to_pixels(*c['centre'])
        r = default
        if c.get('marker'):
            mx, my = geometry.to_pixels(*c['marker'])
            r = math.hypot(mx - cx, my - cy) or default
        out.append((cx, cy, r))
    return out


def _quad(a, c, b, t):
    u = 1 - t
    return (u * u * a[0] + 2 * u * t * c[0] + t * t * b[0], u * u * a[1] + 2 * u * t * c[1] + t * t * b[1])


def _segment_distance(p, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    length2 = dx * dx + dy * dy
    t = 0.0 if not length2 else max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / length2))
    return math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy)


def _route(a, b, obstacles):
    """The control point of a quadratic curve from a to b that keeps clear of
    every obstacle (x, y, clearance): straight (the midpoint) when that's
    clear, else bent sideways as little as works; straight if nothing does."""
    options = _route_options(a, b, obstacles, both=False)
    return options[0][1] if options else ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)


def _route_options(a, b, obstacles, both=True):
    """The clear ways from a to b, as (side, control point), best first:
    [(0, the midpoint)] when straight is clear; else, for each side it can
    bend to (+1 or -1, across a→b), the least bend that keeps clear of every
    obstacle, the smaller bend first (on a tie, away from the obstacle the
    straight line cuts deepest into). [] if nothing is clear. both=False:
    just the best."""
    mid = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
    blocking = [o for o in obstacles if _segment_distance(o, a, b) < o[2]]
    if not blocking:
        return [(0, mid)]
    length = math.hypot(b[0] - a[0], b[1] - a[1]) or 1e-9
    nx, ny = -(b[1] - a[1]) / length, (b[0] - a[0]) / length
    # Bend away from the obstacle the straight line cuts deepest into.
    worst = min(blocking, key=lambda o: _segment_distance(o, a, b) - o[2])
    side = -1 if (worst[0] - mid[0]) * nx + (worst[1] - mid[1]) * ny > 0 else 1
    options = []
    for step in range(1, _BEND_STEPS + 1):
        bulge = _BEND_MAX * length * step / _BEND_STEPS
        for s in (side, -side):
            if any(found == s for found, _c in options):
                continue
            # A quadratic's middle sits halfway to its control point.
            control = (mid[0] + s * nx * 2 * bulge, mid[1] + s * ny * 2 * bulge)
            samples = [_quad(a, control, b, i / PATH_SAMPLES) for i in range(PATH_SAMPLES + 1)]
            if all(math.hypot(p[0] - o[0], p[1] - o[1]) >= o[2] for o in obstacles for p in samples):
                options.append((s, control))
                if not both or len(options) == 2:
                    return options
    return options


def _route_around(a, b, obstacles, wrapped, side, min_bulge=0.0):
    """(control point, ok): a path's arc bent the way the user dragged it.
    _route's quadratic and steps, but only to `side` (+1/-1, from the a->b
    line's normal), and on out to ROUTE_AROUND_MAX lengths: the least bulge
    (no less than min_bulge lengths) that keeps clear of every obstacle and
    has each of `wrapped` (obstacles: the clearings it goes round) between
    the line and the arc. ok False: none does; then the farthest clear one
    on that side (else its least), never the other side."""
    mid = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
    length = math.hypot(b[0] - a[0], b[1] - a[1]) or 1e-9
    nx, ny = -(b[1] - a[1]) / length, (b[0] - a[0]) / length
    ux, uy = (b[0] - a[0]) / length, (b[1] - a[1]) / length
    # Only clearings on that side, and alongside the line (not past its ends).
    wrapped = [w for w in wrapped
               if ((w[0] - a[0]) * nx + (w[1] - a[1]) * ny) * side > 0
               and 0 < ((w[0] - a[0]) * ux + (w[1] - a[1]) * uy) < length]
    unit = _BEND_MAX / _BEND_STEPS
    best = None
    for step in range(1, int(math.ceil(ROUTE_AROUND_MAX / unit)) + 1):
        bulge = unit * step * length
        if bulge < min_bulge * length - 1e-9:
            continue
        control = (mid[0] + side * nx * 2 * bulge, mid[1] + side * ny * 2 * bulge)
        samples = [_quad(a, control, b, i / PATH_SAMPLES) for i in range(PATH_SAMPLES + 1)]
        if not all(math.hypot(p[0] - o[0], p[1] - o[1]) >= o[2] for o in obstacles for p in samples):
            continue
        best = control
        # Round the wrapped ones: each centre inside the arc (the curve, clear
        # of its circle, then passes beyond it).
        if all(_inside_rings((w[0], w[1]), [samples]) for w in wrapped):
            return control, True
    if best is None:
        bulge = max(min_bulge, unit) * length
        best = (mid[0] + side * nx * 2 * bulge, mid[1] + side * ny * 2 * bulge)
    return best, False


def _trim(a, control, b, centre, radius, start):
    """t where the curve crosses the circle around its start (or end)."""
    lo, hi = (0.0, 0.5) if start else (0.5, 1.0)
    inside = lambda t: math.hypot(*(p - c for p, c in zip(_quad(a, control, b, t), centre))) < radius  # noqa: E731
    if start and not inside(lo) or not start and not inside(hi):
        return lo if start else hi
    if start and inside(hi) or not start and inside(lo):
        return hi if start else lo            # the circles overlap: nothing left to draw
    for _ in range(30):
        m = (lo + hi) / 2
        if inside(m) == start:
            lo = m
        else:
            hi = m
    return (lo + hi) / 2


def _middle(points):
    """The point halfway along a polyline, by length."""
    lengths = [math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(points, points[1:])]
    half = sum(lengths) / 2
    for (a, b), length in zip(zip(points, points[1:]), lengths):
        if half <= length and length:
            t = half / length
            return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)
        half -= length
    return points[len(points) // 2]


def _end_key(end):
    """'c3' or 'p1' (map_builder.js endKey): ends compare the same way in both."""
    return f"c{end['c']}" if 'c' in end else f"p{end['p']}"


def _pair_key(path):
    return '|'.join(sorted((_end_key(path['a']), _end_key(path['b']))))


def _key_sign(path):
    """+1 if the path runs from its ends in key order, else -1."""
    return 1 if _end_key(path['a']) <= _end_key(path['b']) else -1


def _pair_side(path, twin):
    """Which way (+1/-1, across the line between the ends in key order) a path
    sharing both ends with its twin of the other family bows. A path's `bow`
    (+1/-1: towards the normal of its own a->b line, chosen where the designer
    dropped it) wins, then its twin's (which this path is opposite); without
    either, the water one bows +1 and the land one -1 (bowApart in
    map_builder.js)."""
    if path.get('bow') in (1, -1):
        return path['bow'] * _key_sign(path)
    if twin is not None and twin.get('bow') in (1, -1):
        return -twin['bow'] * _key_sign(twin)
    return 1 if path_family(path['kind']) == 'river' else -1


def _bow_apart(control, a, b, path, side, width):
    """A river and a path between the same two ends: push their curve apart,
    to `side` (+1/-1, measured from the ends in key order, so both agree), by
    SHARED_BEND half-widths at the middle."""
    p, q = (a, b) if _end_key(path['a']) <= _end_key(path['b']) else (b, a)
    nx, ny = _unit(-(q[1] - p[1]), q[0] - p[0])
    bulge = SHARED_BEND * width * (RIVER_WIDTH + 1) / 2
    # A quadratic's middle sits halfway to its control point.
    return (control[0] + side * nx * 2 * bulge, control[1] + side * ny * 2 * bulge)


def path_shapes(geometry, state):
    """Each path's kind, colour and centre line (a polyline in pixels,
    trimmed to the clearings' circles) and its middle, in state order."""
    return route_paths(state.get('paths') or [], _circles(geometry, state), _path_px(geometry)['width'])


def route_paths(paths, circles, width):
    """path_shapes' work, from its inputs: the paths (state['paths']), the
    clearings' (x, y, r) and a path's width (a river's is RIVER_WIDTH times
    it), in the same units. A map setup card (map_setup_card.py) draws its
    diagram with it too."""
    shapes = {}
    # Paths that share both ends with one of the other family (a river and a
    # path): each one's twin.
    pairs = {}
    for i, p in enumerate(paths):
        pairs.setdefault(_pair_key(p), []).append(i)
    twins = {i: j for members in pairs.values() for i in members for j in members
             if path_family(paths[i]['kind']) != path_family(paths[j]['kind'])}

    def shape(i, depth=0):
        if i in shapes:
            return shapes[i]
        if depth > len(paths):
            raise ValueError('path attachments loop')
        p = paths[i]
        ends, own = [], set()
        for end in (p['a'], p['b']):
            if 'c' in end:
                ends.append(circles[end['c']][:2])
                own.add(end['c'])
            else:
                ends.append(shape(end['p'], depth + 1)['middle'])
        a, b = ends
        # Kept clear of other clearings by half its own width (a river's is wider).
        clearance = width * (RIVER_WIDTH if path_family(p['kind']) == 'river' else 1) / 2
        obstacles = [(x, y, r + clearance) for k, (x, y, r) in enumerate(circles) if k not in own]
        if p.get('side') in (1, -1):
            # Dragged by the user: round the clearings it was dragged past.
            wrapped = [(x, y, r + clearance) for k, (x, y, r) in enumerate(circles)
                       if k in set(p.get('around') or ()) and k not in own]
            control, _ok = _route_around(a, b, obstacles, wrapped, p['side'], p.get('bulge') or 0.0)
        else:
            control = _route(a, b, obstacles)
        if i in twins:
            control = _bow_apart(control, a, b, p, _pair_side(p, paths[twins[i]]), width)
        t0 = _trim(a, control, b, a, circles[p['a']['c']][2], True) if 'c' in p['a'] else 0.0
        t1 = _trim(a, control, b, b, circles[p['b']['c']][2], False) if 'c' in p['b'] else 1.0
        t1 = max(t1, t0)
        points = [_quad(a, control, b, t0 + (t1 - t0) * k / PATH_SAMPLES) for k in range(PATH_SAMPLES + 1)]
        shapes[i] = {'kind': p['kind'], 'colour': path_colour(p), 'points': points,
                     'middle': _middle(points)}
        if p['kind'] == 'special':
            shapes[i]['special'] = special_key(p)
        return shapes[i]

    return [shape(i) for i in range(len(paths))]


def path_lanes(points, width):
    """The PATH_LANES polylines a path is drawn with: its centre line offset
    across its width."""
    lane = width / PATH_LANES
    normals = []
    for k in range(len(points)):
        a, b = points[max(k - 1, 0)], points[min(k + 1, len(points) - 1)]
        length = math.hypot(b[0] - a[0], b[1] - a[1]) or 1e-9
        normals.append((-(b[1] - a[1]) / length, (b[0] - a[0]) / length))
    return [[(x + nx * (i - (PATH_LANES - 1) / 2) * lane, y + ny * (i - (PATH_LANES - 1) / 2) * lane)
             for (x, y), (nx, ny) in zip(points, normals)] for i in range(PATH_LANES)]


def _cubic(p0, p1, p2, p3, t):
    u = 1 - t
    return tuple(u ** 3 * a + 3 * u * u * t * b + 3 * u * t * t * c + t ** 3 * d
                 for a, b, c, d in zip(p0, p1, p2, p3))


def _ray_to_edge(p, d, width, height):
    """Where a ray from p along d leaves the frame (0..width, 0..height)."""
    hits = []
    for axis, limit in ((0, width), (1, height)):
        if d[axis] > 1e-9:
            hits.append((limit - p[axis]) / d[axis])
        elif d[axis] < -1e-9:
            hits.append(-p[axis] / d[axis])
    t = min((h for h in hits if h > 0), default=None)
    return None if t is None else (p[0] + d[0] * t, p[1] + d[1] * t)


def _past_edge(before, edge, half_width, geometry):
    """A point beyond `edge` (where a river leaves the frame) along its way
    out, far enough that the river's flat end, square to the line, is wholly
    off the page at any angle: half its width x tan(the angle to the edge's
    normal). The page clips the rest."""
    dx, dy = edge[0] - before[0], edge[1] - before[1]
    length = math.hypot(dx, dy) or 1e-9
    dx, dy = dx / length, dy / length
    on_side = abs(edge[0]) < 1e-6 or abs(edge[0] - geometry.width) < 1e-6
    cos = abs(dx if on_side else dy) or 1e-9      # against the normal of the edge it crosses
    extra = half_width * math.sqrt(max(1 - cos * cos, 0)) / cos + half_width
    extra = min(extra, half_width * 40)           # (an exit almost along the edge)
    return (edge[0] + dx * extra, edge[1] + dy * extra)


def _unit(dx, dy):
    length = math.hypot(dx, dy)
    return (dx / length, dy / length) if length else (0.0, 0.0)


def _polyline_length(points):
    return sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(points, points[1:]))


def _polyline_at(points, distance):
    """(point, unit tangent, segment index) `distance` along a polyline,
    clamped to its ends."""
    distance = max(distance, 0.0)
    for k, (a, b) in enumerate(zip(points, points[1:])):
        length = math.hypot(b[0] - a[0], b[1] - a[1])
        if distance <= length or k == len(points) - 2:
            t = min(distance / length, 1.0) if length else 0.0
            return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t), _unit(b[0] - a[0], b[1] - a[1]), k
        distance -= length
    return points[-1], (0.0, 0.0), max(len(points) - 2, 0)


def _cut(points, distance, keep_head):
    """The polyline's first `distance` (keep_head) or the rest after it."""
    at, _t, k = _polyline_at(points, distance)
    return points[:k + 1] + [at] if keep_head else [at] + points[k + 1:]


def _arc_position(points, p):
    """How far along a polyline its nearest point to p is."""
    best, best_d, run = 0.0, float('inf'), 0.0
    for a, b in zip(points, points[1:]):
        dx, dy = b[0] - a[0], b[1] - a[1]
        length2 = dx * dx + dy * dy
        t = 0.0 if not length2 else max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / length2))
        d = math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy)
        if d < best_d:
            best, best_d = run + t * math.sqrt(length2), d
        run += math.sqrt(length2)
    return best


def _merge(start, d, target, at, reach):
    """A tributary: a curve from `start` (leaving along d) into the line
    `target`, ending `reach` downstream of the junction `at` (downstream: the
    target's way most like d), arriving along the target so the two read as one."""
    s_at = _arc_position(target, at)
    _p, tangent, _k = _polyline_at(target, s_at)
    sign = 1 if tangent[0] * d[0] + tangent[1] * d[1] >= 0 else -1
    end, t_end, _k = _polyline_at(target, min(max(s_at + sign * reach, 0.0), _polyline_length(target)))
    t_end = (t_end[0] * sign, t_end[1] * sign)
    k = math.hypot(end[0] - start[0], end[1] - start[1]) / 3
    p1 = (start[0] + d[0] * k, start[1] + d[1] * k)
    p2 = (end[0] - t_end[0] * k, end[1] - t_end[1] * k)
    return [_cubic(start, p1, p2, end, n / PATH_SAMPLES) for n in range(PATH_SAMPLES + 1)]


def _run_off(start, d, geometry, obstacles, half_width):
    """A river carried on from `start`, the way it was flowing (d), to the
    map's edge and just past it, bending round `obstacles` like a path. None
    if it's already leaving the frame. (`geometry`: anything with the frame's
    width and height, its corner at 0, 0.)"""
    edge = _ray_to_edge(start, d, geometry.width, geometry.height)
    if edge is None:
        return None
    control = _route(start, edge, obstacles)
    run = [_quad(start, control, edge, n / PATH_SAMPLES) for n in range(PATH_SAMPLES + 1)]
    return run + [_past_edge(run[-2], edge, half_width, geometry)]


def _clearing_run_off(at, d, circle, geometry, obstacles, half_width, prefer=None):
    """A lone river at a clearing (its end `at` on the clearing's rim, flowing
    along d) carried on to the map's edge: {'join': its curve on through the
    clearing (None when it runs straight on), 'run': from there to just past
    the edge, 'choice': (turn, side) as taken, None for _run_off's}, or None
    if it's already leaving the frame.

    Straight on, as _run_off, when that's clear. Else the shortest way off
    of the directions up to RUN_OFF_MAX_TURN either way of d (each 90° of
    turn counting as RUN_OFF_TURN_COST more length): leaving the clearing's
    rim, bent round the obstacles like a path, and joined to the river
    through the clearing as two rivers are (river_extras). prefer: a choice
    to take when it's clear here (the printed map's, for its setup card).
    None clear: _run_off's."""
    w, h = geometry.width, geometry.height
    edge = _ray_to_edge(at, d, w, h)
    if edge is None:
        return None
    if not any(_segment_distance(o, at, edge) < o[2] for o in obstacles):
        return {'join': None, 'run': _run_off(at, d, geometry, obstacles, half_width), 'choice': (0, 0)}
    cx, cy, r = circle
    route_obstacles = list(obstacles) + [(cx, cy, r * 0.98)]   # (not back across its own clearing)

    def turn_of(u):
        return math.degrees(math.acos(max(-1.0, min(1.0, u[0] * d[0] + u[1] * d[1]))))

    def ways(turn):
        """[(score, choice, B, control, E, u_B)] leaving along d turned by `turn`."""
        a = math.radians(turn)
        u = (d[0] * math.cos(a) - d[1] * math.sin(a), d[0] * math.sin(a) + d[1] * math.cos(a))
        end = _ray_to_edge((cx, cy), u, w, h)
        if end is None:
            return []
        out = []
        for side, _control in _route_options((cx + u[0] * r, cy + u[1] * r), end, route_obstacles):
            start, best = (cx + u[0] * r, cy + u[1] * r), None
            for _ in range(2):      # then again from where the run leaves the rim, so it leaves squarely
                options = dict(_route_options(start, end, route_obstacles))
                control = options.get(side, options.get(0))
                if control is None:
                    break
                u_b = _unit(control[0] - start[0], control[1] - start[1])
                if u_b == (0.0, 0.0) or turn_of(u_b) > RUN_OFF_MAX_TURN:
                    break
                best = (start, control, u_b)
                start = (cx + u_b[0] * r, cy + u_b[1] * r)
            if best is None:
                continue
            start, control, u_b = best
            run = [_quad(start, control, end, n / PATH_SAMPLES) for n in range(PATH_SAMPLES + 1)]
            score = _polyline_length(run) * (1 + RUN_OFF_TURN_COST * turn_of(u_b) / 90)
            out.append((score, (turn, side), start, control, end, u_b))
        return out

    found = []
    if prefer is not None:
        found = [way for way in ways(prefer[0]) if way[1] == tuple(prefer)]
    if not found:
        step = 20
        for turn in range(-int(RUN_OFF_MAX_TURN), int(RUN_OFF_MAX_TURN) + 1, step):
            found.extend(ways(turn))
    if not found:
        return {'join': None, 'run': _run_off(at, d, geometry, obstacles, half_width), 'choice': None}
    _score, choice, start, control, end, u_b = min(found, key=lambda way: way[0])
    reach = r * RIVER_LINK_REACH
    p1 = (at[0] + d[0] * reach, at[1] + d[1] * reach)
    p2 = (start[0] - u_b[0] * reach, start[1] - u_b[1] * reach)
    join = [_cubic(at, p1, p2, start, n / PATH_SAMPLES) for n in range(PATH_SAMPLES + 1)]
    run = [_quad(start, control, end, n / PATH_SAMPLES) for n in range(PATH_SAMPLES + 1)]
    return {'join': join, 'run': run + [_past_edge(run[-2], end, half_width, geometry)], 'choice': choice}


def print_rivers(geometry, state, shapes=None):
    """The printed map's rivers (a map without art), beyond what the builder
    draws: {'links': extra polylines, 'trimmed': {river index: its printed
    line}} (pixels). Drawn under the clearing circles and every path.

    - Two rivers at a clearing: a curve through it, continuing each river's
      line (the straightest pairs first). An odd one out curves into the
      nearest of those, like a tributary.
    - A river ending on a path's middle: carried on, the way it was
      flowing, to the map's edge (round the clearings).
    - One river at a clearing: likewise, when its way straight on is clear.
      Else the shortest way off, turning up to RUN_OFF_MAX_TURN in the
      clearing (curving through it as two rivers do), round the other
      clearings ('run_offs': {(river, end): the way it took}).
    - A river ending on another river's middle: cut back a little and curved
      into that river (its printed line), flowing the way it was heading.
    - A loose end (either run-off above) whose way to the edge is longer than
      the way back to the edge behind it is a spring: the river starts there,
      with no run-off ('springs': [{'key': (river, end), 'at', 'd'}])."""
    shapes = shapes if shapes is not None else path_shapes(geometry, state)
    paths = state.get('paths') or []
    lakes = [[geometry.to_pixels(u, v) for u, v in ring] for ring in state.get('lake_shapes') or []]
    return river_extras(paths, shapes, _circles(geometry, state), _path_px(geometry)['width'] * RIVER_WIDTH,
                        geometry, water=water_clearings(paths), feed=True, lakes=lakes)


def _is_spring(start, d, frame):
    """A loose river end at `start`, flowing along d: is the frame's edge
    ahead farther (SPRING_RATIO times) than the edge behind it? Then it's
    where the river starts, not a river running a long way off the map."""
    ahead = _ray_to_edge(start, d, frame.width, frame.height)
    if ahead is None:
        return False                     # already leaving the frame: it runs off
    behind = _ray_to_edge(start, (-d[0], -d[1]), frame.width, frame.height)
    behind_d = math.dist(start, behind) if behind is not None else 0.0
    return math.dist(start, ahead) > behind_d * SPRING_RATIO


def river_extras(paths, shapes, circles, width, frame, join_clearings=True, springs=None, water=(),
                 feed=False, lakes=(), run_offs=None):
    """print_rivers' work, from its inputs: the paths (state['paths']), their
    shapes ({'points', ...} each, as path_shapes), the clearings' (x, y, r),
    the river's width and the frame (anything with width and height, its
    corner at 0, 0), all in the same units. Also draws a map setup card's
    rivers (map_setup_card.py), which stop at the clearings: with
    join_clearings=False, two rivers at a clearing aren't joined through it
    (nor is an odd one out merged), but a lone river there still runs off.

    springs: which loose ends are springs, as a set of (river index, end):
    the card passes the printed map's, so the two agree. None decides here.
    run_offs: likewise, the way each lone river at a clearing leaves the map
    ({(river index, end): (turn, side)}, as _clearing_run_off chose it), so
    the card's runs leave the way the printed map's do (when that's clear).
    water: clearings on a lake: a river ending at one flows into the lake
    (no run-off, and no join through the clearing). feed: and draw it on
    through the clearing into the water: to the nearest point of the lake
    outline (`lakes`, rings in the same units), or without one, to where the
    lake's line meets the clearing."""
    half = width / 2
    reach_base = MERGE_REACH * width
    rivers = [i for i, p in enumerate(paths) if p['kind'] == 'river']
    printed = {i: list(shapes[i]['points']) for i in rivers}
    links, trimmed, done, found_springs, found_run_offs = [], {}, set(), [], {}

    def loose_end(key, at, d, obstacles, circle=None):
        """Run a loose end off to the edge, unless it's a spring. At a
        clearing (its circle): through it, the shortest way off."""
        spring = (key in springs) if springs is not None else _is_spring(at, d, frame)
        if spring:
            found_springs.append({'key': key, 'at': at, 'd': d})
            return
        if circle is None:
            run = _run_off(at, d, frame, obstacles, half)
            if run:
                links.append(run)
            return
        way = _clearing_run_off(at, d, circle, frame, obstacles, half, prefer=(run_offs or {}).get(key))
        if way is None:
            return
        if way['join'] and join_clearings:
            links.append(way['join'])
        if way['run']:
            links.append(way['run'])
        if way['choice'] is not None:
            found_run_offs[key] = way['choice']

    def arriving(points, end):
        """(point, direction it flows in) where a river reaches its `end`."""
        at, before = (points[-1], points[-2]) if end == 'b' else (points[0], points[1])
        return at, _unit(at[0] - before[0], at[1] - before[1])

    def finish(i, depth=0):
        # A river's ends on other rivers' middles, once those are final (so it
        # merges into their printed lines). Paths can't loop (clean_paths).
        if i in done or depth > len(paths):
            return
        for end in ('a', 'b'):
            target = paths[i][end].get('p')
            if target is None:
                continue
            if paths[target]['kind'] == 'river':
                finish(target, depth + 1)
                points = printed[i]
                junction, _d = arriving(points, end)
                total = _polyline_length(points)
                reach = min(reach_base, total / 2)
                if end == 'b':
                    start, tangent, _k = _polyline_at(points, total - reach)
                    d = tangent
                    printed[i] = _cut(points, total - reach, keep_head=True)
                else:
                    start, tangent, _k = _polyline_at(points, reach)
                    d = (-tangent[0], -tangent[1])
                    printed[i] = _cut(points, reach, keep_head=False)
                trimmed[i] = printed[i]
                links.append(_merge(start, d, printed[target], junction, reach_base))
            else:
                at, d = arriving(printed[i], end)
                loose_end((i, end), at, d, [(x, y, r + half) for x, y, r in circles])
        done.add(i)

    for i in rivers:
        finish(i)

    # Rivers at clearings (their clearing ends are never trimmed).
    ends = {}            # clearing -> [(end point, direction into the clearing)]
    keys = {}            # clearing -> [(river, end)], beside ends
    for i in rivers:
        for end in ('a', 'b'):
            if 'c' in paths[i][end]:
                at, d = arriving(printed[i], end)
                if d != (0.0, 0.0):
                    ends.setdefault(paths[i][end]['c'], []).append((at, d))
                    keys.setdefault(paths[i][end]['c'], []).append((i, end))

    def sampled(fn):
        return [fn(k / PATH_SAMPLES) for k in range(PATH_SAMPLES + 1)]

    for k, items in sorted(ends.items()):
        if k in water:
            # It flows into the lake (no run-off), on through the clearing.
            if feed:
                target = _lake_mouth(k, circles[k], paths, shapes, lakes)
                if target is not None:
                    (tx, ty), (nx, ny) = target
                    reach = circles[k][2] * RIVER_LINK_REACH
                    # An inland lake's outline curves out to meet the river at the
                    # rim, widening from its width (map_lakes.js): nothing to draw.
                    # Otherwise (a sea, an island clearing, an outline saved
                    # before that): a curve on through the clearing to the water.
                    items = [(at, d) for at, d in items
                             if not _inside_rings((at[0] + d[0] * half, at[1] + d[1] * half), lakes)]
                    end = (tx + nx * half, ty + ny * half)     # a little into the water: no hairline gap
                    for at, d in items:
                        p1 = (at[0] + d[0] * reach, at[1] + d[1] * reach)
                        p2 = (tx - nx * reach, ty - ny * reach)
                        links.append(sampled(lambda t, at=at, p1=p1, p2=p2: _cubic(at, p1, p2, end, t)))
            continue
        cx, cy, r = circles[k]
        if len(items) == 1:
            at, d = items[0]
            loose_end(keys[k][0], at, d, [(x, y, rr + half) for j, (x, y, rr) in enumerate(circles) if j != k],
                      circle=circles[k])
            continue
        if not join_clearings:
            continue
        remaining, pairs = list(items), []
        while len(remaining) >= 2:
            # The straightest join: one river's way in most like the other's way out.
            i, j = max(((i, j) for i in range(len(remaining)) for j in range(i + 1, len(remaining))),
                       key=lambda ij: -(remaining[ij[0]][1][0] * remaining[ij[1]][1][0]
                                        + remaining[ij[0]][1][1] * remaining[ij[1]][1][1]))
            (a, da), (b, db) = remaining[i], remaining[j]
            reach = r * RIVER_LINK_REACH
            p1 = (a[0] + da[0] * reach, a[1] + da[1] * reach)
            p2 = (b[0] + db[0] * reach, b[1] + db[1] * reach)
            pairs.append(sampled(lambda t: _cubic(a, p1, p2, b, t)))
            remaining = [e for n, e in enumerate(remaining) if n not in (i, j)]
        links.extend(pairs)
        if remaining:
            # The odd one out joins the nearest pair's curve, like a tributary.
            (at, d), = remaining
            curve = min(pairs, key=lambda c: math.hypot(*(p - q for p, q in zip(_middle(c), at))))
            links.append(_merge(at, d, curve, _middle(curve), reach_base))
    return {'links': links, 'trimmed': trimmed, 'springs': found_springs, 'run_offs': found_run_offs}


def _lake_mouth(k, circle, paths, shapes, lakes):
    """Where the water meets lake clearing k: (point, unit direction into the
    water). The nearest point of the lake outline to the clearing (within a
    little of its circle), or without an outline, where a lake's line leaves
    the clearing."""
    cx, cy, r = circle
    best, best_d = None, r * 1.5
    for ring in lakes:
        for a, b in zip(ring, ring[1:] + ring[:1]):
            dx, dy = b[0] - a[0], b[1] - a[1]
            length2 = dx * dx + dy * dy
            t = 0.0 if not length2 else max(0.0, min(1.0, ((cx - a[0]) * dx + (cy - a[1]) * dy) / length2))
            p = (a[0] + dx * t, a[1] + dy * t)
            d = math.hypot(p[0] - cx, p[1] - cy)
            if d < best_d:
                best, best_d = (p, _unit(-dy, dx)), d
    if best is not None:
        p, (nx, ny) = best
        # Of the two normals, the one whose side is water (even-odd: the
        # outline's rings can be holes).
        probe = (p[0] + nx * 2, p[1] + ny * 2)
        if not _inside_rings(probe, lakes):
            nx, ny = -nx, -ny
        return p, (nx, ny)
    for i, path in enumerate(paths):
        if path.get('kind') not in WATER_KINDS:
            continue
        for end, points in (('a', shapes[i]['points']), ('b', shapes[i]['points'][::-1])):
            if path[end].get('c') == k:
                at = points[0]
                return at, _unit(at[0] - cx, at[1] - cy)
    return None


def _inside_rings(p, rings):
    inside = False
    for ring in rings:
        for (ax, ay), (bx, by) in zip(ring, ring[1:] + ring[:1]):
            if (ay > p[1]) != (by > p[1]) and p[0] < ax + (p[1] - ay) * (bx - ax) / (by - ay):
                inside = not inside
    return inside


def river_links(geometry, state, shapes=None):
    """The printed map's extra river lines (print_rivers' links)."""
    return print_rivers(geometry, state, shapes)['links']


def lake_bodies(paths):
    """The lakes' bodies: sets of clearings joined by lake paths."""
    parent = {}

    def find(k):
        while parent.setdefault(k, k) != k:
            parent[k] = parent[parent[k]]
            k = parent[k]
        return k

    for p in paths:
        if p.get('kind') == 'lake' and 'c' in (p.get('a') or {}) and 'c' in (p.get('b') or {}):
            parent[find(p['a']['c'])] = find(p['b']['c'])
    bodies = {}
    for k in parent:
        bodies.setdefault(find(k), set()).add(k)
    return list(bodies.values())


def water_clearings(paths):
    """The clearings on a lake or an ocean (a river ending at one flows into it)."""
    return {end['c'] for p in paths if p.get('kind') in WATER_KINDS
            for end in (p.get('a') or {}, p.get('b') or {}) if 'c' in end}


def path_totals(state):
    """{'path': n, 'special': n, 'river': n, 'lake': lake bodies,
    'river_clearings': n (clearings on a river, a lake or an ocean; oceans aren't counted),
    'specials': {type key: n} (the special paths by type, used ones only)}."""
    paths = state.get('paths') or []
    totals = {kind: sum(1 for p in paths if p.get('kind') == kind) for kind in PATH_KINDS if kind != 'ocean'}
    totals['specials'] = {key: n for key in SPECIAL_KEYS
                          if (n := sum(1 for p in paths if p.get('kind') == 'special' and special_key(p) == key))}
    totals['lake'] = len(lake_bodies(paths))
    totals['river_clearings'] = len({end['c'] for p in paths if p.get('kind') in ('river', 'lake', 'ocean')
                                     for end in (p.get('a') or {}, p.get('b') or {}) if 'c' in end})
    return totals


# ── Checks and counts ───────────────────────────────────────────────────────

# The builder's stages whose counts go to the Map, and the Map fields each sets.
STAGES = ('clearings', 'buildings', 'forests', 'paths')


def check_builder(map_post, state, stages=None, require_buildings=False):
    """(errors, warnings) for a builder layout. Errors block generating; count
    differences from the post are only warnings (Complete updates the post), and
    only for `stages` reached (all when None). `require_buildings`: at least one
    building slot and one ruin (the Forge, where the map is printed with them;
    some keep maps have none)."""
    errors, warnings = [], []
    state = state or {}
    clearings = state.get('clearings') or []
    if not clearings:
        errors.append("Mark at least one clearing.")
    if require_buildings:
        buildings = state.get('buildings') or []
        if not buildings:
            errors.append("Mark at least one building slot.")
        if not any(b.get('ruin') for b in buildings):
            errors.append("Mark at least one ruin.")
    if any(not c.get('marker') for c in clearings):
        errors.append("Every clearing needs its suit-marker spot.")
    numbers = sorted(c.get('number') for c in clearings if isinstance(c.get('number'), int))
    if numbers != list(range(1, len(clearings) + 1)):
        errors.append("Clearing numbers must run from 1 with no gaps or repeats.")
    # Fixed suits: the layout's choice (saved but maybe not completed yet), else the Map's.
    fixed = state['fixed'] if isinstance(state.get('fixed'), bool) else getattr(map_post, 'fixed_clearings', False)
    if fixed and any(c.get('suit') not in SUITS for c in clearings):
        errors.append("This Map has fixed clearings: choose a suit for each clearing before generating.")
    highest = highest_number()
    if clearings and len(clearings) > highest:
        warnings.append(f"Only numbers 1-{highest} have tiles: clearings above "
                        f"{highest} get no number tile.")
    for stage, label, field, count in _counts(state):
        if stages is not None and stage not in stages:
            continue
        current = getattr(map_post, field, None)
        if current is not None and current != count:
            warnings.append(f"You marked {count} {label}; the Map currently has {current}. "
                            f"Completing the map updates it to {count}.")
    return errors, warnings


def _counts(state):
    """(stage, label, Map field, count) for each count the builder sets. River
    clearings only once the Paths step has been reached (the Forge's builder
    only): until then the Map's own figure is left alone."""
    buildings = state.get('buildings') or []
    counts = [
        ('clearings', 'clearings', 'clearings', len(state.get('clearings') or [])),
        ('forests', 'forests', 'forests', len(state.get('forests') or [])),
        ('buildings', 'building slots', 'building_slots', len(buildings)),
        ('buildings', 'ruins', 'ruins', sum(1 for b in buildings if b.get('ruin'))),
    ]
    if 'paths' in visited_stages(state):
        counts.append(('paths', 'river clearings', 'river_clearings', path_totals(state)['river_clearings']))
    return tuple(counts)


def layout_counts(state):
    """{Map field: count} for the counts a layout sets (clearings, forests,
    building_slots, ruins, and river_clearings once the Paths step is reached)."""
    return {field: count for _stage, _label, field, count in _counts(state or {})}


# ── The layout summary card (map_summary.js) ─────────────────────────────────
# The builder's review step draws it from the live layout; the Forge and Keep
# map detail pages draw it from this data (same shape as the builder's).

def _suit_names():
    from django.utils.translation import gettext as _
    return {'fox': _('Fox'), 'rabbit': _('Rabbit'), 'mouse': _('Mouse'), 'bird': _('Bird'), 'city': _('Lost City')}


def _summary(tiles, path_tiles, suits):
    from django.contrib.staticfiles import finders
    return {'tiles': tiles, 'path_tiles': path_tiles, 'suits': suits, 'colours': PATH_COLOURS,
            'marker_svg': static(MARKER_SVG) if finders.find(MARKER_SVG) else None,
            'forest_icon': static(FOREST_ICON) if finders.find(FOREST_ICON) else None,
            'ruin_icon': static(RUIN_ICON) if finders.find(RUIN_ICON) else None}


def _random_note():
    from django.utils.translation import gettext as _
    return {'fixed': False, 'note': _('Random Clearing Suits: shuffled each game.'),
            'icon': static('images/ClearingsRandom.png')}


def layout_summary(state, *, fixed=None, map_card=None, lang_code='en'):
    """The summary card's data from a builder layout: a tile per piece, a row
    for Paths, River Clearings and a tile per special path type the map uses
    (named on its setup card, map_card) once the Paths step was reached, and
    the clearing suits (the layout's fixed / random choice, else `fixed`)."""
    from .map_setup_card import special_types
    from django.utils.translation import gettext as _
    state = state or {}
    counts = layout_counts(state)
    tile = lambda kind, label, count: {'kind': kind, 'label': label, 'count': count}  # noqa: E731
    tiles = [tile('clearing', _('Clearings'), counts['clearings']),
             tile('building', _('Building Slots'), counts['building_slots']),
             tile('ruin', _('Ruins'), counts['ruins']),
             tile('forest', _('Forests'), counts['forests'])]
    path_tiles = []
    if 'paths' in visited_stages(state):
        totals = path_totals(state)
        path_tiles = [tile('path', _('Paths'), totals['path']),
                      tile('river', _('River Clearings'), totals['river_clearings'])]
        # Then each special path type, in its colour, by its name.
        for kind in special_types(state, map_card, lang_code):
            path_tiles.append({**tile('special', kind['name'], kind['count']), 'colour': kind['colour'],
                               'special': kind['key']})
    fixed = state['fixed'] if isinstance(state.get('fixed'), bool) else bool(fixed)
    if not fixed:
        return _summary(tiles, path_tiles, _random_note())
    clearings = state.get('clearings') or []
    names, icons = _suit_names(), suit_icons()
    chips = [{'suit': suit, 'name': names[suit], 'icon': icons[suit],
              'count': sum(1 for c in clearings if c.get('suit') == suit)} for suit in SUITS]
    return _summary(tiles, path_tiles, {
        'fixed': True, 'chips': [chip for chip in chips if chip['count']],
        'unset': sum(1 for c in clearings if c.get('suit') not in SUITS), 'unset_label': _('No suit'),
    })


def figures_summary(map_post):
    """The summary card's data for a Map without a builder layout: its own
    figures (those that are set), and whether its suits are fixed (there's no
    tally without a layout)."""
    from django.utils.translation import gettext as _
    fields = (('clearing', _('Clearings'), 'clearings'), ('building', _('Building Slots'), 'building_slots'),
              ('ruin', _('Ruins'), 'ruins'), ('forest', _('Forests'), 'forests'))
    tiles = [{'kind': kind, 'label': label, 'count': getattr(map_post, field)}
             for kind, label, field in fields if getattr(map_post, field, None) is not None]
    path_tiles = []
    if getattr(map_post, 'river_clearings', None) is not None:
        path_tiles.append({'kind': 'river', 'label': _('River Clearings'), 'count': map_post.river_clearings})
    if map_post.fixed_clearings:
        suits = {'fixed': True, 'note': _('Fixed suits: the clearings always load with the same suits.'),
                 'icon': static('images/ClearingsFixed.png')}
    else:
        suits = _random_note()
    return _summary(tiles, path_tiles, suits)


def map_summary(map_post):
    """A keep Map's summary card: from its builder layout when it has one
    (with clearings), else from its own figures."""
    state = getattr(map_post, 'tts_builder', None) or {}
    if state.get('clearings'):
        language = getattr(map_post, 'language', None)
        return layout_summary(state, fixed=map_post.fixed_clearings, map_card=getattr(map_post, 'map_card', None),
                              lang_code=language.code if language else 'en')
    return figures_summary(map_post)


def visited_stages(state):
    """The stages the builder has reached, as saved in the layout."""
    return {s for s in (state or {}).get('visited') or [] if s in STAGES}


def apply_builder_counts(map_post, state, stages=None):
    """Write the layout's counts to the post where they're new or changed, for
    the `stages` reached only (all when None): a stage not reached yet would
    otherwise overwrite the Map's figure with 0. Returns a list of
    "field old -> new" strings."""
    from ..models import Map
    changes, updates = [], {}
    for stage, _label, field, count in _counts(state or {}):
        if stages is not None and stage not in stages:
            continue
        current = getattr(map_post, field, None)
        if current != count:
            updates[field] = count
            changes.append(f"{field.replace('_', ' ')} {current if current is not None else '-'} → {count}")
    # Fixed or random suits, chosen on the Clearings step.
    fixed = (state or {}).get('fixed')
    if isinstance(fixed, bool) and (stages is None or 'clearings' in stages):
        current = bool(getattr(map_post, 'fixed_clearings', False))
        if fixed != current:
            updates['fixed_clearings'] = fixed
            yes_no = lambda v: 'Yes' if v else 'No'
            changes.append(f"fixed clearings {yes_no(current)} → {yes_no(fixed)}")
    if updates:
        Map.objects.filter(pk=map_post.pk).update(**updates)
        for field, value in updates.items():
            setattr(map_post, field, value)
    return changes


# ── Building the JSON ───────────────────────────────────────────────────────

def _resolve(value):
    """Turn "static:<path>" placeholders into absolute TTS URLs."""
    if isinstance(value, dict):
        return {k: _resolve(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve(v) for v in value]
    if isinstance(value, str) and value.startswith('static:'):
        return tts_image_url(static(value[len('static:'):]), request=SiteURLRequest())
    return value


def _piece(template, dx, dz, height, tags, rot_y=None):
    obj = _guids(_resolve(copy.deepcopy(template)))
    transform = obj.setdefault('Transform', {})
    transform.update({'posX': dx, 'posY': BOARD_Y + height, 'posZ': dz})
    if rot_y is not None:
        transform['rotY'] = rot_y
    # Exactly the builder's tags: a template's own (e.g. a marker's "Clearing
    # Marker") must not survive on a fixed-suit map.
    obj['Tags'] = list(tags)
    return obj


def _snap(x, z, rot_y=0.0, tags=None):
    snap = {'Position': {'x': round(x, 5), 'y': SNAP_Y, 'z': round(z, 5)},
            'Rotation': {'x': 0.0, 'y': round(rot_y % 360, 3), 'z': 0.0}}
    if tags:
        snap['Tags'] = list(tags)
    return snap


def marker_rotation(geometry, clearing):
    """A marker's world rotY: the direction from its clearing's centre to it,
    in table axes -- how the official markers are turned."""
    cx, cz = geometry.to_table(*clearing['centre'])
    mx, mz = geometry.to_table(*clearing['marker'])
    return math.degrees(math.atan2(mx - cx, mz - cz)) % 360


def item_slots(geometry, state):
    """Local (x, z) of each grid slot, keyed by (col, row) as seen on the image."""
    grid = pieces()['item_grid']
    x0, z0 = geometry.to_local(*state.get('items') or geometry.defaults()['items'])
    return {(c, r): (x0 - c * grid['step_x'], z0 + r * grid['step_z'])
            for c in range(grid['cols']) for r in range(grid['rows'])}


def track_snaps(geometry, state):
    _x, z_mid = geometry.to_local(0.5, state.get('track_y', geometry.defaults()['track_y']))
    return [_snap(TRACK_X0 + i * TRACK_STEP, z_mid + row * TRACK_ROW_GAP)
            for row in (-1, 0, 1) for i in range(TRACK_CELLS)]


SETUP_CARD_DECK_ID = 90          # no other object in a map's save has a CustomDeck
SETUP_CARD_GAP = 0.6             # table units between the board's edge and its setup card


def _setup_card_object(map_post, geometry):
    """The map's setup card (any map with a card_image: a keep Map's upload, or
    a Forge map's generated card), beside the board's right edge at its top:
    its back is the rules side, or the front again when it has none."""
    from .tts import DEFAULT_CARD_TRANSFORM, TTSSingleCardDeck

    front = getattr(map_post, 'card_image', None)
    if not front:
        return None
    back = getattr(map_post, 'card_2_image', None) or front
    # A card at scale 2.3 is about 5 x 7 table units.
    half_w, half_h = 2.3 * 1.1, 2.3 * 1.55
    (left_x, top_z), (right_x, bottom_z) = geometry.to_table(0.0, 0.0), geometry.to_table(1.0, 1.0)
    out = 1 if right_x > left_x else -1
    down = 1 if bottom_z > top_z else -1
    transform = {**DEFAULT_CARD_TRANSFORM, 'posX': right_x + out * (half_w + SETUP_CARD_GAP),
                 'posZ': top_z + down * half_h}
    card = TTSSingleCardDeck(front, back, SETUP_CARD_DECK_ID, request=SiteURLRequest(),
                             card_name=f'{map_post.title} Setup Card', transform=transform).to_object()
    card['Tags'] = ['Map Object']
    card['HideWhenFaceDown'] = False     # its back is the rules, for everyone to read
    return card


def build_map_tts_json(map_post, state):
    """The Map post's TTS save from its builder layout."""
    geometry = MapGeometry(map_post.board_image.width, map_post.board_image.height)
    templates = pieces()
    fixed = bool(getattr(map_post, 'fixed_clearings', False))
    clearings = sorted(state.get('clearings') or [], key=lambda c: c['number'])
    buildings = state.get('buildings') or []

    snaps = track_snaps(geometry, state)
    snaps += [_snap(x, z) for x, z in item_slots(geometry, state).values()]
    objects = []

    for i, clearing in enumerate(clearings):
        suit = clearing.get('suit') if fixed else SHUFFLED_SUITS[i % len(SHUFFLED_SUITS)]
        rot = marker_rotation(geometry, clearing)
        mx, mz = geometry.to_local(*clearing['marker'])
        snaps.append(_snap(mx, mz, rot - 180, tags=['Enclave']))
        tags = ['Map Object', f'Suit: {suit}'] if fixed else ['Clearing Marker', 'Map Object', f'Suit: {suit}']
        dx, dz = geometry.to_table(*clearing['marker'])
        # Never "City Marker" / "Lost City Marker": the coordinator's shuffleMaps
        # has special handling for those that expects other pieces.
        objects.append(_piece(marker_templates()[suit], dx, dz, HEIGHT_MARKER, tags, rot_y=rot))

        number = number_templates().get(clearing['number'])
        if number is not None:
            dx, dz = geometry.to_table(*geometry.number_tile_centre(clearing))
            tile = _piece(number, dx, dz, HEIGHT_NUMBER, ['Map Object', 'RTT Priority'])
            tile['Locked'] = True
            objects.append(tile)

    ruin_templates = templates['ruins']
    ruin_count = 0
    for building in buildings:
        x, z = geometry.to_local(*building['at'])
        snaps.append(_snap(x, z))
        if building.get('ruin'):
            dx, dz = geometry.to_table(*building['at'])
            template = ruin_templates[ruin_count % len(ruin_templates)]
            ruin = _piece(template, dx, dz, HEIGHT_TILE, ['Ruin', 'Map Object'])
            # The item side ("Press 2 for ITEM") must be cleared with the map
            # too, so it's tagged as well -- but not "Ruin", which the
            # coordinator's ruin shuffle and locking act on.
            for item_side in (ruin.get('States') or {}).values():
                item_side['Tags'] = ['Map Object']
            # Ruins past the fourth: the loader deals their items when the map
            # spawns (tts/loader.lua, drawRuinItems); this item is the default.
            if ruin_count >= len(ruin_templates):
                ruin['GMNotes'] = RUIN_EXTRA_NOTE
            objects.append(ruin)
            ruin_count += 1

    slots = item_slots(geometry, state)
    for item in templates['items']:
        x, z = slots[tuple(item['slot'])]
        objects.append(_piece(item['object'], -x * BOARD_SCALE, -z * BOARD_SCALE, HEIGHT_TILE, ['Map Object']))

    board = {
        'GUID': generate_tts_guid(),
        'Name': 'Custom_Token',
        'Transform': {'posX': 0.0, 'posY': BOARD_Y, 'posZ': 0.0, 'rotX': 0.0, 'rotY': 180.0, 'rotZ': 0.0,
                      'scaleX': BOARD_SCALE, 'scaleY': 1.0, 'scaleZ': BOARD_SCALE},
        'Nickname': map_post.title,
        'Description': '',
        'GMNotes': '',
        'Tags': ['Map Object', 'RTT Map', f'RTT Map: {map_post.title}'],
        'ColorDiffuse': {'r': 1.0, 'g': 1.0, 'b': 1.0},
        'Locked': True,
        'Grid': True,
        'Snap': True,
        'Autoraise': True,
        'Sticky': True,
        'Tooltip': False,
        'HideWhenFaceDown': False,
        'Hands': False,
        'CustomImage': {
            'ImageURL': tts_image_url(map_post.board_image, request=SiteURLRequest()),
            'ImageSecondaryURL': '',
            'ImageScalar': 1.0,
            'WidthScale': 0.0,
            'CustomToken': {'Thickness': 0.1, 'MergeDistancePixels': 15.0, 'StandUp': False, 'Stackable': False},
        },
        'LuaScript': '',
        'LuaScriptState': '',
        'XmlUI': '',
        'AttachedSnapPoints': snaps,
    }

    card = _setup_card_object(map_post, geometry)
    if card is not None:
        objects.append(card)

    save = wrap_tts_save([board] + objects, save_name=map_post.title)
    save['BoardLayout'] = {
        'clearings': [[round(v, 3) for v in geometry.to_table(*c['centre'])] for c in clearings],
        'forests': [[round(v, 3) for v in geometry.to_table(*f)] for f in state.get('forests') or []],
    }
    return save
