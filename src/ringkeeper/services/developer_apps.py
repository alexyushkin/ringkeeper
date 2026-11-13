"""Your own registered apps (Settings > Developer apps), and signing in through them.

Some vendors only let an app you registered yourself sign in: Microsoft (Entra), HubSpot,
Salesforce, Xero, Intuit and Jobber. Each app is stored in the vault, there may be several
per vendor (a client's own tenant, say), and many accounts sign in through one app. Google
works the same way and keeps its own module (services.google_oauth); it's shown here too.

Where the vendor sends the browser back:

- Most want the exact address registered, and the app's own port changes each launch, so
  they use the fixed address on this computer (`hooks_port`, loopback only): nothing is
  opened to the internet.
- Some (`hosted`) want https, or won't take localhost for real accounts. They use a static
  page at HOSTED_CALLBACK, which forwards the code and state to that same fixed address. The
  page keeps nothing and checks nothing: the state is checked here, as it is for any sign-in,
  and a code is no use without this app's secret and the sign-in's PKCE verifier.

Each account is a `vendor` connection (adapters.vendor) whose secret holds its tokens and
which app got them. A refresh token works only with its own app.

Meta gives no refresh token. A Facebook sign-in keeps each Page's own token, which doesn't end;
a Threads one keeps a 60-day token, renewed while it's in use (`renew`). Its docs want the app's
secret on a server: here it's in this computer's vault, like every other app's.

Google Ads signs in through the Google app (services.google_oauth), so there's nothing more to
add here: `listed` keeps it off the Developer apps page.
"""

import base64
import contextlib
import hashlib
import json
import re
import secrets
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, cast
from urllib.parse import urlencode, urlsplit

import httpx2
from sqlalchemy.orm import Session

from ringkeeper import remote
from ringkeeper.adapters import vendor as vendors
from ringkeeper.config import Settings, get_settings
from ringkeeper.errors import RingkeeperError, RuleViolationError
from ringkeeper.models import Env, InstanceType
from ringkeeper.schemas import BindingIn, InstanceIn
from ringkeeper.services import (
    admin_audit,
    bindings,
    clients,
    google_oauth,
    instances,
    mcp_oauth,
    projects,
    remote_connections,
    secrets_in,
)
from ringkeeper.vault import Secret, Vault, get_vault

JSON = dict[str, Any]
CALLBACK_PATH = "/oauth/callback"
APPS_REF = "file://ringkeeper/developer-apps/{provider}"
PENDING_SECONDS = 600
REFRESH_MARGIN_SECONDS = 120
RENEW_MARGIN_SECONDS = 30 * 24 * 3600  # a token that's renewed, not refreshed: well before it ends
TIMEOUT = 20.0
# A static page: it sends the browser on to HOSTED_PORT on this computer. Its port is fixed.
HOSTED_CALLBACK = "https://yushkin.us/ringkeeper/oauth/callback"
HOSTED_PORT = 17845
# Tests swap in httpx2.MockTransport; production talks to the vendor.
transport: httpx2.BaseTransport | None = None


class SignInError(mcp_oauth.SignInError):
    """A sign-in didn't work. The message is for the user, and tells the gateway the
    connection needs signing in again."""


@dataclass(frozen=True)
class Access:
    """One thing a connection may be allowed to do, and the scope it needs."""

    key: str
    label: str
    scope: str


@dataclass(frozen=True)
class Option:
    """A setting of the app itself, beyond its client ID and secret."""

    key: str
    label: str
    hint: str = ""
    default: str = ""
    choices: tuple[tuple[str, str], ...] = ()
    pattern: str = ""


@dataclass(frozen=True)
class App:
    id: str
    name: str
    client_id: str
    client_secret: str
    options: Mapping[str, str] = field(default_factory=dict[str, str])


@dataclass(frozen=True)
class Provider:
    key: str
    name: str
    console: str  # where the app is registered
    authorize: str  # may name an Option: https://{login_host}/...
    token: str
    steps: tuple[str, ...]  # the guide; {redirect} is the address to paste
    scopes: tuple[str, ...] = ()  # what every sign-in asks for
    access: tuple[Access, ...] = ()  # chosen per connection
    vendor_scopes: Mapping[str, tuple[str, ...]] = field(
        default_factory=dict[str, tuple[str, ...]]
    )  # instead of the above, for one vendor
    options: tuple[Option, ...] = ()
    basic_auth: bool = False  # the client goes in a header, not the form
    extra: Mapping[str, str] = field(default_factory=dict[str, str])  # on the consent page
    revoke: str = ""
    # Who signed in, and anything the vendor's address needs: (account, extra fields).
    identify: Callable[["App", JSON, JSON], tuple[str, JSON]] | None = None
    note: str = ""
    # Wants https, or won't take localhost for real accounts: comes back by HOSTED_CALLBACK.
    hosted: bool = False
    token_by_get: bool = False  # the token address is asked with GET, as Meta documents
    scope_join: str = " "
    no_refresh_token: bool = False  # `identify` makes the sign-in last instead
    # A token with no refresh token, made new before it ends: (the app, the token) -> tokens.
    renew: Callable[[str], JSON] | None = None
    # A client's account needs the vendor's review of the app, until the app says it passed.
    client_review: bool = False
    listed: bool = True  # shown on the Developer apps page


def _http() -> httpx2.Client:
    return httpx2.Client(timeout=TIMEOUT, transport=transport, follow_redirects=False)


def _get(url: str, token: str, what: str) -> Any:
    with _http() as http:
        try:
            response = http.get(url, headers={"Authorization": f"Bearer {token}"})
            found: Any = response.json()
        except (httpx2.HTTPError, ValueError):
            raise SignInError(f"Couldn't ask {what} which account this is. Try again.") from None
    if response.status_code >= 400:
        raise SignInError(f"{what} didn't say which account this is. Try again.")
    return found


def _token_claims(tokens: JSON) -> JSON:
    """The id_token's claims. It came straight from the vendor's token address over TLS,
    so it's read as it is, and used only to name the account."""
    try:
        payload = str(tokens["id_token"]).split(".")[1]
        data: Any = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except (KeyError, IndexError, ValueError):
        return {}
    return cast(JSON, data) if isinstance(data, dict) else {}


