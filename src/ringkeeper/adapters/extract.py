"""The text inside a file: an attachment, or something in Drive.

PDF, Word, Excel, PowerPoint, CSV and text are read here, and so are an attached email (.eml,
or Outlook's .msg) and a .zip: their own text and a list of what's in them, each readable by
`inside`. A picture, or the pictures in a PDF or a deck, need OCR: the desktop app's scan
reader (ocr_local), an optional download, or a `tesseract` program on this computer. Without
one the answer says so rather than returning nothing, and the desktop app offers the download.
`view` shows a page or a picture as an image instead, which keeps a table's or a chart's
layout (adapters.pictures).

These files come from outside, so a size that doesn't match what's inside, or an XML entity,
ends the reading.
"""

import contextlib
import csv
import email
import email.policy
import hashlib
import io
import logging
import posixpath
import re
import shutil
import struct
import subprocess
import tempfile
import time
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage, Message
from pathlib import Path, PurePosixPath
from typing import Any, cast
from xml.etree import ElementTree as ET

import olefile  # pyright: ignore[reportMissingTypeStubs]
import pypdf
from pypdf.errors import PyPdfError

from ringkeeper import ocr_local
from ringkeeper.adapters import pictures
from ringkeeper.adapters.base import Image, ToolError
from ringkeeper.adapters.mail_text import best_text, html_to_text
from ringkeeper.config import get_settings

logger = logging.getLogger(__name__)

MAX_FILE_BYTES = 40_000_000
MAX_UNPACKED_BYTES = 200_000_000
MAX_TEXT_CHARS = 5_000_000
MAX_PDF_PAGES = 2000
MAX_LISTED = 500  # files of a .zip, in its list
MAX_ZIP_FILES = 20_000
MOST_SHRUNK = 200  # a file in a .zip packed smaller than this many times over is a bomb
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
A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
RELS = "{http://schemas.openxmlformats.org/package/2006/relationships}"
PDF = "application/pdf"
WORD = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
EXCEL = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
DECK = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
ZIP = "application/zip"
EML = "message/rfc822"
MSG = "application/vnd.ms-outlook"
BY_SUFFIX = {
    ".pdf": PDF,
    ".docx": WORD,
    ".xlsx": EXCEL,
    ".pptx": DECK,
    ".zip": ZIP,
    ".eml": EML,
    ".msg": MSG,
    ".csv": "text/csv",
    ".tsv": "text/tab-separated-values",
    ".txt": "text/plain",
    ".md": "text/plain",
    ".log": "text/plain",
    ".json": "application/json",
    ".xml": "application/xml",
    ".html": "text/html",
    ".htm": "text/html",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
    ".bmp": "image/bmp",
    ".gif": "image/gif",
    ".webp": "image/webp",
}
KNOWN = frozenset({*BY_SUFFIX.values(), "application/csv", MSG})
# The same kinds, under the other names senders give them.
ALIASES = {
    "application/x-zip-compressed": ZIP,
    "application/x-zip": ZIP,
    "multipart/x-zip": ZIP,
    "application/x-outlook-msg": MSG,
    "application/x-msg": MSG,
    "image/jpg": "image/jpeg",
}
# What a file Ringkeeper can't read is, and what would work instead.
INSTEAD = {
    ".doc": "an old Word document: save it as .docx or PDF",
    ".xls": "an old Excel workbook: save it as .xlsx or CSV",
    ".ppt": "an old PowerPoint deck: save it as .pptx or PDF",
    ".numbers": "a Numbers spreadsheet: export it as .xlsx or PDF",
    ".pages": "a Pages document: export it as .docx or PDF",
    ".key": "a Keynote deck: export it as .pptx or PDF",
    ".odt": "an OpenDocument text: save it as .docx or PDF",
    ".ods": "an OpenDocument spreadsheet: save it as .xlsx or CSV",
    ".odp": "an OpenDocument presentation: save it as .pptx or PDF",
    ".rtf": "a rich text file: save it as .docx, PDF or plain text",
    ".heic": "an iPhone photo (HEIC): export it as JPEG or PNG",
    ".heif": "an iPhone photo (HEIF): export it as JPEG or PNG",
    ".rar": "a RAR archive: send the files themselves, or a .zip",
    ".7z": "a 7-Zip archive: send the files themselves, or a .zip",
    ".tar": "a tar archive: send the files themselves, or a .zip",
    ".gz": "a compressed file: send the file itself, or a .zip",
    ".tgz": "a compressed archive: send the files themselves, or a .zip",
    ".psd": "a Photoshop file: export it as PNG, JPEG or PDF",
    ".ai": "an Illustrator file: export it as PDF or PNG",
    ".dwg": "a CAD drawing: export it as PDF",
    ".vsdx": "a Visio drawing: export it as PDF",
    ".epub": "an e-book: a PDF copy would work",
}
PICTURE_SUFFIXES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                    ".gif": "image/gif", ".bmp": "image/bmp", ".tif": "image/tiff",
                    ".tiff": "image/tiff", ".webp": "image/webp"}  # fmt: skip


