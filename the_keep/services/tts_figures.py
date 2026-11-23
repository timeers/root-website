"""Tabletop Simulator figures built from a piece's image: a warrior meeple PNG
becomes a figure mesh (OBJ) and its texture, like the official RTT warriors.

The official warriors (RTT Workshop 3800311405, each faction's "<X> Supply"
bag) are `Custom_Model` figurines: the meeple's outline extruded into a slab,
standing on its base, centred across. Both faces show the art; the sides show
a plain colour patch of the texture. In-game they're 1.2-1.8 tall (1.55
here), 0.50-0.64 deep (0.56 here), as wide as the art's shape, and most use
the scale (2.34, 2.34, 2.93), which the mesh units below follow. Ours bake the
colour into the texture (as the Knaves do) and keep ColorDiffuse white.

The outline is traced between pixels (marching squares), so anti-aliased art
gives a smooth curve; hard-edged art, which only cuts each pixel step at 45
degrees, is smoothed along the outline first. The mesh carries its own normals:
flat faces, smooth sides, crisp ear tips and rim.

The builder only works on images, numbers and URLs: no models, so any
component can use it (supplies, the pieces of other posts, ...). The storage
helper `ensure_figure` works on any piece-like model with the figure fields.
"""
import hashlib
import io
import logging
import math
import os
from dataclasses import dataclass, field
from typing import List, Tuple

from PIL import Image

logger = logging.getLogger(__name__)

FIGURE_SCALE = (2.34, 2.34, 2.93)
FIGURE_HEIGHT = 1.55
FIGURE_DEPTH = 0.56
# Bump when the mesh or texture output changes, so stored figures regenerate.
FIGURE_VERSION = 2
TEXTURE_SIZE = 1024
# Where the sides sample the texture: inside its plain colour area.
SIDE_UV = (0.75, 0.25)

# Outline detail, per 256 px of image (build_shape scales them with the image,
# so a 256 px and a 400 px warrior get about the same detail). Tuned on two
# meeples: a hard-edged one gives ~90 points, an anti-aliased one ~110; the
# official warriors' meshes have 56-462 vertices.
OUTLINE_TOLERANCE = 0.15      # px: how far the simplified outline may stray
MAX_OUTLINE_POINTS = 300      # an intricate shape simplifies harder to stay under
SMOOTH_RADIUS = 2             # points either side (traced points are ~1 px apart)
SMOOTH_PASSES = 2
# An outline is hard-edged (no anti-aliasing: smooth it) when fewer than this
# share of its points cross a partly transparent pixel. Measured on the outline,
# not the whole image, so translucent detail inside the art doesn't count.
HARD_EDGE_SHARE = 0.25
# Neighbouring side faces turning less than this share a smoothed normal;
# sharper corners (ear tips, the base) stay crisp.
SIDE_SMOOTH_ANGLE = 60.0


class FigureError(ValueError):
    """The image can't become a figure (no transparency, or nothing opaque)."""


@dataclass
class FigureShape:
    outline: List[Tuple[float, float]]           # image pixels, a closed loop
    triangles: List[Tuple[int, int, int]]         # indexes into outline
    image_size: Tuple[int, int]
    width: float                                  # world units
    height: float
    depth: float
    art_box: Tuple[float, float, float, float] = field(default=(0, 0, 0, 0))  # in the texture, 0-1

    @property
    def size(self):
        return [round(self.width, 4), round(self.depth, 4), round(self.height, 4)]


# ── Outline ──────────────────────────────────────────────────────────────────

