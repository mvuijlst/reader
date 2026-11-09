"""Generate the art-deco pattern tiles used behind posts without a picture.

Each tile is a seamless SVG of black line art on transparent. The stylesheet uses
them as CSS masks over a coloured layer, so the colours (and light/dark mode)
live in CSS, not here.

    venv/bin/python tools/make_patterns.py      # writes static/img/patterns/*.svg

Every element that crosses a tile edge is also drawn from the neighbouring
tiles, so the pattern runs on without seams.
"""
from math import sqrt
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "static" / "img" / "patterns"

# The patterns are drawn in the units below and written out SCALE times as
# large, which is the size they show at (the stylesheet uses their natural
# size). Strokes are given in screen pixels: thin, so the large pattern stays
# quiet behind text.
SCALE = 2


def svg(width, height, body, stroke=1.0, defs=""):
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{f(width * SCALE)}" height="{f(height * SCALE)}" '
        f'viewBox="0 0 {f(width)} {f(height)}">'
        f"{f'<defs>{defs}</defs>' if defs else ''}"
        f'<g fill="none" stroke="#000" stroke-width="{px(stroke)}" stroke-linecap="round" stroke-linejoin="round">'
        f"{body}</g></svg>\n"
    )


def f(n):
    return f"{n:.2f}".rstrip("0").rstrip(".")


def px(n):
    """A stroke width that shows as n screen pixels."""
    return f(n / SCALE)


def scales(R=30, rings=5):
    """Seigaiha: overlapping fans of concentric arcs (refs: blue/gold arcs, green scales).

    Rows are R/2 apart and offset by R; each fan shows only the part of its
    circle that the fans in front leave visible. That region is the same for
    every fan, so it is one clip path.
    """
    a, b = 0.8708 * R, 0.4916 * R  # where a fan meets the fans in front of it
    c, d = 0.1292 * R, 0.0084 * R  # where the front fans meet the fan two rows down
    clip = (
        f'<clipPath id="fan"><path d="M{f(-a)},{f(-b)} A{R},{R} 0 0 1 {f(a)},{f(-b)} '
        f"A{R},{R} 0 0 0 {f(c)},{f(d)} A{R},{R} 0 0 0 {f(-c)},{f(d)} "
        f'A{R},{R} 0 0 0 {f(-a)},{f(-b)} Z"/></clipPath>'
    )
    radii = [R * (rings - i) / rings for i in range(rings)]
    fan = "".join(f'<circle r="{f(r)}"/>' for r in radii)
    body = ""
    for k in range(0, 4):  # rows at y = 0, R/2, R, 3R/2
        y = k * R / 2
        for x in range(-R, 4 * R, 2 * R):
            cx = x + (R if k % 2 else 0)
            body += f'<g transform="translate({f(cx)},{f(y)})" clip-path="url(#fan)">{fan}</g>'
    return svg(2 * R, R, body, stroke=1.3, defs=clip)


def fans(S=30, rays=8):
    """Art-deco fans in a diamond lattice (ref: navy with gold fans)."""
    body = ""
    centres = [(0, 0), (2 * S, 0), (S, S), (0, 2 * S), (2 * S, 2 * S), (S, -S), (S, 3 * S), (-S, S), (3 * S, S)]
    for cx, cy in centres:
        top, left, bottom, right = (cx, cy - S), (cx - S, cy), (cx, cy + S), (cx + S, cy)
        body += f'<path d="M{f(top[0])},{f(top[1])} L{f(right[0])},{f(right[1])} L{f(bottom[0])},{f(bottom[1])} L{f(left[0])},{f(left[1])} Z"/>'
        half = rays // 2
        for i in range(1, rays):  # rays from the top corner to the two lower edges
            if i <= half:
                t = i / half
                px, py = left[0] + (bottom[0] - left[0]) * t, left[1] + (bottom[1] - left[1]) * t
            else:
                t = (i - half) / half
                px, py = bottom[0] + (right[0] - bottom[0]) * t, bottom[1] + (right[1] - bottom[1]) * t
            body += f'<path d="M{f(top[0])},{f(top[1])} L{f(px)},{f(py)}"/>'
    return svg(2 * S, 2 * S, body, stroke=1.0)


