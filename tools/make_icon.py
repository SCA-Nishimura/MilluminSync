"""Generate the app icon (PNG / ICO / ICNS) into milluminsync/assets/.

    python -m pip install pillow
    python tools/make_icon.py

Design: two column tiles (Master in blue, Slave in green) with a sync arrow
between them, on a dark rounded square that follows the macOS icon grid.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

S = 1024          # final size
SS = 4            # supersampling factor for smooth edges
OUT = Path(__file__).resolve().parent.parent / "milluminsync" / "assets"

BG_TOP = (36, 46, 78)
BG_BOTTOM = (14, 19, 36)
BLUE = (78, 161, 255)
GREEN = (47, 191, 113)
WHITE = (255, 255, 255)


def lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def rounded_mask(size, box, radius):
    m = Image.new("L", size, 0)
    ImageDraw.Draw(m).rounded_rectangle(box, radius=radius, fill=255)
    return m


def vertical_gradient(size, top, bottom):
    w, h = size
    g = Image.new("RGB", (1, h))
    for y in range(h):
        g.putpixel((0, y), lerp(top, bottom, y / (h - 1)))
    return g.resize((w, h))


def tile(canvas, box, color, k):
    """A Millumin-like column tile with a play triangle."""
    x0, y0, x1, y1 = box
    r = int(70 * k)
    # soft shadow
    sh = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    ImageDraw.Draw(sh).rounded_rectangle((x0, y0 + 24 * k, x1, y1 + 24 * k), radius=r, fill=(0, 0, 0, 120))
    sh = sh.filter(ImageFilter.GaussianBlur(28 * k))
    canvas.alpha_composite(sh)
    # body with a light top-to-bottom shade
    body = vertical_gradient((x1 - x0, y1 - y0), lerp(color, WHITE, 0.18), lerp(color, (0, 0, 0), 0.12))
    canvas.paste(body, (x0, y0), rounded_mask(canvas.size, box, r).crop((x0, y0, x1, y1)))
    # play triangle, shifted right so it looks optically centred
    cx, cy = (x0 + x1) / 2 + 10 * k, (y0 + y1) / 2
    t = 58 * k
    ImageDraw.Draw(canvas).polygon(
        [(cx - t * 0.85, cy - t), (cx - t * 0.85, cy + t), (cx + t, cy)], fill=WHITE)


def make(size_px: int = S) -> Image.Image:
    k = SS
    W = size_px * k
    s = W / S  # scale from 1024 design units
    img = Image.new("RGBA", (W, W), (0, 0, 0, 0))

    # macOS grid: 824 x 824 rounded square centred in 1024
    m = int(100 * s)
    bg_box = (m, m, W - m, W - m)
    bg = vertical_gradient((W, W), BG_TOP, BG_BOTTOM).convert("RGBA")
    img.paste(bg, (0, 0), rounded_mask((W, W), bg_box, int(185 * s)))

    # two tiles
    tw, th = int(215 * s), int(360 * s)
    top = int(282 * s)
    left = (int(195 * s), top, int(195 * s) + tw, top + th)
    right = (W - int(195 * s) - tw, top, W - int(195 * s), top + th)
    tile(img, left, BLUE, s)
    tile(img, right, GREEN, s)

    # arrow between the tiles: Master -> Slave
    d = ImageDraw.Draw(img)
    cy = top + th / 2
    ax0, ax1 = left[2] + 36 * s, right[0] - 36 * s
    shaft = 28 * s
    head = 48 * s
    hx = ax1 - head * 1.1
    d.rounded_rectangle((ax0, cy - shaft / 2, hx + 26 * s, cy + shaft / 2), radius=shaft / 2, fill=WHITE)
    d.polygon([(hx, cy - head), (hx, cy + head), (ax1, cy)], fill=WHITE)

    # Millumin-like timeline under the tiles: track + progress + playhead
    over = Image.new("RGBA", img.size, (0, 0, 0, 0))
    o = ImageDraw.Draw(over)
    by = top + th + 80 * s
    bh = 20 * s
    x_end = left[0] + (right[2] - left[0]) * 0.58
    o.rounded_rectangle((left[0], by, right[2], by + bh), radius=bh / 2, fill=(255, 255, 255, 55))
    o.rounded_rectangle((left[0], by, x_end, by + bh), radius=bh / 2, fill=(255, 255, 255, 200))
    o.ellipse((x_end - 22 * s, by + bh / 2 - 22 * s, x_end + 22 * s, by + bh / 2 + 22 * s), fill=WHITE)
    img.alpha_composite(over)

    return img.resize((size_px, size_px), Image.LANCZOS)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    big = make(S)
    big.save(OUT / "icon.png")
    make(256).save(OUT / "icon-256.png")
    # Small sizes rendered separately stay crisper than a plain downscale.
    sizes = [16, 24, 32, 48, 64, 128, 256]
    make(256).save(OUT / "icon.ico", sizes=[(n, n) for n in sizes])
    big.save(OUT / "icon.icns")
    print("wrote", ", ".join(p.name for p in sorted(OUT.iterdir())))


if __name__ == "__main__":
    main()