@dataclass(frozen=True)
class Page:
    """A page of a PDF, a slide of a deck or a sheet of a workbook."""

    number: int  # from 1
    text: str
    pictures: int = 0  # pictures on it that can hold words
    unread: int = 0  # of those, how many weren't read (no scan reader, or no time left)
    title: str = ""  # a slide's or a sheet's


@dataclass(frozen=True)
class Inner:
    """A file inside this one: a file in a .zip, or an attached email's attachment."""

    ref: str  # what `inside` takes to read it
    name: str
    size: int
    type: str


@dataclass(frozen=True)
class Extracted:
    text: str
    kind: str
    note: str | None = None  # what couldn't be read, when something couldn't
    pages: tuple[Page, ...] = ()
    unit: str = "page"  # what one of `pages` is called
    pictures: int = 0  # a Word document's, which `view` shows by number
    inside: tuple[Inner, ...] = ()


def _member(archive: zipfile.ZipFile, name: str, most: int = MAX_UNPACKED_BYTES) -> bytes:
    with archive.open(name) as found:
        data = found.read(most + 1)
    if len(data) > most:
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
    if len(archive.infolist()) > MAX_ZIP_FILES:
        raise ToolError("that file holds far more files than it should; it wasn't read")
    if sum(i.file_size for i in archive.infolist()) > MAX_UNPACKED_BYTES:
        raise ToolError("that file unpacks to far more than it should; it wasn't read")
    return archive


def _relations(archive: zipfile.ZipFile, part: str) -> dict[str, str]:
    """A part's relations: each id and the file it points to, as a path in the archive."""
    folder, name = posixpath.split(part)
    path = posixpath.join(folder, "_rels", f"{name}.rels")
    if path not in archive.namelist():
        return {}
    found: dict[str, str] = {}
    for item in _xml(_member(archive, path)).iter(f"{RELS}Relationship"):
        target = str(item.get("Target") or "")
        if item.get("TargetMode") == "External" or not target:
            continue
        joined = target.lstrip("/") if target.startswith("/") else posixpath.join(folder, target)
        found[str(item.get("Id"))] = posixpath.normpath(joined) + f"#{item.get('Type', '')}"
    return found


def _picture_parts(archive: zipfile.ZipFile, part: str, root: ET.Element) -> list[str]:
    """The pictures a part shows, in order, as paths in the archive."""
    relations = _relations(archive, part)
    found: list[str] = []
    for blip in root.iter(f"{A}blip"):
        path = relations.get(str(blip.get(f"{R}embed") or ""), "").split("#", 1)[0]
        if path and Path(path).suffix.lower() in PICTURE_SUFFIXES and path not in found:
            found.append(path)
    return found


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


def _docx_pictures(data: bytes) -> list[tuple[str, bytes]]:
    with _archive(data) as archive:
        if "word/document.xml" not in archive.namelist():
            raise ToolError("that isn't a Word document")
        root = _xml(_member(archive, "word/document.xml"))
        return [
            (p, _member(archive, p, MAX_FILE_BYTES))
            for p in _picture_parts(archive, "word/document.xml", root)
        ]


def _docx(data: bytes) -> Extracted:
    with _archive(data) as archive:
        if "word/document.xml" not in archive.namelist():
            raise ToolError("that isn't a Word document")
        root = _xml(_member(archive, "word/document.xml"))
        count = len(_picture_parts(archive, "word/document.xml", root))
    lines: list[str] = []
    _word_block(root, lines)
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
    note = (
        f"It has {count} picture{'' if count == 1 else 's'}, which weren't read: the view tool "
        f"shows them (picture=1{f' to {count}' if count > 1 else ''})."
        if count
        else None
    )
    return Extracted(text, "word", note, pictures=count)


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


