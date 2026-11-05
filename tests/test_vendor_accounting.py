"""Accounting and the Microsoft account: what each table promises, read from its Op objects.

"Reports run; drafts held; posting, sending invoices, payments, journal entries, voids,
bank/payroll: name typed. No accounting write ever auto-runs, even on Test."
"""

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx2
import pytest

from ringkeeper.adapters import vendor
from ringkeeper.adapters.base import ToolError
from ringkeeper.adapters.mail import BULK
from ringkeeper.adapters.vendor_spec import FINAL, HELD, READ, Op, Vendor
from ringkeeper.adapters.vendors import accounting, microsoft
from ringkeeper.adapters.vendors.accounting import BUSINESS_CENTRAL, QUICKBOOKS, WAVE, XERO
from ringkeeper.adapters.vendors.microsoft import MICROSOFT
from ringkeeper.models import Env

DOCS = Path(__file__).parent.parent / "docs" / "services"
ACCOUNTING = accounting.VENDORS
EVERYONE = (*ACCOUNTING, *microsoft.VENDORS)
EVERY_OP = [(v.key, op.name) for v in EVERYONE for op in v.ops]
VENDORS = {v.key: v for v in EVERYONE}
SECRET_ENDPOINTS = ("secret_key", "/secret", "oauth_access_tokens", "sign_in_tokens", "/tokens")
WORDS_THAT_NEED_THE_NAME = ("delete", "void", "post_", "send_", "email_", "payment", "journal")
TENANT = "11111111-2222-3333-4444-555555555555"


def op(found: Vendor, name: str) -> Op:
    return next(o for o in found.ops if o.name == name)


def typed(found: Op, args: dict[str, Any] | None = None) -> str | None:
    """Why this call needs the name typed, as vendor.typed_reason says once it's registered."""
    if found.level != FINAL:
        return None
    return found.why(args or {}) if callable(found.why) else found.why


def people(count: int) -> list[dict[str, Any]]:
    return [{"emailAddress": {"address": f"p{n}@acme.example"}} for n in range(count)]


# --- What every table promises ---


@pytest.mark.parametrize("found", ACCOUNTING, ids=lambda v: v.key)
def test_no_accounting_write_ever_runs_by_itself(found: Vendor) -> None:
    assert found.never_trusted
    assert found.category == "Accounting"


def test_the_modules_end_with_their_vendors() -> None:
    assert [v.key for v in ACCOUNTING] == ["wave", "quickbooks", "xero", "businesscentral"]
    assert [v.key for v in microsoft.VENDORS] == ["microsoft"]


@pytest.mark.parametrize(("key", "name"), EVERY_OP)
def test_a_read_changes_nothing_and_a_change_says_what_it_needs(key: str, name: str) -> None:
    found = op(VENDORS[key], name)
    assert found.says
    if found.level == READ and found.graphql:
        assert "mutation" not in found.graphql.lower(), name
    if found.level == READ and not found.graphql:
        assert found.method == "GET" or found.path == "/search/query"
    if found.method == "DELETE":
        assert found.level == FINAL
    assert not any(s in found.path for s in SECRET_ENDPOINTS), name


@pytest.mark.parametrize(
    ("key", "name"), [(v.key, op.name) for v in ACCOUNTING for op in v.ops if op.level != READ]
)
def test_what_touches_the_books_needs_the_name(key: str, name: str) -> None:
    if any(word in name for word in WORDS_THAT_NEED_THE_NAME):
        assert typed(op(VENDORS[key], name)), name


@pytest.mark.parametrize("found", EVERYONE, ids=lambda v: v.key)
def test_every_vendor_can_be_tested_found_and_read_about(found: Vendor) -> None:
    probe = op(found, found.probe)
    assert probe.level == READ
    assert "{" not in probe.path  # the Test button has no values to fill in
    assert found.who is not None
    for text in (found.words, found.steps, found.key_url, found.note):
        assert text
    assert (DOCS / f"{found.key}.md").exists()


@pytest.mark.parametrize("found", EVERYONE, ids=lambda v: v.key)
def test_an_undo_reads_the_same_record_and_puts_back_all_an_edit_can_change(found: Vendor) -> None:
    for edit in (o for o in found.ops if o.undo):
        assert edit.undo is not None
        assert op(found, edit.undo.read).level == READ
        assert edit.only, edit.name
        assert set(edit.only) <= set(edit.undo.keep), edit.name


