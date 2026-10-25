"""Draws the shell's icons (the web app's ring) at every size it needs.

    uv run --with pillow python shell/scripts/make_icons.py

Writes shell/icons/: the app icon (ico, icns, pngs), the tray icon, the tray
icon with the "waiting for you" dot, and the taskbar overlay dot.
"""

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "shell" / "icons"
BLUE = Image.open(ROOT / "web" / "public" / "icon-512.png").convert("RGB").getpixel((8, 8))
WHITE = (255, 255, 255, 255)
DOT = (232, 72, 60, 255)  # the badge: red, readable on light and dark taskbars
SCALE = 4  # draw big, shrink: smooth edges


def ring(size: int, *, rounded: bool, badge: bool = False) -> Image.Image:
    big = size * SCALE
    image = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    if rounded:
        draw.rounded_rectangle((0, 0, big - 1, big - 1), radius=big * 0.22, fill=(*BLUE, 255))
    else:
        draw.rectangle((0, 0, big - 1, big - 1), fill=(*BLUE, 255))
    outer, inner = big * 0.355, big * 0.245
    centre = big / 2
    draw.ellipse((centre - outer, centre - outer, centre + outer, centre + outer), fill=WHITE)
    draw.ellipse(
        (centre - inner, centre - inner, centre + inner, centre + inner), fill=(*BLUE, 255)
    )
    if badge:
        radius = big * 0.25
        x, y = big - radius - big * 0.02, radius + big * 0.02
        draw.ellipse(
            (
                x - radius - big * 0.05,
                y - radius - big * 0.05,
                x + radius + big * 0.05,
                y + radius + big * 0.05,
            ),
            fill=WHITE,
        )
        draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=DOT)
    return image.resize((size, size), Image.Resampling.LANCZOS)


def dot(size: int) -> Image.Image:
    big = size * SCALE
    image = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.ellipse((0, 0, big - 1, big - 1), fill=WHITE)
    pad = big * 0.12
    draw.ellipse((pad, pad, big - 1 - pad, big - 1 - pad), fill=DOT)
    return image.resize((size, size), Image.Resampling.LANCZOS)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for size, name in (
        (32, "32x32.png"),
        (128, "128x128.png"),
        (256, "128x128@2x.png"),
        (512, "icon.png"),
    ):
        ring(size, rounded=True).save(OUT / name)
    ring(256, rounded=True).save(
        OUT / "icon.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (256, 256)]
    )
    ring(1024, rounded=True).save(OUT / "icon.icns")
    ring(32, rounded=True).save(OUT / "tray.png")
    ring(32, rounded=True, badge=True).save(OUT / "tray-waiting.png")
    dot(16).save(OUT / "overlay-waiting.png")


if __name__ == "__main__":
    main()
