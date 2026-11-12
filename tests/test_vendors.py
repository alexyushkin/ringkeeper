"""Business systems: what every vendor's table promises, and Clerk end to end."""

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx2
import pytest

from ringkeeper import destructive
from ringkeeper.adapters import vendor
from ringkeeper.adapters.base import Target, ToolError, ToolKind
from ringkeeper.adapters.vendor_spec import FINAL, HELD, READ, Money, Op, Vendor
from ringkeeper.calls import Caller, ToolCall
from ringkeeper.db import session_scope
from ringkeeper.errors import RuleViolationError
from ringkeeper.models import Env, InstanceType
from ringkeeper.services import (
    decisions,
    developer_apps,
    env_scan,
    inbox,
    probes,
    secrets_in,
    trusts,
    undo,
)
from ringkeeper.services.calls import RefusalError, run_call
from ringkeeper.vault import Secret, Vault, get_vault
from tests.helpers import bind, make_instance, make_project

TEST_KEY = "sk_test_clerkfake123456"
LIVE_KEY = "sk_live_clerkfake123456"
EVERY_OP = sorted(vendor.OPERATIONS)
pytestmark = pytest.mark.anyio
SECRET_ENDPOINTS = ("secret_key", "/secret", "oauth_access_tokens", "sign_in_tokens", "/tokens")


class FakeApi:
    def __init__(self) -> None:
        self.requests: list[httpx2.Request] = []
        self.user: dict[str, Any] = {"id": "user_1", "public_metadata": {"a": 1},
                                     "private_metadata": {}, "unsafe_metadata": {}}  # fmt: skip

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        path = request.url.path
        if path == "/v1/instance":
            return httpx2.Response(200, json={"id": "ins_1", "environment_type": "development"})
        if path == "/v1/users/user_1" and request.method == "GET":
            return httpx2.Response(200, json=self.user)
        if path == "/v1/users/user_1/metadata":
            sent = json.loads(request.content)
            if request.method == "PATCH":
                self.user["public_metadata"] |= sent.get("public_metadata", {})
            else:
                self.user |= sent
            return httpx2.Response(200, json=self.user)
        return httpx2.Response(200, json={"ok": True})


@pytest.fixture
def api() -> Iterator[FakeApi]:
    fake = FakeApi()
    before = vendor.transport
    vendor.transport = httpx2.MockTransport(fake.handle)
    yield fake
    vendor.transport = before


@pytest.fixture
def vault(database_url: str) -> Vault:
    return get_vault()


@pytest.fixture
def linked(vault: Vault) -> None:
    ref = "file://ringkeeper/connections/acme-clerk"
    vault.store(ref, json.dumps({"vendor": "clerk", "api_key": TEST_KEY}))
    with session_scope() as session:
        make_project(session, "acme-web", owner="acme")
        make_instance(
            session, "acme-clerk", type=InstanceType.VENDOR, secret_ref=ref,
            config={"vendor": "clerk"},
        )  # fmt: skip
    bind("acme-web", "acme-clerk")


def call(operation: str, **args: Any) -> ToolCall:
    caller = Caller("acme-web", "session-1", "token-1")
    return ToolCall(
        caller, InstanceType.VENDOR, "call_operation", "acme-clerk",
        {"operation": operation, **args},
    )  # fmt: skip


def target(fields: dict[str, Any]) -> Target:
    return Target("t", Env.DEV, None, Secret("r", json.dumps(fields)))


def clerk() -> Target:
    return target({"vendor": "clerk", "api_key": TEST_KEY})


def classify(connection: Target, args: dict[str, Any]) -> ToolKind:
    assert vendor.call_operation.classify is not None
    return vendor.call_operation.classify(connection, args)


# --- What every table promises ---


@pytest.mark.parametrize("name", EVERY_OP)
def test_a_read_changes_nothing_and_a_change_says_what_it_needs(name: str) -> None:
    _, op = vendor.OPERATIONS[name]
    if op.level == READ and op.graphql:
        assert "mutation" not in op.graphql.lower(), name
    if op.level == READ and not op.graphql:
        assert vendor.OPERATIONS[name][0]._reads(op) or op.method in ("GET", "HEAD")  # pyright: ignore[reportPrivateUsage]
    if op.method == "DELETE":
        assert op.level == FINAL, f"{name} deletes"
    assert op.says, name


