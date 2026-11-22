"""Tabletop Simulator supplies (bags holding copies of an object) and the row
of things lined up above a faction board.

Built from any TTS object dict, so warriors, tokens, buildings or anything
else can get a supply. Research from the official RTT mod (Workshop
3800311405, its "Faction Selection" catalogue):

* A warrior supply is a `Custom_Model_Bag` with the same mesh and texture as
  its warrior, scaled x1.73 (median; 1.59-2.09), lying down at rot (270, 180,
  0), `Bag: {Order: 0}`, its warriors as `ContainedObjects`.
* The Marquise "Wood Supply" (tokens) is a flat 50-segment cylinder bag:
  2.72 across and 0.66 tall in-game, the token's art on both caps and plain
  colour on the sides. Our round (tokens) and rounded-square (buildings) bags
  are built like it, from the static meshes written by
  tts/make_supply_meshes.py.
* Every supply sits just past the board's printed top edge (+Z here), about
  10.4 north of its centre (ROW_Z, scaled to our board). Without a decree the
  first is left of centre (the Marquise's, ROW_START_X); with one it moves
  left past the board's edge (the Keepers', ROW_START_X_DECREE).

The mod stores its supplies `Locked` at exact heights. Ours spawn unlocked and
lock once they've dropped (LOCK_ON_REST_LUA), as our boards do: a Saved Object
spawns at its stored heights, which don't match the (uneven) RTT table.
"""
import copy
from dataclasses import dataclass
from typing import Optional, Tuple

from django.templatetags.static import static

from .tts import FACTION_BOARD_TRANSFORM, LOCK_ON_REST_LUA, SiteURLRequest, _guids, tts_image_url

SUPPLY_SCALE = 1.73
SUPPLY_MINIMUM = 5

ROW_Z = 10.65
ROW_START_X = -8.25
ROW_START_X_DECREE = -14.8
ROW_GAP = 0.6
LOOSE_SPACING = 1.52   # official spacing of loose warriors

# Heights are only relative: every object falls onto the real table and
# settles. TABLE_Y is the bottom of the 0.2-thick faction board.
TABLE_Y = FACTION_BOARD_TRANSFORM['posY'] - 0.1
SPAWN_LIFT = 0.3

ASSETS_STATIC = 'images/tts/assets/'
STATIC_SUPPLY_MESHES = {'round': 'supply_round.obj', 'square': 'supply_square.obj'}
STATIC_SUPPLY_SCALE = 0.425
STATIC_SUPPLY_SIZE = 2.72      # across, in-game
STATIC_SUPPLY_HEIGHT = 0.66
LYING_ROTATION = {'rotX': 270.0, 'rotY': 180.0, 'rotZ': 0.0}
FLAT_ROTATION = {'rotX': 0.0, 'rotY': 180.0, 'rotZ': 0.0}


def supply_shape_for(template):
    """'round' / 'square' for a tile template (CustomTile.Type 2 / 3), None for a model."""
    if template.get('Name') == 'Custom_Model':
        return None
    tile_type = ((template.get('CustomImage') or {}).get('CustomTile') or {}).get('Type')
    return 'round' if tile_type == 2 else 'square'


def static_mesh_url(shape, request=None):
    return tts_image_url(static(ASSETS_STATIC + STATIC_SUPPLY_MESHES[shape]),
                         request=request or SiteURLRequest())


def _copy(template):
    """A copy for a bag or a row: fresh GUIDs, its own Nickname and scale kept,
    position and rotation reset (a warrior drawn from a bag takes its stored
    scale, so it must stay the template's)."""
    obj = _guids(copy.deepcopy(template))
    transform = obj.setdefault('Transform', {})
    transform.update({'posX': 0.0, 'posY': 0.0, 'posZ': 0.0, **FLAT_ROTATION})
    return obj


def make_supply(template, count, *, nickname, shape=None, texture_url=None, scale_factor=SUPPLY_SCALE,
                transform=None, request=None):
    """A supply bag holding `count` copies of `template`.

    A `Custom_Model` template (shape None) lends the bag its mesh, scaled by
    `scale_factor` and lying down, as the official warrior supplies are. A
    tile can't, so `shape='round'` / `'square'` gives a Wood Supply-like bag
    on a static mesh, showing `texture_url` (tts_figures.art_texture's
    layout)."""
    base = template.get('Transform') or {}
    if shape is None:
        mesh = dict(template['CustomMesh'])
        scale = {k: (base.get(k, 1.0) or 1.0) * scale_factor for k in ('scaleX', 'scaleY', 'scaleZ')}
        rotation = LYING_ROTATION
    else:
        if not texture_url:
            raise ValueError('A round or square supply needs a texture_url.')
        mesh_url = static_mesh_url(shape, request)
        mesh = {'MeshURL': mesh_url, 'DiffuseURL': texture_url, 'NormalURL': '', 'ColliderURL': mesh_url,
                'Convex': True, 'MaterialIndex': 1, 'CastShadows': True}
        scale = {k: STATIC_SUPPLY_SCALE for k in ('scaleX', 'scaleY', 'scaleZ')}
        rotation = FLAT_ROTATION
    mesh['TypeIndex'] = 6
    full_transform = {'posX': 0.0, 'posY': 0.0, 'posZ': 0.0, **rotation, **scale}
    full_transform.update(transform or {})
    return _guids({
        'Name': 'Custom_Model_Bag',
        'Transform': full_transform,
        'Nickname': nickname,
        'Description': '',
        'GMNotes': '',
        'ColorDiffuse': dict(template.get('ColorDiffuse') or {'r': 1.0, 'g': 1.0, 'b': 1.0}),
        'LayoutGroupSortIndex': 0,
        'Value': 0,
        'Locked': False,
        'Grid': True,
        'Snap': True,
        'IgnoreFoW': False,
        'MeasureMovement': False,
        'DragSelectable': True,
        'Autoraise': True,
        'Sticky': True,
        'Tooltip': True,
        'GridProjection': False,
        'HideWhenFaceDown': False,
        'Hands': False,
        'CustomMesh': mesh,
        'Bag': {'Order': 0},
        'LuaScript': LOCK_ON_REST_LUA,
        'LuaScriptState': '',
        'XmlUI': '',
        'ContainedObjects': [_copy(template) for _ in range(count)],
    })


