"""Any mailbox over IMAP and SMTP. The secret is JSON:

    {"preset": "zoho", "address": "me@example.com", "password": "...", "test_address": ""}

`preset` fills in the servers; "custom" gives them: imap_host, imap_port, smtp_host,
smtp_port, smtp_security. Most providers want an app password here.

Microsoft: Exchange Online turned password sign-in off for IMAP, so a work or school
Microsoft 365 mailbox can't be read this way (the login error says so).

Reading runs and never marks mail as read. A draft, moving a message and sending wait
for approval; many recipients at once and deleting need the name typed. Folders are
found by their role (\\Drafts, \\Sent, \\Trash), so any language works.
"""

import email
import email.policy
import imaplib
import json
import re
import socket
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, Any, Literal, cast

import anyio.to_thread
from pydantic import Field

from ringkeeper import netpolicy
from ringkeeper.adapters import mail, smtp
from ringkeeper.adapters.base import (
    JSON,
    Adapter,
    Target,
    ToolError,
    draft,
    final,
    held,
    read,
)
from ringkeeper.adapters.gmail import ADDRESS_PATTERN, fetched, header_of, text_of
from ringkeeper.models import InstanceType

TIMEOUT = 30
MAX_BODY_CHARS = 20_000
IMAP_PORT = 993


@dataclass(frozen=True)
class Preset:
    imap: str
    smtp: str
    smtp_port: int = 465
    security: str = "ssl"
    help: str = ""


PRESETS = {
    "gmail": Preset("imap.gmail.com", "smtp.gmail.com",
                    help="Gmail wants an app password: myaccount.google.com/apppasswords."),
    "outlook": Preset("outlook.office365.com", "smtp.office365.com", 587, "starttls",
                      help="Microsoft has turned password sign-in off for IMAP on Microsoft 365, "
                      "and is doing the same for SMTP. A mailbox that needs Microsoft's own "
                      "sign-in can't connect this way yet."),
    "zoho": Preset("imap.zoho.com", "smtp.zoho.com",
                   help="Zoho wants an app password if two-factor sign-in is on, and IMAP "
                   "turned on in Mail settings."),
    "yahoo": Preset("imap.mail.yahoo.com", "smtp.mail.yahoo.com",
                    help="Yahoo wants an app password: Account security > Generate app password."),
}  # fmt: skip
Folder = Annotated[str, Field(min_length=1, max_length=200, description="From list_folders.")]
Uid = Annotated[int, Field(ge=1, description="The message's uid, from search_messages.")]
Date = Annotated[str, Field(pattern=r"^\d{4}-\d{2}-\d{2}$", description="YYYY-MM-DD.")]
Term = Annotated[str | None, Field(max_length=200)]
Role = Literal["drafts", "sent", "trash"]
ROLES: dict[Role, str] = {"drafts": "\\Drafts", "sent": "\\Sent", "trash": "\\Trash"}
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def settings(value: str) -> JSON:
    """The stored JSON with its servers filled in; raises ToolError if it can't be used."""
    try:
        data: Any = json.loads(value)
    except ValueError:
        raise ToolError("this mailbox's secret isn't in the expected form") from None
    fields = cast(JSON, data) if isinstance(data, dict) else {}
    address = str(fields.get("address") or "").strip()
    preset = PRESETS.get(str(fields.get("preset") or "custom"))
    password = str(fields.get("password") or "")
    if preset is not None:  # an app password is often shown in groups, and pasted that way
        password = "".join(password.split())
    if not re.fullmatch(ADDRESS_PATTERN, address) or not password:
        raise ToolError("this mailbox needs an address and a password")
    imap_host = preset.imap if preset else str(fields.get("imap_host") or "").strip()
    smtp_host = preset.smtp if preset else str(fields.get("smtp_host") or "").strip()
    if not imap_host or not smtp_host:
        raise ToolError("this mailbox needs its IMAP and SMTP servers")
    security = preset.security if preset else str(fields.get("smtp_security") or "ssl")
    port = preset.smtp_port if preset else fields.get("smtp_port") or None
    username = str(fields.get("username") or "").strip() or address
    sending = {"host": smtp_host, "port": port, "security": security, "username": username,
               "password": password, "from_address": address}  # fmt: skip
    return {
        **fields, "address": address, "password": password, "username": username,
        "imap_host": imap_host, "imap_port": int(fields.get("imap_port") or IMAP_PORT),
        "smtp": smtp.settings(json.dumps(sending)),
    }  # fmt: skip


