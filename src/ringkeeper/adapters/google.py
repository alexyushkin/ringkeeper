"""Google accounts (Gmail, Drive, Calendar, Sheets, Docs, YouTube) through the owner's own Google
app. The secret is JSON from services.google_oauth (the refresh and access
tokens); the gateway refreshes it before each call. The connection's config
says which account it is (`email`) and what it may be used for (`access`).

Every write waits for the user's approval by default, even on test data:
sending, sharing, invites, edits, uploads, drafts, the trash. The owner can
let a tool run without asking, per connection (`write_rules`, see
services.calls), except sending: it follows the rule for every email connection
(adapters.mail), so it always asks and a mailing needs the name typed. Reading
runs.

A tool whose access the connection wasn't given says so instead of calling
Google: the owner adds it by signing in again with more ticked.

YouTube: reading runs. A private video, scheduled or not, waits for approval. Anything public or
unlisted, editing a video's details, replying and deleting need the name typed. Privacy is always
sent, and it's private unless the call says otherwise: YouTube's own default is public. A sign-in
reaches one channel, the one picked on Google's page.

A scheduled video goes public by itself at its time: that's the point of it, and approving it
allows that. A time already past would publish at once, so it's refused, again when an approval
runs: the time can pass while the card waits.
"""

import asyncio
import base64
import json
import mimetypes
import re
from dataclasses import replace
from datetime import UTC, datetime
from email.message import EmailMessage
from pathlib import Path
from typing import Annotated, Any, Literal, NotRequired, TypedDict, cast
from urllib.parse import quote

import httpx2
from pydantic import Field

from ringkeeper.adapters import extract, files, mail_text, rest
from ringkeeper.adapters.base import (
    JSON,
    Adapter,
    Hold,
    Target,
    ToolError,
    ToolKind,
    ToolSpec,
    Undoer,
    always_held,
    final,
    read,
    saves,
    saves_draft,
    shows,
)
from ringkeeper.adapters.mail import bulk_reason
from ringkeeper.adapters.mail_text import (
    AttachmentId,
    IncludeQuoted,
    ThreadBody,
    ThreadLimit,
    ThreadOffset,
)
from ringkeeper.adapters.paging import PAGE_CHARS, Offset, PageLimit, page
from ringkeeper.models import InstanceType

GMAIL = "https://gmail.googleapis.com/gmail/v1/users/me"
DRIVE = "https://www.googleapis.com/drive/v3"
UPLOAD = "https://www.googleapis.com/upload/drive/v3/files"
CALENDAR = "https://www.googleapis.com/calendar/v3"
SHEETS = "https://sheets.googleapis.com/v4/spreadsheets"
DOCS = "https://docs.googleapis.com/v1"
SEARCH_CONSOLE = "https://www.googleapis.com/webmasters/v3"
ANALYTICS_ADMIN = "https://analyticsadmin.googleapis.com/v1beta"
ANALYTICS_DATA = "https://analyticsdata.googleapis.com/v1beta"
YOUTUBE = "https://www.googleapis.com/youtube/v3"
YOUTUBE_UPLOAD = "https://www.googleapis.com/upload/youtube/v3/videos"
YOUTUBE_ANALYTICS = "https://youtubeanalytics.googleapis.com/v2"
# A part of a video at a time. YouTube wants a multiple of 256 KB.
UPLOAD_CHUNK = 32 * 256 * 1024
UPLOAD_TIMEOUT = 300.0
UPLOAD_MORE = 308  # YouTube's answer to every part but the last
VIDEO_TYPES = frozenset({
    ".mp4", ".mov", ".m4v", ".avi", ".wmv", ".flv", ".webm", ".mkv", ".mpeg", ".mpg", ".3gp",
})  # fmt: skip
# The account's address. Its own base, called with a path: an HTTP client adds a
# slash to a base address, and Google answers .../userinfo/ with a 404.
OPENID = "https://openidconnect.googleapis.com/v1"
# Tests swap in httpx2.MockTransport; production talks to Google.
transport: httpx2.AsyncBaseTransport | None = None

ADDRESS_PATTERN = r"^[^@\s<>\"]+@[^@\s<>\"]+\.[^@\s<>\"]+$"
ID_PATTERN = r"^[A-Za-z0-9_\-]{6,200}$"
Address = Annotated[str, Field(pattern=ADDRESS_PATTERN)]
Addresses = Annotated[list[Address], Field(min_length=1, max_length=50)]
MessageId = Annotated[
    str, Field(description="The message id, from search_mail.", pattern=ID_PATTERN)
]
ThreadId = Annotated[
    str, Field(description="The thread_id, from search_mail or read_mail.", pattern=ID_PATTERN)
]
FileId = Annotated[str, Field(description="The file id, from search_files.", pattern=ID_PATTERN)]
SheetId = Annotated[
    str, Field(description="The spreadsheet id (in its URL, after /d/).", pattern=ID_PATTERN)
]
Range = Annotated[
    str,
    Field(description="A range in A1 notation, e.g. Leads!A1:D20.", min_length=1, max_length=200),
]
Rows = Annotated[list[list[str | int | float | bool | None]], Field(min_length=1, max_length=1000)]
DocId = Annotated[
    str, Field(description="The document id (in its URL, after /d/).", pattern=ID_PATTERN)
]
DocTitle = Annotated[str, Field(min_length=1, max_length=300)]
DocText = Annotated[str, Field(min_length=1, max_length=100_000)]
Find = Annotated[str, Field(min_length=1, max_length=1000)]
When = Annotated[
    str,
    Field(
        description="RFC 3339 date-time (2026-11-03T15:00:00-05:00), or a date (2026-11-03) for "
        "all day.",
        max_length=40,
    ),
]
Subject = Annotated[str, Field(max_length=500)]
Body = Annotated[str, Field(max_length=100_000)]
Limit = Annotated[int, Field(ge=1, le=50)]


def config_access(target: Target) -> list[str]:
    return [str(a) for a in cast(list[Any], target.config.get("access") or [])]


def _need(target: Target, *keys: str) -> None:
    """Refuses before calling Google when the connection wasn't given this access."""
    if not set(keys) & set(config_access(target)):
        from ringkeeper.services.google_oauth import ACCESS  # noqa: PLC0415 (no cycle at import)

        wanted = " or ".join(f'"{ACCESS[k].label}"' for k in keys)
        raise ToolError(
            f"'{target.name}' wasn't given {wanted}. The user can add it under Connections "
            "(Change access)."
        )


def access_token(target: Target) -> str:
    try:
        data: Any = json.loads(target.secret.reveal())
    except ValueError:
        raise ToolError("this Google connection's secret isn't in the expected form") from None
    token = cast(dict[str, Any], data).get("access_token") if isinstance(data, dict) else None
    if not token:
        raise ToolError("this Google connection's secret isn't in the expected form")
    return str(token)


