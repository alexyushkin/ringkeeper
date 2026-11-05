"""CRM and field service tables: what each vendor's rules say, and the traps in their APIs."""

import json
import re
from collections.abc import Iterator
from typing import Any

import httpx2
import pytest

from ringkeeper.adapters import vendor
from ringkeeper.adapters.base import Target, ToolError, ToolKind
from ringkeeper.adapters.vendor_spec import FINAL, HELD, PLACEHOLDER, READ, Op, Vendor
from ringkeeper.adapters.vendors import crm, field_service
from ringkeeper.models import Env
from ringkeeper.vault import Secret

TABLES = (*crm.VENDORS, *field_service.VENDORS, field_service.SERVICETITAN)
EVERY_OP = [(v, op) for v in TABLES for op in v.ops]
EVERY_NAME = [f"{v.key}.{op.name}" for v, op in EVERY_OP]
pytestmark = pytest.mark.anyio
KEYS = {
    "hubspot": {"access_token": "hs-token"},
    "salesforce": {"access_token": "sf-token", "instance_host": "acme.my.salesforce.com"},
    "pipedrive": {"api_key": "pd-token"},
    "keap": {"api_key": "kp-token"},
    "jobber": {"access_token": "jb-token"},
    "housecallpro": {"api_key": "hcp-token"},
    "servicetitan": {"app_key": "st-key", "default_account": "1234"},
}


@pytest.fixture(autouse=True)
def registered(monkeypatch: pytest.MonkeyPatch) -> None:
    """These tables, as the app will see them once they're in adapters.vendors."""
    for table in TABLES:
        monkeypatch.setitem(vendor.VENDORS, table.key, table)
        for op in table.ops:
            monkeypatch.setitem(vendor.OPERATIONS, f"{table.key}.{op.name}", (table, op))


class FakeApi:
    def __init__(self, record: dict[str, Any]) -> None:
        self.record = record
        self.requests: list[httpx2.Request] = []

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        if request.url.path == "/connect/token":
            return httpx2.Response(200, json={"access_token": "traded", "expires_in": 900})
        if request.method == "GET":
            return httpx2.Response(200, json=self.record)
        return httpx2.Response(200, json={"ok": True})

    def sent(self) -> dict[str, Any]:
        return json.loads(self.requests[-1].content)


@pytest.fixture
def api(request: pytest.FixtureRequest) -> Iterator[FakeApi]:
    fake = FakeApi(getattr(request, "param", {}))
    before = vendor.transport
    vendor.transport = httpx2.MockTransport(fake.handle)
    yield fake
    vendor.transport = before


def fields(key: str) -> dict[str, Any]:
    return {"vendor": key, **KEYS[key]}


def target(key: str) -> Target:
    return Target("t", Env.DEV, None, Secret("r", json.dumps(fields(key))))


def classify(key: str, args: dict[str, Any]) -> ToolKind:
    assert vendor.call_operation.classify is not None
    return vendor.call_operation.classify(target(key), args)


def path_for(op: Op) -> dict[str, str]:
    return {name: "1" for name in PLACEHOLDER.findall(op.path) if name != "account"}


# --- What each vendor's rules say ---