def secret_fragments(value: str) -> list[str]:
    try:
        return [str(settings(value)["password"])]
    except ToolError:
        return []


# Tests swap this for a fake; production talks to the server.
def imap_factory(host: str, port: int) -> imaplib.IMAP4:
    return imaplib.IMAP4_SSL(host, port, timeout=TIMEOUT)


def login_problem(fields: JSON) -> str:
    preset = PRESETS.get(str(fields.get("preset") or ""))
    extra = f" {preset.help}" if preset and preset.help else ""
    return f"{fields['imap_host']} didn't accept this address and password.{extra}"


def _imap(target: Target) -> tuple[imaplib.IMAP4, JSON]:
    fields = settings(target.secret.reveal())
    netpolicy.check_host(str(fields["imap_host"]))
    try:
        conn = imap_factory(fields["imap_host"], fields["imap_port"])
    except socket.gaierror:
        raise ToolError(f"there's no server called {fields['imap_host']}") from None
    except OSError as error:
        raise ToolError(f"cannot reach {fields['imap_host']}: {type(error).__name__}") from None
    try:
        conn.login(fields["username"], fields["password"])
    except imaplib.IMAP4.error:
        conn.shutdown()
        raise ToolError(login_problem(fields)) from None
    return conn, fields


def _folders(conn: imaplib.IMAP4) -> list[tuple[str, list[str]]]:
    """(name as the server gives it, its flags) for every folder."""
    _, lines = conn.list()
    found: list[tuple[str, list[str]]] = []
    for line in lines:
        text = line.decode(errors="replace") if isinstance(line, bytes) else str(line)
        match = re.match(r'\((?P<flags>[^)]*)\) (?:"[^"]*"|NIL) (?P<name>.+)$', text)
        if match:
            found.append((match["name"].strip(), match["flags"].split()))
    return found


def _by_role(conn: imaplib.IMAP4, role: Role) -> str | None:
    folders = _folders(conn)
    by_name = re.compile(rf'^"?(.*[/.])?{role}"?$', re.IGNORECASE)
    return next((n for n, flags in folders if ROLES[role] in flags), None) or next(
        (n for n, _ in folders if by_name.match(n)), None
    )


def _quoted(name: str) -> str:
    return name if name.startswith('"') else '"' + name.replace('"', '\\"') + '"'


def _select(conn: imaplib.IMAP4, folder: str, *, write: bool = False) -> None:
    status, _ = conn.select(_quoted(folder), readonly=not write)
    if status != "OK":
        raise ToolError(f"couldn't open the folder {folder}")


def _blocking(fn: Callable[[], JSON]) -> Any:
    return anyio.to_thread.run_sync(fn)


def _session(target: Target, work: Callable[[imaplib.IMAP4, JSON], JSON]) -> Any:
    def run() -> JSON:
        conn, fields = _imap(target)
        try:
            return work(conn, fields)
        finally:
            conn.logout()

    return _blocking(run)


async def probe(target: Target) -> str:
    """Sign in to both servers and open the inbox (the Test button). Sends nothing."""

    def work(conn: imaplib.IMAP4, fields: JSON) -> JSON:
        _select(conn, "INBOX")
        return fields

    fields = await _session(target, work)

    def check_sending() -> JSON:
        smtp.with_server(fields["smtp"], lambda _s: None, "cannot reach the sending server")
        return {}

    await _blocking(check_sending)
    return f"Connected to {fields['address']}: reading and sending both work."


def _term(value: str) -> str:
    return '"' + " ".join(value.split()).replace("\\", "\\\\").replace('"', '\\"') + '"'


