"""Google Ads, against a fake: reports run, spending needs the name typed, the card shows the
real spend, and the daily operations are counted across every client."""

import json
from collections.abc import Iterator
from typing import Any

import httpx2
import pytest

from ringkeeper import destructive
from ringkeeper.adapters import vendor
from ringkeeper.adapters.base import Target, ToolError
from ringkeeper.adapters.vendor_spec import FINAL, HELD, READ
from ringkeeper.api import routes
from ringkeeper.calls import Caller, ToolCall
from ringkeeper.config import get_settings
from ringkeeper.db import session_scope
from ringkeeper.errors import RuleViolationError
from ringkeeper.models import Env, InstanceType
from ringkeeper.services import ads_budget, decisions, developer_apps, google_oauth
from ringkeeper.vault import Secret

pytestmark = pytest.mark.anyio
ADS = vendor.VENDORS["google_ads"]
FIELDS = {"vendor": "google_ads", "access_token": "at-1", "app": "google-app"}
CLIENT_ID = "1234-abc.apps.googleusercontent.com"


class Api:
    def __init__(self) -> None:
        self.requests: list[httpx2.Request] = []
        self.currency = "USD"
        self.budget = "50000000"

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        if request.url.path.endswith("googleAds:search"):
            query = json.loads(request.content).get("query", "")
            if "currency_code" in query:
                return httpx2.Response(200, json={"results": [
                    {"customer": {"currencyCode": self.currency}},
                ]})  # fmt: skip
            if "amount_micros" in query:
                return httpx2.Response(200, json={"results": [
                    {"campaignBudget": {"amountMicros": self.budget}},
                ]})  # fmt: skip
            return httpx2.Response(200, json={"results": [], "nextPageToken": "p2"})
        return httpx2.Response(200, json={"resourceNames": ["customers/1"], "results": []})

    @property
    def changes(self) -> list[httpx2.Request]:
        return [r for r in self.requests if r.url.path.endswith(":mutate")]


@pytest.fixture
def api(database_url: str) -> Iterator[Api]:
    fake = Api()
    before = vendor.transport
    vendor.transport = httpx2.MockTransport(fake.handle)
    yield fake
    vendor.transport = before


def op(name: str) -> Any:
    return vendor.OPERATIONS[f"google_ads.{name}"][1]


def target() -> Target:
    return Target("t", Env.DEV, None, Secret("r", json.dumps(FIELDS)))


async def call(name: str, body: dict[str, Any] | None = None, **path: str) -> dict[str, Any]:
    handler = vendor.call_operation.handler
    return await handler(
        target(), operation=f"google_ads.{name}", path=path or None, body=body, account="1234567890"
    )


def used() -> int:
    with session_scope(commit=False) as session:
        return ads_budget.used(session, "google-app")


# --- The rules ---


@pytest.mark.parametrize(
    ("name", "wanted"),
    [
        ("report", READ), ("list_accounts", READ),
        ("pause_campaign", HELD), ("pause_ad_group", HELD), ("pause_ad", HELD),
        ("create_campaign", FINAL), ("create_ad_group", FINAL), ("create_search_ad", FINAL),
        ("add_keywords", FINAL), ("start_campaign", FINAL), ("start_ad_group", FINAL),
        ("start_ad", FINAL), ("set_daily_budget", FINAL), ("set_ad_group_bid", FINAL),
    ],
)  # fmt: skip
def test_what_each_needs(name: str, wanted: str) -> None:
    assert op(name).level == wanted


def test_spending_is_never_trusted_and_needs_the_name_typed() -> None:
    assert ADS.never_trusted
    args: dict[str, Any] = {"operation": "google_ads.set_daily_budget", "body": {}}
    assert destructive.reason(InstanceType.VENDOR, "call_operation", args)


def test_nothing_reaches_billing_or_who_has_access() -> None:
    for found in ADS.ops:
        lowered = found.path.lower()
        for word in ("billing", "payment", "invoice", "customeruser", "createcustomer"):
            assert word not in lowered, found.name


def test_a_project_can_be_held_to_one_ad_account() -> None:
    assert ADS.scope[0].key == "customer"
    assert ADS.scope[0].args == ("account",)


# --- What the card shows ---


@pytest.mark.parametrize(
    ("name", "body", "amount", "who"),
    [
        ("set_daily_budget", {"budget_id": "9", "daily_amount": 75.5, "currency": "usd"},
         "75.50 USD a day", "9"),
        ("create_campaign", {"name": "Spring", "daily_amount": "20", "currency": "EUR"},
         "20 EUR a day", "Spring"),
        ("start_campaign", {"campaign_id": "7", "daily_amount": 50.0, "currency": "USD"},
         "50.00 USD a day", "7"),
        ("set_ad_group_bid", {"ad_group_id": "5", "cpc_bid": 1.25, "currency": "USD"},
         "1.25 USD a click", "5"),
    ],
)  # fmt: skip
def test_the_card_shows_the_spend(name: str, body: dict[str, Any], amount: str, who: str) -> None:
    card = vendor.money_card({
        "operation": f"google_ads.{name}", "body": body, "account": "1234567890",
    })  # fmt: skip
    assert card is not None
    assert card.amount == amount
    assert card.counterparty == who
    assert card.account == "1234567890"