@pytest.mark.parametrize("name", EVERY_OP)
def test_no_table_reaches_an_endpoint_that_hands_out_a_secret(name: str) -> None:
    _, op = vendor.OPERATIONS[name]
    assert not any(s in op.path for s in SECRET_ENDPOINTS), name


@pytest.mark.parametrize("name", EVERY_OP)
def test_what_a_call_needs_comes_from_its_table(name: str) -> None:
    _, op = vendor.OPERATIONS[name]
    args = {"operation": name}
    typed = destructive.reason(InstanceType.VENDOR, "call_operation", args)
    if op.level == FINAL and isinstance(op.why, str):
        assert typed == op.why
    if op.level == HELD:
        assert typed is None


def test_an_unknown_operation_fails_closed() -> None:
    args = {"operation": "clerk.create_sign_in_token"}
    assert destructive.reason(InstanceType.VENDOR, "call_operation", args) is not None
    assert destructive.never_trusted(InstanceType.VENDOR, "call_operation", args)


def test_a_vendor_must_say_why_and_cant_call_a_change_a_read() -> None:
    with pytest.raises(ValueError, match="say why"):
        Vendor("x", "X", "Other", "", "https://x.example", (Op("a", "POST", "/a", "A", FINAL),))
    with pytest.raises(ValueError, match="can't only read"):
        Vendor("x", "X", "Other", "", "https://x.example", (Op("a", "POST", "/a", "A", READ),))
    with pytest.raises(ValueError, match="needs a pattern"):
        Vendor("x", "X", "Other", "", "https://{host}/v1", ())


# --- What a table can say ---


class Echo:
    """Answers everything, and keeps what it was sent."""

    def __init__(self, answers: dict[str, Any] | None = None) -> None:
        self.answers = answers or {}
        self.requests: list[httpx2.Request] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        return httpx2.Response(200, json=self.answers.get(request.url.path, {"ok": True}))


@pytest.fixture
def echo(monkeypatch: pytest.MonkeyPatch) -> Echo:
    made = Echo()
    monkeypatch.setattr(vendor, "transport", httpx2.MockTransport(made))
    monkeypatch.setattr(vendor, "_exchanged", {})
    return made


async def test_a_query_written_in_the_path_is_sent_and_cant_be_changed(echo: Echo) -> None:
    op = Op("void", "POST", "/invoice?operation=void", "Void", FINAL, "voids")
    table = Vendor("x", "X", "Other", "", "https://x.example", (op,))
    await vendor.run(table, op, {"api_key": "k"}, query={"operation": "update", "a": "1"})
    sent = echo.requests[0].url
    assert sent.path == "/invoice"
    assert dict(sent.params) == {"operation": "void", "a": "1"}


async def test_write_headers_go_on_changes_and_not_on_reads(echo: Echo) -> None:
    get, patch = Op("g", "GET", "/a", "G", READ), Op("p", "PATCH", "/a", "P", HELD)
    table = Vendor("x", "X", "Other", "", "https://x.example", (get, patch),
                   write_headers={"If-Match": "*"})  # fmt: skip
    await vendor.run(table, get, {"api_key": "k"})
    await vendor.run(table, patch, {"api_key": "k"})
    assert "If-Match" not in echo.requests[0].headers
    assert echo.requests[1].headers["If-Match"] == "*"


async def test_a_value_from_the_connection_goes_in_the_body(echo: Echo) -> None:
    v, op = vendor.OPERATIONS["plaid.get_accounts"]
    fields = {"vendor": "plaid", "host": "sandbox.plaid.com", "client_id": "c", "secret": "s",
              "access_token": "access-sandbox-1"}  # fmt: skip
    await vendor.run(v, op, fields, body={"options": {}})
    assert json.loads(echo.requests[0].content)["access_token"] == "access-sandbox-1"
    assert echo.requests[0].headers["PLAID-SECRET"] == "s"


def test_the_ai_cant_name_plaids_token_itself() -> None:
    connection = target({"vendor": "plaid", "host": "sandbox.plaid.com", "client_id": "c",
                         "secret": "s", "access_token": "a"})  # fmt: skip
    with pytest.raises(ToolError, match="doesn't take access_token"):
        classify(connection, {"operation": "plaid.get_accounts", "body": {"access_token": "x"}})