# Marching squares: for each cell's corner pattern (top-left, top-right,
# bottom-right, bottom-left bits), the segments it adds, from one cell edge to
# another, oriented so the shape is on the right (clockwise on screen). The
# saddles (5, 10) are decided by the cell's mean.
_CELL_SEGMENTS = {
    1: (('L', 'B'),), 2: (('B', 'R'),), 3: (('L', 'R'),), 4: (('R', 'T'),),
    6: (('B', 'T'),), 7: (('L', 'T'),), 8: (('T', 'L'),), 9: (('T', 'B'),),
    11: (('T', 'R'),), 12: (('R', 'L'),), 13: (('R', 'B'),), 14: (('B', 'L'),),
}
_SADDLES = {
    (5, True): (('L', 'T'), ('R', 'B')), (5, False): (('L', 'B'), ('R', 'T')),
    (10, True): (('T', 'R'), ('B', 'L')), (10, False): (('T', 'L'), ('B', 'R')),
}


@dataclass
class Trace:
    points: List[Tuple[float, float]]     # image pixels, a closed loop
    soft_share: float                     # share of points crossing a partly transparent pixel


def trace_outline(image, level=128):
    """The largest outer loop of the shape, traced between pixels (marching
    squares on the alpha, sampled at pixel centres): each point sits where the
    alpha crosses `level`, so anti-aliased art gives a smooth outline. Holes
    and separate parts are ignored: a figure is one piece."""
    alpha = image.getchannel('A')
    box = alpha.getbbox()
    if box is None:
        raise FigureError("The image has no opaque shape to trace.")
    w, h = alpha.size
    px = alpha.load()

    def a(x, y):
        return px[x, y] if 0 <= x < w and 0 <= y < h else 0

    def cross(v0, v1):
        return (level - v0) / (v1 - v0) if v1 != v0 else 0.5

    points, soft, links = {}, {}, {}
    left, top, right, bottom = box
    for y in range(top - 1, bottom):
        for x in range(left - 1, right):
            tl, tr, br, bl = a(x, y), a(x + 1, y), a(x + 1, y + 1), a(x, y + 1)
            case = (tl >= level) << 3 | (tr >= level) << 2 | (br >= level) << 1 | (bl >= level)
            if case in (0, 15):
                continue
            edges = {
                'T': (('h', x, y), x + cross(tl, tr), y, tl, tr),
                'R': (('v', x + 1, y), x + 1, y + cross(tr, br), tr, br),
                'B': (('h', x, y + 1), x + cross(bl, br), y + 1, bl, br),
                'L': (('v', x, y), x, y + cross(tl, bl), tl, bl),
            }
            segments = _CELL_SEGMENTS.get(case) or _SADDLES[(case, (tl + tr + br + bl) / 4 >= level)]
            for start, end in segments:
                for key, ex, ey, v0, v1 in (edges[start], edges[end]):
                    if key not in points:
                        points[key] = (ex + 0.5, ey + 0.5)       # pixel centres
                        soft[key] = 0 < v0 < 255 or 0 < v1 < 255
                links[edges[start][0]] = edges[end][0]

    loops = []
    while links:
        first = next(iter(links))
        key, loop = first, []
        while True:
            loop.append(key)
            key = links.pop(key, None)
            if key is None or key == first:
                break
        if len(loop) >= 3:
            loops.append(loop)
    if not loops:
        raise FigureError("The image has no opaque shape to trace.")
    best = max(loops, key=lambda keys: abs(_area([points[k] for k in keys])))
    return Trace([points[k] for k in best], sum(soft[k] for k in best) / len(best))


def smooth_outline(loop, radius, passes):
    """A moving average along a closed loop: each point becomes the mean of
    itself and `radius` neighbours either side, `passes` times. Rounds off
    the 45-degree steps a hard-edged image leaves."""
    n = len(loop)
    span = 2 * radius + 1
    for _ in range(passes):
        loop = [(sum(loop[(i + j) % n][0] for j in range(-radius, radius + 1)) / span,
                 sum(loop[(i + j) % n][1] for j in range(-radius, radius + 1)) / span)
                for i in range(n)]
    return loop


def _area(points):
    return sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(points, points[1:] + points[:1])) / 2


