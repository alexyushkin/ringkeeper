"""Google accounts through the owner's own Google app (an OAuth client in their own
Google Cloud project, Settings > Google).

The app (its client ID and secret) is stored once, in the vault. Each Google
account is its own connection (type `google`), with the scopes chosen for it:

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
APP_REF = "file://ringkeeper/google-app"
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
    )
}  # fmt: skip
IDENTITY = ("openid", "email")


class SignInError(mcp_oauth.SignInError):
    """Google's sign-in didn't work. The message is for the user (and, like an MCP
    sign-in's, tells the gateway the connection needs signing in again)."""


@dataclass(frozen=True)
class App:
    client_id: str
    client_secret: str


@dataclass
class Pending:
    redirect_uri: str
    verifier: str
    scopes: list[str]  # access keys
    connection: dict[str, Any]  # what to create (or `replace`), as the start asked
    created: float = field(default_factory=time.monotonic)


_pending: dict[str, Pending] = {}
_pending_lock = threading.Lock()
_refresh_locks: dict[str, threading.Lock] = {}


def _http() -> httpx2.Client:
    return httpx2.Client(timeout=TIMEOUT, transport=transport, follow_redirects=False)


# --- The owner's Google app ---


def save_app(settings: Settings, client_id: str, client_secret: str) -> App:
    client_id, client_secret = client_id.strip(), "".join(client_secret.split())
    if not client_id.endswith(".apps.googleusercontent.com"):
        raise RuleViolationError(
            "That isn't a Google OAuth client ID: it ends with .apps.googleusercontent.com."
        )
    if not client_secret:
        raise RuleViolationError("Paste the client secret too (Google shows it only once).")
    app = App(client_id, client_secret)
    get_vault(settings).store(APP_REF, json.dumps(app.__dict__))
    return app


def load_app(settings: Settings) -> App | None:
    try:
        value = get_vault(settings).resolve(APP_REF).reveal()
    except RingkeeperError:
        return None
    data = json.loads(value)
    return App(str(data["client_id"]), str(data["client_secret"]))


def forget_app(settings: Settings) -> None:
    get_vault(settings).delete(APP_REF)


def _app(settings: Settings) -> App:
    app = load_app(settings)
    if app is None:
        raise SignInError("Set up your Google app first (Settings > Google).")
    return app


# --- Signing in ---


def scopes_for(keys: list[str]) -> list[str]:
    unknown = [k for k in keys if k not in ACCESS]
    if unknown or not keys:
        raise RuleViolationError("Choose what this Google account may be used for.")
    return [*IDENTITY, *dict.fromkeys(ACCESS[k].scope for k in keys)]


def start(
    settings: Settings, redirect_uri: str, keys: list[str], connection: dict[str, Any]
) -> str:
    """Google's consent page for these accesses; the connection is made on the way back."""
    app = _app(settings)
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
        _pending[state] = Pending(redirect_uri, verifier, list(keys), connection)
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
                "Settings > Google."
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
    app = _app(settings)
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
    return _secret(email, tokens, str(tokens["refresh_token"])), email, granted


def _secret(email: str, tokens: dict[str, Any], refresh_token: str) -> str:
    expires_in = float(tokens.get("expires_in") or 3600)
    return json.dumps(
        {
            "email": email,
            "refresh_token": refresh_token,
            "access_token": tokens["access_token"],
            "expires_at": int(time.time() + expires_in),
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
            tokens = _token_request(
                _app(settings),
                {"grant_type": "refresh_token", "refresh_token": str(current["refresh_token"])},
            )
            refreshed = _secret(
                str(current["email"]),
                tokens,
                str(tokens.get("refresh_token") or current["refresh_token"]),
            )
            vault.store(ref, refreshed)
            return Secret(ref, refreshed)
        return Secret(ref, json.dumps(current))


def connection_secret(vault: Vault, kind: InstanceType, name: str, ref: str) -> Secret:
    """Any connection's secret, a signed-in one (Google, or an MCP server's) refreshed
    first if it's about to expire."""
    if kind is InstanceType.GOOGLE:
        return resolve(get_settings(), vault, name, ref)
    return mcp_oauth.resolve(vault, name, ref)


def revoke(value: str) -> None:
    """Best effort: tell Google this sign-in is no longer used (a removed connection)."""
    try:
        token = str(parts(value)["refresh_token"])
    except SignInError:
        return
    with _http() as http, contextlib.suppress(httpx2.HTTPError):
        http.post(REVOKE, data={"token": token})