def _client(target: Target, base: str) -> httpx2.AsyncClient:
    return httpx2.AsyncClient(
        base_url=base,
        headers={"Authorization": f"Bearer {access_token(target)}"},
        timeout=rest.TIMEOUT,
        transport=transport,
    )


async def _call(target: Target, base: str, method: str, path: str, **kwargs: Any) -> JSON:
    return await rest.call(_client(target, base), "Google", method, path, **kwargs)


async def probe(target: Target) -> str:
    """Which account this is (the Test button)."""
    me = await _call(target, OPENID, "GET", "/userinfo")
    allowed = ", ".join(config_access(target)) or "nothing"
    return f"Connected to Google as {me.get('email')} (allowed: {allowed})."


# --- Gmail ---


def _headers(payload: JSON) -> dict[str, str]:
    return {
        str(h.get("name", "")).lower(): str(h.get("value", ""))
        for h in cast(list[JSON], payload.get("headers") or [])
    }


def _bytes(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def _decode(data: str) -> str:
    return _bytes(data).decode("utf-8", "replace")


def _text(payload: JSON) -> str:
    """The message's text: its text/plain part, or its HTML without the tags."""
    found: dict[str, str] = {}

    def walk(part: JSON) -> None:
        mime = str(part.get("mimeType") or "")
        data = cast(JSON, part.get("body") or {}).get("data")
        if data and mime in ("text/plain", "text/html") and mime not in found:
            found[mime] = _decode(str(data))
        for child in cast(list[JSON], part.get("parts") or []):
            walk(child)

    walk(payload)
    return mail_text.best_text(found.get("text/plain", ""), found.get("text/html", ""))


def _attached(payload: JSON) -> list[JSON]:
    """The parts of a message that are attachments."""
    found: list[JSON] = []

    def walk(part: JSON) -> None:
        if part.get("filename"):
            found.append(part)
        for child in cast(list[JSON], part.get("parts") or []):
            walk(child)

    walk(payload)
    return found


def _listed(part: JSON) -> JSON:
    """An attachment as a message lists it. Google's own attachment id changes with every read,
    so the id here is the part's place in the message, which doesn't."""
    body = cast(JSON, part.get("body") or {})
    return {
        "id": str(part.get("partId") or ""),
        "name": str(part.get("filename")),
        "type": str(part.get("mimeType") or ""),
        "size": int(body.get("size") or 0),
    }


def _message_fields(message: JSON, offset: int, limit: int, include_quoted: bool = False) -> JSON:
    payload = cast(JSON, message.get("payload") or {})
    headers = _headers(payload)
    return {
        "id": message.get("id"),
        "thread_id": message.get("threadId"),
        **{k: headers.get(k, "") for k in ("from", "to", "cc", "subject", "date")},
        **mail_text.body_fields(_text(payload), offset, limit, include_quoted),
        "attachments": [_listed(p) for p in _attached(payload)],
    }


@read
async def search_mail(
    target: Target,
    query: Annotated[
        str,
        Field(
            description="Gmail search, e.g. from:anna@acme.example newer_than:7d.", max_length=500
        ),
    ] = "",
    limit: Limit = 20,
) -> JSON:
    """Find messages with Gmail's search words, newest first: id, from, to, subject, date,
    whether it's unread, and a preview (the start of its own text, without what it quotes).
    The previews are enough to list or summarize what was found: read_mail only a message
    you need whole."""
    _need(target, "mail_read")
    listed = await _call(
        target, GMAIL, "GET", "/messages", params={"q": query or None, "maxResults": limit}
    )
    found: list[JSON] = []
    for item in cast(list[JSON], listed.get("messages") or [])[:limit]:
        # Its text is fetched to make the preview; only the preview goes back.
        message = await _call(
            target, GMAIL, "GET", f"/messages/{item['id']}", params={"format": "full"}
        )
        payload = cast(JSON, message.get("payload") or {})
        headers = _headers(payload)
        found.append(
            {"id": item["id"], "thread_id": message.get("threadId")}
            | {k: headers.get(k, "") for k in ("from", "to", "subject", "date")}
            | {"unread": "UNREAD" in cast(list[str], message.get("labelIds") or [])}
            | {"preview": mail_text.preview(_text(payload)) or str(message.get("snippet", ""))}
        )
    return {"account": target.config.get("email"), "messages": found}


@read
async def read_mail(
    target: Target,
    message_id: MessageId,
    offset: Offset = 0,
    limit: PageLimit = PAGE_CHARS,
    include_quoted: IncludeQuoted = False,
) -> JSON:
    """Read one message: headers, its whole text and its attachments. Doesn't mark it read. A
    long message comes in parts: total_chars says how long, next_offset where the next part
    starts. Earlier messages a reply quotes are in `quoted` (include_quoted puts them in the
    body). read_attachment reads an attachment; get_thread reads the whole conversation."""
    _need(target, "mail_read")
    message = await _call(
        target, GMAIL, "GET", f"/messages/{message_id}", params={"format": "full"}
    )
    return _message_fields(message, offset, limit, include_quoted) | {"id": message_id}


async def _attachment(target: Target, message_id: str, attachment_id: str) -> tuple[JSON, bytes]:
    """One attachment: how the message lists it, and its bytes."""
    _need(target, "mail_read")
    message = await _call(
        target, GMAIL, "GET", f"/messages/{message_id}", params={"format": "full"}
    )
    parts = _attached(cast(JSON, message.get("payload") or {}))
    part = next((p for p in parts if str(p.get("partId")) == attachment_id), None)
    if part is None:
        raise ToolError(f"this message has no attachment {attachment_id}")
    body = cast(JSON, part.get("body") or {})
    data = str(body.get("data") or "")
    if not data:
        fetched = await _call(
            target, GMAIL, "GET", f"/messages/{message_id}/attachments/{body.get('attachmentId')}"
        )
        data = str(fetched.get("data") or "")
    content = _bytes(data)
    return {k: v for k, v in _listed(part).items() if k != "id"} | {"size": len(content)}, content


@read
async def read_attachment(
    target: Target,
    message_id: MessageId,
    attachment_id: AttachmentId,
    pages: files.Pages = None,
    inside: files.Inside = None,
    offset: Offset = 0,
    limit: PageLimit = PAGE_CHARS,
) -> JSON:
    """Read one attachment's text: PDF, Word, Excel, PowerPoint, CSV, text, an attached email or
    a .zip (`inside` reads a file in it), and pictures where OCR is installed. A long file
    starts with its outline: read on with pages=. view_attachment shows a page as an image."""
    meta, content = await _attachment(target, message_id, attachment_id)
    found = extract.extract(str(meta["name"]), str(meta["type"]), content, inside)
    return files.text_answer(meta, found, offset, limit, pages, "view_attachment")


@shows
async def view_attachment(
    target: Target,
    message_id: MessageId,
    attachment_id: AttachmentId,
    page: files.PageNumber = 1,
    picture: files.PictureNumber = None,
    inside: files.Inside = None,
) -> JSON:
    """Look at one page of an attached PDF, or a picture (attached, or on a slide or in a Word
    document), as an image: better than its text for a table, a chart or a scan."""
    meta, content = await _attachment(target, message_id, attachment_id)
    shown = extract.view(str(meta["name"]), str(meta["type"]), content, page, picture, inside)
    return files.picture_answer(meta, shown)


@saves
async def save_attachment(
    target: Target, message_id: MessageId, attachment_id: AttachmentId, inside: files.Inside = None
) -> JSON:
    """Save a copy of an attachment in the project's folder (attachments/), to work on it there.
    Answers its path. Nothing changes in the mailbox."""
    meta, content = await _attachment(target, message_id, attachment_id)
    name, _kind, content = extract.opened(str(meta["name"]), str(meta["type"]), content, inside)
    return await files.save(target.project, name, content)


@read
async def get_thread(
    target: Target,
    thread_id: ThreadId,
    offset: ThreadOffset = 0,
    limit: ThreadLimit = 20,
    body_chars: ThreadBody = mail_text.THREAD_BODY_CHARS,
) -> JSON:
    """Read a conversation, oldest message first. Each message shows the start of its text,
    with total_chars: read_mail reads the rest of a long one."""
    _need(target, "mail_read")
    thread = await _call(target, GMAIL, "GET", f"/threads/{thread_id}", params={"format": "full"})
    messages = sorted(
        cast(list[JSON], thread.get("messages") or []),
        key=lambda m: int(str(m.get("internalDate") or 0)),
    )
    shown = [_message_fields(m, 0, body_chars) for m in messages]
    return mail_text.thread_page(shown, offset, limit)


async def _compose(
    target: Target,
    to: list[str],
    subject: str,
    body: str,
    cc: list[str] | None,
    reply_to: str | None,
) -> JSON:
    """The Gmail API's {"raw", "threadId"} for a new message, or a reply in its thread."""
    message = EmailMessage()
    message["From"] = str(target.config.get("email") or "me")
    message["To"] = ", ".join(to)
    if cc:
        message["Cc"] = ", ".join(cc)
    message["Subject"] = subject
    thread: str | None = None
    if reply_to:
        original = await _call(
            target, GMAIL, "GET", f"/messages/{reply_to}",
            params={"format": "metadata", "metadataHeaders": ["Message-ID"]},
        )  # fmt: skip
        thread = str(original.get("threadId") or "") or None
        if parent := _headers(cast(JSON, original.get("payload") or {})).get("message-id"):
            message["In-Reply-To"] = parent
            message["References"] = parent
    message.set_content(body)
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
    return {"raw": raw} | ({"threadId": thread} if thread else {})


ReplyTo = Annotated[
    str | None,
    Field(description="The id of the message this answers (keeps the thread).", pattern=ID_PATTERN),
]


@final(bulk_reason)
async def send_mail(
    target: Target,
    to: Addresses,
    subject: Subject,
    body: Body,
    cc: list[Address] | None = None,
    reply_to: ReplyTo = None,
) -> JSON:
    """Send an email from this Google account. Waits for the user's approval."""
    _need(target, "mail_send", "mail_drafts")
    sent = await _call(
        target, GMAIL, "POST", "/messages/send",
        json=await _compose(target, to, subject, body, cc, reply_to),
    )  # fmt: skip
    return {"sent": True, "from": target.config.get("email"), "to": to, "id": sent.get("id")}


@saves_draft
async def create_draft(
    target: Target,
    to: Addresses,
    subject: Subject,
    body: Body,
    cc: list[Address] | None = None,
    reply_to: ReplyTo = None,
) -> JSON:
    """Save an email as a Gmail draft for the user to check and send. Nothing is sent. Put
    the draft's link in your final message."""
    _need(target, "mail_drafts")
    message = await _compose(target, to, subject, body, cc, reply_to)
    saved = await _call(target, GMAIL, "POST", "/drafts", json={"message": message})
    made = cast(JSON, saved.get("message") or {}).get("id")
    link = draft_link(str(target.config.get("email") or ""), str(made)) if made else None
    return {"saved": "draft", "id": saved.get("id"), "to": to, "subject": subject, "link": link}


def draft_link(address: str, message_id: str) -> str:
    """Where the draft opens in Gmail on the web."""
    return f"https://mail.google.com/mail/?authuser={quote(address)}#drafts?compose={message_id}"


class DraftUndo(Undoer):
    """Taking back a saved draft deletes it."""

    async def before(self, target: Target, args: JSON) -> JSON:
        return {"draft": None, "subject": args.get("subject")}

    async def after(self, target: Target, args: JSON, before: JSON, result: JSON) -> JSON:
        before["draft"] = result.get("id")
        return {"draft": before["draft"]}

    async def current(self, target: Target, kept: JSON) -> JSON:
        return {"draft": kept.get("draft")}

    async def restore(self, target: Target, kept: JSON) -> JSON:
        _need(target, "mail_drafts")
        await _call(target, GMAIL, "DELETE", f"/drafts/{quote(str(kept['draft']), safe='')}")
        return {"deleted": kept["draft"]}

    def describe(self, kept: JSON) -> str:
        return f"Deletes the draft it saved ({kept.get('subject') or 'no subject'})."


create_draft = replace(create_draft, undo=DraftUndo())


@always_held
async def trash_mail(target: Target, message_id: MessageId) -> JSON:
    """Move a message to the trash (Gmail empties it after 30 days)."""
    _need(target, "mail_trash")
    await _call(target, GMAIL, "POST", f"/messages/{message_id}/trash")
    return {"trashed": message_id}


# --- Drive ---


def _drive_q(text: str) -> str:
    escaped = text.replace("\\", "\\\\").replace("'", "\\'")
    words = f"(name contains '{escaped}' or fullText contains '{escaped}') and " if text else ""
    return words + "trashed = false"


@read
async def search_files(
    target: Target,
    query: Annotated[
        str, Field(description="Words in the file's name or text.", max_length=300)
    ] = "",
    limit: Limit = 20,
) -> JSON:
    """Find Drive files (id, name, type, modified, link). With "files Ringkeeper creates or is
    given" access, only those files are visible."""
    _need(target, "drive_files", "drive_read_all")
    listed = await _call(
        target, DRIVE, "GET", "/files",
        params={
            "q": _drive_q(query), "pageSize": limit, "orderBy": "modifiedTime desc",
            "fields": "files(id,name,mimeType,modifiedTime,webViewLink)",
        },
    )  # fmt: skip
    return {"files": listed.get("files") or []}


EXPORTS = {
    "application/vnd.google-apps.document": "text/plain",
    "application/vnd.google-apps.spreadsheet": "text/csv",
    "application/vnd.google-apps.presentation": "text/plain",
}
# A Doc, a Sheet or a Slides deck saved to work on: as the Office file it opens as.
SAVED_AS = {
    "application/vnd.google-apps.document": (extract.WORD, ".docx"),
    "application/vnd.google-apps.spreadsheet": (extract.EXCEL, ".xlsx"),
    "application/vnd.google-apps.presentation": (extract.DECK, ".pptx"),
}


async def _drive_file(
    target: Target, file_id: str, exports: dict[str, str]
) -> tuple[JSON, str, httpx2.Response]:
    """A Drive file's details, the type its content comes as, and its content: a Doc, a Sheet
    or a deck exported as `exports` says."""
    _need(target, "drive_files", "drive_read_all")
    meta = await _call(
        target, DRIVE, "GET", f"/files/{file_id}", params={"fields": "id,name,mimeType"}
    )
    mime = str(meta.get("mimeType") or "")
    if mime in exports:
        path, params = f"/files/{file_id}/export", {"mimeType": exports[mime]}
    elif mime.startswith("application/vnd.google-apps."):
        kind = mime.rsplit(".", 1)[-1]
        raise ToolError(f"'{meta.get('name')}' is a Google {kind}, which has nothing to read here")
    else:
        path, params = f"/files/{file_id}", {"alt": "media"}
    try:
        async with _client(target, DRIVE) as http:
            response = await http.get(path, params=params)
    except httpx2.HTTPError as error:
        raise ToolError(f"cannot reach Google: {type(error).__name__}") from None
    if response.status_code >= 400:
        raise ToolError(f"Google returned {response.status_code}: {rest.error_message(response)}")
    return (
        {"id": file_id, "name": meta.get("name"), "type": mime},
        exports.get(mime, mime),
        response,
    )


@read
async def read_file(
    target: Target,
    file_id: FileId,
    pages: files.Pages = None,
    inside: files.Inside = None,
    offset: Offset = 0,
    limit: PageLimit = PAGE_CHARS,
) -> JSON:
    """Read a file's text: Docs and Slides as text, Sheets as CSV, text files as they are, and
    what's in PDF, Word, Excel, PowerPoint and .zip files. A long file starts with its outline:
    read on with pages=. view_file shows a page as an image."""
    answer, kind, response = await _drive_file(target, file_id, EXPORTS)
    if answer["type"] in EXPORTS:
        found = extract.Extracted(response.text, "text")
    else:
        found = extract.extract(str(answer["name"] or ""), kind, response.content, inside)
    return files.text_answer(answer, found, offset, limit, pages, "view_file")


@shows
async def view_file(
    target: Target,
    file_id: FileId,
    page: files.PageNumber = 1,
    picture: files.PictureNumber = None,
    inside: files.Inside = None,
) -> JSON:
    """Look at one page of a file (a PDF, a Doc, a Sheet or a Slides deck), or a picture, as an
    image: better than its text for a table, a chart or a scan."""
    pdf = dict.fromkeys(EXPORTS, extract.PDF)
    answer, kind, response = await _drive_file(target, file_id, pdf)
    shown = extract.view(str(answer["name"] or ""), kind, response.content, page, picture, inside)
    return files.picture_answer(answer, shown)


@saves
async def save_file(target: Target, file_id: FileId, inside: files.Inside = None) -> JSON:
    """Save a copy of a Drive file in the project's folder (attachments/), to work on it there:
    a Doc, Sheet or Slides deck as Word, Excel or PowerPoint. Answers its path. Nothing changes
    in Drive."""
    office = {k: kind for k, (kind, _suffix) in SAVED_AS.items()}
    answer, kind, response = await _drive_file(target, file_id, office)
    name = str(answer["name"] or "file")
    if answer["type"] in SAVED_AS:
        name = files.suffixed(name, SAVED_AS[str(answer["type"])][1])
    name, _kind, content = extract.opened(name, kind, response.content, inside)
    return await files.save(target.project, name, content)


@always_held
async def upload_file(
    target: Target,
    name: Annotated[str, Field(min_length=1, max_length=255)],
    content: Annotated[str, Field(max_length=1_000_000)],
    mime_type: Annotated[str, Field(pattern=r"^[\w.+-]+/[\w.+-]+$")] = "text/plain",
    folder_id: Annotated[str | None, Field(pattern=ID_PATTERN)] = None,
    as_google_doc: Annotated[
        bool, Field(description="Convert to a Google Doc (text) or Sheet (CSV).")
    ] = False,
) -> JSON:
    """Create a file in Drive from text. It's then one of the files this connection can see."""
    _need(target, "drive_files")
    meta: JSON = {"name": name}
    if folder_id:
        meta["parents"] = [folder_id]
    if as_google_doc:
        meta["mimeType"] = (
            "application/vnd.google-apps.spreadsheet"
            if mime_type == "text/csv"
            else "application/vnd.google-apps.document"
        )
    boundary = "ringkeeper-upload"
    body = (
        f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n"
        f"{json.dumps(meta)}\r\n--{boundary}\r\nContent-Type: {mime_type}; charset=UTF-8\r\n\r\n"
        f"{content}\r\n--{boundary}--"
    )
    try:
        async with _client(target, UPLOAD) as http:
            response = await http.post(
                "", params={"uploadType": "multipart", "fields": "id,name,webViewLink"},
                content=body.encode(),
                headers={"Content-Type": f"multipart/related; boundary={boundary}"},
            )  # fmt: skip
    except httpx2.HTTPError as error:
        raise ToolError(f"cannot reach Google: {type(error).__name__}") from None
    if response.status_code >= 400:
        raise ToolError(f"Google returned {response.status_code}: {rest.error_message(response)}")
    return cast(JSON, response.json())


@always_held
async def share_file(
    target: Target,
    file_id: FileId,
    email: Address,
    role: Literal["reader", "commenter", "writer"] = "reader",
    notify: Annotated[bool, Field(description="Email them that it's shared.")] = True,
) -> JSON:
    """Share a Drive file with someone. Waits for the user's approval."""
    _need(target, "drive_files")
    await _call(
        target, DRIVE, "POST", f"/files/{file_id}/permissions",
        params={"sendNotificationEmail": "true" if notify else "false"},
        json={"type": "user", "role": role, "emailAddress": email},
    )  # fmt: skip
    return {"shared": file_id, "with": email, "role": role}


# --- Calendar ---


def _when(value: str) -> JSON:
    return {"date": value} if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) else {"dateTime": value}


