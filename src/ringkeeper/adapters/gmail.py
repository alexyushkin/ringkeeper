"""Gmail over IMAP and SMTP with an app password (the simple option; a Google account
connection signs in with OAuth instead, adapters.google). The secret is JSON:

    {"address": "name@gmail.com", "app_password": "abcd efgh ijkl mnop"}

App passwords need 2-Step Verification; Google doesn't offer them with
Advanced Protection or security-key-only sign-in, and a Workspace admin can
turn them off. Login errors say which case it looks like.

Reading never marks mail as read (BODY.PEEK). Saving a draft only the user
sees runs without asking (unless the connection holds drafts too), and can be undone:
the draft is deleted. Sending always waits for approval, and more than
adapters.mail.BULK people at once needs the name typed. Mailboxes are found by
their role (\\Drafts, \\Sent, \\All), so a Gmail in another language
works too.
"""

import email
import email.message
import email.policy
import email.utils
import imaplib
import json
import re
import smtplib
import time
from collections.abc import Callable
from dataclasses import replace
from email.message import EmailMessage
from typing import Annotated, Any, Literal, cast
from urllib.parse import quote

import anyio.to_thread
from pydantic import Field

from ringkeeper.adapters import extract, files, mail_text
from ringkeeper.adapters.base import (
    JSON,
    Adapter,
    Target,
    ToolError,
    Undoer,
    final,
    read,
    saves,
    saves_draft,
    shows,
)
from ringkeeper.adapters.mail import ADDRESS_PATTERN, bulk_reason, delete_draft
from ringkeeper.adapters.mail_text import (
    AttachmentId,
    IncludeQuoted,
    ThreadBody,
    ThreadLimit,
    ThreadOffset,
)
from ringkeeper.adapters.paging import PAGE_CHARS, Offset, PageLimit
from ringkeeper.models import InstanceType

IMAP_HOST = "imap.gmail.com"
SMTP_HOST = "smtp.gmail.com"
TIMEOUT = 30
MAX_THREAD_MESSAGES = 200
APP_PASSWORDS = "https://myaccount.google.com/apppasswords"


# Tests swap these for fakes; production talks to Gmail.
def imap_factory() -> imaplib.IMAP4:
    return imaplib.IMAP4_SSL(IMAP_HOST, 993, timeout=TIMEOUT)


def smtp_factory() -> smtplib.SMTP:
    return smtplib.SMTP_SSL(SMTP_HOST, 465, timeout=TIMEOUT)


Address = Annotated[str, Field(pattern=ADDRESS_PATTERN)]
Addresses = Annotated[list[Address], Field(min_length=1, max_length=50)]
Mailbox = Literal["inbox", "sent", "drafts", "all"]
Uid = Annotated[int, Field(ge=1, description="The message's uid, from search_messages.")]
ROLES = {"sent": "\\Sent", "drafts": "\\Drafts", "all": "\\All"}


def credentials(value: str) -> tuple[str, str]:
    try:
        data: Any = json.loads(value)
    except ValueError:
        raise ToolError("this Gmail connection's secret isn't in the expected form") from None
    fields = cast(dict[str, Any], data) if isinstance(data, dict) else {}
    address = str(fields.get("address") or "").strip()
    password = "".join(str(fields.get("app_password") or "").split())
    if not re.fullmatch(ADDRESS_PATTERN, address) or not password:
        raise ToolError("this Gmail connection needs an address and an app password")
    return address, password


def secret_fragments(value: str) -> list[str]:
    try:
        return [credentials(value)[1]]
    except ToolError:
        return []


def login_problem(detail: str) -> str:
    """Google's refusal, in plain words."""
    if "Application-specific password required" in detail or "5.7.9" in detail:
        return (
            "Gmail wants an app password here, not your Google password. Create one at "
            f"{APP_PASSWORDS} (it needs 2-Step Verification)."
        )
    return (
        "Gmail didn't accept this address and app password. Check both; changing your "
        "Google password cancels app passwords, so you may need a new one. If your account "
        "uses Advanced Protection or only security keys, or a work admin turned app passwords "
        "off, Google doesn't allow them."
    )


