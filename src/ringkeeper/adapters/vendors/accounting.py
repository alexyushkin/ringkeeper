"""Accounting: Wave, QuickBooks Online, Xero and Dynamics 365 Business Central.

Holds, by the user's rule: reports run; drafts are held; posting, sending invoices, payments,
journal entries, voids, and anything to do with a bank need the name typed. No accounting write
ever runs by itself, even on test data, so each vendor is never_trusted.

What decides the level is whether the record is on the books once the call is done:
- Wave: a draft invoice isn't, until it's approved, and that can't be taken back. An invoice
  created without saying DRAFT may be saved straight to the books, so it needs the name typed.
- QuickBooks has no drafts: a created invoice, bill or payment is posted. Estimates and purchase
  orders don't post, so they're held.
- Xero: the Status in the body decides, on a create. On an edit the call can't show whether the
  invoice is still a draft, so an edit always needs the name typed.
- Business Central: a created invoice is a draft, and posting makes a separate posted record, so
  draft edits are held. Posting is its own action.

Left out on purpose:
- Wave: moneyTransactionsCreate (a batch), the chart of accounts, sales taxes, and estimate
  deposits. Wave has no API for reports or bills, so account balances are the closest thing to a
  report, and vendors can only be read.
- QuickBooks: /batch (it can carry writes), attachments, and PDFs (a call answers JSON).
- Xero: POST to a whole collection (it updates a matching record as well as creating one), the
  wrapped {"Invoices": [...]} form (a batch), BatchPayments, online invoice links, Users, and a
  contact's ContactStatus (archiving).
- Business Central: employees, bank accounts, and the PDF documents.
- Every vendor: API keys, users, and bank feeds and connections.

Sage Intacct and NetSuite aren't here: neither has a credential that can be pasted. Sage's REST
API takes OAuth tokens that last about 12 hours, from a registered app. NetSuite's OAuth 2.0
tokens last an hour, and its token-based sign-in is being retired from 2027.1. Each needs a
sign-in through a Developer app.
"""

from typing import cast

from ringkeeper.adapters.vendor_spec import (
    JSON,
    Money,
    Op,
    Secret,
    Snapshot,
    Vendor,
    final,
    held,
    mutation,
    query,
    reads,
)
from ringkeeper.models import Env
from ringkeeper.remote import ScopeField

CATEGORY = "Accounting"
BOOKS = "posts to the books"
SENDS = "sends an invoice to a customer"
PAYMENT = "records a payment"
JOURNAL = "writes a journal entry"
VOIDS = "voids a record on the books"
DELETES = "deletes a record"
BANK = "records money moving through a bank account"
EDITS_BOOKS = "edits a record that may already be on the books"
JSON_ONLY = {"Accept": "application/json"}


def _body(args: JSON) -> JSON:
    return cast(JSON, args.get("body") or {})


# --- Wave ---
#
# Confirmed against the live schema (gql.waveapps.com/graphql/public answers introspection).

PAGING = "$page: Int = 1, $pageSize: Int = 20"
PAGE = "page: $page, pageSize: $pageSize"
PAGE_INFO = "pageInfo { currentPage totalPages totalCount }"
WAVE_INVOICE = (
    "id invoiceNumber status title poNumber invoiceDate dueDate memo createdAt modifiedAt "
    "lastSentAt lastViewedAt customer { id name email } currency { code } total { value } "
    "taxTotal { value } amountDue { value } amountPaid { value }"
)
WAVE_ESTIMATE = (
    "id estimateNumber status title poNumber estimateDate dueDate memo createdAt modifiedAt "
    "customer { id name email } currency { code } total { value } amountDue { value }"
)
WAVE_ITEMS = (
    "items { description quantity unitPrice total { value } product { id name } "
    "account { id name } }"
)
WAVE_CUSTOMER = (
    "id name firstName lastName email phone mobile website displayId isArchived "
    "currency { code } outstandingAmount { value } overdueAmount { value }"
)
WAVE_PAYMENT = "id paymentDate amount paymentMethod memo account { id name }"
WAVE_PAYMENT_CARD = Money(("amount",), (), ("invoiceId",), ("paymentAccountId",), who="Invoice")
WAVE_MONEY_MOVES = Money(("amount",), (), ("description",), ("accountId",), who="For")


def _wave_list(field: str, node: str, filters: str = "", given: str = "") -> str:
    """A page of one kind of record in a business."""
    wanted = f"$businessId: ID!, {PAGING}{', ' + filters if filters else ''}"
    passed = f"{PAGE}{', ' + given if given else ''}"
    return (
        f"query({wanted}) {{ business(id: $businessId) {{ {field}({passed}) {{ {PAGE_INFO} "
        f"edges {{ node {{ {node} }} }} }} }} }}"
    )


def _wave_one(field: str, node: str, extra: str = "") -> str:
    """One record in a business."""
    return (
        f"query($businessId: ID!, $id: ID!) {{ business(id: $businessId) {{ "
        f"{field}(id: $id) {{ {node} {extra} }} }} }}"
    )


def _wave_change(field: str, answers: str = "", **fields: str) -> str:
    """A mutation with its input spelled out, so a call's body is plain fields: an approval
    card can read them, and only these can be set."""
    wanted = ", ".join(f"${name}: {kind}" for name, kind in fields.items())
    given = ", ".join(f"{name}: ${name}" for name in fields)
    return (
        f"mutation({wanted}) {{ {field}(input: {{ {given} }}) {{ didSucceed "
        f"inputErrors {{ path message code }} {answers} }} }}"
    )


def _wave_draft_why(args: JSON) -> str | None:
    """Wave's schema doesn't say what an invoice with no status becomes, so only DRAFT is held."""
    status = _body(args).get("status")
    if status == "DRAFT":
        return None
    return f"saves an invoice as {status}, which {BOOKS}" if status else (
        "doesn't say the invoice is a draft (status DRAFT), so it may go on the books"
    )  # fmt: skip