@read
async def list_events(
    target: Target,
    start: When,
    end: When,
    query: Annotated[str, Field(max_length=300)] = "",
    limit: Limit = 25,
) -> JSON:
    """Events in the primary calendar between start and end (title, when, attendees, link)."""
    _need(target, "calendar_read", "calendar_write")
    listed = await _call(
        target, CALENDAR, "GET", "/calendars/primary/events",
        params={
            "timeMin": start if "T" in start else f"{start}T00:00:00Z",
            "timeMax": end if "T" in end else f"{end}T23:59:59Z",
            "q": query or None, "maxResults": limit, "singleEvents": "true", "orderBy": "startTime",
        },
    )  # fmt: skip
    return {
        "events": [
            {
                "id": e.get("id"),
                "title": e.get("summary"),
                "start": e.get("start"),
                "end": e.get("end"),
                "location": e.get("location"),
                "attendees": [a.get("email") for a in cast(list[JSON], e.get("attendees") or [])],
                "link": e.get("htmlLink"),
            }
            for e in cast(list[JSON], listed.get("items") or [])
        ]
    }


@always_held
async def create_event(
    target: Target,
    title: Annotated[str, Field(min_length=1, max_length=500)],
    start: When,
    end: When,
    attendees: list[Address] | None = None,
    description: Annotated[str, Field(max_length=8000)] = "",
    location: Annotated[str, Field(max_length=500)] = "",
) -> JSON:
    """Add an event to the primary calendar; attendees get Google's invitation email."""
    _need(target, "calendar_write")
    event: JSON = {"summary": title, "start": _when(start), "end": _when(end)}
    if description:
        event["description"] = description
    if location:
        event["location"] = location
    if attendees:
        event["attendees"] = [{"email": a} for a in attendees]
    made = await _call(
        target, CALENDAR, "POST", "/calendars/primary/events",
        params={"sendUpdates": "all" if attendees else "none"}, json=event,
    )  # fmt: skip
    return {"created": made.get("id"), "link": made.get("htmlLink"), "invited": attendees or []}


