"""Google accounts through the owner's own Google app (an OAuth client in their own
Google Cloud project, Settings > Developer apps).

The app (its client ID and secret) is stored once, in the vault. There may be
more than one: a client's own Google Cloud project (a Workspace admin who only
trusts their own app), or another to get past the 100 accounts Google allows an
unverified app. Each Google account is its own connection (type `google`),
signed in through one of the apps (the first, unless it chose another), with
the scopes chosen for it:

1. `start`: the consent page to open, with PKCE and a one-time `state`; what's
   needed to finish stays in this process's memory for ten minutes. Desktop
   apps use a "Desktop app" client, which takes any loopback redirect, so the
   app's own address works as it is (http://127.0.0.1:<port>/oauth/callback).
2. Google sends the browser back to /oauth/callback; `finish` exchanges the
   code, reads which account it is, and returns the connection's secret: the
   refresh token, the current access token, and the scopes Google granted
   (the consent screen lets people untick some).
3. `fresh`: before each use, an access token close to expiring is refreshed and
   stored, under a lock per connection. A refresh Google refuses (a password
   change, access removed, six months unused) says to sign in again.
"""

import base64
import contextlib
import hashlib
import json
import re
import secrets
import threading
import time
from dataclasses import dataclass, field
from typing import Any, cast
from urllib.parse import urlencode

import httpx2

from ringkeeper.config import Settings, get_settings
from ringkeeper.errors import RingkeeperError, RuleViolationError
from ringkeeper.models import InstanceType
from ringkeeper.services import mcp_oauth
from ringkeeper.vault import Secret, Vault, get_vault

AUTHORIZE = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN = "https://oauth2.googleapis.com/token"  # noqa: S105 (a URL)
USERINFO = "https://openidconnect.googleapis.com/v1/userinfo"
REVOKE = "https://oauth2.googleapis.com/revoke"
APP_REF = "file://ringkeeper/google-app"  # every app, as {"apps": [...]}
FIRST_APP = "google-app"  # the id of the one app there used to be
PENDING_SECONDS = 600
REFRESH_MARGIN_SECONDS = 120
TIMEOUT = 20.0
# Tests swap in httpx2.MockTransport; production talks to Google.
transport: httpx2.BaseTransport | None = None

G = "https://www.googleapis.com/auth/"


@dataclass(frozen=True)
class Access:
    """One thing a connection may be allowed to do, and the Google scope it needs."""

    key: str
    label: str
    scope: str
    note: str = ""


# What the owner chooses per connection. Narrower scopes where they cover the
# need: drive.file sees only files Ringkeeper made or was given; "read all
# files" is the restricted drive.readonly, asked for only when chosen.
ACCESS = {
    a.key: a
    for a in (
        Access("mail_read", "Read mail", G + "gmail.readonly"),
        Access("mail_send", "Send mail", G + "gmail.send"),
        Access("mail_drafts", "Write drafts", G + "gmail.compose"),
        Access("mail_trash", "Move mail to the trash", G + "gmail.modify"),
        Access("drive_files", "Drive: files Ringkeeper creates or is given", G + "drive.file"),
        Access(
            "drive_read_all", "Drive: read all files", G + "drive.readonly",
            "A restricted scope: Google shows the unverified-app warning for it.",
        ),
        Access("calendar_read", "Calendar: read events", G + "calendar.events.readonly"),
        Access("calendar_write", "Calendar: add and change events", G + "calendar.events"),
        Access("sheets_read", "Sheets: read", G + "spreadsheets.readonly"),
        Access("sheets_write", "Sheets: read and edit", G + "spreadsheets"),
        Access("search_console_read", "Search Console: read", G + "webmasters.readonly"),
        Access("analytics_read", "Analytics: read", G + "analytics.readonly"),
        Access("youtube_read", "YouTube: read videos and comments", G + "youtube.readonly"),
        Access("youtube_upload", "YouTube: upload videos", G + "youtube.upload"),
        Access(
            "youtube_manage", "YouTube: edit and delete videos, reply to comments",
            G + "youtube.force-ssl",
        ),
        Access("youtube_analytics", "YouTube: read analytics", G + "yt-analytics.readonly"),
    )
}  # fmt: skip
IDENTITY = ("openid", "email")


