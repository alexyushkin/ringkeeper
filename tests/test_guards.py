"""Services on the shelf whose rules can't be lowered: CRM, accounting, CMS and payments."""

import json
from datetime import UTC, datetime, timedelta

import pytest

from ringkeeper import destructive, remote
from ringkeeper.adapters import mcp, vendor
from ringkeeper.adapters.vendor_spec import Money
from ringkeeper.db import session_scope
from ringkeeper.errors import RuleViolationError
from ringkeeper.models import Decision, DecisionKind, Env, InstanceType
from ringkeeper.services import inbox, remote_connections
from ringkeeper.services.remote_connections import NewConnection
from tests.helpers import make_instance


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
        ("meta_ads", "ads_create_campaign", mcp.CONFIRM),
        ("meta_ads", "ads_activate_entity", mcp.CONFIRM),
        ("meta_ads", "ads_create_creative", mcp.APPROVE),
        ("meta_ads", "ads_get_ad_entities", mcp.READ),
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
        assert sum(map(bool, (service.url, service.url_label, service.local))) == 1, service.key


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


@pytest.mark.parametrize(
    ("body", "minor", "amount"),
    [
        ({"amount": 1250, "currency": "usd"}, True, "12.50 USD"),
        ({"amount_money": {"amount": 1250, "currency": "USD"}}, True, "12.50 USD"),
        ({"amount": "99.00", "currency_code": "eur"}, False, "99.00 EUR"),
        ({"currency": "usd"}, True, "not stated USD"),
        ({"amount": 5}, False, "5 (currency not stated)"),
        ({"amount": {"value": "10.00", "currency": "EUR"}}, False, "10.00 EUR"),
        ({"amount": "about ten", "currency": "usd"}, True, "about ten USD"),
    ],
)
def test_the_money_card_shows_what_the_call_says(
    body: dict[str, object], minor: bool, amount: str
) -> None:
    assert vendor.card_of(Money(minor_units=minor), body).amount == amount


def test_the_money_card_never_guesses_who_or_which_account() -> None:
    card = vendor.card_of(Money(), {"amount": 5, "currency": "usd"})
    assert (card.counterparty, card.account) == ("not stated", "not stated")
    named = vendor.card_of(Money(), {"customer": "cus_1", "destination": "acct_1"})
    assert (named.counterparty, named.account) == ("cus_1", "acct_1")


def _stripe_connection(database_url: str) -> None:
    assert database_url
    with session_scope() as session:
        make_instance(
            session, "acme-stripe", type=InstanceType.MCP, config={"remote": "stripe"},
        )  # fmt: skip


def _held(tool: str, arguments: dict[str, object]) -> Decision:
    return Decision(
        kind=DecisionKind.APPROVE, service=InstanceType.MCP, tool="call_tool",
        instance="acme-stripe", args={"tool": tool, "arguments": arguments},
        expires_at=datetime.now(UTC) + timedelta(days=1), args_hash="hash", project="acme-web",
        session_id="session-1", token_id="token-1",
    )  # fmt: skip


def test_a_payment_servers_refund_gets_the_card_and_a_lookup_doesnt(database_url: str) -> None:
    _stripe_connection(database_url)
    with session_scope() as session:
        refund = _held("create_refund", {"amount": 500, "currency": "usd", "customer": "cus_1"})
        session.add(refund)
        session.flush()
        shown = inbox.money(refund)
        assert shown is not None
        assert (shown["amount"], shown["counterparty"], shown["account"]) == (
            "5.00 USD", "cus_1", "not stated",
        )  # fmt: skip
        assert (shown["who"], shown["which"]) == ("To or from", "Account")
        link = _held("create_payment_link", {"amount": 500})
        session.add(link)
        session.flush()
        assert inbox.money(link) is None
        session.rollback()


@pytest.mark.parametrize(
    ("tool", "level"),
    [
        ("list_agents", mcp.READ), ("get_conversation", mcp.READ), ("search_voices", mcp.READ),
        ("create_agent", mcp.APPROVE), ("update_agent", mcp.APPROVE),
        ("make_outbound_call", mcp.APPROVE), ("clone_voice", mcp.APPROVE),
        ("text_to_speech", mcp.APPROVE), ("delete_agent", mcp.CONFIRM),
        ("remove_voice", mcp.CONFIRM),
    ],
)  # fmt: skip
def test_elevenlabs_goes_by_what_a_tool_does(tool: str, level: str) -> None:
    config = {"remote": "elevenlabs"}
    assert mcp.rule(config, tool).level == level


def test_elevenlabs_cant_be_loosened_or_trusted() -> None:
    config = {"remote": "elevenlabs", "tool_rules": {"make_outbound_call": mcp.READ}}
    assert mcp.rule(config, "make_outbound_call").level == mcp.APPROVE
    assert remote.GUARDS["voice"].never_trusted
    service = remote.BY_KEY["elevenlabs"]
    assert (service.signin, service.paste) == (True, None)  # its server takes no API key
