"""Business systems through their own APIs: one connection type, a table per vendor.

The secret is JSON naming the vendor, with its key or (signed in through a Developer app)
its tokens: {"vendor": "clerk", "api_key": "sk_test_..."}. The AI sees two tools:
list_operations, and call_operation, which names one operation from that vendor's table
(adapters.vendors). The table decides what a call needs (adapters.vendor_spec), and nothing
outside it can be called.

An operation's name carries the vendor (clerk.ban_user), so what a call needs can be read
from the call alone, wherever that's decided. A call naming another vendor's operation is
refused before it's held.
"""

import asyncio
import base64
import contextlib
import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass
from typing import Annotated, Any, cast
from urllib.parse import parse_qs, quote

import httpx2
from pydantic import Field

from ringkeeper import remote, scrub
from ringkeeper.adapters import rest
from ringkeeper.adapters.base import (
    JSON,
    Adapter,
    Hold,
    Target,
    ToolError,
    ToolKind,
    ToolSpec,
    Undoer,
    read,
)
from ringkeeper.adapters.vendor_spec import (
    FINAL,
    PLACEHOLDER,
    READ,
    Auth,
    Money,
    Op,
    Reader,
    Snapshot,
    Vendor,
)
from ringkeeper.adapters.vendors import ALL
from ringkeeper.models import Env, InstanceType

# The HTTP client logs each address at INFO, and Meta's tokens go in the address.
logging.getLogger("httpx2").setLevel(logging.WARNING)

VENDORS: dict[str, Vendor] = {v.key: v for v in ALL}
OPERATIONS: dict[str, tuple[Vendor, Op]] = {
    f"{v.key}.{op.name}": (v, op) for v in ALL for op in v.ops
}
# Currencies with no cents: an amount in them is already whole (Stripe's and Square's list).
WHOLE_CURRENCIES = frozenset({
    "BIF", "CLP", "DJF", "GNF", "ISK", "JPY", "KMF", "KRW", "MGA", "PYG", "RWF", "UGX", "VND",
    "VUV", "XAF", "XOF", "XPF",
})  # fmt: skip
EXCHANGE_MARGIN_SECONDS = 120
RUNS = {
    READ: "without asking",
    "held": "after the user approves",
    FINAL: "after the user approves and types the connection's name",
}
# A value that goes into a path: one segment, nothing that climbs out of it.
SEGMENT_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-.:@~+=!,"
)
# Tests swap in httpx2.MockTransport; production talks to the vendor.
transport: httpx2.AsyncBaseTransport | None = None
_exchanged: dict[str, tuple[str, float]] = {}  # the keys' hash: (token, when it ends)

for _vendor in ALL:
    if _vendor.scope:
        remote.VENDOR_SCOPES[_vendor.key] = remote.ScopeSpec(
            _vendor.name, _vendor.scope, frozenset({"list_operations"})
        )


def _fields(value: str) -> JSON:
    try:
        data: Any = json.loads(value)
    except ValueError:
        raise ToolError("this connection's secret isn't in the expected form") from None
    return cast(JSON, data) if isinstance(data, dict) else {}


def vendor_of(fields: JSON) -> Vendor:
    found = VENDORS.get(str(fields.get("vendor") or ""))
    if found is None:
        raise ToolError("this connection doesn't say which service it's for")
    return found


def settings(value: str) -> JSON:
    """The connection's values, checked: what a form sends, and what a call reads."""
    fields = _fields(value)
    vendor = vendor_of(fields)
    if vendor.provider:
        if not fields.get("access_token"):
            raise ToolError(f"sign in to {vendor.name} from its tile: it doesn't take a pasted key")
        return fields
    for wanted in vendor.secrets:
        given = str(fields.get(wanted.key) or "").strip()
        if not given and wanted.required:
            raise ToolError(f"this {vendor.name} connection needs its {wanted.label}")
        if given and wanted.pattern and not re.fullmatch(wanted.pattern, given):
            raise ToolError(f"that doesn't look like a {vendor.name} {wanted.label}")
    _has_a_way_in(vendor, fields)
    return fields


