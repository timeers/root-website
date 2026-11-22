"""Write the static supply bag meshes used by the_keep/services/tts_supply.py:

    the_keep/static/images/tts/assets/supply_round.obj    (tokens)
    the_keep/static/images/tts/assets/supply_square.obj   (buildings)

Both are built like the official Marquise "Wood Supply" bag: a flat prism of
radius 3.2 and height 1.55 in mesh units (shown at scale 0.425, so 2.72 across
and 0.66 tall in-game), origin at its base. The round one is a 50-segment
cylinder; the square one a rounded square of the same footprint, its corner
radius 20% of its side.

UVs follow the figure texture layout (tts_figures.art_texture): both caps on
the texture's top-left quarter (the art), the sides on its colour area.

Run from the repo root:  python3 tts/make_supply_meshes.py
"""
import math
from pathlib import Path

RADIUS = 3.2
HEIGHT = 1.55
SEGMENTS = 50
CORNER = 0.2            # of the square's side
CORNER_SEGMENTS = 8
SIDE_UV = (0.75, 0.25)  # tts_figures.SIDE_UV

OUT = Path(__file__).resolve().parent.parent / 'the_keep' / 'static' / 'images' / 'tts' / 'assets'


def round_outline():
    return [(RADIUS * math.cos(2 * math.pi * i / SEGMENTS), RADIUS * math.sin(2 * math.pi * i / SEGMENTS))
            for i in range(SEGMENTS)]


def square_outline():
    half = RADIUS
    r = 2 * half * CORNER
    inner = half - r
    points = []
    for cx, cy, start in ((inner, inner, 0), (-inner, inner, 90), (-inner, -inner, 180), (inner, -inner, 270)):
        for k in range(CORNER_SEGMENTS + 1):
            a = math.radians(start + 90 * k / CORNER_SEGMENTS)
            points.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    return points


def prism_obj(outline, name):
    """A prism over a convex outline (counter-clockwise, seen from +y), its
    base at y = 0. Caps are fans (the outline is convex)."""
    n = len(outline)
    lines = [f'# Root Database {name} supply bag (tts/make_supply_meshes.py)']
    for y in (HEIGHT, 0.0):
        for x, z in outline:
            lines.append(f'v {x:.5f} {y:.5f} {z:.5f}')
    for x, z in outline:
        # Cap UVs: the outline's box onto the texture's top-left quarter.
        u = (x / RADIUS + 1) / 4
        v = 1 - (z / RADIUS + 1) / 4
        lines.append(f'vt {u:.5f} {v:.5f}')
    lines.append(f'vt {SIDE_UV[0]} {SIDE_UV[1]}')
    side = n + 1
    # Counter-clockwise in (x, z) is clockwise seen from +y, so the top cap
    # takes each fan triangle reversed (normal +y) and the bottom as it is (-y).
    for k in range(1, n - 1):
        a, b, c = 0, k, k + 1
        lines.append(f'f {c + 1}/{c + 1} {b + 1}/{b + 1} {a + 1}/{a + 1}')                  # top, +y
        lines.append(f'f {a + 1 + n}/{a + 1} {b + 1 + n}/{b + 1} {c + 1 + n}/{c + 1}')      # bottom, -y
    for i in range(n):
        j = (i + 1) % n
        lines.append(f'f {i + 1}/{side} {j + 1}/{side} {j + 1 + n}/{side} {i + 1 + n}/{side}')
    return '\n'.join(lines) + '\n'


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    for name, outline in (('round', round_outline()), ('square', square_outline())):
        path = OUT / f'supply_{name}.obj'
        path.write_text(prism_obj(outline, name))
        print('wrote', path)


if __name__ == '__main__':
    main()
