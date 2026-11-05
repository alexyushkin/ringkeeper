"""Payments: Square, Helcim, Lemon Squeezy, Clover, Braintree, BILL, and Plaid for bank data.

Holds, by the user's rule: payment links, invoices and customers wait for approval; charges,
refunds, captures, voids, subscription changes, dispute responses and bill payments need
the name typed. A card or bank account number never goes through: nothing here takes one,
and a call that carries one anyway is refused before it's held (ringkeeper.sensitive).
Payments use the vendor's own tokens, made on its hosted pages.

Left out on purpose:
- Square: cards, bank accounts, gift cards, terminals, OAuth, team members, webhooks.
- Helcim: verify and withdraw (their fields are raw card numbers), bank account storage, the
  HelcimPay.js session (it answers with a secret token).
- Lemon Squeezy: license keys and files (they answer with the key or a download link).
- Clover: charges, capture and paying an order, until a call can send the customer's address
  in x-forwarded-for, which Clover requires on those three. Recurring plans live at another
  host, and a vendor has one address.
- Braintree: anything that takes card details or hands out a client token.
- Plaid: /auth/get and /liabilities/get (they answer with account numbers), Link and token
  exchange, and every transfer, payment and processor endpoint.
- BILL: bank accounts of any kind (and the vendor fields that hold them), users, approval
  policies, bulk and mass payments, and approving a bill: that call's body is a list, and
  a call here sends an object.

Authorize.net isn't here: its one endpoint takes the keys and the action inside the body, so
the action can't be read from a table. Melio's developer docs couldn't be reached.
"""

from collections.abc import Callable
from typing import cast
from uuid import uuid4