def _has_a_way_in(vendor: Vendor, fields: JSON) -> None:
    """A vendor that takes a token or a key to trade for one needs one of them, whole."""
    exchange = vendor.exchange
    if exchange is None or not exchange.when:
        return
    wanted = [v for v in exchange.form.values() if not v.startswith("=")]
    if fields.get(exchange.when):
        if missing := [
            s.label for s in vendor.secrets if s.key in wanted and not fields.get(s.key)
        ]:
            raise ToolError(f"this {vendor.name} connection needs its {missing[0]} too")
    elif not fields.get(vendor.auth.field):
        raise ToolError(f"this {vendor.name} connection needs a token, or a client ID and secret")


def secret_fragments(value: str) -> list[str]:
    """Every value a vendor's form marks as a key, and any tokens."""
    try:
        fields = _fields(value)
        vendor = vendor_of(fields)
    except ToolError:
        return []
    keys = [s.key for s in vendor.secrets if s.hidden]
    found = [str(fields[k]) for k in (*keys, "access_token", "refresh_token") if fields.get(k)]
    pages = cast(list[JSON], fields.get("pages") or [])
    return found + [str(p["token"]) for p in pages if p.get("token")]


def config_of(value: str) -> JSON:
    """What's kept on the connection itself, so nothing needs the vault to know the vendor."""
    return {"vendor": vendor_of(_fields(value)).key}


def check_env(value: str, env: Env) -> None:
    """The key says what it reaches: live data can't be labelled test."""
    fields = _fields(value)
    vendor = vendor_of(fields)
    says = vendor.env_of(fields) if vendor.env_of else None
    if says is not None and (says is Env.PROD) != (env is Env.PROD):
        which = "production" if says is Env.PROD else "test"
        raise ToolError(f"that's a {which} {vendor.name} key, so choose {which} data")


def operation(name: str) -> tuple[Vendor, Op] | None:
    return OPERATIONS.get(name)


def _own(target: Target, args: JSON) -> tuple[Vendor, Op, JSON]:
    """The operation this call names, on this connection's vendor, or ToolError."""
    fields = _fields(target.secret.reveal())
    vendor = vendor_of(fields)
    found = operation(str(args.get("operation") or ""))
    if found is None or found[0].key != vendor.key:
        raise ToolError(
            f"'{args.get('operation')}' isn't an operation of this {vendor.name} connection: "
            "call list_operations"
        )
    return vendor, found[1], fields


def _segment(name: str, value: Any) -> str:
    text = str(value)
    if not text or text in (".", "..") or not set(text) <= SEGMENT_CHARS:
        raise ToolError(f"'{name}' isn't a plain ID")
    return quote(text, safe="")


def _address(op: Op, given: JSON, account: str | None) -> str:
    values = {**given, **({"account": account} if account else {})}

    def fill(match: Any) -> str:
        name = str(match.group(1))
        if name not in values:
            raise ToolError(f"'{op.name}' needs {name} in `path`")
        return _segment(name, values[name])

    return PLACEHOLDER.sub(fill, op.path)


def _checked_body(op: Op, body: JSON | None) -> JSON | None:
    if op.only and (extra := sorted(set(body or {}) - set(op.only))):
        raise ToolError(
            f"'{op.name}' doesn't take {', '.join(extra)}: it takes {', '.join(op.only)}"
        )
    return body


def _classify(target: Target, args: JSON) -> ToolKind:
    _vendor, op, _ = _own(target, args)
    # Checked before it's held: nobody should approve a call that can only fail.
    account = args.get("account") or _fields(target.secret.reveal()).get("default_account")
    _address(op, cast(JSON, args.get("path") or {}), cast(str | None, account))
    body = _checked_body(op, cast(JSON | None, args.get("body")))
    if op.shape is not None:
        op.shape(body or {}, cast(str | None, account))
    return ToolKind.READ if op.level == READ else ToolKind.WRITE