@pytest.mark.parametrize(
    ("operation", "kind", "typed"),
    [
        ("hubspot.search_contacts", ToolKind.READ, False),
        ("hubspot.update_deal", ToolKind.WRITE, False),
        ("hubspot.delete_contact", ToolKind.WRITE, True),
        ("hubspot.merge_contacts", ToolKind.WRITE, True),
        ("hubspot.update_contacts", ToolKind.WRITE, True),
        ("hubspot.send_email", ToolKind.WRITE, True),
        ("hubspot.enroll_in_sequence", ToolKind.WRITE, True),
        ("salesforce.query", ToolKind.READ, False),
        ("salesforce.update_opportunity", ToolKind.WRITE, False),
        ("salesforce.delete_lead", ToolKind.WRITE, True),
        ("salesforce.send_email", ToolKind.WRITE, True),
        ("pipedrive.get_deal", ToolKind.READ, False),
        ("pipedrive.update_person", ToolKind.WRITE, False),
        ("pipedrive.merge_deals", ToolKind.WRITE, True),
        ("pipedrive.delete_organization", ToolKind.WRITE, True),
        ("keap.list_contacts", ToolKind.READ, False),
        ("keap.update_contact", ToolKind.WRITE, False),
        ("keap.merge_contacts", ToolKind.WRITE, True),
        ("keap.send_email", ToolKind.WRITE, True),
        ("keap.add_to_sequence", ToolKind.WRITE, True),
        ("keap.apply_tag", ToolKind.WRITE, True),
        ("jobber.list_clients", ToolKind.READ, False),
        ("jobber.edit_client", ToolKind.WRITE, False),
        ("jobber.delete_jobs", ToolKind.WRITE, True),
        ("jobber.email_quote_or_invoice", ToolKind.WRITE, True),
        ("housecallpro.list_jobs", ToolKind.READ, False),
        ("housecallpro.update_customer", ToolKind.WRITE, False),
        ("housecallpro.dispatch_job", ToolKind.WRITE, True),
        ("housecallpro.delete_appointment", ToolKind.WRITE, True),
        ("servicetitan.get_job", ToolKind.READ, False),
        ("servicetitan.update_job", ToolKind.WRITE, False),
        ("servicetitan.cancel_job", ToolKind.WRITE, True),
    ],
)
def test_each_vendors_rules(operation: str, kind: ToolKind, typed: bool) -> None:
    key = operation.split(".", maxsplit=1)[0]
    _, op = vendor.OPERATIONS[operation]
    args: dict[str, Any] = {"operation": operation, "path": path_for(op)}
    assert classify(key, args) is kind
    assert (vendor.typed_reason(args) is not None) is typed


# --- What every table here promises ---


@pytest.mark.parametrize(("table", "op"), EVERY_OP, ids=EVERY_NAME)
def test_a_table_keeps_its_promises(table: Vendor, op: Op) -> None:
    if op.method == "DELETE":
        assert op.level == FINAL
    if "batch" in op.path or "bulk" in op.path or "merge" in op.path.lower():
        assert op.level == FINAL
    if op.level == READ and op.graphql:
        assert not op.graphql.lstrip().startswith("mutation")
    if op.level != READ and op.graphql:
        assert op.graphql.lstrip().startswith("mutation")
    assert op.says


@pytest.mark.parametrize(
    ("table", "op"), [(v, o) for v, o in EVERY_OP if o.undo], ids=lambda x: getattr(x, "name", "")
)
def test_an_undo_can_read_and_write_what_it_keeps(table: Vendor, op: Op) -> None:
    assert op.undo is not None
    reader = next(o for o in table.ops if o.name == op.undo.read)
    assert PLACEHOLDER.findall(reader.path) == PLACEHOLDER.findall(op.path)
    if op.only:
        assert set(op.undo.keep) <= set(op.only)


@pytest.mark.parametrize("table", TABLES, ids=lambda v: v.key)
def test_nothing_reaches_a_secret_or_runs_code(table: Vendor) -> None:
    banned = r"smtp-tokens|password|/oauth|account-actions|/tooling|/apexrest|/composite|/process"
    for op in table.ops:
        assert not re.search(banned, op.path), op.name


@pytest.mark.parametrize("table", [crm.HUBSPOT, crm.SALESFORCE, field_service.JOBBER])
def test_a_signed_in_vendor_wont_take_a_pasted_key(table: Vendor) -> None:
    with pytest.raises(ToolError, match="sign in"):
        vendor.settings(json.dumps({"vendor": table.key, "api_key": "x"}))


# --- HubSpot ---


def test_hubspot_edits_of_records_can_be_undone() -> None:
    assert vendor.OperationUndo().why_not({"operation": "hubspot.update_contact"}) is None
    assert vendor.OperationUndo().why_not({"operation": "hubspot.update_task"})


def test_a_hubspot_edit_takes_only_properties() -> None:
    args = {"operation": "hubspot.update_contact", "path": {"contact_id": "1"},
            "body": {"archived": True}}  # fmt: skip
    with pytest.raises(ToolError, match="doesn't take archived"):
        classify("hubspot", args)


