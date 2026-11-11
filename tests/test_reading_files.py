# pyright: reportPrivateUsage=false
"""Reading files, wherever they come from: the pictures on a page, a page or a picture shown as
an image, a copy saved to work on, PowerPoint, attached emails and .zip files, and a long file
read outline first, then page by page."""

import base64
import io
import json
import struct
import time
import zipfile
from datetime import timedelta
from email.message import EmailMessage
from pathlib import Path
from typing import Any

import httpx2
import pytest
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from mcp_types import ImageContent
from sqlalchemy import select

from ringkeeper.adapters import ADAPTERS, extract, files, gmail, google, pictures, storage, vendor
from ringkeeper.adapters.base import IMAGE, Image, Target, ToolError
from ringkeeper.adapters.paging import PAGE_CHARS
from ringkeeper.adapters.vendors.microsoft import MICROSOFT
from ringkeeper.calls import Caller, ToolCall
from ringkeeper.db import session_scope
from ringkeeper.gateway.listing import lean
from ringkeeper.models import AuditEvent, Env, InstanceType
from ringkeeper.services import calls
from ringkeeper.tokens import issue_token
from ringkeeper.vault import Secret, get_vault
from ringkeeper_worker.edits import Edits
from ringkeeper_worker.workspace import Workspace
from tests.fake_services import FakeImap
from tests.helpers import bind, make_instance, make_project
from tests.test_worker_unit import git

pytestmark = pytest.mark.anyio
GMAIL_SECRET = json.dumps({"address": "owner@gmail.example", "app_password": "abcdefghijklmnop"})
W, H = 300, 100  # the price table's picture
P = 'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"'
A = 'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"'
R = 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
RELS = 'xmlns="http://schemas.openxmlformats.org/package/2006/relationships"'
KINDS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def _pdf(objects: list[bytes]) -> bytes:
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    places: list[int] = []
    for number, body in enumerate(objects, start=1):
        places.append(out.tell())
        out.write(b"%d 0 obj\n%s\nendobj\n" % (number, body))
    start = out.tell()
    out.write(b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1))
    for place in places:
        out.write(b"%010d 00000 n \n" % place)
    out.write(b"trailer\n<< /Size %d /Root 1 0 R >>\n" % (len(objects) + 1))
    out.write(b"startxref\n%d\n%%%%EOF\n" % start)
    return out.getvalue()


def pdf_with_picture() -> bytes:
    """One page: a line of text, and under it a picture (a price table, as a scan would be)."""
    pixels = bytes((x * 7 + y * 3) % 256 for y in range(H) for x in range(W))
    content = b"BT /F1 12 Tf 40 700 Td (Prices below) Tj ET q %d 0 0 %d 40 500 cm /Im1 Do Q" % (
        W,
        H,
    )
    return _pdf([
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Count 1 /Kids [4 0 R] >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 5 0 R "
        b"/Resources << /Font << /F1 3 0 R >> /XObject << /Im1 6 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n%s\nendstream" % (len(content), content),
        b"<< /Type /XObject /Subtype /Image /Width %d /Height %d /ColorSpace /DeviceGray "
        b"/BitsPerComponent 8 /Length %d >>\nstream\n%s\nendstream" % (W, H, len(pixels), pixels),
    ])  # fmt: skip


def long_pdf(pages: int = 40, chars: int = 1000) -> bytes:
    """Many pages of one long line each: far more than one answer's worth."""
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Count %d /Kids [%s] >>"
        % (pages, " ".join(f"{4 + 2 * i} 0 R" for i in range(pages)).encode()),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    for number in range(pages):
        line = f"Page {number + 1} says " + "x" * chars
        content = f"BT /F1 6 Tf 10 700 Td ({line}) Tj ET".encode()
        objects.append(
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents %d 0 R "
            b"/Resources << /Font << /F1 3 0 R >> >> >>" % (5 + 2 * number)
        )
        objects.append(b"<< /Length %d >>\nstream\n%s\nendstream" % (len(content), content))
    return _pdf(objects)


class NoReader:
    """The desktop app's scan reader, not there (a server)."""

    def ready(self) -> bool:
        return False

    def available(self) -> bool:
        return False


@pytest.fixture
def ocr(monkeypatch: pytest.MonkeyPatch) -> list[bytes]:
    """OCR that finds the table's number in whatever picture it's given."""
    asked: list[bytes] = []

    def read(data: bytes, _suffix: str) -> str:
        asked.append(data)
        return "Oak cutting board | 42.50"

    monkeypatch.setattr(extract, "scans_readable", lambda: True)
    monkeypatch.setattr(extract, "_ocr", read)
    return asked


@pytest.fixture
def no_ocr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(extract, "_local", NoReader)
    monkeypatch.setattr(extract.shutil, "which", lambda _name="": None)


# --- (a) the pictures on a page with text ---