def _microsoft(_app: App, tokens: JSON, _back: JSON) -> tuple[str, JSON]:
    claims = _token_claims(tokens)
    account = claims.get("preferred_username") or claims.get("email") or claims.get("name")
    if not account:
        raise SignInError("Microsoft didn't say which account this is. Try again.")
    return str(account).lower(), {}


def _hubspot(app: App, tokens: JSON, _back: JSON) -> tuple[str, JSON]:
    form = {
        "client_id": app.client_id, "client_secret": app.client_secret,
        "token_type_hint": "access_token", "token": str(tokens["access_token"]),
    }  # fmt: skip
    with _http() as http:
        try:
            found = cast(JSON, http.post(f"{HUBSPOT_OAUTH}/token/introspect", data=form).json())
        except (httpx2.HTTPError, ValueError):
            raise SignInError("Couldn't ask HubSpot which account this is. Try again.") from None
    account = found.get("hub_domain") or found.get("hub_id")
    if not account:
        raise SignInError("HubSpot didn't say which account this is. Try again.")
    return str(account), {"hub_id": str(found.get("hub_id") or "")}


def _salesforce(_app: App, tokens: JSON, _back: JSON) -> tuple[str, JSON]:
    host = urlsplit(str(tokens.get("instance_url") or "")).hostname or ""
    if not host:
        raise SignInError("Salesforce didn't say which org this is. Try again.")
    who = cast(JSON, _get(str(tokens["id"]), str(tokens["access_token"]), "Salesforce"))
    return str(who.get("username") or host), {"instance_host": host}


def _xero(_app: App, tokens: JSON, _back: JSON) -> tuple[str, JSON]:
    found = _get("https://api.xero.com/connections", str(tokens["access_token"]), "Xero")
    tenants = [
        {"id": str(t.get("tenantId")), "name": str(t.get("tenantName"))}
        for t in cast(list[JSON], found if isinstance(found, list) else [])
    ]
    if not tenants:
        raise SignInError("No Xero organisation was chosen. Try again and pick one.")
    extra: JSON = {"tenants": tenants}
    if len(tenants) == 1:
        extra["default_account"] = tenants[0]["id"]
    return ", ".join(t["name"] for t in tenants), extra


def _intuit(app: App, _tokens: JSON, back: JSON) -> tuple[str, JSON]:
    realm = str(back.get("realmId") or "")
    if not re.fullmatch(r"\d{1,30}", realm):
        raise SignInError("Intuit didn't say which company this is. Try again.")
    sandbox = app.options.get("environment") != "production"
    host = f"{'sandbox-' if sandbox else ''}quickbooks.api.intuit.com"
    return f"company {realm}", {"realm_id": realm, "api_host": host}


def _jobber(_app: App, _tokens: JSON, _back: JSON) -> tuple[str, JSON]:
    return "Jobber account", {}


def _google(_app: App, tokens: JSON, _back: JSON) -> tuple[str, JSON]:
    found = cast(JSON, _get(google_oauth.USERINFO, str(tokens["access_token"]), "Google"))
    email = str(found.get("email") or "").lower()
    if not email:
        raise SignInError("Google didn't say which account this is. Try again.")
    return email, {}


def _graph(url: str, params: JSON, what: str) -> JSON:
    """A Meta read during sign-in. The token goes in the query, as Meta documents."""
    with _http() as http:
        try:
            response = http.get(url, params=params)
            found: Any = response.json()
        except (httpx2.HTTPError, ValueError):
            raise SignInError(f"Couldn't ask {what}. Try again.") from None
    if response.status_code >= 400 or not isinstance(found, dict):
        answer = cast(JSON, found) if isinstance(found, dict) else {}
        error = cast(JSON, answer.get("error") or {})
        said = str(error.get("message") or response.status_code)[:200]
        raise SignInError(f"{what} didn't answer ({said}).")
    return cast(JSON, found)


def _meta(app: App, tokens: JSON, back: JSON) -> tuple[str, JSON]:
    """Who this is, and a lasting token for each Page: one made from a long-lived user token
    doesn't end. The user token itself isn't needed again until the next sign-in."""
    lasting = _graph(f"{GRAPH}/oauth/access_token", {
        "grant_type": "fb_exchange_token", "client_id": app.client_id,
        "client_secret": app.client_secret, "fb_exchange_token": tokens["access_token"],
    }, "Meta")  # fmt: skip
    user = str(lasting.get("access_token") or "")
    if not user:
        raise SignInError("Meta didn't give a lasting sign-in. Start it again.")
    who = _graph(f"{GRAPH}/me", {"fields": "id,name", "access_token": user}, "Meta")
    instagram = back.get("vendor") == "instagram"
    fields = "id,name,access_token"
    if instagram:
        fields += ",instagram_business_account{id,username}"
    found = _graph(f"{GRAPH}/me/accounts",
                   {"fields": fields, "limit": 100, "access_token": user}, "Meta")  # fmt: skip
    pages = [
        {
            "id": str(p.get("id")), "name": str(p.get("name")), "token": str(p["access_token"]),
            "instagram": p.get("instagram_business_account"),
        }
        for p in cast(list[JSON], found.get("data") or [])
        if p.get("access_token")
    ]  # fmt: skip
    if not pages:
        raise SignInError("No Pages were allowed. Sign in again and pick at least one.")
    extra: JSON = {"pages": pages, "access_token": user, "expires_at": _ends(lasting)}
    if instagram:
        linked = [cast(JSON, p["instagram"]) for p in pages if p["instagram"]]
        if not linked:
            raise SignInError(
                "None of those Pages has an Instagram professional account linked to it."
            )
        if len(linked) == 1:
            extra["default_account"] = str(linked[0].get("id"))
    elif len(pages) == 1:
        extra["default_account"] = pages[0]["id"]
    return str(who.get("name") or who.get("id")), extra


def _ends(tokens: JSON) -> int:
    return int(time.time() + float(tokens.get("expires_in") or 60 * 24 * 3600))


