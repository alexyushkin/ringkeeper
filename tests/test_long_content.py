# pyright: reportPrivateUsage=false, reportUnknownLambdaType=false
# pyright: reportUnknownArgumentType=false
"""Long things read whole: mail, attachments, threads, files, SQL rows and any long result.

An agent must never take part of something for all of it. So every answer that holds part says
how much there is and where the next part starts, and following that gets everything back.
"""

import base64
import io
import json
import sqlite3
import time
import zipfile
from email.message import EmailMessage
from pathlib import Path
from typing import Any, cast

import httpx2
import pytest

from ringkeeper.adapters import (
    ADAPTERS,
    extract,
    github,
    gmail,
    google,
    http_api,
    mail_text,
    mailbox,
    oracle,
    sql_paging,
    sqlite_file,
    supabase,
    vendor,
)
from ringkeeper.adapters.base import Target, ToolError
from ringkeeper.adapters.paging import PAGE_CHARS, page
from ringkeeper.adapters.vendors.microsoft import MICROSOFT
from ringkeeper.calls import Caller, ToolCall
from ringkeeper.db import session_scope
from ringkeeper.gateway.app import build_server
from ringkeeper.models import Env, InstanceType
from ringkeeper.services import continuations
from ringkeeper.services.calls import run_call
from ringkeeper.vault import Secret, Vault, get_vault
from ringkeeper.workers.env import FIXED_ENV
from ringkeeper_worker.agent import HEADLESS_NOTE
from tests.fake_services import FakeImap
from tests.helpers import bind, make_instance, make_project

pytestmark = pytest.mark.anyio
GMAIL_SECRET = json.dumps({"address": "owner@gmail.example", "app_password": "abcdefghijklmnop"})
ME = Caller("acme-web", "session-1", "token-1")
SOMEONE_ELSE = Caller("other-web", "session-2", "token-2")


def pdf_of(*pages: str) -> bytes:
    """A small real PDF, one line of text on each page."""
    objects: list[bytes] = []
    kids = " ".join(f"{4 + 2 * i} 0 R" for i in range(len(pages)))
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objects.append(f"<< /Type /Pages /Count {len(pages)} /Kids [{kids}] >>".encode())
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    for number, text in enumerate(pages):
        content = f"BT /F1 12 Tf 40 700 Td ({text}) Tj ET".encode()
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {5 + 2 * number} 0 R "
            "/Resources << /Font << /F1 3 0 R >> >> >>".encode()
        )
        objects.append(b"<< /Length %d >>\nstream\n%s\nendstream" % (len(content), content))
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


def long_html(chars: int = 60_000) -> str:
    """An email of about `chars` characters of text, every paragraph numbered."""
    paragraphs: list[str] = []
    total = 0
    number = 0
    while total < chars:
        number += 1
        line = f"Paragraph {number:05d} of the long contract text goes on for a while here."
        paragraphs.append(f"<p>{line}</p>")
        total += len(line) + 2
    table = (
        "<table><tr><th>Item</th><th>Price</th></tr><tr><td>Windows</td><td>$10</td></tr></table>"
    )
    link = '<p>See <a href="https://example.com/terms">the terms</a>.</p>'
    hidden = "<style>p {color: red}</style><script>alert('no')</script>"
    return f"<html><head>{hidden}</head><body>{link}{table}{''.join(paragraphs)}</body></html>"


def long_email() -> bytes:
    message = EmailMessage()
    message["From"] = "Anna <anna@acme.example>"
    message["To"] = "owner@example.com"
    message["Subject"] = "The contract"
    message["Date"] = "Mon, 26 Oct 2026 09:00:00 +0000"
    message["Message-ID"] = "<m1@acme.example>"
    message.set_content(long_html(), subtype="html")
    message.add_attachment(
        pdf_of("Page one of the terms", "Page two of the terms"),
        maintype="application", subtype="pdf", filename="terms.pdf",
    )  # fmt: skip
    return message.as_bytes()


# --- paging ---


def test_a_whole_thing_says_it_is_whole() -> None:
    assert page("hello") == {
        "text": "hello", "total_chars": 5, "offset": 0, "returned_chars": 5, "truncated": False,
        "next_offset": None,
    }  # fmt: skip


def test_a_part_says_what_part_it_is() -> None:
    first = page("0123456789", 0, 4)
    assert (first["text"], first["next_offset"], first["truncated"]) == ("0123", 4, True)
    assert "only part" in first["note"]
    last = page("0123456789", 8, 4)
    assert (last["text"], last["next_offset"]) == ("89", None)
    assert last["truncated"] is True  # the start of it wasn't in this answer
    past = page("0123456789", 50, 4)
    assert (past["text"], past["returned_chars"], past["next_offset"]) == ("", 0, None)


