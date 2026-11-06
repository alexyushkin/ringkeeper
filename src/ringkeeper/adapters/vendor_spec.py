"""How a business system is described: its address, how it signs in, and what it may do.

A vendor is data, in adapters/vendors/. Its table of operations is the whole of what the
AI can reach: anything else the vendor's API offers can't be called, which is how an
endpoint that hands out a secret or signs in as someone else stays out of reach.

Each operation says what it needs:

- `reads`: runs. Reports and lookups.
- `held`: always waits for approval, even on test data. Drafts and edits.
- `final`: also needs the connection's name typed, in the app itself. It sends to real
  people, publishes, posts to the books, moves money, deletes, changes who has access or
  adds ongoing cost. No setting lets one run by itself.
"""

import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from ringkeeper.models import Env
from ringkeeper.remote import ScopeField

JSON = dict[str, Any]
# How a check reads the vendor: (the operation's name, its path values, its body).
Reader = Callable[[str, JSON, JSON | None], Awaitable[JSON]]
# A check before a change runs: (how to read, the call's path values, its body, its account).
Confirm = Callable[[Reader, JSON, JSON, str | None], Awaitable[None]]
READ, HELD, FINAL = "read", "held", "final"
# What a Field's value may look like when it becomes part of the address.
HOST_LABEL = r"^[a-z0-9][a-z0-9-]{0,62}$"
PLACEHOLDER = re.compile(r"\{([a-z_]+)\}")


@dataclass(frozen=True)
class Money:
    """Which fields of a call say what an approval card shows in large text."""

    # Each may be dotted: amount_money.amount.
    amount: tuple[str, ...] = ("amount.value", "amount_money.amount", "amount", "total", "value")
    currency: tuple[str, ...] = (
        "amount.currency", "amount.currency_code", "amount_money.currency", "currency",
        "currency_code",
    )  # fmt: skip
    counterparty: tuple[str, ...] = ("customer", "customer_id", "payee", "vendor", "email")
    account: tuple[str, ...] = ("account", "source", "source_id", "destination", "location_id")
    # The amount is in the currency's smallest unit (cents).
    minor_units: bool = False
    per: str = ""  # what the amount is for: " a day", " a click"


@dataclass(frozen=True)
class Snapshot:
    """How an edit is taken back: `read` (this vendor's operation, with the same path) gets
    the record before the edit, and `restore` writes `keep` back."""

    read: str
    keep: tuple[str, ...] = ()  # empty: the fields the edit sets, inside `nest`
    restore: str = ""  # the operation that writes it back; empty: the edit itself
    where: str = ""  # the record, inside what `read` answers
    says: str = "the fields it changed"
    nest: str = ""  # the fields go inside this one, both in the edit and in the record
    query: str = ""  # `read` is told which fields to send, in this query value
    empty: Any = None  # what clears a field that was empty before the edit


@dataclass(frozen=True)
class Op:
    name: str
    method: str
    path: str  # under the vendor's address, with {path_values}
    says: str
    level: str
    # `final`: finishes "This change ...", or decides from the call (None: approving is
    # enough for this call).
    why: str | Callable[[JSON], str | None] | None = None
    params: tuple[str, ...] = ()  # worth knowing about, for the AI
    hint: str = ""  # more for the AI, beyond what a card says (`says`)
    only: tuple[str, ...] = ()  # the only body fields it takes (empty: any)
    graphql: str = ""  # sent as the query; the call's body is its variables
    undo: Snapshot | None = None
    money: Money | None = None
    # Values added to the body from the connection, never from the AI.
    inject: tuple[str, ...] = ()
    # A read's own query values, for `prepare` and `after`; the service never sees them.
    local: tuple[str, ...] = ()
    # What the service is asked, from the call's query and the `local` values.
    prepare: Callable[[JSON, JSON], JSON] | None = None
    # The answer, made readable, from what came back and the `local` values.
    after: Callable[[JSON, JSON], JSON] | None = None
    # What's sent, built from the call's plain values and its account. Raises ToolError for a
    # call that can't be sent, before it's held: so the card shows what will really happen.
    shape: Callable[[JSON, str | None], JSON] | None = None
    # Answered from the connection itself, without calling the vendor (its account, if any).
    # It must never hand back a token.
    answer: Callable[[JSON, str | None], JSON] | None = None
    # Checked against the vendor just before it runs, once approved: `read` runs a read of this
    # vendor by name. Raises ToolError if what the card showed isn't so.
    confirm: Confirm | None = None
    # What approving states to the vendor in the owner's name. The card says it.
    declares: str = ""


def reads(
    name: str,
    path: str,
    says: str,
    *params: str,
    method: str = "GET",
    local: tuple[str, ...] = (),
    prepare: Callable[[JSON, JSON], JSON] | None = None,
    after: Callable[[JSON, JSON], JSON] | None = None,
    only: tuple[str, ...] = (),
    answer: Callable[[JSON, str | None], JSON] | None = None,
    hint: str = "",
) -> Op:
    return Op(
        name, method, path, says, READ, params=(*params, *local), local=local, prepare=prepare,
        after=after, only=only, answer=answer, hint=hint,
    )  # fmt: skip


def held(
    name: str,
    method: str,
    path: str,
    says: str,
    *params: str,
    only: tuple[str, ...] = (),
    undo: Snapshot | None = None,
    money: Money | None = None,
    shape: Callable[[JSON, str | None], JSON] | None = None,
    hint: str = "",
) -> Op:
    return Op(
        name, method, path, says, HELD, params=params, only=only, undo=undo, money=money,
        shape=shape, hint=hint,
    )  # fmt: skip