def simplify(loop, tolerance):
    """Douglas-Peucker on a closed loop."""
    def dp(points):
        if len(points) < 3:
            return points
        a, b = points[0], points[-1]
        dx, dy = b[0] - a[0], b[1] - a[1]
        length = math.hypot(dx, dy) or 1e-9
        index, dist = max(((i, abs(dy * (p[0] - a[0]) - dx * (p[1] - a[1])) / length)
                           for i, p in enumerate(points[1:-1], 1)), key=lambda t: t[1])
        if dist > tolerance:
            return dp(points[:index + 1])[:-1] + dp(points[index:])
        return [a, b]

    if len(loop) < 4:
        return list(loop)
    far = max(range(len(loop)), key=lambda i: math.dist(loop[0], loop[i]))
    out = dp(loop[:far + 1])[:-1] + dp(loop[far:] + [loop[0]])[:-1]
    return _drop_collinear(out)


def _drop_collinear(points):
    out = []
    n = len(points)
    for i in range(n):
        a, b, c = points[i - 1], points[i], points[(i + 1) % n]
        if b == a:
            continue
        if (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]) != 0:
            out.append(b)
    return out


def triangulate(polygon):
    """Ear clipping. Returns index triangles, counter-clockwise in the
    polygon's own coordinates."""
    idx = list(range(len(polygon)))
    if _area(polygon) < 0:
        idx.reverse()

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    def inside(p, a, b, c):
        return cross(a, b, p) >= 0 and cross(b, c, p) >= 0 and cross(c, a, p) >= 0

    triangles = []
    while len(idx) > 3:
        for k in range(len(idx)):
            i0, i1, i2 = idx[k - 1], idx[k], idx[(k + 1) % len(idx)]
            a, b, c = polygon[i0], polygon[i1], polygon[i2]
            if cross(a, b, c) <= 0:
                continue
            if any(inside(polygon[j], a, b, c) for j in idx if j not in (i0, i1, i2)):
                continue
            triangles.append((i0, i1, i2))
            idx.pop(k)
            break
        else:
            # Degenerate leftovers (a self-touching outline): close with a fan.
            for k in range(1, len(idx) - 1):
                triangles.append((idx[0], idx[k], idx[k + 1]))
            return triangles
    if len(idx) == 3:
        triangles.append(tuple(idx))
    return triangles


# ── Shape, mesh, texture ────────────────────────────────────────────────────

def art_box(image_size, size=TEXTURE_SIZE):
    """Where the art sits in the texture (0-1: left, top, width, height): its
    top-left quarter, scaled to fit without stretching."""
    w, h = image_size
    quarter = 0.5
    k = quarter / max(w, h)
    bw, bh = w * k, h * k
    return ((quarter - bw) / 2, (quarter - bh) / 2, bw, bh)


def build_shape(image, height=FIGURE_HEIGHT, depth=FIGURE_DEPTH):
    if 'A' not in image.getbands():
        if image.mode == 'P' and 'transparency' in image.info:
            image = image.convert('RGBA')
        else:
            raise FigureError("The image has no transparency, so it has no shape to trace.")
    trace = trace_outline(image)
    s = max(image.size) / 256
    loop = trace.points
    if trace.soft_share < HARD_EDGE_SHARE:
        loop = smooth_outline(loop, max(1, round(SMOOTH_RADIUS * s)), SMOOTH_PASSES)
    tolerance = OUTLINE_TOLERANCE * s
    outline = simplify(loop, tolerance)
    while len(outline) > MAX_OUTLINE_POINTS:
        tolerance *= 2
        outline = simplify(loop, tolerance)
    if len(outline) < 3:
        raise FigureError("The image's shape is too small to trace.")
    if _area(outline) < 0:
        outline.reverse()          # clockwise on screen: figure_obj's side faces rely on it
    xs = [p[0] for p in outline]
    ys = [p[1] for p in outline]
    shape_h = max(ys) - min(ys)
    shape_w = max(xs) - min(xs)
    if shape_h <= 0 or shape_w <= 0:
        raise FigureError("The image's shape is too small to trace.")
    return FigureShape(
        outline=outline, triangles=triangulate(outline), image_size=image.size,
        width=shape_w * height / shape_h, height=height, depth=depth,
        art_box=art_box(image.size),
    )