def typed_reason(args: JSON) -> str | None:
    """Why this call needs the name typed, or None. An unknown operation can't run anyway."""
    found = operation(str(args.get("operation") or ""))
    if found is None:
        return "uses an operation Ringkeeper doesn't know"
    op = found[1]
    if op.level != FINAL:
        return None
    return op.why(args) if callable(op.why) else op.why


def never_trusted(args: JSON) -> bool:
    found = operation(str(args.get("operation") or ""))
    return found is None or found[0].never_trusted


def _token(vendor: Vendor, fields: JSON, account: str | None) -> str:
    if vendor.token_of is not None:
        return vendor.token_of(fields, account)
    return str(fields["access_token"])


def _headers(vendor: Vendor, fields: JSON, account: str | None) -> dict[str, str]:
    auth: Auth = vendor.auth
    headers = dict(vendor.headers)
    if vendor.account_header and account:
        headers[vendor.account_header] = account
    if vendor.token_param:
        return headers
    if vendor.provider:
        return headers | {"Authorization": f"Bearer {_token(vendor, fields, account)}"}
    match auth.kind:
        case "bearer":
            headers["Authorization"] = f"{auth.scheme} {fields[auth.field]}"
        case "header":
            headers[auth.name] = str(fields[auth.field])
        case "basic":
            password = str(fields.get(auth.field2) or "") if auth.field2 else ""
            pair = f"{fields[auth.field]}:{password}".encode()
            headers["Authorization"] = f"Basic {base64.b64encode(pair).decode()}"
        case _:
            headers |= auth.custom(fields) if auth.custom else {}
    return headers


def _base(vendor: Vendor, fields: JSON, account: str | None) -> str:
    """The vendor's address. A value in it is checked on every call, signed-in ones too: the
    key goes wherever this points."""
    patterns = {s.key: s.pattern for s in vendor.secrets}

    def fill(match: Any) -> str:
        name = str(match.group(1))
        if name == "account":
            if not account:
                raise ToolError(f"name the {vendor.name} account in `account`")
            return _segment(name, account)
        value = str(fields.get(name) or "")
        if not re.fullmatch(patterns[name], value):
            raise ToolError(f"this {vendor.name} connection's {name} isn't an address it can use")
        return value

    return PLACEHOLDER.sub(fill, vendor.base)


def _client(vendor: Vendor, fields: JSON, account: str | None) -> httpx2.AsyncClient:
    return httpx2.AsyncClient(
        base_url=_base(vendor, fields, account), headers=_headers(vendor, fields, account),
        timeout=rest.TIMEOUT, transport=transport,
    )  # fmt: skip


def _filled_url(vendor: Vendor, address: str, fields: JSON) -> str:
    patterns = {s.key: s.pattern for s in vendor.secrets}

    def fill(match: Any) -> str:
        name = str(match.group(1))
        value = str(fields.get(name) or "")
        if not re.fullmatch(patterns[name], value):
            raise ToolError(f"this {vendor.name} connection's {name} isn't an address it can use")
        return value

    return PLACEHOLDER.sub(fill, address)


async def _with_token(vendor: Vendor, fields: JSON) -> JSON:
    """The connection's values, with a fresh token if this vendor trades its key for one."""
    exchange = vendor.exchange
    if exchange is None or (exchange.when and not fields.get(exchange.when)):
        return fields
    form = {
        k: v[1:] if v.startswith("=") else str(fields.get(v) or "")
        for k, v in exchange.form.items()
    }
    form = {k: v for k, v in form.items() if v}  # what's optional and unset isn't sent
    seen = hashlib.sha256(json.dumps([vendor.key, form], sort_keys=True).encode()).hexdigest()
    token, ends = _exchanged.get(seen, ("", 0.0))
    if time.time() >= ends - EXCHANGE_MARGIN_SECONDS:
        client = httpx2.AsyncClient(timeout=rest.TIMEOUT, transport=transport)
        sent: JSON = {"json": form} if exchange.as_json else {"data": form}
        address = _filled_url(vendor, exchange.url, fields)
        answer = await rest.call(client, vendor.name, "POST", address, **sent)
        token = str(answer.get(exchange.answer) or "")
        if not token:
            raise ToolError(f"{vendor.name} didn't give a token for this key")
        ends = time.time() + float(answer.get("expires_in") or exchange.lifetime)
        _exchanged[seen] = (token, ends)
    scrub.register(token)
    return {**fields, (exchange.into or vendor.auth.field): token}


