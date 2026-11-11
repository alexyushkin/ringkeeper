"""Pictures out of files: a PDF's page drawn as an image, the pictures on it, and a picture
made small enough to show.

PDFium (pypdfium2) draws pages and gets the pictures out of them, and a PNG is written here
with the standard library, so none of this needs Pillow. Pillow, when it's there (it comes
with the scan reader), makes a large photo smaller and turns it into a JPEG. PDFium isn't safe
to use from two threads at once, so every use holds one lock.
"""

import contextlib
import importlib
import io
import logging
import struct
import threading
import zlib
from collections.abc import Callable, Generator
from dataclasses import dataclass
from importlib.util import find_spec
from typing import Any

import pypdfium2 as _pdfium  # pyright: ignore[reportMissingTypeStubs]
import pypdfium2.raw as _pdfium_raw  # pyright: ignore[reportMissingTypeStubs]

from ringkeeper.adapters.base import Image, ToolError

logger = logging.getLogger(__name__)

MOST_SIDE = 1600  # pixels, the longer side of what's shown
MOST_BYTES = 1_500_000  # of what's shown
LEAST_SIDE = 600  # what a page is never drawn smaller than to fit
MOST_SCALE = 200 / 72  # a small page is drawn at 200 dpi at most
# Smaller than this is an icon or a line, not something with words in it.
LEAST_PIXELS = 20_000
SHOWN = frozenset({"image/png", "image/jpeg", "image/gif", "image/webp"})
LOCK = threading.RLock()
pdfium: Any = _pdfium
pdfium_raw: Any = _pdfium_raw
_MODES = {"L": (1, 0), "BGR": (3, 2), "RGB": (3, 2), "BGRX": (4, 2), "RGBX": (4, 2),
          "BGRA": (4, 6), "RGBA": (4, 6)}  # fmt: skip


def _chunk(kind: bytes, data: bytes) -> bytes:
    body = kind + data
    return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)


def png(width: int, height: int, mode: str, buffer: bytes, stride: int) -> bytes:
    """A PNG of raw pixels: grey, or three or four bytes each (B, G, R order unless mode
    starts with RGB). An unused fourth byte (X) is left out."""
    channels, color = _MODES[mode]
    rows = bytearray()
    for y in range(height):
        row = buffer[y * stride : y * stride + width * channels]
        if channels > 1:
            pixels = bytearray(row)
            if mode.startswith("BGR"):
                pixels[0::channels], pixels[2::channels] = row[2::channels], row[0::channels]
            if mode.endswith("X"):
                del pixels[3::4]
            row = bytes(pixels)
        rows += b"\x00" + row
    header = struct.pack(">IIBBBBB", width, height, 8, color, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", header)
        + _chunk(b"IDAT", zlib.compress(bytes(rows), 6))
        + _chunk(b"IEND", b"")
    )


def _bitmap_png(bitmap: Any) -> tuple[bytes, int, int]:
    data = bytes(bitmap.buffer)
    width, height = int(bitmap.width), int(bitmap.height)
    return png(width, height, str(bitmap.mode), data, int(bitmap.stride)), width, height


@contextlib.contextmanager
def document(data: bytes) -> Generator[Any]:
    """A PDF opened with PDFium, under the lock. ToolError if it can't be opened."""
    with LOCK:
        try:
            pdf = pdfium.PdfDocument(data)
        except pdfium.PdfiumError:
            raise ToolError("that PDF is damaged, or has a password, and can't be shown") from None
        try:
            yield pdf
        finally:
            pdf.close()


def page_pictures(page: Any) -> list[Any]:
    """The pictures on a page big enough to hold words, in the order they're drawn."""
    found: list[Any] = []
    with contextlib.suppress(pdfium.PdfiumError):
        for item in page.get_objects(filter=[pdfium_raw.FPDF_PAGEOBJ_IMAGE], max_depth=3):
            with contextlib.suppress(pdfium.PdfiumError):
                width, height = item.get_px_size()
                if width * height >= LEAST_PIXELS and min(width, height) >= 16:
                    found.append(item)
    return found


def picture_png(item: Any) -> bytes | None:
    """One picture of a page as a PNG, at its own size (for reading the words in it)."""
    try:
        return _bitmap_png(item.get_bitmap(render=False))[0]
    except (pdfium.PdfiumError, KeyError, ValueError):
        return None


@dataclass(frozen=True)
class Found:
    """The pictures on one page: how many, and each as a PNG when they were wanted."""

    count: int
    pngs: list[bytes] | None


def pdf_pictures(data: bytes, wanted: Callable[[], bool]) -> Generator[Found]:
    """Each page's pictures in turn. `wanted` says, before each page, whether to take them out
    (they're only counted otherwise). Nothing at all if PDFium can't open the file."""
    with LOCK:
        try:
            pdf = pdfium.PdfDocument(data)
        except pdfium.PdfiumError:
            return
    try:
        for index in range(len(pdf)):
            take = wanted()
            with LOCK:
                page = pdf[index]
                try:
                    found = page_pictures(page)
                    pngs = [p for p in map(picture_png, found) if p] if take and found else None
                finally:
                    page.close()
            yield Found(len(found), pngs if take else None)
    finally:
        with LOCK:
            pdf.close()


def _drawn(page: Any, scale: float, crop: tuple[float, float, float, float]) -> Image:
    bitmap = page.render(scale=scale, crop=crop, rev_byteorder=True, draw_annots=True)
    data, width, height = _bitmap_png(bitmap)
    return Image(data, "image/png", width, height)


