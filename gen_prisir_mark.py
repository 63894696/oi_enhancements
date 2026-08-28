"""Generate Prisir browser mother-mark icons (Plan B, dark ink / copper / teal).

Design per docs/visual-identity-proposal.md §3.2:
  - circular dark-ink disc (#0f1a24)
  - a copper compass / divider (two legs spread), legs resting on a teal arc (sea-lane)
  - a copper north-star dot at the compass apex
  - 16/32px simplified: only the copper spread-angle + star (drop the arc) for legibility

Pure-PIL procedural generation (same pattern as gen_guohua_icon.py): crisp, controllable,
exactly reproduces the brand tokens, no external image dependency.

Outputs to assets/: prisir-mark-{16,32,48,128,256}.png + prisir-mark.ico (multi-size).
"""
from __future__ import annotations

import math
import os
from PIL import Image, ImageDraw, ImageFilter

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
os.makedirs(OUT, exist_ok=True)

# Brand tokens (visual-identity-proposal.md §3.1, locked)
INK = (15, 26, 36)          # --prisir-ink      #0f1a24  deep ink-blue-black base
INK_2 = (27, 43, 58)        # --prisir-ink-2    #1b2b3a  secondary base / card
COPPER = (201, 138, 75)     # --prisir-copper   #c98a4b  primary accent
COPPER_2 = (224, 168, 102)  # --prisir-copper-2 #e0a866  bright copper (highlight)
TEAL = (47, 143, 131)       # --prisir-teal     #2f8f83  secondary accent
TEAL_2 = (79, 179, 164)     # --prisir-teal-2   #4fb3a4  bright teal
PAPER = (242, 237, 226)     # --prisir-paper    #f2ede2  light foreground


def lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def _base_disc(S):
    """Dark-ink disc with a subtle vertical sheen (lighter toward top)."""
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    disc = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    dd = ImageDraw.Draw(disc)
    cx = cy = S / 2
    R = S * 0.47
    steps = 48
    for i in range(steps, 0, -1):
        r = R * i / steps
        # radial: slightly lighter toward center-top, deeper at rim
        col = lerp(INK, INK_2, (i / steps) * 0.55)
        dd.ellipse([cx - r, cy - r, cx + r, cy + r], fill=col + (255,))
    disc = disc.filter(ImageFilter.GaussianBlur(S * 0.003))
    img.alpha_composite(disc)
    return img, cx, cy, R


def _draw_star(d, x, y, r, col):
    """A small 4-point north-star (diamond sparkle) at the compass apex."""
    # vertical + horizontal points, tapered
    d.polygon([(x, y - r), (x + r * 0.28, y - r * 0.28), (x + r, y),
               (x + r * 0.28, y + r * 0.28), (x, y + r),
               (x - r * 0.28, y + r * 0.28), (x - r, y),
               (x - r * 0.28, y - r * 0.28)], fill=col + (255,))
    # bright core
    cr = r * 0.24
    d.ellipse([x - cr, y - cr, x + cr, y + cr], fill=COPPER_2 + (255,))


def _draw_compass(d, cx, cy, R, S, simplified=False):
    """Copper divider/compass: apex at top, two legs spreading down onto a teal arc."""
    apex_x, apex_y = cx, cy - R * 0.52
    spread = math.radians(26)          # half-angle of the compass opening
    leg_len = R * 1.02
    leg_w = S * 0.052

    # apex hinge (copper ring) + north-star above it
    hinge_r = S * 0.055
    d.ellipse([apex_x - hinge_r, apex_y - hinge_r, apex_x + hinge_r, apex_y + hinge_r],
              outline=COPPER_2 + (255,), width=max(2, int(S * 0.016)))
    _draw_star(d, apex_x, apex_y - hinge_r - S * 0.055, S * 0.05, COPPER)

    feet = []
    for sign in (-1, +1):
        ang = math.pi / 2 + sign * spread   # measured from +x axis, pointing down
        foot_x = apex_x + leg_len * math.cos(ang) * -1 if False else apex_x + sign * math.sin(spread) * leg_len
        foot_y = apex_y + math.cos(spread) * leg_len
        feet.append((foot_x, foot_y))
        # tapered leg: draw as a thick line with a slight gradient copper->copper2
        d.line([apex_x, apex_y, foot_x, foot_y], fill=COPPER + (255,), width=int(leg_w))
        # needle tip
        tip_r = S * 0.018
        d.polygon([(foot_x - leg_w * 0.5, foot_y - tip_r), (foot_x + leg_w * 0.5, foot_y - tip_r),
                   (foot_x, foot_y + tip_r * 1.6)], fill=COPPER_2 + (255,))

    # teal sea-lane arc under the feet (omitted in simplified small-size version)
    if not simplified:
        arc_r = R * 0.86
        bbox = [cx - arc_r, cy - arc_r, cx + arc_r, cy + arc_r]
        # a gentle lower arc (the "航道" the compass measures), drawn as a tapered teal sweep
        start, end = 35, 145
        segs = 56
        for i in range(segs):
            t = i / (segs - 1)
            a = math.radians(start + (end - start) * t)
            w = S * 0.02 * math.sin(math.pi * t) ** 0.6 + S * 0.006
            ax = cx + arc_r * math.cos(a)
            ay = cy + arc_r * math.sin(a) * 0.92 + R * 0.06
            col = lerp(TEAL, TEAL_2, math.sin(math.pi * t))
            d.ellipse([ax - w / 2, ay - w / 2, ax + w / 2, ay + w / 2], fill=col + (230,))


def make_icon(size: int) -> Image.Image:
    S = size * 4  # supersample
    img, cx, cy, R = _base_disc(S)
    d = ImageDraw.Draw(img)
    simplified = size <= 32
    _draw_compass(d, cx, cy, R, S, simplified=simplified)
    return img.resize((size, size), Image.LANCZOS)


def main():
    for s in (16, 32, 48, 128, 256):
        im = make_icon(s)
        p = os.path.join(OUT, f"prisir-mark-{s}.png")
        im.save(p)
        print("wrote", p)
    im256 = make_icon(256)
    ico = os.path.join(OUT, "prisir-mark.ico")
    im256.save(ico, format="ICO", sizes=[(16, 16), (32, 32), (48, 48), (128, 128), (256, 256)])
    print("wrote", ico)


if __name__ == "__main__":
    main()