def _wave_who(found: JSON) -> str:
    edges = cast(list[JSON], cast(JSON, found.get("businesses") or {}).get("edges") or [])
    names = [str(cast(JSON, e.get("node") or {}).get("name")) for e in edges]
    return ", ".join(names) if names else "no businesses yet"


WAVE_INVOICE_FIELDS = {
    "customerId": "ID", "status": "InvoiceCreateStatus", "currency": "CurrencyCode",
    "title": "String", "subhead": "String", "invoiceNumber": "String", "poNumber": "String",
    "invoiceDate": "Date", "dueDate": "Date", "exchangeRate": "Decimal",
    "items": "[InvoiceCreateItemInput!]", "discounts": "[InvoiceDiscountInput!]",
    "memo": "String", "footer": "String",
}  # fmt: skip
WAVE_CONTACT_FIELDS = {
    "name": "String", "firstName": "String", "lastName": "String", "email": "String",
    "phone": "String", "mobile": "String", "website": "String", "displayId": "String",
    "internalNotes": "String", "currency": "CurrencyCode", "address": "AddressInput",
}  # fmt: skip

WAVE = Vendor(
    "wave", "Wave", CATEGORY, "Invoices, customers, estimates", "https://gql.waveapps.com",
    graphql_path="/graphql/public",
    secrets=(
        Secret("api_key", "Full access token", "From Manage Applications in Wave's developer "
               "portal. It reaches every business in the account."),
    ),
    probe="list_businesses", who=_wave_who, never_trusted=True,
    words=("wave", "waveapps", "accounting", "invoices", "bookkeeping", "estimates"),
    key_url="https://developer.waveapps.com/hc/en-us/articles/50682277703188-3-Authentication",
    steps="Sign in to Wave's developer portal, open Manage Applications, create an application, "
    "and make a full access token for it. The token reaches every business in your Wave account.",
    note="One token reaches every business in the account, so each call names a business. "
    "Reading runs. Draft invoices, estimates and customers wait for approval. Approving, sending, "
    "payments and deleting need the name typed. Nothing runs by itself. Wave's API has no reports "
    "and no bills: account balances are the closest it offers.",
    ops=(
        query("list_businesses", "The businesses this token reaches",
              f"query({PAGING}) {{ businesses({PAGE}) {{ {PAGE_INFO} edges {{ node {{ id name "
              "isPersonal isArchived emailSendEnabled currency { code } } } } }",
              "page", "pageSize"),
        query("get_business", "One business: its currency, and whether Wave can email from it",
              "query($businessId: ID!) { business(id: $businessId) { id name isPersonal timezone "
              "emailSendEnabled currency { code } } }", "businessId"),
        query("list_accounts", "The chart of accounts with balances, the closest thing to a report",
              _wave_list("accounts", "id name displayId description isArchived balance "
                         "balanceInBusinessCurrency currency { code } type { name value } "
                         "subtype { name value }"),
              "businessId", "page", "pageSize"),
        query("list_customers", "Customers, with what they owe",
              _wave_list("customers", WAVE_CUSTOMER, "$email: String", "email: $email"),
              "businessId", "email", "page", "pageSize"),
        query("get_customer", "One customer", _wave_one("customer", WAVE_CUSTOMER),
              "businessId", "id"),
        query("list_vendors", "Vendors. Wave's API can only read them",
              _wave_list("vendors", "id name email phone displayId isArchived currency { code }",
                         "$email: String", "email: $email"),
              "businessId", "email", "page", "pageSize"),
        query("list_products", "Products and services, with their IDs for an invoice",
              _wave_list("products", "id name description unitPrice isSold isBought isArchived"),
              "businessId", "page", "pageSize"),
        query("list_sales_taxes", "Sales taxes",
              _wave_list("salesTaxes", "id name abbreviation rate isArchived"),
              "businessId", "page", "pageSize"),
        query("list_invoices", "Find invoices",
              _wave_list("invoices", WAVE_INVOICE,
                         "$status: InvoiceStatus, $customerId: ID, $invoiceNumber: String, "
                         "$invoiceDateStart: Date, $invoiceDateEnd: Date",
                         "status: $status, customerId: $customerId, "
                         "invoiceNumber: $invoiceNumber, invoiceDateStart: $invoiceDateStart, "
                         "invoiceDateEnd: $invoiceDateEnd"),
              "businessId", "status", "customerId", "invoiceNumber", "invoiceDateStart",
              "invoiceDateEnd", "page", "pageSize"),
        query("get_invoice", "One invoice, with its lines and payments",
              _wave_one("invoice", WAVE_INVOICE, f"{WAVE_ITEMS} payments {{ {WAVE_PAYMENT} }}"),
              "businessId", "id"),
        query("get_invoice_payment", "One payment on an invoice",
              _wave_one("invoicePayment", WAVE_PAYMENT, "invoice { id invoiceNumber }"),
              "businessId", "id"),
        query("list_estimates", "Find estimates",
              _wave_list("estimates", WAVE_ESTIMATE,
                         "$status: EstimateListStatusFilter, $customerId: ID",
                         "status: $status, customerId: $customerId"),
              "businessId", "status", "customerId", "page", "pageSize"),
        query("get_estimate", "One estimate", _wave_one("estimate", WAVE_ESTIMATE),
              "businessId", "id"),

        # Held.
        mutation("create_customer", "Add a customer",
                 _wave_change("customerCreate", "customer { id name }", businessId="ID!",
                              **(WAVE_CONTACT_FIELDS | {"name": "String!"})),
                 "businessId", *WAVE_CONTACT_FIELDS),
        mutation("update_customer", "Change a customer",
                 _wave_change("customerPatch", "customer { id name }", id="ID!",
                              **WAVE_CONTACT_FIELDS),
                 "id", *WAVE_CONTACT_FIELDS),
        mutation("create_product", "Add a product or service",
                 _wave_change("productCreate", "product { id name }", businessId="ID!",
                              name="String!", unitPrice="Decimal!", description="String",
                              incomeAccountId="ID", expenseAccountId="ID"),
                 "businessId", "name", "unitPrice", "description", "incomeAccountId",
                 "expenseAccountId"),
        mutation("create_estimate", "Create a draft estimate",
                 _wave_change("estimateCreate", f"estimate {{ {WAVE_ESTIMATE} }}", businessId="ID!",
                              customerId="ID!", status="EstimateCreateStatus",
                              currency="CurrencyCode", title="String", poNumber="String",
                              estimateDate="Date", dueDate="Date",
                              items="[EstimateCreateItemInput!]", memo="String", footer="String"),
                 "businessId", "customerId", "estimateDate", "dueDate", "items", "memo"),

        # Held only if it says it's a draft.
        mutation("create_invoice", "Create an invoice. Give status DRAFT for a draft",
                 _wave_change("invoiceCreate", f"invoice {{ {WAVE_INVOICE} }}", businessId="ID!",
                              **(WAVE_INVOICE_FIELDS | {"customerId": "ID!"})),
                 "businessId", "customerId", "status", "items", "invoiceDate", "dueDate", "memo",
                 why=_wave_draft_why),

        # The name typed.
        mutation("update_invoice", "Change an invoice",
                 _wave_change("invoicePatch", f"invoice {{ {WAVE_INVOICE} }}", id="ID!",
                              **WAVE_INVOICE_FIELDS),
                 "id", *WAVE_INVOICE_FIELDS, why=EDITS_BOOKS),
        mutation("approve_invoice", "Approve a draft invoice, which can't be taken back",
                 _wave_change("invoiceApprove", "invoice { id status }", invoiceId="ID!"),
                 "invoiceId", why=BOOKS),
        mutation("send_invoice", "Email an invoice to a customer",
                 _wave_change("invoiceSend", "invoice { id status lastSentAt }", invoiceId="ID!",
                              to="[String!]!", subject="String", message="String",
                              attachPDF="Boolean!", ccMyself="Boolean"),
                 "invoiceId", "to", "subject", "message", "attachPDF", "ccMyself", why=SENDS),
        mutation("delete_invoice", "Delete an invoice",
                 _wave_change("invoiceDelete", invoiceId="ID!"), "invoiceId", why=DELETES),
        mutation("record_invoice_payment", "Record a payment on an invoice",
                 _wave_change("invoicePaymentCreateManual", f"invoicePayment {{ {WAVE_PAYMENT} }}",
                              invoiceId="ID!", paymentAccountId="ID!", amount="Decimal!",
                              paymentDate="Date!", paymentMethod="InvoicePaymentMethod!",
                              exchangeRate="Decimal!", memo="String"),
                 "invoiceId", "paymentAccountId", "amount", "paymentDate", "paymentMethod",
                 "exchangeRate", "memo", why=PAYMENT, money=WAVE_PAYMENT_CARD),
        mutation("delete_invoice_payment", "Delete a payment on an invoice",
                 _wave_change("invoicePaymentDelete", id="ID!"), "id",
                 why="deletes a recorded payment"),
        mutation("record_transaction", "Record money in or out of an account (Wave's beta call)",
                 "mutation($businessId: ID!, $externalId: String!, $date: Date!, "
                 "$description: String!, $notes: String, $accountId: ID!, $amount: Decimal!, "
                 "$direction: TransactionDirection!, "
                 "$lineItems: [MoneyTransactionCreateLineItemInput!]!) { moneyTransactionCreate("
                 "input: { businessId: $businessId, externalId: $externalId, date: $date, "
                 "description: $description, notes: $notes, anchor: { accountId: $accountId, "
                 "amount: $amount, direction: $direction }, lineItems: $lineItems }) { didSucceed "
                 "inputErrors { path message code } transaction { id } } }",
                 "businessId", "externalId", "date", "description", "notes", "accountId", "amount",
                 "direction", "lineItems", why=BANK, money=WAVE_MONEY_MOVES),
        mutation("send_estimate", "Email an estimate to a customer",
                 _wave_change("estimateSend", "estimate { id status }", estimateId="ID!",
                              to="[String!]!", subject="String", message="String",
                              attachPDF="Boolean!", ccMyself="Boolean"),
                 "estimateId", "to", "subject", "message", "attachPDF", "ccMyself",
                 why="sends an estimate to a customer"),
        mutation("convert_estimate_to_invoice", "Make an invoice from an estimate",
                 _wave_change("convertEstimateToInvoice", "invoiceId", estimateId="ID!"),
                 "estimateId", why="makes an invoice, which may go on the books"),
        mutation("delete_estimate", "Delete an estimate",
                 _wave_change("estimateDelete", estimateId="ID!"), "estimateId", why=DELETES),
        mutation("delete_customer", "Delete a customer",
                 _wave_change("customerDelete", id="ID!"), "id", why=DELETES),
    ),
)  # fmt: skip