@pytest.mark.parametrize("found", EVERYONE, ids=lambda v: v.key)
def test_nothing_reaches_keys_users_or_bank_connections(found: Vendor) -> None:
    for each in found.ops:
        assert not any(w in each.path.lower() for w in ("/users", "/batch", "$batch", "/rules"))
        assert "oAuthApplication" not in each.graphql


# --- Wave ---


@pytest.mark.parametrize(
    ("name", "typed_too"),
    [
        ("list_accounts", False),  # Wave has no reports: balances are the closest
        ("list_invoices", False),
        ("create_customer", False),
        ("create_estimate", False),
        ("approve_invoice", True),
        ("send_invoice", True),
        ("record_invoice_payment", True),
        ("record_transaction", True),
        ("delete_invoice", True),
        ("delete_customer", True),
    ],
)
def test_waves_rules(name: str, typed_too: bool) -> None:
    assert (typed(op(WAVE, name)) is not None) is typed_too


@pytest.mark.parametrize(
    ("status", "typed_too"), [("DRAFT", False), ("SAVED", True), (None, True), ("", True)]
)
def test_a_wave_invoice_is_held_only_if_it_says_its_a_draft(
    status: str | None, typed_too: bool
) -> None:
    args = {"body": {"status": status}}
    assert (typed(op(WAVE, "create_invoice"), args) is not None) is typed_too


def test_a_wave_edit_cant_tell_if_the_invoice_is_a_draft() -> None:
    assert typed(op(WAVE, "update_invoice"), {"body": {"status": "DRAFT"}})


def test_wave_has_no_batch_and_every_change_answers_with_its_errors() -> None:
    assert "moneyTransactionsCreate" not in " ".join(o.graphql for o in WAVE.ops)
    for each in (o for o in WAVE.ops if o.level != READ):
        assert "didSucceed" in each.graphql
        assert "inputErrors" in each.graphql


def test_a_wave_payment_shows_on_the_card() -> None:
    body = {"amount": "120.50", "invoiceId": "inv_1", "paymentAccountId": "acc_1"}
    card = vendor.card_of(accounting.WAVE_PAYMENT_CARD, body)
    assert (card.amount, card.counterparty, card.account) == (
        "120.50 (currency not stated)", "inv_1", "acc_1",
    )  # fmt: skip


# --- QuickBooks ---


@pytest.mark.parametrize(
    ("name", "typed_too"),
    [
        ("report_profit_and_loss", False),
        ("search", False),
        ("create_estimate", False),  # it doesn't post
        ("create_purchase_order", False),
        ("create_customer", False),
        ("create_invoice", True),  # there's no draft: it's posted at once
        ("create_bill", True),
        ("create_payment", True),
        ("create_bill_payment", True),
        ("create_journal_entry", True),
        ("send_invoice", True),
        ("void_invoice", True),
        ("void_payment", True),
        ("delete_invoice", True),
    ],
)
def test_quickbooks_rules(name: str, typed_too: bool) -> None:
    assert (typed(op(QUICKBOOKS, name)) is not None) is typed_too


@pytest.mark.parametrize("query", [{"operation": "delete"}, {"OPERATION": "void"}, {"x": "1"}])
def test_a_held_quickbooks_call_cant_turn_into_a_delete(query: dict[str, str]) -> None:
    assert typed(op(QUICKBOOKS, "create_estimate"), {"query": query})


def test_harmless_quickbooks_query_values_stay_held() -> None:
    assert typed(op(QUICKBOOKS, "update_estimate"), {"query": {"minorversion": 75}}) is None


def test_quickbooks_has_no_batch_and_cant_touch_a_vendors_bank_details() -> None:
    assert not [o for o in QUICKBOOKS.ops if "batch" in o.path]
    assert op(QUICKBOOKS, "search").method == "GET"
    assert "VendorPaymentBankDetail" not in op(QUICKBOOKS, "update_vendor").only
    assert "Active" not in op(QUICKBOOKS, "update_customer").only


