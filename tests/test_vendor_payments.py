"""Payments and CMS tables: what each promises, read from the tables themselves."""

import base64
import hashlib
import hmac
import json
import re
from collections.abc import Iterator
from typing import Any

import httpx2
import pytest

from ringkeeper import sensitive
from ringkeeper.adapters import vendor
from ringkeeper.adapters.base import Target
from ringkeeper.adapters.vendor_spec import FINAL, HELD, READ, Op, Vendor
from ringkeeper.adapters.vendors import cms, payments
from ringkeeper.adapters.vendors.cms import GHOST, SHOPIFY
from ringkeeper.adapters.vendors.payments import (
    BILL,
    BRAINTREE,
    CLOVER,
    HELCIM,
    LEMON_SQUEEZY,
    PLAID,
    SQUARE,
)
from ringkeeper.models import Env
from ringkeeper.vault import Secret

TABLES = (SQUARE, HELCIM, LEMON_SQUEEZY, CLOVER, BRAINTREE, PLAID, BILL, SHOPIFY, GHOST)
EVERY_OP = [(v, op) for v in TABLES for op in v.ops]
OP_IDS = [f"{v.key}.{op.name}" for v, op in EVERY_OP]
GHOST_KEY = f"{'a' * 24}:{'b' * 64}"
SECRET_ENDPOINTS = ("secret_key", "/secret", "/tokens", "/token", "access_token")
RAW_NUMBERS = ("cardNumber", "cardCVV", "bankData", "account_number", "routing_number")

# What moves money in each table: every one needs the name typed and says what the card shows.
MOVES_MONEY = {
    "square": ("create_payment", "complete_payment", "cancel_payment", "refund_payment",
             "publish_invoice", "cancel_invoice", "create_subscription", "update_subscription",
             "cancel_subscription", "resume_subscription", "accept_dispute",
             "submit_dispute_evidence"),
    "helcim": ("purchase", "preauth", "capture", "refund", "reverse", "ach_withdraw", "ach_refund",
             "ach_void", "ach_cancel", "create_subscriptions", "update_subscriptions",
             "delete_subscription", "charge_subscription_payment"),
    "lemonsqueezy": ("refund_order", "refund_subscription_invoice", "update_subscription",
                    "cancel_subscription", "update_subscription_item"),
    "clover": ("refund_charge", "return_order"),
    "braintree": ("charge_payment_method", "authorize_payment_method", "capture_transaction",
                "refund_transaction", "reverse_transaction", "void_transaction", "accept_dispute",
                "finalize_dispute"),
    "bill": ("pay_bill", "cancel_payment", "void_payment", "record_bill_payment",
             "charge_customer", "record_invoice_payment", "send_invoice"),
    "shopify": ("refund_order", "cancel_order", "capture_order", "complete_draft_order"),
}  # fmt: skip
EVERY_MONEY_OP = [f"{key}.{name}" for key, names in MOVES_MONEY.items() for name in names]
A_READ = {"square": "get_payment", "helcim": "get_customer", "lemonsqueezy": "list_orders",
          "clover": "get_charge", "braintree": "search_transactions", "plaid": "get_transactions",
          "shopify": "list_orders", "ghost": "list_posts"}  # fmt: skip
A_HOLD = {"square": "create_invoice", "helcim": "create_invoice", "lemonsqueezy": "create_checkout",
          "clover": "create_customer", "braintree": "create_customer",
          "shopify": "create_draft_order", "ghost": "create_tag"}  # fmt: skip


def op_of(table: Vendor, name: str) -> Op:
    return next(op for op in table.ops if op.name == name)


def named(dotted: str) -> Op:
    key, name = dotted.split(".")
    return op_of(next(t for t in TABLES if t.key == key), name)


def reason(op: Op, **args: Any) -> str | None:
    """What the name is typed for, as vendor.typed_reason answers once it's registered."""
    if op.level != FINAL:
        return None
    return op.why(args) if callable(op.why) else op.why


def fields(table: Vendor, **values: str) -> dict[str, str]:
    return {"vendor": table.key, **values}


def takes(table: Vendor, key: str, value: str) -> bool:
    """Whether this value can become part of the address (vendor.settings and vendor._base)."""
    return bool(re.fullmatch(next(s.pattern for s in table.secrets if s.key == key), value))


def decoded(part: str) -> dict[str, Any]:
    return json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))