def figure_obj(shape, scale=FIGURE_SCALE):
    """The figure's OBJ, in mesh units (world / scale): base at y = 0, centred
    across, depth along z. Front (+z) and back faces show the art, the sides
    the texture's colour area.

    Like the official warriors, every vertex has one texture coordinate and
    one normal: the caps and the sides have their own vertices, so the rim
    where they meet stays sharp. Side normals are smoothed across gentle turns
    and split at sharp corners (SIDE_SMOOTH_ANGLE)."""
    xs = [p[0] for p in shape.outline]
    ys = [p[1] for p in shape.outline]
    x_mid = (min(xs) + max(xs)) / 2
    y_base = max(ys)
    k = shape.height / (max(ys) - min(ys))        # world units per pixel
    half_z = shape.depth / 2 / scale[2]
    w, h = shape.image_size
    left, top, bw, bh = shape.art_box
    ring = [((px - x_mid) * k / scale[0], (y_base - py) * k / scale[1]) for px, py in shape.outline]
    n = len(ring)

    # Outward normal of each side face i (ring[i] -> ring[i+1]). The ring is the
    # picture the same way up (image top = mesh top), so the outline is still
    # clockwise here: outward is (-dy, dx).
    face_normals = []
    for i in range(n):
        (x0, y0), (x1, y1) = ring[i], ring[(i + 1) % n]
        length = math.hypot(x1 - x0, y1 - y0) or 1e-9
        face_normals.append((-(y1 - y0) / length, (x1 - x0) / length))

    vertices = [(x, y, z) for z in (half_z, -half_z) for x, y in ring]       # caps
    normals = [(0.0, 0.0, 1.0), (0.0, 0.0, -1.0)]
    # Side vertices per outline point: (ends face i-1, starts face i), as
    # (front, back, normal) indexes. One shared pair on a gentle turn, two pairs
    # on a sharp one.
    side = []
    for i in range(n):
        before, after = face_normals[i - 1], face_normals[i]
        dot = max(-1.0, min(1.0, before[0] * after[0] + before[1] * after[1]))
        x, y = ring[i]

        def pair(normal):
            normals.append((normal[0], normal[1], 0.0))
            vertices.extend([(x, y, half_z), (x, y, -half_z)])
            return (len(vertices) - 1, len(vertices), len(normals))

        if math.degrees(math.acos(dot)) < SIDE_SMOOTH_ANGLE:
            mx, my = before[0] + after[0], before[1] + after[1]
            length = math.hypot(mx, my) or 1e-9
            shared = pair((mx / length, my / length))
            side.append((shared, shared))
        else:
            side.append((pair(before), pair(after)))

    lines = ['# Root Database figure (the_keep/services/tts_figures.py)']
    lines += [f'v {x:.5f} {y:.5f} {z:.5f}' for x, y, z in vertices]
    for px, py in shape.outline:
        lines.append(f'vt {left + px / w * bw:.5f} {1 - (top + py / h * bh):.5f}')
    lines.append(f'vt {SIDE_UV[0]} {SIDE_UV[1]}')
    lines += [f'vn {x:.5f} {y:.5f} {z:.5f}' for x, y, z in normals]
    side_uv = n + 1
    # Image y runs down, so the outline's winding is mirrored in mesh space:
    # the front cap takes the triangles reversed, the back cap as they are.
    for a, b, c in shape.triangles:
        lines.append(f'f {c + 1}/{c + 1}/1 {b + 1}/{b + 1}/1 {a + 1}/{a + 1}/1')
        lines.append(f'f {a + 1 + n}/{a + 1}/2 {b + 1 + n}/{b + 1}/2 {c + 1 + n}/{c + 1}/2')
    for i in range(n):
        j = (i + 1) % n
        fi, bi, ni = side[i][1]          # starts face i
        fj, bj, nj = side[j][0]          # ends face i
        lines.append(f'f {fi}/{side_uv}/{ni} {fj}/{side_uv}/{nj} {bj}/{side_uv}/{nj} {bi}/{side_uv}/{ni}')
    return '\n'.join(lines) + '\n'