async def run(
    vendor: Vendor,
    op: Op,
    fields: JSON,
    path: JSON | None = None,
    query: JSON | None = None,
    body: JSON | None = None,
    account: str | None = None,
) -> JSON:
    account = account or cast(str | None, fields.get("default_account"))
    if op.answer is not None:
        return op.answer(fields, account)
    fields = await _with_token(vendor, fields)
    client = _client(vendor, fields, account)
    if op.graphql:
        answer = await rest.call(
            client, vendor.name, "POST", vendor.graphql_path,
            json={"query": op.graphql, "variables": body or {}},
        )  # fmt: skip
        if errors := answer.get("errors"):
            raise ToolError(f"{vendor.name} said: {json.dumps(errors)[:500]}")
        return cast(JSON, answer.get("data") or {})
    sent = _checked_body(op, body)
    if op.shape is not None:
        sent = op.shape(sent or {}, account)
    if op.inject:
        sent = {**(sent or {}), **{k: fields[k] for k in op.inject}}
    # A query written in the operation's path is part of what it does (operation=void), so
    # it's sent, and the call's own values can't change it.
    address, _, fixed = _address(op, path or {}, account).partition("?")
    values = {**(query or {}), **{k: v[0] for k, v in parse_qs(fixed).items()}}
    if op.method != "GET":
        client.headers.update(dict(vendor.write_headers))
    for header, name in vendor.path_headers.items():
        if given := (path or {}).get(name):
            client.headers[header] = _segment(name, given)
    used = vendor.meter(op, values, sent) if vendor.meter else 0
    try:
        token = _token_for(vendor, fields, account)
        return await _send(vendor, op, client, address, values, sent, token)
    finally:
        if used:
            await _metered(fields, used)


def _token_for(vendor: Vendor, fields: JSON, account: str | None) -> JSON:
    return {vendor.token_param: _token(vendor, fields, account)} if vendor.token_param else {}


async def _send(
    vendor: Vendor,
    op: Op,
    client: httpx2.AsyncClient,
    address: str,
    values: JSON,
    sent: JSON | None,
    token: JSON,
) -> JSON:
    """One request. A token that isn't in a header goes where the vendor documents it: in the
    form of a change, and the query of anything else."""
    if not token:
        return await rest.call(client, vendor.name, op.method, address, params=values, json=sent)
    if op.method in ("GET", "DELETE"):
        return await rest.call(client, vendor.name, op.method, address, params=values | token)
    form = {k: _plain(v) for k, v in (sent or {}).items()} | token
    return await rest.call(client, vendor.name, op.method, address, params=values, data=form)


def _plain(value: Any) -> str:
    """A form's value: JSON for anything that isn't already text."""
    return value if isinstance(value, str) else json.dumps(value)


async def _metered(fields: JSON, used: int) -> None:
    from ringkeeper.services import ads_budget  # noqa: PLC0415 (it needs the database)

    await asyncio.to_thread(ads_budget.add, str(fields.get("app") or ""), used)


@read
async def list_operations(target: Target) -> JSON:
    """List what this connection can do: each operation's name, what it does, what it takes,
    and how a call runs (without asking, after approval, or with the name typed)."""
    vendor = vendor_of(_fields(target.secret.reveal()))
    return {
        "service": vendor.name,
        "operations": [
            {
                "name": f"{vendor.key}.{op.name}",
                "does": op.says,
                "path": PLACEHOLDER.findall(op.path),
                "takes": list(op.params),
                "runs": RUNS[op.level],
                **({"note": op.hint} if op.hint else {}),
            }
            for op in vendor.ops
        ],
    }