@pytest.fixture
def sent() -> Iterator[list[httpx2.Request]]:
    requests: list[httpx2.Request] = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(200, json={"data": {"shop": {"name": "Acme"}}})

    before = vendor.transport
    vendor.transport = httpx2.MockTransport(handle)
    yield requests
    vendor.transport = before


# --- What every table promises ---


def test_the_modules_export_what_can_run_now() -> None:
    assert payments.VENDORS == (SQUARE, HELCIM, LEMON_SQUEEZY, CLOVER, BRAINTREE, PLAID, BILL)
    assert cms.VENDORS == (SHOPIFY, GHOST)


@pytest.mark.parametrize("table", TABLES, ids=lambda t: t.key)
def test_a_table_says_how_to_connect(table: Vendor) -> None:
    assert table.category == ("CMS" if table.key in ("shopify", "ghost") else "Payments")
    assert all((table.sub, table.words, table.steps, table.key_url, table.note))
    assert op_of(table, table.probe).level == READ


@pytest.mark.parametrize(("table", "op"), EVERY_OP, ids=OP_IDS)
def test_an_operation_keeps_to_the_rules(table: Vendor, op: Op) -> None:
    assert op.says
    if op.method == "DELETE":
        assert op.level == FINAL
    if op.level == READ and op.graphql:
        assert "mutation" not in op.graphql.lower()
    assert not any(s in op.path for s in SECRET_ENDPOINTS)
    assert not any(s in " ".join(op.only) for s in RAW_NUMBERS)


@pytest.mark.parametrize(("key", "name"), A_READ.items())
def test_a_read_runs(key: str, name: str) -> None:
    assert named(f"{key}.{name}").level == READ


@pytest.mark.parametrize(("key", "name"), A_HOLD.items())
def test_a_held_item_waits_without_the_name(key: str, name: str) -> None:
    assert named(f"{key}.{name}").level == HELD


@pytest.mark.parametrize("name", EVERY_MONEY_OP)
def test_moving_money_needs_the_name_and_says_what_moves(name: str) -> None:
    op = named(name)
    assert op.level == FINAL
    assert op.money is not None
    assert reason(op)


@pytest.mark.parametrize("key", ["square", "helcim", "lemonsqueezy"])
def test_a_customer_only_waits(key: str) -> None:
    for name in ("create_customer", "update_customer"):
        assert named(f"{key}.{name}").level == HELD


def test_nothing_that_issues_a_payment_token_or_takes_a_raw_number_is_in_a_table() -> None:
    assert not any("tokenize" in op.graphql or "ClientToken" in op.graphql for op in BRAINTREE.ops)
    assert not any("CreditCard" in op.graphql for op in BRAINTREE.ops)
    assert not {"verify", "withdraw"} & {op.name for op in HELCIM.ops}
    assert not [op for op in SQUARE.ops if "/cards" in op.path or "/bank-accounts" in op.path]
    assert not [op for op in HELCIM.ops if "/bank-accounts" in op.path]


def test_a_raw_card_number_sent_to_helcim_is_refused_all_the_same() -> None:
    body = {"amount": 10, "cardData": {"cardNumber": "5454545454545454", "cardExpiry": "1257"}}
    assert sensitive.find(body) == sensitive.CARD_WORD
    assert sensitive.find({"amount": 10, "cardData": {"cardToken": "tok-abc"}}) is None


@pytest.mark.parametrize(
    ("table", "live", "test"),
    [
        (SQUARE, "connect.squareup.com", "connect.squareupsandbox.com"),
        (CLOVER, "scl.clover.com", "scl-sandbox.dev.clover.com"),
        (BRAINTREE, "payments.braintree-api.com", "payments.sandbox.braintree-api.com"),
        (PLAID, "production.plaid.com", "sandbox.plaid.com"),
        (BILL, "gateway.prod.bill.com", "gateway.stage.bill.com"),
    ],
    ids=["square", "clover", "braintree", "plaid", "bill"],
)
def test_the_host_says_test_or_live_and_nothing_else_is_taken(
    table: Vendor, live: str, test: str
) -> None:
    assert table.env_of is not None
    assert table.env_of({"host": live}) is Env.PROD
    assert table.env_of({"host": test}) is Env.DEV
    assert table.env_of({}) is None
    assert takes(table, "host", live)
    assert takes(table, "host", test)
    assert not takes(table, "host", "evil.example")


# --- The approval card ---


