"""Stores and sites: Shopify, through its Admin API, and Ghost.

Holds, by the user's rule: drafts wait for approval; publishing, unpublishing, deleting,
themes and store settings need the name typed. Whether a call publishes is read from the call
itself, and anything that doesn't plainly say "draft" counts as publishing.

An edit to something already live says nothing about being live, so it's held like a draft:
the call alone can't tell the two apart.

Left out on purpose:
- Shopify: access tokens of any kind, activation and sign-in links, payment methods, staff,
  webhooks, apps and billing, bulk operations, and gift cards.
- Ghost: settings (reading them can answer with keys, and writing them can change keys),
  users, invites, integrations, webhooks, the database, bulk edits and deletes, and sign-in
  links for members.

Undo isn't offered on either yet. A Shopify read takes its ID as a variable, which an undo's
read can't pass; a Ghost edit needs the record's current updated_at, which a snapshot doesn't
have.
"""

import base64
import hashlib
import hmac
import json
import time
from collections.abc import Callable
from typing import cast

from ringkeeper.adapters.vendor_spec import (
    JSON,
    Auth,
    Exchange,
    Money,
    Secret,
    Vendor,
    final,
    held,
    mutation,
    query,
    reads,
)

THEME = "changes how the live site looks"
DELETES = "deletes it for good"


def _about(*account: str) -> Money:
    """A call that names a record and no amount: the card shows which record."""
    return Money((), (), (), account)


def _body(args: JSON) -> JSON:
    return cast(JSON, args.get("body") or {})


# --- Shopify ---

SHOPIFY_VERSION = "2026-10"  # the latest stable version in Shopify's version table
SHOP_ADDRESS = r"^[a-z0-9][a-z0-9-]*\.myshopify\.com$"
ERRORS = "userErrors { field message }"
PAGED = "$first: Int!, $after: String, $query: String"
PAGE_ARGS = "first: $first, after: $after, query: $query"
PAGE_INFO = "pageInfo { hasNextPage endCursor }"
PRODUCT = "id title handle status vendor productType tags totalInventory updatedAt"
VARIANTS = "variants(first: 50) { nodes { id title sku price inventoryQuantity } }"
MONEY = "shopMoney { amount currencyCode }"
ORDER = (
    "id name createdAt email displayFinancialStatus displayFulfillmentStatus cancelledAt "
    f"totalPriceSet {{ {MONEY} }} customer {{ id displayName }}"
)
ORDER_DETAIL = (
    "lineItems(first: 50) { nodes { id title quantity sku } } "
    f"transactions {{ id kind status gateway amountSet {{ {MONEY} }} }} "
    "fulfillmentOrders(first: 10) { nodes { id status } }"
)
CUSTOMER = (
    "id displayName firstName lastName state tags note numberOfOrders "
    "defaultEmailAddress { emailAddress } defaultPhoneNumber { phoneNumber } "
    "amountSpent { amount currencyCode }"
)
DRAFT_ORDER = f"id name status email invoiceUrl totalPriceSet {{ {MONEY} }}"


def _list(root: str, fields: str) -> str:
    return f"query List({PAGED}) {{ {root}({PAGE_ARGS}) {{ nodes {{ {fields} }} {PAGE_INFO} }} }}"


def _one(root: str, fields: str) -> str:
    return f"query One($id: ID!) {{ {root}(id: $id) {{ {fields} }} }}"


def _change(declared: str, call: str, answer: str) -> str:
    return f"mutation Change({declared}) {{ {call} {{ {answer} {ERRORS} }} }}"


def _product_created(args: JSON) -> str | None:
    product = cast(JSON, _body(args).get("product") or {})
    return None if product.get("status") == "DRAFT" else "puts a product on sale"


def _product_edited(args: JSON) -> str | None:
    product = cast(JSON, _body(args).get("product") or {})
    return "changes whether a product is on sale" if "status" in product else None


def _visible_created(kind: str) -> Callable[[JSON], str | None]:
    def why(args: JSON) -> str | None:
        item = cast(JSON, _body(args).get(kind) or {})
        hidden = item.get("isPublished") is False and "publishDate" not in item
        return None if hidden else f"publishes a {kind}"

    return why


def _visible_edited(kind: str) -> Callable[[JSON], str | None]:
    def why(args: JSON) -> str | None:
        item = cast(JSON, _body(args).get(kind) or {})
        changes = "isPublished" in item or "publishDate" in item
        return f"changes whether a {kind} is published" if changes else None

    return why


