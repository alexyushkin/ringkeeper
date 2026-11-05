"""Sending email through Resend (resend.com). The secret is JSON:

    {"api_key": "re_...", "from_address": "hello@example.com", "test_address": "me@..."}

A key can be limited to sending, which can't read domains or messages: the Test button
says which kind it is. The From address must be on a domain verified in Resend.
Sending waits for approval, many recipients at once needs the name typed
(adapters.mail), and a test to `test_address` runs by itself.
"""

import json
import re
from typing import Annotated, Any, cast

import httpx2
from pydantic import Field

from ringkeeper.adapters import mail, rest
from ringkeeper.adapters.base import JSON, Adapter, Target, ToolError, draft, final, read
from ringkeeper.adapters.gmail import ADDRESS_PATTERN
from ringkeeper.models import InstanceType

API = "https://api.resend.com"
# Tests swap in httpx2.MockTransport; production uses the real network.
transport: httpx2.AsyncBaseTransport | None = None
EmailId = Annotated[str, Field(pattern=r"^[0-9a-fA-F-]{8,64}$", description="The email's id.")]


def settings(value: str) -> JSON:
    try:
        data: Any = json.loads(value)
    except ValueError:
        raise ToolError("this Resend connection's secret isn't in the expected form") from None
    fields = cast(JSON, data) if isinstance(data, dict) else {}
    key = str(fields.get("api_key") or "").strip()
    sender = str(fields.get("from_address") or "").strip()
    if not key.startswith("re_"):
        raise ToolError("a Resend API key starts with re_")
    if not re.fullmatch(ADDRESS_PATTERN, sender):
        raise ToolError("this Resend connection needs a From address")
    return {**fields, "api_key": key, "from_address": sender}


def secret_fragments(value: str) -> list[str]:
    try:
        return [str(settings(value)["api_key"])]
    except ToolError:
        return []


def _client(target: Target) -> httpx2.AsyncClient:
    key = settings(target.secret.reveal())["api_key"]
    return httpx2.AsyncClient(
        base_url=API, headers={"Authorization": f"Bearer {key}"}, timeout=rest.TIMEOUT,
        transport=transport,
    )  # fmt: skip


async def _call(target: Target, method: str, path: str, **kwargs: Any) -> JSON:
    return await rest.call(_client(target), "Resend", method, path, **kwargs)


async def probe(target: Target) -> str:
    try:
        found = await _call(target, "GET", "/domains")
    except ToolError as error:
        if "restricted" in str(error).lower():
            return "Connected to Resend with a key that can only send."
        raise
    count = len(cast(list[JSON], found.get("data") or []))
    return f"Connected to Resend: {count} domain{'' if count == 1 else 's'}."


async def _send(target: Target, to: list[str], **parts: Any) -> JSON:
    body = {
        "from": settings(target.secret.reveal())["from_address"], "to": to,
        "subject": parts["subject"], "text": parts["body"],
        **{k: parts[k] for k in ("cc", "bcc", "html") if parts.get(k)},
    }  # fmt: skip
    sent = await _call(target, "POST", "/emails", json=body)
    return {"sent": True, "id": sent.get("id"), "to": to, "subject": parts["subject"]}


@read
async def list_domains(target: Target) -> JSON:
    """The account's sending domains and whether each is verified."""
    found = await _call(target, "GET", "/domains")
    keys = ("id", "name", "status", "region")
    domains = cast(list[JSON], found.get("data") or [])
    return {"domains": [{k: d.get(k) for k in keys} for d in domains]}


@read
async def get_email(target: Target, email_id: EmailId) -> JSON:
    """One sent email: who it went to, its subject, and whether it was delivered."""
    found = await _call(target, "GET", f"/emails/{email_id}")
    keys = ("id", "from", "to", "subject", "created_at", "last_event")
    return {k: found.get(k) for k in keys}


@final(mail.bulk_reason)
async def send_email(
    target: Target,
    to: Annotated[list[mail.Address], Field(min_length=1, max_length=50)],
    subject: mail.Subject,
    body: mail.Body,
    cc: Annotated[list[mail.Address] | None, Field(max_length=50)] = None,
    bcc: Annotated[list[mail.Address] | None, Field(max_length=50)] = None,
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
    service=InstanceType.RESEND,
    tools=(list_domains, get_email, send_email, send_test_email),
    secret_fragments=secret_fragments,
)
