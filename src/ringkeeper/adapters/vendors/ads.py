"""Advertising: Google Ads, through the owner's own Google app.

Holds, by the user's rule: reports run. Pausing waits for approval: it only stops spending.
Creating a campaign, starting anything, and changing a budget or a bid need the name typed, are
never trusted, and can't be approved from a chat app. The card shows the money, and the call
says it in the account's own currency: each call that spends is checked against Google just
before it runs, so what the card said is what's true.

Everything created here starts paused, and starting it is a call of its own.

There's no developer token: since 2026-09-09 access belongs to the Google Cloud project that owns
the app, and every client's calls come out of that project's one daily allowance. Settings shows
what's been used (services.ads_budget). Google counts a report as one operation, and each change
in a call as one; moving through a report's pages costs nothing.

A client reached through a manager account needs the manager's ID in `path`, as `manager`.

Left out on purpose:
- Payment methods, billing setups and invoices: Ringkeeper never handles them.
- Account creation and who has access to an account.
- Anything but Search campaigns, for now. Each kind of campaign has its own fields.
- Undo: a budget's history isn't something a snapshot can honestly restore.
"""

from decimal import Decimal, InvalidOperation
from typing import Any, cast

from ringkeeper.adapters.base import ToolError
from ringkeeper.adapters.vendor_spec import JSON, Money, Op, Reader, Vendor, final, held, reads
from ringkeeper.remote import ScopeField

CATEGORY = "Ads"
API_VERSION = "v26"
MICROS = Decimal(1_000_000)
MOST_KEYWORDS = 50
CURRENCY_LETTERS = 3
MATCH_TYPES = frozenset({"EXACT", "PHRASE", "BROAD"})
STARTS = "starts spending"
CHANGES_SPEND = "changes what the account spends"
CREATES = "creates something that can spend once it's started"
DAILY = Money(("daily_amount",), ("currency",), ("budget_id", "campaign_id", "name"), ("account",),
              per=" a day")  # fmt: skip
BID = Money(("cpc_bid",), ("currency",), ("ad_group_id", "name"), ("account",), per=" a click")
SPEND_CHECKED = ("daily_amount", "currency")
ACCOUNT_CURRENCY = "SELECT customer.currency_code FROM customer LIMIT 1"
# Google won't make a campaign without this answer, and it's the owner's to give: it's in the
# call, so it's in what's approved, and the card says it in full.
EU_POLITICAL = "eu_political_advertising"
NO_EU_POLITICAL = "none"
DECLARES = "This campaign doesn't contain EU political advertising."


def _id(body: JSON, key: str) -> str:
    value = str(body.get(key) or "")
    if not value.isdigit():
        raise ToolError(f"'{key}' must be Google's number for it")
    return value


def _micros(body: JSON, key: str) -> str:
    """An amount in the account's currency, as Google wants it: exact, not a float's guess."""
    try:
        amount = Decimal(str(body.get(key)))
    except InvalidOperation:
        raise ToolError(f"'{key}' must be an amount, like 25.50") from None
    if not amount.is_finite() or amount <= 0:
        raise ToolError(f"'{key}' must be more than zero")
    return str(int(amount * MICROS))


def _currency(body: JSON) -> str:
    value = str(body.get("currency") or "").upper()
    if len(value) != CURRENCY_LETTERS or not value.isalpha():
        raise ToolError("say the account's currency, like USD: the card shows it")
    return value


def _resource(account: str | None, kind: str, value: str) -> str:
    return f"customers/{account}/{kind}/{value}"


def _status(kind: str, key: str, status: str) -> Any:
    def shape(body: JSON, account: str | None) -> JSON:
        name = _resource(account, kind, _id(body, key))
        return {"operations": [{"updateMask": "status",
                                "update": {"resourceName": name, "status": status}}]}  # fmt: skip

    return shape


def _ad_status(status: str) -> Any:
    def shape(body: JSON, account: str | None) -> JSON:
        name = _resource(account, "adGroupAds", f"{_id(body, 'ad_group_id')}~{_id(body, 'ad_id')}")
        return {"operations": [{"updateMask": "status",
                                "update": {"resourceName": name, "status": status}}]}  # fmt: skip

    return shape


