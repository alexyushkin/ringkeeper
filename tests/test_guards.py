"""Services on the shelf whose rules can't be lowered: CRM, accounting, CMS and payments."""

import json

import pytest

from ringkeeper import destructive, remote
from ringkeeper.adapters import mcp
from ringkeeper.errors import RuleViolationError
from ringkeeper.models import Env, InstanceType
from ringkeeper.services import inbox, remote_connections
from ringkeeper.services.remote_connections import NewConnection


def config(key: str, **fields: object) -> dict[str, object]:
    return {"remote": key, **fields}


def level(key: str, tool: str, **fields: object) -> str:
    return mcp.rule(config(key, **fields), tool).level


@pytest.mark.parametrize(
    ("service", "tool", "needs"),
    [
        ("close", "list_leads", mcp.READ),
        ("close", "update_lead", mcp.APPROVE),
        ("close", "delete_lead", mcp.CONFIRM),
        ("close", "merge_leads", mcp.CONFIRM),
        ("close", "send_email", mcp.CONFIRM),
        ("close", "create_sms", mcp.CONFIRM),
        ("attio", "bulk_update_records", mcp.CONFIRM),
        ("gohighlevel", "execute_operation", mcp.CONFIRM),
        ("monday", "all_monday_api", mcp.CONFIRM),
        ("zoho_books", "list_invoices", mcp.READ),
        ("zoho_books", "create_invoice", mcp.APPROVE),
        ("zoho_books", "send_invoice", mcp.CONFIRM),
        ("zoho_books", "void_invoice", mcp.CONFIRM),
        ("zoho_books", "create_journal", mcp.CONFIRM),
        ("zoho_books", "record_payment", mcp.CONFIRM),
        ("sanity", "patch_documents", mcp.APPROVE),
        ("sanity", "publish_documents", mcp.CONFIRM),
        ("sanity", "unpublish_documents", mcp.CONFIRM),
        ("webflow", "update_site_settings", mcp.CONFIRM),
        ("wix", "CallWixSiteAPI", mcp.CONFIRM),
        ("wordpress", "install_plugin", mcp.CONFIRM),
        ("stripe", "list_customers", mcp.READ),
        ("stripe", "create_customer", mcp.APPROVE),
        ("stripe", "create_payment_link", mcp.APPROVE),
        ("stripe", "create_refund", mcp.CONFIRM),
        ("stripe", "cancel_subscription", mcp.CONFIRM),
        ("stripe", "create_payout", mcp.CONFIRM),
        ("paypal", "capture_order", mcp.CONFIRM),
        ("gocardless", "create_payment", mcp.CONFIRM),
        ("apollo", "people_enrichment", mcp.APPROVE),
        ("apify", "call_actor", mcp.APPROVE),
        ("upwork", "send_message", mcp.APPROVE),
        ("pipeboard", "update_campaign_budget", mcp.CONFIRM),
    ],
)
def test_what_each_kind_of_service_needs(service: str, tool: str, needs: str) -> None:
    assert level(service, tool) == needs


@pytest.mark.parametrize(
    ("service", "tool"),
    [("stripe", "create_refund"), ("zoho_books", "void_invoice"), ("sanity", "publish_documents")],
)
def test_the_per_tool_setting_cant_lower_a_name_typed_tool(service: str, tool: str) -> None:
    for chosen in (mcp.READ, mcp.APPROVE):
        assert level(service, tool, tool_rules={tool: chosen}) == mcp.CONFIRM
    legacy = level(service, tool, read_only_tools=[tool])  # the first version's ticks
    assert legacy == mcp.CONFIRM


def test_the_servers_own_read_only_hint_cant_lower_it_either() -> None:
    annotations = {"create_refund": {"read_only": True}}
    assert level("stripe", "create_refund", annotations=annotations) == mcp.CONFIRM
    annotations = {"apply_credit": {"read_only": True}}
    assert level("stripe", "apply_credit", annotations=annotations) == mcp.APPROVE


def test_a_change_cant_be_set_to_run_without_asking() -> None:
    assert level("close", "update_lead", tool_rules={"update_lead": mcp.READ}) == mcp.APPROVE


def test_a_tool_can_still_be_made_stricter() -> None:
    assert level("close", "update_lead", tool_rules={"update_lead": mcp.CONFIRM}) == mcp.CONFIRM


def test_a_service_with_no_guard_keeps_the_users_choice() -> None:
    assert level("linear", "update_issue", tool_rules={"update_issue": mcp.READ}) == mcp.READ


def test_every_guard_named_exists_and_every_opaque_tool_is_on_a_guarded_service() -> None:
    for service in remote.CATALOG:
        assert not service.guard or service.guard in remote.GUARDS, service.key
        assert not service.opaque_tools or service.guard, service.key
        assert bool(service.url) != bool(service.url_label), service.key


@pytest.mark.parametrize("key", [r.key for r in remote.CATALOG if r.guard == "accounting"])
def test_no_accounting_change_is_ever_trusted(key: str) -> None:
    args = {"tool": "create_invoice"}
    assert destructive.never_trusted(InstanceType.MCP, "call_tool", args, config(key))


def test_other_services_can_be_trusted_with_a_held_action() -> None:
    args = {"tool": "update_lead"}
    assert not destructive.never_trusted(InstanceType.MCP, "call_tool", args, config("close"))


def test_the_reason_says_why_it_needs_the_name() -> None:
    args: dict[str, object] = {"tool": "create_refund", "arguments": {}}
    why = destructive.reason(InstanceType.MCP, "call_tool", args, config("stripe"))
    assert why == "runs 'create_refund', which needs the name typed on this kind of service"


def own_address(key: str, url: str = "https://cms.example.com/mcp") -> NewConnection:
    return NewConnection(name="x", owner="acme", env=Env.DEV, remote=key, url=url)


def test_a_customers_own_address_is_asked_for() -> None:
    with pytest.raises(RuleViolationError, match="your strapi mcp address"):
        remote_connections.resolve(NewConnection(name="x", owner="a", env=Env.DEV, remote="strapi"))
    assert remote_connections.resolve(own_address("strapi")).url == "https://cms.example.com/mcp"


def test_a_fixed_address_cant_be_changed() -> None:
    new = own_address("stripe", "https://evil.example.com/mcp")
    assert remote_connections.resolve(new).url == "https://mcp.stripe.com"


def test_a_wordpress_password_goes_as_basic() -> None:
    secret = remote_connections.token_secret(own_address("wordpress"), "ann:abcd efgh ijkl")
    assert json.loads(secret)["headers"]["Authorization"] == "Basic YW5uOmFiY2RlZmdoaWprbA=="
    with pytest.raises(RuleViolationError, match="user:password"):
        remote_connections.token_secret(own_address("wordpress"), "no-colon")


def test_a_key_in_an_address_isnt_shown_and_is_scrubbed() -> None:
    key = "a1b2c3d4e5f6a7b8c9d0e1f2a3b4"
    url = f"https://acme-123.zohomcp.com/mcp/{key}/message"
    new = own_address("zoho_crm", url)
    shown = remote_connections._shown(remote_connections.resolve(new))  # pyright: ignore[reportPrivateUsage]
    assert shown == "acme-123.zohomcp.com"
    assert key in mcp.secret_fragments(remote_connections.open_secret(new))


def test_the_shelf_says_where_each_service_belongs() -> None:
    shelf = {s["key"]: s for s in remote_connections.catalog_shelf()}
    assert shelf["stripe"]["category"] == "Payments"
    assert shelf["strapi"]["url_label"]


def test_inbox_has_a_card_for_money() -> None:
    assert inbox.money