class SignInError(mcp_oauth.SignInError):
    """Google's sign-in didn't work. The message is for the user (and, like an MCP
    sign-in's, tells the gateway the connection needs signing in again)."""


@dataclass(frozen=True)
class App:
    id: str
    name: str
    client_id: str
    client_secret: str


@dataclass
class Pending:
    redirect_uri: str
    verifier: str
    scopes: list[str]  # access keys
    connection: dict[str, Any]  # what to create (or `replace`), as the start asked
    app: str  # the id of the app it signs in through
    created: float = field(default_factory=time.monotonic)


_pending: dict[str, Pending] = {}
_pending_lock = threading.Lock()
_refresh_locks: dict[str, threading.Lock] = {}


def _http() -> httpx2.Client:
    return httpx2.Client(timeout=TIMEOUT, transport=transport, follow_redirects=False)


# --- The owner's Google apps ---


def list_apps(settings: Settings) -> list[App]:
    try:
        value = get_vault(settings).resolve(APP_REF).reveal()
    except RingkeeperError:
        return []
    data = json.loads(value)
    if "apps" not in data:  # stored before there could be more than one
        data = {"apps": [{"id": FIRST_APP, "name": "Your Google app", **data}]}
    return [
        App(str(a["id"]), str(a["name"]), str(a["client_id"]), str(a["client_secret"]))
        for a in data["apps"]
    ]


def _store_apps(settings: Settings, apps: list[App]) -> None:
    if apps:
        get_vault(settings).store(APP_REF, json.dumps({"apps": [a.__dict__ for a in apps]}))
    else:
        get_vault(settings).delete(APP_REF)


def load_app(settings: Settings, app_id: str | None = None) -> App | None:
    """That app, or (no id) the first: the one an account uses unless it chose another."""
    apps = list_apps(settings)
    if app_id is None:
        return apps[0] if apps else None
    return next((a for a in apps if a.id == app_id), None)


def _credentials(client_id: str, client_secret: str) -> tuple[str, str]:
    client_id, client_secret = client_id.strip(), "".join(client_secret.split())
    if not client_id.endswith(".apps.googleusercontent.com"):
        raise RuleViolationError(
            "That isn't a Google OAuth client ID: it ends with .apps.googleusercontent.com."
        )
    if not client_secret:
        raise RuleViolationError("Paste the client secret too (Google shows it only once).")
    return client_id, client_secret


def save_app(
    settings: Settings,
    client_id: str,
    client_secret: str,
    app_id: str | None = None,
    name: str | None = None,
) -> App:
    """Replace an app's client (and name), or (no id) the first app's, adding it if there's
    none yet."""
    client_id, client_secret = _credentials(client_id, client_secret)
    apps = list_apps(settings)
    if app_id is None and not apps:
        return add_app(settings, name or "Your Google app", client_id, client_secret)
    target = app_id or apps[0].id
    current = load_app(settings, target)
    if current is None:
        raise RuleViolationError(f"There's no Google app '{target}'.")
    _check_unused(apps, client_id, but=target)
    app = App(target, (name or "").strip() or current.name, client_id, client_secret)
    _store_apps(settings, [app if a.id == target else a for a in apps])
    return app


def add_app(settings: Settings, name: str, client_id: str, client_secret: str) -> App:
    client_id, client_secret = _credentials(client_id, client_secret)
    name = name.strip()
    if not name:
        raise RuleViolationError("Name this Google app (say, whose Google Cloud project it is).")
    apps = list_apps(settings)
    _check_unused(apps, client_id)
    base = FIRST_APP if not apps else re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    base = base[:40].strip("-") or FIRST_APP
    app_id, n = base, 2
    while any(a.id == app_id for a in apps):
        app_id, n = f"{base}-{n}", n + 1
    app = App(app_id, name, client_id, client_secret)
    _store_apps(settings, [*apps, app])
    return app


def _check_unused(apps: list[App], client_id: str, but: str | None = None) -> None:
    for a in apps:
        if a.client_id == client_id and a.id != but:
            raise RuleViolationError(f"That client is already set up, as '{a.name}'.")