def _threads(app: App, tokens: JSON, _back: JSON) -> tuple[str, JSON]:
    lasting = _graph(f"{THREADS_GRAPH}/access_token", {
        "grant_type": "th_exchange_token", "client_secret": app.client_secret,
        "access_token": tokens["access_token"],
    }, "Threads")  # fmt: skip
    token = str(lasting.get("access_token") or "")
    if not token:
        raise SignInError("Threads didn't give a lasting sign-in. Start it again.")
    who = _graph(f"{THREADS_GRAPH}/v1.0/me", {"fields": "id,username", "access_token": token},
                 "Threads")  # fmt: skip
    return str(who.get("username") or who.get("id")), {
        "access_token": token, "expires_at": _ends(lasting), "default_account": str(who.get("id")),
    }  # fmt: skip


def _threads_renew(token: str) -> JSON:
    return _graph(f"{THREADS_GRAPH}/refresh_access_token",
                  {"grant_type": "th_refresh_token", "access_token": token}, "Threads")  # fmt: skip


HUBSPOT_OAUTH = "https://api.hubspot.com/oauth/2026-09"
GRAPH = "https://graph.facebook.com/v27.0"
THREADS_GRAPH = "https://graph.threads.com"
META_APPS = "https://developers.facebook.com/apps"
FACEBOOK_SCOPES = (
    "business_management", "pages_show_list", "pages_read_engagement", "pages_manage_posts",
    "pages_manage_engagement", "pages_read_user_content", "read_insights",
)  # fmt: skip
INSTAGRAM_SCOPES = (
    "business_management", "pages_show_list", "pages_read_engagement", "instagram_basic",
    "instagram_content_publish", "instagram_manage_comments", "instagram_manage_insights",
)  # fmt: skip
THREADS_SCOPES = (
    "threads_basic", "threads_content_publish", "threads_read_replies",
    "threads_manage_replies", "threads_manage_insights", "threads_delete",
)  # fmt: skip
REVIEW_OPTION = Option(
    "client_accounts",
    "Client accounts",
    "Meta must review your app before a client's account can sign in. Your own accounts work now.",
    default="needs_review",
    choices=(("needs_review", "Needs Meta review"), ("approved", "Approved by Meta")),
)
HUBSPOT_OPTIONAL = (
    "automation.sequences.read", "automation.sequences.enrollments.write", "marketing-email",
    "transactional-email",
)  # fmt: skip
LOGIN = "https://login.microsoftonline.com/{tenant}/oauth2/v2.0"
MICROSOFT_ACCESS = (
    Access("mail_read", "Read mail", "Mail.Read"),
    Access("mail_send", "Send mail", "Mail.Send"),
    Access("mail_drafts", "Write drafts and move mail", "Mail.ReadWrite"),
    Access("calendar_read", "Calendar: read events", "Calendars.Read"),
    Access("calendar_write", "Calendar: add and change events", "Calendars.ReadWrite"),
    Access("files_read", "OneDrive and SharePoint: read files", "Files.Read.All"),
    Access("files_write", "OneDrive and SharePoint: change files", "Files.ReadWrite.All"),
)

