"""Render favicon.ico + favicon-512.png for Notifier (bell + badge mark).

Mirrors assets/favicon.svg using PIL primitives so no SVG rasterizer is needed.
Run: python assets/make_favicon.py
"""
from pathlib import Path

from PIL import Image, ImageDraw

S = 512  # master canvas
HERE = Path(__file__).parent


def lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def make_master():
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # Background: rounded square with vertical navy gradient
    top, bottom = (27, 30, 52), (14, 16, 32)
    grad = Image.new("RGBA", (S, S))
    gd = ImageDraw.Draw(grad)
    for y in range(S):
        gd.line([(0, y), (S, y)], fill=lerp(top, bottom, y / S) + (255,))
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, S - 1, S - 1], radius=112, fill=255)
    img.paste(grad, (0, 0), mask)
    d = ImageDraw.Draw(img)

    # Bell (amber), scaled from the 64-unit SVG coordinates (x8)
    amber_top, amber_bot = (255, 209, 102), (244, 162, 97)

    bell = Image.new("L", (S, S), 0)
    bd = ImageDraw.Draw(bell)
    # Dome: circle centered (256, 234) r=112
    bd.ellipse([144, 122, 368, 346], fill=255)
    # Flared skirt below the dome
    bd.polygon([(144, 234), (368, 234), (392, 330), (404, 348),
                (108, 348), (120, 330)], fill=255)
    # Bottom lip
    bd.rounded_rectangle([100, 332, 412, 376], radius=22, fill=255)
    # Top stem
    bd.rounded_rectangle([230, 84, 282, 150], radius=26, fill=255)
    # Clapper
    bd.pieslice([212, 350, 300, 438], 0, 180, fill=255)

    bell_grad = Image.new("RGBA", (S, S))
    bgd = ImageDraw.Draw(bell_grad)
    for y in range(S):
        bgd.line([(0, y), (S, y)], fill=lerp(amber_top, amber_bot, y / S) + (255,))
    img.paste(bell_grad, (0, 0), bell)
    d = ImageDraw.Draw(img)

    # Badge: red dot, dark ring, upper-right (SVG: cx46 cy18 r8.5 -> x8)
    cx, cy, r = 368, 144, 68
    d.ellipse([cx - r - 12, cy - r - 12, cx + r + 12, cy + r + 12], fill=(14, 16, 32, 255))
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(239, 71, 111, 255))

    return img


def main():
    master = make_master()
    master.save(HERE / "favicon-512.png")
    sizes = [16, 24, 32, 48, 64, 128, 256]
    master.resize((256, 256), Image.LANCZOS).save(
        HERE / "favicon.ico",
        sizes=[(s, s) for s in sizes],
    )
    print(f"Wrote favicon-512.png and favicon.ico ({sizes}) to {HERE}")


if __name__ == "__main__":
    main()