def _spending_campaign(body: JSON, account: str | None) -> JSON:
    _micros(body, "daily_amount")
    _currency(body)
    return cast(JSON, _status("campaigns", "campaign_id", "ENABLED")(body, account))


def _budget(body: JSON, account: str | None) -> JSON:
    _currency(body)
    name = _resource(account, "campaignBudgets", _id(body, "budget_id"))
    update = {"resourceName": name, "amountMicros": _micros(body, "daily_amount")}
    return {"operations": [{"updateMask": "amount_micros", "update": update}]}


def _bid(body: JSON, account: str | None) -> JSON:
    _currency(body)
    name = _resource(account, "adGroups", _id(body, "ad_group_id"))
    update = {"resourceName": name, "cpcBidMicros": _micros(body, "cpc_bid")}
    return {"operations": [{"updateMask": "cpc_bid_micros", "update": update}]}


def _new_campaign(body: JSON, account: str | None) -> JSON:
    """A budget and a paused Search campaign, in one call: they're made together or not at
    all. The -1 is Google's way to name the budget before it exists."""
    title = str(body.get("name") or "").strip()
    if not title:
        raise ToolError("name the campaign")
    _currency(body)
    if body.get(EU_POLITICAL) != NO_EU_POLITICAL:
        raise ToolError(
            f"Google needs to know: send {EU_POLITICAL}: '{NO_EU_POLITICAL}' if this campaign "
            "has none, and the card will ask the owner to declare it. A campaign with EU "
            "political advertising can't be made here"
        )
    budget = _resource(account, "campaignBudgets", "-1")
    return {"mutateOperations": [
        {"campaignBudgetOperation": {"create": {
            "resourceName": budget, "name": f"{title} budget", "deliveryMethod": "STANDARD",
            "amountMicros": _micros(body, "daily_amount"), "explicitlyShared": False,
        }}},
        {"campaignOperation": {"create": {
            "name": title, "advertisingChannelType": "SEARCH", "status": "PAUSED",
            "manualCpc": {}, "campaignBudget": budget,
            "networkSettings": {
                "targetGoogleSearch": True, "targetSearchNetwork": True,
                "targetContentNetwork": False, "targetPartnerSearchNetwork": False,
            },
            "containsEuPoliticalAdvertising": "DOES_NOT_CONTAIN_EU_POLITICAL_ADVERTISING",
        }}},
    ]}  # fmt: skip


def _new_ad_group(body: JSON, account: str | None) -> JSON:
    title = str(body.get("name") or "").strip()
    if not title:
        raise ToolError("name the ad group")
    _currency(body)
    return {"operations": [{"create": {
        "name": title, "status": "PAUSED", "type": "SEARCH_STANDARD",
        "campaign": _resource(account, "campaigns", _id(body, "campaign_id")),
        "cpcBidMicros": _micros(body, "cpc_bid"),
    }}]}  # fmt: skip


def _texts(body: JSON, key: str, least: int) -> list[JSON]:
    found = [str(t).strip() for t in cast(list[Any], body.get(key) or []) if str(t).strip()]
    if len(found) < least:
        raise ToolError(f"a search ad needs at least {least} {key}")
    return [{"text": t} for t in found]


def _new_ad(body: JSON, account: str | None) -> JSON:
    address = str(body.get("final_url") or "")
    if not address.startswith("https://"):
        raise ToolError("say where the ad goes, as an https:// address")
    return {"operations": [{"create": {
        "status": "PAUSED",
        "adGroup": _resource(account, "adGroups", _id(body, "ad_group_id")),
        "ad": {
            "finalUrls": [address],
            "responsiveSearchAd": {
                "headlines": _texts(body, "headlines", 3),
                "descriptions": _texts(body, "descriptions", 2),
            },
        },
    }}]}  # fmt: skip