@pytest.mark.parametrize(
    ("host", "env"),
    [
        ("sandbox-quickbooks.api.intuit.com", Env.DEV),
        ("quickbooks.api.intuit.com", Env.PROD),
        ("", None),
    ],
)
def test_the_quickbooks_address_says_test_or_production(host: str, env: Env | None) -> None:
    assert QUICKBOOKS.env_of is not None
    assert QUICKBOOKS.env_of({"api_host": host}) is env


@pytest.mark.parametrize("host", ["evil.example", "quickbooks.api.intuit.com.evil.example", ""])
def test_a_quickbooks_token_only_goes_to_quickbooks(host: str) -> None:
    with pytest.raises(ToolError, match="isn't an address"):
        vendor._base(QUICKBOOKS, {"api_host": host, "realm_id": "123"}, None)  # pyright: ignore[reportPrivateUsage]


def test_a_quickbooks_bill_shows_on_the_card() -> None:
    body = {"TotalAmt": 300, "CurrencyRef": {"value": "USD"}, "VendorRef": {"name": "Acme"},
            "APAccountRef": {"value": "33"}}  # fmt: skip
    card = vendor.card_of(accounting.QBO_BILL, body)
    assert (card.amount, card.counterparty, card.account) == ("300 USD", "Acme", "33")


# --- Xero ---


@pytest.mark.parametrize(
    ("name", "typed_too"),
    [
        ("report_profit_and_loss", False),
        ("report_balance_sheet", False),
        ("list_invoices", False),
        ("create_invoice", False),  # no Status: a draft
        ("create_credit_note", False),
        ("create_contact", False),
        ("update_invoice", True),  # it may be approved already
        ("approve_invoice", True),
        ("void_invoice", True),
        ("delete_invoice", True),
        ("email_invoice", True),
        ("create_payment", True),
        ("delete_payment", True),
        ("create_bank_transaction", True),
        ("create_bank_transfer", True),
        ("create_manual_journal", True),
    ],
)
def test_xeros_rules(name: str, typed_too: bool) -> None:
    assert (typed(op(XERO, name)) is not None) is typed_too


@pytest.mark.parametrize(
    ("status", "typed_too"),
    [
        (None, False),
        ("DRAFT", False),
        ("SUBMITTED", True),
        ("AUTHORISED", True),
        ("PAID", True),
        ("draft", True),  # not a value Xero documents: don't guess
    ],
)
@pytest.mark.parametrize(
    "name", ["create_invoice", "create_credit_note", "create_quote", "create_purchase_order"]
)
def test_a_xero_record_is_held_only_as_a_draft(
    name: str, status: str | None, typed_too: bool
) -> None:
    body = {"Status": status} if status else {}
    assert (typed(op(XERO, name), {"body": body}) is not None) is typed_too


@pytest.mark.parametrize("name", ["create_invoice", "create_credit_note", "create_quote"])
def test_a_xero_batch_cant_slip_past_the_status_check(name: str) -> None:
    only = op(XERO, name).only
    assert only
    assert "status" not in only
    assert not {"Invoices", "CreditNotes", "Quotes"} & set(only)


def test_a_status_cant_ride_along_on_a_xero_edit() -> None:
    assert "Status" not in op(XERO, "update_invoice").only


@pytest.mark.parametrize("name", ["create_invoice", "create_credit_note", "create_payment"])
def test_xero_creates_with_put_which_cant_update(name: str) -> None:
    assert op(XERO, name).method == "PUT"


def test_a_xero_contacts_bank_details_need_the_name() -> None:
    update = op(XERO, "update_contact")
    assert typed(update, {"body": {"Name": "Acme"}}) is None
    assert typed(update, {"body": {"BankAccountDetails": "000000"}})
    assert "ContactStatus" not in update.only


def test_xero_names_the_organisation_in_a_header() -> None:
    assert XERO.account_header == "Xero-tenant-id"
    assert [s.args for s in XERO.scope] == [("account",)]


def test_a_xero_payment_shows_on_the_card() -> None:
    body = {"Amount": 32.06, "Invoice": {"InvoiceID": "inv-1"}, "Account": {"Code": "001"}}
    card = vendor.card_of(accounting.XERO_PAYMENT_CARD, body)
    assert (card.amount, card.counterparty, card.account) == (
        "32.06 (currency not stated)", "inv-1", "001",
    )  # fmt: skip


# --- Business Central ---