def object_footprint(obj, size=None):
    """(width, depth) of an object standing on the table. A figure passes its
    size ([width, depth, height]); a tile is about 2 x its scale across."""
    if size:
        return float(size[0]), float(size[1])
    t = obj.get('Transform') or {}
    if obj.get('Name') in ('Custom_Tile', 'Custom_Token'):
        return 2 * abs(t.get('scaleX', 1.0)), 2 * abs(t.get('scaleZ', 1.0))
    return 1.0, 1.0


class RowLayout:
    """The cursor for the row of things lined up above the board: each item
    goes `gap` to the right (+X) of the last. `start_x` is the first item's
    centre. Obstacles are (x0, x1, z0, z1); an item that would overlap one
    starts past its right edge, unless it only clips the obstacle's north edge
    (by up to NUDGE), when it moves north just clear of it instead."""

    NUDGE = 0.5

    def __init__(self, start_x=ROW_START_X, z=ROW_Z, gap=ROW_GAP, obstacles=()):
        self.start_x = start_x
        self.z = z
        self.gap = gap
        self.obstacles = [tuple(o) for o in obstacles]
        self._left = None        # the next item's left edge, once something's placed
        self.placed = []         # (x0, x1, z0, z1) of each item

    def place(self, width, depth, min_x=None):
        """The centre (x, z) for an item `width` x `depth`."""
        left = self.start_x - width / 2 if self._left is None else self._left
        if min_x is not None and left + width / 2 < min_x:
            left = min_x - width / 2
        z = self.z
        for _ in range(4 * len(self.obstacles) + 1):
            hit = next((o for o in self.obstacles
                        if left < o[1] and left + width > o[0]
                        and z - depth / 2 < o[3] and z + depth / 2 > o[2]), None)
            if hit is None:
                break
            clip = hit[3] - (z - depth / 2)
            if clip <= self.NUDGE and z == self.z:
                z = hit[3] + depth / 2 + 0.05
            else:
                left, z = hit[1] + self.gap, self.z
        self._left = left + width + self.gap
        self.placed.append((left, left + width, z - depth / 2, z + depth / 2))
        return left + width / 2, z

    @property
    def end(self):
        """Where the next item's left edge would go (the gap included)."""
        return self.start_x if self._left is None else self._left


def tile_obstacle(transform, aspect):
    """(x0, x1, z0, z1) of a Custom_Tile on the table: a tile is 2 x scaleZ
    deep and 2 x scaleX x its image's aspect (width / height) wide (our
    board: scale 9.035, aspect 1.294, so 23.40 x 18.08)."""
    hw = abs(transform.get('scaleX', 1.0)) * aspect
    hd = abs(transform.get('scaleZ', 1.0))
    x, z = transform.get('posX', 0.0), transform.get('posZ', 0.0)
    return (x - hw, x + hw, z - hd, z + hd)


@dataclass
class SupplyGroup:
    template: dict
    count: int
    nickname: str                       # the supply's
    size: Optional[Tuple[float, float, float]] = None   # standing (width, depth, height), world
    shape: Optional[str] = None         # None (model), 'round', 'square'
    texture_url: Optional[str] = None


def _supply_footprint(group):
    if group.shape is not None:
        return STATIC_SUPPLY_SIZE, STATIC_SUPPLY_SIZE
    width, _depth, height = group.size
    # Lying down, its height runs front to back along the table.
    return width * SUPPLY_SCALE, height * SUPPLY_SCALE


def layout_groups(groups, row, loose_spacing=None, supply_minimum=SUPPLY_MINIMUM, request=None):
    """Place each group in the row: a supply when it has `supply_minimum` or
    more, else its copies standing side by side. Returns the TTS objects."""
    objects = []
    for group in groups:
        if group.count <= 0:
            continue
        if group.count >= supply_minimum:
            width, depth = _supply_footprint(group)
            x, z = row.place(width, depth)
            if group.shape is None:
                _w, standing_depth, height = group.size
                # The mesh's origin is its base, which lying down (rot 270, 180)
                # is the footprint's south end: shift so the footprint is centred.
                transform = {'posX': x, 'posZ': z - height * SUPPLY_SCALE / 2,
                             'posY': TABLE_Y + standing_depth * SUPPLY_SCALE / 2 + SPAWN_LIFT}
            else:
                transform = {'posX': x, 'posZ': z, 'posY': TABLE_Y + SPAWN_LIFT}
            objects.append(make_supply(group.template, group.count, nickname=group.nickname,
                                       shape=group.shape, texture_url=group.texture_url,
                                       transform=transform, request=request))
            continue
        width, depth = object_footprint(group.template, group.size)
        spacing = max(loose_spacing or 0, width + 0.3)
        span = (group.count - 1) * spacing + width
        x, z = row.place(span, depth)
        first = x - span / 2 + width / 2
        for i in range(group.count):
            obj = _copy(group.template)
            obj['Transform'].update({'posX': first + i * spacing, 'posZ': z, 'posY': TABLE_Y + SPAWN_LIFT})
            objects.append(obj)
    return objects