# --- text from HTML, and quoted chains ---


def test_html_keeps_links_and_tables_and_loses_the_rest() -> None:
    text = mail_text.html_to_text(
        "<html><head><title>x</title><style>p{}</style></head><body>"
        '<h1>Order</h1><p>See <a href="https://example.com/a?b=1">the order</a> now.</p>'
        "<table><tr><th>Item</th><th>Qty</th></tr><tr><td>Door</td><td>2</td></tr></table>"
        "<ul><li>One</li><li>Two</li></ul><script>evil()</script>"
        "<p>Tom &amp; Jerry<br>next line</p></body></html>"
    )
    assert "See the order (https://example.com/a?b=1) now." in text
    assert "Item | Qty\nDoor | 2" in text
    assert "- One\n- Two" in text
    assert "Tom & Jerry\nnext line" in text
    assert "evil" not in text
    assert "p{}" not in text


def test_a_link_shown_as_its_own_address_isnt_repeated() -> None:
    html = '<a href="https://example.com/x">https://example.com/x</a>'
    assert mail_text.html_to_text(html) == "https://example.com/x"
    assert mail_text.html_to_text('<a href="javascript:evil()">click</a>') == "click"


def test_a_plain_stub_gives_way_to_the_html() -> None:
    html = "<p>" + "The whole message, with everything it says. " * 10 + "</p>"
    assert "everything it says" in mail_text.best_text("View this in your browser", html)
    assert mail_text.best_text("A real plain version.", "<p>A real plain version.</p>") == (
        "A real plain version."
    )


REPLY = """Yes, Tuesday works.

Thanks,
Anna

On Mon, Oct 26, 2026 at 9:00 AM Owner <owner@example.com> wrote:
> Does Tuesday work?
> > And before that
"""
OUTLOOK = """Fine by me.

________________________________
From: Owner <owner@example.com>
Sent: Monday, October 26, 2026 9:00 AM
To: Anna
Subject: Tuesday

Does Tuesday work?
"""
FORWARD = """FYI

---------- Forwarded message ---------
From: Sam <sam@example.com>
Date: Mon, Oct 26, 2026
Subject: The numbers

Here are the numbers.
"""
BETWEEN = """> Does Tuesday work?
Yes.
> And the price?
It's fine.
"""


def test_a_reply_chain_is_set_apart() -> None:
    new, quoted = mail_text.split_quoted(REPLY)
    assert new == "Yes, Tuesday works.\n\nThanks,\nAnna"
    assert quoted.startswith("On Mon")
    assert "And before that" in quoted
    new, quoted = mail_text.split_quoted(OUTLOOK)
    assert new == "Fine by me."
    assert "Does Tuesday work?" in quoted


def test_what_isnt_a_chain_stays_in_the_message() -> None:
    assert mail_text.split_quoted(FORWARD) == (FORWARD.strip(), "")  # a forward is the message
    assert mail_text.split_quoted(BETWEEN) == (BETWEEN.strip(), "")  # a reply between the lines
    only = "> Nothing but a quote\n> here"
    assert mail_text.split_quoted(only) == (only, "")


def test_the_chain_comes_back_when_asked_for() -> None:
    apart = mail_text.body_fields(REPLY)
    assert "Does Tuesday" not in apart["body"]
    assert "Does Tuesday" in apart["quoted"]
    assert apart["quoted_chars"] == len(apart["quoted"])
    assert apart["quoted_truncated"] is False
    whole = mail_text.body_fields(REPLY, include_quoted=True)
    assert "Does Tuesday" in whole["body"]
    assert "quoted" not in whole


def test_a_very_long_chain_says_its_cut_short() -> None:
    chain = "Answer.\n\nOn Monday someone wrote:\n" + "> old words\n" * 5000
    found = mail_text.body_fields(chain)
    assert len(found["quoted"]) == mail_text.QUOTED_SHOWN
    assert found["quoted_truncated"] is True
    assert found["quoted_chars"] > mail_text.QUOTED_SHOWN


# --- attachments ---


def zipped(**members: str) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        for name, text in members.items():
            archive.writestr(name.replace("__", "/").replace("_DOT_", "."), text)
    return out.getvalue()