PROVIDERS: dict[str, Provider] = {
    p.key: p
    for p in (
        Provider(
            "microsoft", "Microsoft (Entra)",
            "https://entra.microsoft.com/#view/Microsoft_AAD_RegisteredApps/ApplicationsListBlade",
            f"{LOGIN}/authorize", f"{LOGIN}/token",
            steps=(
                "Open App registrations in the Microsoft Entra admin center and click New "
                "registration. Name it Ringkeeper.",
                "Supported account types: choose accounts in any organizational directory, "
                "and personal Microsoft accounts if you want those too. That lets each "
                "client's account sign in through this one app.",
                "Redirect URI: choose Web and paste {redirect}. Register.",
                "Copy the Application (client) ID from the Overview page.",
                "Open Certificates & secrets, then New client secret, and copy its Value now: "
                "Microsoft shows it only once.",
                "Open API permissions, then Add a permission, Microsoft Graph, Delegated "
                "permissions, and add the scopes listed here. Delegated permissions need no "
                "admin unless a client's tenant requires it.",
            ),
            scopes=("openid", "profile", "email", "offline_access", "User.Read"),
            access=MICROSOFT_ACCESS,
            vendor_scopes={
                "businesscentral": (
                    "openid", "profile", "email", "offline_access",
                    "https://api.businesscentral.dynamics.com/user_impersonation",
                ),
            },
            options=(
                Option(
                    "tenant", "Tenant", "common for any account. A single-tenant app needs its "
                    "tenant ID.", default="common", pattern=r"^[A-Za-z0-9.\-]{2,80}$",
                ),
            ),
            extra={"prompt": "select_account"},
            identify=_microsoft,
            note="A client's admin may have to allow your app in their tenant first.",
        ),
        Provider(
            "hubspot", "HubSpot", "https://developers.hubspot.com/docs/apps/developer-platform/"
            "build-apps/create-an-app",
            "https://app.hubspot.com/oauth/authorize", f"{HUBSPOT_OAUTH}/token",
            steps=(
                "HubSpot apps are made with its command line tool: follow Create a new app "
                "using the CLI, in a free developer account.",
                "In the app's configuration, set the auth type to OAuth and add {redirect} to "
                "the redirect URLs.",
                "Add the scopes listed here to the app's required scopes, and these to its "
                f"optional scopes: {', '.join(HUBSPOT_OPTIONAL)}. Then upload it.",
                "In HubSpot, open the project, click the app, then the Auth tab, and copy the "
                "Client ID and Client secret.",
            ),
            scopes=(
                "oauth", "crm.objects.contacts.read", "crm.objects.contacts.write",
                "crm.objects.companies.read", "crm.objects.companies.write",
                "crm.objects.deals.read", "crm.objects.deals.write",
                "crm.objects.owners.read",
            ),
            # Dropped by HubSpot for an account without these products.
            extra={"optional_scope": " ".join(HUBSPOT_OPTIONAL)},
            identify=_hubspot,
            hosted=True,
        ),
        Provider(
            "salesforce", "Salesforce",
            "https://help.salesforce.com/s/articleView?id=xcloud.create_a_local_external_client_app.htm",
            "https://{login_host}/services/oauth2/authorize",
            "https://{login_host}/services/oauth2/token",
            steps=(
                "In Salesforce Setup, open External Client App Manager and click New External "
                "Client App. Name it Ringkeeper.",
                "Under API (Enable OAuth Settings), turn OAuth on and paste {redirect} as the "
                "Callback URL.",
                "Add the OAuth scopes: Manage user data via APIs (api), and Perform requests "
                "at any time (refresh_token, offline_access).",
                "Turn on Require Proof Key for Code Exchange (PKCE), and keep Require secret "
                "for Web Server Flow on. Create it.",
                "Open the app's Settings, then OAuth Settings, then Consumer Key and Secret, "
                "and copy both.",
            ),
            scopes=("api", "refresh_token"),
            options=(
                Option(
                    "login_host", "Sign-in address", "login.salesforce.com, test.salesforce.com "
                    "for a sandbox, or the org's My Domain.", default="login.salesforce.com",
                    pattern=r"^[a-z0-9.\-]+\.salesforce\.com$",
                ),
            ),
            identify=_salesforce,
            note="An app made this way works in the org it was made in. For a client's org, "
            "make one there and add it here as another app.",
            hosted=True,
        ),
        Provider(
            "xero", "Xero", "https://developer.xero.com/app/manage",
            "https://login.xero.com/identity/connect/authorize",
            "https://identity.xero.com/connect/token",
            steps=(
                "Open My Apps in the Xero developer portal and click New app.",
                "Integration type: Web app. Add a company URL, and paste {redirect} as the "
                "redirect URI. Create it.",
                "Open Configuration, copy the Client id, and click Generate a secret and copy "
                "that too.",
            ),
            scopes=(
                "openid", "profile", "email", "offline_access", "accounting.invoices",
                "accounting.payments", "accounting.banktransactions",
                "accounting.manualjournals", "accounting.contacts", "accounting.settings",
                "accounting.reports.aged.read", "accounting.reports.balancesheet.read",
                "accounting.reports.profitandloss.read", "accounting.reports.trialbalance.read",
            ),
            basic_auth=True,
            identify=_xero,
            note="Apps made since March 2026 can only use these narrower scopes. Each sign-in "
            "picks which organisations it reaches.",
            hosted=True,
        ),
        Provider(
            "intuit", "Intuit (QuickBooks)", "https://developer.intuit.com/app/developer/dashboard",
            "https://appcenter.intuit.com/connect/oauth2",
            "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer",
            steps=(
                "Open your Intuit developer dashboard and create an app for QuickBooks Online "
                "and Payments, with the Accounting scope.",
                "Open Keys & credentials. Development keys reach sandbox companies only; "
                "production keys reach real ones, and Intuit asks you to finish its app "
                "assessment first.",
                "Open Redirect URIs for the same environment and add {redirect}.",
                "Copy the Client ID and Client Secret, and choose the same environment here.",
            ),
            scopes=("com.intuit.quickbooks.accounting",),
            options=(
                Option(
                    "environment", "Environment", "Which keys these are.", default="sandbox",
                    choices=(("sandbox", "Development (sandbox)"), ("production", "Production")),
                ),
            ),
            basic_auth=True,
            revoke="https://developer.api.intuit.com/v2/oauth2/tokens/revoke",
            identify=_intuit,
            hosted=True,
        ),
        Provider(
            "jobber", "Jobber", "https://developer.getjobber.com/apps",
            "https://api.getjobber.com/api/oauth/authorize",
            "https://api.getjobber.com/api/oauth/token",
            steps=(
                "Open the Jobber Developer Center and create an app.",
                "Paste {redirect} as the OAuth callback URL, and tick the scopes you want in "
                "the app itself: Jobber takes them from there.",
                "Copy the Client ID and Client Secret.",
            ),
            identify=_jobber,
        ),
        Provider(
            "meta", "Meta (Facebook and Instagram)", META_APPS,
            "https://www.facebook.com/v27.0/dialog/oauth", f"{GRAPH}/oauth/access_token",
            steps=(
                "Open My Apps on Meta for Developers and click Create app. Choose the use case "
                "Manage everything on your Page. Add the Instagram use case too if you want it.",
                "Click Dashboard in the left menu, select each use case and click Customize. "
                "Click Add next to the permissions under Scopes here: Meta adds only a few by "
                "itself, and a missing one stops sign-in with Invalid Scopes.",
                "In the left menu, open Facebook Login for Business, then Settings. If it isn't "
                "there, add that product first. Paste {redirect} as the Redirect URI, click "
                "Check URI, then Save changes.",
                "Keep yourself in the app's Roles: without Meta's review, only people with a "
                "role can sign in.",
                "Open App settings, then Basic. The App ID is the client ID here, and the App "
                "secret is the client secret.",
            ),
            scopes=tuple(dict.fromkeys((*FACEBOOK_SCOPES, *INSTAGRAM_SCOPES))),
            vendor_scopes={"facebook": FACEBOOK_SCOPES, "instagram": INSTAGRAM_SCOPES},
            options=(REVIEW_OPTION,),
            identify=_meta,
            note="Your own Pages and Instagram accounts work without Meta's review. A client's "
            "need Tech Provider status, App Review and Business Verification first.",
            hosted=True, token_by_get=True, scope_join=",", no_refresh_token=True,
            client_review=True,
        ),
        Provider(
            "threads", "Threads", META_APPS,
            "https://threads.com/oauth/authorize", f"{THREADS_GRAPH}/oauth/access_token",
            steps=(
                "Open your Meta app, or make one, and add the Threads use case: Access the "
                "Threads API.",
                "Under the use case's settings, add the permissions listed here, and paste "
                "{redirect} as the redirect callback URL.",
                "Add your Threads account under App roles, as a Threads tester, and accept it in "
                "Threads: open Settings, Account, Website permissions.",
                "Copy the Threads app ID and Threads app secret from the use case's settings. "
                "They're not the same as the Meta app's.",
            ),
            scopes=THREADS_SCOPES,
            options=(REVIEW_OPTION,),
            identify=_threads,
            note="Your own account works without Meta's review. A client's needs App Review "
            "first.",
            hosted=True, scope_join=",", no_refresh_token=True, renew=_threads_renew,
            client_review=True,
        ),
        Provider(
            "google", "Google", "https://console.cloud.google.com/apis/credentials",
            google_oauth.AUTHORIZE, google_oauth.TOKEN, steps=(),
            scopes=google_oauth.IDENTITY,
            vendor_scopes={"google_ads": (*google_oauth.IDENTITY, f"{google_oauth.G}adwords")},
            extra={"access_type": "offline", "prompt": "consent select_account"},
            identify=_google, listed=False,
        ),
    )
}  # fmt: skip