def test_the_card_gets_the_spend_and_offers_no_trust(database_url: str) -> None:
    """From the API, not just the table: the card is drawn from what comes back."""
    args = {
        "operation": "google_ads.set_daily_budget", "account": "1234567890",
        "body": {"budget_id": "9", "daily_amount": 75.5, "currency": "USD"},
    }  # fmt: skip
    held = ToolCall(Caller("p", "s", "t"), InstanceType.VENDOR, "call_operation", "ads", args)
    with session_scope() as session:
        made = decisions.hold(session, held, "ads")
        shown = routes._decision_out(made)  # pyright: ignore[reportPrivateUsage]
    assert shown.money is not None
    assert shown.money["amount"] == "75.50 USD a day"
    assert shown.trustable is False


# --- Calls ---


async def test_a_report_says_which_account_and_manager(api: Api) -> None:
    await call("report", {"query": "SELECT campaign.id FROM campaign"}, manager="9876543210")
    sent = api.requests[-1]
    assert sent.url.path == "/v26/customers/1234567890/googleAds:search"
    assert sent.headers["login-customer-id"] == "9876543210"
    assert sent.headers["authorization"] == "Bearer at-1"
    assert "developer-token" not in sent.headers


async def test_a_manager_is_a_plain_id(api: Api) -> None:
    with pytest.raises(ToolError, match="isn't a plain ID"):
        await call("report", {"query": "SELECT campaign.id FROM campaign"}, manager="a b")


async def test_money_goes_as_exact_micros(api: Api) -> None:
    await call("set_daily_budget", {"budget_id": "9", "daily_amount": "19.99", "currency": "USD"})
    sent = json.loads(api.changes[0].content)["operations"][0]
    assert sent["update"]["amountMicros"] == "19990000"
    assert sent["update"]["resourceName"] == "customers/1234567890/campaignBudgets/9"
    assert sent["updateMask"] == "amount_micros"


@pytest.mark.parametrize("amount", [0, -5, "lots", None, "NaN", "Infinity"])
def test_an_amount_that_isnt_one_is_refused_before_its_held(amount: Any) -> None:
    args = {
        "operation": "google_ads.set_daily_budget", "account": "1234567890",
        "body": {"budget_id": "9", "daily_amount": amount, "currency": "USD"},
    }  # fmt: skip
    assert vendor.call_operation.classify is not None
    with pytest.raises(ToolError, match="daily_amount"):
        vendor.call_operation.classify(target(), args)


def test_a_call_cant_slip_in_a_raw_change() -> None:
    args = {
        "operation": "google_ads.set_daily_budget", "account": "1234567890",
        "body": {"operations": [{"remove": "customers/1/campaigns/1"}]},
    }  # fmt: skip
    assert vendor.call_operation.classify is not None
    with pytest.raises(ToolError, match="doesn't take operations"):
        vendor.call_operation.classify(target(), args)


async def test_a_card_in_the_wrong_currency_changes_nothing(api: Api) -> None:
    api.currency = "EUR"
    with pytest.raises(ToolError, match="spends in EUR, not USD"):
        await call("set_daily_budget",
                   {"budget_id": "9", "daily_amount": 20, "currency": "USD"})  # fmt: skip
    assert not api.changes


async def test_starting_a_campaign_checks_the_budget_the_card_showed(api: Api) -> None:
    api.budget = "500000000"
    with pytest.raises(ToolError, match="daily budget is 500, not 50"):
        await call("start_campaign",
                   {"campaign_id": "7", "daily_amount": 50, "currency": "USD"})  # fmt: skip
    assert not api.changes
    api.budget = "50000000"
    await call("start_campaign", {"campaign_id": "7", "daily_amount": 50, "currency": "USD"})
    update = json.loads(api.changes[0].content)["operations"][0]["update"]
    assert update["status"] == "ENABLED"


async def test_a_new_campaign_starts_paused_with_its_budget(api: Api) -> None:
    await call("create_campaign", {"name": "Spring", "daily_amount": 20, "currency": "USD"})
    sent = json.loads(api.changes[0].content)["mutateOperations"]
    budget = sent[0]["campaignBudgetOperation"]["create"]
    campaign = sent[1]["campaignOperation"]["create"]
    assert budget["amountMicros"] == "20000000"
    assert campaign["campaignBudget"] == budget["resourceName"]
    assert campaign["status"] == "PAUSED"
    assert campaign["advertisingChannelType"] == "SEARCH"