# --- Sheets ---


def _range(spreadsheet_id: str, cells: str) -> str:
    return f"/{spreadsheet_id}/values/{quote(cells, safe='')}"


@read
async def read_sheet(target: Target, spreadsheet_id: SheetId, cells: Range) -> JSON:
    """Read a range of a spreadsheet: its rows, as shown."""
    _need(target, "sheets_read", "sheets_write")
    got = await _call(target, SHEETS, "GET", _range(spreadsheet_id, cells))
    return {"range": got.get("range"), "rows": got.get("values") or []}


@always_held
async def update_sheet(target: Target, spreadsheet_id: SheetId, cells: Range, rows: Rows) -> JSON:
    """Overwrite a range with these rows (formulas and numbers as if typed)."""
    _need(target, "sheets_write")
    done = await _call(
        target, SHEETS, "PUT", _range(spreadsheet_id, cells),
        params={"valueInputOption": "USER_ENTERED"}, json={"values": rows},
    )  # fmt: skip
    return {"updated": done.get("updatedRange"), "cells": done.get("updatedCells")}


@always_held
async def append_rows(target: Target, spreadsheet_id: SheetId, cells: Range, rows: Rows) -> JSON:
    """Add rows after the table in this range (nothing that was there changes)."""
    _need(target, "sheets_write")
    done = await _call(
        target, SHEETS, "POST", _range(spreadsheet_id, cells) + ":append",
        params={"valueInputOption": "USER_ENTERED", "insertDataOption": "INSERT_ROWS"},
        json={"values": rows},
    )  # fmt: skip
    updates = cast(JSON, done.get("updates") or {})
    return {"appended": updates.get("updatedRange"), "rows": updates.get("updatedRows")}