def forget_app(settings: Settings, app_id: str | None = None) -> None:
    """Remove that app (no id: the first). Accounts that signed in through it stop
    working at their next refresh."""
    apps = list_apps(settings)
    target = app_id or (apps[0].id if apps else None)
    if target is None or not any(a.id == target for a in apps):
        raise RuleViolationError(f"There's no Google app '{target}'.")
    _store_apps(settings, [a for a in apps if a.id != target])


def _app(settings: Settings, app_id: str | None = None) -> App:
    app = load_app(settings, app_id)
    if app is None and app_id is not None and list_apps(settings):
        raise SignInError(
            "The Google app this account signed in through was removed (Settings > Developer "
            "apps). Sign it in again through another one."
        )
    if app is None:
        raise SignInError("Set up your Google app first (Settings > Developer apps).")
    return app


# --- Signing in ---


def scopes_for(keys: list[str]) -> list[str]:
    unknown = [k for k in keys if k not in ACCESS]
    if unknown or not keys:
        raise RuleViolationError("Choose what this Google account may be used for.")
    return [*IDENTITY, *dict.fromkeys(ACCESS[k].scope for k in keys)]


def start(
    settings: Settings,
    redirect_uri: str,
    keys: list[str],
    connection: dict[str, Any],
    app_id: str | None = None,
) -> str:
    """Google's consent page for these accesses, through that app (no id: the first);
    the connection is made on the way back."""
    app = _app(settings, app_id)
    scope = " ".join(scopes_for(keys))
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
    state = secrets.token_urlsafe(32)
    params = {
        "client_id": app.client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": scope,
        "state": state,
        "code_challenge": challenge.rstrip(b"=").decode(),
        "code_challenge_method": "S256",
        "access_type": "offline",  # a refresh token
        "prompt": "consent select_account",  # a refresh token every time, and the right account
    }
    if hint := connection.get("login_hint"):
        params["login_hint"] = str(hint)
    with _pending_lock:
        _forget_old()
        _pending[state] = Pending(redirect_uri, verifier, list(keys), connection, app.id)
    return f"{AUTHORIZE}?{urlencode(params)}"


def _forget_old() -> None:
    now = time.monotonic()
    for state in [s for s, p in _pending.items() if now - p.created > PENDING_SECONDS]:
        del _pending[state]


def pending(state: str) -> bool:
    """Whether this callback is a Google sign-in this process started."""
    with _pending_lock:
        return state in _pending


def take(state: str) -> Pending:
    with _pending_lock:
        _forget_old()
        found = _pending.pop(state, None)
    if found is None:
        raise SignInError("This sign-in has expired or was already used. Start it again.")
    return found


def _token_request(app: App, form: dict[str, str]) -> dict[str, Any]:
    form = {**form, "client_id": app.client_id, "client_secret": app.client_secret}
    with _http() as http:
        try:
            response = http.post(TOKEN, data=form)
        except httpx2.HTTPError:
            raise SignInError("Couldn't reach Google. Check this computer is online.") from None
    try:
        tokens = cast(dict[str, Any], response.json())
    except ValueError:
        tokens = {}
    if response.status_code >= 400 or not tokens.get("access_token"):
        error = str(tokens.get("error") or response.status_code)
        if error == "invalid_grant":
            raise SignInError(
                "Google no longer accepts this sign-in (a password change, access removed in "
                "the Google account, or six months unused). Sign in again."
            )
        if error in ("invalid_client", "unauthorized_client"):
            raise SignInError(
                "Google didn't accept your app's client ID or secret. Check them in "
                "Settings > Developer apps."
            )
        said = tokens.get("error_description") or error
        raise SignInError(f"Google didn't give Ringkeeper access ({said}).")
    return tokens


def _email(access_token: str) -> str:
    with _http() as http:
        try:
            response = http.get(USERINFO, headers={"Authorization": f"Bearer {access_token}"})
            data = cast(dict[str, Any], response.json())
        except (httpx2.HTTPError, ValueError):
            raise SignInError("Couldn't ask Google which account this is. Try again.") from None
    email = str(data.get("email") or "")
    if response.status_code != 200 or not email:
        raise SignInError("Google didn't say which account this is. Try again.")
    return email.lower()


