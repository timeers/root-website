import json
import os
from functools import lru_cache

from the_keep.services.tts import (
    tts_image_url,
    wrap_tts_save,
    TTSBoardBase,
    FACTION_BOARD_TRANSFORM,
    CRAFTED_ITEMS_SNAP_POINTS,
    generate_tts_guid,
    LOCK_ON_REST_LUA,
)


TTS_OBJECTS_DIR = os.path.join(os.path.dirname(__file__), 'tts_objects')


@lru_cache(maxsize=8)
def _load_tts_object_template(filename):
    """Reads a saved-object JSON and returns its first ObjectState dict.
    Cached — file contents change rarely. Restart the worker to pick up edits."""
    path = os.path.join(TTS_OBJECTS_DIR, filename)
    with open(path) as f:
        save = json.load(f)
    return save['ObjectStates'][0]


def load_tts_object(filename, transform=None):
    """Returns a fresh copy of a saved-object template with a new GUID and
    optionally an overridden Transform."""
    template = _load_tts_object_template(filename)
    obj = json.loads(json.dumps(template))  # deep copy
    obj['GUID'] = generate_tts_guid()
    if transform is not None:
        obj['Transform'] = dict(transform)
    return obj


def _hex_to_rgb_floats(hex_color):
    if not hex_color:
        return {"r": 1.0, "g": 1.0, "b": 1.0}
    s = hex_color.lstrip('#')
    if len(s) == 3:
        s = ''.join(ch * 2 for ch in s)
    if len(s) != 6:
        return {"r": 1.0, "g": 1.0, "b": 1.0}
    try:
        r = int(s[0:2], 16) / 255.0
        g = int(s[2:4], 16) / 255.0
        b = int(s[4:6], 16) / 255.0
    except ValueError:
        return {"r": 1.0, "g": 1.0, "b": 1.0}
    return {"r": r, "g": g, "b": b}


class TTSForgedFactionBoard(TTSBoardBase):
    DEFAULT_TRANSFORM = FACTION_BOARD_TRANSFORM
    LUA_SCRIPT = LOCK_ON_REST_LUA

    def __init__(self, faction, request=None):
        super().__init__(post=faction, request=request)
        self.faction = faction

    def get_nickname(self):
        return self.faction.faction_name or self.faction.slug or str(self.faction.pk)

    def get_description(self):
        return f"{self.faction.faction_name} Faction Board"

    def get_color_diffuse(self):
        return _hex_to_rgb_floats(self.faction.color)

    def get_front_image(self):
        sheet = getattr(self.faction, 'faction_sheet', None)
        if sheet and sheet.image_preview:
            return tts_image_url(sheet.image_preview, request=self.request)
        back = getattr(self.faction, 'faction_back', None)
        if back and back.image_preview:
            return tts_image_url(back.image_preview, request=self.request)
        return ""

    def get_back_image(self):
        back = getattr(self.faction, 'faction_back', None)
        if back and back.image_preview:
            return tts_image_url(back.image_preview, request=self.request)
        return self.get_front_image()

    def get_default_snap_points(self):
        sheet = getattr(self.faction, 'faction_sheet', None)
        if not (sheet and sheet.include_crafted_items):
            return []
        from the_forge.pdf_engine import pdf_y_delta_to_tts_z_delta
        # Decree pushes the whole header down; ability-bar extension only shifts
        # the crafted items by half the delta (they re-center in the new band).
        ability_shift_pts = (sheet.ability_bar_extra_h_pts or 0.0) / 2.0
        z_shift = pdf_y_delta_to_tts_z_delta(
            (sheet.decree_slide_pts or 0.0) + ability_shift_pts
        )
        if not z_shift:
            return list(CRAFTED_ITEMS_SNAP_POINTS)
        shifted = []
        for p in CRAFTED_ITEMS_SNAP_POINTS:
            sp = {
                "Position": dict(p["Position"]),
                "Rotation": dict(p["Rotation"]),
            }
            sp["Position"]["z"] = p["Position"]["z"] + z_shift
            shifted.append(sp)
        return shifted

    def get_snap_points(self):
        sheet = getattr(self.faction, 'faction_sheet', None)
        sheet_points = list(sheet.snap_points) if sheet and sheet.snap_points else []
        return sheet_points + self.get_default_snap_points()