def criteria(
    conn: imaplib.IMAP4, text: str | None, sender: str | None, subject: str | None,
    since: str | None, unread: bool,
) -> tuple[list[str], bool]:  # fmt: skip
    """IMAP SEARCH words, and whether one goes as a literal (it isn't plain ASCII)."""
    found: list[str] = ["UNSEEN"] if unread else []
    if since:
        year, month, day = since.split("-")
        found += ["SINCE", f"{int(day)}-{MONTHS[int(month) - 1]}-{year}"]
    terms = [(k, v) for k, v in (("TEXT", text), ("FROM", sender), ("SUBJECT", subject)) if v]
    wide = [(key, value) for key, value in terms if not value.isascii()]
    if len(wide) > 1:
        raise ToolError("search one field at a time when it isn't in plain English letters")
    for key, value in terms:
        if value.isascii():
            found += [key, _term(value)]
    if wide:
        setattr(conn, "literal", wide[0][1].encode())  # noqa: B010 (sent as a literal: any text)
        found.append(wide[0][0])
    return found or ["ALL"], bool(wide)


@read
async def list_folders(target: Target) -> JSON:
    """The mailbox's folders, and which ones hold drafts, sent mail and the trash."""

    def work(conn: imaplib.IMAP4, _fields: JSON) -> JSON:
        roles = {role: (_by_role(conn, role) or "").strip('"') or None for role in ROLES}
        return {"folders": [name.strip('"') for name, _ in _folders(conn)], **roles}

    return await _session(target, work)


@read
async def search_messages(
    target: Target,
    text: Term = None,
    sender: Term = None,
    subject: Term = None,
    since: Date | None = None,
    unread: bool = False,
    folder: Folder = "INBOX",
    limit: Annotated[int, Field(ge=1, le=50)] = 20,
) -> JSON:
    """Find messages; newest first (uid, from, to, subject, date). Doesn't mark them read."""

    def work(conn: imaplib.IMAP4, _fields: JSON) -> JSON:
        _select(conn, folder)
        words, wide = criteria(conn, text, sender, subject, since, unread)
        status, data = conn.uid("SEARCH", *(("CHARSET", "UTF-8") if wide else ()), *words)
        if status != "OK":
            raise ToolError("the server couldn't run that search")
        found: list[JSON] = []
        for uid in (data[0] or b"").split()[-limit:][::-1]:
            _, parts = conn.uid(
                "FETCH", uid.decode(), "(BODY.PEEK[HEADER.FIELDS (FROM TO SUBJECT DATE)])"
            )
            headers = email.message_from_bytes(fetched(parts) or b"", policy=email.policy.default)
            found.append(
                {"uid": int(uid)}
                | {k: header_of(headers, k.title()) for k in ("from", "to", "subject", "date")}
            )
        return {"messages": found}

    return await _session(target, work)


@read
async def get_message(target: Target, uid: Uid, folder: Folder = "INBOX") -> JSON:
    """Read one message: headers, its text, and attachment names. Doesn't mark it read."""

    def work(conn: imaplib.IMAP4, _fields: JSON) -> JSON:
        _select(conn, folder)
        status, parts = conn.uid("FETCH", str(uid), "(BODY.PEEK[])")
        raw = fetched(parts) if status == "OK" else None
        if raw is None:
            raise ToolError(f"no message with uid {uid} in {folder}")
        message = email.message_from_bytes(raw, policy=email.policy.default)
        body = text_of(message)
        return {
            "uid": uid,
            **{k: header_of(message, k.title()) for k in ("from", "to", "cc", "subject", "date")},
            "message_id": header_of(message, "Message-ID"),
            "body": body[:MAX_BODY_CHARS],
            "truncated": len(body) > MAX_BODY_CHARS,
            "attachments": [
                name for part in message.iter_attachments() if (name := part.get_filename())
            ],
        }

    return await _session(target, work)


ReplyTo = Annotated[
    str | None, Field(description="message_id of the message this answers.", max_length=998)
]


