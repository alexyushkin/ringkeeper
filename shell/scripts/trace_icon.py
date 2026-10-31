"""Traces assets/icon-source.png into assets/icon.svg and assets/icon-small.svg.

    uv run --no-project --with pillow --with numpy --with scipy --with scikit-image \
        python shell/scripts/trace_icon.py

Then shell/scripts/make_icons.py renders every size from them.

The tile is a rounded rect with the source's gradient, the ring a circle arc
(fitted to the source: centre 502,408, radius 274.8, width 87.7), and the
keys smoothed outlines traced from the source's colours: curves become cubic
Béziers, corners (the teeth) stay sharp. The ring's tip lies over the white
key and goes into its hole."""

import math
from pathlib import Path

import numpy as np
from PIL import Image
from scipy.ndimage import binary_fill_holes, gaussian_filter, label
from skimage.measure import approximate_polygon, find_contours

ASSETS = Path(__file__).resolve().parents[2] / "assets"
SRC, OUT, SMALL_OUT = ASSETS / "icon-source.png", ASSETS / "icon.svg", ASSETS / "icon-small.svg"
CORNER = math.radians(38)  # a turn sharper than this is a corner

a = np.asarray(Image.open(SRC).convert("RGB")).astype(int)
R, G, B = a[..., 0], a[..., 1], a[..., 2]
white = (R > 150) & (G > 150) & (B > 160)
cyan = (R < 80) & (G > 185) & (B > 190)
purple = (R > 120) & (B > 200) & (G < 130)
shade = (R > 70) & (R <= 120) & (B > 190) & (G < 100)
# The white key's hole: the dark region around (431, 612).
dark = (R < 20) & (G < 40) & (B < 80)
labels, _ = label(dark)
hole = labels == labels[612, 431]
# The ring's tip, where it lies over the white key (it bends into the hole).
ring = (R < 40) & (B > 200) & (G > 85) & (G < 190)
yy, xx = np.mgrid[0:1024, 0:1024]
angle = np.degrees(np.arctan2(-(yy - 408), xx - 502)) % 360
tip = ring & (angle > 214) & (angle < 265) & (np.hypot(xx - 502, yy - 408) < 340)


def smooth(poly: np.ndarray) -> str:
    """A closed polygon (x, y) as a path: Catmull-Rom curves between corners."""
    n = len(poly)
    pts = [tuple(p) for p in poly]

    def turn(i: int) -> float:
        p0, p1, p2 = np.array(pts[i - 1]), np.array(pts[i]), np.array(pts[(i + 1) % n])
        v1, v2 = p1 - p0, p2 - p1
        c = np.dot(v1, v2) / (np.linalg.norm(v1) * np.linalg.norm(v2) + 1e-9)
        return math.acos(max(-1.0, min(1.0, c)))

    corner = [turn(i) > CORNER for i in range(n)]
    out = [f"M{pts[0][0]:.1f} {pts[0][1]:.1f}"]
    for i in range(n):
        p0, p1 = np.array(pts[i - 1]), np.array(pts[i])
        p2, p3 = np.array(pts[(i + 1) % n]), np.array(pts[(i + 2) % n])
        if corner[i] and corner[(i + 1) % n]:
            out.append(f"L{p2[0]:.1f} {p2[1]:.1f}")
            continue
        t1 = (p2 - p0) / 6 if not corner[i] else (p2 - p1) / 3 * 0.0
        t2 = (p3 - p1) / 6 if not corner[(i + 1) % n] else (p2 - p1) / 3 * 0.0
        c1, c2 = p1 + t1, p2 - t2
        out.append(f"C{c1[0]:.1f} {c1[1]:.1f} {c2[0]:.1f} {c2[1]:.1f} {p2[0]:.1f} {p2[1]:.1f}")
    return "".join(out) + "Z"


def outline(mask: np.ndarray, *, tol: float = 1.4, min_area: float = 80.0) -> str:
    soft = gaussian_filter(mask.astype(float), 1.8)
    parts = []
    for contour in find_contours(soft, 0.5):
        poly = approximate_polygon(contour, tolerance=tol)[:-1][:, ::-1]  # (x, y)
        if len(poly) < 4:
            continue
        xs, ys = poly[:, 0], poly[:, 1]
        if 0.5 * abs(np.dot(xs, np.roll(ys, 1)) - np.dot(ys, np.roll(xs, 1))) < min_area:
            continue
        parts.append(smooth(poly))
    return "".join(parts)