@pytest.mark.parametrize(
    ("table", "name", "body", "amount", "party", "account"),
    [
        (SQUARE, "create_payment",
         {"amount_money": {"amount": 2500, "currency": "usd"}, "customer_id": "C1",
          "source_id": "cnon:1"}, "25.00 USD", "C1", "cnon:1"),
        (SQUARE, "refund_payment",
         {"amount_money": {"amount": 150, "currency": "CAD"}, "payment_id": "P1"},
         "1.50 CAD", "not stated", "P1"),
        (HELCIM, "purchase",
         {"amount": 12.5, "currency": "CAD", "customerCode": "CST1",
          "cardData": {"cardToken": "tok"}}, "12.50 CAD", "CST1", "tok"),
        (LEMON_SQUEEZY, "refund_order",
         {"order_id": "7", "data": {"attributes": {"amount": 500}}},
         "500 (smallest units) (currency not stated)", "not stated", "7"),
        (CLOVER, "refund_charge", {"charge": "CH1", "amount": 1000},
         "1000 (smallest units) (currency not stated)", "not stated", "CH1"),
        (BRAINTREE, "charge_payment_method",
         {"input": {"paymentMethodId": "PM1",
                    "transaction": {"amount": "9.99", "customerId": "BC1"}}},
         "9.99 (currency not stated)", "BC1", "PM1"),
        (SHOPIFY, "capture_order",
         {"input": {"id": "gid://shopify/Order/1", "amount": "4.00", "currency": "USD"}},
         "4.00 USD", "not stated", "gid://shopify/Order/1"),
    ],
    ids=["square-charge", "square-refund", "helcim", "lemon", "clover", "braintree", "shopify"],
)  # fmt: skip
def test_the_card_shows_what_the_call_says(
    table: Vendor, name: str, body: dict[str, Any], amount: str, party: str, account: str
) -> None:
    rule = op_of(table, name).money
    assert rule is not None
    card = vendor.card_of(rule, body)
    assert (card.amount, card.counterparty, card.account) == (amount, party, account)


def test_a_capture_with_no_amount_says_so_and_names_the_payment() -> None:
    rule = op_of(SQUARE, "complete_payment").money
    assert rule is not None
    card = vendor.card_of(rule, {"payment_id": "P1"})
    assert card.amount == "not stated (currency not stated)"
    assert card.account == "P1"


# --- Plaid ---


def test_plaid_cant_change_anything() -> None:
    assert {op.level for op in PLAID.ops} == {READ}
    assert not [op for op in PLAID.ops if op.graphql or op.undo or op.money]


def test_plaid_cant_move_money_or_reach_a_token_or_an_account_number() -> None:
    banned = (
        "/transfer", "/payment", "/bank_transfer", "/processor", "/wallet", "/link", "/auth",
        "/liabilities", "/item/public_token", "/item/remove", "/sandbox", "/user",
    )  # fmt: skip
    for op in PLAID.ops:
        assert not op.path.startswith(banned), op.path
        assert "access_token" not in op.only, op.name


def test_plaid_waits_for_the_core() -> None:
    assert PLAID in payments.VENDORS


# --- Publishing ---


@pytest.mark.parametrize(
    ("name", "body", "typed"),
    [
        ("create_product", {"product": {"title": "A", "status": "DRAFT"}}, False),
        ("create_product", {"product": {"title": "A", "status": "ACTIVE"}}, True),
        ("create_product", {"product": {"title": "A"}}, True),  # only DRAFT stays off sale
        ("create_product", {}, True),
        ("update_product", {"product": {"id": "1", "title": "B"}}, False),
        ("update_product", {"product": {"id": "1", "status": "ACTIVE"}}, True),
        ("update_product", {"product": {"id": "1", "status": "ARCHIVED"}}, True),
        ("create_page", {"page": {"title": "A", "isPublished": False}}, False),
        ("create_page", {"page": {"title": "A"}}, True),  # visible by default
        ("create_page", {"page": {"title": "A", "isPublished": False, "publishDate": "x"}}, True),
        ("update_page", {"id": "1", "page": {"title": "B"}}, False),
        ("update_page", {"id": "1", "page": {"isPublished": True}}, True),
        ("create_article", {"article": {"title": "A", "isPublished": False}}, False),
        ("create_article", {"article": {"title": "A", "isPublished": True}}, True),
        ("update_article", {"id": "1", "article": {"title": "B"}}, False),
        ("update_article", {"id": "1", "article": {"publishDate": "x"}}, True),
    ],
)
def test_what_shopify_publishes_is_read_from_the_call(
    name: str, body: dict[str, Any], typed: bool
) -> None:
    assert (reason(op_of(SHOPIFY, name), body=body) is not None) is typed