# --- QuickBooks Online ---
#
# Confirmed against Intuit's Java SDK (the XSD and report names) and its CLI and MCP server.
# Create, update, void and delete all share POST /<entity>; a query value says which. A query
# written into `path` is lost (rest.call's params replace it), so the call must send it.

QBO_NO_DRAFTS = "creates a record that's on the books at once: QuickBooks has no drafts"
QBO_HARMLESS_QUERY = frozenset({"minorversion", "requestid"})
QBO_ID = ("Id", "SyncToken", "sparse")
QBO_PERSON = (
    *QBO_ID, "DisplayName", "Title", "GivenName", "MiddleName", "FamilyName", "Suffix",
    "CompanyName", "PrintOnCheckName", "PrimaryEmailAddr", "PrimaryPhone", "AlternatePhone",
    "Mobile", "Fax", "WebAddr", "Notes", "BillAddr", "ShipAddr", "CurrencyRef",
)  # fmt: skip
# Neither takes Active (deactivating). A vendor's bank details aren't in the list either.
QBO_CUSTOMER = (*QBO_PERSON, "SalesTermRef", "PaymentMethodRef", "Taxable")
QBO_VENDOR = (*QBO_PERSON, "TermRef")
QBO_REPORT = ("start_date", "end_date", "date_macro", "accounting_method", "summarize_column_by")
QBO_AGING = ("report_date", "aging_period", "num_periods", "customer", "vendor")
QBO_SALE = Money(("TotalAmt",), ("CurrencyRef.value",), ("CustomerRef.name", "CustomerRef.value"),
                 ("DepositToAccountRef.value", "ARAccountRef.value"))  # fmt: skip