class TTSForgedFactionDecree(TTSBoardBase):
    LUA_SCRIPT = LOCK_ON_REST_LUA
    DECREE_TRANSFORM = {
        "posX": 0.0,
        "posY": -0.113604546,
        "posZ": 18.8,
        "rotX": 0.0,
        "rotY": 180.0,
        "rotZ": 0.0,
        "scaleX": 9.035468,
        "scaleY": 1.0,
        "scaleZ": 9.035468,
    }
    DEFAULT_TRANSFORM = DECREE_TRANSFORM

    def __init__(self, faction, request=None):
        super().__init__(post=faction, request=request)
        self.faction = faction

    def get_nickname(self):
        return f"{self.faction.faction_name or self.faction.slug or self.faction.pk} Decree"

    def get_description(self):
        return f"{self.faction.faction_name} Decree"

    def get_color_diffuse(self):
        return _hex_to_rgb_floats(self.faction.color)

    def get_front_image(self):
        sheet = getattr(self.faction, 'faction_sheet', None)
        if sheet and sheet.decree_preview:
            return tts_image_url(sheet.decree_preview, request=self.request)
        return ""

    def get_back_image(self):
        return self.get_front_image()

    def get_transform(self):
        # Width is fixed by the renderer (canvas spans PAGE_W). The saved
        # decree.webp's height encodes how tall the cropped stack image is at
        # 150 DPI, so converting back to points and dividing by PAGE_H gives
        # the fraction of a faction sheet's height. Multiply by 9.035468 (the
        # FactionSheet TTS scale) so the decree tile sits next to the faction
        # sheet at matching units-per-inch.
        transform = dict(self.DEFAULT_TRANSFORM)
        sheet = getattr(self.faction, 'faction_sheet', None)
        if sheet and sheet.decree_preview:
            try:
                from PIL import Image as PILImage
                from .. decree_preview import DECREE_PREVIEW_DPI
                from ..pdf_engine import PAGE_H
                with PILImage.open(sheet.decree_preview.path) as im:
                    image_h_px = im.height
                image_h_pts = image_h_px * 72.0 / DECREE_PREVIEW_DPI
                scale = (image_h_pts / PAGE_H) * 9.035468
                transform['scaleX'] = scale
                transform['scaleZ'] = scale
            except Exception:
                pass
        return transform

    def get_default_snap_points(self):
        return []

    def get_snap_points(self):
        sheet = getattr(self.faction, 'faction_sheet', None)
        decree = sheet.decrees.first() if sheet else None
        n = decree.card_slots.count() if decree else 0
        if not n:
            return []
        from the_forge.decree_preview import decree_snap_points
        transform = self.get_transform()
        return decree_snap_points(
            n,
            decree_scale_x=transform['scaleX'],
            decree_scale_z=transform['scaleZ'],
        )


# ---------------------------------------------------------------------------
# Buildings & tokens — Piece export
# ---------------------------------------------------------------------------

PIECE_BASE_Y = 1.5
PIECE_STACK_Y_STEP = 0.12
PIECE_GROUP_X_STEP = 1.5
# Fallback origin: just NW of the faction board. The board sits at world
# origin with scale 9.035, so its NW corner is roughly (-4.5, +4.5) on (X, Z).
# Step the X out a bit further so stacks don't overhang the board edge.
PIECE_FALLBACK_ORIGIN_X = -13.0
PIECE_FALLBACK_ORIGIN_Z = 12.0
PIECE_ROT_Y = 180.0
BUILDING_SCALE = 0.70858
TOKEN_SCALE = 0.703911364

PIECE_TYPE_TO_TRACK_TYPE = {'B': 'building', 'T': 'token'}

MARKER_FALLBACK_ORIGIN_X = 13.0
MARKER_FALLBACK_ORIGIN_Z = 12.0
MARKER_GROUP_X_STEP = 1.5

# Fallback spawn location for ForgedDeckGroup decks that don't match a
# CardPile snap point. Sits north of the marker stack (higher Z in TTS world coords) so it doesn't overlap the markers; subsequent decks step east (higher X).
DECK_FALLBACK_ORIGIN_X = MARKER_FALLBACK_ORIGIN_X + 1.5
DECK_FALLBACK_ORIGIN_Z = MARKER_FALLBACK_ORIGIN_Z + 5.0  # ~5u north of markers
DECK_FALLBACK_X_STEP = 6.5


