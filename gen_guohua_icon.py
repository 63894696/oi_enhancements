"""Generate guohua-style browser icon for SecBrowser (light ink-painting, distinct from Chrome color wheel).

Design: circular rice-paper disc (#f5f0e6), ink-wash brushstroke arc (grey-green),
a warm-gold sun dot, and a small ochre seal mark. Sizes: 16/32/48/128/256.
"""
from __future__ import annotations

import math
import os
from PIL import Image, ImageDraw, ImageFilter

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
os.makedirs(OUT, exist_ok=True)

# Palette sampled from 采薇/蒹葭 references
PAPER = (245, 240, 230)      # 宣纸底
PAPER_EDGE = (228, 220, 205) # 宣纸暗边
INK = (74, 92, 82)           # 水墨灰绿(主笔)
INK_SOFT = (120, 138, 126)   # 淡墨
GOLD = (212, 175, 105)       # 暖金(日)
SEAL = (178, 58, 48)         # 印章赭红


def lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def make_icon(size: int) -> Image.Image:
    S = size * 4  # supersample
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    cx = cy = S / 2
    R = S * 0.46

    # rice-paper disc with subtle radial shading
    disc = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    dd = ImageDraw.Draw(disc)
    steps = 40
    for i in range(steps, 0, -1):
        r = R * i / steps
        col = lerp(PAPER_EDGE, PAPER, i / steps)
        dd.ellipse([cx - r, cy - r, cx + r, cy + r], fill=col + (255,))
    disc = disc.filter(ImageFilter.GaussianBlur(S * 0.004))
    img.alpha_composite(disc)

    d = ImageDraw.Draw(img)

    # ink-wash brushstroke: a sweeping arc (like a reed/mountain stroke), tapered
    arc_box = [cx - R * 0.72, cy - R * 0.55, cx + R * 0.72, cy + R * 0.75]
    start, end = 200, 340
    segments = 60
    for i in range(segments):
        t = i / (segments - 1)
        ang = math.radians(start + (end - start) * t)
        # taper width: thick in middle, thin at ends
        w = S * 0.10 * math.sin(math.pi * t) ** 0.7 + S * 0.012
        ax = cx + (R * 0.72) * math.cos(ang)
        ay = cy + (R * 0.65) * math.sin(ang) + R * 0.10
        col = lerp(INK_SOFT, INK, math.sin(math.pi * t))
        d.ellipse([ax - w / 2, ay - w / 2, ax + w / 2, ay + w / 2], fill=col + (235,))

    # a second lighter reed stroke (vertical suggestion)
    for i in range(30):
        t = i / 29
        rx = cx - R * 0.30 + S * 0.01 * math.sin(t * 6)
        ry = cy - R * 0.45 + t * R * 0.9
        w = S * 0.02 * (1 - abs(t - 0.5) * 1.4)
        if w <= 0:
            continue
        d.ellipse([rx - w / 2, ry - w / 2, rx + w / 2, ry + w / 2], fill=INK_SOFT + (140,))

    # warm-gold sun (circle) upper-right
    sun_r = S * 0.10
    sun_x, sun_y = cx + R * 0.32, cy - R * 0.30
    sun = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    sd = ImageDraw.Draw(sun)
    sd.ellipse([sun_x - sun_r, sun_y - sun_r, sun_x + sun_r, sun_y + sun_r], fill=GOLD + (255,))
    sun = sun.filter(ImageFilter.GaussianBlur(S * 0.004))
    img.alpha_composite(sun)

    # ochre seal mark (small square) lower-right, like 印章
    seal_s = S * 0.12
    seal_x, seal_y = cx + R * 0.42, cy + R * 0.40
    d = ImageDraw.Draw(img)
    d.rounded_rectangle(
        [seal_x - seal_s / 2, seal_y - seal_s / 2, seal_x + seal_s / 2, seal_y + seal_s / 2],
        radius=seal_s * 0.18, fill=SEAL + (235,))
    # seal inner mark (white stroke cross)
    m = seal_s * 0.18
    d.line([seal_x - m, seal_y, seal_x + m, seal_y], fill=PAPER + (255,), width=max(2, int(S * 0.012)))
    d.line([seal_x, seal_y - m, seal_x, seal_y + m], fill=PAPER + (255,), width=max(2, int(S * 0.012)))

    return img.resize((size, size), Image.LANCZOS)


def main():
    for s in (16, 32, 48, 128, 256):
        im = make_icon(s)
        p = os.path.join(OUT, f"secbrowser_icon_{s}.png")
        im.save(p)
        print("wrote", p)
    # multi-size ICO for Windows
    im256 = make_icon(256)
    ico = os.path.join(OUT, "secbrowser.ico")
    im256.save(ico, format="ICO", sizes=[(16, 16), (32, 32), (48, 48), (128, 128), (256, 256)])
    print("wrote", ico)


if __name__ == "__main__":
    main()