async def test_a_key_is_traded_once_and_the_token_kept_for_its_life(echo: Echo) -> None:
    echo.answers["/connect/token"] = {"access_token": "traded-1", "expires_in": 900}
    v, op = vendor.OPERATIONS["servicetitan.list_business_units"]
    fields = {"vendor": "servicetitan", "client_id": "c", "client_secret": "s", "app_key": "ak",
              "default_account": "123"}  # fmt: skip
    await vendor.run(v, op, fields)
    await vendor.run(v, op, fields)
    trades = [r for r in echo.requests if r.url.path == "/connect/token"]
    assert len(trades) == 1
    assert b"grant_type=client_credentials" in trades[0].content
    last = echo.requests[-1]
    assert (last.headers["Authorization"], last.headers["ST-App-Key"]) == ("traded-1", "ak")


async def test_a_shopify_store_takes_an_old_token_or_trades_a_new_apps_key(echo: Echo) -> None:
    v, op = vendor.OPERATIONS["shopify.get_shop"]
    echo.answers["/admin/api/2026-10/graphql.json"] = {"data": {"shop": {}}}
    await vendor.run(v, op, {"shop": "acme.myshopify.com", "api_key": "shpat_old"})
    assert echo.requests[-1].headers["X-Shopify-Access-Token"] == "shpat_old"
    assert len(echo.requests) == 1
    echo.answers["/admin/oauth/access_token"] = {"access_token": "shpua_new", "expires_in": 86399}
    await vendor.run(v, op, {"shop": "acme.myshopify.com", "client_id": "c", "client_secret": "s"})
    assert echo.requests[-1].headers["X-Shopify-Access-Token"] == "shpua_new"
    assert str(echo.requests[-2].url) == "https://acme.myshopify.com/admin/oauth/access_token"


@pytest.mark.parametrize(
    ("fields", "says"),
    [
        ({}, "needs a token, or a client ID"),
        ({"client_id": "c"}, "needs its Client secret"),
    ],
)
def test_a_shopify_connection_needs_a_whole_way_in(fields: dict[str, str], says: str) -> None:
    value = json.dumps({"vendor": "shopify", "shop": "acme.myshopify.com", **fields})
    with pytest.raises(ToolError, match=says):
        vendor.settings(value)


async def test_undo_keeps_what_the_edit_sets_and_empties_what_was_empty(echo: Echo) -> None:
    v = vendor.VENDORS["hubspot"]
    name = next(o.name for o in v.ops if o.undo is not None and o.undo.nest)
    echo.answers["/crm/v3/objects/contacts/1"] = {"properties": {"city": "Riga"}}
    connection = target({"vendor": "hubspot", "access_token": "at"})
    op = next(o for o in v.ops if o.name == name)
    path = dict.fromkeys(vendor.PLACEHOLDER.findall(op.path), "1")
    args = {"operation": f"hubspot.{name}", "path": path,
            "body": {"properties": {"city": "Oslo", "phone": "1"}}}  # fmt: skip
    back = vendor.OperationUndo()
    kept = await back.before(connection, args)
    assert kept["fields"] == {"city": "Riga", "phone": None}
    assert echo.requests[0].url.params["properties"] == "city,phone"
    await back.restore(connection, kept)
    assert json.loads(echo.requests[-1].content) == {"properties": {"city": "Riga", "phone": ""}}


@pytest.mark.parametrize(
    ("body", "amount"),
    [
        ({"amount": 500, "currency": "jpy"}, "500 JPY"),
        ({"amount": 500, "currency": "usd"}, "5.00 USD"),
        ({"amount": 500}, "500 (smallest units) (currency not stated)"),
    ],
)
def test_whole_currencies_arent_divided(body: dict[str, Any], amount: str) -> None:
    assert vendor.card_of(Money(minor_units=True), body).amount == amount


def test_a_money_field_can_be_inside_a_list() -> None:
    rule = Money(("input.transactions.0.amount",), ("input.currency",))
    body = {"input": {"transactions": [{"amount": "9.50"}], "currency": "usd"}}
    assert vendor.card_of(rule, body).amount == "9.50 USD"


def test_every_vendors_sign_in_exists() -> None:
    for v in vendor.VENDORS.values():
        assert not v.provider or v.provider in developer_apps.PROVIDERS, v.key
    microsoft = developer_apps.PROVIDERS["microsoft"]
    for key in microsoft.vendor_scopes:
        assert key in vendor.VENDORS


# --- Clerk ---