# --- Docs ---


def _doc_text(elements: list[Any]) -> str:
    """A document's words, in order: paragraphs, then each table cell's, and a table of contents."""
    parts: list[str] = []
    for element in elements:
        item = cast(JSON, element)
        if "paragraph" in item:
            runs = cast(list[JSON], cast(JSON, item["paragraph"]).get("elements") or [])
            parts += [str(cast(JSON, r.get("textRun") or {}).get("content") or "") for r in runs]
        elif "table" in item:
            for row in cast(list[JSON], cast(JSON, item["table"]).get("tableRows") or []):
                cells = cast(list[JSON], row.get("tableCells") or [])
                text = "\t".join(_doc_text(cell.get("content") or []).strip() for cell in cells)
                parts.append(text + "\n")
        elif "tableOfContents" in item:
            parts.append(_doc_text(cast(JSON, item["tableOfContents"]).get("content") or []))
    return "".join(parts)


@read
async def read_doc(
    target: Target, document_id: DocId, offset: Offset = 0, limit: PageLimit = PAGE_CHARS
) -> JSON:
    """Read a Google Doc: its title and its text, tables as tab-separated rows. A long one
    comes in parts: total_chars says how long, next_offset where the next part starts."""
    _need(target, "docs_read", "docs_write")
    doc = await _call(target, DOCS, "GET", f"/documents/{document_id}")
    body = cast(JSON, doc.get("body") or {})
    return {
        "document_id": doc.get("documentId"),
        "title": doc.get("title"),
        "revision_id": doc.get("revisionId"),
    } | page(_doc_text(body.get("content") or []), offset, limit)


def _link(document_id: str) -> str:
    return f"https://docs.google.com/document/d/{document_id}/edit"


@always_held
async def create_doc(target: Target, title: DocTitle, text: DocText | None = None) -> JSON:
    """Make a new Google Doc in the account's Drive, with this text in it. Only the account
    can see it until someone shares it."""
    _need(target, "docs_write")
    made = await _call(target, DOCS, "POST", "/documents", json={"title": title})
    document_id = str(made.get("documentId") or "")
    if text:
        insert: JSON = {"insertText": {"text": text, "endOfSegmentLocation": {}}}
        await _call(target, DOCS, "POST", f"/documents/{document_id}:batchUpdate",
                    json={"requests": [insert]})  # fmt: skip
    return {"created": document_id, "title": made.get("title"), "link": _link(document_id)}


class Replacement(TypedDict):
    """Every match of `find` becomes `replace_with`."""

    find: Find
    replace_with: Annotated[str, Field(max_length=10_000)]
    match_case: NotRequired[bool]  # True unless said


async def _doc_revision(target: Target, document_id: str, **_edit: Any) -> JSON:
    """The document as it is when the edit is held: approving a stale edit is refused."""
    doc = await _call(target, DOCS, "GET", f"/documents/{document_id}",
                      params={"fields": "revisionId"})  # fmt: skip
    return {"revision_id": doc.get("revisionId")}


@always_held
async def edit_doc(
    target: Target,
    document_id: DocId,
    replace: Annotated[list[Replacement], Field(max_length=50)] | None = None,
    append: DocText | None = None,
) -> JSON:
    """Change a Google Doc: replace every match of a piece of text, and/or add text at the
    end. Nothing else in it changes. If the document changes between the request and your
    approval, the edit doesn't run."""
    _need(target, "docs_write")
    if not replace and not append:
        raise ToolError("say what to change: replace, append, or both")
    requests: list[JSON] = [
        {"replaceAllText": {
            "containsText": {"text": r["find"], "matchCase": r.get("match_case", True)},
            "replaceText": r["replace_with"],
        }}
        for r in replace or []
    ]  # fmt: skip
    if append:
        requests.append({"insertText": {"text": append, "endOfSegmentLocation": {}}})
    done = await _call(target, DOCS, "POST", f"/documents/{document_id}:batchUpdate",
                       json={"requests": requests})  # fmt: skip
    replies = cast(list[JSON], done.get("replies") or [])
    changed = [
        int(cast(JSON, r.get("replaceAllText") or {}).get("occurrencesChanged") or 0)
        for r in replies[: len(replace or [])]
    ]
    return {
        "edited": document_id, "replaced": changed, "appended_chars": len(append or ""),
        "link": _link(document_id),
    }  # fmt: skip