def test_a_picture_under_a_pages_text_is_read_and_its_text_added(ocr: list[bytes]) -> None:
    found = extract.extract("prices.pdf", "application/pdf", pdf_with_picture())
    assert "Prices below" in found.text
    assert "[Text in pictures on this page]\nOak cutting board | 42.50" in found.text
    assert found.text.index("Prices below") < found.text.index("42.50")  # under its own text
    assert found.pages[0].pictures == 1
    assert found.pages[0].unread == 0
    assert found.note is None
    [picture] = ocr
    assert picture.startswith(b"\x89PNG")
    assert struct.unpack(">II", picture[16:24]) == (W, H)  # the picture itself, at its size


@pytest.mark.usefixtures("no_ocr")
def test_without_ocr_the_note_says_which_pages_pictures_werent_read() -> None:
    found = extract.extract("prices.pdf", "application/pdf", pdf_with_picture())
    assert "Prices below" in found.text
    assert found.note is not None
    assert "The pictures on pages 1 weren't read" in found.note
    assert extract.NO_OCR in found.note


def test_out_of_time_the_note_says_which_pages(
    ocr: list[bytes], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(extract, "OCR_BUDGET_SECONDS", -1)
    found = extract.extract("prices.pdf", "application/pdf", pdf_with_picture())
    assert found.note is not None
    assert "pages 1 weren't read, for lack of time" in found.note
    assert "view tool" in found.note
    assert not ocr


def test_a_picture_seen_on_every_page_is_read_once(ocr: list[bytes]) -> None:
    reading = extract._Reading()
    first = reading.read(b"the same logo", ".png")
    again = reading.read(b"the same logo", ".png")
    assert (first, again) == ("Oak cutting board | 42.50", "")
    assert len(ocr) == 1


# --- (b) looking at a page ---


def test_a_pdf_page_is_drawn_as_a_picture_small_enough_to_show() -> None:
    shown = extract.view("prices.pdf", "application/pdf", pdf_with_picture(), page=1)
    assert shown.image.mime == "image/png"
    assert shown.image.data.startswith(b"\x89PNG")
    assert max(shown.image.width, shown.image.height) <= pictures.MOST_SIDE
    assert len(shown.image.data) <= pictures.MOST_BYTES
    assert shown.where == {"page": 1, "pages": 1}
    one = extract.view("prices.pdf", "application/pdf", pdf_with_picture(), 1, picture=1)
    assert one.where == {"page": 1, "picture": 1, "pictures": 1}
    assert one.image.width / one.image.height == pytest.approx(W / H, rel=0.05)
    with pytest.raises(ToolError, match="1 page"):
        extract.view("prices.pdf", "application/pdf", pdf_with_picture(), page=2)
    with pytest.raises(ToolError, match="no pages or pictures"):
        extract.view("a.csv", "text/csv", b"a,b\n")


def test_a_png_is_written_right() -> None:
    data = pictures.png(2, 1, "BGR", bytes([0, 0, 255, 255, 0, 0]), 6)
    assert data.startswith(b"\x89PNG\r\n\x1a\n")
    assert struct.unpack(">II", data[16:24]) == (2, 1)


def test_a_small_picture_is_shown_as_it_is() -> None:
    small = pictures.png(40, 30, "L", bytes(1200), 40)
    assert pictures.picture(small, "image/png") == Image(small, "image/png", 40, 30)


def message_with(*files_: tuple[str, str, bytes]) -> bytes:
    message = EmailMessage()
    message["From"] = "Sam <sam@supplier.example>"
    message["To"] = "owner@example.com"
    message["Subject"] = "Spring prices"
    message.set_content("The prices are attached.")
    for name, kind, data in files_:
        main, sub = kind.split("/")
        message.add_attachment(data, maintype=main, subtype=sub, filename=name)
    return message.as_bytes()


def gmail_target(project: str | None = None) -> Target:
    return Target("t", Env.DEV, None, Secret("r", GMAIL_SECRET), project=project)


@pytest.fixture
def mailbox(monkeypatch: pytest.MonkeyPatch) -> FakeImap:
    fake = FakeImap(messages={
        7: message_with(("prices.pdf", "application/pdf", pdf_with_picture())),
        8: message_with(("long.pdf", "application/pdf", long_pdf())),
    })  # fmt: skip
    monkeypatch.setattr(gmail, "imap_factory", lambda: fake)
    return fake


@pytest.fixture
def gateway_mail(bare_gateway: str, mailbox: FakeImap, tmp_path: Path) -> str:
    del mailbox
    ref = "file://ringkeeper/connections/acme-mail"
    get_vault().store(ref, GMAIL_SECRET)
    with session_scope() as session:
        make_project(session, "acme-web", workspace_path=str(tmp_path / "acme-web"))
        make_instance(session, "acme-mail", type=InstanceType.GMAIL, secret_ref=ref)
    bind("acme-web", "acme-mail", is_default=True)
    return bare_gateway


async def test_view_reaches_the_agent_as_an_image_and_the_audit_keeps_no_bytes(
    gateway_mail: str,
) -> None:
    token, _ = issue_token("acme-web", timedelta(minutes=10))
    transport = StreamableHttpTransport(
        f"{gateway_mail}/mcp/gmail", headers={"Authorization": f"Bearer {token}"}
    )
    async with Client(transport) as mcp:
        result = await mcp.call_tool("view_attachment", {"uid": 7, "attachment_id": "1"})
    [said, picture] = result.content
    assert isinstance(picture, ImageContent)
    assert picture.mime_type == "image/png"
    data = base64.b64decode(picture.data)
    assert data.startswith(b"\x89PNG")
    described = json.loads(getattr(said, "text", ""))
    assert described["name"] == "prices.pdf"
    assert (described["page"], described["pages"], described["bytes"]) == (1, 1, len(data))
    assert IMAGE not in described
    with session_scope() as session:
        row = session.scalars(select(AuditEvent).where(AuditEvent.tool == "view_attachment")).one()
        kept = json.dumps(row.args) + (row.result or "")
    shown = json.loads(row.result or "{}")
    assert shown["name"] == "prices.pdf"
    assert (shown["page"], shown["bytes"], shown["shown_as"]) == (1, len(data), "image/png")
    assert picture.data[:200] not in kept
    assert len(kept) < 1000


async def test_view_is_audited_and_never_split_or_scrubbed(
    mailbox: FakeImap, database_url: str
) -> None:
    """Through the call pipeline: the picture comes back as it is, beside a fitted result."""
    del mailbox, database_url
    ref = "file://ringkeeper/connections/acme-mail"
    get_vault().store(ref, GMAIL_SECRET)
    with session_scope() as session:
        make_project(session, "acme-web")
        make_instance(session, "acme-mail", type=InstanceType.GMAIL, secret_ref=ref)
    bind("acme-web", "acme-mail", is_default=True)
    call = ToolCall(
        Caller("acme-web", "session-1", "token-1"), InstanceType.GMAIL, "view_attachment",
        None, {"uid": 7, "attachment_id": "1"},
    )  # fmt: skip
    found = await calls.run_call(call, get_vault())
    image = found[IMAGE]
    assert isinstance(image, Image)
    assert "continuation" not in found
    with session_scope() as session:
        row = session.scalars(select(AuditEvent)).one()
        assert row.tool == "view_attachment"
        assert row.result is not None
        assert base64.b64encode(image.data).decode()[:100] not in row.result


def test_a_schema_says_no_more_than_it_needs() -> None:
    schema = {
        "type": "object",
        "properties": {
            "instance": {"anyOf": [{"type": "string"}, {"type": "null"}], "default": None,
                         "description": "The connection."},
            "title": {"type": "string", "title": "Title"},
            "body": {"type": "object", "properties": {"title": {"type": "string"}}},
        },
    }  # fmt: skip
    assert lean(schema)["properties"] == {
        "instance": {"type": "string", "description": "The connection."},
        "title": {"type": "string"},
        "body": {"type": "object", "properties": {"title": {"type": "string"}}},
    }


# --- (c) saving a copy to work on ---


@pytest.mark.usefixtures("mailbox")
async def test_save_attachment_puts_it_in_the_attachments_folder(
    database_url: str, tmp_path: Path
) -> None:
    del database_url
    folder = tmp_path / "acme-web"
    folder.mkdir()
    with session_scope() as session:
        make_project(session, "acme-web", workspace_path=str(folder))
    found = await gmail.save_attachment.handler(gmail_target("acme-web"), uid=7, attachment_id="1")
    assert found["saved_to"] == "attachments/prices.pdf"
    assert (folder / "attachments" / "prices.pdf").read_bytes() == pdf_with_picture()
    again = await gmail.save_attachment.handler(gmail_target("acme-web"), uid=7, attachment_id="1")
    assert again["saved_to"] == "attachments/prices.pdf"  # the same file, not a second copy
    assert sorted(p.name for p in (folder / "attachments").iterdir()) == ["prices.pdf"]
    assert gmail.save_attachment.kind.value == "read"
    assert gmail.save_attachment.logged is not None


async def test_saving_needs_a_project_folder_it_can_reach(database_url: str) -> None:
    del database_url
    with pytest.raises(ToolError, match="no project"):
        await files.save(None, "a.pdf", b"x")
    with session_scope() as session:
        make_project(session, "acme-web", workspace_path="/nowhere/acme-web")
    with pytest.raises(ToolError, match="can't be reached"):
        await files.save("acme-web", "a.pdf", b"x")


def saved_result(path: str) -> object:
    """A tool result as the SDK hands it to the worker."""
    text = json.dumps({"saved_to": path, "size": 3})
    return type("Result", (), {"tool_use_id": "t1", "content": [{"type": "text", "text": text}]})()


def test_a_saved_file_is_the_tasks_own_and_is_committed_with_its_work(tmp_path: Path) -> None:
    root = tmp_path / "acme-web"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "dev@example.com")
    git(root, "config", "user.name", "Dev")
    (root / "README.md").write_text("acme\n", encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "initial")
    workspace = Workspace(root, "ringkeeper/t-1")
    workspace.start()
    (root / "attachments").mkdir()
    (root / "attachments" / "prices.pdf").write_bytes(b"pdf")  # as the gateway saves it
    (root / "attachments" / "other.pdf").write_bytes(b"pdf")  # not by this task
    own = Edits()
    message = type("User", (), {"content": [saved_result("attachments/prices.pdf")]})()
    own.message(message)
    own.message(type("User", (), {"content": [saved_result("../outside.txt")]})())
    assert own.paths == {"attachments/prices.pdf"}
    outcome = workspace.finish("ringkeeper: task t-1 session 1", own)
    assert outcome.changed_files == ["attachments/prices.pdf"]  # listed with the task's files


