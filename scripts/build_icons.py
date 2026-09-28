"""Draw the home-screen icons the web app manifest points at.

    python scripts/build_icons.py

Writes docs/icons/: a rounded "any" icon at 192 and 512, a full-bleed maskable
512 (Android crops it to its own shape, so the plane sits inside the central
safe zone), and a 180 apple-touch-icon (iOS rounds the corners itself).
Drawn at 1024 and downsampled, so the edges stay smooth at every size.
"""
import math
from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parent.parent / "docs" / "icons"
BLUE = (42, 120, 214)  # --series-1
WHITE = (255, 255, 255)
BIG = 1024

# Top-down airliner pointing up, centred on the origin, spanning about ±0.9.
HALF = [
    (0.00, -0.94), (0.04, -0.92), (0.07, -0.87), (0.09, -0.78), (0.09, -0.20),
    (0.88, 0.22), (0.88, 0.36), (0.09, 0.14),
    (0.08, 0.60), (0.34, 0.78), (0.34, 0.90), (0.07, 0.86), (0.03, 0.93), (0.00, 0.94),
]
PLANE = HALF + [(-x, y) for x, y in reversed(HALF[1:-1])]


def plane(scale):
    """The outline, turned 45° clockwise to head up-right, in canvas pixels."""
    turn = math.radians(45)
    c, s = math.cos(turn), math.sin(turn)
    r = scale * BIG / 2
    return [(BIG / 2 + r * (x * c - y * s), BIG / 2 + r * (x * s + y * c)) for x, y in PLANE]


def icon(rounded, scale):
    img = Image.new("RGBA", (BIG, BIG), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    if rounded:
        draw.rounded_rectangle((0, 0, BIG - 1, BIG - 1), radius=int(BIG * 0.22), fill=BLUE)
    else:
        draw.rectangle((0, 0, BIG, BIG), fill=BLUE)
    draw.polygon(plane(scale), fill=WHITE)
    return img


def save(img, size, name, opaque=False):
    out = img.resize((size, size), Image.LANCZOS)
    if opaque:
        out = out.convert("RGB")
    out.save(OUT / name, optimize=True)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    rounded = icon(True, 0.62)
    save(rounded, 192, "icon-192.png")
    save(rounded, 512, "icon-512.png")
    # Maskable safe zone is the central 80% circle; the rotated plane's
    # reach is ~0.9 of its scale, so 0.5 keeps it well inside.
    save(icon(False, 0.5), 512, "icon-maskable-512.png", opaque=True)
    save(icon(False, 0.58), 180, "apple-touch-icon.png", opaque=True)
    save(rounded, 32, "favicon-32.png")
    print(f"wrote icons to {OUT}")


if __name__ == "__main__":
    main()
