"""Renders every icon from the SVGs in assets/: the app icon (ico, icns, pngs),
the tray icon and its "waiting for you" variant, the taskbar overlay dot, the
installer's images, and the web app's favicons, PWA and apple-touch icons.

    uv run --no-project --with pillow --with resvg-py python shell/scripts/make_icons.py

assets/icon.svg is the icon (traced from assets/icon-source.png by
shell/scripts/trace_icon.py); assets/icon-small.svg is the same with thicker
keys, for 16 and 24 px, where the thin ones run together.
"""

import io
import re
from pathlib import Path

import resvg_py  # pyright: ignore[reportMissingImports]
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / "assets"
ICONS = ROOT / "shell" / "icons"
PUBLIC = ROOT / "web" / "public"
APP = ROOT / "web" / "app"
ICON = (ASSETS / "icon.svg").read_text(encoding="utf-8")
SMALL = (ASSETS / "icon-small.svg").read_text(encoding="utf-8")
NAVY = (1, 26, 71)  # the tile's middle
WHITE = (255, 255, 255, 255)
DOT = (232, 72, 60, 255)  # the badge: red, readable on light and dark taskbars
SMALL_UP_TO = 24


def render(size: int, svg: str | None = None) -> Image.Image:
    svg = svg or (SMALL if size <= SMALL_UP_TO else ICON)
    png = resvg_py.svg_to_bytes(svg_string=svg, width=size, height=size)
    return Image.open(io.BytesIO(bytes(png))).convert("RGBA")


def square(svg: str, *, art: float = 1.0) -> str:
    """Full bleed (no rounded corners, no rim), the art scaled to `art` of the
    tile: for apple-touch (iOS rounds it) and the maskable PWA icon (safe zone)."""
    svg = re.sub(r'<rect x="2"[^>]*/>', "", svg).replace('rx="198"', 'rx="0"')
    if art == 1.0:
        return svg
    pad = 512 * (1 - art)
    head, rest = svg.split('<rect width="1024"', 1)
    tile, body = rest.split("/>", 1)
    body = body.replace("</svg>", "</g></svg>")
    group = f'<g transform="translate({pad} {pad}) scale({art})">'
    return f'{head}<rect width="1024"{tile}/>{group}{body}'


def badge(image: Image.Image) -> Image.Image:
    """The tray icon with the red "waiting for you" dot, top right."""
    size = image.width
    out = image.copy()
    draw = ImageDraw.Draw(out)
    radius = size * 0.25
    x, y = size - radius - size * 0.02, radius + size * 0.02
    ring = size * 0.05
    draw.ellipse(
        (x - radius - ring, y - radius - ring, x + radius + ring, y + radius + ring), WHITE
    )
    draw.ellipse((x - radius, y - radius, x + radius, y + radius), DOT)
    return out


def dot(size: int) -> Image.Image:
    big = size * 4
    image = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.ellipse((0, 0, big - 1, big - 1), fill=WHITE)
    pad = big * 0.12
    draw.ellipse((pad, pad, big - 1 - pad, big - 1 - pad), fill=DOT)
    return image.resize((size, size), Image.Resampling.LANCZOS)


def bmp(image: Image.Image, path: Path) -> None:
    """NSIS wants 24-bit BMPs."""
    image.convert("RGB").save(path, format="BMP")


def installer_images() -> None:
    # The welcome/finish pages' sidebar (164x314): the icon on the tile's navy.
    sidebar = Image.new("RGBA", (164, 314), (*NAVY, 255))
    sidebar.alpha_composite(render(120), (22, 70))
    bmp(sidebar, ICONS / "installer-sidebar.bmp")
    # The other pages' header (150x57): the icon on white, at the right.
    header = Image.new("RGBA", (150, 57), WHITE)
    header.alpha_composite(render(48), (150 - 48 - 6, 4))
    bmp(header, ICONS / "installer-header.bmp")


def main() -> None:
    ICONS.mkdir(parents=True, exist_ok=True)
    for size, name in ((32, "32x32.png"), (128, "128x128.png"), (256, "128x128@2x.png")):
        render(size).save(ICONS / name)
    render(512).save(ICONS / "icon.png")
    sizes = (16, 24, 32, 48, 64, 256)
    frames = [render(s) for s in sizes]
    frames[-1].save(ICONS / "icon.ico", format="ICO", append_images=frames[:-1],
                    sizes=[(s, s) for s in sizes])  # fmt: skip
    icns = [render(s) for s in (16, 32, 64, 128, 256, 512)]
    render(1024).save(ICONS / "icon.icns", format="ICNS", append_images=icns)
    # The tray shows 16 px at 100% scaling and 32 at 200%: the thick keys read at both.
    tray = render(32, SMALL)
    tray.save(ICONS / "tray.png")
    badge(tray).save(ICONS / "tray-waiting.png")
    dot(16).save(ICONS / "overlay-waiting.png")
    installer_images()

    (PUBLIC / "icon.svg").write_text(ICON, encoding="utf-8", newline="\n")
    render(16).save(PUBLIC / "icon-16.png")
    render(32).save(PUBLIC / "icon-32.png")
    render(192).save(PUBLIC / "icon-192.png")
    render(512).save(PUBLIC / "icon-512.png")
    render(512, square(ICON, art=0.8)).save(PUBLIC / "icon-maskable-512.png")
    render(180, square(ICON)).convert("RGB").save(APP / "apple-icon.png")


if __name__ == "__main__":
    main()