def _shop_who(found: JSON) -> str:
    shop = cast(JSON, found.get("shop") or {})
    return f"{shop.get('name')} ({shop.get('myshopifyDomain')})"


SHOPIFY = Vendor(
    "shopify", "Shopify", "CMS", "Products, orders, customers, pages, themes", "https://{shop}",
    auth=Auth("header", "X-Shopify-Access-Token"),
    secrets=(
        Secret("shop", "Store address", "Like acme.myshopify.com", hidden=False,
               pattern=SHOP_ADDRESS),
        Secret("api_key", "Admin API access token", "A custom app made before 2026: leave "
               "the next two empty.", required=False),
        Secret("client_id", "Client ID", "A Dev Dashboard app, made in the store's own "
               "organization.", hidden=False, required=False),
        Secret("client_secret", "Client secret", "From the same app.", required=False),
    ),
    exchange=Exchange(
        "https://{shop}/admin/oauth/access_token",
        {"grant_type": "=client_credentials", "client_id": "client_id",
         "client_secret": "client_secret"},
        when="client_id",
    ),
    graphql_path=f"/admin/api/{SHOPIFY_VERSION}/graphql.json",
    probe="get_shop", who=_shop_who,
    words=("shopify", "store", "ecommerce", "products", "orders", "commerce"),
    key_url="https://shopify.dev/docs/apps/build/dev-dashboard/create-apps-using-dev-dashboard",
    steps="A custom app made before January 2026: in the Shopify admin, open Apps, then Develop "
    "apps, pick the app, and copy its Admin API access token. Since then, Shopify makes new "
    "apps in its Dev Dashboard: create one in the organization that owns the store, give it "
    "only the access it needs, install it on the store, and paste its client ID and secret "
    "here. Ringkeeper trades those for a token that lasts a day. One connection is one store.",
    note="One connection is one store. Reading runs, orders and customers too. Draft products, "
    "draft orders and customers wait for approval. Publishing, activating, bulk price changes, "
    "refunds, cancelling or fulfilling an order, themes and store policies need the name typed.",
    unverified="Shopify's own page on trading a client ID and secret was read only in part, and "
    "nothing here has been tried on a real store. An app outside the store's organization "
    "can't use this.",
    ops=(
        query("get_shop", "This store", "query Shop { shop { name myshopifyDomain currencyCode "
              "contactEmail primaryDomain { url } plan { publicDisplayName } } }"),
        query("list_products", "Find products", _list("products", PRODUCT), "first", "after",
              "query"),
        query("get_product", "Read one product", _one("product", f"{PRODUCT} {VARIANTS}"), "id"),
        query("list_orders", "Find orders", _list("orders", ORDER), "first", "after", "query"),
        query("get_order", "Read one order", _one("order", f"{ORDER} {ORDER_DETAIL}"), "id"),
        query("list_customers", "Find customers", _list("customers", CUSTOMER), "first", "after",
              "query"),
        query("get_customer", "Read one customer", _one("customer", CUSTOMER), "id"),
        query("list_draft_orders", "Find draft orders", _list("draftOrders", DRAFT_ORDER), "first",
              "after", "query"),
        query("list_collections", "Find collections",
              _list("collections", "id title handle updatedAt"), "first", "after", "query"),
        query("list_pages", "Find pages", _list("pages", "id title handle isPublished updatedAt"),
              "first", "after", "query"),
        query("list_blogs", "Find blogs", _list("blogs", "id title handle"), "first", "after",
              "query"),
        query("list_articles", "Find blog articles",
              _list("articles", "id title handle isPublished publishedAt blog { id title }"),
              "first", "after", "query"),
        query("list_themes", "The store's themes",
              "query Themes($first: Int!) { themes(first: $first) { nodes { id name role "
              "updatedAt } } }", "first"),
        query("list_publications", "Where products can be published",
              "query Publications($first: Int!) { publications(first: $first) { nodes { id name "
              "} } }", "first"),

        # Held: a draft, or an edit that doesn't change what's live.
        mutation("create_product", "Create a product: status DRAFT only waits for approval",
                 _change("$product: ProductCreateInput!", "productCreate(product: $product)",
                         "product { id title status }"),
                 "product", why=_product_created),
        mutation("update_product", "Change a product",
                 _change("$product: ProductUpdateInput!", "productUpdate(product: $product)",
                         "product { id title status }"),
                 "product", why=_product_edited),
        mutation("create_customer", "Create a customer",
                 _change("$input: CustomerInput!", "customerCreate(input: $input)",
                         "customer { id displayName }"), "input"),
        mutation("update_customer", "Change a customer",
                 _change("$input: CustomerInput!", "customerUpdate(input: $input)",
                         "customer { id displayName }"), "input"),
        mutation("update_order", "Change an order's note, tags or address",
                 _change("$input: OrderInput!", "orderUpdate(input: $input)",
                         "order { id name }"), "input"),
        mutation("create_draft_order", "Create a draft order",
                 _change("$input: DraftOrderInput!", "draftOrderCreate(input: $input)",
                         f"draftOrder {{ {DRAFT_ORDER} }}"), "input"),
        mutation("create_page", "Create a page: isPublished false only waits for approval",
                 _change("$page: PageCreateInput!", "pageCreate(page: $page)",
                         "page { id title isPublished }"),
                 "page", why=_visible_created("page")),
        mutation("update_page", "Change a page",
                 _change("$id: ID!, $page: PageUpdateInput!", "pageUpdate(id: $id, page: $page)",
                         "page { id title isPublished }"),
                 "id", "page", why=_visible_edited("page")),
        mutation("create_article", "Create a blog article: isPublished false only waits for "
                 "approval",
                 _change("$article: ArticleCreateInput!", "articleCreate(article: $article)",
                         "article { id title isPublished }"),
                 "article", why=_visible_created("article")),
        mutation("update_article", "Change a blog article",
                 _change("$id: ID!, $article: ArticleUpdateInput!",
                         "articleUpdate(id: $id, article: $article)",
                         "article { id title isPublished }"),
                 "id", "article", why=_visible_edited("article")),

        # The name typed.
        mutation("publish_product", "Publish something to a sales channel",
                 _change("$id: ID!, $input: [PublicationInput!]!",
                         "publishablePublish(id: $id, input: $input)", "shop { name }"),
                 "id", "input", why="publishes to a sales channel"),
        mutation("unpublish_product", "Take something off a sales channel",
                 _change("$id: ID!, $input: [PublicationInput!]!",
                         "publishableUnpublish(id: $id, input: $input)", "shop { name }"),
                 "id", "input", why="takes it off a sales channel"),
        mutation("update_variants", "Change variants, prices included, in bulk",
                 _change("$productId: ID!, $variants: [ProductVariantsBulkInput!]!",
                         "productVariantsBulkUpdate(productId: $productId, variants: $variants)",
                         "productVariants { id title price }"),
                 "productId", "variants", why="changes prices or stock in bulk"),
        mutation("delete_product", "Delete a product",
                 _change("$input: ProductDeleteInput!", "productDelete(input: $input)",
                         "deletedProductId"), "input", why=DELETES),
        mutation("delete_customer", "Delete a customer",
                 _change("$input: CustomerDeleteInput!", "customerDelete(input: $input)",
                         "deletedCustomerId"), "input", why=DELETES),
        mutation("delete_page", "Delete a page",
                 _change("$id: ID!", "pageDelete(id: $id)", "deletedPageId"), "id", why=DELETES),
        mutation("delete_article", "Delete a blog article",
                 _change("$id: ID!", "articleDelete(id: $id)", "deletedArticleId"), "id",
                 why=DELETES),
        mutation("refund_order", "Refund an order",
                 _change("$input: RefundInput!", "refundCreate(input: $input)",
                         f"refund {{ id totalRefundedSet {{ {MONEY} }} }}"),
                 "input", why="gives money back",
                 money=Money(("input.transactions.0.amount",), ("input.currency",), (),
                             ("input.orderId",))),
        mutation("cancel_order", "Cancel an order",
                 _change("$orderId: ID!, $reason: OrderCancelReason!, $restock: Boolean!, "
                         "$refund: Boolean, $notifyCustomer: Boolean, $staffNote: String",
                         "orderCancel(orderId: $orderId, reason: $reason, restock: $restock, "
                         "refund: $refund, notifyCustomer: $notifyCustomer, staffNote: $staffNote)",
                         "job { id }").replace(ERRORS, "orderCancelUserErrors { field message }"),
                 "orderId", "reason", "restock", "refund", "notifyCustomer", "staffNote",
                 why="cancels an order, and can refund it", money=_about("orderId")),
        mutation("capture_order", "Capture an authorized payment",
                 _change("$input: OrderCaptureInput!", "orderCapture(input: $input)",
                         "transaction { id status }"),
                 "input", why="takes payment that was only authorized",
                 money=Money(("input.amount",), ("input.currency",), (), ("input.id",))),
        mutation("fulfill_order", "Mark items as shipped",
                 _change("$fulfillment: FulfillmentInput!, $message: String",
                         "fulfillmentCreate(fulfillment: $fulfillment, message: $message)",
                         "fulfillment { id status }"),
                 "fulfillment", "message", why="ships an order, and can email the customer"),
        mutation("complete_draft_order", "Turn a draft order into an order",
                 _change("$id: ID!, $paymentPending: Boolean",
                         "draftOrderComplete(id: $id, paymentPending: $paymentPending)",
                         "draftOrder { id status order { id name } }"),
                 "id", "paymentPending", why="creates a real order, and can take payment",
                 money=_about("id")),
        mutation("send_draft_order_invoice", "Email a draft order's invoice",
                 _change("$id: ID!, $email: EmailInput",
                         "draftOrderInvoiceSend(id: $id, email: $email)", "draftOrder { id }"),
                 "id", "email", why="emails a customer"),
        mutation("publish_theme", "Make a theme the live one",
                 _change("$id: ID!", "themePublish(id: $id)", "theme { id name role }"), "id",
                 why=THEME),
        mutation("update_theme_files", "Write files into a theme",
                 _change("$themeId: ID!, $files: [OnlineStoreThemeFilesUpsertFileInput!]!",
                         "themeFilesUpsert(themeId: $themeId, files: $files)",
                         "upsertedThemeFiles { filename }"),
                 "themeId", "files", why=THEME),
        mutation("update_shop_policy", "Change a store policy",
                 _change("$shopPolicy: ShopPolicyInput!",
                         "shopPolicyUpdate(shopPolicy: $shopPolicy)", "shopPolicy { id type }"),
                 "shopPolicy", why="changes a store setting"),
    ),
)  # fmt: skip