@pytest.mark.parametrize(
    "name",
    ["publish_product", "unpublish_product", "update_variants", "delete_product", "refund_order",
     "cancel_order", "fulfill_order", "publish_theme", "update_theme_files", "update_shop_policy",
     "send_draft_order_invoice"],
)  # fmt: skip
def test_shopifys_final_steps_need_the_name(name: str) -> None:
    assert reason(op_of(SHOPIFY, name))


def test_reading_shopify_orders_and_customers_runs() -> None:
    for name in ("list_orders", "get_order", "list_customers", "get_customer"):
        assert op_of(SHOPIFY, name).level == READ


@pytest.mark.parametrize(
    ("name", "kind", "item", "typed"),
    [
        ("create_post", "posts", {"status": "draft"}, False),
        ("create_post", "posts", {"status": "published"}, True),
        ("create_post", "posts", {"status": "scheduled"}, True),
        ("create_post", "posts", {}, True),  # only a call that says draft stays a draft
        ("create_post", "posts", {"status": "draft", "email_only": True}, True),
        ("create_page", "pages", {"status": "draft"}, False),
        ("create_page", "pages", {"status": "published"}, True),
        ("update_post", "posts", {"title": "B"}, False),
        ("update_post", "posts", {"status": "published"}, True),
        ("update_post", "posts", {"status": "draft"}, True),  # could take a live post down
        ("update_post", "posts", {"published_at": "2030-01-01T00:00:00.000Z"}, True),
        ("update_page", "pages", {"title": "B"}, False),
        ("update_page", "pages", {"status": "published"}, True),
    ],
)
def test_a_ghost_posts_status_decides(
    name: str, kind: str, item: dict[str, Any], typed: bool
) -> None:
    assert (reason(op_of(GHOST, name), body={kind: [item]}) is not None) is typed


@pytest.mark.parametrize("name", ["create_post", "update_post"])
def test_a_newsletter_always_needs_the_name(name: str) -> None:
    body = {"posts": [{"status": "draft", "title": "A"}]}
    assert reason(op_of(GHOST, name), body=body, query={"newsletter": "weekly"})
    assert reason(op_of(GHOST, name), body=body, query={"email_segment": "status:free"})


def test_a_ghost_call_that_doesnt_plainly_say_draft_counts_as_publishing() -> None:
    op = op_of(GHOST, "create_post")
    assert reason(op, body={"posts": [{"status": "draft"}, {"status": "published"}]})
    assert reason(op, body={"posts": []})
    assert reason(op, body={"posts": "published"})
    assert reason(op)


@pytest.mark.parametrize("name", ["delete_post", "delete_page", "activate_theme", "delete_member"])
def test_ghosts_final_steps_need_the_name(name: str) -> None:
    assert reason(op_of(GHOST, name))


def test_ghosts_settings_and_keys_are_out_of_reach() -> None:
    paths = " ".join(op.path for op in GHOST.ops)
    for word in ("/settings", "/users", "/integrations", "/webhooks", "/db", "signin_urls"):
        assert word not in paths


# --- On the wire ---


@pytest.mark.anyio
async def test_square_sends_its_version_to_the_host_in_the_connection(
    sent: list[httpx2.Request],
) -> None:
    connection = fields(SQUARE, host="connect.squareupsandbox.com", api_key="sq-token")
    await vendor.run(SQUARE, op_of(SQUARE, "get_payment"), connection, path={"payment_id": "P1"})
    request = sent[0]
    assert str(request.url) == "https://connect.squareupsandbox.com/v2/payments/P1"
    assert request.headers["Authorization"] == "Bearer sq-token"
    assert request.headers["Square-Version"] == payments.SQUARE_VERSION