# --- (d) more kinds ---


def pptx() -> bytes:
    picture = pictures.png(200, 150, "L", bytes(200 * 150), 200)
    title = (
        '<p:sp><p:nvSpPr><p:cNvPr id="1" name="t"/><p:cNvSpPr/><p:nvPr><p:ph type="title"/>'
        "</p:nvPr></p:nvSpPr><p:txBody><a:p><a:r><a:t>Spring prices</a:t></a:r></a:p>"
        "</p:txBody></p:sp>"
    )
    cell = "<a:tc><a:txBody><a:p><a:r><a:t>{}</a:t></a:r></a:p></a:txBody></a:tc>"
    table = (
        "<p:graphicFrame><a:graphic><a:graphicData><a:tbl><a:tr>"
        f"{cell.format('Oak cutting board')}{cell.format('42.50')}"
        "</a:tr></a:tbl></a:graphicData></a:graphic></p:graphicFrame>"
    )
    pic = '<p:pic><p:blipFill><a:blip r:embed="rId3"/></p:blipFill></p:pic>'
    slide = f"<p:sld {P} {A} {R}><p:cSld><p:spTree>{{}}</p:spTree></p:cSld></p:sld>"
    notes = (
        f'<p:notes {P} {A}><p:cSld><p:spTree><p:sp><p:nvSpPr><p:cNvPr id="2" name="n"/>'
        '<p:cNvSpPr/><p:nvPr><p:ph type="body"/></p:nvPr></p:nvSpPr><p:txBody><a:p><a:r>'
        "<a:t>Say the board sells out</a:t></a:r></a:p></p:txBody></p:sp></p:spTree></p:cSld>"
        "</p:notes>"
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "ppt/presentation.xml",
            f'<p:presentation {P} {R}><p:sldIdLst><p:sldId id="256" r:id="rId2"/>'
            '<p:sldId id="257" r:id="rId9"/></p:sldIdLst></p:presentation>',
        )
        archive.writestr(
            "ppt/_rels/presentation.xml.rels",
            f'<Relationships {RELS}><Relationship Id="rId2" Type="{KINDS}/slide" '
            f'Target="slides/slide1.xml"/><Relationship Id="rId9" Type="{KINDS}/slide" '
            'Target="slides/slide2.xml"/></Relationships>',
        )
        archive.writestr("ppt/slides/slide1.xml", slide.format(title + table + pic))
        archive.writestr("ppt/slides/slide2.xml", slide.format("<p:sp><p:txBody><a:p><a:r>"
                         "<a:t>Thank you</a:t></a:r></a:p></p:txBody></p:sp>"))  # fmt: skip
        archive.writestr(
            "ppt/slides/_rels/slide1.xml.rels",
            f'<Relationships {RELS}><Relationship Id="rId3" Type="{KINDS}/image" '
            f'Target="../media/image1.png"/><Relationship Id="rId4" Type="{KINDS}/notesSlide" '
            'Target="../notesSlides/notesSlide1.xml"/></Relationships>',
        )
        archive.writestr("ppt/notesSlides/notesSlide1.xml", notes)
        archive.writestr("ppt/media/image1.png", picture)
    return buffer.getvalue()