# --- Ghost ---

GHOST_VERSION = "v6.0"  # as Ghost's own Admin API client sends it
# A public name, not an address on this machine: the key goes wherever this points.
GHOST_DOMAIN = r"^([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$"
GHOST_KEY = r"^[0-9a-f]{24}:[0-9a-f]{64}$"
GHOST_TOKEN_SECONDS = 300  # Ghost's limit
DRAFT = "draft"
GOES_OUT = ("status", "email_only", "published_at")


def _encoded(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


def _ghost_token(key: str, now: int) -> str:
    """Ghost's short-lived token: a JWT signed with HS256 by the integration's secret."""
    key_id, secret = key.split(":", 1)
    header = {"alg": "HS256", "typ": "JWT", "kid": key_id}
    claims = {"iat": now, "exp": now + GHOST_TOKEN_SECONDS, "aud": "/admin/"}
    signed = ".".join(_encoded(json.dumps(part).encode()) for part in (header, claims))
    signature = hmac.new(bytes.fromhex(secret), signed.encode(), hashlib.sha256).digest()
    return f"{signed}.{_encoded(signature)}"


def _ghost_headers(fields: JSON) -> dict[str, str]:
    return {"Authorization": f"Ghost {_ghost_token(str(fields['api_key']), int(time.time()))}"}


def _sends_email(args: JSON) -> bool:
    asked = cast(JSON, args.get("query") or {})
    return bool(asked.get("newsletter") or asked.get("email_segment"))


def _items(args: JSON, kind: str) -> list[JSON]:
    found = _body(args).get(kind)
    return [cast(JSON, i) for i in cast(list[object], found)] if isinstance(found, list) else []


def _ghost_created(kind: str) -> Callable[[JSON], str | None]:
    """A new post or page: only one that says `draft` stays off the site."""

    def why(args: JSON) -> str | None:
        if _sends_email(args):
            return "sends a newsletter to members"
        items = _items(args, kind)
        drafts = items and all(i.get("status") == DRAFT and not i.get("email_only") for i in items)
        return None if drafts else f"publishes {kind} to the site"

    return why


def _ghost_edited(kind: str) -> Callable[[JSON], str | None]:
    """An edit that names a status publishes, schedules or takes down."""

    def why(args: JSON) -> str | None:
        if _sends_email(args):
            return "sends a newsletter to members"
        changes = any(field in i for i in _items(args, kind) for field in GOES_OUT)
        return f"changes whether {kind} are live" if changes else None

    return why


def _ghost_who(found: JSON) -> str:
    site = cast(JSON, found.get("site") or {})
    return f"{site.get('title')} (Ghost {site.get('version')})"


GHOST = Vendor(
    "ghost", "Ghost", "CMS", "Posts, pages, newsletters, members",
    "https://{domain}/ghost/api/admin",
    auth=Auth("custom", custom=_ghost_headers),
    secrets=(
        Secret("domain", "Admin domain", "Like acme.ghost.io: where you open Ghost Admin",
               hidden=False, pattern=GHOST_DOMAIN),
        Secret("api_key", "Admin API key", "From a custom integration, as id:secret",
               pattern=GHOST_KEY),
    ),
    headers={"Accept-Version": GHOST_VERSION},
    probe="get_site", who=_ghost_who,
    words=("ghost", "blog", "newsletter", "cms", "publishing", "members"),
    key_url="https://docs.ghost.org/admin-api",
    steps="In Ghost Admin, open Settings, find Integrations, and add a custom integration. Copy "
    "its Admin API key, and type the domain you open Ghost Admin on. One key reaches one site.",
    note="One connection is one site. Reading runs. Drafts, tags and members wait for approval. "
    "Publishing, scheduling, unpublishing, sending a newsletter, deleting and themes need the "
    "name typed. An edit to a live post that doesn't change its status only waits for approval.",
    ops=(
        reads("get_site", "/site/", "This site: its title and Ghost version"),
        reads("list_posts", "/posts/", "Find posts", "filter", "limit", "page", "order", "include",
              "fields", "formats"),
        reads("get_post", "/posts/{id}/", "Read one post", "include", "fields", "formats"),
        reads("get_post_by_slug", "/posts/slug/{slug}/", "Read one post by its slug", "include",
              "fields", "formats"),
        reads("list_pages", "/pages/", "Find pages", "filter", "limit", "page", "order", "include",
              "fields", "formats"),
        reads("get_page", "/pages/{id}/", "Read one page", "include", "fields", "formats"),
        reads("list_tags", "/tags/", "Find tags", "filter", "limit", "page", "order"),
        reads("list_newsletters", "/newsletters/", "The site's newsletters", "limit", "page"),
        reads("list_tiers", "/tiers/", "Membership tiers", "include", "limit", "page"),
        reads("list_offers", "/offers/", "Offers", "limit", "page"),
        reads("list_members", "/members/", "Find members", "filter", "limit", "page", "order",
              "include"),
        reads("get_member", "/members/{id}/", "Read one member", "include"),

        # Held, unless the call says it goes out.
        final("create_post", "POST", "/posts/", "Create a post: status draft only waits for "
              "approval", _ghost_created("posts"), "posts", "newsletter", "email_segment",
              only=("posts",)),
        final("update_post", "PUT", "/posts/{id}/", "Change a post: send its current updated_at",
              _ghost_edited("posts"), "posts", "newsletter", "email_segment", "save_revision",
              only=("posts",)),
        final("create_page", "POST", "/pages/", "Create a page: status draft only waits for "
              "approval", _ghost_created("pages"), "pages", only=("pages",)),
        final("update_page", "PUT", "/pages/{id}/", "Change a page: send its current updated_at",
              _ghost_edited("pages"), "pages", "save_revision", only=("pages",)),
        held("create_tag", "POST", "/tags/", "Create a tag", "tags", only=("tags",)),
        held("update_tag", "PUT", "/tags/{id}/", "Change a tag", "tags", only=("tags",)),
        held("create_member", "POST", "/members/", "Add a member", "members", only=("members",)),
        held("update_member", "PUT", "/members/{id}/", "Change a member", "members",
             only=("members",)),

        # The name typed.
        final("delete_post", "DELETE", "/posts/{id}/", "Delete a post", DELETES),
        final("delete_page", "DELETE", "/pages/{id}/", "Delete a page", DELETES),
        final("delete_tag", "DELETE", "/tags/{id}/", "Delete a tag", DELETES),
        final("delete_member", "DELETE", "/members/{id}/", "Delete a member", DELETES),
        final("activate_theme", "PUT", "/themes/{name}/activate/", "Make a theme the live one",
              THEME),
    ),
)  # fmt: skip

VENDORS = (SHOPIFY, GHOST)
