"""Google accounts (Gmail, Drive, Calendar, Sheets) through the owner's own Google
app. The secret is JSON from services.google_oauth (the refresh and access
tokens); the gateway refreshes it before each call. The connection's config
says which account it is (`email`) and what it may be used for (`access`).

Every write waits for the user's approval by default, even on test data:
sending, sharing, invites, edits, uploads, drafts, the trash. The owner can
let a tool run without asking, per connection (`write_rules`, see
services.calls). Reading runs.

A tool whose access the connection wasn't given says so instead of calling
Google: the owner adds it by signing in again with more ticked.
"""

import base64
import json
import re
from dataclasses import replace
from email.message import EmailMessage
from typing import Annotated, Any, Literal, cast
from urllib.parse import quote

import httpx2
from pydantic import Field

from ringkeeper.adapters import rest
from ringkeeper.adapters.base import (
    JSON,
    Adapter,
    Target,
    ToolError,
    ToolKind,
    ToolSpec,
    always_held,
    read,
)
from ringkeeper.models import InstanceType

GMAIL = "https://gmail.googleapis.com/gmail/v1/users/me"
DRIVE = "https://www.googleapis.com/drive/v3"
UPLOAD = "https://www.googleapis.com/upload/drive/v3/files"
CALENDAR = "https://www.googleapis.com/calendar/v3"
SHEETS = "https://sheets.googleapis.com/v4/spreadsheets"
USERINFO = "https://openidconnect.googleapis.com/v1/userinfo"
MAX_TEXT_CHARS = 20_000
# Tests swap in httpx2.MockTransport; production talks to Google.
transport: httpx2.AsyncBaseTransport | None = None