@dataclass
class Pending:
    provider: str
    vendor: str
    redirect_uri: str
    verifier: str
    scopes: list[str]
    connection: JSON  # what to create (or `replace`), as the start asked
    app: str
    created: float = field(default_factory=time.monotonic)
    # A Recommended MCP server that signs in through this app (ringkeeper.remote, `provider`):
    # the account becomes an `mcp` connection, not a `vendor` one.
    remote: str = ""


@dataclass
class Outcome:
    state: str  # waiting, paste, done, failed
    name: str | None = None
    error: str | None = None


_pending: dict[str, Pending] = {}
_outcomes: dict[str, Outcome] = {}
_lock = threading.Lock()
_refresh_locks: dict[str, threading.Lock] = {}


def provider(key: str) -> Provider:
    found = PROVIDERS.get(key)
    if found is None:
        raise RuleViolationError(f"There's no developer app for '{key}'.")
    return found


# --- The owner's apps ---


def list_apps(settings: Settings, key: str) -> list[App]:
    if key == "google":
        return [
            App(a.id, a.name, a.client_id, a.client_secret)
            for a in google_oauth.list_apps(settings)
        ]
    try:
        value = get_vault(settings).resolve(APPS_REF.format(provider=key)).reveal()
    except RingkeeperError:
        return []
    return [
        App(str(a["id"]), str(a["name"]), str(a["client_id"]), str(a["client_secret"]),
            dict(a.get("options") or {}))
        for a in json.loads(value)["apps"]
    ]  # fmt: skip


def _store_apps(settings: Settings, key: str, apps: list[App]) -> None:
    ref = APPS_REF.format(provider=key)
    if apps:
        kept = [{**vars(a), "options": dict(a.options)} for a in apps]
        get_vault(settings).store(ref, json.dumps({"apps": kept}))
    else:
        get_vault(settings).delete(ref)


def _options(found: Provider, given: Mapping[str, str]) -> dict[str, str]:
    known = {o.key: o for o in found.options}
    if extra := sorted(set(given) - set(known)):
        raise RuleViolationError(f"{found.name} apps don't have a setting named {extra[0]}.")
    options: dict[str, str] = {}
    for option in found.options:
        value = str(given.get(option.key) or option.default).strip()
        if option.pattern and value and not re.fullmatch(option.pattern, value):
            raise RuleViolationError(f"That {option.label.lower()} doesn't look right.")
        if option.choices and value not in dict(option.choices):
            raise RuleViolationError(f"Choose the {option.label.lower()}.")
        if value:
            options[option.key] = value
    return options


def save_app(
    settings: Settings,
    key: str,
    name: str,
    client_id: str,
    client_secret: str,
    options: Mapping[str, str] | None = None,
    app_id: str | None = None,
) -> App:
    """Add an app, or (with an id) replace one. Replacing its client signs its accounts
    out at their next refresh."""
    found = provider(key)
    if not found.listed:
        raise RuleViolationError(f"{found.name} apps are set up on their own card.")
    client_id, client_secret = client_id.strip(), "".join(client_secret.split())
    name = name.strip()
    if not client_id or not client_secret:
        raise RuleViolationError("Paste the client ID and the secret.")
    if not name:
        raise RuleViolationError("Name this app (say, whose it is).")
    apps = list_apps(settings, key)
    for app in apps:
        if app.client_id == client_id and app.id != app_id:
            raise RuleViolationError(f"That client is already set up, as '{app.name}'.")
    checked = _options(found, options or {})
    if app_id is not None:
        if not any(a.id == app_id for a in apps):
            raise RuleViolationError(f"There's no {found.name} app '{app_id}'.")
        replaced = App(app_id, name, client_id, client_secret, checked)
        _store_apps(settings, key, [replaced if a.id == app_id else a for a in apps])
        return replaced
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40].strip("-") or key
    new_id, n = base, 2
    while any(a.id == new_id for a in apps):
        new_id, n = f"{base}-{n}", n + 1
    app = App(new_id, name, client_id, client_secret, checked)
    _store_apps(settings, key, [*apps, app])
    return app


def set_options(settings: Settings, key: str, app_id: str, options: Mapping[str, str]) -> App:
    """Change an app's settings, keeping its client and its secret."""
    found = provider(key)
    apps = list_apps(settings, key)
    app = next((a for a in apps if a.id == app_id), None)
    if app is None or not found.listed:
        raise RuleViolationError(f"There's no {found.name} app '{app_id}'.")
    changed = App(app.id, app.name, app.client_id, app.client_secret, _options(found, options))
    _store_apps(settings, key, [changed if a.id == app_id else a for a in apps])
    return changed


def forget_app(settings: Settings, key: str, app_id: str) -> None:
    if not provider(key).listed:
        raise RuleViolationError(f"{provider(key).name} apps are set up on their own card.")
    apps = list_apps(settings, key)
    if not any(a.id == app_id for a in apps):
        raise RuleViolationError(f"There's no {provider(key).name} app '{app_id}'.")
    _store_apps(settings, key, [a for a in apps if a.id != app_id])


def _app(settings: Settings, key: str, app_id: str | None) -> App:
    apps = list_apps(settings, key)
    app = next((a for a in apps if a.id == app_id), None) if app_id else None
    if app is None and app_id is None and apps:
        app = apps[0]
    if app is None:
        raise SignInError(
            f"Set up your {provider(key).name} app first (Settings > Developer apps)."
            if not apps
            else f"That {provider(key).name} app was removed. Sign in through another one."
        )
    return app


def listed_redirect(settings: Settings) -> str | None:
    """The one address a hosted client document can name, where this install can use it."""
    desktop = settings.mode == "desktop" and settings.hooks_port == HOSTED_PORT
    return fixed_redirect(settings) if desktop else None


def fixed_redirect(settings: Settings) -> str:
    """The address on this computer that doesn't change, or the server's own."""
    if settings.mode == "desktop":
        if not settings.hooks_port:
            raise SignInError("Ringkeeper's fixed address on this computer is turned off.")
        return f"http://localhost:{settings.hooks_port}{CALLBACK_PATH}"
    if not settings.web_origin:
        raise SignInError("Signing in needs RINGKEEPER_WEB_ORIGIN, the web app's own address.")
    return settings.web_origin.rstrip("/") + CALLBACK_PATH