@held
async def create_draft(
    target: Target,
    to: mail.Recipients,
    subject: mail.Subject,
    body: mail.Body,
    cc: mail.MoreRecipients = None,
    in_reply_to: ReplyTo = None,
) -> JSON:
    """Save an email as a draft for the user to check and send. Waits for approval; nothing
    is sent."""

    def work(conn: imaplib.IMAP4, fields: JSON) -> JSON:
        drafts = _by_role(conn, "drafts")
        if drafts is None:
            raise ToolError("this mailbox has no drafts folder over IMAP")
        message = mail.message(fields["address"], to, subject, body, cc=cc, in_reply_to=in_reply_to)
        status, _ = conn.append(
            _quoted(drafts), "(\\Draft)", imaplib.Time2Internaldate(time.time()),
            message.as_bytes(),
        )  # fmt: skip
        if status != "OK":
            raise ToolError("the server didn't save the draft")
        return {"saved": "draft", "to": to, "subject": subject}

    return await _session(target, work)


async def _send(target: Target, to: list[str], **parts: Any) -> JSON:
    fields = settings(target.secret.reveal())
    built = mail.message(fields["address"], to, **parts)

    def run() -> JSON:
        smtp.with_server(fields["smtp"], lambda s: s.send_message(built), "it wasn't sent")
        return {"sent": True, "to": to, "subject": parts["subject"]}

    return await _blocking(run)


@final(mail.bulk_reason)
async def send_email(
    target: Target,
    to: mail.Recipients,
    subject: mail.Subject,
    body: mail.Body,
    cc: mail.MoreRecipients = None,
    bcc: mail.MoreRecipients = None,
    in_reply_to: ReplyTo = None,
) -> JSON:
    """Send an email from this mailbox. Always waits for the user's approval; to more than
    10 people at once, they also have to type the connection's name."""
    return await _send(
        target, to, subject=subject, body=body, cc=cc, bcc=bcc, in_reply_to=in_reply_to
    )


@draft
async def send_test_email(target: Target, subject: mail.Subject, body: mail.Body) -> JSON:
    """Send a test to the mailbox's own test address, set by the user. It runs without
    asking, and can't go anywhere else."""
    to = mail.own_test_address(settings(target.secret.reveal()))
    return await _send(target, [to], subject=f"[Test] {subject}", body=body)


def _move(conn: imaplib.IMAP4, uid: int, destination: str) -> None:
    """MOVE where the server has it; else copy, then mark the original deleted."""
    if "MOVE" in conn.capabilities:
        status, _ = conn.uid("MOVE", str(uid), _quoted(destination))
    else:
        status, _ = conn.uid("COPY", str(uid), _quoted(destination))
        if status == "OK":
            status, _ = conn.uid("STORE", str(uid), "+FLAGS", "(\\Deleted)")
    if status != "OK":
        raise ToolError(f"the server couldn't move message {uid}")


@held
async def move_message(
    target: Target, uid: Uid, to_folder: Folder, folder: Folder = "INBOX"
) -> JSON:
    """Move a message to another folder. Waits for approval."""

    def work(conn: imaplib.IMAP4, _fields: JSON) -> JSON:
        _select(conn, folder, write=True)
        _move(conn, uid, to_folder)
        return {"moved": uid, "to": to_folder}

    return await _session(target, work)


@final("deletes an email")
async def delete_message(target: Target, uid: Uid, folder: Folder = "INBOX") -> JSON:
    """Move a message to the trash. Waits for approval, with the connection's name typed."""

    def work(conn: imaplib.IMAP4, _fields: JSON) -> JSON:
        trash = _by_role(conn, "trash")
        if trash is None:
            raise ToolError("this mailbox has no trash folder over IMAP, so nothing was deleted")
        _select(conn, folder, write=True)
        _move(conn, uid, trash)
        return {"deleted": uid, "in": trash.strip('"')}

    return await _session(target, work)


ADAPTER = Adapter(
    service=InstanceType.MAILBOX,
    tools=(
        list_folders, search_messages, get_message, create_draft, send_email, send_test_email,
        move_message, delete_message,
    ),
    secret_fragments=secret_fragments,
)  # fmt: skip