W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
DOCX = zipped(
    word__document_DOT_xml=f"""<w:document {W}><w:body>
<w:p><w:r><w:t>First paragraph</w:t></w:r></w:p>
<w:tbl><w:tr><w:tc><w:p><w:r><w:t>Cell A</w:t></w:r></w:p></w:tc>
<w:tc><w:p><w:r><w:t>Cell B</w:t></w:r></w:p></w:tc></w:tr></w:tbl>
</w:body></w:document>"""
)
S = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
R = 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
XLSX = zipped(
    xl__workbook_DOT_xml=f'<workbook {S} {R}><sheets><sheet name="Prices" sheetId="1" r:id="rId1"/>'
    "</sheets></workbook>",
    xl___rels__workbook_DOT_xml_DOT_rels='<Relationships xmlns="http://schemas.openxmlformats.org/'
    'package/2006/relationships"><Relationship Id="rId1" Target="worksheets/sheet1.xml"/>'
    "</Relationships>",
    xl__sharedStrings_DOT_xml=f"<sst {S}><si><t>Door</t></si></sst>",
    xl__worksheets__sheet1_DOT_xml=f'<worksheet {S}><sheetData><row r="1"><c r="A1" t="s"><v>0</v>'
    '</c><c r="C1"><v>12.5</v></c></row></sheetData></worksheet>',
)


def test_text_comes_out_of_each_kind_of_file() -> None:
    pdf = extract.extract("a.pdf", "application/pdf", pdf_of("Hello page", "Second page"))
    assert "Hello page" in pdf.text
    assert "Second page" in pdf.text
    assert "[Page 2 of 2]" in pdf.text
    word = extract.extract("a.docx", "application/octet-stream", DOCX)
    assert "First paragraph" in word.text
    assert "Cell A | Cell B" in word.text
    sheet = extract.extract("a.xlsx", "application/octet-stream", XLSX)
    assert sheet.text == "# Prices\nDoor,,12.5"
    table = extract.extract("a.csv", "text/csv", b"a,b\n1,2\n")
    assert table.text == "a,b\n1,2\n"
    assert extract.extract("a.txt", "text/plain", "caf\xe9".encode("latin-1")).text == "caf\xe9"


