"""Gmail over IMAP and SMTP with an app password (no Google OAuth in v1). The secret is JSON:

    {"address": "name@gmail.com", "app_password": "abcd efgh ijkl mnop"}

App passwords need 2-Step Verification; Google doesn't offer them with
Advanced Protection or security-key-only sign-in, and a Workspace admin can
turn them off. Login errors say which case it looks like.

Reading never marks mail as read (BODY.PEEK). Saving a draft only the user
sees runs without asking; sending always waits for approval. Mailboxes are
found by their role (\\Drafts, \\Sent, \\All), so a Gmail in another language
works too.
"""

import email
import email.message
import email.policy
import imaplib
import json
import re
import smtplib
import time
from collections.abc import Callable
from email.message import EmailMessage
from typing import Annotated, Any, Literal, cast

import anyio.to_thread
from pydantic import Field

from ringkeeper.adapters.base import JSON, Adapter, Target, ToolError, draft, read, send
from ringkeeper.models import InstanceType

IMAP_HOST = "imap.gmail.com"
SMTP_HOST = "smtp.gmail.com"
TIMEOUT = 30
MAX_BODY_CHARS = 20_000
APP_PASSWORDS = "https://myaccount.google.com/apppasswords"


# Tests swap these for fakes; production talks to Gmail.
def imap_factory() -> imaplib.IMAP4:
    return imaplib.IMAP4_SSL(IMAP_HOST, 993, timeout=TIMEOUT)


def smtp_factory() -> smtplib.SMTP:
    return smtplib.SMTP_SSL(SMTP_HOST, 465, timeout=TIMEOUT)


ADDRESS_PATTERN = r"^[^@\s<>\"]+@[^@\s<>\"]+\.[^@\s<>\"]+$"
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


def _header(message: email.message.Message, name: str) -> str:
    return str(message.get(name, ""))


def _text(message: email.message.EmailMessage) -> str:
    part = message.get_body(preferencelist=("plain", "html"))
    if part is None:
        return ""
    content = str(cast(EmailMessage, part).get_content())
    if part.get_content_type() == "text/html":
        content = re.sub(r"<[^>]+>", " ", content)
        content = re.sub(r"[ \t]+", " ", content)
    return content.strip()


def _fetched(parts: list[Any]) -> bytes | None:
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
        Field(description="Gmail search, e.g. from:anna@acme.com newer_than:7d.", max_length=500),
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
                raw = _fetched(parts) or b""
                headers = email.message_from_bytes(raw, policy=email.policy.default)
                found.append(
                    {"uid": int(uid)}
                    | {k: _header(headers, k.title()) for k in ("from", "to", "subject", "date")}
                )
            return {"messages": found}
        finally:
            conn.logout()

    return await _blocking(run)


@read
async def get_message(target: Target, uid: Uid, mailbox: Mailbox = "inbox") -> JSON:
    """Read one message: headers, its text, and attachment names. Doesn't mark it read."""

    def run() -> JSON:
        conn = _imap(target)
        try:
            _select(conn, mailbox)
            status, parts = conn.uid("FETCH", str(uid), "(BODY.PEEK[])")
            raw = _fetched(parts) if status == "OK" else None
            if raw is None:
                raise ToolError(f"no message with uid {uid} in {mailbox}")
            message = email.message_from_bytes(raw, policy=email.policy.default)
            body = _text(message)
            return {
                "uid": uid,
                **{k: _header(message, k.title()) for k in ("from", "to", "cc", "subject", "date")},
                "message_id": _header(message, "Message-ID"),
                "body": body[:MAX_BODY_CHARS],
                "truncated": len(body) > MAX_BODY_CHARS,
                "attachments": [
                    name for part in message.iter_attachments() if (name := part.get_filename())
                ],
            }
        finally:
            conn.logout()

    return await _blocking(run)


def _compose(
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


@draft
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
            message = _compose(sender, to, subject, body, cc, in_reply_to)
            status, _ = conn.append(
                _mailbox(conn, "drafts"), "(\\Draft)", imaplib.Time2Internaldate(time.time()),
                message.as_bytes(),
            )  # fmt: skip
            if status != "OK":
                raise ToolError("Gmail didn't save the draft")
            return {"saved": "draft", "to": to, "subject": subject}
        finally:
            conn.logout()

    return await _blocking(run)


@send
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
        message = _compose(address, to, subject, body, cc, in_reply_to)
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


ADAPTER = Adapter(
    service=InstanceType.GMAIL,
    tools=(search_messages, get_message, create_draft, send_email),
    secret_fragments=secret_fragments,
)