def _keywords(body: JSON, account: str | None) -> JSON:
    match = str(body.get("match_type") or "PHRASE").upper()
    if match not in MATCH_TYPES:
        raise ToolError(f"match_type is one of {', '.join(sorted(MATCH_TYPES))}")
    words = [str(k).strip() for k in cast(list[Any], body.get("keywords") or []) if str(k).strip()]
    if not words or len(words) > MOST_KEYWORDS:
        raise ToolError(f"add between 1 and {MOST_KEYWORDS} keywords at a time")
    group = _resource(account, "adGroups", _id(body, "ad_group_id"))
    return {"operations": [
        {"create": {"adGroup": group, "status": "ENABLED",
                    "keyword": {"text": word, "matchType": match}}}
        for word in words
    ]}  # fmt: skip


def _rows(found: JSON) -> list[JSON]:
    return cast(list[JSON], found.get("results") or [])


async def _account_currency(read: Reader, path: JSON, body: JSON) -> None:
    rows = _rows(await read("report", path, {"query": ACCOUNT_CURRENCY}))
    said = cast(JSON, rows[0].get("customer") or {}).get("currencyCode") if rows else None
    if not said or str(said).upper() != _currency(body):
        raise ToolError(
            f"this account spends in {said or 'a currency Google did not say'}, not "
            f"{_currency(body)}: nothing was changed. Ask for this again in its own currency"
        )


async def _in_currency(read: Reader, path: JSON, body: JSON, _account: str | None) -> None:
    await _account_currency(read, path, body)


async def _campaign_spend(read: Reader, path: JSON, body: JSON, _account: str | None) -> None:
    """Starting a campaign says what it will spend: that has to be its budget today."""
    await _account_currency(read, path, body)
    query = (
        "SELECT campaign_budget.amount_micros FROM campaign "  # noqa: S608 (an ID of digits)
        f"WHERE campaign.id = {_id(body, 'campaign_id')}"
    )
    rows = _rows(await read("report", path, {"query": query}))
    budget = cast(JSON, rows[0].get("campaignBudget") or {}) if rows else {}
    now = budget.get("amountMicros")
    if str(now) != _micros(body, "daily_amount"):
        shown = f"{Decimal(str(now)) / MICROS:f}" if now else "not known"
        raise ToolError(
            f"this campaign's daily budget is {shown}, not {body.get('daily_amount')}: nothing "
            "was started. Ask for this again with its real budget"
        )


def _operations(_op: Op, _query: JSON, sent: JSON | None) -> int:
    """Google's count: a report or a listing is one, each change is one, and a further page of
    a report is free."""
    body = sent or {}
    if body.get("pageToken"):
        return 0
    changes = body.get("operations") or body.get("mutateOperations")
    return len(cast(list[Any], changes)) if isinstance(changes, list) else 1


def _who(found: JSON) -> str:
    names = cast(list[str], found.get("resourceNames") or [])
    return f"{len(names)} ad account{'' if len(names) == 1 else 's'}"


BASE = "/customers/{account}"
CAMPAIGN = ("campaign_id",)
GROUP = ("ad_group_id",)
AD = ("ad_group_id", "ad_id")