@pytest.mark.parametrize(
    ("operation", "kind", "typed"),
    [
        ("clerk.list_users", ToolKind.READ, False),
        ("clerk.get_session", ToolKind.READ, False),
        ("clerk.list_organizations", ToolKind.READ, False),
        ("clerk.invite_user", ToolKind.WRITE, False),
        ("clerk.delete_user", ToolKind.WRITE, True),
        ("clerk.ban_user", ToolKind.WRITE, True),
        ("clerk.revoke_session", ToolKind.WRITE, True),
        ("clerk.update_instance", ToolKind.WRITE, True),
        ("clerk.update_restrictions", ToolKind.WRITE, True),
        ("clerk.delete_organization", ToolKind.WRITE, True),
    ],
)
def test_clerks_rules(operation: str, kind: ToolKind, typed: bool) -> None:
    path = {"user_id": "user_1", "session_id": "sess_1", "organization_id": "org_1"}
    args: dict[str, Any] = {"operation": operation, "path": path}
    assert classify(clerk(), args) is kind
    assert (vendor.typed_reason(args) is not None) is typed


@pytest.mark.parametrize(
    ("operation", "body", "typed"),
    [
        ("clerk.update_user_metadata", {"public_metadata": {"nickname": "A"}}, False),
        ("clerk.update_user_metadata", {"public_metadata": {"role": "admin"}}, True),
        ("clerk.update_user_metadata", {"private_metadata": {"a": {"permissions": []}}}, True),
        ("clerk.invite_to_organization", {"role": "org:member"}, False),
        ("clerk.invite_to_organization", {"role": "org:admin"}, True),
        ("clerk.set_organization_role", {"role": "org:member"}, True),
    ],
)
def test_who_may_do_what_needs_the_name(operation: str, body: dict[str, Any], typed: bool) -> None:
    assert (vendor.typed_reason({"operation": operation, "body": body}) is not None) is typed


def test_the_ways_around_clerks_rules_arent_there() -> None:
    ops = vendor.VENDORS["clerk"].ops
    for path in ("/users/{user_id}", "/sign_in_tokens", "/sessions"):
        assert not [o for o in ops if o.path == path and o.method in ("PATCH", "POST")]
    with pytest.raises(ToolError, match="doesn't take password"):
        classify(clerk(), {
            "operation": "clerk.update_user_metadata", "path": {"user_id": "user_1"},
            "body": {"password": "x"},
        })  # fmt: skip


@pytest.mark.parametrize("value", ["../../instance", "a/b", "a b", "..", ""])
def test_a_path_value_cant_climb_out(value: str) -> None:
    with pytest.raises(ToolError, match="plain ID"):
        classify(clerk(), {"operation": "clerk.get_user", "path": {"user_id": value}})


def test_another_vendors_operation_is_refused() -> None:
    fake = Op("x", "GET", "/x", "x", READ)
    vendor.OPERATIONS["other.x"] = (
        Vendor("other", "O", "Other", "", "https://x.example", (fake,)),
        fake,
    )
    try:
        with pytest.raises(ToolError, match="isn't an operation of this Clerk"):
            classify(clerk(), {"operation": "other.x"})
    finally:
        del vendor.OPERATIONS["other.x"]


@pytest.mark.parametrize(
    ("key", "env", "fits"),
    [
        (TEST_KEY, Env.DEV, True),
        (LIVE_KEY, Env.PROD, True),
        (LIVE_KEY, Env.DEV, False),
        (TEST_KEY, Env.PROD, False),
        (LIVE_KEY, Env.STAGING, False),
    ],
)
def test_the_key_says_test_or_production(key: str, env: Env, fits: bool) -> None:
    secret = json.dumps({"vendor": "clerk", "api_key": key})
    if fits:
        vendor.check_env(secret, env)
    else:
        with pytest.raises(ToolError, match="choose"):
            vendor.check_env(secret, env)


def test_a_key_that_isnt_clerks_is_refused() -> None:
    with pytest.raises(RuleViolationError, match="look like a Clerk"):
        secrets_in.check_connection_secret(
            InstanceType.VENDOR, json.dumps({"vendor": "clerk", "api_key": "pk_test_abc"})
        )


async def test_a_read_runs_with_the_connections_key(
    linked: None, vault: Vault, api: FakeApi
) -> None:
    found = await run_call(call("clerk.get_user", path={"user_id": "user_1"}), vault)
    assert found["id"] == "user_1"
    sent = api.requests[0]
    assert sent.headers["Authorization"] == f"Bearer {TEST_KEY}"
    assert sent.headers["Clerk-API-Version"] == "2026-05-12"


