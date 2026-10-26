"""Twilio REST API (2010-04-01). The secret is JSON:

    {"account_sid": "AC...", "key_sid": "SK...", "key_secret": "..."}

An API key (console.twilio.com: Account > API keys & tokens) is the usual
choice; with no key_sid, key_secret is the account's Auth Token instead.
A Standard key can't read the Accounts resource itself, so nothing here does.

Sending a text reaches a real person and costs money: it always waits for
approval, even on a connection marked as test data.
"""

import json
import re
from typing import Annotated, Any, cast

import httpx2
from pydantic import Field

from ringkeeper.adapters import rest
from ringkeeper.adapters.base import JSON, Adapter, Target, ToolError, read, send
from ringkeeper.models import InstanceType

API = "https://api.twilio.com/2010-04-01"
# Tests swap in httpx2.MockTransport; production uses the real network.
transport: httpx2.AsyncBaseTransport | None = None

PHONE_PATTERN = r"^\+[1-9][0-9]{6,14}$"  # E.164
Phone = Annotated[
    str, Field(description="A number in E.164 form, e.g. +15551234567.", pattern=PHONE_PATTERN)
]
SID_PATTERN = r"^(SM|MM)[0-9a-f]{32}$"
Limit = Annotated[int, Field(ge=1, le=100)]


def credentials(value: str) -> tuple[str, str, str]:
    """(account_sid, username, password) from the stored JSON; raises ToolError if malformed."""
    try:
        data: Any = json.loads(value)
    except ValueError:
        raise ToolError("this Twilio connection's secret isn't in the expected form") from None
    fields = cast(dict[str, Any], data) if isinstance(data, dict) else {}
    account, key, secret = (
        str(fields.get(k) or "") for k in ("account_sid", "key_sid", "key_secret")
    )
    if not re.fullmatch(r"AC[0-9a-f]{32}", account) or not secret:
        raise ToolError("this Twilio connection needs an Account SID (AC...) and a secret")
    if key and not re.fullmatch(r"SK[0-9a-f]{32}", key):
        raise ToolError("a Twilio API key SID starts with SK")
    return account, key or account, secret


def secret_fragments(value: str) -> list[str]:
    try:
        return [credentials(value)[2]]
    except ToolError:
        return []


def _client(target: Target) -> httpx2.AsyncClient:
    account, username, password = credentials(target.secret.reveal())
    return httpx2.AsyncClient(
        base_url=f"{API}/Accounts/{account}",
        auth=(username, password),
        timeout=rest.TIMEOUT,
        transport=transport,
    )


async def _call(target: Target, method: str, path: str, **kwargs: Any) -> JSON:
    return await rest.call(_client(target), "Twilio", method, path, **kwargs)


def _message(m: JSON) -> JSON:
    keys = (
        "sid",
        "from",
        "to",
        "body",
        "status",
        "direction",
        "date_sent",
        "error_message",
        "price",
    )
    return {key: m.get(key) for key in keys}


@read
async def list_phone_numbers(target: Target, limit: Limit = 20) -> JSON:
    """List the account's phone numbers (number, friendly name, SMS and voice capable)."""
    data = await _call(target, "GET", "/IncomingPhoneNumbers.json", params={"PageSize": limit})
    numbers = cast(list[JSON], data.get("incoming_phone_numbers", []))
    return {
        "numbers": [
            {
                "number": n.get("phone_number"),
                "name": n.get("friendly_name"),
                "capabilities": n.get("capabilities"),
            }
            for n in numbers
        ]
    }


@read
async def list_messages(
    target: Target,
    to: Annotated[str | None, Field(pattern=PHONE_PATTERN)] = None,
    from_number: Annotated[str | None, Field(pattern=PHONE_PATTERN)] = None,
    sent_after: Annotated[
        str | None, Field(description="YYYY-MM-DD.", pattern=r"^\d{4}-\d{2}-\d{2}$")
    ] = None,
    limit: Limit = 20,
) -> JSON:
    """List texts sent and received, newest first."""
    params = {"To": to, "From": from_number, "DateSent>": sent_after, "PageSize": limit}
    data = await _call(target, "GET", "/Messages.json", params=params)
    return {"messages": [_message(m) for m in cast(list[JSON], data.get("messages", []))]}


@read
async def get_message(
    target: Target, message_sid: Annotated[str, Field(pattern=SID_PATTERN)]
) -> JSON:
    """Get one text by its sid (SM...): body, status, and the error if it failed."""
    if not re.fullmatch(SID_PATTERN, message_sid):
        raise ToolError("that isn't a Twilio message sid")
    return _message(await _call(target, "GET", f"/Messages/{message_sid}.json"))


@read
async def list_calls(target: Target, limit: Limit = 20) -> JSON:
    """List calls, newest first (from, to, status, duration in seconds)."""
    data = await _call(target, "GET", "/Calls.json", params={"PageSize": limit})
    keys = ("sid", "from", "to", "status", "direction", "duration", "start_time")
    return {"calls": [{k: c.get(k) for k in keys} for c in cast(list[JSON], data.get("calls", []))]}


@send
async def send_sms(
    target: Target,
    to: Phone,
    from_number: Annotated[
        str, Field(description="One of the account's numbers.", pattern=PHONE_PATTERN)
    ],
    body: Annotated[str, Field(min_length=1, max_length=1600)],
) -> JSON:
    """Send a text. Always waits for the user's approval."""
    data = await _call(
        target, "POST", "/Messages.json", data={"To": to, "From": from_number, "Body": body}
    )
    return _message(data)


ADAPTER = Adapter(
    service=InstanceType.TWILIO,
    tools=(list_phone_numbers, list_messages, get_message, list_calls, send_sms),
    secret_fragments=secret_fragments,
)
