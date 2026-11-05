"""The text inside a file: an attachment, or something in Drive.

PDF, Word, Excel, CSV and text are read here. A picture, or a scanned PDF, needs OCR: the
desktop app's scan reader (ocr_local), an optional download, or a `tesseract` program on this
computer. Without one the answer says so rather than returning nothing, and the desktop app
offers the download. These files come from outside, so a size that doesn't match what's
inside, or an XML entity, ends the reading.
"""

import csv
import io
import logging
import re
import shutil
import subprocess
import tempfile
import time
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from importlib.util import find_spec
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

import pypdf
from pypdf.errors import PyPdfError

from ringkeeper import ocr_local
from ringkeeper.adapters.base import ToolError
from ringkeeper.adapters.mail_text import html_to_text
from ringkeeper.config import get_settings

logger = logging.getLogger(__name__)

MAX_FILE_BYTES = 40_000_000
MAX_UNPACKED_BYTES = 200_000_000
MAX_TEXT_CHARS = 5_000_000
MAX_PDF_PAGES = 2000
OCR_SECONDS = 60
OCR_BUDGET_SECONDS = 180  # a whole file: a long scan shouldn't hold a task up for an hour
NO_OCR = (
    "This has pictures or scanned pages, and reading text from those needs the tesseract "
    "program, which isn't installed here. Don't guess at what they say."
)
OFFERED = (
    "This has pictures or scanned pages, and Ringkeeper can't read those until its scan reader "
    "is downloaded. The app is offering that download now: tell the person, and don't guess at "
    "what they say. Once it's in, this can be read again."
)
NOT_DOWNLOADED = (
    "This has pictures or scanned pages, and Ringkeeper can't read those without its scan "
    "reader, an optional download in Settings > Reading scans. Don't guess at what they say."
)
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
S = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
BY_SUFFIX = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".csv": "text/csv",
    ".tsv": "text/tab-separated-values",
    ".txt": "text/plain",
    ".md": "text/plain",
    ".log": "text/plain",
    ".json": "application/json",
    ".xml": "application/xml",
    ".html": "text/html",
    ".htm": "text/html",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
    ".bmp": "image/bmp",
    ".gif": "image/gif",
    ".webp": "image/webp",
}


@dataclass(frozen=True)
class Extracted:
    text: str
    kind: str
    note: str | None = None  # what couldn't be read, when something couldn't


def _member(archive: zipfile.ZipFile, name: str) -> bytes:
    with archive.open(name) as found:
        data = found.read(MAX_UNPACKED_BYTES + 1)
    if len(data) > MAX_UNPACKED_BYTES:
        raise ToolError("that file unpacks to far more than it should; it wasn't read")
    return data


def _xml(data: bytes) -> ET.Element:
    if b"<!ENTITY" in data[:4096] or b"<!DOCTYPE" in data[:4096]:
        raise ToolError("that file has XML it has no reason to have; it wasn't read")
    return ET.fromstring(data)  # noqa: S314 (no entities or doctype, checked above)


def _archive(data: bytes) -> zipfile.ZipFile:
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise ToolError("that file is damaged, or isn't what its name says") from None
    if sum(i.file_size for i in archive.infolist()) > MAX_UNPACKED_BYTES:
        raise ToolError("that file unpacks to far more than it should; it wasn't read")
    return archive


def _paragraph(paragraph: ET.Element) -> str:
    parts: list[str] = []
    for node in paragraph.iter():
        if node.tag == f"{W}t":
            parts.append(node.text or "")
        elif node.tag == f"{W}tab":
            parts.append("\t")
        elif node.tag in (f"{W}br", f"{W}cr"):
            parts.append("\n")
    return "".join(parts)


def _word_block(block: ET.Element, lines: list[str]) -> None:
    for child in block:
        if child.tag == f"{W}p":
            lines.append(_paragraph(child))
        elif child.tag == f"{W}tbl":
            for row in child.iter(f"{W}tr"):
                cells = [
                    " ".join(_paragraph(p) for p in cell.iter(f"{W}p")).strip()
                    for cell in row.findall(f"{W}tc")
                ]
                lines.append(" | ".join(cells))
        elif child.tag in (f"{W}body", f"{W}sdt", f"{W}sdtContent"):
            _word_block(child, lines)