def secret_fragments(value: str) -> list[str]:
    try:
        data: Any = json.loads(value)
    except ValueError:
        return []
    if not isinstance(data, dict):
        return []
    fields = cast(dict[str, Any], data)
    return [str(fields[k]) for k in ("refresh_token", "access_token") if fields.get(k)]


# --- Search Console and Analytics (read-only) ---


Day = Annotated[str, Field(pattern=r"^\d{4}-\d{2}-\d{2}$", description="YYYY-MM-DD.")]
AnalyticsDay = Annotated[
    str,
    Field(
        pattern=r"^(\d{4}-\d{2}-\d{2}|today|yesterday|\d{1,4}daysAgo)$",
        description="YYYY-MM-DD, today, yesterday or NdaysAgo.",
    ),
]
Names = Annotated[list[Annotated[str, Field(max_length=100)]], Field(max_length=10)]
ReportRows = Annotated[int, Field(ge=1, le=1000)]


@read
async def list_search_sites(target: Target) -> JSON:
    """The sites this account can see in Search Console, and its access to each."""
    _need(target, "search_console_read")
    listed = await _call(target, SEARCH_CONSOLE, "GET", "/sites")
    entries = cast(list[JSON], listed.get("siteEntry") or [])
    return {
        "sites": [{"site": s.get("siteUrl"), "access": s.get("permissionLevel")} for s in entries]
    }


@read
async def search_performance(
    target: Target,
    site: Annotated[str, Field(max_length=300, description="From list_search_sites.")],
    start: Day,
    end: Day,
    dimensions: Names | None = None,
    limit: ReportRows = 100,
) -> JSON:
    """Clicks, impressions, CTR and position from Search Console. `dimensions`: query, page,
    country, device, date."""
    _need(target, "search_console_read")
    body: JSON = {"startDate": start, "endDate": end, "dimensions": dimensions or ["query"],
                  "rowLimit": limit}  # fmt: skip
    found = await _call(
        target, SEARCH_CONSOLE, "POST",
        f"/sites/{quote(site, safe='')}/searchAnalytics/query", json=body,
    )  # fmt: skip
    return {"rows": found.get("rows") or []}


@read
async def list_analytics_properties(target: Target) -> JSON:
    """The Google Analytics accounts and properties this account can read."""
    _need(target, "analytics_read")
    found = await _call(target, ANALYTICS_ADMIN, "GET", "/accountSummaries")
    accounts = cast(list[JSON], found.get("accountSummaries") or [])
    return {
        "accounts": [
            {
                "account": a.get("displayName"),
                "properties": [
                    {"property": p.get("property"), "name": p.get("displayName")}
                    for p in cast(list[JSON], a.get("propertySummaries") or [])
                ],
            }
            for a in accounts
        ]
    }


@read
async def analytics_report(
    target: Target,
    property_id: Annotated[
        str, Field(pattern=r"^(properties/)?\d+$", description="From list_analytics_properties.")
    ],
    start: AnalyticsDay,
    end: AnalyticsDay,
    metrics: Names,
    dimensions: Names | None = None,
    limit: ReportRows = 100,
) -> JSON:
    """A Google Analytics report, like metrics ["sessions", "totalUsers"] by dimensions
    ["date"]. Names are GA4's own."""
    _need(target, "analytics_read")
    number = property_id.rsplit("/", 1)[-1]
    body: JSON = {
        "dateRanges": [{"startDate": start, "endDate": end}],
        "metrics": [{"name": m} for m in metrics],
        "dimensions": [{"name": d} for d in dimensions or []],
        "limit": limit,
    }
    found = await _call(
        target, ANALYTICS_DATA, "POST", f"/properties/{number}:runReport", json=body
    )
    columns = [
        str(h.get("name"))
        for h in (
            *cast(list[JSON], found.get("dimensionHeaders") or []),
            *cast(list[JSON], found.get("metricHeaders") or []),
        )
    ]
    rows = [
        dict(zip(columns, [
            v.get("value")
            for v in (
                *cast(list[JSON], r.get("dimensionValues") or []),
                *cast(list[JSON], r.get("metricValues") or []),
            )
        ], strict=False))
        for r in cast(list[JSON], found.get("rows") or [])
    ]  # fmt: skip
    return {"rows": rows, "row_count": found.get("rowCount", len(rows))}


# --- YouTube ---

Privacy = Literal["private", "unlisted", "public"]
VideoId = Annotated[str, Field(description="The video's ID.", pattern=r"^[A-Za-z0-9_\-]{6,20}$")]
CommentId = Annotated[str, Field(description="The comment's ID.", pattern=ID_PATTERN)]
PublishAt = Annotated[
    str | None,
    Field(
        description="When a private video goes public by itself, like 2026-11-20T15:00:00-05:00. "
        "Only with privacy private.",
        max_length=40,
    ),
]
Title = Annotated[str, Field(min_length=1, max_length=100)]
Description = Annotated[str, Field(max_length=5000)]
Tags = Annotated[list[Annotated[str, Field(max_length=100)]], Field(max_length=50)]
PUBLIC_VIDEO = "makes a video visible to other people"
EDITS_VIDEO = "changes details people may already see"


def _check_schedule(privacy: str, publish_at: str | None) -> None:
    """YouTube publishes a time in the past at once, so one never reaches it."""
    if publish_at is None:
        return
    if privacy != "private":
        raise ToolError("a scheduled video stays private until its time: set privacy to private")
    try:
        when = datetime.fromisoformat(publish_at)
    except ValueError:
        raise ToolError("publish_at isn't a date and time like 2026-11-20T15:00:00-05:00") from None
    if when.tzinfo is None:
        raise ToolError("publish_at needs its time zone, like -05:00")
    if when <= datetime.now(UTC):
        raise ToolError("that time has passed, and YouTube would publish at once: pick a new one")


def _video_why(args: JSON) -> str | None:
    return None if args.get("privacy", "private") == "private" else PUBLIC_VIDEO