def _stacked(width, height, columns, top_path, hit):
    """Columns of nested shapes stacked vertically: each shape's lines run down
    until they meet the outer edge of the next shape below (so nothing overlaps).
    `top_path(cx, base_y, r)` draws the top of a shape of half-width r whose
    sides start at base_y; `hit(r)` is how far above the next base its outer
    edge crosses a line at offset r."""
    body = ""
    for cx, phase, offsets in columns:
        R = max(offsets)
        for k in range(-1, 3):
            base = phase + k * height
            for r in offsets:
                body += top_path(cx, base, r)
                end = base + height - hit(R, r)
                for x in (cx - r, cx + r):
                    body += f'<path d="M{f(x)},{f(base)} L{f(x)},{f(end)}"/>'
    return body


def arches(W=44, H=96, offsets=(6, 12, 18)):
    """Nested round arches in staggered columns (ref: teal arches)."""
    def top(cx, base, r):
        return f'<path d="M{f(cx - r)},{f(base)} A{f(r)},{f(r)} 0 0 1 {f(cx + r)},{f(base)}"/>'

    def hit(R, r):
        return sqrt(max(R * R - r * r, 0))

    cols = [(W / 2, 0, offsets), (W * 1.5, H / 2, offsets)]
    return svg(2 * W, H, _stacked(2 * W, H, cols, top, hit), stroke=1.2)


def gables(W=44, H=96, offsets=(5, 11, 17), slope=0.75):
    """Nested gabled arches over vertical lines (ref: green skyscraper chevrons)."""
    def top(cx, base, r):
        return f'<path d="M{f(cx - r)},{f(base)} L{f(cx)},{f(base - r * slope)} L{f(cx + r)},{f(base)}"/>'

    def hit(R, r):
        return (R - r) * slope

    cols = [(W / 2, 0, offsets), (W * 1.5, H / 2, offsets)]
    body = _stacked(2 * W, H, cols, top, hit)
    for x in (0, W, 2 * W):  # the long lines between the columns
        body += f'<path d="M{f(x)},0 L{f(x)},{f(H)}"/>'
    return svg(2 * W, H, body, stroke=1.2)


def ogee(W=60, H=84):
    """Ogee (onion) lattice with a thin echo line inside each cell (ref: green ogee)."""
    q = H / 4
    body = ""
    for dx in (-W, 0, W):
        # one S-curve per column edge and its mirror, meeting halfway across
        body += f'<path d="M{f(dx)},0 C{f(dx)},{f(q * 1.1)} {f(dx + W / 2)},{f(q * 0.9)} {f(dx + W / 2)},{f(2 * q)} C{f(dx + W / 2)},{f(q * 3.1)} {f(dx)},{f(q * 2.9)} {f(dx)},{f(H)}"/>'
        body += f'<path d="M{f(dx + W)},0 C{f(dx + W)},{f(q * 1.1)} {f(dx + W / 2)},{f(q * 0.9)} {f(dx + W / 2)},{f(2 * q)} C{f(dx + W / 2)},{f(q * 3.1)} {f(dx + W)},{f(q * 2.9)} {f(dx + W)},{f(H)}"/>'
    thin = ""
    inset = W * 0.14
    for dx in (-W, 0, W):  # a finer line echoing each cell, as in the reference
        for side in (dx + inset, dx + W - inset):
            mid = dx + W / 2
            lean = inset if side < mid else -inset
            thin += f'<path d="M{f(side)},0 C{f(side)},{f(q * 1.1)} {f(mid + lean * 0.2)},{f(q * 0.9 + inset)} {f(mid + lean * 0.2)},{f(2 * q)}"/>'
    return svg(W, H, body + f'<g stroke-width="{px(0.6)}">{thin}</g>', stroke=1.3)


PATTERNS = {"scales": scales, "fans": fans, "arches": arches, "gables": gables, "ogee": ogee}


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    for name, make in PATTERNS.items():
        path = OUT / f"{name}.svg"
        with open(path, "w", encoding="utf-8", newline="\n") as fh:  # LF, also on Windows
            fh.write(make())
        print(f"wrote {path.relative_to(OUT.parent.parent.parent)}")


if __name__ == "__main__":
    main()
