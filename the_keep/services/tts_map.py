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

from .tts import SiteURLRequest, generate_tts_guid, tts_image_url, wrap_tts_save

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
    return clean


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
        'suit_icons': {suit: static(f'images/tags/{suit.capitalize()}_Tag.png') for suit in SUITS},
        # The suit marker's outline (tts/make_marker_svg.py); round dots without it.
        'marker_svg': static(MARKER_SVG) if finders.find(MARKER_SVG) else None,
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
    return layout


# ── Checks and counts ───────────────────────────────────────────────────────

# The builder's stages whose counts go to the Map, and the Map fields each sets.
STAGES = ('clearings', 'buildings', 'forests')


def check_builder(map_post, state, stages=None):
    """(errors, warnings) for a builder layout. Errors block generating; count
    differences from the post are only warnings (saving updates the post), and
    only for `stages` reached (all when None)."""
    errors, warnings = [], []
    state = state or {}
    clearings = state.get('clearings') or []
    if not clearings:
        errors.append("Mark at least one clearing.")
    if any(not c.get('marker') for c in clearings):
        errors.append("Every clearing needs its suit-marker spot.")
    numbers = sorted(c.get('number') for c in clearings if isinstance(c.get('number'), int))
    if numbers != list(range(1, len(clearings) + 1)):
        errors.append("Clearing numbers must run from 1 with no gaps or repeats.")
    if getattr(map_post, 'fixed_clearings', False) and any(c.get('suit') not in SUITS for c in clearings):
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
                            f"Saving updates the Map to {count}.")
    return errors, warnings


def _counts(state):
    """(stage, label, Map field, count) for each count the builder sets."""
    buildings = state.get('buildings') or []
    return (
        ('clearings', 'clearings', 'clearings', len(state.get('clearings') or [])),
        ('forests', 'forests', 'forests', len(state.get('forests') or [])),
        ('buildings', 'building slots', 'building_slots', len(buildings)),
        ('buildings', 'ruins', 'ruins', sum(1 for b in buildings if b.get('ruin'))),
    )


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


def _guids(obj):
    obj['GUID'] = generate_tts_guid()
    for child in obj.get('ContainedObjects') or []:
        _guids(child)
    for child in (obj.get('States') or {}).values():
        _guids(child)
    return obj


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

    save = wrap_tts_save([board] + objects, save_name=map_post.title)
    save['BoardLayout'] = {
        'clearings': [[round(v, 3) for v in geometry.to_table(*c['centre'])] for c in clearings],
        'forests': [[round(v, 3) for v in geometry.to_table(*f)] for f in state.get('forests') or []],
    }
    return save