@pytest.mark.parametrize(
    ("name", "level", "typed_too"),
    [
        ("report_trial_balance", READ, False),
        ("report_income_statement", READ, False),
        ("list_sales_invoices", READ, False),
        ("create_sales_invoice", HELD, False),
        ("update_sales_invoice", HELD, False),
        ("create_purchase_invoice", HELD, False),
        ("post_sales_invoice", FINAL, True),
        ("post_and_send_sales_invoice", FINAL, True),
        ("send_sales_invoice", FINAL, True),
        ("cancel_sales_invoice", FINAL, True),
        ("delete_sales_invoice", FINAL, True),
        ("post_purchase_invoice", FINAL, True),
        ("create_customer_payment", FINAL, True),
        ("add_journal_line", FINAL, True),
        ("post_journal", FINAL, True),
    ],
)
def test_business_centrals_rules(name: str, level: str, typed_too: bool) -> None:
    found = op(BUSINESS_CENTRAL, name)
    assert found.level == level
    assert (typed(found) is not None) is typed_too


@pytest.mark.parametrize(
    "fields",
    [
        {"tenant_id": "evil.example/x", "environment": "Production"},
        {"tenant_id": TENANT, "environment": "../../other"},
        {"tenant_id": TENANT, "environment": ""},
    ],
)
def test_a_business_central_address_cant_be_bent(fields: dict[str, str]) -> None:
    with pytest.raises(ToolError, match="isn't an address"):
        vendor._base(BUSINESS_CENTRAL, fields, None)  # pyright: ignore[reportPrivateUsage]


def test_a_business_central_payment_shows_on_the_card() -> None:
    body = {"amount": 1500, "customerNumber": "10400", "journal_id": "j-1"}
    card = vendor.card_of(accounting.BC_PAYMENT, body)
    assert (card.amount, card.counterparty, card.account) == (
        "1500 (currency not stated)", "10400", "j-1",
    )  # fmt: skip


# --- Sent as each vendor expects ---


class Seen:
    def __init__(self) -> None:
        self.requests: list[httpx2.Request] = []

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        return httpx2.Response(200, json={"data": {"businesses": {"edges": []}}})


@pytest.fixture
def seen() -> Iterator[Seen]:
    fake = Seen()
    before = vendor.transport
    vendor.transport = httpx2.MockTransport(fake.handle)
    yield fake
    vendor.transport = before


@pytest.mark.anyio
async def test_wave_sends_its_graphql_with_the_token(seen: Seen) -> None:
    fields = {"vendor": "wave", "api_key": "wave-token"}
    found = op(WAVE, "create_invoice")
    await vendor.run(WAVE, found, fields, body={"businessId": "b", "customerId": "c"})
    sent = seen.requests[0]
    assert str(sent.url) == "https://gql.waveapps.com/graphql/public"
    assert sent.headers["Authorization"] == "Bearer wave-token"
    payload = json.loads(sent.content)
    assert payload["query"] == found.graphql
    assert payload["variables"] == {"businessId": "b", "customerId": "c"}


@pytest.mark.anyio
async def test_quickbooks_calls_the_company_and_asks_for_json(seen: Seen) -> None:
    fields = {"access_token": "t", "api_host": "sandbox-quickbooks.api.intuit.com", "realm_id": "9"}
    await vendor.run(
        QUICKBOOKS, op(QUICKBOOKS, "void_invoice"), fields, query={"operation": "void"},
        body={"Id": "1", "SyncToken": "0"},
    )  # fmt: skip
    sent = seen.requests[0]
    assert str(sent.url) == (
        "https://sandbox-quickbooks.api.intuit.com/v3/company/9/invoice?operation=void"
    )
    assert sent.headers["Accept"] == "application/json"
    assert sent.headers["Authorization"] == "Bearer t"


@pytest.mark.anyio
async def test_xero_sends_the_organisation(seen: Seen) -> None:
    fields = {"access_token": "t", "default_account": "tenant-1"}
    await vendor.run(XERO, op(XERO, "get_organisation"), fields)
    sent = seen.requests[0]
    assert sent.headers["Xero-tenant-id"] == "tenant-1"
    assert sent.headers["Accept"] == "application/json"