def _imap(target: Target) -> imaplib.IMAP4:
    address, password = credentials(target.secret.reveal())
    try:
        conn = imap_factory()
    except OSError as error:
        raise ToolError(f"cannot reach Gmail: {type(error).__name__}") from None
    try:
        conn.login(address, password)
    except imaplib.IMAP4.error as error:
        conn.shutdown()
        raise ToolError(login_problem(str(error))) from None
    return conn


def _mailbox(conn: imaplib.IMAP4, which: str) -> str:
    if which == "inbox":
        return "INBOX"
    role = ROLES[which]
    _, lines = conn.list()
    for line in lines:
        text = line.decode() if isinstance(line, bytes) else str(line)
        match = re.match(r'\((?P<flags>[^)]*)\) "[^"]*" (?P<name>.+)$', text)
        if match and role in match["flags"].split():
            return match["name"]
    raise ToolError(f"this Gmail account has no {which} mailbox over IMAP")


def _select(conn: imaplib.IMAP4, which: str) -> None:
    status, _ = conn.select(_mailbox(conn, which), readonly=True)
    if status != "OK":
        raise ToolError(f"couldn't open the {which} mailbox")


def header_of(message: email.message.Message, name: str) -> str:
    return str(message.get(name, ""))


def _part_text(message: email.message.EmailMessage, kind: str) -> str:
    part = message.get_body(preferencelist=(kind,))
    if part is None:
        return ""
    try:
        return str(cast(EmailMessage, part).get_content())
    except (LookupError, ValueError):  # a charset nobody knows
        payload = cast(EmailMessage, part).get_payload(decode=True)
        return bytes(cast(bytes, payload) or b"").decode("utf-8", "replace")


def text_of(message: email.message.EmailMessage) -> str:
    return mail_text.best_text(_part_text(message, "plain"), _part_text(message, "html"))


def attachments_of(message: email.message.EmailMessage) -> list[JSON]:
    """What's attached: read one with read_attachment, by its id."""
    found: list[JSON] = []
    for number, part in enumerate(message.iter_attachments(), start=1):
        data = cast(bytes | None, part.get_payload(decode=True)) or b""
        found.append({
            "id": str(number),
            "name": part.get_filename() or f"attachment {number}",
            "type": part.get_content_type(),
            "size": len(data),
        })  # fmt: skip
    return found


def attachment(message: email.message.EmailMessage, attachment_id: str) -> tuple[JSON, bytes]:
    """One attachment: its name, type and size, and its bytes."""
    parts = list(message.iter_attachments())
    if not attachment_id.isdigit() or not 1 <= int(attachment_id) <= len(parts):
        raise ToolError(f"this message has no attachment {attachment_id}")
    part = parts[int(attachment_id) - 1]
    name = part.get_filename() or f"attachment {attachment_id}"
    data = cast(bytes | None, part.get_payload(decode=True)) or b""
    return {"name": name, "type": part.get_content_type(), "size": len(data)}, data


def attachment_text(
    message: email.message.EmailMessage,
    attachment_id: str,
    offset: int,
    limit: int,
    pages: str | None = None,
    inside: str | None = None,
) -> JSON:
    """The text of one attachment, in parts."""
    meta, data = attachment(message, attachment_id)
    found = extract.extract(str(meta["name"]), str(meta["type"]), data, inside)
    return files.text_answer(meta, found, offset, limit, pages, "view_attachment")


def attachment_view(
    message: email.message.EmailMessage,
    attachment_id: str,
    page: int,
    picture: int | None,
    inside: str | None,
) -> JSON:
    """One page or picture of an attachment, as an image."""
    meta, data = attachment(message, attachment_id)
    shown = extract.view(str(meta["name"]), str(meta["type"]), data, page, picture, inside)
    return files.picture_answer(meta, shown)


def attachment_file(
    message: email.message.EmailMessage, attachment_id: str, inside: str | None
) -> tuple[str, bytes]:
    """An attachment's name and bytes, or a file's inside it, to save."""
    meta, data = attachment(message, attachment_id)
    name, _kind, data = extract.opened(str(meta["name"]), str(meta["type"]), data, inside)
    return name, data


def thread_key(message: email.message.EmailMessage) -> list[str]:
    """The Message-IDs of a conversation, as far as this message knows it."""
    known = f"{message.get('References', '')} {message.get('In-Reply-To', '')}"
    known += f" {message.get('Message-ID', '')}"
    return list(dict.fromkeys(re.findall(r"<[^<>\s]+>", known)))