def redirect_for(settings: Settings, found: Provider) -> str:
    """The address to register with this vendor. A server has its own https address."""
    if not found.hosted or settings.mode != "desktop":
        return fixed_redirect(settings)
    if settings.hooks_port != HOSTED_PORT:
        raise SignInError(
            f"{found.name} comes back through a page that expects Ringkeeper's fixed address on "
            f"port {HOSTED_PORT}, and this computer uses another one."
        )
    return HOSTED_CALLBACK


def _filled(address: str, app: App) -> str:
    def fill(match: re.Match[str]) -> str:
        return app.options.get(match.group(1), "")

    return re.sub(r"\{([a-z_]+)\}", fill, address)


def _refused(status: int, text: str) -> bool:
    """Whether a token address turned the client itself down, not the made-up code."""
    bad = ("invalid_client", "unauthorized_client", "BAD_CLIENT", "AADSTS700016", "AADSTS7000215",
           "AADSTS7000222", "client secret", "Invalid Client ID",
           "Invalid application ID")  # fmt: skip
    return status == 401 or any(word in text for word in bad)


def check_client(
    token: str, client_id: str, client_secret: str, redirect: str, basic: bool, by_get: bool = False
) -> str:
    """Asks the vendor about this client with a code that can't work, so nothing is
    created: turning the code down means it accepted the client."""
    form = {
        "grant_type": "authorization_code",
        "code": "ringkeeper-check",
        "redirect_uri": redirect,
    }
    headers: dict[str, str] = {}
    if basic:
        pair = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
        headers["Authorization"] = f"Basic {pair}"
    else:
        form |= {"client_id": client_id, "client_secret": client_secret}
    with _http() as http:
        try:
            response = (
                http.get(token, params=form)
                if by_get
                else http.post(token, data=form, headers=headers)
            )
        except httpx2.HTTPError:
            raise SignInError("Couldn't reach the vendor. Check this computer is online.") from None
    if _refused(response.status_code, response.text):
        raise SignInError("It didn't accept this client ID and secret. Check both.")
    return "It accepted this client ID and secret."


def check_app(settings: Settings, key: str, app_id: str) -> str:
    found = provider(key)
    app = _app(settings, key, app_id)
    return check_client(
        _filled(found.token, app), app.client_id, app.client_secret,
        redirect_for(settings, found), found.basic_auth, found.token_by_get,
    )  # fmt: skip


# --- Signing in ---


def scopes_for(found: Provider, vendor_key: str, access: list[str]) -> list[str]:
    if vendor_key in found.vendor_scopes:
        return list(found.vendor_scopes[vendor_key])
    if not found.access:
        return list(found.scopes)
    known = {a.key: a.scope for a in found.access}
    if not access or [k for k in access if k not in known]:
        raise RuleViolationError(f"Choose what this {found.name} account may be used for.")
    return [*found.scopes, *dict.fromkeys(known[k] for k in access)]


def _forget_old() -> None:
    now = time.monotonic()
    for state in [s for s, p in _pending.items() if now - p.created > PENDING_SECONDS]:
        del _pending[state]


def needs_review(found: Provider, app: App, connection: JSON) -> bool:
    """Whether this is a client's account, on an app the vendor hasn't reviewed. A shared tool
    is the owner's own."""
    if not found.client_review or app.options.get("client_accounts") == "approved":
        return False
    return not connection.get("shared") and not connection.get("replace")


def check_start(session: Session, connection: JSON, vendor_key: str) -> None:
    """Before anyone goes to the vendor: the name is free, the project is the same client's,
    or the connection to sign in again is this vendor's."""
    if replace := connection.get("replace"):
        found = instances.get_instance(session, str(replace))
        if found.type is not InstanceType.VENDOR or found.config.get("vendor") != vendor_key:
            raise RuleViolationError(f"'{replace}' isn't that kind of connection.")
        return
    name = connection.get("name")
    if name and instances.find_instance(session, str(name)):
        raise RuleViolationError(f"A connection named '{name}' already exists.")
    project = connection.get("project")
    if project and not connection.get("shared"):
        owner = projects.get_project(session, str(project)).owner
        if not bindings.same_client(owner, str(connection.get("owner") or "")):
            raise RuleViolationError(
                f"That project is {clients.display(session, owner)}'s, not "
                f"{clients.display(session, str(connection.get('owner') or ''))}'s."
            )


def start(
    settings: Settings,
    vendor_key: str,
    connection: JSON,
    access: list[str] | None = None,
    app_id: str | None = None,
) -> tuple[str, str]:
    """(the consent page, its state)."""
    wanted = vendors.VENDORS.get(vendor_key)
    if wanted is None or not wanted.provider:
        raise RuleViolationError("That service doesn't sign in through a developer app.")
    found = provider(wanted.provider)
    app = _app(settings, found.key, app_id)
    if needs_review(found, app, connection):
        raise RuleViolationError(
            f"A client's account needs Meta review of your app. Add {wanted.name} as a shared "
            "tool for your own accounts, or, once Meta has approved the app, say so in Settings "
            "> Developer apps."
        )
    redirect = redirect_for(settings, found)
    scopes = scopes_for(found, vendor_key, list(access or []))
    return _begin(found, app, redirect, scopes, vendor_key, connection)


def start_remote(
    settings: Settings, remote_key: str, connection: JSON, app_id: str | None = None
) -> tuple[str, str]:
    """(the consent page, its state) for a Recommended MCP server that signs in through your
    own app, like Meta's ads server: the token it gives goes to the server as Bearer."""
    service = remote.BY_KEY.get(remote_key)
    if service is None or not service.provider:
        raise RuleViolationError("That service doesn't sign in through a developer app.")
    found = provider(service.provider)
    app = _app(settings, found.key, app_id)
    redirect = redirect_for(settings, found)
    return _begin(found, app, redirect, list(service.provider_scopes), "", connection, remote_key)