@pytest.mark.usefixtures("no_ocr")
def test_a_deck_is_read_slide_by_slide_with_its_notes_and_its_pictures_shown() -> None:
    found = extract.extract("deck.pptx", "application/octet-stream", pptx())
    assert found.kind == "powerpoint"
    assert found.unit == "slide"
    assert "[Slide 1 of 2] Spring prices" in found.text
    assert "Oak cutting board | 42.50" in found.text
    assert "[Speaker notes]\nSay the board sells out" in found.text
    assert "[Slide 2 of 2]\nThank you" in found.text
    assert [(p.number, p.pictures, p.title) for p in found.pages] == [
        (1, 1, "Spring prices"), (2, 0, ""),
    ]  # fmt: skip
    assert found.note is not None
    assert "slides 1 weren't read" in found.note
    answer = files.text_answer({"name": "deck.pptx"}, found, 0, PAGE_CHARS, None, "view_file")
    assert "Slides 1 have pictures: view_file shows one" in answer["pictures"]
    shown = extract.view("deck.pptx", "", pptx(), page=1, picture=1)
    assert (shown.image.mime, shown.image.width, shown.image.height) == ("image/png", 200, 150)
    with pytest.raises(ToolError, match="no pictures"):
        extract.view("deck.pptx", "", pptx(), page=2)