from ringkeeper.adapters.vendor_spec import (
    JSON,
    READ,
    Auth,
    Exchange,
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

CHARGES = "charges a customer"
REFUNDS = "gives money back"
SUBSCRIPTION = "changes what a customer pays from now on"
DISPUTE = "answers a dispute"


def _about(*account: str) -> Money:
    """A call that names a record and no amount: the card shows which record."""
    return Money((), (), (), account)


def _env_by_host(live: str) -> Callable[[JSON], Env | None]:
    def env_of(fields: JSON) -> Env | None:
        host = str(fields.get("host") or "")
        if not host:
            return None
        return Env.PROD if host == live else Env.DEV

    return env_of


# --- Square ---

SQUARE_VERSION = "2026-09-16"  # the Square-Version in Square's API spec, and its changelog
SQUARE_HOST = r"^connect\.squareup(sandbox)?\.com$"
SQUARE_CHARGE = Money(("amount_money.amount",), ("amount_money.currency",), ("customer_id",),
                      ("source_id",), minor_units=True)  # fmt: skip
SQUARE_REFUND = Money(("amount_money.amount",), ("amount_money.currency",), ("customer_id",),
                      ("payment_id",), minor_units=True)  # fmt: skip
SQUARE_LINK = Money(("quick_pay.price_money.amount",), ("quick_pay.price_money.currency",),
                    ("pre_populated_data.buyer_email",), ("quick_pay.location_id",),
                    minor_units=True)  # fmt: skip
SQUARE_INVOICE = Money((), (), ("invoice.primary_recipient.customer_id",), ("invoice.location_id",))
SQUARE_SUBSCRIBE = Money(("price_override_money.amount",), ("price_override_money.currency",),
                         ("customer_id",), ("card_id",), minor_units=True)  # fmt: skip
SQUARE_SUBSCRIPTION = Money(("subscription.price_override_money.amount",),
                            ("subscription.price_override_money.currency",), (),
                            ("subscription_id",), minor_units=True)  # fmt: skip
SQUARE_CUSTOMER = (
    "given_name", "family_name", "company_name", "nickname", "email_address", "address",
    "phone_number", "reference_id", "note", "birthday", "tax_ids",
)  # fmt: skip
SQUARE_CUSTOMER_BACK = Snapshot("get_customer", SQUARE_CUSTOMER, where="customer",
                                says="the customer's details")  # fmt: skip


def _square_who(found: JSON) -> str:
    merchants = cast(list[JSON], found.get("merchant") or [{}])
    return str(merchants[0].get("business_name") or merchants[0].get("id"))


SQUARE = Vendor(
    "square", "Square", "Payments", "Payments, invoices, customers, subscriptions", "https://{host}",
    auth=Auth("bearer"),
    secrets=(
        Secret("host", "Environment", "connect.squareup.com for real data, or "
               "connect.squareupsandbox.com for the sandbox", hidden=False, pattern=SQUARE_HOST),
        Secret("api_key", "Access token", "From Credentials in the Square Developer Console. "
               "Production and sandbox have different tokens."),
    ),
    headers={"Square-Version": SQUARE_VERSION},
    probe="list_merchants", who=_square_who, env_of=_env_by_host("connect.squareup.com"),
    env_vars={"SQUARE_ACCESS_TOKEN": "api_key", "SQUARE_TOKEN": "api_key"},
    words=("square", "payments", "point of sale", "pos", "invoices", "card reader"),
    key_url="https://developer.squareup.com/apps",
    steps="In the Square Developer Console, open your application, choose Credentials, pick "
    "Production or Sandbox at the top, and copy the access token. Then type that environment's "
    "address. Each has its own token, so add one connection per environment.",
    note="One connection is one Square account. Reading runs. Customers, orders, draft invoices "
    "and payment links wait for approval. Sending an invoice, charges, refunds, captures, voids, "
    "subscription changes and dispute answers need the name typed.",
    ops=(
        reads("list_merchants", "/v2/merchants", "This Square account"),
        reads("list_locations", "/v2/locations", "The account's locations"),
        reads("list_payments", "/v2/payments", "Find payments", "begin_time", "end_time",
              "location_id", "total", "last_4", "card_brand", "limit", "cursor"),
        reads("get_payment", "/v2/payments/{payment_id}", "Read one payment"),
        reads("list_refunds", "/v2/refunds", "Find refunds", "begin_time", "end_time",
              "location_id", "status", "limit", "cursor"),
        reads("list_payouts", "/v2/payouts", "Payouts to the bank", "location_id", "status",
              "begin_time", "end_time", "limit", "cursor"),
        reads("list_payout_entries", "/v2/payouts/{payout_id}/payout-entries",
              "What's in a payout", "limit", "cursor"),
        reads("search_customers", "/v2/customers/search", "Search customers", "query", "limit",
              "cursor", method="POST"),
        reads("get_customer", "/v2/customers/{customer_id}", "Read one customer"),
        reads("search_orders", "/v2/orders/search", "Search orders", "location_ids", "query",
              "limit", "cursor", method="POST"),
        reads("get_order", "/v2/orders/{order_id}", "Read one order"),
        reads("list_invoices", "/v2/invoices", "Invoices at a location", "location_id", "limit",
              "cursor"),
        reads("get_invoice", "/v2/invoices/{invoice_id}", "Read one invoice"),
        reads("list_payment_links", "/v2/online-checkout/payment-links", "List payment links",
              "limit", "cursor"),
        reads("list_catalog", "/v2/catalog/list", "Items and prices", "types", "cursor"),
        reads("search_subscriptions", "/v2/subscriptions/search", "Search subscriptions", "query",
              "limit", "cursor", method="POST"),
        reads("get_subscription", "/v2/subscriptions/{subscription_id}",
              "Read one subscription"),
        reads("list_disputes", "/v2/disputes", "List disputes", "states", "location_id", "cursor"),
        reads("get_dispute", "/v2/disputes/{dispute_id}", "Read one dispute"),

        # Held.
        held("create_customer", "POST", "/v2/customers", "Create a customer", *SQUARE_CUSTOMER,
             only=("idempotency_key", *SQUARE_CUSTOMER)),
        held("update_customer", "PUT", "/v2/customers/{customer_id}", "Change a customer",
             *SQUARE_CUSTOMER, only=("version", *SQUARE_CUSTOMER), undo=SQUARE_CUSTOMER_BACK),
        held("create_order", "POST", "/v2/orders", "Create an order", "order", "idempotency_key",
             only=("order", "idempotency_key")),
        held("create_invoice", "POST", "/v2/invoices", "Create a draft invoice", "invoice",
             "idempotency_key", only=("invoice", "idempotency_key"), money=SQUARE_INVOICE),
        held("update_invoice", "PUT", "/v2/invoices/{invoice_id}", "Change an invoice", "invoice",
             "fields_to_clear", only=("invoice", "idempotency_key", "fields_to_clear"),
             money=SQUARE_INVOICE),
        held("create_payment_link", "POST", "/v2/online-checkout/payment-links",
             "Create a payment link", "quick_pay", "order", "description", "checkout_options",
             only=("idempotency_key", "description", "quick_pay", "order", "checkout_options",
                   "pre_populated_data", "payment_note"), money=SQUARE_LINK),

        # The name typed.
        final("create_payment", "POST", "/v2/payments", "Charge a payment source", CHARGES,
              "source_id", "idempotency_key", "amount_money", "customer_id", "location_id",
              "order_id", "autocomplete", "note",
              only=("source_id", "idempotency_key", "amount_money", "tip_money", "delay_duration",
                    "delay_action", "autocomplete", "order_id", "customer_id", "location_id",
                    "team_member_id", "reference_id", "verification_token",
                    "accept_partial_authorization", "buyer_email_address", "billing_address",
                    "shipping_address", "note", "statement_description_identifier"),
              money=SQUARE_CHARGE),
        final("complete_payment", "POST", "/v2/payments/{payment_id}/complete",
              "Capture an authorized payment", "takes payment that was only authorized",
              "version_token", only=("version_token",), money=_about("payment_id")),
        final("cancel_payment", "POST", "/v2/payments/{payment_id}/cancel",
              "Void an authorized payment", "voids a payment", money=_about("payment_id")),
        final("refund_payment", "POST", "/v2/refunds", "Refund a payment", REFUNDS,
              "payment_id", "idempotency_key", "amount_money", "reason",
              only=("idempotency_key", "amount_money", "payment_id", "reason", "location_id",
                    "customer_id", "team_member_id", "payment_version_token"),
              money=SQUARE_REFUND),
        final("publish_invoice", "POST", "/v2/invoices/{invoice_id}/publish",
              "Send an invoice", "sends an invoice, and can charge a saved card", "version",
              "idempotency_key", only=("version", "idempotency_key"), money=_about("invoice_id")),
        final("cancel_invoice", "POST", "/v2/invoices/{invoice_id}/cancel", "Cancel a sent invoice",
              "cancels an invoice a customer has", "version", only=("version",),
              money=_about("invoice_id")),
        final("delete_invoice", "DELETE", "/v2/invoices/{invoice_id}", "Delete a draft invoice",
              "deletes an invoice", "version"),
        final("delete_payment_link", "DELETE", "/v2/online-checkout/payment-links/{id}",
              "Delete a payment link", "takes a payment link down"),
        final("delete_customer", "DELETE", "/v2/customers/{customer_id}", "Delete a customer",
              "deletes a customer", "version"),
        final("create_subscription", "POST", "/v2/subscriptions", "Start a subscription",
              SUBSCRIPTION, "location_id", "plan_variation_id", "customer_id", "card_id",
              "start_date", "idempotency_key",
              only=("idempotency_key", "location_id", "plan_variation_id", "customer_id",
                    "start_date", "canceled_date", "tax_percentage", "price_override_money",
                    "card_id", "timezone", "source", "monthly_billing_anchor_date", "phases"),
              money=SQUARE_SUBSCRIBE),
        final("update_subscription", "PUT", "/v2/subscriptions/{subscription_id}",
              "Change a subscription", SUBSCRIPTION, "subscription", only=("subscription",),
              money=SQUARE_SUBSCRIPTION),
        final("cancel_subscription", "POST", "/v2/subscriptions/{subscription_id}/cancel",
              "Cancel a subscription", SUBSCRIPTION, money=_about("subscription_id")),
        final("resume_subscription", "POST", "/v2/subscriptions/{subscription_id}/resume",
              "Resume a subscription", SUBSCRIPTION, "resume_effective_date",
              "resume_change_timing", only=("resume_effective_date", "resume_change_timing"),
              money=_about("subscription_id")),
        final("accept_dispute", "POST", "/v2/disputes/{dispute_id}/accept",
              "Accept a dispute and give up the money", DISPUTE, money=_about("dispute_id")),
        final("add_dispute_evidence_text", "POST", "/v2/disputes/{dispute_id}/evidence-text",
              "Add text evidence to a dispute", DISPUTE, "evidence_text", "evidence_type",
              "idempotency_key", only=("idempotency_key", "evidence_type", "evidence_text")),
        final("submit_dispute_evidence", "POST", "/v2/disputes/{dispute_id}/submit-evidence",
              "Submit a dispute's evidence", DISPUTE, money=_about("dispute_id")),
    ),
)  # fmt: skip


# --- Helcim ---

HELCIM_CUSTOMER = ("customerCode", "contactName", "businessName", "cellPhone", "billingAddress",
                   "shippingAddress")  # fmt: skip
HELCIM_CUSTOMER_BACK = Snapshot("get_customer", HELCIM_CUSTOMER, says="the customer's details")
HELCIM_INVOICE = ("currency", "lineItems", "customerId", "billingAddress", "shipping", "pickup",
                  "tax", "discount", "type", "status", "convenienceFeeEnabled", "tipAmount",
                  "invoiceNumber", "notes")  # fmt: skip
HELCIM_PAYMENT = ("ipAddress", "ecommerce", "terminalId", "currency", "amount", "customerCode",
                  "invoiceNumber", "cardData", "billingAddress", "invoice")  # fmt: skip
HELCIM_CHARGE = Money(("amount",), ("currency",), ("customerCode",), ("cardData.cardToken",))
HELCIM_INVOICE_CARD = Money((), ("currency",), ("customerId",), ("invoiceNumber",))
HELCIM_SUBSCRIBE = Money(
    ("subscriptions.0.recurringAmount",), (), ("subscriptions.0.customerCode",),
    ("subscriptions.0.paymentPlanId",),
)  # fmt: skip


def _helcim_headers(fields: JSON) -> dict[str, str]:
    """Helcim's payment calls each need their own idempotency key. Other calls ignore it."""
    return {"api-token": str(fields["api_key"]), "idempotency-key": str(uuid4())}


HELCIM = Vendor(
    "helcim", "Helcim", "Payments", "Card and ACH payments, invoices, customers",
    "https://api.helcim.com/v2",
    auth=Auth("custom", custom=_helcim_headers),
    secrets=(Secret("api_key", "API token", "From API Access Configurations in Helcim"),),
    probe="test_connection",
    words=("helcim", "payments", "ach", "invoices", "cards"),
    key_url="https://devdocs.helcim.com/docs/authentication-with-the-helcim-api-and-helcimpayjs",
    steps="In Helcim, open All Tools, then Integrations, then API Access Configurations. Choose "
    "New API Access, set its Access Restrictions, and create it. Helcim's access levels narrow "
    "what the token can do: refunds need the Admin level. A developer test account has its own "
    "token, so add one connection per account.",
    note="One connection is one Helcim account. Reading runs. Customers and invoices wait for "
    "approval. Charges, captures, refunds, reversals, subscriptions and ACH payments need the "
    "name typed. Charges use a saved card token: a card number is refused.",
    ops=(
        reads("test_connection", "/connection-test", "Check the token works"),
        reads("list_card_transactions", "/card-transactions", "Find card transactions", "dateFrom",
              "dateTo", "search", "customerCode", "invoiceNumber", "cardBatchId", "limit", "page"),
        reads("get_card_transaction", "/card-transactions/{transaction_id}",
              "Read one card transaction"),
        reads("list_card_batches", "/card-batches", "Card batches", "batchNumber", "terminalId",
              "collect-stats"),
        reads("list_ach_transactions", "/ach/transactions", "Find ACH transactions", "startDate",
              "endDate", "amountMin", "amountMax", "search", "limit", "page"),
        reads("get_ach_transaction", "/ach/transactions/{transaction_id}",
              "Read one ACH transaction"),
        reads("list_customers", "/customers", "Find customers", "search", "customerCode", "limit",
              "page"),
        reads("get_customer", "/customers/{customer_id}", "Read one customer"),
        reads("list_customer_cards", "/customers/{customer_id}/cards",
              "A customer's saved cards, as tokens"),
        reads("list_invoices", "/invoices", "Find invoices", "dateStart", "dateEnd",
              "invoiceNumber", "customerId", "status", "type", "currency", "page"),
        reads("get_invoice", "/invoices/{invoice_id}", "Read one invoice"),
        reads("list_payment_plans", "/payment-plans", "Recurring plans", "status", "name", "limit",
              "offset"),
        reads("list_subscriptions", "/subscriptions", "Subscribers", "status", "paymentPlanId",
              "customerCode", "limit", "offset"),
        reads("get_subscription", "/subscriptions/{subscription_id}", "Read one subscriber"),

        # Held.
        held("create_customer", "POST", "/customers", "Create a customer", *HELCIM_CUSTOMER,
             only=HELCIM_CUSTOMER),
        held("update_customer", "PUT", "/customers/{customer_id}", "Change a customer",
             *HELCIM_CUSTOMER, only=HELCIM_CUSTOMER, undo=HELCIM_CUSTOMER_BACK),
        held("create_invoice", "POST", "/invoices", "Create an invoice", "currency", "lineItems",
             "customerId", "status", "notes", only=HELCIM_INVOICE, money=HELCIM_INVOICE_CARD),
        held("update_invoice", "PUT", "/invoices/{invoice_id}", "Change an invoice", "currency",
             "lineItems", "status", "notes", only=HELCIM_INVOICE, money=HELCIM_INVOICE_CARD),

        # The name typed.
        final("purchase", "POST", "/payment/purchase", "Charge a saved card", CHARGES, "ipAddress",
              "currency", "amount", "customerCode", "invoiceNumber", "cardData",
              only=HELCIM_PAYMENT, money=HELCIM_CHARGE),
        final("preauth", "POST", "/payment/preauth", "Hold money on a saved card",
              "holds money on a customer's card", "ipAddress", "currency", "amount", "customerCode",
              "invoiceNumber", "cardData", only=HELCIM_PAYMENT, money=HELCIM_CHARGE),
        final("capture", "POST", "/payment/capture", "Capture a held amount",
              "takes payment that was only held", "preAuthTransactionId", "amount", "ipAddress",
              only=("preAuthTransactionId", "amount", "ipAddress", "ecommerce"),
              money=Money(("amount",), (), (), ("preAuthTransactionId",))),
        final("refund", "POST", "/payment/refund", "Refund a card transaction", REFUNDS,
              "originalTransactionId", "amount", "ipAddress",
              only=("originalTransactionId", "amount", "ipAddress", "ecommerce"),
              money=Money(("amount",), (), (), ("originalTransactionId",))),
        final("reverse", "POST", "/payment/reverse", "Void a card transaction",
              "voids a card transaction", "cardTransactionId", "ipAddress",
              only=("cardTransactionId", "ipAddress", "ecommerce"),
              money=_about("cardTransactionId")),
        final("ach_withdraw", "PUT", "/ach/withdraw", "Take a payment from a saved bank account",
              CHARGES, "bankAccountId", "customerId", "amount", "currencyId", "orderId",
              only=("bankAccountId", "customerId", "orderId", "amount", "currencyId"),
              money=Money(("amount",), ("currencyId",), ("customerId",), ("bankAccountId",))),
        final("ach_refund", "PUT", "/ach/transactions/{transaction_id}/refund",
              "Refund an ACH transaction", REFUNDS, "amount", only=("amount",),
              money=Money(("amount",), (), (), ("transaction_id",))),
        final("ach_void", "PUT", "/ach/transactions/{transaction_id}/void",
              "Void an ACH transaction", "voids an ACH transaction",
              money=_about("transaction_id")),
        final("ach_cancel", "PATCH", "/ach/transactions/{transaction_id}/cancel",
              "Cancel a pending ACH transaction", "cancels an ACH payment",
              money=_about("transaction_id")),
        final("create_subscriptions", "POST", "/subscriptions", "Subscribe customers to a plan",
              SUBSCRIPTION, "subscriptions", only=("subscriptions",), money=HELCIM_SUBSCRIBE),
        final("update_subscriptions", "PATCH", "/subscriptions", "Change subscribers", SUBSCRIPTION,
              "subscriptions", only=("subscriptions",),
              money=Money(("subscriptions.0.recurringAmount",), (), (), ("subscriptions.0.id",))),
        final("delete_subscription", "DELETE", "/subscriptions/{subscription_id}",
              "End a subscription", SUBSCRIPTION, money=_about("subscription_id")),
        final("charge_subscription_payment", "POST", "/procedures/process-payment",
              "Charge a subscriber's outstanding payment", CHARGES, "subscriptionId",
              "paymentNumber", only=("subscriptionId", "paymentNumber"),
              money=_about("subscriptionId")),
        final("delete_customer_card", "DELETE", "/customers/{customer_id}/cards/{card_id}",
              "Remove a customer's saved card", "removes a saved card"),
    ),
)  # fmt: skip


# --- Lemon Squeezy ---

JSON_API = "application/vnd.api+json"
LEMON_REFUND = ("data.attributes.amount",)


def _lemon_who(found: JSON) -> str:
    user = cast(JSON, found.get("data") or {})
    name = cast(JSON, user.get("attributes") or {}).get("name") or user.get("id")
    test = bool(cast(JSON, found.get("meta") or {}).get("test_mode"))
    return f"{name}, in {'test' if test else 'live'} mode"


LEMON_SQUEEZY = Vendor(
    "lemonsqueezy", "Lemon Squeezy", "Payments", "Orders, subscriptions, checkouts, refunds",
    "https://api.lemonsqueezy.com/v1",
    auth=Auth("bearer"),
    secrets=(Secret("api_key", "API key", "From Settings, then API, in Lemon Squeezy"),),
    headers={"Accept": JSON_API, "Content-Type": JSON_API},
    probe="get_user", who=_lemon_who,
    env_vars={"LEMONSQUEEZY_API_KEY": "api_key", "LEMON_SQUEEZY_API_KEY": "api_key"},
    words=("lemon squeezy", "lemonsqueezy", "payments", "subscriptions", "digital products"),
    key_url="https://app.lemonsqueezy.com/settings/api",
    steps="In Lemon Squeezy, open Settings, then API, and create a key. A key made in test mode "
    "reaches test data only, and keys last a year. The Test button says which mode a key is in.",
    note="One connection is one Lemon Squeezy account. Reading runs. Customers, discounts and "
    "checkouts wait for approval. Refunds and subscription changes need the name typed. A "
    "customer or subscription record carries a signed link to that customer's billing page, "
    "valid for a day.",
    ops=(
        reads("get_user", "/users/me", "The signed-in user, and test or live mode"),
        reads("list_stores", "/stores", "List stores"),
        reads("get_store", "/stores/{store_id}", "Read one store"),
        reads("list_customers", "/customers", "Find customers", "filter[store_id]", "filter[email]",
              "page[number]", "page[size]"),
        reads("get_customer", "/customers/{customer_id}", "Read one customer"),
        reads("list_products", "/products", "List products", "filter[store_id]", "page[number]"),
        reads("get_product", "/products/{product_id}", "Read one product"),
        reads("list_variants", "/variants", "List product variants", "filter[product_id]",
              "filter[status]", "page[number]"),
        reads("get_variant", "/variants/{variant_id}", "Read one variant"),
        reads("list_orders", "/orders", "Find orders", "filter[store_id]", "filter[user_email]",
              "filter[order_number]", "page[number]", "page[size]"),
        reads("get_order", "/orders/{order_id}", "Read one order"),
        reads("list_order_items", "/order-items", "Lines of orders", "filter[order_id]",
              "filter[product_id]", "filter[variant_id]", "page[number]"),
        reads("list_subscriptions", "/subscriptions", "Find subscriptions", "filter[store_id]",
              "filter[order_id]", "filter[product_id]", "filter[user_email]", "filter[status]",
              "page[number]"),
        reads("get_subscription", "/subscriptions/{subscription_id}", "Read one subscription"),
        reads("list_subscription_invoices", "/subscription-invoices", "Subscription invoices",
              "filter[store_id]", "filter[status]", "filter[subscription_id]", "page[number]"),
        reads("get_subscription_invoice", "/subscription-invoices/{invoice_id}",
              "Read one subscription invoice"),
        reads("list_discounts", "/discounts", "List discounts", "filter[store_id]", "page[number]"),
        reads("get_discount", "/discounts/{discount_id}", "Read one discount"),
        reads("list_checkouts", "/checkouts", "List checkouts", "filter[store_id]",
              "filter[variant_id]", "page[number]"),
        reads("get_checkout", "/checkouts/{checkout_id}", "Read one checkout"),

        # Held.
        held("create_customer", "POST", "/customers", "Create a customer", "data", only=("data",)),
        held("update_customer", "PATCH", "/customers/{customer_id}", "Change a customer", "data",
             only=("data",)),
        held("create_checkout", "POST", "/checkouts", "Create a checkout link", "data",
             only=("data",),
             money=Money(("data.attributes.custom_price",), (),
                         ("data.attributes.checkout_data.email",),
                         ("data.relationships.variant.data.id",), minor_units=True)),
        held("create_discount", "POST", "/discounts", "Create a discount code", "data",
             only=("data",)),

        # The name typed.
        final("refund_order", "POST", "/orders/{order_id}/refund", "Refund an order", REFUNDS,
              "data", only=("data",),
              money=Money(LEMON_REFUND, (), (), ("order_id",), minor_units=True)),
        final("refund_subscription_invoice", "POST", "/subscription-invoices/{invoice_id}/refund",
              "Refund a subscription invoice", REFUNDS, "data", only=("data",),
              money=Money(LEMON_REFUND, (), (), ("invoice_id",), minor_units=True)),
        final("update_subscription", "PATCH", "/subscriptions/{subscription_id}",
              "Change a subscription: its plan, pause or billing date", SUBSCRIPTION, "data",
              only=("data",), money=_about("subscription_id")),
        final("cancel_subscription", "DELETE", "/subscriptions/{subscription_id}",
              "Cancel a subscription", SUBSCRIPTION, money=_about("subscription_id")),
        final("update_subscription_item", "PATCH", "/subscription-items/{item_id}",
              "Change how many of a subscription a customer has", SUBSCRIPTION, "data",
              only=("data",), money=_about("item_id")),
        final("delete_discount", "DELETE", "/discounts/{discount_id}", "Delete a discount code",
              "deletes a discount code"),
    ),
)  # fmt: skip


# --- Clover ---

CLOVER_HOST = r"^scl(-sandbox\.dev)?\.clover\.com$"
CLOVER_CUSTOMER = ("ecomind", "email", "firstName", "lastName", "name", "source", "phone",
                   "shipping")  # fmt: skip

CLOVER = Vendor(
    "clover", "Clover", "Payments", "Online orders, refunds, card-on-file customers", "https://{host}",
    auth=Auth("bearer"),
    secrets=(
        Secret("host", "Environment", "scl.clover.com for real data, or "
               "scl-sandbox.dev.clover.com for the sandbox", hidden=False, pattern=CLOVER_HOST),
        Secret("api_key", "Private token", "From Ecommerce API Tokens in the Clover dashboard"),
    ),
    headers={"Accept": "application/json", "User-Agent": "Ringkeeper"},
    probe="list_charges", env_of=_env_by_host("scl.clover.com"),
    words=("clover", "payments", "ecommerce", "refunds", "orders"),
    key_url="https://docs.clover.com/dev/docs/create-ecommerce-api-tokens",
    steps="In the Clover Merchant Dashboard, open Settings, View all settings, then Ecommerce API "
    "Tokens. Create a token for an API integration and copy the private token. It belongs to one "
    "merchant. Clover's Ecommerce API serves the US and Canada.",
    note="One connection is one merchant, through Clover's Ecommerce API. Reading runs. Orders "
    "and customers wait for approval. Refunds and returns need the name typed. New charges "
    "aren't available here yet.",
    ops=(
        reads("list_charges", "/v1/charges", "Find charges", "created", "customer", "limit",
              "starting_after", "ending_before"),
        reads("get_charge", "/v1/charges/{charge_id}", "Read one charge"),
        reads("list_refunds", "/v1/refunds", "Find refunds", "charge", "created", "limit",
              "starting_after", "ending_before"),
        reads("get_refund", "/v1/refunds/{refund_id}", "Read one refund"),
        reads("list_orders", "/v1/orders", "Find orders", "created", "customer", "ids", "status",
              "limit", "starting_after", "ending_before"),
        reads("get_order", "/v1/orders/{order_id}", "Read one order", "expand"),

        # Held.
        held("create_order", "POST", "/v1/orders", "Create an order", "currency", "email",
             "customer", "items", "shipping",
             only=("currency", "customer", "email", "expand", "items", "metadata", "shipping"),
             money=Money((), ("currency",), ("email", "customer"), ())),
        held("create_customer", "POST", "/v1/customers", "Save a customer with a card token",
             "email", "source", "firstName", "lastName", "phone", only=CLOVER_CUSTOMER),
        held("update_customer", "PUT", "/v1/customers/{customer_id}", "Change a saved customer",
             "email", "source", "firstName", "lastName", "phone", only=CLOVER_CUSTOMER),

        # The name typed.
        final("refund_charge", "POST", "/v1/refunds", "Refund a charge", REFUNDS, "charge",
              "amount", "reason", "external_reference_id",
              only=("charge", "amount", "external_reference_id", "reason"),
              money=Money(("amount",), (), (), ("charge",), minor_units=True)),
        final("return_order", "POST", "/v1/orders/{order_id}/returns", "Return an order", REFUNDS,
              "items", "refund_amounts", only=("expand", "items", "refund_amounts"),
              money=_about("order_id")),
        final("revoke_payment_source", "DELETE", "/v1/customers/{customer_id}/sources/{source_id}",
              "Remove a customer's saved card", "removes a saved payment source"),
    ),
)  # fmt: skip


# --- Braintree ---

BRAINTREE_VERSION = "2026-11-05"  # any date works: Braintree asks for the day you integrate
BRAINTREE_HOST = r"^payments(\.sandbox)?\.braintree-api\.com$"
BT_AMOUNT = "amount { value currencyCode }"
BT_TRANSACTION = f"id status orderId createdAt {BT_AMOUNT} customer {{ id email }}"
BT_CUSTOMER = "id email firstName lastName company createdAt defaultPaymentMethod { id usage }"
BT_CUSTOMER_ANSWER = f"customer {{ {BT_CUSTOMER} }}"
BT_REFUND = f"id status orderId createdAt {BT_AMOUNT} refundedTransaction {{ id }}"
BT_DISPUTE = "id status type replyByDate amountDisputed { value currencyCode } transaction { id }"
BT_PAGE = "pageInfo { hasNextPage endCursor }"
BT_CHARGE = Money(("input.transaction.amount",), (), ("input.transaction.customerId",),
                  ("input.paymentMethodId",))  # fmt: skip


def _bt_search(root: str, kind: str, fields: str) -> str:
    return (
        f"query Search($input: {kind}!, $first: Int, $after: String) {{ "
        f"{root}(input: $input, first: $first, after: $after) {{ "
        f"edges {{ node {{ {fields} }} }} {BT_PAGE} }} }}"
    )


def _bt_change(name: str, kind: str, answer: str) -> str:
    return f"mutation Change($input: {kind}!) {{ {name}(input: $input) {{ {answer} }} }}"


BRAINTREE = Vendor(
    "braintree", "Braintree", "Payments", "Transactions, refunds, customers, disputes",
    "https://{host}",
    auth=Auth("basic", field="public_key", field2="private_key"),
    secrets=(
        Secret("host", "Environment", "payments.braintree-api.com for real data, or "
               "payments.sandbox.braintree-api.com for the sandbox", hidden=False,
               pattern=BRAINTREE_HOST),
        Secret("public_key", "Public key", "From API keys in the Braintree Control Panel",
               hidden=False),
        Secret("private_key", "Private key", "From API keys in the Braintree Control Panel"),
    ),
    headers={"Braintree-Version": BRAINTREE_VERSION},
    graphql_path="/graphql",
    probe="ping", env_of=_env_by_host("payments.braintree-api.com"),
    words=("braintree", "paypal", "payments", "transactions", "disputes"),
    key_url="https://developer.paypal.com/braintree/articles/control-panel/important-gateway-credentials",
    steps="In the Braintree Control Panel, open your API settings and copy a public and private "
    "key. Sandbox and production have different keys, so add one connection per environment.",
    note="One connection is one Braintree gateway. Reading runs. Customers wait for approval. "
    "Charges, captures, refunds, voids and dispute answers need the name typed.",
    ops=(
        query("ping", "Check the keys work", "query Ping { ping }"),
        query("search_transactions", "Find transactions",
              _bt_search("transactions", "TransactionSearchInput", BT_TRANSACTION), "input",
              "first", "after"),
        query("search_refunds", "Find refunds",
              _bt_search("refunds", "RefundSearchInput", BT_REFUND), "input", "first", "after"),
        query("search_customers", "Find customers",
              _bt_search("customers", "CustomerSearchInput", BT_CUSTOMER), "input", "first",
              "after"),
        query("search_disputes", "Find disputes",
              _bt_search("disputes", "DisputeSearchInput", BT_DISPUTE), "input", "first", "after"),

        # Held.
        mutation("create_customer", "Create a customer",
                 _bt_change("createCustomer", "CreateCustomerInput", BT_CUSTOMER_ANSWER),
                 "input"),
        mutation("update_customer", "Change a customer",
                 _bt_change("updateCustomer", "UpdateCustomerInput", BT_CUSTOMER_ANSWER),
                 "input"),

        # The name typed.
        mutation("charge_payment_method", "Charge a saved payment method",
                 _bt_change("chargePaymentMethod", "ChargePaymentMethodInput",
                            f"transaction {{ {BT_TRANSACTION} }}"),
                 "input", why=CHARGES, money=BT_CHARGE),
        mutation("authorize_payment_method", "Hold money on a saved payment method",
                 _bt_change("authorizePaymentMethod", "AuthorizePaymentMethodInput",
                            f"transaction {{ {BT_TRANSACTION} }}"),
                 "input", why="holds money on a customer's card", money=BT_CHARGE),
        mutation("capture_transaction", "Capture an authorized transaction",
                 _bt_change("captureTransaction", "CaptureTransactionInput",
                            f"transaction {{ {BT_TRANSACTION} }}"),
                 "input", why="takes payment that was only authorized",
                 money=Money(("input.transaction.amount",), (), (), ("input.transactionId",))),
        mutation("refund_transaction", "Refund a settled transaction",
                 _bt_change("refundTransaction", "RefundTransactionInput",
                            f"refund {{ {BT_REFUND} }}"),
                 "input", why=REFUNDS,
                 money=Money(("input.refund.amount",), (), (), ("input.transactionId",))),
        mutation("reverse_transaction", "Void or refund a whole transaction",
                 _bt_change("reverseTransaction", "ReverseTransactionInput",
                            "reversal { ... on Refund { id status } "
                            "... on Transaction { id status } }"),
                 "input", why=REFUNDS, money=_about("input.transactionId")),
        mutation("void_transaction", "Void an unsettled transaction",
                 _bt_change("voidTransaction", "VoidTransactionInput",
                            f"transaction {{ {BT_TRANSACTION} }}"),
                 "input", why="voids a transaction", money=_about("input.transactionId")),
        mutation("delete_customer", "Delete a customer",
                 _bt_change("deleteCustomer", "DeleteCustomerInput", "clientMutationId"), "input",
                 why="deletes a customer"),
        mutation("delete_payment_method", "Remove a saved payment method",
                 _bt_change("deletePaymentMethodFromVault", "DeletePaymentMethodFromVaultInput",
                            "clientMutationId"),
                 "input", why="removes a saved payment method"),
        mutation("accept_dispute", "Accept a dispute and give up the money",
                 _bt_change("acceptDispute", "AcceptDisputeInput", "dispute { id status }"),
                 "input", why=DISPUTE, money=_about("input.disputeId")),
        mutation("add_dispute_evidence_text", "Add text evidence to a dispute",
                 _bt_change("createDisputeTextEvidence", "CreateDisputeTextEvidenceInput",
                            "evidence { id category }"),
                 "input", why=DISPUTE),
        mutation("delete_dispute_evidence", "Remove evidence from a dispute",
                 _bt_change("deleteDisputeEvidence", "DeleteDisputeEvidenceInput",
                            "clientMutationId"),
                 "input", why=DISPUTE),
        mutation("finalize_dispute", "Submit a dispute's evidence",
                 _bt_change("finalizeDispute", "FinalizeDisputeInput", "dispute { id status }"),
                 "input", why=DISPUTE, money=_about("input.disputeId")),
    ),
)  # fmt: skip


# --- Plaid ---
# Written and checked, but not in VENDORS: it needs two things the core doesn't do yet. Every
# Plaid read is a POST ending in /get, which a table can't call a read; and each one needs the
# Item's access_token in the body, which must come from the connection, never from the AI.

PLAID_VERSION = "2020-09-14"
PLAID_HOST = r"^(sandbox|production)\.plaid\.com$"


def _bank_read(name: str, path: str, says: str, *takes: str) -> Op:
    """A read of the linked bank: its token comes from the connection."""
    return Op(name, "POST", path, says, READ, params=takes, only=takes, inject=("access_token",))


def _public_read(name: str, path: str, says: str, *takes: str) -> Op:
    return Op(name, "POST", path, says, READ, params=takes, only=takes)


def _plaid_headers(fields: JSON) -> dict[str, str]:
    return {"PLAID-CLIENT-ID": str(fields["client_id"]), "PLAID-SECRET": str(fields["secret"])}


class _PostReads(Vendor):
    """Plaid sends every read by POST, and has no writes in its table."""

    @staticmethod
    def _reads(op: Op) -> bool:
        return op.method == "POST"


PLAID = _PostReads(
    "plaid", "Plaid", "Payments", "Read-only bank accounts, balances, transactions",
    "https://{host}",
    auth=Auth("custom", custom=_plaid_headers),
    secrets=(
        Secret("host", "Environment", "production.plaid.com for real data, or sandbox.plaid.com "
               "for the sandbox", hidden=False, pattern=PLAID_HOST),
        Secret("client_id", "Client ID", "From Keys in the Plaid Dashboard", hidden=False),
        Secret("secret", "Secret", "From Keys in the Plaid Dashboard: each environment has one"),
        Secret("access_token", "Access token", "The token for one linked bank: your Plaid "
               "integration has it. Ringkeeper doesn't link banks."),
    ),
    headers={"Plaid-Version": PLAID_VERSION},
    probe="get_item", env_of=_env_by_host("production.plaid.com"),
    env_vars={"PLAID_CLIENT_ID": "client_id", "PLAID_SECRET": "secret"},
    words=("plaid", "bank", "banking", "transactions", "balances"),
    key_url="https://dashboard.plaid.com/developers/keys",
    steps="In the Plaid Dashboard, open Developers, then Keys, and copy the client ID and the "
    "secret for your environment. Then paste the access token your integration holds for one "
    "linked bank. One connection is one linked bank.",
    note="Read-only: nothing here can move money or change anything. Full account and routing "
    "numbers are left out.",
    ops=(
        _bank_read("get_item", "/item/get", "Which bank this is, and its status"),
        _bank_read("get_accounts", "/accounts/get", "The bank's accounts", "options"),
        _bank_read("get_balances", "/accounts/balance/get", "Live balances", "options"),
        _bank_read("get_transactions", "/transactions/get", "Transactions between two dates",
                   "start_date", "end_date", "options"),
        _bank_read("sync_transactions", "/transactions/sync", "Transactions since a cursor",
                   "cursor", "count", "options"),
        _bank_read("get_recurring_transactions", "/transactions/recurring/get",
                   "Recurring payments and deposits", "account_ids", "options"),
        _bank_read("get_identity", "/identity/get", "The account owners on file", "options"),
        _bank_read("get_holdings", "/investments/holdings/get", "Investment holdings", "options"),
        _bank_read("get_investment_transactions", "/investments/transactions/get",
                   "Investment transactions between two dates", "start_date", "end_date",
                   "options"),
        _bank_read("list_statements", "/statements/list", "Bank statements on file"),
        _public_read("get_institution", "/institutions/get_by_id", "Read one bank",
                     "institution_id", "country_codes", "options"),
        _public_read("search_institutions", "/institutions/search", "Find a bank", "query",
                   "country_codes", "products", "options"),
    ),
)  # fmt: skip

# --- BILL ---

BILL_HOST = r"^gateway\.(stage|prod)\.bill\.com$"
# A session ends after 35 idle minutes, so one is never kept that long.
BILL_SESSION_SECONDS = 1800
BILL_CUSTOMER = ("name", "companyName", "contact", "email", "phone", "fax", "description",
                 "invoiceCurrency", "accountType", "paymentTermId", "billingAddress",
                 "shippingAddress")  # fmt: skip
BILL_VENDOR = ("name", "shortName", "accountType", "email", "phone", "address", "additionalInfo",
               "billCurrency")  # fmt: skip
BILL_BILL = ("vendorId", "description", "dueDate", "paymentTermId", "billLineItems", "invoice",
             "payFromChartOfAccountId", "classifications", "vendorCredits", "purchaseOrderNumber",
             "billApprovals")  # fmt: skip
BILL_INVOICE = ("invoiceNumber", "invoiceDate", "dueDate", "paymentTermId", "customer",
                "invoiceLineItems", "processingOptions", "payToChartOfAccountId",
                "classifications", "creditAmount", "salesTaxItemId", "enableCardPayment",
                "convenienceFee")  # fmt: skip
BILL_PAGE = ("max", "sort", "filters", "page")
BILL_ARCHIVES = "takes a record out of use"
BILL_BOOKS = "records a payment in the books"
BILL_CUSTOMER_BACK = Snapshot(
    "get_customer", ("name", "companyName", "contact", "email", "phone", "fax", "description",
                     "billingAddress", "shippingAddress"), says="the customer's details",
)  # fmt: skip
BILL_VENDOR_BACK = Snapshot(
    "get_vendor", ("name", "shortName", "email", "phone", "address"), says="the vendor's details",
)  # fmt: skip


def _bill_headers(fields: JSON) -> dict[str, str]:
    return {"sessionId": str(fields["session_id"]), "devKey": str(fields["dev_key"])}


def _bill_who(found: JSON) -> str:
    return f"organization {found.get('organizationId')}"


BILL = Vendor(
    "bill", "BILL", "Payments", "Bills, vendor payments, invoices, customers",
    "https://{host}/connect",
    auth=Auth("custom", custom=_bill_headers),
    exchange=Exchange(
        "https://{host}/connect/v3/login",
        {"username": "username", "password": "password", "organizationId": "organization_id",
         "devKey": "dev_key", "rememberMeId": "remember_me_id", "device": "device"},
        into="session_id", as_json=True, answer="sessionId", lifetime=BILL_SESSION_SECONDS,
    ),
    secrets=(
        Secret("host", "Environment", "gateway.prod.bill.com for real data, or "
               "gateway.stage.bill.com for the sandbox", hidden=False, pattern=BILL_HOST),
        Secret("username", "Username", "The email address you sign in to BILL with", hidden=False),
        Secret("password", "Password", "Use a BILL user made for this, with only the access it "
               "needs"),
        Secret("organization_id", "Organization ID", "Starts with 008", hidden=False,
               pattern=r"^008[A-Za-z0-9]+$"),
        Secret("dev_key", "Developer key", "Sent by BILL when you make a developer account"),
        Secret("remember_me_id", "MFA ID", "Needed to pay bills. From BILL's MFA sign-in, and it "
               "lasts 30 days", required=False),
        Secret("device", "MFA device name", "The nickname used with the MFA ID", hidden=False,
               required=False),
    ),
    probe="get_session", who=_bill_who, env_of=_env_by_host("gateway.prod.bill.com"),
    never_trusted=True,
    words=("bill", "bill.com", "billcom", "accounts payable", "bill pay", "invoices", "vendors"),
    key_url="https://developer.bill.com/docs/api-sandbox-sign-up",
    steps="Sign up for a BILL developer account, in the sandbox or in production, and generate "
    "a developer key. You'll need the username, password, organization ID and developer key. "
    "Each environment has its own, so add one connection per organization and environment.",
    note="One connection is one BILL organization, signed in as one user. Reading runs. Bills, "
    "customers, vendors, invoices and payment links wait for approval, every time. Paying a "
    "bill, charging a customer, sending an invoice, voids, cancels, recorded payments and "
    "archiving need the name typed. Bank accounts stay out of reach.",
    unverified="BILL requires MFA to pay a bill: the MFA ID comes from BILL's own MFA calls, "
    "which Ringkeeper doesn't run, and lasts 30 days. Whether BILL accepts a sign-in with that "
    "field left blank couldn't be confirmed.",
    ops=(
        reads("get_session", "/v3/login/session", "This session: its organization and user"),
        reads("list_bills", "/v3/bills", "Find bills", *BILL_PAGE),
        reads("get_bill", "/v3/bills/{bill_id}", "Read one bill"),
        reads("list_bills_awaiting_approval", "/v3/bill-approvals/pending-user-approvals",
              "Bills waiting for this user's approval"),
        reads("list_payments", "/v3/payments", "Find vendor payments", *BILL_PAGE),
        reads("get_payment", "/v3/payments/{payment_id}", "Read one vendor payment"),
        reads("list_payment_options", "/v3/payments/options", "How a vendor can be paid",
              "vendorId", "amount"),
        reads("list_vendors", "/v3/vendors", "Find vendors", *BILL_PAGE),
        reads("get_vendor", "/v3/vendors/{vendor_id}", "Read one vendor"),
        reads("list_vendor_credits", "/v3/vendor-credits", "Find vendor credits", *BILL_PAGE),
        reads("list_invoices", "/v3/invoices", "Find invoices", *BILL_PAGE),
        reads("get_invoice", "/v3/invoices/{invoice_id}", "Read one invoice"),
        reads("list_received_payments", "/v3/receivable-payments", "Find payments received",
              *BILL_PAGE),
        reads("get_received_payment", "/v3/receivable-payments/{payment_id}",
              "Read one payment received"),
        reads("list_customers", "/v3/customers", "Find customers", *BILL_PAGE),
        reads("get_customer", "/v3/customers/{customer_id}", "Read one customer"),

        # Held, and never trusted for an hour.
        held("create_bill", "POST", "/v3/bills", "Enter a bill", *BILL_BILL,
             only=(*BILL_BILL, "allowDuplicateInvoiceNumber")),
        held("update_bill", "PATCH", "/v3/bills/{bill_id}", "Change a bill", *BILL_BILL,
             only=(*BILL_BILL, "policyRematch")),
        held("create_invoice", "POST", "/v3/invoices", "Create an invoice", *BILL_INVOICE,
             only=BILL_INVOICE),
        held("update_invoice", "PATCH", "/v3/invoices/{invoice_id}", "Change an invoice",
             *BILL_INVOICE, only=(*BILL_INVOICE, "invoicePdfId")),
        held("get_invoice_payment_link", "POST", "/v3/invoices/{invoice_id}/payment-link",
             "Make an invoice's payment link", "customerId", "email", "returnUrl",
             only=("customerId", "email", "returnUrl"),
             money=Money((), (), ("email", "customerId"), ("invoice_id",))),
        held("create_customer", "POST", "/v3/customers", "Create a customer", *BILL_CUSTOMER,
             only=BILL_CUSTOMER),
        held("update_customer", "PATCH", "/v3/customers/{customer_id}", "Change a customer",
             *BILL_CUSTOMER, only=BILL_CUSTOMER, undo=BILL_CUSTOMER_BACK),
        held("create_vendor", "POST", "/v3/vendors", "Create a vendor", *BILL_VENDOR,
             only=BILL_VENDOR),
        held("update_vendor", "PATCH", "/v3/vendors/{vendor_id}", "Change a vendor", *BILL_VENDOR,
             only=BILL_VENDOR, undo=BILL_VENDOR_BACK),

        # The name typed.
        final("pay_bill", "POST", "/v3/payments", "Pay a bill", "pays a vendor", "vendorId",
              "billId", "amount", "fundingAccount", "processDate", "processingOptions",
              "description",
              only=("vendorId", "billId", "description", "processDate", "exchangeRateBatchId",
                    "fundingAccount", "amount", "processingOptions", "paymentPurpose",
                    "transactionNumber", "cardFundingPurpose", "vendorCredits"),
              money=Money(("amount",), (), ("vendorId",), ("fundingAccount.id",))),
        final("cancel_payment", "POST", "/v3/payments/{payment_id}/cancel", "Cancel a payment",
              "cancels a payment to a vendor", money=_about("payment_id")),
        final("void_payment", "POST", "/v3/payments/{payment_id}/void", "Void a payment",
              "voids a payment to a vendor", money=_about("payment_id")),
        final("record_bill_payment", "POST", "/v3/bills/record-payment",
              "Record a payment made outside BILL", BILL_BOOKS, "vendorId", "processDate",
              "toPrintCheck", "payments", "description",
              only=("vendorId", "processDate", "toPrintCheck", "classifications", "description",
                    "syncReference", "payments", "allowExport"),
              money=Money(("payments.0.amount",), (), ("vendorId",), ("payments.0.billId",))),
        final("charge_customer", "POST", "/v3/receivable-payments",
              "Charge a customer's saved bank account for invoices", CHARGES, "customerId",
              "fundingAccount", "invoicePayments", "paymentDate", "description",
              only=("customerId", "fundingAccount", "description", "paymentDate",
                    "invoicePayments"),
              money=Money(("invoicePayments.0.amount",), (), ("customerId",),
                          ("fundingAccount.id",))),
        final("record_invoice_payment", "POST", "/v3/invoices/record-payment",
              "Record a payment received outside BILL", BILL_BOOKS, "customerId", "paymentDate",
              "paymentType", "amount", "invoices", "description",
              only=("customerId", "paymentDate", "paymentType", "amount", "description",
                    "invoices"),
              money=Money(("amount",), (), ("customerId",), ("invoices.0.id",))),
        final("send_invoice", "POST", "/v3/invoices/{invoice_id}/email", "Email an invoice",
              "sends an invoice to a customer", "replyTo", "recipient",
              only=("replyTo", "recipient"),
              money=Money((), (), ("recipient.to.0",), ("invoice_id",))),
        final("archive_bill", "POST", "/v3/bills/{bill_id}/archive", "Archive a bill",
              BILL_ARCHIVES),
        final("archive_invoice", "POST", "/v3/invoices/{invoice_id}/archive", "Archive an invoice",
              BILL_ARCHIVES),
        final("archive_customer", "POST", "/v3/customers/{customer_id}/archive",
              "Archive a customer", BILL_ARCHIVES),
        final("archive_vendor", "POST", "/v3/vendors/{vendor_id}/archive", "Archive a vendor",
              BILL_ARCHIVES),
    ),
)  # fmt: skip

VENDORS = (SQUARE, HELCIM, LEMON_SQUEEZY, CLOVER, BRAINTREE, PLAID, BILL)