def _fitting(page: Any, size: float, crop: tuple[float, float, float, float]) -> Image:
    """Drawn as large as it's allowed, then smaller until it's few enough bytes."""
    scale = min(MOST_SIDE / max(size, 1), MOST_SCALE)
    shown = _drawn(page, scale, crop)
    while len(shown.data) > MOST_BYTES and max(shown.width, shown.height) > LEAST_SIDE:
        scale *= 0.75
        shown = _drawn(page, scale, crop)
    if len(shown.data) > MOST_BYTES:
        if smaller := _jpeg(shown.data):
            return smaller
        raise ToolError("that page is too detailed to show as a picture")
    return shown


def pdf_page(data: bytes, number: int) -> tuple[Image, int]:
    """Page `number` (from 1) drawn as a picture, and how many pages there are."""
    with document(data) as pdf:
        count = len(pdf)
        if not 1 <= number <= count:
            raise ToolError(f"that PDF has {count} page{'' if count == 1 else 's'}")
        page = pdf[number - 1]
        width, height = page.get_size()
        return _fitting(page, max(width, height), (0, 0, 0, 0)), count


def pdf_picture(data: bytes, number: int, picture: int) -> tuple[Image, int]:
    """Picture `picture` on page `number`, drawn as it shows on the page, and how many
    pictures that page has."""
    with document(data) as pdf:
        count = len(pdf)
        if not 1 <= number <= count:
            raise ToolError(f"that PDF has {count} page{'' if count == 1 else 's'}")
        page = pdf[number - 1]
        found = page_pictures(page)
        if not 1 <= picture <= len(found):
            raise ToolError(f"page {number} has {len(found) or 'no'} pictures to show")
        left, bottom, right, top = found[picture - 1].get_bounds()
        width, height = page.get_size()
        crop = (max(left, 0), max(bottom, 0), max(width - right, 0), max(height - top, 0))
        size = max(right - left, top - bottom)
        return _fitting(page, size, crop), len(found)


def _size(data: bytes, mime: str) -> tuple[int, int]:
    """A picture's width and height from its header, or (0, 0) if it doesn't say."""
    with contextlib.suppress(struct.error, IndexError, ValueError):
        if mime == "image/png" and data[12:16] == b"IHDR":
            width, height = struct.unpack(">II", data[16:24])
            return int(width), int(height)
        if mime == "image/gif":
            width, height = struct.unpack("<HH", data[6:10])
            return int(width), int(height)
        if mime == "image/jpeg":
            at = 2
            while at < len(data) - 9:
                marker, length = data[at + 1], struct.unpack(">H", data[at + 2 : at + 4])[0]
                if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                    height, width = struct.unpack(">HH", data[at + 5 : at + 9])
                    return int(width), int(height)
                at += 2 + length
    return 0, 0


def _jpeg(data: bytes) -> Image | None:
    """With Pillow: the picture at most MOST_SIDE wide or high, as a JPEG under MOST_BYTES."""
    if find_spec("PIL") is None:
        return None
    pil: Any = importlib.import_module("PIL.Image")  # only where the scan reader brought it

    try:
        with pil.open(io.BytesIO(data)) as opened:
            picture = opened.convert("RGB")
    except (OSError, ValueError, pil.DecompressionBombError):
        return None
    picture.thumbnail((MOST_SIDE, MOST_SIDE))
    for quality in (85, 70, 55, 40):
        out = io.BytesIO()
        picture.save(out, "JPEG", quality=quality)
        if out.tell() <= MOST_BYTES:
            return Image(out.getvalue(), "image/jpeg", picture.width, picture.height)
    return None


def _jpeg_redrawn(data: bytes) -> Image | None:
    """Without Pillow: a JPEG drawn smaller by PDFium, on a page of its own."""
    width, height = _size(data, "image/jpeg")
    if not width or not height:
        return None
    with LOCK:
        pdf = pdfium.PdfDocument.new()
        try:
            page = pdf.new_page(width, height)
            item = pdfium.PdfImage.new(pdf)
            item.load_jpeg(io.BytesIO(data), pages=[page])
            item.set_matrix(pdfium.PdfMatrix().scale(width, height))
            page.insert_obj(item)
            page.gen_content()
            return _fitting(page, max(width, height), (0, 0, 0, 0))
        except Exception:  # a JPEG PDFium can't take: said as too large, below
            logger.info("a JPEG couldn't be drawn smaller")
            return None
        finally:
            pdf.close()


def picture(data: bytes, mime: str) -> Image:
    """A picture file, as it is when it's small enough to show, or made smaller."""
    width, height = _size(data, mime)
    small = len(data) <= MOST_BYTES and max(width, height) <= MOST_SIDE
    if mime in SHOWN and small and (width or mime == "image/webp"):
        return Image(data, mime, width, height)
    shown = _jpeg(data) or (_jpeg_redrawn(data) if mime == "image/jpeg" else None)
    if shown is not None:
        return shown
    if mime in SHOWN and len(data) <= MOST_BYTES:
        return Image(data, mime, width, height)  # large, but the model makes it smaller
    kind = mime.removeprefix("image/").upper()
    why = "is too large" if mime in SHOWN else f"is a {kind}, which can't be shown"
    raise ToolError(f"that picture {why} here. Its text can still be read, or a copy saved.")