def finish(settings: Settings, found: Pending, code: str) -> tuple[str, str, list[str]]:
    """(secret, the account's address, the accesses granted). An access whose scope was
    unticked on the consent screen isn't granted; none at all is an error."""
    app = _app(settings, found.app)
    tokens = _token_request(
        app,
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": found.redirect_uri,
            "code_verifier": found.verifier,
        },
    )
    if not tokens.get("refresh_token"):
        raise SignInError("Google didn't give a lasting sign-in. Start it again.")
    got = set(str(tokens.get("scope") or "").split())
    granted = [k for k in found.scopes if ACCESS[k].scope in got]
    if not granted:
        raise SignInError(
            "Nothing was allowed on Google's page: tick what Ringkeeper may use and try again."
        )
    email = _email(str(tokens["access_token"]))
    return _secret(email, tokens, str(tokens["refresh_token"]), app.id), email, granted


def _secret(email: str, tokens: dict[str, Any], refresh_token: str, app_id: str) -> str:
    """A refresh token works only with the app that got it, so the secret says which."""
    expires_in = float(tokens.get("expires_in") or 3600)
    return json.dumps(
        {
            "email": email,
            "refresh_token": refresh_token,
            "access_token": tokens["access_token"],
            "expires_at": int(time.time() + expires_in),
            "app": app_id,
        }
    )


def parts(value: str) -> dict[str, Any]:
    try:
        data: Any = json.loads(value)
    except ValueError:
        raise SignInError("This Google connection's secret isn't in the expected form.") from None
    if not isinstance(data, dict) or not cast(dict[str, Any], data).get("refresh_token"):
        raise SignInError("This Google connection's secret isn't in the expected form.")
    return cast(dict[str, Any], data)


def secret_fragments(value: str) -> list[str]:
    try:
        data = parts(value)
    except SignInError:
        return []
    return [str(data["refresh_token"]), str(data.get("access_token") or "")]


# --- Keeping the access token fresh ---


def _needs_refresh(data: dict[str, Any]) -> bool:
    expires = data.get("expires_at")
    return not isinstance(expires, int | float) or expires - time.time() < REFRESH_MARGIN_SECONDS


def resolve(settings: Settings, vault: Vault, name: str, ref: str) -> Secret:
    """The connection's secret with a usable access token (refreshed and stored first if
    it's about to expire). Raises SignInError when Google won't refresh it."""
    secret = vault.resolve(ref)
    if not _needs_refresh(parts(secret.reveal())):
        return secret
    with _refresh_locks.setdefault(name, threading.Lock()):
        current = parts(vault.resolve(ref).reveal())
        if _needs_refresh(current):  # not refreshed by another call meanwhile
            # From before there could be more than one app: the first.
            app = _app(settings, str(current["app"]) if current.get("app") else None)
            tokens = _token_request(
                app,
                {"grant_type": "refresh_token", "refresh_token": str(current["refresh_token"])},
            )
            refreshed = _secret(
                str(current["email"]),
                tokens,
                str(tokens.get("refresh_token") or current["refresh_token"]),
                app.id,
            )
            vault.store(ref, refreshed)
            return Secret(ref, refreshed)
        return Secret(ref, json.dumps(current))


def connection_secret(vault: Vault, kind: InstanceType, name: str, ref: str) -> Secret:
    """Any connection's secret, a signed-in one (Google, or an MCP server's) refreshed
    first if it's about to expire."""
    if kind is InstanceType.GOOGLE:
        return resolve(get_settings(), vault, name, ref)
    if kind is InstanceType.VENDOR:
        from ringkeeper.services import developer_apps  # noqa: PLC0415 (it needs instances)

        return developer_apps.resolve(vault, name, ref)
    return mcp_oauth.resolve(vault, name, ref)


def revoke(value: str) -> None:
    """Best effort: tell Google this sign-in is no longer used (a removed connection)."""
    try:
        token = str(parts(value)["refresh_token"])
    except SignInError:
        return
    with _http() as http, contextlib.suppress(httpx2.HTTPError):
        http.post(REVOKE, data={"token": token})