def _begin(
    found: Provider,
    app: App,
    redirect: str,
    scopes: list[str],
    vendor_key: str,
    connection: JSON,
    remote_key: str = "",
) -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
    state = secrets.token_urlsafe(32)
    params = {
        "client_id": app.client_id,
        "redirect_uri": redirect,
        "response_type": "code",
        "state": state,
        "code_challenge": challenge.rstrip(b"=").decode(),
        "code_challenge_method": "S256",
        **found.extra,
    }
    if scopes:
        params["scope"] = found.scope_join.join(scopes)
    with _lock:
        _forget_old()
        _pending[state] = Pending(
            found.key, vendor_key, redirect, verifier, scopes, connection, app.id,
            remote=remote_key,
        )  # fmt: skip
        _outcomes[state] = Outcome("waiting")
    return f"{_filled(found.authorize, app)}?{urlencode(params)}", state


def pending(state: str) -> bool:
    with _lock:
        return state in _pending


def outcome(state: str) -> Outcome | None:
    with _lock:
        return _outcomes.get(state)


def _ended(state: str, result: Outcome) -> None:
    with _lock:
        _outcomes[state] = result


def take(state: str) -> Pending:
    with _lock:
        _forget_old()
        found = _pending.pop(state, None)
    if found is None:
        raise SignInError("This sign-in has expired or was already used. Start it again.")
    return found


def _token_request(found: Provider, app: App, form: dict[str, str]) -> JSON:
    headers: dict[str, str] = {}
    if found.basic_auth:
        pair = base64.b64encode(f"{app.client_id}:{app.client_secret}".encode()).decode()
        headers["Authorization"] = f"Basic {pair}"
    else:
        form = {**form, "client_id": app.client_id, "client_secret": app.client_secret}
    address = _filled(found.token, app)
    with _http() as http:
        try:
            response = (
                http.get(address, params=form)
                if found.token_by_get
                else http.post(address, data=form, headers=headers)
            )
        except httpx2.HTTPError:
            raise SignInError(f"Couldn't reach {found.name}. Check you're online.") from None
    try:
        tokens = cast(JSON, response.json())
    except ValueError:
        tokens = {}
    if response.status_code >= 400 or not tokens.get("access_token"):
        said_by = tokens.get("error")
        if isinstance(said_by, dict):  # Meta's errors are objects
            tokens = {"message": cast(JSON, said_by).get("message"), "error": "refused"}
        error = str(tokens.get("error") or tokens.get("status") or response.status_code)
        if error == "invalid_grant":
            raise SignInError(f"{found.name} no longer accepts this sign-in. Sign in again.")
        if _refused(response.status_code, response.text):
            raise SignInError(
                f"{found.name} didn't accept your app's client ID or secret. Check them in "
                "Settings > Developer apps."
            )
        said = tokens.get("error_description") or tokens.get("message") or error
        raise SignInError(f"{found.name} didn't give Ringkeeper access ({str(said)[:200]}).")
    return tokens


def _secret(pending_: Pending, tokens: JSON, refresh: str, extra: JSON, account: str) -> str:
    expires_in = float(tokens.get("expires_in") or 3600)
    return json.dumps({
        "vendor": pending_.vendor, "account": account, "app": pending_.app,
        "access_token": tokens["access_token"], "refresh_token": refresh,
        "expires_at": int(time.time() + expires_in), **extra,
    })  # fmt: skip


def _slug(text: str) -> str:
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", text.lower())).strip("-")[:50].strip("-")


def _name_for(session: Session, vendor_key: str, owner: str, shared: bool) -> str:
    base = _slug(vendor_key if shared else f"{owner}-{vendor_key}") or vendor_key
    name, n = base, 2
    while instances.find_instance(session, name) is not None:
        name, n = f"{base}-{n}", n + 1
    return name


def _store(session: Session, settings: Settings, found: Pending, secret: str, account: str) -> str:
    wanted = vendors.VENDORS[found.vendor]
    connection = found.connection
    kind = InstanceType.VENDOR
    if replace := connection.get("replace"):
        instance = instances.get_instance(session, str(replace))
        before = str(instance.config.get("account") or "")
        if before and before != account:
            raise RuleViolationError(
                f"That was {account}, but '{instance.name}' is {before}. Sign in as {before}."
            )
        secrets_in.store_connection(session, settings, instance.name, kind, secret)
        instance.config = {**instance.config, "account": account, "app": found.app}
        admin_audit.record(
            session, "connection.update", f"{instance.name}: signed in again as {account}",
            by="owner", instance=instance.name,
        )  # fmt: skip
        return instance.name
    shared = bool(connection.get("shared"))
    owner = instances.SHARED_OWNER if shared else str(connection["owner"])
    name = str(connection.get("name") or "") or _name_for(session, found.vendor, owner, shared)
    if instances.find_instance(session, name) is not None:
        raise RuleViolationError(f"A connection named '{name}' already exists.")
    ref = secrets_in.store_connection(session, settings, name, kind, secret)
    instances.create_instance(
        session,
        InstanceIn(
            name=name, title=f"{wanted.name} · {account}", type=kind,
            env=Env(str(connection.get("env") or Env.DEV.value)), secret_ref=ref, owner=owner,
            config={"vendor": found.vendor, "account": account, "source": "signin",
                    "app": found.app},
            shared=shared, all_projects=bool(connection.get("all_projects")),
        ),
    )  # fmt: skip
    admin_audit.record(
        session, "connection.create", f"added {wanted.name} account {account} as {name}",
        by="owner", instance=name,
    )  # fmt: skip
    if project := connection.get("project"):
        bindings.check_same_client(session, str(project), name)
        bindings.set_binding(
            session, BindingIn(project=str(project), instance=name, is_default=False)
        )
        admin_audit.record(
            session, "binding.set", f"connected {name}", by="owner", project=str(project),
            instance=name,
        )  # fmt: skip
    return name


def finish(
    session: Session, settings: Settings, found: Pending, state: str, code: str, back: JSON
) -> str:
    """The vendor sent the browser back: store the account and link it. Returns its name."""
    try:
        wanted = provider(found.provider)
        app = _app(settings, found.provider, found.app)
        tokens = _token_request(wanted, app, {
            "grant_type": "authorization_code", "code": code,
            "redirect_uri": found.redirect_uri, "code_verifier": found.verifier,
        })  # fmt: skip
        if found.remote:
            name = _finish_remote(session, settings, found, app, tokens)
            _ended(state, Outcome("done", name=name))
            return name
        refresh = str(tokens.get("refresh_token") or "")
        if not refresh and not wanted.no_refresh_token:
            raise SignInError(f"{wanted.name} didn't give a lasting sign-in. Start it again.")
        if wanted.identify is None:
            raise SignInError(f"Ringkeeper can't tell which {wanted.name} account this is.")
        account, extra = wanted.identify(app, tokens, {**back, "vendor": found.vendor})
        name = _store(session, settings, found, _secret(found, tokens, refresh, extra, account),
                      account)  # fmt: skip
    except (SignInError, RuleViolationError) as failed:
        _ended(state, Outcome("failed", error=str(failed)))
        raise
    _ended(state, Outcome("done", name=name))
    return name


