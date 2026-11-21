"""Extract the map pieces the site's map builder copies from the RTT mod.

Reads the RTT coordinator's script (the "Faction Selection" object's LuaScript,
saved to a file), takes the Summer map's item tiles, ruin tiles and suit
markers plus the clearing number tiles, downloads their art into
the_keep/static/images/tts/assets/ under readable names, and writes
tts/rtt_map_pieces.json with every URL replaced by a "static:<path>"
placeholder (resolved by the_keep/services/tts_map.py).

    python3 tts/extract_rtt_map_pieces.py path/to/coordinator.lua

Re-run it only to refresh the pieces. bird_marker.png is the site's own art and
is never overwritten.
"""
import copy
import json
import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / 'the_keep' / 'static' / 'images' / 'tts' / 'assets'
STATIC_PREFIX = 'images/tts/assets/'
TEMPLATE = ROOT / 'tts' / 'rtt_map_pieces.json'

# Readable names for the art, keyed by the end of the mod's file name.
NAMES = {
    'A7369B6E': 'coins_item', '72AC8D57': 'coins_item_back',
    'C14E9FB5': 'tea_item', 'C194F170': 'tea_item_back',
    '584B742B': 'sword_item', 'AA4AEE3D': 'sword_item_back',
    '3DA73CD7': 'hammer_item', '17B75381': 'hammer_item_back',
    '345E7F5C': 'crossbow_item', '7754FACE': 'crossbow_item_back',
    '17DC1528': 'boots_item', '8ADDA7FD': 'boots_item_back',
    '58D14843': 'bag_item', '7A66E3D9': 'bag_item_back',
    'CF1208DB': 'ruin',
    'B6E263D1': 'number_back',
    '86921B3F': 'boots_ruin_item', 'CC20B652': 'boots_ruin_item_back',
    '73FEBCF3': 'hammer_ruin_item', 'C5021A32': 'hammer_ruin_item_back',
    '5241E821': 'sword_ruin_item', 'EA579BE0': 'sword_ruin_item_back',
    '90327765': 'bag_ruin_item', '2BA165EE': 'bag_ruin_item_back',
}
# The suit markers' textures (the coordinator's RTT_SUIT_TEX) and shared mesh.
SUIT_TEXTURES = {
    'BF0F13D634B3B535D470396151B8A2F456507526': 'fox_marker',
    '195F0F3DFD439596DE7A5D941E93DE07BF820D11': 'rabbit_marker',
    'AF3D10F25ABE87305AF3F9A77B4C04B7761FBDDA': 'mouse_marker',
}
MARKER_MESH = 'marker'
# RTT_PRIO_SUMMERMAP's tiles, in the mod's order, read off their images.
PRIORITY_NUMBERS = [12, 8, 10, 11, 9, 7, 6, 4, 2, 5, 3, 1]


def lua_entries(source, start_marker):
    """(move_to, json) pairs of an EVERYTHING[...] catalogue entry."""
    start = source.index(start_marker)
    end = source.find('\nEVERYTHING[', start + len(start_marker))
    block = source[start:end if end > 0 else len(source)]
    pattern = re.compile(r"\{move_to=\{\s*([-\d.eE]+),\s*([-\d.eE]+),\s*([-\d.eE]+)\s*\},\s*json=\[(=*)\[(.*?)\]\4\]", re.S)
    return [((float(m.group(1)), float(m.group(2)), float(m.group(3))), json.loads(m.group(5)))
            for m in pattern.finditer(block)]


def lua_json_list(source, name):
    start = source.index(f'{name} = {{')
    block = source[start:source.index('\n}', start)]
    return [json.loads(m.group(1)) for m in re.finditer(r'\[==\[(.*?)\]==\]', block, re.S)]


def strip(obj):
    """A template object: no GUIDs, no position (rotation and scale stay)."""
    obj = copy.deepcopy(obj)

    def clean(o):
        o.pop('GUID', None)
        t = o.get('Transform')
        if t:
            for key in ('posX', 'posY', 'posZ'):
                t.pop(key, None)
        for child in o.get('ContainedObjects') or []:
            clean(child)
        for child in (o.get('States') or {}).values():
            clean(child)
    clean(obj)
    return obj