async def test_a_hubspot_search_reads_and_uses_the_sign_in(api: FakeApi) -> None:
    v, op = vendor.OPERATIONS["hubspot.search_deals"]
    await vendor.run(v, op, fields("hubspot"), body={"query": "Acme"})
    sent = api.requests[0]
    assert (sent.method, sent.url.path) == ("POST", "/crm/v3/objects/deals/search")
    assert sent.headers["Authorization"] == "Bearer hs-token"


# --- Salesforce ---


@pytest.mark.parametrize(
    ("host", "fits"),
    [
        ("acme.my.salesforce.com", True),
        ("acme--uat.sandbox.my.salesforce.com", True),
        ("acme-dev-ed.develop.my.salesforce.com", True),
        ("my.salesforce.com", False),
        ("evil.example", False),
        ("acme.my.salesforce.com.evil.example", False),
        ("acme.my.salesforce.com/x", False),
        ("a.b.c.my.salesforce.com", False),
    ],
)
def test_the_token_only_goes_to_a_salesforce_address(host: str, fits: bool) -> None:
    values = {**fields("salesforce"), "instance_host": host}
    base = vendor._base  # pyright: ignore[reportPrivateUsage]
    if fits:
        assert base(crm.SALESFORCE, values, None).startswith(f"https://{host}/")
    else:
        with pytest.raises(ToolError, match="isn't an address"):
            base(crm.SALESFORCE, values, None)


def test_salesforce_writes_only_to_named_objects() -> None:
    changes = [op for op in crm.SALESFORCE.ops if op.level != READ]
    assert all(
        re.match(r"/sobjects/(Contact|Account|Opportunity|Lead|Task|Note)\b", op.path)
        or op.name == "send_email"
        for op in changes
    )


@pytest.mark.parametrize(
    "api", [{"attributes": {}, "Id": "003", "LastName": "Old", "Email": None}], indirect=True
)
async def test_undoing_a_salesforce_edit_writes_the_old_fields(api: FakeApi) -> None:
    args = {
        "operation": "salesforce.update_contact", "path": {"record_id": "003"},
        "body": {"LastName": "New"},
    }  # fmt: skip
    back = vendor.OperationUndo()
    kept = await back.before(target("salesforce"), args)
    await back.restore(target("salesforce"), kept)
    assert api.requests[-1].method == "PATCH"
    assert api.requests[-1].url.path == "/services/data/v68.0/sobjects/Contact/003"
    assert api.sent() == {"LastName": "Old"}
    assert back.describe(kept) == "Puts back the contact's standard fields."


# --- Pipedrive ---


@pytest.mark.parametrize(
    ("operation", "field"),
    [
        ("pipedrive.update_deal", "is_deleted"),
        ("pipedrive.update_deal", "visible_to"),
        ("pipedrive.update_person", "visible_to"),
        ("pipedrive.update_person", "marketing_status"),
        ("pipedrive.update_organization", "visible_to"),
        ("pipedrive.update_activity", "attendees"),
    ],
)
def test_a_pipedrive_edit_cant_carry_a_delete_or_a_change_of_access(
    operation: str, field: str
) -> None:
    _, op = vendor.OPERATIONS[operation]
    args = {"operation": operation, "path": path_for(op), "body": {field: 1}}
    with pytest.raises(ToolError, match=f"doesn't take {field}"):
        classify("pipedrive", args)


@pytest.mark.parametrize("api", [{"data": {"id": 7, "title": "Old", "value": 5}}], indirect=True)
async def test_undoing_a_pipedrive_edit_reads_inside_data(api: FakeApi) -> None:
    args = {
        "operation": "pipedrive.update_deal", "path": {"deal_id": "7"},
        "body": {"title": "New", "value": 9},
    }  # fmt: skip
    back = vendor.OperationUndo()
    kept = await back.before(target("pipedrive"), args)
    await back.restore(target("pipedrive"), kept)
    assert api.requests[-1].url.path == "/api/v2/deals/7"
    assert api.requests[-1].headers["x-api-token"] == "pd-token"
    assert api.sent() == {"title": "Old", "value": 5}