GOOGLE_ADS = Vendor(
    "google_ads", "Google Ads", CATEGORY, "Reports, campaigns, budgets, bids",
    f"https://googleads.googleapis.com/{API_VERSION}",
    provider="google", secrets=(), never_trusted=True,
    path_headers={"login-customer-id": "manager"},
    scope=(
        ScopeField("customer", "Ad account", ("account",),
                   "The 10-digit customer ID, without dashes."),
    ),
    meter=_operations, probe="list_accounts", who=_who,
    words=("google ads", "adwords", "ads", "campaigns", "ppc", "advertising", "budgets"),
    key_url="https://console.cloud.google.com/apis/library/googleads.googleapis.com",
    steps="Turn on the Google Ads API in your Google app's Cloud project, and apply for Explorer "
    "access on its overview page. Then sign in from this tile.",
    note="Reports run. Pausing waits for approval. Creating, starting, and changing a budget or "
    "a bid need the name typed, in the app itself, and the card shows the spend. Everything "
    "starts paused. Approving a new campaign declares it isn't EU political advertising: the "
    "card says so. Every client "
    "shares your Cloud project's daily operations: Settings shows what's used.",
    ops=(
        reads("list_accounts", "/customers:listAccessibleCustomers",
              "The ad accounts this sign-in can reach directly"),
        reads("report", f"{BASE}/googleAds:search", "A report", "query", "pageToken", "manager",
              method="POST", only=("query", "pageToken"),
              hint="In Google Ads Query Language. Every call here takes `manager` in `path`, for "
              "an account under a manager account."),

        # Held: pausing only stops spending.
        held("pause_campaign", "POST", f"{BASE}/campaigns:mutate", "Pause a campaign", *CAMPAIGN,
             "manager", only=CAMPAIGN, shape=_status("campaigns", "campaign_id", "PAUSED")),
        held("pause_ad_group", "POST", f"{BASE}/adGroups:mutate", "Pause an ad group", *GROUP,
             "manager", only=GROUP, shape=_status("adGroups", "ad_group_id", "PAUSED")),
        held("pause_ad", "POST", f"{BASE}/adGroupAds:mutate", "Pause an ad", *AD, "manager",
             only=AD, shape=_ad_status("PAUSED")),

        # The name typed.
        final("create_campaign", "POST", f"{BASE}/googleAds:mutate",
              "Make a paused Search campaign with its daily budget", CREATES, "name",
              *SPEND_CHECKED, EU_POLITICAL, "manager",
              only=("name", *SPEND_CHECKED, EU_POLITICAL), money=DAILY,
              shape=_new_campaign, confirm=_in_currency, declares=DECLARES,
              hint=f"{EU_POLITICAL} must be '{NO_EU_POLITICAL}': the owner declares that to "
              "Google by approving. If the campaign has any, don't make it here."),
        final("create_ad_group", "POST", f"{BASE}/adGroups:mutate",
              "Make a paused ad group in a campaign", CREATES, "campaign_id", "name", "cpc_bid",
              "currency", "manager", only=("campaign_id", "name", "cpc_bid", "currency"),
              money=BID, shape=_new_ad_group, confirm=_in_currency),
        final("create_search_ad", "POST", f"{BASE}/adGroupAds:mutate",
              "Make a paused responsive search ad", CREATES, *GROUP, "headlines", "descriptions",
              "final_url", "manager",
              only=(*GROUP, "headlines", "descriptions", "final_url"), shape=_new_ad),
        final("add_keywords", "POST", f"{BASE}/adGroupCriteria:mutate",
              "Add keywords to an ad group", "changes what the account pays to show up for",
              *GROUP, "keywords", "match_type", "manager",
              only=(*GROUP, "keywords", "match_type"), shape=_keywords),
        final("start_campaign", "POST", f"{BASE}/campaigns:mutate",
              "Start a campaign", STARTS, *CAMPAIGN, *SPEND_CHECKED, "manager",
              only=(*CAMPAIGN, *SPEND_CHECKED), money=DAILY, shape=_spending_campaign,
              confirm=_campaign_spend,
              hint="Say its current daily budget: the card shows it, and it's checked first."),
        final("start_ad_group", "POST", f"{BASE}/adGroups:mutate", "Start an ad group", STARTS,
              *GROUP, "manager", only=GROUP,
              shape=_status("adGroups", "ad_group_id", "ENABLED")),
        final("start_ad", "POST", f"{BASE}/adGroupAds:mutate", "Start an ad", STARTS, *AD,
              "manager", only=AD, shape=_ad_status("ENABLED")),
        final("set_daily_budget", "POST", f"{BASE}/campaignBudgets:mutate",
              "Change a budget's daily amount", CHANGES_SPEND, "budget_id", *SPEND_CHECKED,
              "manager", only=("budget_id", *SPEND_CHECKED), money=DAILY, shape=_budget,
              confirm=_in_currency),
        final("set_ad_group_bid", "POST", f"{BASE}/adGroups:mutate",
              "Change an ad group's bid per click", CHANGES_SPEND, *GROUP, "cpc_bid", "currency",
              "manager", only=(*GROUP, "cpc_bid", "currency"), money=BID, shape=_bid,
              confirm=_in_currency),
    ),
)  # fmt: skip

VENDORS = (GOOGLE_ADS,)