QBO_BILL = Money(("TotalAmt",), ("CurrencyRef.value",), ("VendorRef.name", "VendorRef.value"),
                 ("APAccountRef.value",))  # fmt: skip
QBO_BILL_PAYMENT = Money(
    ("TotalAmt",), ("CurrencyRef.value",), ("VendorRef.name", "VendorRef.value"),
    ("CheckPayment.BankAccountRef.value", "CreditCardPayment.CCAccountRef.value"),
)  # fmt: skip
QBO_EXPENSE = Money(("TotalAmt",), ("CurrencyRef.value",), ("EntityRef.name", "EntityRef.value"),
                    ("AccountRef.value",))  # fmt: skip
QBO_JOURNAL = Money(("TotalAmt",), ("CurrencyRef.value",), (), ())


def _quickbooks_held_why(args: JSON) -> str | None:
    """Held, unless the call's query turns this POST into something else: operation=delete on
    this same endpoint deletes."""
    asked = {str(k).lower() for k in cast(JSON, args.get("query") or {})} - QBO_HARMLESS_QUERY
    return f"sets {', '.join(sorted(asked))}, which can delete or void" if asked else None


def _quickbooks_env(fields: JSON) -> Env | None:
    host = str(fields.get("api_host") or "")
    if not host:
        return None
    return Env.DEV if host.startswith("sandbox-") else Env.PROD


def _quickbooks_who(found: JSON) -> str:
    prefs = cast(JSON, found.get("Preferences") or {})
    home = cast(JSON, cast(JSON, prefs.get("CurrencyPrefs") or {}).get("HomeCurrency") or {})
    return f"a company in {home.get('value')}" if home.get("value") else "the company"


QUICKBOOKS = Vendor(
    "quickbooks", "QuickBooks Online", CATEGORY, "Invoices, bills, payments, reports",
    "https://{api_host}/v3/company/{realm_id}",
    provider="intuit", headers=JSON_ONLY, never_trusted=True,
    secrets=(
        Secret("api_host", "QuickBooks address", "Production or sandbox, set by the sign-in.",
               hidden=False, pattern=r"^(sandbox-)?quickbooks\.api\.intuit\.com$"),
        Secret("realm_id", "Company ID", "Set by the sign-in.", hidden=False,
               pattern=r"^[0-9]{1,20}$"),
    ),
    probe="get_preferences", who=_quickbooks_who, env_of=_quickbooks_env,
    words=("quickbooks", "qbo", "intuit", "accounting", "invoices", "bills", "bookkeeping"),
    key_url="https://developer.intuit.com/app/developer/dashboard",
    steps="Add your Intuit app in Settings, Developer apps, then sign in from this tile and "
    "choose the company. Each company is its own connection.",
    note="One connection is one company. Reports and searches run. Estimates, purchase orders, "
    "customers and vendors wait for approval. QuickBooks has no draft invoices or bills: creating "
    "one posts it, so it needs the name typed, as do payments, journal entries, sends, voids and "
    "deletes. Nothing runs by itself.",
    ops=(
        reads("get_preferences", "/preferences", "This company's settings, like its currency"),
        reads("search", "/query", "Find anything with a select statement, like select * from "
              "Invoice where Balance > '0'. Use it for customers, vendors, bills, payments, "
              "accounts and items", "query"),
        reads("report_profit_and_loss", "/reports/ProfitAndLoss", "Profit and loss", *QBO_REPORT),
        reads("report_balance_sheet", "/reports/BalanceSheet", "Balance sheet", *QBO_REPORT),
        reads("report_cash_flow", "/reports/CashFlow", "Cash flow", *QBO_REPORT),
        reads("report_trial_balance", "/reports/TrialBalance", "Trial balance", *QBO_REPORT),
        reads("report_aged_receivables", "/reports/AgedReceivables", "Who owes what, by age",
              *QBO_AGING),
        reads("report_aged_payables", "/reports/AgedPayables", "What's owed to vendors, by age",
              *QBO_AGING),
        reads("get_invoice", "/invoice/{invoice_id}", "Read one invoice"),
        reads("get_estimate", "/estimate/{estimate_id}", "Read one estimate"),

        # Held: these don't touch the books. The query can turn one into a delete.
        final("create_customer", "POST", "/customer", "Add a customer", _quickbooks_held_why,
              "DisplayName", "PrimaryEmailAddr", only=QBO_CUSTOMER),
        final("update_customer", "POST", "/customer", "Change a customer (give Id and SyncToken)",
              _quickbooks_held_why, "Id", "SyncToken", "sparse", only=QBO_CUSTOMER),
        final("create_vendor", "POST", "/vendor", "Add a vendor", _quickbooks_held_why,
              "DisplayName", "PrimaryEmailAddr", only=QBO_VENDOR),
        final("update_vendor", "POST", "/vendor", "Change a vendor (give Id and SyncToken)",
              _quickbooks_held_why, "Id", "SyncToken", "sparse", only=QBO_VENDOR),
        final("create_estimate", "POST", "/estimate", "Create an estimate", _quickbooks_held_why,
              "CustomerRef", "Line", "TxnDate", "ExpirationDate", money=QBO_SALE),
        final("update_estimate", "POST", "/estimate", "Change an estimate (give Id and SyncToken)",
              _quickbooks_held_why, "Id", "SyncToken", "sparse", "Line", money=QBO_SALE),
        final("create_purchase_order", "POST", "/purchaseorder", "Create a purchase order",
              _quickbooks_held_why, "VendorRef", "APAccountRef", "Line", money=QBO_BILL),

        # The name typed.
        final("create_invoice", "POST", "/invoice", "Create an invoice, which posts at once",
              QBO_NO_DRAFTS, "CustomerRef", "Line", "TxnDate", "DueDate", money=QBO_SALE),
        final("update_invoice", "POST", "/invoice", "Change an invoice (give Id and SyncToken)",
              EDITS_BOOKS, "Id", "SyncToken", "sparse", "Line", money=QBO_SALE),
        final("send_invoice", "POST", "/invoice/{invoice_id}/send", "Email an invoice", SENDS,
              "sendTo"),
        final("void_invoice", "POST", "/invoice", "Void an invoice. Send query operation=void, "
              "and Id and SyncToken in the body", VOIDS, "operation=void", "Id", "SyncToken",
              only=QBO_ID),
        final("delete_invoice", "POST", "/invoice", "Delete an invoice. Send query "
              "operation=delete, and Id and SyncToken in the body", DELETES, "operation=delete",
              "Id", "SyncToken", only=QBO_ID),
        final("create_bill", "POST", "/bill", "Enter a bill, which posts at once", QBO_NO_DRAFTS,
              "VendorRef", "Line", "TxnDate", "DueDate", money=QBO_BILL),
        final("delete_bill", "POST", "/bill", "Delete a bill. Send query operation=delete, and Id "
              "and SyncToken in the body", DELETES, "operation=delete", "Id", "SyncToken",
              only=QBO_ID),
        final("create_payment", "POST", "/payment", "Record a customer's payment", PAYMENT,
              "CustomerRef", "TotalAmt", "DepositToAccountRef", "Line", money=QBO_SALE),
        final("void_payment", "POST", "/payment", "Void a payment. Send query operation=update and "
              "include=void, and Id and SyncToken in the body", VOIDS, "operation=update",
              "include=void", "Id", "SyncToken", "sparse", only=QBO_ID),
        final("delete_payment", "POST", "/payment", "Delete a payment. Send query "
              "operation=delete, and Id and SyncToken in the body", DELETES, "operation=delete",
              "Id", "SyncToken", only=QBO_ID),
        final("create_bill_payment", "POST", "/billpayment", "Pay a bill", PAYMENT, "VendorRef",
              "TotalAmt", "PayType", "CheckPayment", "CreditCardPayment", "Line",
              money=QBO_BILL_PAYMENT),
        final("create_expense", "POST", "/purchase", "Record an expense paid by bank or card", BANK,
              "AccountRef", "PaymentType", "EntityRef", "Line", money=QBO_EXPENSE),
        final("create_journal_entry", "POST", "/journalentry", "Write a journal entry", JOURNAL,
              "Line", "TxnDate", money=QBO_JOURNAL),
        final("send_estimate", "POST", "/estimate/{estimate_id}/send", "Email an estimate",
              "sends an estimate to a customer", "sendTo"),
        final("delete_estimate", "POST", "/estimate", "Delete an estimate. Send query "
              "operation=delete, and Id and SyncToken in the body", DELETES, "operation=delete",
              "Id", "SyncToken", only=QBO_ID),
    ),
)  # fmt: skip