def parse_colour(colour):
    """'#rrggbb' (or an (r, g, b) tuple) to an RGB tuple; white when unset."""
    if isinstance(colour, (tuple, list)) and len(colour) >= 3:
        return tuple(int(c) for c in colour[:3])
    value = (colour or '').strip().lstrip('#')
    if len(value) == 6:
        try:
            return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))
        except ValueError:
            pass
    return (255, 255, 255)


def art_texture(image, colour, size=TEXTURE_SIZE):
    """The art over the colour in the texture's top-left quarter (art_box),
    the rest solid colour: the faces sample the art, the sides the colour."""
    rgb = parse_colour(colour)
    texture = Image.new('RGB', (size, size), rgb)
    left, top, bw, bh = art_box(image.size, size)
    art = image.convert('RGBA').resize((max(1, round(bw * size)), max(1, round(bh * size))), Image.LANCZOS)
    backing = Image.new('RGBA', art.size, rgb + (255,))
    backing.alpha_composite(art)
    texture.paste(backing.convert('RGB'), (round(left * size), round(top * size)))
    return texture


def figure_object(mesh_url, texture_url, *, nickname, transform=None, scale=FIGURE_SCALE):
    """A TTS figurine (Custom_Model), set up like the official warriors."""
    from .tts import generate_tts_guid
    base = {'posX': 0.0, 'posY': 0.0, 'posZ': 0.0, 'rotX': 0.0, 'rotY': 180.0, 'rotZ': 0.0,
            'scaleX': scale[0], 'scaleY': scale[1], 'scaleZ': scale[2]}
    base.update(transform or {})
    return {
        'GUID': generate_tts_guid(),
        'Name': 'Custom_Model',
        'Transform': base,
        'Nickname': nickname,
        'Description': '',
        'GMNotes': '',
        'ColorDiffuse': {'r': 1.0, 'g': 1.0, 'b': 1.0},
        'LayoutGroupSortIndex': 0,
        'Value': 0,
        'Locked': False,
        'Grid': True,
        'Snap': False,
        'IgnoreFoW': False,
        'MeasureMovement': False,
        'DragSelectable': True,
        'Autoraise': True,
        'Sticky': True,
        'Tooltip': True,
        'GridProjection': False,
        'HideWhenFaceDown': False,
        'Hands': False,
        'CustomMesh': {
            'MeshURL': mesh_url,
            'DiffuseURL': texture_url,
            'NormalURL': '',
            'ColliderURL': mesh_url,
            'Convex': True,
            'MaterialIndex': 1,
            'TypeIndex': 1,
            'CastShadows': True,
        },
        'LuaScript': '',
        'LuaScriptState': '',
        'XmlUI': '',
    }


# ── Storage: a piece's figure files ─────────────────────────────────────────

FIGURE_FIELDS = ('figure_mesh', 'figure_texture', 'figure_fingerprint', 'figure_size')


def _file_sig(field_file):
    try:
        mtime = os.path.getmtime(field_file.path)
    except (OSError, ValueError, NotImplementedError):
        mtime = 0
    return f'{field_file.name}@{mtime}'


def figure_fingerprint(piece, colour):
    """Everything a piece's figure is made from."""
    payload = '|'.join([_file_sig(piece.small_icon), '%02x%02x%02x' % parse_colour(colour),
                        str(FIGURE_VERSION), str(FIGURE_HEIGHT), str(FIGURE_DEPTH)])
    return hashlib.md5(payload.encode()).hexdigest()