OperationName = Annotated[
    str, Field(min_length=3, max_length=100, description="From list_operations.")
]
Values = dict[str, Any] | None


def _reader(vendor: Vendor, fields: JSON, account: str | None) -> Reader:
    async def read_it(name: str, path: JSON, body: JSON | None) -> JSON:
        op = next(o for o in vendor.ops if o.name == name)
        return await run(vendor, op, fields, path, None, body, account)

    return read_it


async def _call_operation(
    target: Target,
    operation: OperationName,
    path: Annotated[Values, Field(description="Values for the operation's path.")] = None,
    query: Annotated[
        Values,
        Field(description="Query values, for a read or a search, as list_operations shows."),
    ] = None,
    body: Annotated[Values, Field(description="What to send, for a change.")] = None,
    account: Annotated[
        str | None, Field(description="Which account, where the service has several.")
    ] = None,
) -> JSON:
    """Run one operation from list_operations. It waits for the user's approval unless
    list_operations says it runs without asking."""
    args = {"operation": operation, "path": path, "body": body}
    vendor, op, fields = _own(target, args)
    values = dict(query or {})
    local = {k: values.pop(k) for k in op.local if k in values}
    if op.prepare is not None:
        values = op.prepare(values, local)
    if op.confirm is not None:
        await op.confirm(_reader(vendor, fields, account), path or {}, body or {}, account)
    answer = await run(vendor, op, fields, path, values, body, account)
    # A long answer is read in parts: services.continuations.
    return op.after(answer, local) if op.after is not None else answer


def _at(value: JSON, dotted: str) -> Any:
    """A value by its path: amount_money.amount, input.transactions.0.amount."""
    found: Any = value
    for part in dotted.split("."):
        if isinstance(found, list):
            items = cast(list[Any], found)
            found = items[int(part)] if part.isdigit() and int(part) < len(items) else None
        else:
            found = cast(JSON, found).get(part) if isinstance(found, dict) else None
    return found


def _picked(fields: tuple[str, ...], value: JSON) -> Any:
    return next((_at(value, f) for f in fields if _at(value, f) not in (None, "")), None)


@dataclass(frozen=True)
class Card:
    """What an approval card shows in large text, for a call that moves money."""

    amount: str
    counterparty: str
    account: str


def money_card(args: JSON) -> Card | None:
    """Amount, currency, counterparty and account of a call that moves money, as written in
    the call. What the call doesn't say is shown as not stated, not guessed at."""
    found = operation(str(args.get("operation") or ""))
    if found is None or found[1].money is None:
        return None
    body = {**cast(JSON, args.get("path") or {}), **cast(JSON, args.get("body") or {})}
    if args.get("account"):
        body.setdefault("account", args["account"])
    return card_of(found[1].money, body)


def card_of(rule: Money, body: JSON) -> Card:
    amount, currency = _picked(rule.amount, body), _picked(rule.currency, body)
    shown = "not stated" if amount is None else str(amount)
    if isinstance(amount, float):
        shown = f"{amount:,.2f}"
    if amount is not None and rule.minor_units and str(currency).upper() not in WHOLE_CURRENCIES:
        # An amount in a currency the call doesn't name can't be divided by a guess.
        with contextlib.suppress(TypeError, ValueError):  # not a number: shown as written
            shown = f"{float(amount) / 100:,.2f}" if currency else f"{amount} (smallest units)"
    return Card(
        f"{shown} {str(currency).upper() if currency else '(currency not stated)'}{rule.per}",
        str(_picked(rule.counterparty, body) or "not stated"),
        str(_picked(rule.account, body) or "not stated"),
    )


def _edited(snapshot: Snapshot, args: JSON) -> list[str]:
    body = cast(JSON, args.get("body") or {})
    return list(cast(JSON, body.get(snapshot.nest) or {}) if snapshot.nest else body)