# --- Xero ---
#
# Confirmed against XeroAPI/Xero-OpenAPI (xero_accounting.yaml), and XeroAPI/Api.Documentation
# for the single-record bodies. Every create is PUT, which only creates. `only` keeps a body to
# one record, so {"Invoices": [...]} (a batch) can't go past the Status check.

XERO_DRAFT = ("DRAFT",)
XERO_INVOICE = (
    "Type", "Contact", "LineItems", "Date", "DueDate", "LineAmountTypes", "InvoiceNumber",
    "Reference", "BrandingThemeID", "Url", "CurrencyCode", "CurrencyRate", "Status", "Total",
    "ExpectedPaymentDate", "PlannedPaymentDate",
)  # fmt: skip
XERO_CREDIT_NOTE = (
    "Type", "Contact", "LineItems", "Date", "LineAmountTypes", "CreditNoteNumber", "Reference",
    "BrandingThemeID", "CurrencyCode", "CurrencyRate", "Status", "Total",
)  # fmt: skip
XERO_QUOTE = (
    "Contact", "LineItems", "Date", "ExpiryDate", "QuoteNumber", "Reference", "Terms", "Title",
    "Summary", "BrandingThemeID", "CurrencyCode", "CurrencyRate", "LineAmountTypes", "Status",
    "Total",
)  # fmt: skip
XERO_PURCHASE_ORDER = (
    "Contact", "LineItems", "Date", "DeliveryDate", "LineAmountTypes", "PurchaseOrderNumber",
    "Reference", "BrandingThemeID", "CurrencyCode", "CurrencyRate", "Status", "DeliveryAddress",
    "AttentionTo", "Telephone", "DeliveryInstructions", "Total",
)  # fmt: skip
XERO_CONTACT = (
    "Name", "FirstName", "LastName", "EmailAddress", "ContactNumber", "AccountNumber",
    "CompanyNumber", "TaxNumber", "BankAccountDetails", "DefaultCurrency", "Addresses", "Phones",
    "ContactPersons", "PaymentTerms",
)  # fmt: skip
XERO_PAYMENT = (
    "Invoice", "CreditNote", "Prepayment", "Overpayment", "InvoiceNumber", "CreditNoteNumber",
    "Account", "Code", "Date", "CurrencyRate", "Amount", "BankAmount", "Reference", "IsReconciled",
)  # fmt: skip
XERO_BANK_TRANSACTION = (
    "Type", "Contact", "LineItems", "BankAccount", "IsReconciled", "Date", "Reference",
    "CurrencyCode", "CurrencyRate", "Url", "LineAmountTypes", "Total",
)  # fmt: skip
XERO_MANUAL_JOURNAL = (
    "Narration", "JournalLines", "Date", "LineAmountTypes", "Status", "Url",
    "ShowOnCashBasisReports",
)  # fmt: skip
XERO_LIST = ("where", "order", "page", "pageSize")
XERO_DOCUMENT = Money(("Total",), ("CurrencyCode",), ("Contact.Name", "Contact.ContactID"), ())
XERO_PAYMENT_CARD = Money(
    ("Amount",), ("CurrencyCode",),
    ("Invoice.InvoiceNumber", "Invoice.InvoiceID", "InvoiceNumber", "CreditNote.CreditNoteID",
     "CreditNoteNumber"),
    ("Account.Code", "Account.AccountID", "Code"),
)  # fmt: skip
XERO_BANK_CARD = Money(("Total",), ("CurrencyCode",), ("Contact.Name", "Contact.ContactID"),
                       ("BankAccount.Code", "BankAccount.AccountID"))  # fmt: skip