async def test_new_ad_groups_and_ads_start_paused(api: Api) -> None:
    await call("create_ad_group", {"campaign_id": "7", "name": "A", "cpc_bid": 1,
                                   "currency": "USD"})  # fmt: skip
    await call("create_search_ad", {
        "ad_group_id": "5", "final_url": "https://example.com", "headlines": ["a", "b", "c"],
        "descriptions": ["d", "e"],
    })  # fmt: skip
    for sent in api.changes:
        assert json.loads(sent.content)["operations"][0]["create"]["status"] == "PAUSED"


async def test_pausing_sends_paused(api: Api) -> None:
    await call("pause_campaign", {"campaign_id": "7"})
    update = json.loads(api.changes[0].content)["operations"][0]["update"]
    assert update == {"resourceName": "customers/1234567890/campaigns/7", "status": "PAUSED"}


# --- The operations every client shares ---


async def test_google_counts_each_change_and_each_report_once(api: Api) -> None:
    await call("report", {"query": "SELECT campaign.id FROM campaign"})
    assert used() == 1
    await call("add_keywords", {"ad_group_id": "5", "keywords": ["a", "b", "c"]})
    assert used() == 4


async def test_a_further_page_of_a_report_is_free(api: Api) -> None:
    await call("report", {"query": "SELECT campaign.id FROM campaign", "pageToken": "p2"})
    assert used() == 0


async def test_a_refused_call_still_counts(api: Api) -> None:
    def refuse(_request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(400, json={"error": {"message": "no"}})

    vendor.transport = httpx2.MockTransport(refuse)
    with pytest.raises(ToolError):
        await call("report", {"query": "SELECT campaign.id FROM campaign"})
    assert used() == 1


async def test_a_check_before_spending_counts_too(api: Api) -> None:
    await call("set_daily_budget", {"budget_id": "9", "daily_amount": 20, "currency": "USD"})
    assert used() == 2


async def test_every_client_shares_one_count(api: Api) -> None:
    await call("report", {"query": "SELECT campaign.id FROM campaign"})
    other = Target("u", Env.DEV, None, Secret("r", json.dumps(FIELDS)))
    await vendor.call_operation.handler(
        other, operation="google_ads.report", body={"query": "SELECT campaign.id FROM campaign"},
        account="5555555555",
    )  # fmt: skip
    assert used() == 2


def test_only_the_last_24_hours_count(database_url: str) -> None:
    now = 1_900_000_000.0
    ads_budget.add("a", 5, now=now - 25 * 3600)
    ads_budget.add("a", 7, now=now - 23 * 3600)
    ads_budget.add("a", 1, now=now)
    with session_scope(commit=False) as session:
        assert ads_budget.used(session, "a", now=now) == 8


def test_each_google_app_is_its_own_project(database_url: str) -> None:
    ads_budget.add("a", 5)
    ads_budget.add("b", 7)
    with session_scope(commit=False) as session:
        assert ads_budget.used(session, "a") == 5


def test_settings_shows_explorers_limit_until_told_otherwise(database_url: str) -> None:
    ads_budget.add("a", 5)
    with session_scope() as session:
        assert ads_budget.shown(session, "a") == {"level": "explorer", "used": 5, "limit": 2880}
        ads_budget.set_level(session, "a", "basic")
    with session_scope() as session:
        assert ads_budget.shown(session, "a")["limit"] == 15000
        ads_budget.set_level(session, "a", "standard")
        assert ads_budget.shown(session, "a")["limit"] is None
        assert ads_budget.used(session, "a") == 5
        with pytest.raises(RuleViolationError):
            ads_budget.set_level(session, "a", "unlimited")


# --- Signing in through the Google app ---


def test_it_signs_in_through_the_google_app_with_no_new_app(database_url: str) -> None:
    settings = get_settings().model_copy(update={"web_origin": "http://localhost:17845"})
    google_oauth.save_app(settings, CLIENT_ID, "s3cret-value")
    assert [a.client_id for a in developer_apps.list_apps(settings, "google")] == [CLIENT_ID]
    url, _ = developer_apps.start(settings, "google_ads", {"owner": "acme", "env": "dev"})
    assert url.startswith(google_oauth.AUTHORIZE)
    assert "adwords" in url
    assert "access_type=offline" in url


def test_google_isnt_a_second_card_on_developer_apps(database_url: str) -> None:
    settings = get_settings()
    assert not developer_apps.provider("google").listed
    with pytest.raises(RuleViolationError, match="their own card"):
        developer_apps.save_app(settings, "google", "Mine", "c", "s")
    with pytest.raises(RuleViolationError, match="their own card"):
        developer_apps.forget_app(settings, "google", "x")