def test_an_attached_email_lists_its_attachments_and_reads_one_by_number() -> None:
    inner = message_with(("prices.csv", "text/csv", b"item,price\nboard,42.50\n"))
    found = extract.extract("fwd.eml", "", inner)
    assert found.kind == "eml"
    assert "Subject: Spring prices" in found.text
    assert "The prices are attached." in found.text
    assert "1. prices.csv (text/csv" in found.text
    assert [(i.ref, i.name) for i in found.inside] == [("1", "prices.csv")]
    assert extract.extract("fwd.eml", "", inner, inside="1").text == "item,price\nboard,42.50\n"
    assert "board,42.50" in extract.extract("fwd.eml", "", inner, inside="prices.csv").text
    with pytest.raises(ToolError, match="no attachment 4"):
        extract.extract("fwd.eml", "", inner, inside="4")


def ole(entries: dict[str, bytes | dict[str, bytes]]) -> bytes:
    """A small Outlook-style compound file: streams, and storages of streams. Each stream is
    under 4 KB, so all of them sit in the mini stream, as Outlook keeps small ones."""
    sector, mini = 512, 64
    end, free, fat_mark, none = 0xFFFFFFFE, 0xFFFFFFFF, 0xFFFFFFFD, 0xFFFFFFFF
    flat: list[tuple[str, int, bytes, int]] = [("Root Entry", 5, b"", -1)]
    for name, value in entries.items():
        if isinstance(value, dict):
            flat.append((name, 1, b"", 0))
            parent = len(flat) - 1
            flat += [(child, 2, data, parent) for child, data in value.items()]
        else:
            flat.append((name, 2, value, 0))
    ministream, minifat = b"", b""
    starts: list[int] = []
    for _name, kind, stream, _parent in flat:
        if kind != 2 or not stream:
            starts.append(end)
            continue
        at, count = len(ministream) // mini, (len(stream) + mini - 1) // mini
        starts.append(at)
        ministream += stream.ljust(count * mini, b"\0")
        minifat += b"".join(struct.pack("<I", n) for n in [*range(at + 1, at + count), end])
    directory_sectors = (len(flat) * 128 + sector - 1) // sector
    minifat_at = 1 + directory_sectors
    data_at = minifat_at + 1
    data_count = (len(ministream) + sector - 1) // sector
    chains = [fat_mark, *range(2, minifat_at), end, end, *range(data_at + 1, data_at + data_count)]
    chains.append(end)
    fat = b"".join(struct.pack("<I", c) for c in chains).ljust(sector, b"\xff")
    children: dict[int, list[int]] = {}
    for index, item in enumerate(flat):
        if item[3] >= 0:
            children.setdefault(item[3], []).append(index)
    directory = b""
    for index, (name, kind, stream, _parent) in enumerate(flat):
        found = [c for c in children.values() if index in c]
        siblings = found[0] if found else []
        right = siblings[siblings.index(index) + 1] if index in siblings[:-1] else none
        child = children.get(index, [none])[0]
        start, size = (data_at, len(ministream)) if kind == 5 else (starts[index], len(stream))
        encoded = name.encode("utf-16-le") + b"\0\0"
        directory += (
            encoded.ljust(64, b"\0") + struct.pack("<HBB", len(encoded), kind, 1)
            + struct.pack("<III", none, right, child) + bytes(16 + 4 + 16)
            + struct.pack("<IQ", start, size)
        )  # fmt: skip
    header = (
        b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + bytes(16)
        + struct.pack("<HHHHH", 0x3E, 3, 0xFFFE, 9, 6) + bytes(6)
        + struct.pack("<IIIIIIIII", 0, 1, 1, 0, 4096, minifat_at, 1, end, 0)
        + struct.pack("<I", 0) + struct.pack("<I", free) * 108
    )  # fmt: skip
    return (
        header + fat + directory.ljust(directory_sectors * sector, b"\0")
        + minifat.ljust(sector, b"\xff") + ministream.ljust(data_count * sector, b"\0")
    )  # fmt: skip


def text_stream(value: str) -> bytes:
    return value.encode("utf-16-le")


def outlook_message() -> bytes:
    return ole({
        "__substg1.0_0037001F": text_stream("Spring prices"),
        "__substg1.0_0C1A001F": text_stream("Sam"),
        "__substg1.0_0C1F001F": text_stream("sam@supplier.example"),
        "__substg1.0_0E04001F": text_stream("Owner"),
        "__substg1.0_1000001F": text_stream("The prices are attached."),
        "__attach_version1.0_#00000000": {
            "__substg1.0_3707001F": text_stream("prices.csv"),
            "__substg1.0_37010102": b"item,price\nboard,42.50\n",
        },
    })  # fmt: skip


def test_an_outlook_message_lists_its_attachments_and_reads_one() -> None:
    found = extract.extract("fwd.msg", "application/octet-stream", outlook_message())
    assert found.kind == "outlook"
    assert "From: Sam <sam@supplier.example>" in found.text
    assert "Subject: Spring prices" in found.text
    assert "The prices are attached." in found.text
    assert "1. prices.csv (text/csv, 23 bytes)" in found.text
    inner = extract.extract("fwd.msg", "", outlook_message(), inside="1")
    assert inner.text == "item,price\nboard,42.50\n"
    with pytest.raises(ToolError, match="isn't an Outlook message"):
        extract.extract("fwd.msg", "", b"not one")


