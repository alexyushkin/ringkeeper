"""A plain PDF from the report's Markdown: headings, bullets, paragraphs and table rows.

Written directly, with the PDF standard fonts (Helvetica), so it needs nothing installed and
opens anywhere. Letter-size pages. Characters Helvetica's WinAnsi encoding lacks become "?".
"""

import re
import textwrap
import zlib

PAGE_W, PAGE_H = 612, 792
MARGIN = 54
LEADING = {"title": 24, "heading": 20, "text": 14}
SIZE = {"title": 18, "heading": 13, "text": 10}
WRAP = 100  # characters of 10pt Helvetica on a line between the margins, roughly
TABLE_RULE = re.compile(r"^\|?\s*-{3,}")


def _escape(text: str) -> bytes:
    raw = text.encode("cp1252", errors="replace")
    return raw.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")


def _lines(markdown: str) -> list[tuple[str, str]]:
    """(style, text) per printed line."""
    out: list[tuple[str, str]] = []
    header: list[str] | None = None
    for raw in markdown.splitlines():
        line = raw.rstrip()
        if line.startswith("# "):
            out.append(("title", line[2:]))
        elif line.startswith("## "):
            out += [("text", ""), ("heading", line[3:])]
        elif TABLE_RULE.match(line):
            continue
        elif line.startswith("|"):
            cells = [c.strip() for c in line.strip("|").split("|")]
            if header is None:
                header = cells
                continue
            first, *rest = cells
            out += [("bold", first)]
            for name, value in zip(header[1:], rest, strict=False):
                for i, part in enumerate(textwrap.wrap(f"{name}: {value}", WRAP - 4) or [""]):
                    out.append(("text", ("    " if i == 0 else "      ") + part))
            out.append(("text", ""))
        elif line.startswith("- "):
            for i, part in enumerate(textwrap.wrap(line[2:], WRAP - 4) or [""]):
                out.append(("text", ("  • " if i == 0 else "     ") + part))
        else:
            header = None if not line else header
            out += [("text", part) for part in (textwrap.wrap(line, WRAP) or [""])]
    return out


def render(markdown: str, title: str = "Report") -> bytes:
    pages: list[bytes] = []
    stream: list[bytes] = []
    y = PAGE_H - MARGIN
    for style, text in _lines(markdown):
        kind = "text" if style == "bold" else style
        step = LEADING[kind]
        if y - step < MARGIN:
            pages.append(b"\n".join(stream))
            stream, y = [], PAGE_H - MARGIN
        y -= step
        if not text:
            continue
        font = "F1" if style == "text" else "F2"
        stream.append(
            b"BT /%s %d Tf %d %d Td (%s) Tj ET"
            % (font.encode(), SIZE[kind], MARGIN, y, _escape(text))
        )
    pages.append(b"\n".join(stream))

    objects: list[bytes] = []

    def add(body: bytes) -> int:
        objects.append(body)
        return len(objects)

    catalog = add(b"")  # filled in once the pages are known
    tree = add(b"")
    regular = add(
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>"
    )
    bold = add(
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>"
    )
    kids: list[int] = []
    for content in pages:
        packed = zlib.compress(content)
        body = add(
            b"<< /Length %d /Filter /FlateDecode >>\nstream\n%s\nendstream" % (len(packed), packed)
        )
        kids.append(
            add(
                b"<< /Type /Page /Parent %d 0 R /MediaBox [0 0 %d %d] /Contents %d 0 R "
                b"/Resources << /Font << /F1 %d 0 R /F2 %d 0 R >> >> >>"
                % (tree, PAGE_W, PAGE_H, body, regular, bold)
            )
        )
    objects[catalog - 1] = b"<< /Type /Catalog /Pages %d 0 R >>" % tree
    objects[tree - 1] = b"<< /Type /Pages /Kids [%s] /Count %d >>" % (
        b" ".join(b"%d 0 R" % k for k in kids), len(kids),
    )  # fmt: skip
    info = add(b"<< /Title (%s) /Producer (Ringkeeper) >>" % _escape(title))

    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets: list[int] = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n%s\nendobj\n" % (number, body)
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % o for o in offsets)
    out += b"trailer\n<< /Size %d /Root %d 0 R /Info %d 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1, catalog, info, xref,
    )  # fmt: skip
    return bytes(out)