@pytest.mark.anyio
async def test_business_central_reaches_a_company_in_an_environment(seen: Seen) -> None:
    fields = {"access_token": "t", "tenant_id": TENANT, "environment": "Sandbox"}
    await vendor.run(
        BUSINESS_CENTRAL, op(BUSINESS_CENTRAL, "post_sales_invoice"), fields,
        path={"invoice_id": "inv-1"}, account="co-1",
    )  # fmt: skip
    sent = seen.requests[0]
    assert sent.url.path == (
        f"/v2.0/{TENANT}/Sandbox/api/v2.0/companies(co-1)/salesInvoices(inv-1)/Microsoft.NAV.post"
    )
    assert sent.headers["If-Match"] == "*"


# --- Microsoft ---


@pytest.mark.parametrize(
    ("name", "level", "typed_too"),
    [
        ("list_messages", READ, False),
        ("list_events_between", READ, False),
        ("search_files", READ, False),
        ("send_mail", FINAL, False),  # one send is held
        ("reply", FINAL, False),
        ("forward", FINAL, False),
        ("create_event", FINAL, False),
        ("create_draft", HELD, False),
        ("create_folder", HELD, False),
        ("send_draft", FINAL, True),  # its recipients can't be counted from the call
        ("reply_all", FINAL, True),
        ("delete_message", FINAL, True),
        ("delete_event", FINAL, True),
        ("cancel_event", FINAL, True),
        ("delete_file", FINAL, True),
        ("share_file", FINAL, True),
        ("create_share_link", FINAL, True),
        ("remove_file_access", FINAL, True),
    ],
)
def test_microsofts_rules(name: str, level: str, typed_too: bool) -> None:
    found = op(MICROSOFT, name)
    assert found.level == level
    assert (typed(found) is not None) is typed_too


@pytest.mark.parametrize(
    ("body", "typed_too"),
    [
        ({"message": {"toRecipients": people(BULK)}}, False),
        ({"message": {"toRecipients": people(BULK + 1)}}, True),
        ({"message": {"toRecipients": people(4), "ccRecipients": people(4),
                      "bccRecipients": people(3)}}, True),
        ({"message": {"bccRecipients": people(BULK + 1)}}, True),
        ({"toRecipients": people(6), "message": {"toRecipients": people(5)}}, True),
    ],
)  # fmt: skip
@pytest.mark.parametrize("name", ["send_mail", "reply", "forward"])
def test_a_mailing_needs_the_name(name: str, body: dict[str, Any], typed_too: bool) -> None:
    assert (typed(op(MICROSOFT, name), {"body": body}) is not None) is typed_too


@pytest.mark.parametrize(("count", "typed_too"), [(0, False), (BULK, False), (BULK + 1, True)])
@pytest.mark.parametrize("name", ["create_event", "update_event"])
def test_a_big_invitation_needs_the_name(name: str, count: int, typed_too: bool) -> None:
    args = {"body": {"attendees": people(count)}}
    assert (typed(op(MICROSOFT, name), args) is not None) is typed_too


@pytest.mark.parametrize(
    ("folder", "typed_too"), [("archive", False), ("deleteditems", True), ("DeletedItems", True)]
)
def test_moving_mail_to_the_bin_is_deleting_it(folder: str, typed_too: bool) -> None:
    args = {"body": {"destinationId": folder}}
    assert (typed(op(MICROSOFT, "move_message"), args) is not None) is typed_too


def test_microsoft_signs_in_and_asks_for_each_kind_of_access() -> None:
    assert MICROSOFT.provider == "microsoft"
    assert MICROSOFT.category == "Email"
    assert not MICROSOFT.never_trusted  # like Google: the owner can let some run
    assert set(microsoft.SCOPES) == {
        "mail_read", "mail_send", "mail_drafts", "calendar_read", "calendar_write", "files_read",
        "files_write",
    }  # fmt: skip


def test_mail_rules_and_other_peoples_mailboxes_arent_there() -> None:
    for each in MICROSOFT.ops:
        assert "messageRules" not in each.path
        assert "mailboxSettings" not in each.path
        assert not each.path.startswith(("/users/", "/groups/"))


def test_a_draft_cant_be_sent_from_someone_else() -> None:
    assert not {"from", "sender"} & set(op(MICROSOFT, "create_draft").only)
    assert not {"from", "sender"} & set(op(MICROSOFT, "update_draft").only)