XERO_TRANSFER_CARD = Money(("Amount",), (), ("ToBankAccount.Code", "ToBankAccount.AccountID"),
                           ("FromBankAccount.Code", "FromBankAccount.AccountID"),
                           who="To account", which="From account")  # fmt: skip


def _xero_status_why(args: JSON) -> str | None:
    """Held only as a draft: DRAFT, or no status, which Xero makes a draft."""
    status = str(_body(args).get("Status") or "DRAFT")
    return None if status in XERO_DRAFT else f"sets its status to {status}, which {BOOKS}"


def _xero_contact_why(args: JSON) -> str | None:
    """A changed bank account is how a supplier payment gets redirected."""
    return "sets a contact's bank details" if "BankAccountDetails" in _body(args) else None


def _xero_who(found: JSON) -> str:
    organisations = cast(list[JSON], found.get("Organisations") or [])
    return str(organisations[0].get("Name")) if organisations else "the organisation"


XERO = Vendor(
    "xero", "Xero", CATEGORY, "Invoices, bills, contacts, reports",
    "https://api.xero.com/api.xro/2.0",
    provider="xero", headers=JSON_ONLY, never_trusted=True, account_header="Xero-tenant-id",
    scope=(
        ScopeField("tenant", "Xero organisation", ("account",),
                   "The organisation's tenant ID. A sign-in can reach several."),
    ),
    probe="get_organisation", who=_xero_who,
    words=("xero", "accounting", "invoices", "bills", "bookkeeping", "contacts"),
    key_url="https://developer.xero.com/app/manage",
    steps="Add your Xero app in Settings, Developer apps, then sign in from this tile and choose "
    "the organisations it may reach.",
    note="One sign-in can reach several organisations, so a call names one. Reports and lookups "
    "run. Draft invoices, bills, credit notes, quotes and purchase orders wait for approval, and "
    "so do contacts. Approving, editing an invoice, sending, payments, bank entries, journals, "
    "voids and deletes need the name typed. Nothing runs by itself.",
    ops=(
        reads("get_organisation", "/Organisation", "This organisation"),
        reads("list_accounts", "/Accounts", "The chart of accounts", "where", "order"),
        reads("list_contacts", "/Contacts", "Find contacts", "searchTerm", "summaryOnly",
              "includeArchived", *XERO_LIST),
        reads("get_contact", "/Contacts/{contact_id}", "Read one contact"),
        reads("list_invoices", "/Invoices", "Find invoices (ACCREC) and bills (ACCPAY)", "Statuses",
              "ContactIDs", "InvoiceNumbers", "IDs", "searchTerm", "summaryOnly", *XERO_LIST),
        reads("get_invoice", "/Invoices/{invoice_id}", "Read one invoice or bill"),
        reads("list_payments", "/Payments", "Find payments", *XERO_LIST),
        reads("get_payment", "/Payments/{payment_id}", "Read one payment"),
        reads("list_bank_transactions", "/BankTransactions", "Spent and received money",
              *XERO_LIST),
        reads("list_credit_notes", "/CreditNotes", "Find credit notes", *XERO_LIST),
        reads("list_manual_journals", "/ManualJournals", "Find manual journals", *XERO_LIST),
        reads("list_quotes", "/Quotes", "Find quotes", "Status", "ContactID", "QuoteNumber",
              "DateFrom", "DateTo", "page", "order"),
        reads("list_purchase_orders", "/PurchaseOrders", "Find purchase orders", "Status",
              "DateFrom", "DateTo", "order", "page", "pageSize"),
        reads("list_items", "/Items", "Products and services", "where", "order"),
        reads("list_tax_rates", "/TaxRates", "Tax rates", "where", "order"),
        reads("report_profit_and_loss", "/Reports/ProfitAndLoss", "Profit and loss", "fromDate",
              "toDate", "periods", "timeframe", "standardLayout", "paymentsOnly"),
        reads("report_balance_sheet", "/Reports/BalanceSheet", "Balance sheet", "date", "periods",
              "timeframe", "standardLayout", "paymentsOnly"),
        reads("report_trial_balance", "/Reports/TrialBalance", "Trial balance", "date",
              "paymentsOnly"),
        reads("report_aged_receivables", "/Reports/AgedReceivablesByContact",
              "What one contact owes, by age", "contactId", "date", "fromDate", "toDate"),
        reads("report_aged_payables", "/Reports/AgedPayablesByContact",
              "What's owed to one contact, by age", "contactId", "date", "fromDate", "toDate"),
        reads("report_bank_summary", "/Reports/BankSummary", "Bank balances and movements",
              "fromDate", "toDate"),
        reads("report_executive_summary", "/Reports/ExecutiveSummary", "Key figures for a month",
              "date"),

        # Held. A contact's bank details need the name.
        final("create_contact", "PUT", "/Contacts", "Add a contact", _xero_contact_why, "Name",
              "EmailAddress", only=XERO_CONTACT),
        final("update_contact", "POST", "/Contacts/{contact_id}", "Change a contact",
              _xero_contact_why, "Name", "EmailAddress", only=XERO_CONTACT),

        # Held as a draft. Any other Status needs the name.
        final("create_invoice", "PUT", "/Invoices", "Create an invoice (Type ACCREC) or a bill "
              "(Type ACCPAY). With no Status it's a draft", _xero_status_why, "Type", "Contact",
              "LineItems", "Date", "DueDate", "Status", only=XERO_INVOICE, money=XERO_DOCUMENT),
        final("create_credit_note", "PUT", "/CreditNotes", "Create a credit note. With no Status "
              "it's a draft", _xero_status_why, "Type", "Contact", "LineItems", "Status",
              only=XERO_CREDIT_NOTE, money=XERO_DOCUMENT),
        final("create_quote", "PUT", "/Quotes", "Create a quote. With no Status it's a draft",
              _xero_status_why, "Contact", "LineItems", "Date", "ExpiryDate", "Status",
              only=XERO_QUOTE, money=XERO_DOCUMENT),
        final("create_purchase_order", "PUT", "/PurchaseOrders", "Create a purchase order. With no "
              "Status it's a draft", _xero_status_why, "Contact", "LineItems", "Date", "Status",
              only=XERO_PURCHASE_ORDER, money=XERO_DOCUMENT),

        # The name typed.
        final("update_invoice", "POST", "/Invoices/{invoice_id}", "Change an invoice or a bill",
              EDITS_BOOKS, "LineItems", "Reference", "DueDate", "Date", "Contact",
              only=tuple(f for f in XERO_INVOICE if f != "Status"), money=XERO_DOCUMENT),
        final("approve_invoice", "POST", "/Invoices/{invoice_id}", "Approve an invoice or a bill: "
              "send Status AUTHORISED", BOOKS, "Status", only=("Status",)),
        final("void_invoice", "POST", "/Invoices/{invoice_id}", "Void an approved invoice: send "
              "Status VOIDED", VOIDS, "Status", only=("Status",)),
        final("delete_invoice", "POST", "/Invoices/{invoice_id}", "Delete a draft invoice: send "
              "Status DELETED", DELETES, "Status", only=("Status",)),
        final("email_invoice", "POST", "/Invoices/{invoice_id}/Email", "Email an approved invoice "
              "to its contact. Send an empty body", SENDS, only=("Status",)),
        final("create_payment", "PUT", "/Payments", "Record a payment on an invoice or credit "
              "note", PAYMENT, "Invoice", "Account", "Date", "Amount", "Reference",
              only=XERO_PAYMENT, money=XERO_PAYMENT_CARD),
        final("delete_payment", "POST", "/Payments/{payment_id}", "Delete a payment: send Status "
              "DELETED", "deletes a recorded payment", "Status", only=("Status",)),
        final("create_bank_transaction", "PUT", "/BankTransactions", "Record money spent or "
              "received", BANK, "Type", "Contact", "BankAccount", "LineItems", "Date",
              only=XERO_BANK_TRANSACTION, money=XERO_BANK_CARD),
        final("create_bank_transfer", "PUT", "/BankTransfers", "Record a transfer between bank "
              "accounts", BANK, "FromBankAccount", "ToBankAccount", "Amount", "Date", "Reference",
              only=("FromBankAccount", "ToBankAccount", "Amount", "Date", "Reference"),
              money=XERO_TRANSFER_CARD),
        final("create_manual_journal", "PUT", "/ManualJournals", "Write a manual journal", JOURNAL,
              "Narration", "JournalLines", "Date", "Status", only=XERO_MANUAL_JOURNAL),
    ),
)  # fmt: skip