def _files_exist(piece):
    for name in ('figure_mesh', 'figure_texture'):
        f = getattr(piece, name)
        if not f or not f.name:
            return False
        try:
            if not f.storage.exists(f.name):
                return False
        except Exception:
            return False
    return True


def ensure_figure(piece, colour):
    """(figure_mesh, figure_texture, [width, depth, height]) for a piece-like
    model with a `small_icon` and the figure fields, generating the files when
    they're missing or out of date. None when the image can't be a figure (the
    piece is then left out of the export; it never fails it).

    The fields are written with a queryset update, so saving doesn't run the
    model's save() / signals (e.g. the Forge's last_updated bubbling)."""
    from django.core.files.base import ContentFile

    if not piece.small_icon:
        return None
    fingerprint = figure_fingerprint(piece, colour)
    if piece.figure_fingerprint == fingerprint and piece.figure_size and _files_exist(piece):
        return piece.figure_mesh, piece.figure_texture, piece.figure_size
    try:
        with piece.small_icon.open('rb') as handle:
            image = Image.open(handle)
            image.load()
        shape = build_shape(image)
        texture = art_texture(image, colour)
    except (FigureError, OSError, ValueError) as exc:
        logger.warning("No TTS figure for piece %s: %s", piece.pk, exc)
        return None

    out = io.BytesIO()
    texture.save(out, format='PNG', optimize=True)
    piece.figure_mesh.save('figure.obj', ContentFile(figure_obj(shape).encode()), save=False)
    piece.figure_texture.save('figure.png', ContentFile(out.getvalue()), save=False)
    piece.figure_fingerprint = fingerprint
    piece.figure_size = shape.size
    type(piece).objects.filter(pk=piece.pk).update(
        figure_mesh=piece.figure_mesh.name, figure_texture=piece.figure_texture.name,
        figure_fingerprint=fingerprint, figure_size=shape.size)
    return piece.figure_mesh, piece.figure_texture, shape.size


def clear_figure(piece):
    """Delete a piece's figure files and clear its figure fields (in memory;
    the caller saves)."""
    for name in ('figure_mesh', 'figure_texture'):
        f = getattr(piece, name, None)
        if f and f.name:
            try:
                f.delete(save=False)
            except Exception:
                pass
    piece.figure_fingerprint = ''
    piece.figure_size = None


def warrior_nickname(piece_name, faction_name):
    """'<name> Warrior' (never doubled), or '<faction> Warrior': the RTT
    coordinator recognises warriors by a name ending in "Warrior"."""
    name = (piece_name or '').strip()
    if name.lower().endswith('warriors'):        # "Cat Warriors" -> "Cat Warrior"
        name = name[:-1]
    if name and name.lower() != 'warrior':
        return name if name.lower().endswith('warrior') else f'{name} Warrior'
    return f'{faction_name} Warrior'.strip()


def warrior_groups(pieces, colour, faction_name, request=None):
    """SupplyGroups for warrior pieces (Forge or Keep, anything with
    small_icon, name, quantity and the figure fields), generating their
    figures as needed. A warrior whose image can't be a figure is skipped."""
    from .tts import tts_image_url
    from .tts_supply import SupplyGroup
    pieces = list(pieces)
    groups = []
    for piece in pieces:
        figure = ensure_figure(piece, colour)
        if figure is None:
            continue
        mesh, texture, size = figure
        warrior = figure_object(tts_image_url(mesh, request=request), tts_image_url(texture, request=request),
                                nickname=warrior_nickname(piece.name, faction_name))
        # The RTT coordinator finds supplies by a name ending in "Supply".
        if len(pieces) > 1 and (piece.name or '').strip():
            supply = f'{faction_name} {piece.name.strip()} Supply'
        else:
            supply = f'{faction_name} Supply'
        groups.append(SupplyGroup(warrior, piece.quantity, supply, size=tuple(size)))
    return groups