@pytest.mark.anyio
async def test_square_undoes_a_customer_edit(monkeypatch: pytest.MonkeyPatch) -> None:
    customer: dict[str, Any] = {"id": "C1", "given_name": "Ann", "note": "first", "version": 1}

    def handle(request: httpx2.Request) -> httpx2.Response:
        if request.method == "PUT":
            customer.update(json.loads(request.content))
        return httpx2.Response(200, json={"customer": customer})

    monkeypatch.setattr(vendor, "transport", httpx2.MockTransport(handle))
    monkeypatch.setitem(vendor.VENDORS, "square", SQUARE)
    for op in SQUARE.ops:
        monkeypatch.setitem(vendor.OPERATIONS, f"square.{op.name}", (SQUARE, op))
    secret = json.dumps(fields(SQUARE, host="connect.squareup.com", api_key="sq-token"))
    target = Target("t", Env.DEV, None, Secret("r", secret))
    args = {"operation": "square.update_customer", "path": {"customer_id": "C1"}}
    back = vendor.OperationUndo()
    kept = await back.before(target, args)
    customer["note"] = "changed"
    await back.restore(target, kept)
    assert customer["note"] == "first"
    assert back.describe(kept) == "Puts back the customer's details."


@pytest.mark.anyio
async def test_helcim_gives_each_call_its_own_idempotency_key(
    sent: list[httpx2.Request],
) -> None:
    connection = fields(HELCIM, api_key="helcim-token")
    body = {"ipAddress": "203.0.113.1", "amount": 5, "currency": "CAD"}
    for _ in range(2):
        await vendor.run(HELCIM, op_of(HELCIM, "purchase"), connection, body=body)
    first, second = sent
    assert str(first.url) == "https://api.helcim.com/v2/payment/purchase"
    assert first.headers["api-token"] == "helcim-token"
    assert first.headers["idempotency-key"] != second.headers["idempotency-key"]
    assert 25 <= len(first.headers["idempotency-key"]) <= 36


@pytest.mark.anyio
async def test_lemon_squeezy_speaks_json_api(sent: list[httpx2.Request]) -> None:
    await vendor.run(
        LEMON_SQUEEZY, op_of(LEMON_SQUEEZY, "refund_order"),
        fields(LEMON_SQUEEZY, api_key="lemon-key"), path={"order_id": "7"},
        body={"data": {"type": "orders", "id": "7", "attributes": {"amount": 100}}},
    )  # fmt: skip
    request = sent[0]
    assert str(request.url) == "https://api.lemonsqueezy.com/v1/orders/7/refund"
    assert request.headers["Content-Type"] == payments.JSON_API
    assert request.headers["Accept"] == payments.JSON_API


def test_lemon_squeezy_says_test_or_live_from_its_answer() -> None:
    assert LEMON_SQUEEZY.who is not None
    found = {"meta": {"test_mode": True}, "data": {"id": "1", "attributes": {"name": "Ann"}}}
    assert LEMON_SQUEEZY.who(found) == "Ann, in test mode"


@pytest.mark.anyio
async def test_braintree_signs_in_with_its_two_keys(sent: list[httpx2.Request]) -> None:
    connection = fields(BRAINTREE, host="payments.sandbox.braintree-api.com", public_key="pub",
                        private_key="priv")  # fmt: skip
    await vendor.run(BRAINTREE, op_of(BRAINTREE, "ping"), connection)
    request = sent[0]
    assert str(request.url) == "https://payments.sandbox.braintree-api.com/graphql"
    assert request.headers["Authorization"] == f"Basic {base64.b64encode(b'pub:priv').decode()}"
    assert request.headers["Braintree-Version"] == payments.BRAINTREE_VERSION


@pytest.mark.anyio
async def test_shopify_calls_one_store_at_the_current_version(
    sent: list[httpx2.Request],
) -> None:
    connection = fields(SHOPIFY, shop="acme.myshopify.com", api_key="shop-token")
    found = await vendor.run(SHOPIFY, op_of(SHOPIFY, "get_shop"), connection)
    assert found == {"shop": {"name": "Acme"}}
    request = sent[0]
    assert str(request.url) == "https://acme.myshopify.com/admin/api/2026-10/graphql.json"
    assert request.headers["X-Shopify-Access-Token"] == "shop-token"


@pytest.mark.parametrize(
    "shop", ["acme.myshopify.com.evil.example", "evil.example", "acme.myshopify.com/x", ""]
)
def test_a_store_address_cant_point_anywhere_else(shop: str) -> None:
    assert not takes(SHOPIFY, "shop", shop)