# --- Dynamics 365 Business Central ---
#
# Confirmed against Microsoft's API v2.0 docs (MicrosoftDocs/dynamics365smb-devitpro-pb).
# A PATCH or DELETE must carry If-Match. A vendor's headers go on every call, reads too.

BC = "/companies({account})"
BC_LIST = ("$filter", "$orderby", "$top", "$skip", "$select", "$expand")
BC_SALES_EDIT = (
    "invoiceDate", "postingDate", "dueDate", "externalDocumentNumber",
    "customerPurchaseOrderReference", "paymentTermsId", "salesperson",
)  # fmt: skip
BC_PURCHASE_EDIT = ("invoiceDate", "postingDate", "dueDate", "vendorInvoiceNumber")
BC_SALES_BACK = Snapshot("get_sales_invoice", BC_SALES_EDIT, says="the invoice's dates and details")
BC_PURCHASE_BACK = Snapshot("get_purchase_invoice", BC_PURCHASE_EDIT,
                            says="the invoice's dates and number")  # fmt: skip
BC_PAYMENT = Money(("amount",), (), ("customerNumber", "customerId"), ("journal_id",),
                   who="Customer", which="Journal")  # fmt: skip
BC_JOURNAL_LINE = Money(("amount",), (), ("accountNumber", "accountId"),
                        ("balancingAccountNumber", "balancingAccountId"), who="Account",
                        which="Balancing account")  # fmt: skip


def _bc_who(found: JSON) -> str:
    companies = cast(list[JSON], found.get("value") or [])
    names = [str(c.get("displayName") or c.get("name")) for c in companies]
    return ", ".join(names) if names else "no companies yet"


def _report(name: str, entity: str, says: str, *params: str) -> Op:
    return reads(name, f"{BC}/{entity}", says, *params)


