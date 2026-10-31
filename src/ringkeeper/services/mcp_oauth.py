"""Signing in to a remote MCP server (MCP's OAuth: discovery, dynamic client
registration, the authorization code flow with PKCE) and keeping its tokens fresh.

1. `start`: find the server's authorization server (RFC 9728, then RFC 8414),
   register Ringkeeper as a client there (RFC 7591), and return the page to
   open. What's needed to finish (the PKCE verifier, the client) stays in this
   process's memory for ten minutes, keyed by the unguessable `state`.
2. The service redirects the browser to /oauth/callback; `finish` exchanges
   the code (with the `resource` indicator, RFC 8707) and stores the tokens in
   the vault as the connection's secret. Nothing is stored before that.
3. `fresh`: before each use, a token close to expiring is refreshed and the
   new one stored. A lock per connection keeps two calls from spending the
   same refresh token (many services rotate it).
"""

import base64
import hashlib
import json
import secrets
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Protocol, cast
from urllib.parse import urlencode, urlsplit

import httpx2

from ringkeeper.errors import RingkeeperError
from ringkeeper.vault import Secret, Vault

PENDING_SECONDS = 600
REFRESH_MARGIN_SECONDS = 120
TIMEOUT = 20.0
# Tests swap in httpx2.MockTransport; production uses the real network.
transport: httpx2.BaseTransport | None = None


class SignInError(RingkeeperError):
    """The service's sign-in didn't work. The message is for the user."""


@dataclass
class Pending:
    url: str
    redirect_uri: str
    token_endpoint: str
    client: dict[str, Any]
    verifier: str
    connection: dict[str, Any]  # name, owner, env and project for the new connection
    created: float = field(default_factory=time.monotonic)


class SecretStore(Protocol):
    """Where a connection's secret lives (the vault), read again under the lock."""

    def current(self) -> str: ...
    def save(self, value: str) -> None: ...


_pending: dict[str, Pending] = {}
_pending_lock = threading.Lock()
_refresh_locks: dict[str, threading.Lock] = {}


def _http() -> httpx2.Client:
    return httpx2.Client(timeout=TIMEOUT, transport=transport, follow_redirects=False)


def _json(response: httpx2.Response) -> dict[str, Any]:
    try:
        data: object = response.json()
    except ValueError:
        return {}
    return cast(dict[str, Any], data) if isinstance(data, dict) else {}


def _well_known(base: str, name: str) -> list[str]:
    """RFC 8414/9728 places: the path-suffixed one first, then the root one."""
    parts = urlsplit(base)
    origin = f"{parts.scheme}://{parts.netloc}"
    path = parts.path.rstrip("/")
    return (
        [f"{origin}/.well-known/{name}{path}", f"{origin}/.well-known/{name}"]
        if path
        else [f"{origin}/.well-known/{name}"]
    )


def _first(http: httpx2.Client, urls: list[str]) -> dict[str, Any]:
    for url in urls:
        try:
            response = http.get(url)
        except httpx2.HTTPError:
            continue
        if response.status_code == 200 and (data := _json(response)):
            return data
    return {}


def discover(url: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """(protected-resource metadata, authorization-server metadata) for an MCP server."""
    with _http() as http:
        resource = _first(http, _well_known(url, "oauth-protected-resource"))
        servers = cast(list[str], resource.get("authorization_servers") or [])
        issuer = servers[0] if servers else f"{urlsplit(url).scheme}://{urlsplit(url).netloc}"
        server = _first(
            http,
            [
                *_well_known(issuer, "oauth-authorization-server"),
                *_well_known(issuer, "openid-configuration"),
            ],
        )
    if not server.get("authorization_endpoint") or not server.get("token_endpoint"):
        raise SignInError("This service doesn't offer sign-in here. Paste a token instead.")
    return resource, server


def needs_nothing(url: str) -> bool:
    """Whether the MCP server answers an initialize with no sign-in, token or header."""
    hello = {
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                   "clientInfo": {"name": "Ringkeeper", "version": "probe"}},
    }  # fmt: skip
    accept = {"Accept": "application/json, text/event-stream"}
    with _http() as http:
        try:
            # Only the status matters; an event stream isn't read.
            with http.stream("POST", url, json=hello, headers=accept) as response:
                return 200 <= response.status_code < 300
        except httpx2.HTTPError:
            return False


def _register(server: dict[str, Any], redirect_uri: str, scope: str | None) -> dict[str, Any]:
    endpoint = server.get("registration_endpoint")
    if not endpoint:
        raise SignInError("This service doesn't let apps register themselves. Paste a token.")
    body: dict[str, Any] = {
        "client_name": "Ringkeeper",
        "redirect_uris": [redirect_uri],
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
    }
    if scope:
        body["scope"] = scope
    with _http() as http:
        try:
            response = http.post(str(endpoint), json=body)
        except httpx2.HTTPError:
            raise SignInError("Couldn't reach the service to sign in. Try again.") from None
    client = _json(response)
    if response.status_code >= 400 or not client.get("client_id"):
        said = client.get("error_description") or client.get("error") or response.status_code
        raise SignInError(f"The service refused to register Ringkeeper ({said}).")
    return client


