"""Generate the PNG app icons that make Reader installable as a web app.

Android needs bitmap icons at 192 and 512 pixels in the web app manifest; the
SVG favicon alone doesn't count. This draws the same mark as
static/img/icon.svg (orange tile, two arcs and a dot), so if you change one,
change the other.

    python tools/make_icons.py      # writes static/img/app-*.png

Needs Pillow, which is only used here and isn't a runtime dependency.

Two kinds of icon come out of it:

  app-192.png, app-512.png             the rounded tile on transparent, as in the
                                       favicon ("any" in the manifest)
  app-maskable-192.png, -512.png       orange to the edges with the mark shrunk
                                       into the middle, so Android can cut it
                                       into a circle or squircle without
                                       clipping the arcs ("maskable")
"""
from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parent.parent / "static" / "img"

ORANGE = "#f08a4b"
INK = "#1c0f05"

# Everything below is in the 32-unit grid of icon.svg.
GRID = 32
CORNER = 7
STROKE = 3.2
ARC_CENTRE = (8, 24)
ARC_RADII = (8.5, 15.5)
DOT, DOT_RADIUS = (9, 23), 2.2

# How much of the maskable tile the mark fills. Android's safe zone is a circle
# 80% of the icon wide; at 0.8 the mark's farthest point sits at about 30%
# from the centre.
MASKABLE_SCALE = 0.8

SUPERSAMPLE = 8  # draw large, then shrink, since Pillow doesn't antialias shapes


def draw_icon(size, maskable):
    big = size * SUPERSAMPLE
    unit = big / GRID
    image = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    if maskable:
        draw.rectangle([0, 0, big, big], fill=ORANGE)
        scale = MASKABLE_SCALE
    else:
        draw.rounded_rectangle([0, 0, big - 1, big - 1], radius=CORNER * unit, fill=ORANGE)
        scale = 1.0

    def at(x, y):
        """Grid point to pixels, shrunk towards the centre for the maskable tile."""
        mid = GRID / 2
        return ((mid + (x - mid) * scale) * unit, (mid + (y - mid) * scale) * unit)

    def disc(centre, radius):
        cx, cy = centre
        draw.ellipse([cx - radius, cy - radius, cx + radius, cy + radius], fill=INK)

    stroke = STROKE * unit * scale
    cx, cy = at(*ARC_CENTRE)
    for r in ARC_RADII:
        r *= unit * scale
        # Pillow strokes inwards from the box edge, so grow the box by half the
        # stroke to centre it on the radius, as SVG does.
        outer = r + stroke / 2
        draw.arc([cx - outer, cy - outer, cx + outer, cy + outer], start=-90, end=0, fill=INK, width=round(stroke))
        disc((cx, cy - r), stroke / 2)  # round caps, as stroke-linecap="round"
        disc((cx + r, cy), stroke / 2)
    disc(at(*DOT), DOT_RADIUS * unit * scale)

    return image.resize((size, size), Image.LANCZOS)


def main():
    for maskable in (False, True):
        for size in (192, 512):
            name = f"app-{'maskable-' if maskable else ''}{size}.png"
            draw_icon(size, maskable).save(OUT / name, optimize=True)
            print(f"wrote static/img/{name}")


if __name__ == "__main__":
    main()