# --- Keap ---


@pytest.mark.parametrize("api", [{"id": "9", "given_name": "Ann"}], indirect=True)
async def test_undoing_a_keap_edit_says_how_much_it_covers(api: FakeApi) -> None:
    args = {
        "operation": "keap.update_contact", "path": {"contact_id": "9"},
        "body": {"given_name": "Bo"},
    }  # fmt: skip
    back = vendor.OperationUndo()
    kept = await back.before(target("keap"), args)
    await back.restore(target("keap"), kept)
    assert api.requests[-1].url.path == "/crm/rest/v2/contacts/9"
    assert api.requests[-1].headers["Authorization"] == "Bearer kp-token"
    assert api.sent() == {"given_name": "Ann"}
    assert back.describe(kept) == "Puts back the contact's name."


# --- Jobber ---


async def test_a_jobber_call_is_graphql_on_a_pinned_version(api: FakeApi) -> None:
    v, op = vendor.OPERATIONS["jobber.edit_client"]
    await vendor.run(v, op, fields("jobber"), body={"clientId": "c1", "input": {"firstName": "A"}})
    sent = api.requests[0]
    assert (sent.method, sent.url.path) == ("POST", "/api/graphql")
    assert sent.headers["X-JOBBER-GRAPHQL-VERSION"] == field_service.JOBBER_VERSION
    assert sent.headers["Authorization"] == "Bearer jb-token"
    assert api.sent()["variables"] == {"clientId": "c1", "input": {"firstName": "A"}}


def test_jobber_says_what_couldnt_be_confirmed() -> None:
    assert "2022" in field_service.JOBBER.unverified


# --- Housecall Pro ---


@pytest.mark.parametrize(
    ("body", "typed"),
    [
        ({"start_time": "x"}, True),
        ({"notify": True, "notify_pro": False}, True),
        ({"notify": False}, True),
        ({"notify": False, "notify_pro": False}, False),
    ],
)
def test_moving_a_job_needs_the_name_unless_nobody_is_told(
    body: dict[str, Any], typed: bool
) -> None:
    args = {"operation": "housecallpro.reschedule_job", "body": body}
    assert (vendor.typed_reason(args) is not None) is typed


@pytest.mark.parametrize(
    ("body", "typed"),
    [
        ({"customer_id": "c"}, False),
        ({"schedule": {"notify_customer": False}}, False),
        ({"schedule": {"notify_customer": True}}, True),
        ({"schedule": {"start_time": "x"}}, True),
    ],
)
def test_a_scheduled_estimate_can_tell_the_customer(body: dict[str, Any], typed: bool) -> None:
    args = {"operation": "housecallpro.create_estimate", "body": body}
    assert (vendor.typed_reason(args) is not None) is typed


async def test_housecall_pro_signs_in_with_the_word_token(api: FakeApi) -> None:
    v, op = vendor.OPERATIONS["housecallpro.get_company"]
    await vendor.run(v, op, fields("housecallpro"))
    assert api.requests[0].headers["Authorization"] == "Token hcp-token"


# --- ServiceTitan ---


async def test_servicetitan_calls_name_the_tenant(api: FakeApi) -> None:
    v, op = vendor.OPERATIONS["servicetitan.get_customer"]
    await vendor.run(v, op, fields("servicetitan"), path={"customer_id": "5"})
    sent = api.requests[-1]
    assert sent.url.path == "/crm/v2/tenant/1234/customers/5"
    assert sent.headers["ST-App-Key"] == "st-key"


@pytest.mark.parametrize("field", ["active", "creditLimit", "paymentTermId"])
def test_a_servicetitan_customer_edit_stays_a_plain_edit(field: str) -> None:
    args = {"operation": "servicetitan.update_customer", "path": {"customer_id": "5"},
            "body": {field: 1}}  # fmt: skip
    with pytest.raises(ToolError, match=f"doesn't take {field}"):
        classify("servicetitan", args)


def test_levels_are_only_the_three() -> None:
    assert {op.level for _, op in EVERY_OP} == {READ, HELD, FINAL}