def _project_video(target: Target, file: str) -> Path:
    """A video in the project's own folder, and nowhere else."""
    from ringkeeper.db import session_scope  # noqa: PLC0415 (no cycle at import)
    from ringkeeper.services import projects  # noqa: PLC0415

    if target.project is None:
        raise ToolError("a video comes from a project's folder, and this call has no project")
    with session_scope(commit=False) as session:
        folder = projects.get_project(session, target.project).workspace_path
    if not folder:
        raise ToolError(f"'{target.project}' has no folder")
    root = Path(folder).resolve()
    found = (root / file).resolve()
    if not found.is_relative_to(root):
        raise ToolError("that file isn't in the project's folder")
    if found.suffix.lower() not in VIDEO_TYPES:
        raise ToolError(f"YouTube won't take a {found.suffix or 'file like this'}")
    if not found.is_file():
        raise ToolError(f"there's no {file} in the project's folder")
    return found


async def _send_parts(http: httpx2.AsyncClient, address: str, path: Path, size: int) -> JSON:
    """The video, a part at a time, so a large one is never all in memory."""
    answer: JSON = {}
    with path.open("rb") as video:
        start = 0
        while start < size:
            data = await asyncio.to_thread(video.read, UPLOAD_CHUNK)
            end = start + len(data) - 1
            response = await http.put(
                address, content=data, headers={"Content-Range": f"bytes {start}-{end}/{size}"}
            )
            if response.status_code >= 400:
                raise ToolError(f"YouTube stopped the upload: {rest.error_message(response)}")
            if response.status_code != UPLOAD_MORE:
                answer = cast(JSON, response.json())
            start = end + 1
    return answer


@read
async def youtube_channel(target: Target) -> JSON:
    """This account's YouTube channel: its name, its numbers and its uploads list."""
    _need(target, "youtube_read", "youtube_manage")
    found = await _call(
        target, YOUTUBE, "GET", "/channels",
        params={"part": "snippet,statistics,contentDetails", "mine": "true"},
    )  # fmt: skip
    items = cast(list[JSON], found.get("items") or [])
    if not items:
        raise ToolError("this Google account has no YouTube channel")
    return items[0]


@read
async def youtube_videos(
    target: Target,
    limit: Limit = 25,
    page_token: Annotated[str | None, Field(max_length=200)] = None,
) -> JSON:
    """The channel's videos, newest first, with their privacy and numbers."""
    channel = await youtube_channel.handler(target)
    details = cast(JSON, channel.get("contentDetails") or {})
    uploads = cast(JSON, details.get("relatedPlaylists") or {}).get("uploads")
    listed = await _call(
        target, YOUTUBE, "GET", "/playlistItems",
        params={"part": "contentDetails", "playlistId": uploads, "maxResults": limit,
                "pageToken": page_token},
    )  # fmt: skip
    items = cast(list[JSON], listed.get("items") or [])
    ids = [str(cast(JSON, i.get("contentDetails") or {}).get("videoId")) for i in items]
    if not ids:
        return {"videos": []}
    found = await _call(
        target, YOUTUBE, "GET", "/videos",
        params={"part": "snippet,status,statistics", "id": ",".join(ids)},
    )  # fmt: skip
    return {"videos": found.get("items") or [], "next_page": listed.get("nextPageToken")}


@read
async def youtube_comments(
    target: Target,
    video_id: VideoId,
    limit: Limit = 25,
    page_token: Annotated[str | None, Field(max_length=200)] = None,
) -> JSON:
    """A video's comments and their replies."""
    _need(target, "youtube_read", "youtube_manage")
    found = await _call(
        target, YOUTUBE, "GET", "/commentThreads",
        params={"part": "snippet,replies", "videoId": video_id, "maxResults": limit,
                "pageToken": page_token, "textFormat": "plainText"},
    )  # fmt: skip
    return {"comments": found.get("items") or [], "next_page": found.get("nextPageToken")}


@read
async def youtube_report(
    target: Target,
    start: Annotated[str, Field(pattern=r"^\d{4}-\d{2}-\d{2}$")],
    end: Annotated[str, Field(pattern=r"^\d{4}-\d{2}-\d{2}$")],
    metrics: Annotated[str, Field(max_length=300, description="Like views,likes,comments.")],
    dimensions: Annotated[str | None, Field(max_length=100, description="Like day.")] = None,
) -> JSON:
    """The channel's numbers between two dates."""
    _need(target, "youtube_analytics")
    return await _call(
        target, YOUTUBE_ANALYTICS, "GET", "/reports",
        params={"ids": "channel==MINE", "startDate": start, "endDate": end, "metrics": metrics,
                "dimensions": dimensions},
    )  # fmt: skip


@final(_video_why)
async def upload_video(
    target: Target,
    file: Annotated[str, Field(min_length=1, max_length=500, description="In its folder.")],
    title: Title,
    made_for_kids: Annotated[bool, Field(description="YouTube requires an answer.")],
    description: Description = "",
    tags: Tags | None = None,
    privacy: Privacy = "private",
    publish_at: PublishAt = None,
) -> JSON:
    """Upload a video from the project's folder. It's private unless you say otherwise."""
    _need(target, "youtube_upload", "youtube_manage")
    _check_schedule(privacy, publish_at)
    path = _project_video(target, file)
    size = path.stat().st_size
    status: JSON = {"privacyStatus": privacy, "selfDeclaredMadeForKids": made_for_kids}
    if publish_at:
        status["publishAt"] = publish_at
    meta: JSON = {
        "snippet": {"title": title, "description": description, "tags": tags or []},
        "status": status,
    }
    kind = mimetypes.guess_type(path.name)[0] or "video/*"
    try:
        async with httpx2.AsyncClient(
            headers={"Authorization": f"Bearer {access_token(target)}"},
            timeout=UPLOAD_TIMEOUT, transport=transport,
        ) as http:  # fmt: skip
            opened = await http.post(
                YOUTUBE_UPLOAD, params={"uploadType": "resumable", "part": "snippet,status"},
                json=meta,
                headers={"X-Upload-Content-Length": str(size), "X-Upload-Content-Type": kind},
            )  # fmt: skip
            if opened.status_code >= 400:
                raise ToolError(f"YouTube refused the upload: {rest.error_message(opened)}")
            found = await _send_parts(http, opened.headers["Location"], path, size)
    except httpx2.HTTPError as error:
        raise ToolError(f"cannot reach YouTube: {type(error).__name__}") from None
    return {"video_id": found.get("id"), "status": found.get("status")}


async def _video(target: Target, video_id: str, part: str) -> JSON:
    found = await _call(target, YOUTUBE, "GET", "/videos", params={"part": part, "id": video_id})
    items = cast(list[JSON], found.get("items") or [])
    if not items:
        raise ToolError(f"there's no video {video_id} on this channel")
    return items[0]


@final(EDITS_VIDEO)
async def edit_video(
    target: Target,
    video_id: VideoId,
    title: Title | None = None,
    description: Description | None = None,
    tags: Tags | None = None,
) -> JSON:
    """Change a video's title, description or tags. What isn't named stays as it is."""
    _need(target, "youtube_manage")
    snippet = cast(JSON, (await _video(target, video_id, "snippet")).get("snippet") or {})
    changed = {"title": title, "description": description, "tags": tags}
    kept = ("title", "description", "tags", "categoryId", "defaultLanguage")
    merged = {k: snippet[k] for k in kept if k in snippet}
    merged |= {k: v for k, v in changed.items() if v is not None}
    return await _call(
        target, YOUTUBE, "PUT", "/videos", params={"part": "snippet"},
        json={"id": video_id, "snippet": merged},
    )  # fmt: skip