def _xlsx(data: bytes) -> Extracted:
    pages: list[Page] = []
    written = 0
    with _archive(data) as archive:
        if "xl/workbook.xml" not in archive.namelist():
            raise ToolError("that isn't an Excel workbook")
        shared = _shared_strings(archive)
        for number, (name, path) in enumerate(_sheets(archive), start=1):
            out = io.StringIO()
            out.write(f"# {name}\n")
            rows = csv.writer(out, lineterminator="\n")
            for row in _xml(_member(archive, path)).iter(f"{S}row"):
                values: list[str] = []
                for cell in row.findall(f"{S}c"):
                    at = _column(str(cell.get("r") or "A"))
                    values += [""] * (at - len(values))
                    values.append(_cell(cell, shared))
                rows.writerow(values)
            pages.append(Page(number, out.getvalue().strip(), title=name))
            written += out.tell()
            if written > MAX_TEXT_CHARS:
                break
    text = "\n\n".join(p.text for p in pages).strip()
    return Extracted(text, "excel", pages=tuple(pages), unit="sheet")


def _slide_text(node: ET.Element, lines: list[str]) -> None:
    """A slide's words in order: a table's rows as rows, everything else by paragraph."""
    for child in node:
        if child.tag == f"{A}tr":
            cells = [
                " ".join("".join(t.text or "" for t in p.iter(f"{A}t")) for p in c.iter(f"{A}p"))
                for c in child.findall(f"{A}tc")
            ]
            lines.append(" | ".join(c.strip() for c in cells))
        elif child.tag == f"{A}p":
            lines.append("".join(t.text or "" for t in child.iter(f"{A}t")))
        else:
            _slide_text(child, lines)


def _placeholder(shape: ET.Element) -> str:
    found = shape.find(f"{P}nvSpPr/{P}nvPr/{P}ph")
    return "" if found is None else str(found.get("type") or "body")


def _shape_text(shape: ET.Element) -> str:
    lines: list[str] = []
    _slide_text(shape, lines)
    return "\n".join(line for line in lines if line.strip()).strip()


def _deck_slides(archive: zipfile.ZipFile) -> list[str]:
    """The slides' files, in the deck's order."""
    if "ppt/presentation.xml" not in archive.namelist():
        raise ToolError("that isn't a PowerPoint deck")
    deck = _xml(_member(archive, "ppt/presentation.xml"))
    relations = _relations(archive, "ppt/presentation.xml")
    found: list[str] = []
    for slide in deck.iter(f"{P}sldId"):
        path = relations.get(str(slide.get(f"{R}id") or ""), "").split("#", 1)[0]
        if path in archive.namelist():
            found.append(path)
    return found


def _notes(archive: zipfile.ZipFile, slide: str) -> str:
    for target in _relations(archive, slide).values():
        path, kind = target.split("#", 1)
        if kind.endswith("/notesSlide") and path in archive.namelist():
            root = _xml(_member(archive, path))
            shapes = [s for s in root.iter(f"{P}sp") if _placeholder(s) == "body"]
            return "\n".join(t for t in map(_shape_text, shapes) if t)
    return ""


def _pptx(data: bytes, reading: "_Reading") -> Extracted:
    pages: list[Page] = []
    with _archive(data) as archive:
        slides = _deck_slides(archive)
        for number, slide in enumerate(slides, start=1):
            root = _xml(_member(archive, slide))
            shapes = list(root.iter(f"{P}sp"))
            title = next(
                (_shape_text(s) for s in shapes if _placeholder(s) in ("title", "ctrTitle")), ""
            )
            lines: list[str] = []
            _slide_text(root, lines)
            text = "\n".join(line for line in lines if line.strip())
            found = _picture_parts(archive, slide, root)
            read = [reading.read(_member(archive, p, MAX_FILE_BYTES), Path(p).suffix)
                    for p in found]  # fmt: skip
            if said := "\n".join(t for t in read if t):
                text += f"\n[Text in pictures on this slide]\n{said}"
            if notes := _notes(archive, slide):
                text += f"\n[Speaker notes]\n{notes}"
            unread = sum(1 for t in read if t is None)
            pages.append(Page(number, text.strip(), len(found), unread, title.replace("\n", " ")))
    notes = reading.notes(pages, "slide")
    return Extracted(joined(pages, "slide"), "powerpoint", notes, tuple(pages), "slide")