def test_a_picture_says_what_it_needs_when_ocr_isnt_there(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(extract.shutil, "which", lambda _name: None)
    found = extract.extract("scan.png", "image/png", b"\x89PNG")
    assert found.text == ""
    assert found.note
    assert "tesseract" in found.note


def test_a_picture_is_read_when_ocr_is_there(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(extract.shutil, "which", lambda _name: "tesseract")

    class Done:
        returncode = 0
        stdout = b"Invoice 42"

    monkeypatch.setattr(extract.subprocess, "run", lambda *_a, **_k: Done())
    assert extract.extract("scan.png", "image/png", b"\x89PNG").text == "Invoice 42"


def test_a_scanned_pdf_says_some_pages_couldnt_be_read(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(extract.shutil, "which", lambda _name: None)
    found = extract.extract("scan.pdf", "application/pdf", pdf_of("Text here", ""))
    assert found.note
    assert "1 of 2 pages" in found.note
    assert "tesseract" in found.note


@pytest.mark.parametrize(
    ("name", "mime", "data", "says"),
    [
        ("a.exe", "application/x-msdownload", b"MZ", "text can't be taken out"),
        ("a.docx", "application/octet-stream", b"not a zip", "damaged"),
        ("a.pdf", "application/pdf", b"not a pdf", "damaged"),
        ("a.docx", "application/octet-stream", zipped(other="x"), "isn't a Word document"),
    ],
)
def test_what_cant_be_read_says_why(name: str, mime: str, data: bytes, says: str) -> None:
    with pytest.raises(ToolError, match=says):
        extract.extract(name, mime, data)


def test_a_file_built_to_do_harm_isnt_read(monkeypatch: pytest.MonkeyPatch) -> None:
    entity = '<!DOCTYPE x [<!ENTITY a "aaa">]><w:document/>'
    with pytest.raises(ToolError, match="XML it has no reason"):
        extract.extract("a.docx", "", zipped(word__document_DOT_xml=entity))
    monkeypatch.setattr(extract, "MAX_UNPACKED_BYTES", 10)
    with pytest.raises(ToolError, match="unpacks to far more"):
        extract.extract("a.docx", "", DOCX)
    monkeypatch.setattr(extract, "MAX_FILE_BYTES", 1)
    with pytest.raises(ToolError, match="too large"):
        extract.extract("a.txt", "text/plain", b"abc")


# --- the 60k email, on each mail connection ---


def imap_target() -> Target:
    return Target("t", Env.DEV, None, Secret("r", GMAIL_SECRET))


def mailbox_target() -> Target:
    fields = {
        "address": "owner@example.com", "password": "abcdefghijklmnop",
        "imap_host": "imap.example.com", "imap_port": 993,
        "smtp_host": "smtp.example.com", "smtp_port": 587,
    }  # fmt: skip
    return Target("t", Env.DEV, None, Secret("r", json.dumps(fields)))


@pytest.fixture
def imap(monkeypatch: pytest.MonkeyPatch) -> FakeImap:
    fake = FakeImap(messages={7: long_email()})
    monkeypatch.setattr(gmail, "imap_factory", lambda: fake)
    return fake


async def test_a_long_email_comes_whole_by_default(imap: FakeImap) -> None:
    found = await gmail.get_message.handler(imap_target(), uid=7)
    assert found["truncated"] is False
    assert found["next_offset"] is None
    assert found["total_chars"] == len(found["body"]) > 60_000
    assert "the terms (https://example.com/terms)" in found["body"]
    assert "Item | Price\nWindows | $10" in found["body"]
    assert "alert" not in found["body"]
    assert found["attachments"] == [
        {
            "id": "1",
            "name": "terms.pdf",
            "type": "application/pdf",
            "size": found["attachments"][0]["size"],
        }
    ]
    assert found["attachments"][0]["size"] > 100


async def test_a_long_email_and_its_pdf_read_fully_across_pages(imap: FakeImap) -> None:
    whole = await gmail.get_message.handler(imap_target(), uid=7)
    parts: list[dict[str, Any]] = []
    offset: int | None = 0
    while offset is not None:
        part = await gmail.get_message.handler(imap_target(), uid=7, offset=offset, limit=25_000)
        parts.append(part)
        offset = part["next_offset"]
    assert len(parts) == 3
    assert parts[0]["truncated"] is True
    assert "only part" in parts[0]["note"]
    assert "".join(p["body"] for p in parts) == whole["body"]
    assert parts[-1]["body"].rstrip().endswith("goes on for a while here.")

    first = await gmail.read_attachment.handler(imap_target(), uid=7, attachment_id="1", limit=30)
    assert first["name"] == "terms.pdf"
    assert first["next_offset"] == 30
    text = first["text"]
    offset = first["next_offset"]
    while offset is not None:
        part = await gmail.read_attachment.handler(
            imap_target(), uid=7, attachment_id="1", offset=offset, limit=30
        )
        text += part["text"]
        offset = part["next_offset"]
    assert len(text) == first["total_chars"]
    assert "Page one of the terms" in text
    assert "Page two of the terms" in text
    with pytest.raises(ToolError, match="no attachment 9"):
        await gmail.read_attachment.handler(imap_target(), uid=7, attachment_id="9")


async def test_the_mailbox_connection_reads_the_same_way(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeImap(messages={7: long_email()})
    monkeypatch.setattr(mailbox, "imap_factory", lambda *_a, **_k: fake)
    monkeypatch.setattr(mailbox, "_imap", lambda _t: (fake, {}))
    target = mailbox_target()
    part = await mailbox.get_message.handler(target, uid=7, limit=25_000)
    assert part["truncated"] is True
    assert part["next_offset"] == 25_000
    rest = await mailbox.get_message.handler(target, uid=7, offset=25_000)
    assert rest["next_offset"] is None
    assert len(part["body"]) + len(rest["body"]) == part["total_chars"]
    pdf = await mailbox.read_attachment.handler(target, uid=7, attachment_id="1")
    assert "Page two of the terms" in pdf["text"]


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


class FakeGmailApi:
    """A long HTML message with a PDF, the way Gmail's API sends it."""

    def __init__(self) -> None:
        self.reads = 0

    def message(self, ident: str = "m1abcd", when: str = "1000") -> dict[str, Any]:
        self.reads += 1
        return {
            "id": ident, "threadId": "t1abcd", "internalDate": when,
            "payload": {
                "mimeType": "multipart/mixed",
                "headers": [{"name": "Subject", "value": f"About {ident}"}],
                "parts": [
                    {
                        "partId": "0", "mimeType": "text/html",
                        "body": {"data": b64(long_html().encode())},
                    },
                    {
                        "partId": "1", "mimeType": "application/pdf", "filename": "terms.pdf",
                        # Gmail changes this on every read: only the part's place is stable.
                        "body": {"size": 900, "attachmentId": f"changing-{self.reads}"},
                    },
                ],
            },
        }  # fmt: skip

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        path = request.url.path
        if "/attachments/" in path:
            assert path.endswith(f"/attachments/changing-{self.reads}")
            return httpx2.Response(200, json={"data": b64(pdf_of("Page one of the terms"))})
        if "/threads/" in path:
            messages = [self.message("m2abcd", "2000"), self.message("m1abcd", "1000")]
            return httpx2.Response(200, json={"id": "t1abcd", "messages": messages})
        if "/messages/" in path:
            return httpx2.Response(200, json=self.message())
        if path.endswith("/files/f1abcd") and "alt" not in request.url.params:
            mime = "application/pdf"
            return httpx2.Response(200, json={"id": "f1abcd", "name": "a.pdf", "mimeType": mime})
        if path.endswith("/files/f1abcd"):
            return httpx2.Response(200, content=pdf_of("Drive page"))
        return httpx2.Response(404, json={"error": {"message": path}})


@pytest.fixture
def gmail_api(monkeypatch: pytest.MonkeyPatch) -> FakeGmailApi:
    fake = FakeGmailApi()
    monkeypatch.setattr(google, "transport", httpx2.MockTransport(fake.handle))
    return fake


def google_target() -> Target:
    secret = Secret("r", json.dumps({"access_token": "at"}))
    return Target("t", Env.DEV, None, secret, {"access": ["mail_read", "drive_read_all"]})


async def test_a_google_account_reads_a_long_email_and_its_pdf(gmail_api: FakeGmailApi) -> None:
    target = google_target()
    whole = await google.read_mail.handler(target, message_id="m1abcd")
    assert whole["total_chars"] > 60_000
    assert whole["truncated"] is False
    assert whole["attachments"] == [
        {"id": "1", "name": "terms.pdf", "type": "application/pdf", "size": 900}
    ]
    first = await google.read_mail.handler(target, message_id="m1abcd", limit=40_000)
    rest = await google.read_mail.handler(target, message_id="m1abcd", offset=first["next_offset"])
    assert first["body"] + rest["body"] == whole["body"]
    pdf = await google.read_attachment.handler(target, message_id="m1abcd", attachment_id="1")
    assert "Page one of the terms" in pdf["text"]
    assert pdf["name"] == "terms.pdf"
    assert "id" not in pdf
    with pytest.raises(ToolError, match="no attachment 7"):
        await google.read_attachment.handler(target, message_id="m1abcd", attachment_id="7")


async def test_a_drive_pdf_is_read_too(gmail_api: FakeGmailApi) -> None:
    found = await google.read_file.handler(google_target(), file_id="f1abcd")
    assert "Drive page" in found["text"]
    assert found["truncated"] is False


async def test_a_google_thread_runs_oldest_to_newest_with_each_message_in_parts(
    gmail_api: FakeGmailApi,
) -> None:
    found = await google.get_thread.handler(google_target(), thread_id="t1abcd", body_chars=500)
    assert [m["id"] for m in found["messages"]] == ["m1abcd", "m2abcd"]
    assert found["total_messages"] == 2
    assert found["next_offset"] is None
    first = found["messages"][0]
    assert len(first["body"]) == 500
    assert first["total_chars"] > 60_000
    assert first["next_offset"] == 500
    assert first["truncated"] is True
    one = await google.get_thread.handler(google_target(), thread_id="t1abcd", limit=1)
    assert one["returned_messages"] == 1
    assert one["next_offset"] == 1
    assert one["truncated"] is True
    assert "More messages" in one["note"]


def thread_message(ident: str, day: int, references: str = "") -> bytes:
    message = EmailMessage()
    message["From"] = "anna@acme.example"
    message["Subject"] = "Tuesday"
    message["Date"] = f"Mon, {day} Oct 2026 09:00:00 +0000"
    message["Message-ID"] = ident
    if references:
        message["References"] = references
    message.set_content(f"Message from day {day}.")
    return message.as_bytes()


async def test_an_imap_thread_runs_oldest_to_newest(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeImap(messages={
        3: thread_message("<a@x>", 20),
        1: thread_message("<c@x>", 22, "<a@x> <b@x>"),
        2: thread_message("<b@x>", 21, "<a@x>"),
    })  # fmt: skip
    monkeypatch.setattr(gmail, "imap_factory", lambda: fake)
    found = await gmail.get_thread.handler(imap_target(), uid=1)
    assert [m["body"] for m in found["messages"]] == [
        "Message from day 20.", "Message from day 21.", "Message from day 22.",
    ]  # fmt: skip
    assert found["total_messages"] == 3


def microsoft_target() -> Target:
    fields = {"vendor": "microsoft", "access_token": "t"}
    return Target("t", Env.DEV, None, Secret("r", json.dumps(fields)))


class FakeGraph:
    def __init__(self) -> None:
        self.requests: list[httpx2.Request] = []

    def message(self, ident: str = "m1", when: str = "2026-10-26T09:00:00Z") -> dict[str, Any]:
        return {
            "id": ident, "subject": "The contract", "conversationId": "c1",
            "receivedDateTime": when, "bodyPreview": "start",
            "body": {"contentType": "html", "content": long_html()},
            "attachments": [{"id": "a1", "name": "terms.pdf", "contentType": "application/pdf",
                             "size": 900}],
        }  # fmt: skip

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        path = request.url.path
        if path.endswith("/attachments/a1"):
            data = base64.b64encode(pdf_of("Page one of the terms")).decode()
            return httpx2.Response(200, json={
                "name": "terms.pdf", "contentType": "application/pdf", "contentBytes": data,
            })  # fmt: skip
        if path.endswith("/attachments"):
            row = {"id": "a1", "name": "x.pdf", "contentType": "application/pdf", "size": 9}
            return httpx2.Response(200, json={"value": [row | {"contentBytes": "AAAA"}]})
        if path.endswith("/messages"):
            later = self.message("m2", "2026-10-27T09:00:00Z")
            return httpx2.Response(200, json={"value": [later, self.message()]})
        return httpx2.Response(200, json=self.message())


@pytest.fixture
def graph(monkeypatch: pytest.MonkeyPatch) -> FakeGraph:
    fake = FakeGraph()
    monkeypatch.setattr(vendor, "transport", httpx2.MockTransport(fake.handle))
    return fake


async def run_graph(operation: str, path: dict[str, Any] | None = None, **query: Any) -> Any:
    return await vendor.call_operation.handler(
        microsoft_target(), operation=f"microsoft.{operation}", path=path, query=query
    )


async def test_a_microsoft_account_reads_a_long_email_and_its_pdf(graph: FakeGraph) -> None:
    where = {"message_id": "m1"}
    whole = await run_graph("get_message", where)
    assert whole["total_chars"] > 60_000
    assert whole["truncated"] is False
    assert "the terms (https://example.com/terms)" in whole["body"]
    assert "bodyPreview" not in whole
    assert whole["attachments"] == [
        {"id": "a1", "name": "terms.pdf", "type": "application/pdf", "size": 900}
    ]
    sent = graph.requests[-1].url.params
    assert "attachments" in sent["$expand"]
    assert "offset" not in sent
    assert "limit" not in sent
    first = await run_graph("get_message", where, limit=40_000)
    rest = await run_graph("get_message", where, offset=first["next_offset"])
    assert first["body"] + rest["body"] == whole["body"]
    pdf = await run_graph("read_attachment", where | {"attachment_id": "a1"})
    assert "Page one of the terms" in pdf["text"]


async def test_a_microsoft_thread_runs_oldest_to_newest(graph: FakeGraph) -> None:
    found = await run_graph("get_thread", conversation_id="c'1", body_chars=100)
    assert [m["id"] for m in found["messages"]] == ["m1", "m2"]
    assert found["messages"][0]["total_chars"] > 60_000
    assert found["messages"][0]["next_offset"] == 100
    assert graph.requests[-1].url.params["$filter"] == "conversationId eq 'c''1'"
    with pytest.raises(ToolError, match="name the conversation"):
        await run_graph("get_thread")


async def test_listing_attachments_never_carries_their_content(graph: FakeGraph) -> None:
    found = await run_graph("list_attachments", {"message_id": "m1"})
    assert found == {
        "attachments": [{"id": "a1", "name": "x.pdf", "type": "application/pdf", "size": 9}]
    }
    assert graph.requests[-1].url.params["$select"] == "id,name,contentType,size"


def test_microsofts_reads_are_still_reads_and_its_snapshots_see_the_raw_message() -> None:
    ops = {op.name: op for op in MICROSOFT.ops}
    for name in ("get_message", "get_thread", "read_attachment", "list_attachments"):
        assert ops[name].level == "read"
    assert set(ops["get_message"].local) == {"offset", "limit", "include_quoted"}
    assert "offset" in ops["get_message"].params  # list_operations shows it


# --- SQL ---


@pytest.fixture
def demo(tmp_path: Path) -> Path:
    path = tmp_path / "demo.db"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE t (n INTEGER)")
        conn.executemany("INSERT INTO t VALUES (?)", [(i,) for i in range(450)])
    conn.close()
    return path


def test_a_truncated_sql_result_pages_correctly(demo: Path) -> None:
    seen: list[int] = []
    offset: int | None = 0
    calls = 0
    while offset is not None:
        found = sqlite_file.run(
            demo, "SELECT n FROM t ORDER BY n", None, read_only=True, max_rows=200, offset=offset
        )
        calls += 1
        assert found["total_rows"] == 450
        assert found["offset"] == offset
        seen += [row[0] for row in found["rows"]]
        offset = found["next_offset"]
        if offset is not None:
            assert found["truncated"] is True
            assert "offset=next_offset" in found["note"]
    assert calls == 3
    assert seen == list(range(450))


def test_a_short_sql_result_says_its_all_there(demo: Path) -> None:
    found = sqlite_file.run(demo, "SELECT n FROM t LIMIT 3", None, read_only=True, max_rows=200)
    assert (found["truncated"], found["next_offset"], found["total_rows"]) == (False, None, 3)


def test_a_write_never_says_to_run_it_again(demo: Path) -> None:
    found = sqlite_file.run(
        demo, "UPDATE t SET n = n + 1 RETURNING n", None, read_only=False, max_rows=10
    )
    assert found["next_offset"] is None
    assert found["truncated"] is True
    assert "don't run it again" in found["note"]


class Column:
    def __init__(self, name: str) -> None:
        self.name = name

    def __getitem__(self, _at: int) -> str:
        return self.name


class FakeCursor:
    """What a database driver gives: rows from where the cursor is."""

    def __init__(self, count: int, says_rowcount: bool) -> None:
        self.rows = [(i,) for i in range(count)]
        self.at = 0
        self.description = [Column("n")]
        self.rowcount = count if says_rowcount else -1

    def scroll(self, by: int) -> None:
        self.at += by

    def fetchmany(self, size: int) -> list[tuple[int]]:
        found = self.rows[self.at : self.at + size]
        self.at += len(found)
        return found


def test_postgres_pages_and_says_how_many_rows_there_are() -> None:
    first = supabase._rows(cast(Any, FakeCursor(450, True)), 200)
    assert (first["total_rows"], first["next_offset"], first["row_count"]) == (450, 200, 200)
    last = supabase._rows(cast(Any, FakeCursor(450, True)), 200, 400)
    assert (last["rows"][0], last["next_offset"], last["row_count"]) == ([400], None, 50)
    past = supabase._rows(cast(Any, FakeCursor(450, True)), 200, 900)
    assert (past["rows"], past["next_offset"]) == ([], None)


def test_oracle_pages_and_says_it_doesnt_know_the_total() -> None:
    first = oracle._rows(FakeCursor(450, False), 200)
    assert (first["total_rows"], first["next_offset"]) == (None, 200)
    middle = oracle._rows(FakeCursor(450, False), 200, offset=200)
    assert (middle["rows"][0], middle["next_offset"]) == ([200], 400)
    last = oracle._rows(FakeCursor(450, False), 200, offset=400)
    assert (last["row_count"], last["next_offset"]) == (50, None)


def test_rows_say_more_follow_even_without_a_total() -> None:
    answer = sql_paging.rows(["n"], [[1], [2], [3]], 2, 0, None)
    assert (answer["row_count"], answer["next_offset"]) == (2, 2)


# --- HTTP, GitHub, and anything else long ---


def test_http_no_longer_cuts_an_answer_off() -> None:
    body = "x" * 400_000
    response = httpx2.Response(200, text=body, headers={"content-type": "text/plain"})
    assert http_api._answer(response)["text"] == body


class FakeGithub:
    def handle(self, request: httpx2.Request) -> httpx2.Response:
        path = request.url.path
        if path.endswith("/comments"):
            at = int(request.url.params["page"])
            rows = [{"user": {"login": "anna"}, "body": f"comment {at}"}]
            return httpx2.Response(200, json=rows)
        if "/contents/" in path:
            text = base64.b64encode(b"x" * 300_000).decode()
            return httpx2.Response(200, json={"content": text})
        return httpx2.Response(200, json={"number": 1, "body": "y" * 50_000, "comments": 250})


async def test_github_says_how_much_of_a_conversation_and_a_file_it_gave(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(github, "transport", httpx2.MockTransport(FakeGithub().handle))
    target = Target("t", Env.DEV, None, Secret("r", "ghp_faketoken"))
    issue = await github.get_issue.handler(target, repo="acme/web", number=1)
    assert len(issue["body"]) == 50_000
    assert (issue["total_comments"], issue["next_page"], issue["truncated"]) == (250, 2, True)
    last = await github.get_issue.handler(target, repo="acme/web", number=1, comments_page=3)
    assert last["next_page"] is None
    file = await github.get_file.handler(target, repo="acme/web", path="a.txt")
    assert (file["total_chars"], file["next_offset"]) == (300_000, PAGE_CHARS)


@pytest.fixture(autouse=True)
def nothing_kept() -> Any:
    continuations.clear()
    yield
    continuations.clear()


def test_a_short_result_passes_as_it_is() -> None:
    result = {"ok": True}
    assert continuations.fit(ME, result) is result
    assert continuations.kept_count() == 0


def test_a_long_result_is_read_back_exactly() -> None:
    result = {"rows": [{"n": i, "text": "word " * 20} for i in range(10_000)]}
    first = continuations.fit(ME, result)
    assert first["truncated"] is True
    assert first["returned_chars"] == PAGE_CHARS
    assert first["total_chars"] > PAGE_CHARS
    assert "read_more" in first["note"]
    text = first["text"]
    offset = first["next_offset"]
    while offset is not None:
        part = continuations.read_more(ME, first["continuation"], offset, PAGE_CHARS)
        text += part["text"]
        offset = part["next_offset"]
    assert json.loads(text) == result


def test_someone_elses_result_cant_be_read() -> None:
    first = continuations.fit(ME, {"text": "x" * 400_000})
    with pytest.raises(ToolError, match="no longer kept"):
        continuations.read_more(SOMEONE_ELSE, first["continuation"], 0, 100)
    assert continuations.read_more(ME, first["continuation"], 0, 100)["returned_chars"] == 100


def test_a_kept_result_ends(monkeypatch: pytest.MonkeyPatch) -> None:
    first = continuations.fit(ME, {"text": "x" * 400_000})
    later = time.monotonic() + continuations.TTL_SECONDS + 1
    monkeypatch.setattr(continuations.time, "monotonic", lambda: later)
    with pytest.raises(ToolError, match="don't repeat anything that changed"):
        continuations.read_more(ME, first["continuation"], 0, 100)


def test_only_so_many_are_kept(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(continuations, "MOST_RESULTS", 2)
    oldest = continuations.fit(ME, {"text": "x" * 400_000})
    continuations.fit(ME, {"text": "y" * 400_000})
    continuations.fit(ME, {"text": "z" * 400_000})
    assert continuations.kept_count() == 2
    with pytest.raises(ToolError):
        continuations.read_more(ME, oldest["continuation"], 0, 100)


def test_a_result_too_long_to_keep_says_what_was_kept(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(continuations, "MOST_KEPT_CHARS", 350_000)
    first = continuations.fit(ME, {"text": "x" * 500_000})
    last = continuations.read_more(ME, first["continuation"], PAGE_CHARS, PAGE_CHARS)
    assert last["next_offset"] is None
    assert last["truncated"] is True
    assert last["total_chars"] > 500_000
    assert "Only the first" in last["note"]


@pytest.fixture
def vault(database_url: str) -> Vault:
    return get_vault()


async def test_a_long_vendor_answer_reaches_the_agent_in_parts_with_secrets_scrubbed(
    vault: Vault, monkeypatch: pytest.MonkeyPatch
) -> None:
    key = "sk_test_clerkfake123456"
    ref = "file://ringkeeper/connections/acme-clerk"
    vault.store(ref, json.dumps({"vendor": "clerk", "api_key": key}))
    with session_scope() as session:
        make_project(session, "acme-web", owner="acme")
        make_instance(
            session, "acme-clerk", type=InstanceType.VENDOR, secret_ref=ref,
            config={"vendor": "clerk"},
        )  # fmt: skip
    bind("acme-web", "acme-clerk")

    def answer(_request: httpx2.Request) -> httpx2.Response:
        rows = [{"id": f"user_{i}", "note": "word " * 40} for i in range(3000)]
        return httpx2.Response(200, json={"data": rows, "leak": key})

    monkeypatch.setattr(vendor, "transport", httpx2.MockTransport(answer))
    call = ToolCall(
        ME, InstanceType.VENDOR, "call_operation", "acme-clerk",
        {"operation": "clerk.list_users"},
    )  # fmt: skip
    first = await run_call(call, vault)
    assert first["truncated"] is True
    assert first["continuation"]
    text = first["text"]
    offset = first["next_offset"]
    while offset is not None:
        part = continuations.read_more(ME, first["continuation"], offset, PAGE_CHARS)
        text += part["text"]
        offset = part["next_offset"]
    whole = json.loads(text)
    assert len(whole["data"]) == 3000
    assert key not in text


async def test_every_server_can_read_on() -> None:
    for adapter in ADAPTERS.values():
        server = build_server(adapter, cast(Vault, None))
        tools = {t.name: t for t in await server.list_tools()}
        assert "read_more" in tools
        assert "continuation" in (tools["read_more"].description or "")


# --- the worker ---


def test_the_worker_is_told_to_ask_for_more_when_a_result_is_partial() -> None:
    note = " ".join(HEADLESS_NOTE.split())
    assert '"truncated": true' in note
    assert "fetch the rest before you answer" in note
    assert "read_more" in note
    assert "next_offset" in note
    assert "based on part of it" in note
    assert "Never summarize part of something as if it were the whole" in note


def test_claude_codes_own_limit_lets_a_whole_page_through() -> None:
    assert int(FIXED_ENV["MAX_MCP_OUTPUT_TOKENS"]) >= PAGE_CHARS // 2
