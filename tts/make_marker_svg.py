"""Draw the clearing suit marker's outline, seen from above, as an SVG for the
map builder (the_keep/static/images/map_builder/clearing_marker.svg).

It's traced from the in-game mesh (the_keep/static/images/tts/assets/marker.obj,
copied from the RTT mod): the boundary of the mesh's flat top. The SVG has the
marker's spot at (0, 0) and its bump -- the side facing away from the clearing --
pointing up, so the builder only has to rotate it towards the clearing centre.

    python3 tts/make_marker_svg.py [preview.png]

Re-run it if marker.obj is refreshed. With a path, it also writes a PNG preview.
"""
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MESH = ROOT / 'the_keep' / 'static' / 'images' / 'tts' / 'assets' / 'marker.obj'
OUT = ROOT / 'the_keep' / 'static' / 'images' / 'map_builder' / 'clearing_marker.svg'
UNITS = 100   # SVG units per mesh unit


def read_mesh(path):
    vertices, faces = [], []
    for line in path.read_text().splitlines():
        parts = line.split()
        if not parts:
            continue
        if parts[0] == 'v':
            vertices.append(tuple(float(v) for v in parts[1:4]))
        elif parts[0] == 'f':
            faces.append([int(p.split('/')[0]) - 1 for p in parts[1:]])
    return vertices, faces


def boundary_loops(faces):
    """Closed loops of the edges used by exactly one face."""
    count = defaultdict(int)
    for face in faces:
        for a, b in zip(face, face[1:] + face[:1]):
            count[frozenset((a, b))] += 1
    neighbours = defaultdict(list)
    for edge, n in count.items():
        if n == 1:
            a, b = tuple(edge)
            neighbours[a].append(b)
            neighbours[b].append(a)
    loops, seen = [], set()
    for start in neighbours:
        if start in seen:
            continue
        loop, prev, cur = [start], None, start
        seen.add(start)
        while True:
            options = [n for n in neighbours[cur] if n != prev]
            if not options:
                break
            nxt = options[0]
            if nxt == start:
                loops.append(loop)
                break
            if nxt in seen:
                break
            loop.append(nxt)
            seen.add(nxt)
            prev, cur = cur, nxt
    return loops


def area(points):
    return abs(sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(points, points[1:] + points[:1]))) / 2


def main(preview=None):
    vertices, faces = read_mesh(MESH)
    top = max(v[1] for v in vertices)
    cap = [f for f in faces if all(abs(vertices[i][1] - top) < 0.001 for i in f)]
    loops = boundary_loops(cap)
    if not loops:
        # No flat top: outline every face seen from above, keep the outermost loop.
        loops = boundary_loops(faces)
    # Mesh (x, z) -> SVG (x, -z): the bump (+z) points up.
    shapes = [[(vertices[i][0] * UNITS, -vertices[i][2] * UNITS) for i in loop] for loop in loops]
    # The marker has no holes, and its top surface is duplicated in the mesh
    # (two identical loops would cancel out under evenodd): keep the outermost.
    shapes = [max(shapes, key=area)]
    xs = [x for s in shapes for x, _ in s]
    ys = [y for s in shapes for _, y in s]
    x0, y0, w, h = min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)
    path = ' '.join('M ' + ' L '.join(f'{x:.1f} {y:.1f}' for x, y in s) + ' Z' for s in shapes)
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{x0:.1f} {y0:.1f} {w:.1f} {h:.1f}">\n'
        f'  <!-- The clearing suit marker from above, traced from marker.obj by tts/make_marker_svg.py.\n'
        f'       (0, 0) is the marker\'s spot; its bump (facing away from the clearing) points up. -->\n'
        f'  <path d="{path}" fill="currentColor"\n'
        f'        style="stroke: var(--mb-marker-stroke, #231f20); stroke-width: 2;"\n'
        f'        vector-effect="non-scaling-stroke" stroke-linejoin="round"/>\n'
        f'</svg>\n'
    )
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(svg)
    print(f"Wrote {OUT.relative_to(ROOT)}: viewBox {x0:.1f} {y0:.1f} {w:.1f} {h:.1f}")

    if preview:
        from PIL import Image, ImageDraw
        scale = 2
        im = Image.new('RGB', (int(w * scale) + 20, int(h * scale) + 20), 'white')
        d = ImageDraw.Draw(im)
        for s in shapes:
            d.polygon([((x - x0) * scale + 10, (y - y0) * scale + 10) for x, y in s], fill=(200, 80, 60), outline='black')
        ax, ay = (0 - x0) * scale + 10, (0 - y0) * scale + 10
        d.ellipse([ax - 5, ay - 5, ax + 5, ay + 5], fill='blue')
        im.save(preview)


if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else None)
