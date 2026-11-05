"""Sending email through any provider's SMTP server. The secret is JSON:

    {"host": "smtp.example.com", "port": 587, "security": "starttls",
     "username": "...", "password": "...", "from_address": "hello@example.com",
     "test_address": "me@example.com"}

Always encrypted: "ssl" from the start (port 465) or "starttls" (port 587), with the
server's certificate checked. Sending waits for approval, and many recipients at once
needs the name typed (adapters.mail). A test to `test_address` runs by itself.
"""

import json
import re
import smtplib
import socket
import ssl
from collections.abc import Callable
from typing import Any, cast

import anyio.to_thread

from ringkeeper import netpolicy
from ringkeeper.adapters import mail
from ringkeeper.adapters.base import JSON, Adapter, Target, ToolError, draft, final, read
from ringkeeper.adapters.gmail import ADDRESS_PATTERN
from ringkeeper.models import InstanceType

TIMEOUT = 30
SECURITY = ("ssl", "starttls")
MAX_PORT = 65535


def settings(value: str) -> JSON:
    """The stored JSON, checked; raises ToolError if it can't be used."""
    try:
        data: Any = json.loads(value)
    except ValueError:
        raise ToolError("this email connection's secret isn't in the expected form") from None
    fields = cast(JSON, data) if isinstance(data, dict) else {}
    host = str(fields.get("host") or "").strip()
    security = str(fields.get("security") or "starttls")
    sender = str(fields.get("from_address") or "").strip()
    try:
        port = int(fields.get("port") or (465 if security == "ssl" else 587))
    except (TypeError, ValueError):
        port = 0
    if not host or not fields.get("password"):
        raise ToolError("this email connection needs a server and a password")
    if security not in SECURITY:
        raise ToolError("the connection to the server must be encrypted (SSL or STARTTLS)")
    if not 0 < port <= MAX_PORT:
        raise ToolError("that port isn't a number from 1 to 65535")
    if not re.fullmatch(ADDRESS_PATTERN, sender):
        raise ToolError("this email connection needs a From address")
    username = str(fields.get("username") or "").strip() or sender
    return {**fields, "host": host, "port": port, "security": security,
            "from_address": sender, "username": username}  # fmt: skip


def secret_fragments(value: str) -> list[str]:
    try:
        return [str(settings(value)["password"])]
    except ToolError:
        return []


# Tests swap this for a fake; production talks to the server.
def smtp_factory(host: str, port: int, security: str) -> smtplib.SMTP:
    context = ssl.create_default_context()
    if security == "ssl":
        return smtplib.SMTP_SSL(host, port, timeout=TIMEOUT, context=context)
    server = smtplib.SMTP(host, port, timeout=TIMEOUT)
    server.starttls(context=context)
    return server


def with_server(fields: JSON, work: Callable[[smtplib.SMTP], object], what: str) -> None:
    netpolicy.check_host(str(fields["host"]))
    try:
        with smtp_factory(fields["host"], fields["port"], fields["security"]) as server:
            server.login(fields["username"], fields["password"])
            work(server)
    except smtplib.SMTPAuthenticationError:
        raise ToolError(
            f"{fields['host']} didn't accept this username and password. Many providers want "
            "an app password or SMTP key here, not your normal password."
        ) from None
    except smtplib.SMTPRecipientsRefused as error:
        raise ToolError(f"the server refused {len(error.recipients)} recipient(s)") from None
    except socket.gaierror:
        raise ToolError(f"{what}: there's no server called {fields['host']}") from None
    except (smtplib.SMTPException, OSError) as error:
        raise ToolError(f"{what}: {type(error).__name__}") from None


async def probe(target: Target) -> str:
    """Sign in and leave (the Test button). Sends nothing."""
    fields = settings(target.secret.reveal())

    def run() -> None:
        with_server(fields, lambda _server: None, f"cannot reach {fields['host']}")

    await anyio.to_thread.run_sync(run)
    return f"Connected to {fields['host']} as {fields['username']}."


async def _send(target: Target, to: list[str], **parts: Any) -> JSON:
    fields = settings(target.secret.reveal())
    built = mail.message(fields["from_address"], to, **parts)

    def run() -> None:
        with_server(fields, lambda server: server.send_message(built), "it wasn't sent")

    await anyio.to_thread.run_sync(run)
    return {"sent": True, "to": to, "subject": parts["subject"]}


@read
async def sender_info(target: Target) -> JSON:
    """Which address this connection sends from, and whether it has a test address."""
    fields = settings(target.secret.reveal())
    return {"from": fields["from_address"], "has_test_address": bool(fields.get("test_address"))}


@final(mail.bulk_reason)
async def send_email(
    target: Target,
    to: mail.Recipients,
    subject: mail.Subject,
    body: mail.Body,
    cc: mail.MoreRecipients = None,
    bcc: mail.MoreRecipients = None,
    html: mail.Html = None,
) -> JSON:
    """Send an email. Always waits for the user's approval; to more than 10 people at once,
    they also have to type the connection's name."""
    return await _send(target, to, subject=subject, body=body, cc=cc, bcc=bcc, html=html)


@draft
async def send_test_email(target: Target, subject: mail.Subject, body: mail.Body) -> JSON:
    """Send a test to the connection's own test address, set by the user. It runs without
    asking, and can't go anywhere else."""
    to = mail.own_test_address(settings(target.secret.reveal()))
    return await _send(target, [to], subject=f"[Test] {subject}", body=body)


ADAPTER = Adapter(
    service=InstanceType.SMTP,
    tools=(sender_info, send_email, send_test_email),
    secret_fragments=secret_fragments,
)