def thread_uids(conn: imaplib.IMAP4, ids: list[str]) -> list[bytes]:
    """Every message in the selected folder that is, or refers to, one of these."""
    found: set[bytes] = set()
    for ident in ids[:50]:
        quoted = ident.replace("\\", "").replace('"', "")
        for header in ("Message-ID", "References"):
            status, data = conn.uid("SEARCH", "HEADER", header, f'"{quoted}"')
            if status == "OK":
                found.update((data[0] or b"").split())
    return sorted(found, key=int)


def _sent_at(message: email.message.EmailMessage) -> float:
    try:
        return email.utils.parsedate_to_datetime(header_of(message, "Date")).timestamp()
    except (TypeError, ValueError):
        return 0.0


def thread_messages(
    conn: imaplib.IMAP4, uids: list[bytes], offset: int, limit: int, body_chars: int
) -> JSON:
    """A conversation, oldest message first, each with the start of its text."""
    messages: list[tuple[float, JSON]] = []
    for uid in uids[:MAX_THREAD_MESSAGES]:
        status, parts = conn.uid("FETCH", uid.decode(), "(BODY.PEEK[])")
        raw = fetched(parts) if status == "OK" else None
        if raw is None:
            continue
        message = email.message_from_bytes(raw, policy=email.policy.default)
        shown: JSON = {
            "uid": int(uid),
            **{k: header_of(message, k.title()) for k in ("from", "to", "cc", "subject", "date")},
            **mail_text.body_fields(text_of(message), 0, body_chars),
            "attachments": attachments_of(message),
        }
        messages.append((_sent_at(message), shown))
    ordered = [m for _, m in sorted(messages, key=lambda item: item[0])]
    return mail_text.thread_page(ordered, offset, limit)


def fetched(parts: list[Any]) -> bytes | None:
    """The message bytes in an IMAP FETCH answer (the (envelope, bytes) item)."""
    for part in parts:
        if isinstance(part, tuple) and len(cast(tuple[Any, ...], part)) > 1:
            body = cast(tuple[Any, ...], part)[1]
            if isinstance(body, bytes):
                return body
    return None


def _blocking(fn: Callable[[], JSON]) -> Any:
    return anyio.to_thread.run_sync(fn)


async def probe(target: Target) -> str:
    """Sign in and open the inbox (the Test button)."""

    def run() -> JSON:
        conn = _imap(target)
        try:
            _select(conn, "inbox")
        finally:
            conn.logout()
        return {}

    await _blocking(run)
    return f"Connected to Gmail as {credentials(target.secret.reveal())[0]}."


@read
async def search_messages(
    target: Target,
    query: Annotated[
        str,
        Field(
            description="Gmail search, e.g. from:anna@acme.example newer_than:7d.",
            max_length=500,
        ),
    ] = "",
    mailbox: Mailbox = "inbox",
    limit: Annotated[int, Field(ge=1, le=50)] = 20,
) -> JSON:
    """Find messages with Gmail's own search words; newest first (uid, from, to, subject, date)."""

    def run() -> JSON:
        conn = _imap(target)
        try:
            _select(conn, mailbox)
            if query:
                setattr(conn, "literal", query.encode())  # noqa: B010 (sent as a literal: any text, safely)
                status, data = conn.uid("SEARCH", "CHARSET", "UTF-8", "X-GM-RAW")
            else:
                status, data = conn.uid("SEARCH", "ALL")
            if status != "OK":
                raise ToolError("Gmail couldn't run that search")
            uids = (data[0] or b"").split()[-limit:][::-1]
            found: list[JSON] = []
            for uid in uids:
                _, parts = conn.uid(
                    "FETCH", uid.decode(), "(BODY.PEEK[HEADER.FIELDS (FROM TO SUBJECT DATE)])"
                )
                raw = fetched(parts) or b""
                headers = email.message_from_bytes(raw, policy=email.policy.default)
                found.append(
                    {"uid": int(uid)}
                    | {k: header_of(headers, k.title()) for k in ("from", "to", "subject", "date")}
                )
            return {"messages": found}
        finally:
            conn.logout()

    return await _blocking(run)