class TTSForgedPiece:
    """Builds TTS Custom_Token / Custom_Tile dicts for a Piece.

    Buildings (`type='B'`) → Custom_Token (the Mouse Base shape).
    Tokens   (`type='T'`) → Custom_Tile with CustomTile.Type=2 (the Sympathy shape).
    """

    def __init__(self, piece, request=None):
        self.piece = piece
        self.request = request

    def _is_token(self):
        return self.piece.type == 'T'

    def _scale(self):
        return TOKEN_SCALE if self._is_token() else BUILDING_SCALE

    def _color_diffuse(self):
        faction_color = getattr(self.piece.faction, 'color', None)
        return _hex_to_rgb_floats(faction_color)

    def _custom_image(self):
        front = tts_image_url(self.piece.small_icon, request=self.request)
        back = (tts_image_url(self.piece.back_image, request=self.request)
                if self.piece.back_image else "")
        # Tokens use CustomTile.Type=2 (hex/circle, e.g. Sympathy);
        # buildings use CustomTile.Type=3 (square, e.g. Recruiter).
        return {
            "ImageURL": front,
            "ImageSecondaryURL": back,
            "ImageScalar": 1.0,
            "WidthScale": 0.0,
            "CustomTile": {
                "Type": 2 if self._is_token() else 3,
                "Thickness": 0.1,
                "Stackable": False,
                "Stretch": True,
            },
        }

    def _base_dict(self, transform):
        return {
            "GUID": generate_tts_guid(),
            "Name": "Custom_Tile",
            "Transform": transform,
            "Nickname": self.piece.name or self.piece.get_type_display(),
            "Description": "",
            "GMNotes": "",
            "AltLookAngle": {"x": 0.0, "y": 0.0, "z": 0.0},
            "ColorDiffuse": self._color_diffuse(),
            "LayoutGroupSortIndex": 0,
            "Value": 0,
            "Locked": False,
            "Grid": True,
            "Snap": True,
            "IgnoreFoW": False,
            "MeasureMovement": False,
            "DragSelectable": True,
            "Autoraise": True,
            "Sticky": True,
            "Tooltip": True,
            "GridProjection": False,
            "HideWhenFaceDown": False,
            "Hands": False,
            "CustomImage": self._custom_image(),
            "LuaScript": "",
            "LuaScriptState": "",
            "XmlUI": "",
        }

    def at_world_point(self, world_x, world_z, world_y=None):
        scale = self._scale()
        return self._base_dict({
            "posX": world_x,
            "posY": PIECE_BASE_Y if world_y is None else world_y,
            "posZ": world_z,
            "rotX": 0.0, "rotY": PIECE_ROT_Y, "rotZ": 0.0,
            "scaleX": scale, "scaleY": 1.0, "scaleZ": scale,
        })

    def at_fallback_stack(self, group_index, copy_index):
        scale = self._scale()
        return self._base_dict({
            "posX": PIECE_FALLBACK_ORIGIN_X - group_index * PIECE_GROUP_X_STEP,
            "posY": PIECE_BASE_Y + copy_index * PIECE_STACK_Y_STEP,
            "posZ": PIECE_FALLBACK_ORIGIN_Z,
            "rotX": 0.0, "rotY": PIECE_ROT_Y, "rotZ": 0.0,
            "scaleX": scale, "scaleY": 1.0, "scaleZ": scale,
        })


class TTSForgedMarker:
    """Builds a TTS Custom_Tile dict for a faction's VP or Relationship marker.

    Same shape as a building piece (CustomTile.Type=3, square, BUILDING_SCALE),
    using the faction's color as ColorDiffuse and the marker image as ImageURL.
    """

    def __init__(self, faction, image_field, nickname, request=None):
        self.faction = faction
        self.image_field = image_field
        self.nickname = nickname
        self.request = request

    def _color_diffuse(self):
        return _hex_to_rgb_floats(getattr(self.faction, 'color', None))

    def _custom_image(self):
        return {
            "ImageURL": tts_image_url(self.image_field, request=self.request),
            "ImageSecondaryURL": "",
            "ImageScalar": 1.0,
            "WidthScale": 0.0,
            "CustomTile": {
                "Type": 3,
                "Thickness": 0.1,
                "Stackable": False,
                "Stretch": True,
            },
        }

    def at_world_point(self, world_x, world_z):
        return {
            "GUID": generate_tts_guid(),
            "Name": "Custom_Tile",
            "Transform": {
                "posX": world_x,
                "posY": PIECE_BASE_Y,
                "posZ": world_z,
                "rotX": 0.0, "rotY": PIECE_ROT_Y, "rotZ": 0.0,
                "scaleX": BUILDING_SCALE, "scaleY": 1.0, "scaleZ": BUILDING_SCALE,
            },
            "Nickname": self.nickname,
            "Description": "",
            "GMNotes": "",
            "AltLookAngle": {"x": 0.0, "y": 0.0, "z": 0.0},
            "ColorDiffuse": self._color_diffuse(),
            "LayoutGroupSortIndex": 0,
            "Value": 0,
            "Locked": False,
            "Grid": True,
            "Snap": True,
            "IgnoreFoW": False,
            "MeasureMovement": False,
            "DragSelectable": True,
            "Autoraise": True,
            "Sticky": True,
            "Tooltip": True,
            "GridProjection": False,
            "HideWhenFaceDown": False,
            "Hands": False,
            "CustomImage": self._custom_image(),
            "LuaScript": "",
            "LuaScriptState": "",
            "XmlUI": "",
        }