BUSINESS_CENTRAL = Vendor(
    "businesscentral", "Dynamics 365 Business Central", CATEGORY,
    "Sales and purchase invoices, journals, reports",
    "https://api.businesscentral.dynamics.com/v2.0/{tenant_id}/{environment}/api/v2.0",
    provider="microsoft", write_headers={"If-Match": "*"}, never_trusted=True,
    secrets=(
        Secret("tenant_id", "Tenant ID", "Your Microsoft Entra tenant, set by the sign-in.",
               hidden=False, pattern=r"^[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$"),
        Secret("environment", "Environment", "Its name in the Business Central admin center, like "
               "Production or Sandbox.", hidden=False, pattern=r"^[A-Za-z0-9_-]{1,30}$"),
    ),
    scope=(
        ScopeField("company", "Business Central company", ("account",),
                   "The company's ID, from list_companies."),
    ),
    probe="list_companies", who=_bc_who,
    words=("business central", "dynamics", "dynamics 365", "navision", "microsoft", "erp",
           "accounting", "invoices"),
    key_url="https://businesscentral.dynamics.com",
    steps="Add your Microsoft app in Settings, Developer apps, with the Business Central API "
    "permission, then sign in from this tile and give the environment's name.",
    note="One connection is one environment, and a call names the company. Reports and lookups "
    "run. Draft sales and purchase invoices wait for approval. Posting, sending, canceling, "
    "payments, journal lines and deletes need the name typed. Nothing runs by itself.",
    ops=(
        reads("list_companies", "/companies", "The companies in this environment"),
        reads("get_company_information", f"{BC}/companyInformation", "This company's details"),
        reads("list_customers", f"{BC}/customers", "Find customers", *BC_LIST),
        reads("list_vendors", f"{BC}/vendors", "Find vendors", *BC_LIST),
        reads("list_accounts", f"{BC}/accounts", "The chart of accounts", *BC_LIST),
        reads("list_general_ledger_entries", f"{BC}/generalLedgerEntries",
              "Entries on the books", *BC_LIST),
        reads("list_sales_invoices", f"{BC}/salesInvoices", "Find sales invoices", *BC_LIST),
        reads("get_sales_invoice", f"{BC}/salesInvoices({{invoice_id}})", "Read one sales invoice",
              "$expand"),
        reads("list_purchase_invoices", f"{BC}/purchaseInvoices", "Find purchase invoices",
              *BC_LIST),
        reads("get_purchase_invoice", f"{BC}/purchaseInvoices({{invoice_id}})",
              "Read one purchase invoice", "$expand"),
        reads("list_customer_payment_journals", f"{BC}/customerPaymentJournals",
              "Journals customer payments go in", *BC_LIST),
        reads("list_customer_payments", f"{BC}/customerPayments", "Find customer payments",
              *BC_LIST),
        reads("list_journals", f"{BC}/journals", "General journals", *BC_LIST),
        reads("list_journal_lines", f"{BC}/journals({{journal_id}})/journalLines",
              "Lines in a journal", *BC_LIST),
        _report("report_trial_balance", "trialBalances", "Trial balance", "$filter"),
        _report("report_balance_sheet", "balanceSheets", "Balance sheet", "$filter", "$orderby"),
        _report("report_income_statement", "incomeStatements", "Income statement", "$filter",
                "$orderby"),
        _report("report_cash_flow", "cashFlowStatements", "Cash flow", "$filter", "$orderby"),
        _report("report_aged_receivables", "agedAccountsReceivables", "Who owes what, by age",
                "$filter"),
        _report("report_aged_payables", "agedAccountsPayables", "What's owed to vendors, by age",
                "$filter"),

        # Held: a draft isn't on the books. A posted invoice is a different record.
        held("create_sales_invoice", "POST", f"{BC}/salesInvoices", "Create a draft sales invoice",
             "customerId", "customerNumber", "invoiceDate", "dueDate", "externalDocumentNumber"),
        held("update_sales_invoice", "PATCH", f"{BC}/salesInvoices({{invoice_id}})",
             "Change a draft sales invoice", *BC_SALES_EDIT, only=BC_SALES_EDIT,
             undo=BC_SALES_BACK),
        held("add_sales_invoice_line", "POST",
             f"{BC}/salesInvoices({{invoice_id}})/salesInvoiceLines",
             "Add a line to a draft sales invoice", "lineType", "lineObjectNumber", "description",
             "quantity", "unitPrice"),
        held("create_purchase_invoice", "POST", f"{BC}/purchaseInvoices",
             "Create a draft purchase invoice", "vendorId", "vendorNumber", "invoiceDate",
             "dueDate", "vendorInvoiceNumber"),
        held("update_purchase_invoice", "PATCH", f"{BC}/purchaseInvoices({{invoice_id}})",
             "Change a draft purchase invoice", *BC_PURCHASE_EDIT, only=BC_PURCHASE_EDIT,
             undo=BC_PURCHASE_BACK),
        held("add_purchase_invoice_line", "POST",
             f"{BC}/purchaseInvoices({{invoice_id}})/purchaseInvoiceLines",
             "Add a line to a draft purchase invoice", "lineType", "lineObjectNumber",
             "description", "quantity", "unitCost"),

        # The name typed.
        final("post_sales_invoice", "POST",
              f"{BC}/salesInvoices({{invoice_id}})/Microsoft.NAV.post", "Post a sales invoice",
              BOOKS),
        final("post_and_send_sales_invoice", "POST",
              f"{BC}/salesInvoices({{invoice_id}})/Microsoft.NAV.postAndSend",
              "Post a sales invoice and email it", f"{BOOKS} and {SENDS}"),
        final("send_sales_invoice", "POST",
              f"{BC}/salesInvoices({{invoice_id}})/Microsoft.NAV.send", "Email a sales invoice",
              SENDS),
        final("cancel_sales_invoice", "POST",
              f"{BC}/salesInvoices({{invoice_id}})/Microsoft.NAV.cancel",
              "Cancel a posted sales invoice", VOIDS),
        final("delete_sales_invoice", "DELETE", f"{BC}/salesInvoices({{invoice_id}})",
              "Delete a draft sales invoice", DELETES),
        final("post_purchase_invoice", "POST",
              f"{BC}/purchaseInvoices({{invoice_id}})/Microsoft.NAV.post",
              "Post a purchase invoice", BOOKS),
        final("delete_purchase_invoice", "DELETE", f"{BC}/purchaseInvoices({{invoice_id}})",
              "Delete a draft purchase invoice", DELETES),
        final("create_customer_payment", "POST",
              f"{BC}/customerPaymentJournals({{journal_id}})/customerPayments",
              "Add a customer's payment to a journal", PAYMENT, "customerNumber", "amount",
              "postingDate", "appliesToInvoiceNumber", "description", money=BC_PAYMENT),
        final("add_journal_line", "POST", f"{BC}/journals({{journal_id}})/journalLines",
              "Add a line to a general journal", JOURNAL, "accountNumber", "postingDate", "amount",
              "description", money=BC_JOURNAL_LINE),
        final("post_journal", "POST", f"{BC}/journals({{journal_id}})/Microsoft.NAV.post",
              "Post a general journal", BOOKS),
    ),
)  # fmt: skip

VENDORS = (WAVE, QUICKBOOKS, XERO, BUSINESS_CENTRAL)