def _deck_pictures(data: bytes, number: int) -> list[tuple[str, bytes]]:
    with _archive(data) as archive:
        slides = _deck_slides(archive)
        if not 1 <= number <= len(slides):
            raise ToolError(f"that deck has {len(slides)} slide{'' if len(slides) == 1 else 's'}")
        root = _xml(_member(archive, slides[number - 1]))
        return [
            (p, _member(archive, p, MAX_FILE_BYTES))
            for p in _picture_parts(archive, slides[number - 1], root)
        ]


def _local() -> ocr_local.LocalOcr:
    return ocr_local.model(get_settings())


def ocr_available() -> bool:
    return _local().ready() or shutil.which("tesseract") is not None


def scans_readable() -> bool:
    """A scanned page's picture comes out with PDFium (adapters.pictures), so reading one only
    needs a scan reader."""
    return ocr_available()


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


def spans(numbers: list[int]) -> str:
    """Page numbers as a person writes them: 1-3, 7, 9-10."""
    found: list[str] = []
    for number in sorted(set(numbers)):
        if found and number == int(found[-1].split("-")[-1]) + 1:
            found[-1] = f"{found[-1].split('-')[0]}-{number}"
        else:
            found.append(str(number))
    return ", ".join(found)


class _Reading:
    """The pictures of one file, read within its time, and each only the first time it's seen
    (a logo on every page is read once)."""

    def __init__(self) -> None:
        self.able = scans_readable()
        self.deadline = time.monotonic() + OCR_BUDGET_SECONDS
        self.seen: dict[str, str] = {}
        self.late = False

    def can(self) -> bool:
        return self.able and time.monotonic() < self.deadline

    def read(self, data: bytes | None, suffix: str) -> str | None:
        """Its text ("" if it has none), or None when it wasn't read."""
        if data is None:
            return None
        key = hashlib.sha256(data).hexdigest()
        if key in self.seen:
            return ""
        if not self.can():
            self.late = self.able
            return None
        self.seen[key] = text = _ocr(data, suffix or ".png")
        return text

    def notes(self, pages: list[Page], unit: str) -> str | None:
        empty = [p.number for p in pages if not p.text]
        unread = [p.number for p in pages if p.unread]
        notes: list[str] = []
        if empty:
            notes.append(f"{len(empty)} of {len(pages)} {unit}s had no text that could be read.")
        if unread and self.able:
            notes.append(
                f"The pictures on {unit}s {spans(unread)} weren't read, for lack of time: look at "
                "them with the view tool, or say so in your answer."
            )
        elif unread:
            notes.append(f"The pictures on {unit}s {spans(unread)} weren't read. {_no_ocr()}")
        return " ".join(notes) or None


def _has_pictures(page: Any) -> bool:
    try:
        return bool(page.images.keys())
    except Exception:
        return False


def _pdf_pages(data: bytes) -> list[Any]:
    try:
        reader = pypdf.PdfReader(io.BytesIO(data))
        if reader.is_encrypted and not reader.decrypt(""):
            raise ToolError("that PDF has a password, so it can't be read here")
        return list(reader.pages)
    except (PyPdfError, ValueError, OSError, KeyError):
        raise ToolError("that PDF is damaged and can't be read") from None


def _page_text(page: Any) -> str:
    try:
        return (page.extract_text() or "").strip()
    except Exception:
        logger.info("a page of a PDF couldn't be read")
        return ""