def zipped(members: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    return buffer.getvalue()


def test_a_zip_lists_its_files_and_reads_one_by_path() -> None:
    archive = zipped({"prices/list.csv": b"board,42.50\n", "readme.txt": b"hello"})
    found = extract.extract("files.zip", "application/x-zip-compressed", archive)
    assert found.kind == "zip"
    assert "prices/list.csv (12 bytes)" in found.text
    assert {i.ref for i in found.inside} == {"prices/list.csv", "readme.txt"}
    assert extract.extract("files.zip", "", archive, inside="prices/list.csv").text == (
        "board,42.50\n"
    )
    assert extract.extract("files.zip", "", archive, inside="readme.txt").text == "hello"
    with pytest.raises(ToolError, match=r"there's no nothing.txt"):
        extract.extract("files.zip", "", archive, inside="nothing.txt")
    with pytest.raises(ToolError, match="no files inside"):
        extract.extract("a.csv", "text/csv", b"a", inside="x")


def test_a_zip_bomb_isnt_unpacked() -> None:
    bomb = zipped({"zeros": bytes(5_000_000)})
    with pytest.raises(ToolError, match="unpacks to far more"):
        extract.extract("bomb.zip", "", bomb, inside="zeros")


@pytest.mark.parametrize(
    ("name", "says"),
    [
        ("prices.numbers", "a Numbers spreadsheet: export it as .xlsx or PDF"),
        ("letter.doc", "an old Word document: save it as .docx or PDF"),
        ("photo.heic", "export it as JPEG or PNG"),
        ("drawing.xyz", "A PDF, Word, Excel, PowerPoint, picture or text copy of it would work"),
    ],
)
def test_what_cant_be_read_says_what_it_is_and_what_would_work(name: str, says: str) -> None:
    with pytest.raises(ToolError, match=r"[Tt]ext can't be taken out") as caught:
        extract.extract(name, "application/octet-stream", b"\x00\x01")
    assert says in str(caught.value)


def test_a_short_outlook_file_is_never_taken_for_a_path(tmp_path: Path) -> None:
    """olefile reads a short bytes value as a file's path: it's only ever given the bytes."""
    here = tmp_path / "secret.msg"
    here.write_bytes(outlook_message())
    with pytest.raises(ToolError, match="isn't an Outlook message"):
        extract.extract("fwd.msg", "", str(here).encode())


# --- (e) a long file: its outline, then page by page ---


def test_a_long_pdf_starts_with_its_outline_and_is_read_on_page_by_page() -> None:
    found = extract.extract("long.pdf", "application/pdf", long_pdf())
    meta = {"name": "long.pdf"}
    first = files.text_answer(meta, found, 0, PAGE_CHARS, None, "view_attachment")
    outline = first["outline"]
    assert (outline["pages"], outline["pages_with_text"]) == (40, "1-40")
    assert "[Page 1 of 40]" in first["text"]
    assert "[Page 2 of 40]" in first["text"]
    assert "[Page 3 of 40]" not in first["text"]
    assert len(first["text"]) < files.OUTLINE_CHARS
    assert first["truncated"] is True
    assert first["pages_shown"] == "1-2"
    assert "pages=" in first["note"]
    seen = {1, 2}
    following = first["next_pages"]
    while following:
        part = files.text_answer(meta, found, 0, PAGE_CHARS, following, "view_attachment")
        start, _, end = following.partition("-")
        seen |= set(range(int(start), int(end or start) + 1))
        assert f"[Page {start} of 40]" in part["text"]
        following = part.get("next_pages")
    assert seen == set(range(1, 41))
    last = files.text_answer(meta, found, 0, PAGE_CHARS, "40", "view_attachment")
    assert "next_pages" not in last
    assert "Page 40 says" in last["text"]
    with pytest.raises(ToolError, match="it has 40 pages"):
        files.text_answer(meta, found, 0, PAGE_CHARS, "41-45", "view_attachment")


def test_a_short_file_comes_whole() -> None:
    found = extract.extract("prices.pdf", "application/pdf", pdf_with_picture())
    answer = files.text_answer({"name": "p.pdf"}, found, 0, PAGE_CHARS, None, "view_attachment")
    assert "outline" not in answer
    assert answer["truncated"] is False
    assert "Pages 1 have pictures: view_attachment shows a page as an image" in answer["pictures"]


@pytest.mark.usefixtures("mailbox")
async def test_read_attachment_takes_pages() -> None:
    first = await gmail.read_attachment.handler(gmail_target(), uid=8, attachment_id="1")
    assert first["outline"]["pages"] == 40
    part = await gmail.read_attachment.handler(
        gmail_target(), uid=8, attachment_id="1", pages="39-40"
    )
    assert "Page 39 says" in part["text"]
    assert "Page 40 says" in part["text"]
    assert part["pages_shown"] == "39-40"


def test_a_long_text_without_pages_starts_with_its_beginning() -> None:
    found = extract.Extracted("y" * 100_000, "text")
    answer = files.text_answer({"name": "a.txt"}, found, 0, PAGE_CHARS, None, "view_file")
    assert answer["returned_chars"] == files.OUTLINE_CHARS
    assert answer["next_offset"] == files.OUTLINE_CHARS
    assert "first 30,000 of 100,000 characters" in answer["note"]
    whole = files.text_answer({"name": "a.txt"}, found, 0, 100_000, None, "view_file")
    assert whole["returned_chars"] == 100_000


def test_the_worker_is_told_how_to_read_a_long_file() -> None:
    from ringkeeper_worker.agent import HEADLESS_NOTE  # noqa: PLC0415

    note = " ".join(HEADLESS_NOTE.split())
    assert "starts with its outline" in note
    assert "pages=" in note
    assert "view tool" in note


def test_timing_stays_within_the_budget(ocr: list[bytes]) -> None:
    started = time.monotonic()
    extract.extract("long.pdf", "application/pdf", long_pdf(pages=60, chars=100))
    assert time.monotonic() - started < 30
    assert not ocr  # no pictures, nothing to read


def test_tool_answers_never_hold_bytes_as_text() -> None:
    found = files.picture_answer(
        {"name": "a.png"},
        extract.Shown(Image(b"\x89PNG", "image/png", 1, 1), "a.png", "image/png", {}),
    )
    image = found.pop(IMAGE)
    assert isinstance(image, Image)
    assert json.dumps(found)  # the rest is plain JSON


# --- every place that reads a file ---


def microsoft_target(project: str | None = None) -> Target:
    fields = {"vendor": "microsoft", "access_token": "graph-token"}
    return Target("t", Env.DEV, None, Secret("r", json.dumps(fields)), project=project)


async def test_a_onedrive_file_is_read_shown_and_saved_through_its_signed_link(
    monkeypatch: pytest.MonkeyPatch, database_url: str, tmp_path: Path
) -> None:
    del database_url
    seen: list[httpx2.Request] = []

    def graph(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        path = request.url.path
        if path.endswith("/drives/d1/items/i1"):
            return httpx2.Response(200, json={
                "id": "i1", "name": "prices.pdf", "size": 900,
                "file": {"mimeType": "application/pdf"},
            })  # fmt: skip
        if path.endswith("/drives/d1/items/i1/content"):
            return httpx2.Response(302, headers={"Location": "https://files.example/signed?x=1"})
        if request.url.host == "files.example":
            return httpx2.Response(200, content=pdf_with_picture())
        return httpx2.Response(404, json={"error": {"message": path}})

    monkeypatch.setattr(vendor, "transport", httpx2.MockTransport(graph))
    where = {"drive_id": "d1", "item_id": "i1"}

    async def run(operation: str, target: Target | None = None, **query: Any) -> Any:
        return await vendor.call_operation.handler(
            target or microsoft_target(), operation=f"microsoft.{operation}", path=where,
            query=query,
        )  # fmt: skip

    text = await run("read_file")
    assert "Prices below" in text["text"]
    assert text["name"] == "prices.pdf"
    assert "microsoft.view_file" in text["pictures"]
    signed = [r for r in seen if r.url.host == "files.example"]
    assert signed
    assert "authorization" not in signed[-1].headers  # the link is all it takes
    shown = await run("view_file", page="1")
    assert isinstance(shown[IMAGE], Image)
    assert shown["page"] == 1
    folder = tmp_path / "acme-web"
    folder.mkdir()
    with session_scope() as session:
        make_project(session, "acme-web", workspace_path=str(folder))
    saved = await run("save_file", microsoft_target("acme-web"))
    assert saved["saved_to"] == "attachments/prices.pdf"
    assert (folder / "attachments" / "prices.pdf").read_bytes() == pdf_with_picture()
    ops = {op.name: op for op in MICROSOFT.ops}
    assert all(ops[n].level == "read" for n in ("read_file", "view_file", "save_file"))
    assert ops["view_file"].audited
    assert ops["save_attachment"].audited
    assert not ops["read_file"].audited
    logged = vendor.call_operation.logged
    assert logged is not None
    assert logged(microsoft_target(), {"operation": "microsoft.view_file"})
    assert not logged(microsoft_target(), {"operation": "microsoft.read_file"})


async def test_a_microsoft_attachment_is_shown_too(monkeypatch: pytest.MonkeyPatch) -> None:
    def graph(_request: httpx2.Request) -> httpx2.Response:
        data = base64.b64encode(pdf_with_picture()).decode()
        return httpx2.Response(200, json={
            "name": "prices.pdf", "contentType": "application/pdf", "contentBytes": data,
        })  # fmt: skip

    monkeypatch.setattr(vendor, "transport", httpx2.MockTransport(graph))
    shown = await vendor.call_operation.handler(
        microsoft_target(), operation="microsoft.view_attachment",
        path={"message_id": "m1", "attachment_id": "a1"}, query={"page": "1", "picture": "1"},
    )  # fmt: skip
    assert shown["picture"] == 1
    assert shown[IMAGE].mime == "image/png"


def google_target(project: str | None = None) -> Target:
    secret = Secret("r", json.dumps({"access_token": "at"}))
    access = {"access": ["mail_read", "drive_read_all"]}
    return Target("t", Env.DEV, None, secret, access, project=project)


async def test_a_google_doc_is_shown_as_its_pdf_and_saved_as_word(
    monkeypatch: pytest.MonkeyPatch, database_url: str, tmp_path: Path
) -> None:
    del database_url
    asked: list[str] = []

    def drive(request: httpx2.Request) -> httpx2.Response:
        if "/export" in request.url.path:
            asked.append(request.url.params["mimeType"])
            body = pdf_with_picture() if asked[-1] == extract.PDF else b"PK word"
            return httpx2.Response(200, content=body)
        doc = "application/vnd.google-apps.document"
        return httpx2.Response(200, json={"id": "doc123", "name": "Price list", "mimeType": doc})

    monkeypatch.setattr(google, "transport", httpx2.MockTransport(drive))
    shown = await google.view_file.handler(google_target(), file_id="doc123")
    assert isinstance(shown[IMAGE], Image)
    folder = tmp_path / "acme-web"
    folder.mkdir()
    with session_scope() as session:
        make_project(session, "acme-web", workspace_path=str(folder))
    saved = await google.save_file.handler(google_target("acme-web"), file_id="doc123")
    assert saved["saved_to"] == "attachments/Price list.docx"
    assert asked == [extract.PDF, extract.WORD]


async def test_storage_reads_a_pdf_s_text_and_shows_its_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def s3(request: httpx2.Request) -> httpx2.Response:
        if request.url.path.endswith("/notes.bin"):
            return httpx2.Response(200, content=b"\x00\x01\xff")
        return httpx2.Response(200, content=pdf_with_picture(), headers={"content-type": "x/y"})

    monkeypatch.setattr(storage, "transport", httpx2.MockTransport(s3))
    secret = json.dumps({"provider": "aws", "access_key": "AKIDEXAMPLE", "secret_key": "s"})
    target = Target("t", Env.DEV, None, Secret("r", secret))
    found = await storage.read_file.handler(target, key="docs/prices.pdf", bucket="acme")
    assert "Prices below" in found["text"]
    shown = await storage.view_file.handler(target, key="docs/prices.pdf", bucket="acme")
    assert isinstance(shown[IMAGE], Image)
    raw = await storage.read_file.handler(target, key="notes.bin", bucket="acme")
    assert raw["base64"] == base64.b64encode(b"\x00\x01\xff").decode()


# --- (e) what each worker reads about the tools ---


def test_a_project_lists_only_the_tools_its_connections_can_use(database_url: str) -> None:
    del database_url
    with session_scope() as session:
        make_project(session, "acme-web")
        make_instance(
            session, "acme-google", type=InstanceType.GOOGLE, config={"access": ["mail_read"]}
        )
        make_instance(session, "acme-db")
    bind("acme-web", "acme-google")
    bind("acme-web", "acme-db", allowed_tools=["query", "list_tables"])
    caller = Caller("acme-web", None, "token-1")
    listed = calls.usable_tools(caller, ADAPTERS[InstanceType.GOOGLE])
    assert listed is not None
    assert {"read_mail", "read_attachment", "view_attachment", "save_attachment"} <= listed
    assert not {"youtube_videos", "read_file", "list_events"} & listed
    assert calls.usable_tools(caller, ADAPTERS[InstanceType.SUPABASE]) == {"query", "list_tables"}
    assert calls.usable_tools(caller, ADAPTERS[InstanceType.GITHUB]) is None  # none of its own
    assert calls.usable_tools(Caller(None, None, None), ADAPTERS[InstanceType.GOOGLE]) is None


@pytest.mark.usefixtures("mailbox")
async def test_check_my_setup_never_saves_a_copy(
    database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    del database_url
    ref = "file://ringkeeper/connections/acme-mail"
    get_vault().store(ref, GMAIL_SECRET)
    with session_scope() as session:
        make_project(session, "acme-web")
        make_instance(session, "acme-mail", type=InstanceType.GMAIL, secret_ref=ref)
    bind("acme-web", "acme-mail", is_default=True)

    def from_check(_session: object, session_id: str | None) -> bool:
        return session_id is not None

    monkeypatch.setattr(calls.setup_check, "session_from_check", from_check)
    me = Caller("acme-web", "session-1", "token-1")
    which = {"uid": 7, "attachment_id": "1"}
    save = ToolCall(me, InstanceType.GMAIL, "save_attachment", None, which)
    with pytest.raises(calls.RefusalError, match="Check my setup changes nothing"):
        await calls.run_call(save, get_vault())
    view = ToolCall(me, InstanceType.GMAIL, "view_attachment", None, which)
    assert isinstance((await calls.run_call(view, get_vault()))[IMAGE], Image)