ADDRESS_PATTERN = r"^[^@\s<>\"]+@[^@\s<>\"]+\.[^@\s<>\"]+$"
ID_PATTERN = r"^[A-Za-z0-9_\-]{6,200}$"
Address = Annotated[str, Field(pattern=ADDRESS_PATTERN)]
Addresses = Annotated[list[Address], Field(min_length=1, max_length=50)]
MessageId = Annotated[
    str, Field(description="The message id, from search_mail.", pattern=ID_PATTERN)
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
    me = await _call(target, USERINFO, "GET", "")
    allowed = ", ".join(config_access(target)) or "nothing"
    return f"Connected to Google as {me.get('email')} (allowed: {allowed})."


# --- Gmail ---


def _headers(payload: JSON) -> dict[str, str]:
    return {
        str(h.get("name", "")).lower(): str(h.get("value", ""))
        for h in cast(list[JSON], payload.get("headers") or [])
    }


def _decode(data: str) -> str:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", "replace")


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
    if "text/plain" in found:
        return found["text/plain"].strip()
    html = re.sub(r"<[^>]+>", " ", found.get("text/html", ""))
    return re.sub(r"[ \t]+", " ", html).strip()


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
    """Find messages with Gmail's search words, newest first (id, from, to, subject, date)."""
    _need(target, "mail_read")
    listed = await _call(
        target, GMAIL, "GET", "/messages", params={"q": query or None, "maxResults": limit}
    )
    found: list[JSON] = []
    for item in cast(list[JSON], listed.get("messages") or [])[:limit]:
        message = await _call(
            target, GMAIL, "GET", f"/messages/{item['id']}",
            params={"format": "metadata", "metadataHeaders": ["From", "To", "Subject", "Date"]},
        )  # fmt: skip
        headers = _headers(cast(JSON, message.get("payload") or {}))
        found.append(
            {"id": item["id"], "thread_id": message.get("threadId")}
            | {k: headers.get(k, "") for k in ("from", "to", "subject", "date")}
            | {"snippet": message.get("snippet", "")}
        )
    return {"account": target.config.get("email"), "messages": found}


@read
async def read_mail(target: Target, message_id: MessageId) -> JSON:
    """Read one message: headers, its text, and attachment names. Doesn't mark it read."""
    _need(target, "mail_read")
    message = await _call(
        target, GMAIL, "GET", f"/messages/{message_id}", params={"format": "full"}
    )
    payload = cast(JSON, message.get("payload") or {})
    headers = _headers(payload)
    body = _text(payload)
    attachments: list[str] = []

    def walk(part: JSON) -> None:
        if part.get("filename"):
            attachments.append(str(part["filename"]))
        for child in cast(list[JSON], part.get("parts") or []):
            walk(child)

    walk(payload)
    return {
        "id": message_id,
        "thread_id": message.get("threadId"),
        **{k: headers.get(k, "") for k in ("from", "to", "cc", "subject", "date")},
        "body": body[:MAX_TEXT_CHARS],
        "truncated": len(body) > MAX_TEXT_CHARS,
        "attachments": attachments,
    }


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


@always_held
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


@always_held
async def create_draft(
    target: Target,
    to: Addresses,
    subject: Subject,
    body: Body,
    cc: list[Address] | None = None,
    reply_to: ReplyTo = None,
) -> JSON:
    """Save an email as a Gmail draft for the user to check and send. Nothing is sent."""
    _need(target, "mail_drafts")
    message = await _compose(target, to, subject, body, cc, reply_to)
    saved = await _call(target, GMAIL, "POST", "/drafts", json={"message": message})
    return {"saved": "draft", "id": saved.get("id"), "to": to, "subject": subject}


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


@read
async def read_file(target: Target, file_id: FileId) -> JSON:
    """Read a file's text: Docs and Slides as text, Sheets as CSV, text files as they are."""
    _need(target, "drive_files", "drive_read_all")
    meta = await _call(
        target, DRIVE, "GET", f"/files/{file_id}", params={"fields": "id,name,mimeType"}
    )
    mime = str(meta.get("mimeType") or "")
    if mime in EXPORTS:
        path, params = f"/files/{file_id}/export", {"mimeType": EXPORTS[mime]}
    elif mime.startswith("text/") or mime in ("application/json", "application/csv"):
        path, params = f"/files/{file_id}", {"alt": "media"}
    else:
        raise ToolError(f"'{meta.get('name')}' is a {mime} file; only text can be read here")
    try:
        async with _client(target, DRIVE) as http:
            response = await http.get(path, params=params)
    except httpx2.HTTPError as error:
        raise ToolError(f"cannot reach Google: {type(error).__name__}") from None
    if response.status_code >= 400:
        raise ToolError(f"Google returned {response.status_code}: {rest.error_message(response)}")
    text = response.text
    return {
        "id": file_id, "name": meta.get("name"), "type": mime,
        "text": text[:MAX_TEXT_CHARS], "truncated": len(text) > MAX_TEXT_CHARS,
    }  # fmt: skip


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


def secret_fragments(value: str) -> list[str]:
    try:
        data: Any = json.loads(value)
    except ValueError:
        return []
    if not isinstance(data, dict):
        return []
    fields = cast(dict[str, Any], data)
    return [str(fields[k]) for k in ("refresh_token", "access_token") if fields.get(k)]


# Which accesses each tool can use (any one of them).
NEEDS: dict[str, tuple[str, ...]] = {
    "search_mail": ("mail_read",),
    "read_mail": ("mail_read",),
    "send_mail": ("mail_send", "mail_drafts"),
    "create_draft": ("mail_drafts",),
    "trash_mail": ("mail_trash",),
    "search_files": ("drive_files", "drive_read_all"),
    "read_file": ("drive_files", "drive_read_all"),
    "upload_file": ("drive_files",),
    "share_file": ("drive_files",),
    "list_events": ("calendar_read", "calendar_write"),
    "create_event": ("calendar_write",),
    "read_sheet": ("sheets_read", "sheets_write"),
    "update_sheet": ("sheets_write",),
    "append_rows": ("sheets_write",),
}


def _asks_first(spec: ToolSpec) -> ToolSpec:
    """A write: checked for its access before it's held, so nobody approves a call that
    can only fail. (Still a write: it asks first.)"""

    def classify(target: Target, _args: JSON) -> ToolKind:
        _need(target, *NEEDS[spec.name])
        return ToolKind.WRITE

    return replace(spec, classify=classify)


ADAPTER = Adapter(
    service=InstanceType.GOOGLE,
    tools=tuple(
        _asks_first(t) if t.kind is ToolKind.WRITE else t
        for t in (
            search_mail, read_mail, send_mail, create_draft, trash_mail,
            search_files, read_file, upload_file, share_file,
            list_events, create_event,
            read_sheet, update_sheet, append_rows,
        )
    ),
    secret_fragments=secret_fragments,
)  # fmt: skip
WRITES = tuple(t.name for t in ADAPTER.tools if t.kind is ToolKind.WRITE)