async def _record(vendor: Vendor, snapshot: Snapshot, fields: JSON, kept: JSON) -> JSON:
    """The fields being kept, as the vendor has them now."""
    wanted = cast(list[str], kept["keep"])
    reader = next(o for o in vendor.ops if o.name == snapshot.read)
    asked = {snapshot.query: ",".join(wanted)} if snapshot.query else None
    found = await run(
        vendor, reader, fields, cast(JSON, kept["path"]), asked, account=kept.get("account")
    )
    record = cast(JSON, _at(found, snapshot.where) or {}) if snapshot.where else found
    if snapshot.nest:
        record = cast(JSON, record.get(snapshot.nest) or {})
    return {k: record.get(k) for k in wanted}


def _snapshot(op: Op) -> Snapshot:
    if op.undo is None:
        raise ToolError(f"'{op.name}' can't be undone here")
    return op.undo


class OperationUndo(Undoer):
    """Takes back an edit whose operation says how (Op.undo)."""

    def _op(self, args: JSON) -> tuple[Vendor, Op] | None:
        return operation(str(args.get("operation") or ""))

    def why_not(self, args: JSON) -> str | None:
        found = self._op(args)
        if found is None or found[1].undo is None:
            return "Ringkeeper can't undo this here."
        return None

    async def before(self, target: Target, args: JSON) -> JSON:
        vendor, op, fields = _own(target, args)
        snapshot = _snapshot(op)
        path = cast(JSON, args.get("path") or {})
        # Only what the edit sets goes back: anything else was never changed.
        edited = _edited(snapshot, args)
        keep = [k for k in snapshot.keep if k in edited] or list(snapshot.keep) or sorted(edited)
        kept: JSON = {
            "operation": f"{vendor.key}.{op.name}",
            "restore": f"{vendor.key}.{snapshot.restore or op.name}",
            "path": path,
            "account": args.get("account"),
            "keep": keep,
        }
        return kept | {"fields": await _record(vendor, snapshot, fields, kept)}

    async def current(self, target: Target, kept: JSON) -> JSON:
        vendor, op, fields = _own(target, kept)
        return {"fields": await _record(vendor, _snapshot(op), fields, kept)}

    async def restore(self, target: Target, kept: JSON) -> JSON:
        vendor, op, fields = _own(target, kept)
        snapshot = _snapshot(op)
        _, restore, _ = _own(target, {"operation": kept["restore"]})
        back = {
            k: snapshot.empty if v is None else v for k, v in cast(JSON, kept["fields"]).items()
        }
        return await run(
            vendor, restore, fields, cast(JSON, kept["path"]),
            body={snapshot.nest: back} if snapshot.nest else back,
            account=kept.get("account"),
        )  # fmt: skip

    def describe(self, kept: JSON) -> str:
        found = operation(str(kept.get("operation") or ""))
        says = found[1].undo.says if found and found[1].undo else "the fields it changed"
        return f"Puts back {says}."


call_operation = ToolSpec(
    name="call_operation",
    kind=ToolKind.WRITE,  # the careful answer; _classify decides per call
    description=(_call_operation.__doc__ or "").strip(),
    handler=_call_operation,
    hold=Hold.ALWAYS,
    classify=_classify,
    undo=OperationUndo(),
    typed=typed_reason,
)


async def probe(target: Target) -> str:
    """The vendor's own read (the Test button)."""
    fields = _fields(target.secret.reveal())
    vendor = vendor_of(fields)
    if not vendor.probe:
        return f"Saved. {vendor.name} has nothing to read without changing something."
    op = next(o for o in vendor.ops if o.name == vendor.probe)
    found = await run(vendor, op, fields)
    return f"Connected to {vendor.name}" + (f": {vendor.who(found)}." if vendor.who else ".")


ADAPTER = Adapter(
    service=InstanceType.VENDOR,
    tools=(list_operations, call_operation),
    secret_fragments=secret_fragments,
)
