from the_keep.services.tts import (
    tts_image_url,
    wrap_tts_save,
    TTSBoardBase,
    FACTION_BOARD_TRANSFORM,
    DEFAULT_TRACKER_SNAP_POINTS,
)


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