def _pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    digest = hashlib.sha256(verifier.encode()).digest()
    return verifier, base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def start(url: str, redirect_uri: str, connection: dict[str, Any]) -> str:
    """Begin signing in to the MCP server at `url` (a service from ringkeeper.remote,
    one from the catalog, or one added by its address); returns the page to open."""
    resource, server = discover(url)
    supported = cast(list[str], resource.get("scopes_supported") or [])
    scope = " ".join(supported) or None
    client = _register(server, redirect_uri, scope)
    verifier, challenge = _pkce()
    state = secrets.token_urlsafe(32)
    params = {
        "response_type": "code",
        "client_id": client["client_id"],
        "redirect_uri": redirect_uri,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "resource": str(resource.get("resource") or url),
    }
    if scope:
        params["scope"] = scope
    with _pending_lock:
        _forget_old()
        _pending[state] = Pending(
            url=url,
            redirect_uri=redirect_uri,
            token_endpoint=str(server["token_endpoint"]),
            client=client,
            verifier=verifier,
            connection=connection,
        )
    separator = "&" if "?" in str(server["authorization_endpoint"]) else "?"
    return f"{server['authorization_endpoint']}{separator}{urlencode(params)}"


def _forget_old() -> None:
    now = time.monotonic()
    for state in [s for s, p in _pending.items() if now - p.created > PENDING_SECONDS]:
        del _pending[state]


def take(state: str) -> Pending:
    """The sign-in this callback belongs to (once only)."""
    with _pending_lock:
        _forget_old()
        pending = _pending.pop(state, None)
    if pending is None:
        raise SignInError("This sign-in has expired or was already used. Start it again.")
    return pending


def _token_request(endpoint: str, client: dict[str, Any], form: dict[str, str]) -> dict[str, Any]:
    form = {**form, "client_id": str(client["client_id"])}
    extra: dict[str, Any] = {}
    if secret := client.get("client_secret"):
        if client.get("token_endpoint_auth_method") == "client_secret_basic":
            extra["auth"] = (str(client["client_id"]), str(secret))
        else:
            form["client_secret"] = str(secret)
    with _http() as http:
        try:
            response = http.post(endpoint, data=form, **extra)
        except httpx2.HTTPError:
            raise SignInError("Couldn't reach the service's sign-in. Try again.") from None
    tokens = _json(response)
    if response.status_code >= 400 or not tokens.get("access_token"):
        said = tokens.get("error_description") or tokens.get("error") or response.status_code
        raise SignInError(f"The service didn't give Ringkeeper access ({said}).")
    return tokens


def _secret(pending_or_old: dict[str, Any], tokens: dict[str, Any]) -> str:
    expires_in = tokens.get("expires_in")
    oauth = {
        **pending_or_old,
        "access_token": tokens["access_token"],
        "refresh_token": tokens.get("refresh_token") or pending_or_old.get("refresh_token"),
        "expires_at": int(time.time() + float(expires_in)) if expires_in else None,
    }
    return oauth_secret(oauth)


def oauth_secret(oauth: dict[str, Any]) -> str:
    return json.dumps({"url": oauth["url"], "oauth": oauth})


def finish(pending: Pending, code: str) -> str:
    """Exchange the code for tokens; returns the connection's secret (JSON)."""
    tokens = _token_request(
        pending.token_endpoint,
        pending.client,
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": pending.redirect_uri,
            "code_verifier": pending.verifier,
            "resource": pending.url,
        },
    )
    kept = {
        "url": pending.url,
        "token_endpoint": pending.token_endpoint,
        "client": {k: v for k, v in pending.client.items() if k in CLIENT_KEYS},
    }
    return _secret(kept, tokens)


CLIENT_KEYS = ("client_id", "client_secret", "token_endpoint_auth_method")


def needs_refresh(oauth: dict[str, Any]) -> bool:
    expires = oauth.get("expires_at")
    return (
        bool(oauth.get("refresh_token"))
        and isinstance(expires, int | float)
        and (expires - time.time() < REFRESH_MARGIN_SECONDS)
    )


def fresh(name: str, value: str, store: SecretStore) -> str:
    """The connection's secret with a usable access token: refreshed (and stored
    with `store(new_value)`) if it's about to expire. Other secrets pass through."""
    try:
        spec: object = json.loads(value)
    except ValueError:
        return value
    found = cast(dict[str, Any], spec).get("oauth") if isinstance(spec, dict) else None
    if not isinstance(found, dict):
        return value
    oauth = cast(dict[str, Any], found)
    if not needs_refresh(oauth):
        return value
    with _refresh_locks.setdefault(name, threading.Lock()):
        current: dict[str, Any] = json.loads(store.current())["oauth"]
        if not needs_refresh(current):  # another call refreshed it meanwhile
            return oauth_secret(current)
        tokens = _token_request(
            str(current["token_endpoint"]),
            cast(dict[str, Any], current["client"]),
            {
                "grant_type": "refresh_token",
                "refresh_token": str(current["refresh_token"]),
                "resource": str(current["url"]),
            },
        )
        updated = _secret(current, tokens)
        store.save(updated)
        return updated


class _VaultStore:
    def __init__(self, vault: Vault, ref: str) -> None:
        self.vault, self.ref = vault, ref

    def current(self) -> str:
        return self.vault.resolve(self.ref).reveal()

    def save(self, value: str) -> None:
        self.vault.store(self.ref, value)


def resolve(vault: Vault, name: str, ref: str) -> Secret:
    """A connection's secret, with a signed-in token refreshed first if it's about to
    expire. Raises SignInError when the service won't refresh it (sign in again)."""
    secret = vault.resolve(ref)
    value = fresh(name, secret.reveal(), _VaultStore(vault, ref))
    return secret if value == secret.reveal() else Secret(ref, value)