class Downloader:
    def __init__(self):
        self.files = {}

    def name_for(self, url):
        tail = url.rstrip('/').rsplit('/', 1)[-1]
        if tail in SUIT_TEXTURES:
            return SUIT_TEXTURES[tail]
        return NAMES.get(tail[-8:])

    def fetch(self, url, name):
        if url in self.files:
            return self.files[url]
        data = urllib.request.urlopen(url, timeout=60).read()
        if data[:8] == b'\x89PNG\r\n\x1a\n':
            ext = '.png'
        elif data[:3] == b'\xff\xd8\xff':
            ext = '.jpg'
        else:
            ext = '.obj'
        filename = name + ext
        (ASSETS / filename).write_bytes(data)
        self.files[url] = STATIC_PREFIX + filename
        return self.files[url]

    def localise(self, obj, mesh_name=None):
        """Replace every URL in obj with a static: placeholder."""
        def walk(value):
            if isinstance(value, dict):
                for key, item in value.items():
                    if (isinstance(item, str) and key.lower().endswith('url') and item
                            and not item.startswith('static:')):
                        if key in ('MeshURL', 'ColliderURL') and mesh_name:
                            name = mesh_name
                        else:
                            name = self.name_for(item)
                        if not name:
                            raise SystemExit(f"No name for {key} {item}; add it to NAMES.")
                        value[key] = 'static:' + self.fetch(item, name)
                    else:
                        walk(item)
            elif isinstance(value, list):
                for item in value:
                    walk(item)
        walk(obj)
        return obj


def main(coordinator_path):
    source = Path(coordinator_path).read_text()
    ASSETS.mkdir(parents=True, exist_ok=True)
    entries = lua_entries(source, "EVERYTHING['Maps']['Summer Map'] = {color='', data={")
    board_move, board = max(entries, key=lambda e: len(e[1].get('AttachedSnapPoints') or []))
    scale = board['Transform']['scaleX']

    def local(move):
        # The board is rotated 180 degrees: local = -(world offset) / scale.
        return (-(move[0] - board_move[0]) / scale, -(move[2] - board_move[2]) / scale)

    dl = Downloader()
    items = [(local(m), o) for m, o in entries if o['Name'] == 'Custom_Tile' and not o.get('Tags')]
    xs = sorted({round(p[0], 2) for p, _ in items}, reverse=True)   # image left is local +x
    zs = sorted({round(p[1], 2) for p, _ in items})                 # image top is local -z
    item_templates = []
    for (x, z), obj in items:
        col = min(range(len(xs)), key=lambda i: abs(xs[i] - x))
        row = min(range(len(zs)), key=lambda i: abs(zs[i] - z))
        item_templates.append({'slot': [col, row], 'object': dl.localise(strip(obj))})
    item_templates.sort(key=lambda t: (t['slot'][1], t['slot'][0]))

    ruins = [dl.localise(strip(o)) for _, o in entries if 'Ruin' in (o.get('Tags') or [])]

    markers = {}
    for _, obj in entries:
        if 'Clearing Marker' not in (obj.get('Tags') or []):
            continue
        suit = SUIT_TEXTURES[obj['CustomMesh']['DiffuseURL'].rsplit('/', 1)[-1]].split('_')[0]
        if suit not in markers:
            markers[suit] = dl.localise(strip(obj), mesh_name=MARKER_MESH)
    bird = copy.deepcopy(markers['fox'])
    bird['CustomMesh']['DiffuseURL'] = 'static:' + STATIC_PREFIX + 'bird_marker.png'
    markers['bird'] = bird

    numbers = {}
    for n, obj in zip(PRIORITY_NUMBERS, lua_json_list(source, 'RTT_PRIO_SUMMERMAP')):
        url = obj['CustomImage']['ImageURL']
        obj = strip(obj)
        obj['CustomImage']['ImageURL'] = 'static:' + dl.fetch(url, f'{n}_number')
        numbers[str(n)] = dl.localise(obj)   # the shared back

    # Steps from the unrounded positions (xs / zs are rounded for grouping).
    xs_exact = [p[0] for p, _ in items]
    zs_exact = [p[1] for p, _ in items]
    step_x = (max(xs_exact) - min(xs_exact)) / (len(xs) - 1)
    step_z = (max(zs_exact) - min(zs_exact)) / (len(zs) - 1)
    template = {
        'item_grid': {'cols': len(xs), 'rows': len(zs), 'step_x': round(step_x, 4), 'step_z': round(step_z, 4)},
        'items': item_templates,
        'ruins': ruins,
        'markers': markers,
        'numbers': dict(sorted(numbers.items(), key=lambda kv: int(kv[0]))),
    }
    TEMPLATE.write_text(json.dumps(template, indent=1) + '\n')
    print(f"Wrote {TEMPLATE.relative_to(ROOT)} and {len(dl.files)} files in {ASSETS.relative_to(ROOT)}")


if __name__ == '__main__':
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    main(sys.argv[1])
