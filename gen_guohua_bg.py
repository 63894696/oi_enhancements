"""Generate a light guohua (rice-paper + faint ink) background texture for unified theming.

Subtle: warm rice-paper base with very faint wash gradients + a soft ink mountain
silhouette along the bottom edge and a faint gold glow top-right. Kept low-contrast
so text stays readable. Outputs a wide texture + a tall panel texture.
"""
from __future__ import annotations

import math
import os
import random
from PIL import Image, ImageDraw, ImageFilter

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
os.makedirs(OUT, exist_ok=True)
random.seed(20260810)

PAPER = (246, 241, 231)
PAPER_DK = (236, 229, 216)
INK = (108, 124, 114)
GOLD = (214, 178, 108)


def lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def base_paper(w, h):
    img = Image.new("RGB", (w, h), PAPER)
    d = ImageDraw.Draw(img, "RGBA")
    # vertical subtle gradient
    for y in range(h):
        t = y / h
        col = lerp(PAPER, PAPER_DK, t * 0.5)
        d.line([(0, y), (w, y)], fill=col + (255,))
    # paper grain
    for _ in range(int(w * h / 900)):
        x, y = random.randint(0, w - 1), random.randint(0, h - 1)
        v = random.randint(-6, 6)
        c = lerp(PAPER, (255, 255, 255) if v > 0 else (210, 200, 185), abs(v) / 8)
        d.point((x, y), fill=c + (26,))
    return img


def add_mountains(img, strength=26):
    w, h = img.size
    d = ImageDraw.Draw(img, "RGBA")
    # distant ridge
    for layer, (alpha, ybase, amp) in enumerate([(strength, 0.82, 0.05), (int(strength * 0.6), 0.88, 0.04)]):
        pts = []
        for x in range(0, w + 1, 8):
            t = x / w
            y = h * ybase - math.sin(t * math.pi * 2 + layer) * h * amp - math.sin(t * math.pi * 5) * h * amp * 0.3
            pts.append((x, y))
        pts += [(w, h), (0, h)]
        d.polygon(pts, fill=INK + (alpha,))
    return img


def add_gold_glow(img):
    w, h = img.size
    glow = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(glow)
    gx, gy, gr = int(w * 0.80), int(h * 0.12), int(min(w, h) * 0.30)
    for i in range(gr, 0, -6):
        a = int(40 * (i / gr) ** 2)
        d.ellipse([gx - i, gy - i, gx + i, gy + i], fill=GOLD + (a,))
    glow = glow.filter(ImageFilter.GaussianBlur(30))
    img.paste(Image.alpha_composite(img.convert("RGBA"), glow).convert("RGB"), (0, 0))
    return img


def main():
    # wide texture (browser new-tab / web chat backdrop)
    wide = base_paper(1920, 1080)
    wide = add_gold_glow(wide)
    wide = add_mountains(wide, strength=24)
    wide = wide.filter(ImageFilter.GaussianBlur(0.6))
    p1 = os.path.join(OUT, "guohua_bg_wide.png")
    wide.save(p1)
    print("wrote", p1)

    # tall panel texture (side panel / SecureDM)
    tall = base_paper(800, 1280)
    tall = add_gold_glow(tall)
    tall = add_mountains(tall, strength=22)
    tall = tall.filter(ImageFilter.GaussianBlur(0.6))
    p2 = os.path.join(OUT, "guohua_bg_panel.png")
    tall.save(p2)
    print("wrote", p2)


if __name__ == "__main__":
    main()