def _norm_name(s):
    """HTML-stripped, whitespace-trimmed, lowercased — matching key."""
    if not s:
        return ""
    from django.utils.html import strip_tags
    return strip_tags(s).strip().lower()


def _snap_to_world(sp):
    """Convert a snap point's local Position (TTS Custom_Tile local coords on
    the faction board) to world coords. The faction board sits at world origin
    with rotY=180, so X and Z are mirrored when projected to world."""
    pos = sp.get("Position", {}) or {}
    local_x = pos.get("x", 0.0)
    local_z = pos.get("z", 0.0)
    local_y = pos.get("y", 0.1)
    scale = FACTION_BOARD_TRANSFORM["scaleX"]
    world_x = -local_x * scale + FACTION_BOARD_TRANSFORM["posX"]
    world_z = -local_z * scale + FACTION_BOARD_TRANSFORM["posZ"]
    world_y = local_y + 0.5  # spawn slightly above so it settles onto the slot
    return world_x, world_z, world_y


def place_pieces_for_back(back, sheet, request):
    """Return TTS object dicts for all buildings/tokens on `back`.

    Pieces whose names match a CardboardTrack title (or row title) on `sheet`
    get placed at that track's snap points; everything else falls back to an
    offset stack next to the board.
    """
    pieces = list(back.faction.pieces.filter(type__in=['B', 'T']).order_by('type', 'pk'))
    if not pieces:
        return []

    # Build the snap-point indices, keyed by (track_type, normalized_name).
    track_buckets = {}
    row_buckets = {}
    if sheet and sheet.snap_points:
        for sp in sheet.snap_points:
            ttype = sp.get('track_type')
            if ttype not in ('building', 'token'):
                continue
            t_key = (ttype, _norm_name(sp.get('track_title')))
            track_buckets.setdefault(t_key, []).append(sp)
            r_norm = _norm_name(sp.get('row_title'))
            if r_norm:
                row_buckets.setdefault((ttype, r_norm), []).append(sp)

    consumed_ids = set()  # id(snap_dict): same dict appears in both buckets

    def _take(key, n, source):
        out = []
        for sp in source.get(key, []):
            if id(sp) in consumed_ids:
                continue
            consumed_ids.add(id(sp))
            out.append(sp)
            if len(out) >= n:
                break
        return out

    objects = []
    fallback_queue = []  # list of (piece, remaining_quantity)

    for piece in pieces:
        if not piece.small_icon:
            continue
        ttype = PIECE_TYPE_TO_TRACK_TYPE[piece.type]
        name_key = _norm_name(piece.name)
        builder = TTSForgedPiece(piece, request=request)
        remaining = piece.quantity

        matched = _take((ttype, name_key), remaining, track_buckets) if name_key else []
        if name_key and len(matched) < remaining:
            matched.extend(_take((ttype, name_key), remaining - len(matched), row_buckets))

        for sp in matched:
            world_x, world_z, world_y = _snap_to_world(sp)
            objects.append(builder.at_world_point(world_x, world_z, world_y))

        remaining -= len(matched)
        if remaining > 0:
            fallback_queue.append((piece, remaining))

    for fallback_index, (piece, remaining) in enumerate(fallback_queue):
        builder = TTSForgedPiece(piece, request=request)
        for copy_index in range(remaining):
            objects.append(builder.at_fallback_stack(fallback_index, copy_index))

    return objects


def place_markers_for_faction(faction, request):
    """Return TTS object dicts for the faction's VP and Relationship markers.

    Markers stack east of the faction board, mirroring the piece fallback
    stacks that sit west of it. Missing markers are skipped.
    """
    candidates = [
        (getattr(faction, 'vp_marker', None),
         f"{faction.faction_name or 'Faction'} VP"),
        (getattr(faction, 'relationship_marker', None),
         f"{faction.faction_name or 'Faction'} Relationship"),
    ]
    objects = []
    group_index = 0
    for image_field, nickname in candidates:
        if not image_field:
            continue
        world_x = MARKER_FALLBACK_ORIGIN_X + group_index * MARKER_GROUP_X_STEP
        world_z = MARKER_FALLBACK_ORIGIN_Z
        builder = TTSForgedMarker(faction, image_field, nickname, request=request)
        objects.append(builder.at_world_point(world_x, world_z))
        group_index += 1
    return objects