def _finish_remote(
    session: Session, settings: Settings, found: Pending, app: App, tokens: JSON
) -> str:
    """A Meta sign-in for an MCP server: a long-lived user token (about 60 days; Meta gives no
    refresh token) sent to the server as Bearer, and the day it ends on the connection."""
    service = remote.BY_KEY[found.remote]
    lasting = _graph(f"{GRAPH}/oauth/access_token", {
        "grant_type": "fb_exchange_token", "client_id": app.client_id,
        "client_secret": app.client_secret, "fb_exchange_token": tokens["access_token"],
    }, "Meta")  # fmt: skip
    token = str(lasting.get("access_token") or "")
    if not token:
        raise SignInError("Meta didn't give a lasting sign-in. Start it again.")
    who = _graph(f"{GRAPH}/me", {"fields": "id,name", "access_token": token}, "Meta")
    account = str(who.get("name") or who.get("id") or "")
    ends = _ends(lasting)
    secret = json.dumps({
        "url": service.url, "headers": {"Authorization": f"Bearer {token}"}, "expires_at": ends,
    })  # fmt: skip
    new = remote_connections.NewConnection.from_dict(found.connection)
    day = time.strftime("%Y-%m-%d", time.gmtime(ends))
    instance = remote_connections.create(
        session, settings, new, secret, how="signin",
        extra={"account": account, "app": found.app, "expires_at": day},
    )  # fmt: skip
    return instance.name


def refused(state: str, error: str) -> None:
    """The vendor sent the browser back without access (cancelled, or an error)."""
    _ended(state, Outcome("failed", error=error))


# --- Keeping the access token fresh ---


def _parts(value: str) -> JSON:
    try:
        data: Any = json.loads(value)
    except ValueError:
        return {}
    return cast(JSON, data) if isinstance(data, dict) else {}


def _needs_refresh(data: JSON) -> bool:
    expires = data.get("expires_at")
    return not isinstance(expires, int | float) or expires - time.time() < REFRESH_MARGIN_SECONDS


def _renews(data: JSON) -> bool:
    """A token that's renewed, with a month or less to go."""
    wanted = vendors.VENDORS.get(str(data.get("vendor") or ""))
    found = PROVIDERS.get(wanted.provider) if wanted else None
    if found is None or found.renew is None or not data.get("access_token"):
        return False
    return float(data.get("expires_at") or 0) - time.time() < RENEW_MARGIN_SECONDS


def _renewed(vault: Vault, name: str, ref: str) -> Secret:
    """A failed renewal only matters once the token has really ended: until then, it works."""
    with _refresh_locks.setdefault(name, threading.Lock()):
        current = _parts(vault.resolve(ref).reveal())
        if not _renews(current):  # renewed by another call meanwhile
            return Secret(ref, json.dumps(current))
        found = provider(vendors.VENDORS[str(current["vendor"])].provider)
        renew = cast(Callable[[str], JSON], found.renew)
        try:
            tokens = renew(str(current["access_token"]))
        except SignInError:
            if float(current.get("expires_at") or 0) > time.time():
                return Secret(ref, json.dumps(current))
            raise
        renewed = json.dumps({
            **current, "access_token": tokens["access_token"], "expires_at": _ends(tokens),
        })  # fmt: skip
        vault.store(ref, renewed)
        return Secret(ref, renewed)


def resolve(vault: Vault, name: str, ref: str) -> Secret:
    """The connection's secret, its token refreshed and stored first if it's about to end. A
    pasted key has nothing to refresh. Raises SignInError when the vendor won't."""
    secret = vault.resolve(ref)
    data = _parts(secret.reveal())
    if _renews(data):
        return _renewed(vault, name, ref)
    if not data.get("refresh_token") or not _needs_refresh(data):
        return secret
    with _refresh_locks.setdefault(name, threading.Lock()):
        current = _parts(vault.resolve(ref).reveal())
        if not _needs_refresh(current):  # refreshed by another call meanwhile
            return Secret(ref, json.dumps(current))
        wanted = vendors.VENDORS.get(str(current.get("vendor") or ""))
        if wanted is None or not wanted.provider:
            raise SignInError("This connection doesn't say which service it's for.")
        found = provider(wanted.provider)
        app = _app(get_settings(), found.key, str(current.get("app") or "") or None)
        tokens = _token_request(
            found, app,
            {"grant_type": "refresh_token", "refresh_token": str(current["refresh_token"])},
        )  # fmt: skip
        refreshed = json.dumps({
            **current,
            "access_token": tokens["access_token"],
            # Xero and Intuit replace it each time, and the old one stops working.
            "refresh_token": str(tokens.get("refresh_token") or current["refresh_token"]),
            "expires_at": int(time.time() + float(tokens.get("expires_in") or 3600)),
        })  # fmt: skip
        vault.store(ref, refreshed)
        return Secret(ref, refreshed)


def revoke(value: str) -> None:
    """Best effort: tell the vendor this sign-in is no longer used (a removed connection)."""
    data = _parts(value)
    wanted = vendors.VENDORS.get(str(data.get("vendor") or ""))
    if wanted is None or not wanted.provider or not data.get("refresh_token"):
        return
    found = provider(wanted.provider)
    if not found.revoke:
        return
    with contextlib.suppress(RingkeeperError, httpx2.HTTPError), _http() as http:
        app = _app(get_settings(), found.key, str(data.get("app") or "") or None)
        pair = base64.b64encode(f"{app.client_id}:{app.client_secret}".encode()).decode()
        http.post(
            found.revoke, json={"token": data["refresh_token"]},
            headers={"Authorization": f"Basic {pair}"},
        )  # fmt: skip