def arc(start: float, end: float) -> str:
    cx, cy, r = 502.0, 408.0, 274.8
    x1, y1 = cx + r * math.cos(math.radians(start)), cy - r * math.sin(math.radians(start))
    x2, y2 = cx + r * math.cos(math.radians(end)), cy - r * math.sin(math.radians(end))
    large = 1 if (end - start) % 360 > 180 else 0
    return f"M{x1:.1f} {y1:.1f}A{r} {r} 0 {large} 0 {x2:.1f} {y2:.1f}"


white_key = outline(binary_fill_holes(white | hole | (tip & (np.hypot(xx - 431, yy - 612) < 120))))
tip_path = outline(tip, tol=1.0)
svg = f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1024 1024">
  <defs>
    <linearGradient id="tile" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#022359"/>
      <stop offset=".5" stop-color="#011A47"/>
      <stop offset="1" stop-color="#01112E"/>
    </linearGradient>
    <linearGradient id="rim" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#0050E0"/>
      <stop offset=".6" stop-color="#0050E0" stop-opacity="0"/>
    </linearGradient>
    <linearGradient id="ring" gradientUnits="userSpaceOnUse" x1="226" y1="300" x2="774" y2="620">
      <stop offset="0" stop-color="#019DFC"/>
      <stop offset="1" stop-color="#0472FB"/>
    </linearGradient>
  </defs>
  <rect width="1024" height="1024" rx="198" fill="url(#tile)"/>
  <rect x="2" y="2" width="1020" height="1020" rx="196" fill="none" stroke="url(#rim)"
    stroke-width="4"/>
  <clipPath id="white-key"><path d="{white_key}"/></clipPath>
  <path d="{arc(-34, 232)}" fill="none" stroke="url(#ring)" stroke-width="87.7"/>
  <g stroke="#01143A" stroke-width="14" paint-order="stroke" stroke-linejoin="round">
    <path fill="#A55AFF" d="{outline(purple | shade)}"/>
    <path fill="#6534EF" stroke="none" d="{outline(shade, tol=1.2)}"/>
    <path fill="#01E9FD" d="{outline(cyan)}"/>
    <path fill="#FDFDFC" d="{white_key}"/>
    <path clip-path="url(#white-key)" fill="url(#ring)" d="{tip_path}"/>
  </g>
  <path fill="url(#ring)" d="{tip_path}"/>
  <path fill="#000F2D" d="{outline(hole, tol=1.0)}"/>
</svg>
"""
OUT.write_text(svg, encoding="utf-8", newline="\n")
print(OUT, len(svg), "bytes")

# 16 and 24 px: the keys thickened (each outlined in its own colour) so they
# don't thin out and run together; no rim, no shade, a slightly wider ring.
purple_path, cyan_path = outline(purple | shade), outline(cyan)
small = f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1024 1024">
  <defs>
    <linearGradient id="tile" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#022359"/>
      <stop offset=".5" stop-color="#011A47"/>
      <stop offset="1" stop-color="#01112E"/>
    </linearGradient>
    <linearGradient id="ring" gradientUnits="userSpaceOnUse" x1="226" y1="300" x2="774" y2="620">
      <stop offset="0" stop-color="#019DFC"/>
      <stop offset="1" stop-color="#0472FB"/>
    </linearGradient>
  </defs>
  <rect width="1024" height="1024" rx="198" fill="url(#tile)"/>
  <path d="{arc(-34, 232)}" fill="none" stroke="url(#ring)" stroke-width="104"/>
  <g stroke-linejoin="round">
    <path fill="#A55AFF" stroke="#A55AFF" stroke-width="44" d="{purple_path}"/>
    <path fill="#01143A" stroke="#01143A" stroke-width="124" d="{cyan_path}"/>
    <path fill="#01E9FD" stroke="#01E9FD" stroke-width="44" d="{cyan_path}"/>
    <path fill="#01143A" stroke="#01143A" stroke-width="124" d="{white_key}"/>
    <path fill="#FDFDFC" stroke="#FDFDFC" stroke-width="44" d="{white_key}"/>
  </g>
  <path fill="url(#ring)" d="{tip_path}"/>
  <path fill="#000F2D" d="{outline(hole, tol=1.0)}"/>
</svg>
"""
SMALL_OUT.write_text(small, encoding="utf-8", newline=chr(10))
