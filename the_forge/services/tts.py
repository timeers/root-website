import json
import os
from functools import lru_cache

from the_keep.services.tts import (
    tts_image_url,
    wrap_tts_save,
    TTSBoardBase,
    FACTION_BOARD_TRANSFORM,
    DEFAULT_TRACKER_SNAP_POINTS,
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
            return list(DEFAULT_TRACKER_SNAP_POINTS)
        shifted = []
        for p in DEFAULT_TRACKER_SNAP_POINTS:
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