@pytest.mark.anyio
async def test_ghost_signs_a_short_lived_token_with_its_key(sent: list[httpx2.Request]) -> None:
    connection = fields(GHOST, domain="acme.ghost.io", api_key=GHOST_KEY)
    await vendor.run(GHOST, op_of(GHOST, "list_posts"), connection)
    request = sent[0]
    assert str(request.url) == "https://acme.ghost.io/ghost/api/admin/posts/"
    assert request.headers["Accept-Version"] == cms.GHOST_VERSION
    scheme, token = request.headers["Authorization"].split(" ")
    assert scheme == "Ghost"
    header, claims, signature = token.split(".")
    assert decoded(header) == {"alg": "HS256", "typ": "JWT", "kid": "a" * 24}
    body = decoded(claims)
    assert body["aud"] == "/admin/"
    assert body["exp"] - body["iat"] == 300
    expected = hmac.new(bytes.fromhex("b" * 64), f"{header}.{claims}".encode(), hashlib.sha256)
    assert base64.urlsafe_b64decode(signature + "=" * (-len(signature) % 4)) == expected.digest()


@pytest.mark.parametrize("domain", ["localhost", "169.254.169.254", "acme.ghost.io/x", "a b.io"])
def test_a_ghost_domain_is_a_public_name(domain: str) -> None:
    assert not takes(GHOST, "domain", domain)


# --- BILL ---


def bill_api(requests: list[httpx2.Request]) -> httpx2.MockTransport:
    def handle(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        if request.url.path.endswith("/v3/login"):
            return httpx2.Response(200, json={"sessionId": "bill-session", "userId": "006x"})
        return httpx2.Response(200, json={"organizationId": "008abc"})

    return httpx2.MockTransport(handle)


@pytest.mark.anyio
async def test_bill_signs_in_once_and_sends_the_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[httpx2.Request] = []
    monkeypatch.setattr(vendor, "transport", bill_api(requests))
    connection = fields(
        BILL, host="gateway.stage.bill.com", username="ap@acme.example", password="pw-test",
        organization_id="008once", dev_key="dev-test", remember_me_id="mfa-id", device="desk",
    )  # fmt: skip
    for _ in range(2):
        await vendor.run(BILL, op_of(BILL, "get_session"), connection)
    login, first, second = requests
    assert str(login.url) == "https://gateway.stage.bill.com/connect/v3/login"
    assert json.loads(login.content) == {
        "username": "ap@acme.example", "password": "pw-test", "organizationId": "008once",
        "devKey": "dev-test", "rememberMeId": "mfa-id", "device": "desk",
    }  # fmt: skip
    assert str(first.url) == "https://gateway.stage.bill.com/connect/v3/login/session"
    assert first.headers["sessionId"] == second.headers["sessionId"] == "bill-session"
    assert first.headers["devKey"] == "dev-test"


def test_a_bill_session_is_given_up_before_bill_ends_it() -> None:
    assert BILL.exchange is not None
    assert BILL.exchange.lifetime < 35 * 60


def test_nothing_at_bill_runs_on_trust() -> None:
    assert BILL.never_trusted


@pytest.mark.parametrize(
    "name",
    ["create_bill", "update_bill", "create_invoice", "update_invoice", "get_invoice_payment_link",
     "create_customer", "update_customer", "create_vendor", "update_vendor"],
)  # fmt: skip
def test_bills_invoices_links_and_customers_only_wait(name: str) -> None:
    assert op_of(BILL, name).level == HELD


def test_paying_a_bill_shows_what_goes_to_whom() -> None:
    rule = op_of(BILL, "pay_bill").money
    assert rule is not None
    account = {"type": "BANK_ACCOUNT", "id": "bac1"}
    card = vendor.card_of(rule, {"amount": 120.5, "vendorId": "009x", "fundingAccount": account})
    assert card.amount == "120.50 (currency not stated)"
    assert (card.counterparty, card.account) == ("009x", "bac1")


def test_sending_an_invoice_says_to_whom() -> None:
    rule = op_of(BILL, "send_invoice").money
    assert rule is not None
    body = {"invoice_id": "00e1", "recipient": {"to": ["ap@acme.example"]}}
    assert vendor.card_of(rule, body).counterparty == "ap@acme.example"


def test_bills_bank_accounts_and_users_are_out_of_reach() -> None:
    paths = " ".join(op.path for op in BILL.ops)
    for word in ("bank-accounts", "/users", "/bulk", "/mass", "/mfa", "/organizations"):
        assert word not in paths
    for name in ("create_vendor", "update_vendor", "create_customer", "update_customer"):
        taken = op_of(BILL, name).only
        assert not {"paymentInformation", "accountNumber", "autoPay"} & set(taken), name