async def test_a_ban_waits_on_test_data_and_cant_be_trusted_or_answered_from_chat(
    linked: None, vault: Vault, api: FakeApi
) -> None:
    held = await run_call(call("clerk.ban_user", path={"user_id": "user_1"}), vault)
    assert held["status"] == "held_for_approval"
    assert not api.requests
    with session_scope() as session:
        decision = decisions.get_decision(session, held["decision_id"])
        assert inbox.heading(decision) == "Ban a user"
        with pytest.raises(RuleViolationError, match="always asks"):
            trusts.grant(session, decision, by="owner")
        for via in ("telegram", "slack", "discord"):
            with pytest.raises(RuleViolationError, match="in the Ringkeeper app"):
                decisions.check_confirmation(decision, "acme-clerk", via)
        assert not inbox.quick_approval(decision)


async def test_trusting_one_operation_doesnt_trust_another(
    linked: None, vault: Vault, api: FakeApi
) -> None:
    held = await run_call(call("clerk.invite_user", body={"email_address": "a@b.example"}), vault)
    with session_scope() as session:
        trusts.grant(session, decisions.get_decision(session, held["decision_id"]), by="owner")
    again = await run_call(call("clerk.invite_user", body={"email_address": "c@b.example"}), vault)
    assert again.get("ok") is True  # the same operation runs for the hour
    other = await run_call(call("clerk.revoke_invitation", path={"invitation_id": "inv_1"}), vault)
    assert other["status"] == "held_for_approval"


async def test_a_card_number_is_refused_before_it_reaches_clerk(
    linked: None, vault: Vault, api: FakeApi
) -> None:
    card = {"public_metadata": {"note": "4242 4242 4242 4242"}}
    with pytest.raises(RefusalError, match="card number"):
        await run_call(
            call("clerk.update_user_metadata", path={"user_id": "user_1"}, body=card), vault
        )
    assert not api.requests


def test_only_metadata_can_be_undone_here() -> None:
    spec = vendor.call_operation
    edit = {"operation": "clerk.update_user_metadata"}
    assert undo.why_not(InstanceType.VENDOR, spec, edit) is None
    ban = {"operation": "clerk.ban_user"}
    assert undo.why_not(InstanceType.VENDOR, spec, ban) == "Ringkeeper can't undo this here."


async def test_undoing_a_metadata_edit_puts_it_back(api: FakeApi) -> None:
    path = {"user_id": "user_1"}
    args = {"operation": "clerk.update_user_metadata", "path": path}
    back = vendor.OperationUndo()
    kept = await back.before(clerk(), args)
    v, op = vendor.OPERATIONS["clerk.update_user_metadata"]
    fields = {"vendor": "clerk", "api_key": TEST_KEY}
    await vendor.run(v, op, fields, path=path, body={"public_metadata": {"b": 2}})
    assert api.user["public_metadata"] == {"a": 1, "b": 2}
    await back.restore(clerk(), kept)
    assert api.user["public_metadata"] == {"a": 1}
    assert back.describe(kept) == "Puts back the user's metadata."


async def test_the_test_button_says_which_instance(api: FakeApi) -> None:
    assert await vendor.probe(clerk()) == "Connected to Clerk: the development instance ins_1."


@pytest.mark.usefixtures("api", "linked")
def test_check_my_setup_also_counts_the_operations() -> None:
    count = sum(1 for name in vendor.OPERATIONS if name.startswith("clerk."))
    said = probes.test_connection("acme-clerk", by="setup-check", counted=True)
    assert said.ok
    assert said.message == (
        f"Connected to Clerk: the development instance ins_1. It offers {count} operations."
    )
    # The Test button itself says what it said before.
    assert probes.test_connection("acme-clerk", by="owner").message.endswith("ins_1.")


def test_a_projects_clerk_key_is_found(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text(f"CLERK_SECRET_KEY={LIVE_KEY}\nOTHER=1\n", encoding="utf-8")
    found = [f for f in env_scan.scan(tmp_path) if f[0].service == "vendor"]
    assert len(found) == 1
    finding, secret, _ = found[0]
    assert (finding.label, finding.env, finding.usable) == ("Clerk", "prod", True)
    assert LIVE_KEY not in finding.masked
    assert json.loads(secret) == {"vendor": "clerk", "api_key": LIVE_KEY}