def _open(conn: imaplib.IMAP4, uid: int, where: str) -> email.message.EmailMessage:
    status, parts = conn.uid("FETCH", str(uid), "(BODY.PEEK[])")
    raw = fetched(parts) if status == "OK" else None
    if raw is None:
        raise ToolError(f"no message with uid {uid} in {where}")
    return email.message_from_bytes(raw, policy=email.policy.default)


@read
async def get_message(
    target: Target,
    uid: Uid,
    mailbox: Mailbox = "inbox",
    offset: Offset = 0,
    limit: PageLimit = PAGE_CHARS,
    include_quoted: IncludeQuoted = False,
) -> JSON:
    """Read one message: headers, its whole text and its attachments. Doesn't mark it read. A
    long message comes in parts: total_chars says how long, next_offset where the next part
    starts. Earlier messages a reply quotes are in `quoted` (include_quoted puts them in the
    body). read_attachment reads an attachment."""

    def run() -> JSON:
        conn = _imap(target)
        try:
            _select(conn, mailbox)
            message = _open(conn, uid, mailbox)
            return {
                "uid": uid,
                **{
                    k: header_of(message, k.title())
                    for k in ("from", "to", "cc", "subject", "date")
                },
                "message_id": header_of(message, "Message-ID"),
                **mail_text.body_fields(text_of(message), offset, limit, include_quoted),
                "attachments": attachments_of(message),
            }
        finally:
            conn.logout()

    return await _blocking(run)


@read
async def read_attachment(
    target: Target,
    uid: Uid,
    attachment_id: AttachmentId,
    mailbox: Mailbox = "inbox",
    pages: files.Pages = None,
    inside: files.Inside = None,
    offset: Offset = 0,
    limit: PageLimit = PAGE_CHARS,
) -> JSON:
    """Read one attachment's text: PDF, Word, Excel, PowerPoint, CSV, text, an attached email or
    a .zip (`inside` reads a file in it), and pictures where OCR is installed. A long file
    starts with its outline: read on with pages=. view_attachment shows a page as an image."""

    def run() -> JSON:
        conn = _imap(target)
        try:
            _select(conn, mailbox)
            message = _open(conn, uid, mailbox)
            return attachment_text(message, attachment_id, offset, limit, pages, inside)
        finally:
            conn.logout()

    return await _blocking(run)


@shows
async def view_attachment(
    target: Target,
    uid: Uid,
    attachment_id: AttachmentId,
    mailbox: Mailbox = "inbox",
    page: files.PageNumber = 1,
    picture: files.PictureNumber = None,
    inside: files.Inside = None,
) -> JSON:
    """Look at one page of an attached PDF, or a picture (attached, or on a slide or in a Word
    document), as an image: better than its text for a table, a chart or a scan."""

    def run() -> JSON:
        conn = _imap(target)
        try:
            _select(conn, mailbox)
            message = _open(conn, uid, mailbox)
            return attachment_view(message, attachment_id, page, picture, inside)
        finally:
            conn.logout()

    return await _blocking(run)


@saves
async def save_attachment(
    target: Target,
    uid: Uid,
    attachment_id: AttachmentId,
    mailbox: Mailbox = "inbox",
    inside: files.Inside = None,
) -> JSON:
    """Save a copy of an attachment in the project's folder (attachments/), to work on it there.
    Answers its path. Nothing changes in the mailbox."""

    def run() -> tuple[str, bytes]:
        conn = _imap(target)
        try:
            _select(conn, mailbox)
            return attachment_file(_open(conn, uid, mailbox), attachment_id, inside)
        finally:
            conn.logout()

    name, data = await anyio.to_thread.run_sync(run)
    return await files.save(target.project, name, data)


@read
async def get_thread(
    target: Target,
    uid: Uid,
    mailbox: Mailbox = "inbox",
    offset: ThreadOffset = 0,
    limit: ThreadLimit = 20,
    body_chars: ThreadBody = mail_text.THREAD_BODY_CHARS,
) -> JSON:
    """Read the conversation a message belongs to, oldest message first. Each message shows the
    start of its text, with total_chars: get_message reads the rest of a long one. Only
    messages in this mailbox are found."""

    def run() -> JSON:
        conn = _imap(target)
        try:
            _select(conn, mailbox)
            uids = thread_uids(conn, thread_key(_open(conn, uid, mailbox)))
            return thread_messages(conn, uids, offset, limit, body_chars)
        finally:
            conn.logout()

    return await _blocking(run)