def final(
    name: str,
    method: str,
    path: str,
    says: str,
    why: str | Callable[[JSON], str | None],
    *params: str,
    only: tuple[str, ...] = (),
    undo: Snapshot | None = None,
    money: Money | None = None,
    shape: Callable[[JSON, str | None], JSON] | None = None,
    confirm: Confirm | None = None,
    inject: tuple[str, ...] = (),
    hint: str = "",
    declares: str = "",
) -> Op:
    return Op(
        name, method, path, says, FINAL, why, params=params, only=only, undo=undo, money=money,
        shape=shape, confirm=confirm, inject=inject, hint=hint, declares=declares,
    )  # fmt: skip


def query(name: str, says: str, text: str, *params: str) -> Op:
    """A GraphQL read."""
    return Op(name, "POST", "", says, READ, params=params, graphql=text)


def mutation(
    name: str,
    says: str,
    text: str,
    *params: str,
    why: str | Callable[[JSON], str | None] | None = None,
    money: Money | None = None,
) -> Op:
    """A GraphQL change: held, or (with `why`) needing the name typed."""
    level = FINAL if why is not None else HELD
    return Op(name, "POST", "", says, level, why, params=params, graphql=text, money=money)


@dataclass(frozen=True)
class Secret:
    """One thing the form asks for, kept in the connection's secret."""

    key: str
    label: str
    hint: str = ""
    hidden: bool = True  # a key, shown as dots
    pattern: str = ""  # must match; required for anything that becomes part of the address
    required: bool = True


@dataclass(frozen=True)
class Exchange:
    """A key traded for a short-lived token before calling. `form` maps what's sent to the
    Secret that holds it; a value starting with = is sent as it is. The token goes in the
    Auth's field. With `when`, only a connection that has that Secret trades."""

    url: str  # may name a patterned Secret
    form: Mapping[str, str]
    when: str = ""
    into: str = ""  # the field the token goes in (empty: the Auth's)
    as_json: bool = False  # sent as JSON, not a form
    answer: str = "access_token"  # where the token is in the answer
    lifetime: int = 3600  # seconds, when the answer doesn't say (expires_in)


@dataclass(frozen=True)
class Auth:
    """How a call signs in. `bearer`: Authorization: Bearer <field>. `header`: <name>: <field>.
    `basic`: <field> as the user, `field2` (if any) as the password. `custom`: from the
    connection's values."""

    kind: str = "bearer"
    name: str = ""
    field: str = "api_key"
    field2: str = ""
    scheme: str = "Bearer"
    custom: Callable[[JSON], dict[str, str]] | None = None


@dataclass(frozen=True)
class Vendor:
    key: str
    name: str
    category: str  # as the catalog groups it
    sub: str  # the tile's second line
    base: str  # may name a Secret, like https://{shop}/admin/api/2026-10
    ops: tuple[Op, ...]
    auth: Auth = Auth()
    secrets: tuple[Secret, ...] = (Secret("api_key", "API key"),)
    probe: str = ""  # a read the Test button runs
    who: Callable[[JSON], str] | None = None  # says which account, from the probe's answer
    provider: str = ""  # signs in through this Developer app instead of a pasted key
    exchange: Exchange | None = None
    headers: Mapping[str, str] = field(default_factory=dict[str, str])
    graphql_path: str = ""  # under `base`: where its GraphQL operations go
    write_headers: Mapping[str, str] = field(default_factory=dict[str, str])  # on changes only
    words: tuple[str, ...] = ()  # what a search for it might say
    steps: str = ""  # where to get the key
    key_url: str = ""
    note: str = ""
    # The key says whether it's test or live data: a connection can't claim otherwise.
    env_of: Callable[[JSON], Env | None] | None = None
    # A project's link can be narrowed to one of these (a company, a store, a location).
    scope: tuple[ScopeField, ...] = ()
    account_header: str = ""  # sends the scope's value in this header
    env_vars: Mapping[str, str] = field(default_factory=dict[str, str])  # .env name -> Secret
    # Every write waits for a person, whatever else allows: nothing trusts it for an hour.
    never_trusted: bool = False
    # Why the tile says Unverified: part of this couldn't be confirmed from the vendor's docs.
    unverified: str = ""
    # Sent as this form field and query value, as the vendor documents, not as a header.
    token_param: str = ""
    # Which token a call uses, for a sign-in that holds one per account (a Facebook Page).
    token_of: Callable[[JSON, str | None], str] | None = None
    # Path values that go in a header instead: {"login-customer-id": "manager"}.
    path_headers: Mapping[str, str] = field(default_factory=dict[str, str])
    # How many of the vendor's daily operations a call uses, where they're counted.
    meter: Callable[[Op, JSON, JSON | None], int] | None = None

    def __post_init__(self) -> None:
        names = [op.name for op in self.ops]
        if len(names) != len(set(names)):
            raise ValueError(f"{self.key} declares an operation twice")
        patterned = {s.key for s in self.secrets if s.pattern}
        for name in PLACEHOLDER.findall(self.base):
            if name != "account" and name not in patterned:
                raise ValueError(f"{self.key}: {name} is in the address, so it needs a pattern")
        for op in self.ops:
            if op.level == FINAL and op.why is None:
                raise ValueError(f"{self.key}.{op.name} needs the name typed: say why")
            if op.level not in (READ, HELD, FINAL):
                raise ValueError(f"{self.key}.{op.name}: what does it need?")
            if op.level == READ and op.method not in ("GET", "HEAD") and not self._reads(op):
                raise ValueError(f"{self.key}.{op.name} changes something, so it can't only read")

    @staticmethod
    def _reads(op: Op) -> bool:
        """A read sent by POST: a GraphQL query, or a search the vendor only offers that way."""
        path = op.path
        return bool(op.graphql) or "/search" in path or path.endswith(("/query", ":search"))