def _docx(data: bytes) -> str:
    with _archive(data) as archive:
        if "word/document.xml" not in archive.namelist():
            raise ToolError("that isn't a Word document")
        root = _xml(_member(archive, "word/document.xml"))
    lines: list[str] = []
    _word_block(root, lines)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def _shared_strings(archive: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in archive.namelist():
        return []
    root = _xml(_member(archive, "xl/sharedStrings.xml"))
    return ["".join(t.text or "" for t in item.iter(f"{S}t")) for item in root.findall(f"{S}si")]


def _column(reference: str) -> int:
    letters = re.match(r"[A-Z]+", reference)
    number = 0
    for letter in letters.group(0) if letters else "A":
        number = number * 26 + ord(letter) - 64
    return number - 1


def _cell(cell: ET.Element, shared: list[str]) -> str:
    kind = cell.get("t")
    if kind == "inlineStr":
        return "".join(t.text or "" for t in cell.iter(f"{S}t"))
    value = cell.findtext(f"{S}v") or ""
    if kind == "s" and value.isdigit() and int(value) < len(shared):
        return shared[int(value)]
    return value


def _sheets(archive: zipfile.ZipFile) -> list[tuple[str, str]]:
    """Each sheet's name and file, in the workbook's order."""
    book = _xml(_member(archive, "xl/workbook.xml"))
    relations = _xml(_member(archive, "xl/_rels/workbook.xml.rels"))
    targets = {r.get("Id"): str(r.get("Target") or "") for r in relations}
    found: list[tuple[str, str]] = []
    for sheet in book.iter(f"{S}sheet"):
        target = targets.get(sheet.get(f"{R}id"), "").lstrip("/")
        path = target if target.startswith("xl/") else f"xl/{target}"
        if path in archive.namelist():
            found.append((str(sheet.get("name") or "Sheet"), path))
    return found


def _xlsx(data: bytes) -> str:
    written = io.StringIO()
    with _archive(data) as archive:
        if "xl/workbook.xml" not in archive.namelist():
            raise ToolError("that isn't an Excel workbook")
        shared = _shared_strings(archive)
        for name, path in _sheets(archive):
            written.write(f"# {name}\n")
            out = csv.writer(written, lineterminator="\n")
            for row in _xml(_member(archive, path)).iter(f"{S}row"):
                values: list[str] = []
                for cell in row.findall(f"{S}c"):
                    at = _column(str(cell.get("r") or "A"))
                    values += [""] * (at - len(values))
                    values.append(_cell(cell, shared))
                out.writerow(values)
            written.write("\n")
            if written.tell() > MAX_TEXT_CHARS:
                break
    return written.getvalue().strip()


def _local() -> ocr_local.LocalOcr:
    return ocr_local.model(get_settings())


def ocr_available() -> bool:
    return _local().ready() or shutil.which("tesseract") is not None


def scans_readable() -> bool:
    """Getting a picture out of a PDF takes Pillow, which comes with the scan reader."""
    return ocr_available() and find_spec("PIL") is not None


def _no_ocr() -> str:
    """Why a picture wasn't read, and the desktop app's offer of the reader, the first time."""
    local = _local()
    if not local.available():
        return NO_OCR
    return OFFERED if local.missed() else NOT_DOWNLOADED


def _ocr(data: bytes, suffix: str) -> str:
    local = _local()
    if local.ready():
        return local.read(data, suffix)
    program = shutil.which("tesseract")
    if program is None:
        return ""
    with tempfile.TemporaryDirectory(prefix="ringkeeper-ocr-") as folder:
        picture = Path(folder) / f"picture{suffix}"
        picture.write_bytes(data)
        try:
            done = subprocess.run(  # noqa: S603 (a fixed program, and our own file)
                [program, str(picture), "stdout"],
                capture_output=True, timeout=OCR_SECONDS, check=False,
            )  # fmt: skip
        except (OSError, subprocess.TimeoutExpired):
            return ""
    return done.stdout.decode("utf-8", "replace").strip() if done.returncode == 0 else ""


def _has_pictures(page: Any) -> bool:
    try:
        return bool(page.images.keys())
    except Exception:
        return False


def _pdf_page_pictures(page: Any) -> str:
    found: list[str] = []
    try:
        pictures = list(page.images)
    except Exception:
        return ""
    for picture in pictures:
        if text := _ocr(bytes(picture.data), Path(str(picture.name)).suffix or ".png"):
            found.append(text)
    return "\n".join(found)


def _pdf(data: bytes) -> Extracted:
    try:
        reader = pypdf.PdfReader(io.BytesIO(data))
        if reader.is_encrypted and not reader.decrypt(""):
            raise ToolError("that PDF has a password, so it can't be read here")
        pages = list(reader.pages)
    except (PyPdfError, ValueError, OSError, KeyError):
        raise ToolError("that PDF is damaged and can't be read") from None
    written: list[str] = []
    empty = late = pictured = 0
    scans = scans_readable()
    deadline = time.monotonic() + OCR_BUDGET_SECONDS
    for number, page in enumerate(pages[:MAX_PDF_PAGES], start=1):
        try:
            text = (page.extract_text() or "").strip()
        except Exception:
            logger.info("a page of a PDF couldn't be read")
            text = ""
        if not text and _has_pictures(page):
            pictured += 1
            if scans and time.monotonic() < deadline:
                text = _pdf_page_pictures(page)
            elif scans:
                late += 1
        empty += 0 if text else 1
        written.append(f"[Page {number} of {len(pages)}]\n{text}")
    notes: list[str] = []
    if empty:
        notes.append(f"{empty} of {len(pages)} pages had no text that could be read.")
    if late:
        notes.append(f"{late} scanned pages weren't read, for lack of time. Say so in your answer.")
    if pictured and not scans:
        notes.append(_no_ocr())
    if len(pages) > MAX_PDF_PAGES:
        notes.append(f"Only the first {MAX_PDF_PAGES} of {len(pages)} pages were read.")
    return Extracted("\n\n".join(written), "pdf", " ".join(notes) or None)


def _decoded(data: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-16" if data[:2] in (b"\xff\xfe", b"\xfe\xff") else "utf-8"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1")


def kind_of(name: str, mime: str) -> str:
    """A file's type, from its name when the sender only said "octet-stream"."""
    given = mime.split(";", maxsplit=1)[0].strip().lower()
    if given and given not in ("application/octet-stream", "binary/octet-stream"):
        return given
    return BY_SUFFIX.get(Path(name).suffix.lower(), given or "application/octet-stream")


def _picture(name: str, data: bytes) -> Extracted:
    if not ocr_available():
        return Extracted("", "image", _no_ocr())
    text = _ocr(data, Path(name).suffix or ".png")
    return Extracted(text, "image", None if text else "No text could be read from this picture.")


def _unlimited(name: str, mime: str, data: bytes) -> Extracted:
    kind = kind_of(name, mime)
    readers: dict[str, Callable[[], Extracted]] = {
        BY_SUFFIX[".pdf"]: lambda: _pdf(data),
        BY_SUFFIX[".docx"]: lambda: Extracted(_docx(data), "word"),
        BY_SUFFIX[".xlsx"]: lambda: Extracted(_xlsx(data), "excel"),
        "text/html": lambda: Extracted(html_to_text(_decoded(data)), "html"),
    }
    if kind in readers:
        return readers[kind]()
    if kind.startswith("text/") or kind in (
        "application/json",
        "application/xml",
        "application/csv",
    ):
        return Extracted(_decoded(data), "text")
    if kind.startswith("image/"):
        return _picture(name, data)
    raise ToolError(f"'{name}' is a {kind} file; text can't be taken out of that here")


def extract(name: str, mime: str, data: bytes) -> Extracted:
    """The text of a file, or a ToolError saying why it can't be read."""
    if len(data) > MAX_FILE_BYTES:
        raise ToolError(f"'{name}' is too large to read here ({len(data):,} bytes)")
    found = _unlimited(name, mime, data)
    if len(found.text) <= MAX_TEXT_CHARS:
        return found
    note = f"Only the first {MAX_TEXT_CHARS:,} characters of this file were taken out."
    return Extracted(found.text[:MAX_TEXT_CHARS], found.kind, note)