def compose(
    sender: str,
    to: list[str],
    subject: str,
    body: str,
    cc: list[str] | None,
    in_reply_to: str | None,
) -> EmailMessage:
    message = EmailMessage()
    message["From"] = sender
    message["To"] = ", ".join(to)
    if cc:
        message["Cc"] = ", ".join(cc)
    message["Subject"] = subject
    if in_reply_to:
        message["In-Reply-To"] = in_reply_to
        message["References"] = in_reply_to
    message.set_content(body)
    return message


Subject = Annotated[str, Field(max_length=500)]
Body = Annotated[str, Field(max_length=100_000)]
ReplyTo = Annotated[
    str | None, Field(description="message_id of the message this answers.", max_length=998)
]


@saves_draft
async def create_draft(
    target: Target,
    to: Addresses,
    subject: Subject,
    body: Body,
    cc: list[Address] | None = None,
    in_reply_to: ReplyTo = None,
) -> JSON:
    """Save an email as a Gmail draft for the user to check and send. Nothing is sent."""

    def run() -> JSON:
        conn = _imap(target)
        try:
            sender = credentials(target.secret.reveal())[0]
            message = compose(sender, to, subject, body, cc, in_reply_to)
            message["Message-ID"] = email.utils.make_msgid()
            status, _ = conn.append(
                _mailbox(conn, "drafts"), "(\\Draft)", imaplib.Time2Internaldate(time.time()),
                message.as_bytes(),
            )  # fmt: skip
            if status != "OK":
                raise ToolError("Gmail didn't save the draft")
            made = str(message["Message-ID"])
            return {
                "saved": "draft", "to": to, "subject": subject, "message_id": made,
                "link": draft_link(sender, made),
            }  # fmt: skip
        finally:
            conn.logout()

    return await _blocking(run)


@final(bulk_reason)
async def send_email(
    target: Target,
    to: Addresses,
    subject: Subject,
    body: Body,
    cc: list[Address] | None = None,
    in_reply_to: ReplyTo = None,
) -> JSON:
    """Send an email from this Gmail account. Always waits for the user's approval."""

    def run() -> JSON:
        address, password = credentials(target.secret.reveal())
        message = compose(address, to, subject, body, cc, in_reply_to)
        try:
            with smtp_factory() as smtp:
                smtp.login(address, password)
                smtp.send_message(message)
        except smtplib.SMTPAuthenticationError as error:
            raise ToolError(login_problem(f"{error.smtp_code} {error.smtp_error!r}")) from None
        except (smtplib.SMTPException, OSError) as error:
            raise ToolError(f"Gmail didn't send it: {type(error).__name__}") from None
        return {"sent": True, "to": to, "subject": subject}

    return await _blocking(run)


def draft_link(address: str, message_id: str) -> str:
    """Where the draft opens in Gmail on the web."""
    return (
        f"https://mail.google.com/mail/?authuser={quote(address)}"
        f"#search/rfc822msgid:{quote(message_id.strip('<>'))}"
    )


class DraftUndo(Undoer):
    """Taking back a saved draft deletes it."""

    async def before(self, target: Target, args: JSON) -> JSON:
        return {"message_id": None, "subject": args.get("subject")}

    async def after(self, target: Target, args: JSON, before: JSON, result: JSON) -> JSON:
        before["message_id"] = result.get("message_id")
        return {"message_id": before["message_id"]}

    async def current(self, target: Target, kept: JSON) -> JSON:
        return {"message_id": kept.get("message_id")}

    async def restore(self, target: Target, kept: JSON) -> JSON:
        def run() -> JSON:
            conn = _imap(target)
            try:
                gone = delete_draft(conn, _mailbox(conn, "drafts"), str(kept["message_id"]))
                return {"deleted": gone}
            finally:
                conn.logout()

        return await _blocking(run)

    def describe(self, kept: JSON) -> str:
        return f"Deletes the draft it saved ({kept.get('subject') or 'no subject'})."


create_draft = replace(create_draft, undo=DraftUndo())


ADAPTER = Adapter(
    service=InstanceType.GMAIL,
    tools=(
        search_messages, get_message, read_attachment, view_attachment, save_attachment,
        get_thread, create_draft, send_email,
    ),
    secret_fragments=secret_fragments,
)  # fmt: skip