def _pdf(data: bytes, reading: "_Reading") -> Extracted:
    pages = _pdf_pages(data)
    written: list[Page] = []
    with contextlib.closing(pictures.pdf_pictures(data, reading.can)) as drawn:
        for number, page in enumerate(pages[:MAX_PDF_PAGES], start=1):
            text = _page_text(page)
            found = next(drawn, None)
            if found is None:  # PDFium couldn't open it: pypdf still knows there are pictures
                found = pictures.Found(1 if _has_pictures(page) else 0, None)
            read = [reading.read(p, ".png") for p in found.pngs or []]
            if said := "\n".join(t for t in read if t):
                text = f"{text}\n[Text in pictures on this page]\n{said}" if text else said
            unread = found.count - sum(1 for t in read if t is not None)
            written.append(Page(number, text, found.count, unread))
    note = reading.notes(written, "page")
    if len(pages) > MAX_PDF_PAGES:
        more = f"Only the first {MAX_PDF_PAGES} of {len(pages)} pages were read."
        note = f"{note} {more}" if note else more
    return Extracted(joined(written, "page", len(pages)), "pdf", note, tuple(written))


def joined(pages: list[Page] | tuple[Page, ...], unit: str, total: int | None = None) -> str:
    """Pages with a line saying which each is: [Page 2 of 9]."""
    of = total or len(pages)
    blocks: list[str] = []
    for page in pages:
        title = f" {page.title}" if page.title and unit != "sheet" else ""
        head = f"[{unit.title()} {page.number} of {of}]{title}"
        blocks.append(page.text if unit == "sheet" else f"{head}\n{page.text}")
    return "\n\n".join(blocks)


def _decoded(data: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-16" if data[:2] in (b"\xff\xfe", b"\xfe\xff") else "utf-8"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1")


def kind_of(name: str, mime: str) -> str:
    """A file's type, from its name when the sender only said "octet-stream", or gave a type
    nothing here reads (a storage service's guess) for a name that says what it is."""
    given = mime.split(";", maxsplit=1)[0].strip().lower()
    given = ALIASES.get(given, given)
    named = BY_SUFFIX.get(Path(name).suffix.lower())
    generic = given in ("", "application/octet-stream", "binary/octet-stream")
    if not generic and (named is None or _known(given)):
        return given
    return named or given or "application/octet-stream"


def _known(kind: str) -> bool:
    return kind in KNOWN or kind.startswith(("text/", "image/"))


# --- Files inside files ---


def _listing(found: list[Inner], what: str) -> str:
    shown = [f"{i.ref}. {i.name} ({i.type}, {i.size:,} bytes)" for i in found[:MAX_LISTED]]
    if len(found) > MAX_LISTED:
        shown.append(f"... and {len(found) - MAX_LISTED:,} more")
    return f"{what}:\n" + "\n".join(shown) if found else ""