@final(_video_why)
async def set_video_privacy(
    target: Target,
    video_id: VideoId,
    privacy: Privacy,
    publish_at: PublishAt = None,
) -> JSON:
    """Make a video private, unlisted or public, or set when a private one goes public."""
    _need(target, "youtube_manage")
    _check_schedule(privacy, publish_at)
    status = cast(JSON, (await _video(target, video_id, "status")).get("status") or {})
    kept = ("embeddable", "license", "publicStatsViewable", "selfDeclaredMadeForKids")
    sent: JSON = {k: status[k] for k in kept if k in status} | {"privacyStatus": privacy}
    if publish_at:
        sent["publishAt"] = publish_at
    return await _call(
        target, YOUTUBE, "PUT", "/videos", params={"part": "status"},
        json={"id": video_id, "status": sent},
    )  # fmt: skip


@final("deletes a video for good")
async def delete_video(target: Target, video_id: VideoId) -> JSON:
    """Delete a video. It can't be undone."""
    _need(target, "youtube_manage")
    await _call(target, YOUTUBE, "DELETE", "/videos", params={"id": video_id})
    return {"deleted": video_id}


@final("posts a reply in public")
async def reply_to_comment(
    target: Target,
    comment_id: CommentId,
    text: Annotated[str, Field(min_length=1, max_length=10_000)],
) -> JSON:
    """Reply to a comment, as the channel."""
    _need(target, "youtube_manage")
    return await _call(
        target, YOUTUBE, "POST", "/comments", params={"part": "snippet"},
        json={"snippet": {"parentId": comment_id, "textOriginal": text}},
    )  # fmt: skip


@final("deletes a comment for good")
async def delete_comment(target: Target, comment_id: CommentId) -> JSON:
    """Delete one of the channel's own comments or replies."""
    _need(target, "youtube_manage")
    await _call(target, YOUTUBE, "DELETE", "/comments", params={"id": comment_id})
    return {"deleted": comment_id}


# Which accesses each tool can use (any one of them).
NEEDS: dict[str, tuple[str, ...]] = {
    "search_mail": ("mail_read",),
    "read_mail": ("mail_read",),
    "read_attachment": ("mail_read",),
    "view_attachment": ("mail_read",),
    "save_attachment": ("mail_read",),
    "get_thread": ("mail_read",),
    "send_mail": ("mail_send", "mail_drafts"),
    "create_draft": ("mail_drafts",),
    "trash_mail": ("mail_trash",),
    "search_files": ("drive_files", "drive_read_all"),
    "read_file": ("drive_files", "drive_read_all"),
    "view_file": ("drive_files", "drive_read_all"),
    "save_file": ("drive_files", "drive_read_all"),
    "upload_file": ("drive_files",),
    "share_file": ("drive_files",),
    "list_events": ("calendar_read", "calendar_write"),
    "create_event": ("calendar_write",),
    "read_sheet": ("sheets_read", "sheets_write"),
    "update_sheet": ("sheets_write",),
    "append_rows": ("sheets_write",),
    "read_doc": ("docs_read", "docs_write"),
    "create_doc": ("docs_write",),
    "edit_doc": ("docs_write",),
    "list_search_sites": ("search_console_read",),
    "search_performance": ("search_console_read",),
    "list_analytics_properties": ("analytics_read",),
    "analytics_report": ("analytics_read",),
    "youtube_channel": ("youtube_read", "youtube_manage"),
    "youtube_videos": ("youtube_read", "youtube_manage"),
    "youtube_comments": ("youtube_read", "youtube_manage"),
    "youtube_report": ("youtube_analytics",),
    "upload_video": ("youtube_upload", "youtube_manage"),
    "edit_video": ("youtube_manage",),
    "set_video_privacy": ("youtube_manage",),
    "delete_video": ("youtube_manage",),
    "reply_to_comment": ("youtube_manage",),
    "delete_comment": ("youtube_manage",),
}
SCHEDULED = frozenset({"upload_video", "set_video_privacy"})


def _offered(config: JSON, tool: str) -> bool:
    """Whether an account given this access can use the tool (the others aren't listed)."""
    wanted = NEEDS.get(tool)
    given = {str(a) for a in cast(list[Any], config.get("access") or [])}
    return wanted is None or bool(set(wanted) & given)


def _asks_first(spec: ToolSpec) -> ToolSpec:
    """A write: checked for its access before it's held, so nobody approves a call that
    can only fail. (Still a write: it asks first.)"""

    def classify(target: Target, args: JSON) -> ToolKind:
        _need(target, *NEEDS[spec.name])
        if spec.name in SCHEDULED:
            _check_schedule(
                str(args.get("privacy") or "private"), cast(str | None, args.get("publish_at"))
            )
        if spec.name == "upload_video":
            _project_video(target, str(args.get("file") or ""))
        return ToolKind.WRITE

    return replace(spec, classify=classify)


ADAPTER = Adapter(
    service=InstanceType.GOOGLE,
    tools=tuple(
        _asks_first(t) if t.kind is ToolKind.WRITE else t
        for t in (
            search_mail, read_mail, read_attachment, view_attachment, save_attachment,
            get_thread, send_mail, create_draft, trash_mail,
            search_files, read_file, view_file, save_file, upload_file, share_file,
            list_events, create_event,
            read_sheet, update_sheet, append_rows,
            read_doc, create_doc, replace(edit_doc, precondition=_doc_revision),
            list_search_sites, search_performance, list_analytics_properties, analytics_report,
            youtube_channel, youtube_videos, youtube_comments, youtube_report,
            upload_video, edit_video, set_video_privacy, delete_video, reply_to_comment,
            delete_comment,
        )
    ),
    secret_fragments=secret_fragments,
    offers=_offered,
)  # fmt: skip
WRITES = tuple(t.name for t in ADAPTER.tools if t.kind is ToolKind.WRITE)
# Saving a draft: runs without asking unless the account holds drafts too (its own setting).
DRAFTS = tuple(t.name for t in ADAPTER.tools if t.hold is Hold.DRAFT)
# Writes that always ask: no per-account setting lets them run by themselves.
ALWAYS_ASKS = tuple(
    t.name for t in ADAPTER.tools if t.typed is not None and t.hold is not Hold.DRAFT
)
# Writes the owner can't set to run without asking either: a document may be someone else's.
KEEPS_ASKING = (*ALWAYS_ASKS, "edit_doc")
# Why, where there's more to say than "always asks you first" (the account's page shows it).
ASKS_WHY = {
    "send_mail": "More than 10 people at once needs the name typed.",
    "edit_doc": "A document may be someone else's.",
}