def _zip_files(archive: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    return [i for i in archive.infolist() if not i.is_dir()]


def _zip(data: bytes) -> Extracted:
    with _archive(data) as archive:
        files = _zip_files(archive)
    found = [
        Inner(i.filename, PurePosixPath(i.filename).name, i.file_size, kind_of(i.filename, ""))
        for i in files
    ]
    listed = [f"{i.filename} ({i.file_size:,} bytes)" for i in files[:MAX_LISTED]]
    if len(files) > MAX_LISTED:
        listed.append(f"... and {len(files) - MAX_LISTED:,} more")
    text = f"A .zip of {len(files)} file{'' if len(files) == 1 else 's'}:\n" + "\n".join(listed)
    note = "Read a file in it with inside=<its path>." if files else "The .zip is empty."
    return Extracted(text, "zip", note, inside=tuple(found))


def _from_zip(data: bytes, inside: str) -> tuple[str, str, bytes]:
    with _archive(data) as archive:
        files = _zip_files(archive)
        wanted = inside.strip().lstrip("/")
        item = next((i for i in files if i.filename == wanted), None)
        if item is None:
            named = [i for i in files if PurePosixPath(i.filename).name == wanted]
            item = named[0] if len(named) == 1 else None
        if item is None:
            raise ToolError(f"there's no {inside} in that .zip: its list shows the paths")
        if item.flag_bits & 0x1:
            raise ToolError(f"{item.filename} in that .zip has a password, so it can't be read")
        if item.file_size > MAX_FILE_BYTES:
            raise ToolError(f"{item.filename} is too large to read here ({item.file_size:,} bytes)")
        packed = max(item.compress_size, 1)
        if item.file_size > 1_000_000 and item.file_size / packed > MOST_SHRUNK:
            raise ToolError("that file unpacks to far more than it should; it wasn't read")
        try:
            content = _member(archive, item.filename, MAX_FILE_BYTES)
        except (zipfile.BadZipFile, NotImplementedError, RuntimeError, OSError):
            raise ToolError(f"{item.filename} in that .zip is damaged") from None
    return PurePosixPath(item.filename).name, kind_of(item.filename, ""), content


def _email(data: bytes) -> EmailMessage:
    return email.message_from_bytes(data, policy=email.policy.default)


def _eml_parts(message: EmailMessage) -> list[tuple[str, str, bytes | None]]:
    found: list[tuple[str, str, bytes | None]] = []
    for number, part in enumerate(message.iter_attachments(), start=1):
        kind = part.get_content_type()
        if kind == EML:
            inner: object = part.get_payload(0) if part.is_multipart() else None
            content = inner.as_bytes() if isinstance(inner, Message) else b""
        else:
            content = cast(bytes | None, part.get_payload(decode=True)) or b""
        name = part.get_filename() or f"attachment {number}{'.eml' if kind == EML else ''}"
        found.append((name, kind, content))
    return found


def _eml(data: bytes) -> Extracted:
    message = _email(data)
    head = [f"{k}: {message[k]}" for k in ("From", "To", "Cc", "Date", "Subject") if message[k]]
    bodies: dict[str, str] = {}
    for kind in ("plain", "html"):
        part = message.get_body(preferencelist=(kind,))
        if part is not None:
            try:
                bodies[kind] = str(cast(EmailMessage, part).get_content())
            except (LookupError, ValueError):
                payload = cast(bytes | None, cast(EmailMessage, part).get_payload(decode=True))
                bodies[kind] = (payload or b"").decode("utf-8", "replace")
    found = [
        Inner(str(n), name, len(content or b""), kind_of(name, kind))
        for n, (name, kind, content) in enumerate(_eml_parts(message), start=1)
    ]
    return _mail(head, best_text(bodies.get("plain", ""), bodies.get("html", "")), found, "eml")


def _mail(head: list[str], body: str, found: list[Inner], kind: str) -> Extracted:
    listed = _listing(found, "Its attachments")
    text = "\n".join(head) + f"\n\n{body.strip()}" + (f"\n\n{listed}" if listed else "")
    note = "Read an attachment of it with inside=<its number>." if found else None
    return Extracted(text.strip(), kind, note, inside=tuple(found))


def _ole_text(ole: Any, folder: list[str], code: str) -> str:
    """A text property of an Outlook message (or of one of its attachments)."""
    for suffix, encoding in (("001F", "utf-16-le"), ("001E", "cp1252")):
        path = [*folder, f"__substg1.0_{code}{suffix}"]
        if ole.exists(path):
            return bytes(ole.openstream(path).read()).decode(encoding, "replace").rstrip("\x00")
    return ""


def _ole_bytes(ole: Any, folder: list[str], code: str) -> bytes | None:
    path = [*folder, f"__substg1.0_{code}0102"]
    return bytes(ole.openstream(path).read(MAX_FILE_BYTES + 1)) if ole.exists(path) else None


def _ole_sent(ole: Any) -> str:
    """When it was sent, from the message's own properties."""
    if not ole.exists("__properties_version1.0"):
        return ""
    data = bytes(ole.openstream("__properties_version1.0").read())
    for at in range(32, len(data) - 15, 16):
        tag = struct.unpack("<I", data[at : at + 4])[0]
        if tag in (0x00390040, 0x0E060040):  # submitted, delivered: a FILETIME
            ticks = struct.unpack("<Q", data[at + 8 : at + 16])[0]
            when = datetime(1601, 1, 1, tzinfo=UTC) + timedelta(microseconds=ticks // 10)
            return when.strftime("%a, %d %b %Y %H:%M:%S +0000")
    return ""


def _msg_parts(ole: Any) -> list[tuple[str, str, bytes | None]]:
    folders = sorted({e[0] for e in ole.listdir(streams=True, storages=True)
                      if e and e[0].startswith("__attach_version1.0_")})  # fmt: skip
    found: list[tuple[str, str, bytes | None]] = []
    for number, folder in enumerate(folders, start=1):
        name = (
            _ole_text(ole, [folder], "3707")
            or _ole_text(ole, [folder], "3704")
            or _ole_text(ole, [folder], "3001")
            or f"attachment {number}"
        )
        content = _ole_bytes(ole, [folder], "3701")
        kind = _ole_text(ole, [folder], "370E") or ""
        if content is None and ole.exists([folder, "__substg1.0_3701000D"]):
            kind = MSG  # an Outlook message attached to this one, kept as its own storage
        found.append((name, kind_of(name, kind), content))
    return found


def _ole(data: bytes) -> Any:
    # Always as a file in memory: olefile takes a short bytes value for a path on this computer.
    if len(data) < olefile.MINIMAL_OLEFILE_SIZE or not olefile.isOleFile(io.BytesIO(data)):
        raise ToolError("that isn't an Outlook message, or it's damaged")
    try:
        opened: Any = cast(Any, olefile).OleFileIO(io.BytesIO(data))
        return opened
    except (OSError, ValueError, struct.error, IndexError):
        raise ToolError("that Outlook message is damaged and can't be read") from None


def _msg(data: bytes) -> Extracted:
    with _ole(data) as ole:
        try:
            sender = _ole_text(ole, [], "0C1A")
            address = _ole_text(ole, [], "5D01") or _ole_text(ole, [], "0C1F")
            who = f"{sender} <{address}>" if address and sender else sender or address
            head = [
                f"From: {who}",
                f"To: {_ole_text(ole, [], '0E04')}",
                f"Cc: {_ole_text(ole, [], '0E03')}",
                f"Date: {_ole_sent(ole)}",
                f"Subject: {_ole_text(ole, [], '0037')}",
            ]
            plain = _ole_text(ole, [], "1000")
            html = _decoded(_ole_bytes(ole, [], "1013") or b"")
            parts = _msg_parts(ole)
        except (OSError, ValueError, struct.error):
            raise ToolError("that Outlook message is damaged and can't be read") from None
    found = [
        Inner(str(n), name, len(content or b""), kind)
        for n, (name, kind, content) in enumerate(parts, start=1)
    ]
    head = [line for line in head if not line.endswith(": ")]
    return _mail(head, best_text(plain, html), found, "outlook")


def _attached(parts: list[tuple[str, str, bytes | None]], inside: str) -> tuple[str, str, bytes]:
    wanted = inside.strip()
    if wanted.isdigit() and 1 <= int(wanted) <= len(parts):
        name, kind, content = parts[int(wanted) - 1]
    else:
        named = [p for p in parts if p[0] == wanted]
        if len(named) != 1:
            raise ToolError(f"that email has no attachment {inside}: its list shows them")
        name, kind, content = named[0]
    if content is None:
        raise ToolError(f"{name} is an Outlook message kept inside this one, which can't be read")
    if len(content) > MAX_FILE_BYTES:
        raise ToolError(f"{name} is too large to read here ({len(content):,} bytes)")
    return name, kind, content


def opened(name: str, mime: str, data: bytes, inside: str | None) -> tuple[str, str, bytes]:
    """The file `inside` names in this one (a .zip's file, an email's attachment), or this one."""
    if not inside:
        return name, mime, data
    kind = kind_of(name, mime)
    if kind == ZIP:
        return _from_zip(data, inside)
    if kind == EML:
        return _attached(_eml_parts(_email(data)), inside)
    if kind == MSG:
        with _ole(data) as ole:
            return _attached(_msg_parts(ole), inside)
    raise ToolError(f"'{name}' has no files inside it: leave out `inside`")


# --- Reading ---


def _picture(name: str, data: bytes) -> Extracted:
    if not ocr_available():
        return Extracted("", "image", _no_ocr(), pictures=1)
    text = _ocr(data, Path(name).suffix or ".png")
    note = None if text else "No text could be read from this picture: the view tool shows it."
    return Extracted(text, "image", note, pictures=1)


def cant_read(name: str, kind: str) -> ToolError:
    """What a file Ringkeeper can't read is, and what would work instead."""
    instead = INSTEAD.get(Path(name).suffix.lower())
    if instead:
        return ToolError(f"'{name}' is {instead}. Text can't be taken out of that here.")
    return ToolError(
        f"'{name}' is a {kind} file; text can't be taken out of that here. A PDF, Word, Excel, "
        "PowerPoint, picture or text copy of it would work."
    )


def _unlimited(name: str, mime: str, data: bytes) -> Extracted:
    kind = kind_of(name, mime)
    readers: dict[str, Callable[[], Extracted]] = {
        PDF: lambda: _pdf(data, _Reading()),
        WORD: lambda: _docx(data),
        EXCEL: lambda: _xlsx(data),
        DECK: lambda: _pptx(data, _Reading()),
        ZIP: lambda: _zip(data),
        EML: lambda: _eml(data),
        MSG: lambda: _msg(data),
        "text/html": lambda: Extracted(html_to_text(_decoded(data)), "html"),
    }
    if kind in readers:
        return readers[kind]()
    if Path(name).suffix.lower() in INSTEAD:
        raise cant_read(name, kind)
    if kind.startswith("text/") or kind in (
        "application/json",
        "application/xml",
        "application/csv",
        "image/svg+xml",
    ):
        return Extracted(_decoded(data), "text")
    if kind.startswith("image/"):
        return _picture(name, data)
    raise cant_read(name, kind)


def extract(name: str, mime: str, data: bytes, inside: str | None = None) -> Extracted:
    """The text of a file (or of the file `inside` names in it), or a ToolError saying why it
    can't be read."""
    name, mime, data = opened(name, mime, data, inside)
    if len(data) > MAX_FILE_BYTES:
        raise ToolError(f"'{name}' is too large to read here ({len(data):,} bytes)")
    found = _unlimited(name, mime, data)
    if len(found.text) <= MAX_TEXT_CHARS:
        return found
    note = f"Only the first {MAX_TEXT_CHARS:,} characters of this file were taken out."
    return replace(found, text=found.text[:MAX_TEXT_CHARS], note=note)


# --- Showing ---


@dataclass(frozen=True)
class Shown:
    image: Image
    name: str
    kind: str  # the file's type
    where: dict[str, int]  # which page or picture this is, and of how many


def _numbered(found: list[tuple[str, bytes]], picture: int, what: str) -> tuple[str, bytes]:
    if not found:
        raise ToolError(f"{what} has no pictures to show")
    if not 1 <= picture <= len(found):
        raise ToolError(f"{what} has {len(found)} picture{'' if len(found) == 1 else 's'}")
    return found[picture - 1]


def _shown_picture(path: str, content: bytes) -> Image:
    return pictures.picture(content, PICTURE_SUFFIXES[Path(path).suffix.lower()])


def view(
    name: str, mime: str, data: bytes, page: int = 1, picture: int | None = None,
    inside: str | None = None,
) -> Shown:  # fmt: skip
    """One page of a PDF drawn as a picture, or one picture: a picture file, or the nth on a
    PDF's page, a deck's slide or in a Word document."""
    name, mime, data = opened(name, mime, data, inside)
    if len(data) > MAX_FILE_BYTES:
        raise ToolError(f"'{name}' is too large to show here ({len(data):,} bytes)")
    kind = kind_of(name, mime)
    if kind == PDF:
        if picture is None:
            image, count = pictures.pdf_page(data, page)
            return Shown(image, name, kind, {"page": page, "pages": count})
        image, count = pictures.pdf_picture(data, page, picture)
        return Shown(image, name, kind, {"page": page, "picture": picture, "pictures": count})
    if kind == DECK:
        found = _deck_pictures(data, page)
        path, content = _numbered(found, picture or 1, f"slide {page}")
        where = {"slide": page, "picture": picture or 1, "pictures": len(found)}
        return Shown(_shown_picture(path, content), name, kind, where)
    if kind == WORD:
        found = _docx_pictures(data)
        path, content = _numbered(found, picture or 1, "that document")
        return Shown(_shown_picture(path, content), name, kind,
                     {"picture": picture or 1, "pictures": len(found)})  # fmt: skip
    if kind.startswith("image/") and kind != "image/svg+xml":
        return Shown(pictures.picture(data, kind), name, kind, {"picture": 1, "pictures": 1})
    if kind in (ZIP, EML, MSG):
        raise ToolError(f"'{name}' holds other files: name one with `inside`")
    raise ToolError(
        f"'{name}' has no pages or pictures to show: read its text instead. A PDF's pages, a "
        "picture, and the pictures in a deck or a Word document can be shown."
    )
