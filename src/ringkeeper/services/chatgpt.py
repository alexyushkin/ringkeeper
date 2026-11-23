"""Your ChatGPT plan, through OpenAI's Sign in with ChatGPT: the desktop app only.

OpenAI lets open-source apps that run on the user's own computer use the user's ChatGPT plan,
with no API key and no client secret (developers.openai.com/siwc). Usage comes out of the
plan's own limits, and the user can set a limit for Ringkeeper in ChatGPT's settings. The
app never sees the user's chats.

1. `start`: OpenAI's sign-in page, with PKCE, a `state` and a `nonce`. The first sign-in
   registers this install (client `dynamic_agent_client`, our name, and a host ID made once);
   OpenAI's answer names the client ID to use from then on. The browser comes back to the
   app's own address, http://127.0.0.1:<port>/oauth/callback: only the port may change
   between sign-ins, and OpenAI wants 127.0.0.1, not localhost.
2. `finish`: the code for tokens. The ID token is checked against OpenAI's keys, and the
   grant must include plan usage (`chatgpt.tokens.use.direct`).
3. `access_token`: an access token lasts an hour. It's refreshed first when it's close to
   expiring, under a database lock, since each refresh token works once: the router and the
   app must never both spend it.
4. `sign_out`: the refresh token is revoked, and the tokens are deleted. The host ID and
   client ID stay, as OpenAI asks.

Requests go to POST /v1/responses with `store` false and `stream` true, never to ChatGPT's
own backend. Model IDs from this plan are written `chatgpt:<id>` wherever a model is chosen
(`is_plan_model`), so a role can be on either one.
"""

import base64
import contextlib
import hashlib
import json
import secrets
import threading
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any, cast
from urllib.parse import urlencode

import httpx2
import jwt
from sqlalchemy.orm import Session

from ringkeeper import claude_failures
from ringkeeper.claude_failures import Failure, Kind
from ringkeeper.errors import RingkeeperError
from ringkeeper.models import Setting
from ringkeeper.scrub import register
from ringkeeper.services import mcp_oauth
from ringkeeper.storage import sql
from ringkeeper.vault import Vault

AUTH = "https://auth.openai.com"
AUTHORIZE = f"{AUTH}/api/accounts/authorize"
TOKEN = f"{AUTH}/api/accounts/oauth/token"
DISCOVERY = f"{AUTH}/.well-known/openid-configuration"
API = "https://api.openai.com/v1"
RESOURCE = API
PLAN_SCOPE = "chatgpt.tokens.use.direct"
SCOPES = f"openid profile email offline_access resource.invoke {PLAN_SCOPE}"
FIRST_CLIENT = "dynamic_agent_client"
APP_NAME = "Ringkeeper"
USAGE_URL = "https://chatgpt.com/settings/usage"
REF = "file://ringkeeper/chatgpt-plan"
STATE_KEY = "chatgpt"  # a Setting: host and client IDs, the account. No tokens.
PREFIX = "chatgpt:"
# The roles that may run on the plan for now: the router's model also answers in Help. Tasks
# need a second engine (docs/MODEL_PROVIDERS.md) and come later.
ROLES = ("router",)
PENDING_SECONDS = 600
REFRESH_MARGIN_SECONDS = 300
TIMEOUT = 20.0
ANSWER_TIMEOUT = 120.0
LOCK_KEY = 0x63686174  # "chat": one refresh at a time, across processes
# Refresh errors after which the sign-in is gone: sign in again.
SIGNED_OUT = frozenset({
    "invalid_grant", "invalid_refresh_token", "token_expired", "refresh_token_expired",
    "refresh_token_invalidated", "refresh_token_reused",
})  # fmt: skip
NOTICE = "Eligible usage in this app uses your ChatGPT plan. Manage usage in your ChatGPT settings."
# Tests swap in httpx2.MockTransport; production talks to OpenAI.
transport: httpx2.BaseTransport | None = None


class SignInError(mcp_oauth.SignInError):
    """OpenAI's sign-in didn't work, or no longer does. The message is for the user."""


class PlanError(RingkeeperError):
    """A request on the plan failed. `failure` says what kind, as Claude's failures do, so
    the router waits and tries again the same way."""

    def __init__(self, failure: Failure) -> None:
        super().__init__(failure.message)
        self.failure = failure


@dataclass
class Pending:
    redirect_uri: str
    verifier: str
    nonce: str
    client_id: str  # the one the sign-in started with: dynamic_agent_client the first time
    created: float = field(default_factory=time.monotonic)


_pending: dict[str, Pending] = {}
_pending_lock = threading.Lock()
_refresh_lock = threading.Lock()  # within one process; the database lock covers the others


def _http(timeout: float = TIMEOUT) -> httpx2.Client:
    return httpx2.Client(timeout=timeout, transport=transport, follow_redirects=False)


def available(settings: Any) -> bool:
    """Only the desktop app: OpenAI offers plan usage to apps on the user's own computer, and
    its sign-in comes back to a 127.0.0.1 address. (Tests swap this.)"""
    return getattr(settings, "mode", "") == "desktop"


def is_plan_model(model: str | None) -> bool:
    return bool(model) and str(model).startswith(PREFIX)


def bare(model: str) -> str:
    """The model's ID as OpenAI names it."""
    return model.removeprefix(PREFIX)


# --- What's kept: in a Setting (not secret) and the vault (the tokens) ---


def _state(session: Session) -> dict[str, Any]:
    row = session.get(Setting, STATE_KEY)
    return dict(row.value) if row else {}


def _keep(session: Session, value: dict[str, Any]) -> None:
    row = session.get(Setting, STATE_KEY)
    if row is None:
        session.add(Setting(key=STATE_KEY, value=value))
    else:
        row.value = value
    session.flush()


def host_id(session: Session) -> str:
    """This install's host ID, made once: a UUIDv4 as OpenAI asks. It isn't a credential."""
    state = _state(session)
    if not state.get("host_id"):
        state["host_id"] = f"urn:uuid:{uuid.uuid4()}"
        _keep(session, state)
    return str(state["host_id"])


def stored(vault: Vault) -> dict[str, Any] | None:
    """The saved sign-in, or None when there's none."""
    return _tokens(vault)


def _tokens(vault: Vault) -> dict[str, Any] | None:
    try:
        data: Any = json.loads(vault.resolve(REF).reveal())
    except (RingkeeperError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    found = cast(dict[str, Any], data)
    return found if found.get("refresh_token") else None


def _store(vault: Vault, tokens: dict[str, Any], previous: dict[str, Any] | None = None) -> None:
    kept: dict[str, Any] = {
        "access_token": str(tokens["access_token"]),
        "refresh_token": str(tokens.get("refresh_token") or (previous or {})["refresh_token"]),
        "id_token": str(tokens.get("id_token") or (previous or {}).get("id_token") or ""),
        "scope": str(tokens.get("scope") or (previous or {}).get("scope") or ""),
        "expires_at": int(time.time() + float(tokens.get("expires_in") or 3600)),
    }
    register(str(kept["access_token"]), str(kept["refresh_token"]), str(kept["id_token"]))
    vault.store(REF, json.dumps(kept))


def status(session: Session, vault: Vault) -> dict[str, Any]:
    """For Settings: whether it's signed in, as whom, and whether the notice was seen."""
    state = _state(session)
    signed_in = _tokens(vault) is not None
    return {
        "signed_in": signed_in,
        "email": state.get("email") if signed_in else None,
        "noticed": bool(state.get("noticed")),
        "usage_url": USAGE_URL,
        "notice": NOTICE,
    }


def noticed(session: Session) -> None:
    """The first-use notice was seen ("Got it")."""
    _keep(session, {**_state(session), "noticed": True})


# --- Signing in ---


def _pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    digest = hashlib.sha256(verifier.encode()).digest()
    return verifier, base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def start(session: Session, redirect_uri: str) -> str:
    """OpenAI's sign-in page. The first time, it also registers this install."""
    state = _state(session)
    client = str(state.get("client_id") or FIRST_CLIENT)
    verifier, challenge = _pkce()
    nonce, key = secrets.token_urlsafe(24), secrets.token_urlsafe(32)
    params = {
        "client_id": client,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "scope": SCOPES,
        "resource": RESOURCE,
        "state": key,
        "nonce": nonce,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "ext_agent_host_id": host_id(session),
    }
    if client == FIRST_CLIENT:
        params["agent_name_hint"] = APP_NAME
    with _pending_lock:
        _forget_old()
        _pending[key] = Pending(redirect_uri, verifier, nonce, client)
    return f"{AUTHORIZE}?{urlencode(params)}"


def _forget_old() -> None:
    now = time.monotonic()
    for key in [k for k, p in _pending.items() if now - p.created > PENDING_SECONDS]:
        del _pending[key]


def pending(state: str) -> bool:
    """Whether this callback is a ChatGPT sign-in this process started."""
    with _pending_lock:
        return state in _pending


def take(state: str) -> Pending:
    with _pending_lock:
        _forget_old()
        found = _pending.pop(state, None)
    if found is None:
        raise SignInError("This sign-in has expired or was already used. Start it again.")
    return found


def _post(url: str, form: dict[str, str]) -> tuple[int, dict[str, Any]]:
    with _http() as http:
        try:
            response = http.post(url, data=form)
        except httpx2.HTTPError:
            raise SignInError("Couldn't reach OpenAI. Check this computer is online.") from None
    try:
        body: Any = response.json() if response.content else {}
    except ValueError:
        body = {}
    return response.status_code, cast(dict[str, Any], body) if isinstance(body, dict) else {}


def _error(body: dict[str, Any], status: int) -> str:
    error = body.get("error")
    if isinstance(error, dict):
        return str(cast(dict[str, Any], error).get("code") or status)
    return str(error or body.get("detail") or status)


def _discovery() -> dict[str, Any]:
    with _http() as http:
        try:
            return cast(dict[str, Any], http.get(DISCOVERY).raise_for_status().json())
        except (httpx2.HTTPError, ValueError):
            raise SignInError("Couldn't reach OpenAI's sign-in service. Try again.") from None


def _identity(id_token: str, client_id: str, nonce: str) -> dict[str, Any]:
    """The ID token's claims, once its signature, issuer, audience, expiry and nonce check out."""
    found = _discovery()
    with _http() as http:
        try:
            keys = cast(dict[str, Any], http.get(str(found["jwks_uri"])).raise_for_status().json())
        except (httpx2.HTTPError, ValueError, KeyError):
            raise SignInError("Couldn't check the sign-in with OpenAI. Try again.") from None
    try:
        header = jwt.get_unverified_header(id_token)
        key = next(k for k in jwt.PyJWKSet.from_dict(keys).keys if k.key_id == header.get("kid"))
        claims: dict[str, Any] = jwt.decode(
            id_token, key.key, algorithms=[str(header.get("alg") or "RS256")],
            audience=client_id, issuer=str(found.get("issuer") or AUTH),
        )  # fmt: skip
    except (jwt.PyJWTError, StopIteration):
        raise SignInError("OpenAI's answer didn't check out. Start the sign-in again.") from None
    if claims.get("nonce") != nonce or not claims.get("sub"):
        raise SignInError("OpenAI's answer didn't check out. Start the sign-in again.")
    return claims


def finish(
    session: Session, vault: Vault, found: Pending, code: str, client_id: str, error: str
) -> str:
    """Tokens for the code, kept in the vault. Returns the account's address, for the page."""
    if error:
        said = "You chose not to allow it" if error == "access_denied" else f"OpenAI said: {error}"
        raise SignInError(f"{said[:200]}. Nothing was changed.")
    saved = _state(session)
    issued = client_id or (found.client_id if found.client_id != FIRST_CLIENT else "")
    if not issued:
        raise SignInError("OpenAI didn't finish registering Ringkeeper. Start the sign-in again.")
    if saved.get("client_id") and issued != saved["client_id"]:
        raise SignInError("OpenAI answered for a different app. Start the sign-in again.")
    status, tokens = _post(TOKEN, {
        "grant_type": "authorization_code", "client_id": issued, "code": code,
        "code_verifier": found.verifier, "redirect_uri": found.redirect_uri, "resource": RESOURCE,
    })  # fmt: skip
    if status >= 400 or not tokens.get("access_token") or not tokens.get("refresh_token"):
        raise SignInError(
            f"OpenAI didn't sign Ringkeeper in ({_error(tokens, status)}). Try again."
        )
    if PLAN_SCOPE not in str(tokens.get("scope") or "").split():
        raise SignInError(
            "Signed in, but using your ChatGPT plan wasn't allowed. Sign in again and allow it."
        )
    claims = _identity(str(tokens.get("id_token") or ""), issued, found.nonce)
    _store(vault, tokens)
    email = str(claims.get("email") or "")
    _keep(session, {**saved, "client_id": issued, "sub": claims["sub"], "email": email})
    claude_failures.clear(session)
    return email or "your ChatGPT account"


# --- Keeping the access token fresh ---


def _expiring(tokens: dict[str, Any]) -> bool:
    expires = tokens.get("expires_at")
    return not isinstance(expires, int | float) or expires - time.time() < REFRESH_MARGIN_SECONDS


def access_token(vault: Vault) -> str:
    """A usable access token, refreshed first when it's about to expire."""
    from ringkeeper.db import session_scope  # noqa: PLC0415 (cycle)

    tokens = _tokens(vault)
    if tokens is None:
        raise PlanError(_signed_out("Ringkeeper isn't signed in to ChatGPT."))
    if not _expiring(tokens):
        return str(tokens["access_token"])
    with _refresh_lock, session_scope() as session:
        sql.lock(session, LOCK_KEY)
        state = _state(session)
        _keep(session, {**state, "refreshing": time.time()})  # SQLite: take the write lock now
        current = _tokens(vault)
        if current is None:
            raise PlanError(_signed_out("Ringkeeper isn't signed in to ChatGPT."))
        if not _expiring(current):  # another process refreshed it meanwhile
            return str(current["access_token"])
        status, got = _post(TOKEN, {
            "grant_type": "refresh_token", "client_id": str(state.get("client_id") or ""),
            "refresh_token": str(current["refresh_token"]), "resource": RESOURCE,
        })  # fmt: skip
        if status >= 400 or not got.get("access_token"):
            code = _error(got, status)
            if code in SIGNED_OUT:
                vault.delete(REF)
                raise PlanError(_signed_out("OpenAI ended Ringkeeper's ChatGPT sign-in."))
            raise PlanError(
                Failure(Kind.NETWORK, f"Couldn't refresh the ChatGPT sign-in ({code}).")
            )
        _store(vault, got, current)
        return str(got["access_token"])


def sign_out(session: Session, vault: Vault) -> None:
    """Revoke the refresh token (best effort) and delete the tokens. The host and client IDs
    stay, so a later sign-in is the same app."""
    tokens = _tokens(vault)
    state = _state(session)
    if tokens is not None:
        with contextlib.suppress(SignInError, KeyError):
            _post(str(_discovery()["revocation_endpoint"]), {
                "token": str(tokens["refresh_token"]), "token_type_hint": "refresh_token",
                "client_id": str(state.get("client_id") or ""),
            })  # fmt: skip
        vault.delete(REF)
    _keep(session, {k: v for k, v in state.items() if k not in ("email", "sub")})


# --- Using it ---


def _signed_out(said: str) -> Failure:
    return Failure(Kind.LOGIN, f"{said} Sign in again in Settings > Models.")


LIMIT = (
    "Your ChatGPT plan's limit for now is reached (or the limit you set for Ringkeeper). "
    f"Manage usage at {USAGE_URL}. Ringkeeper tries again later."
)
UNAVAILABLE = "ChatGPT plan usage isn't available just now. Ringkeeper tries again in a moment."
# OpenAI's error codes for plan usage (developers.openai.com/siwc, errors and recovery).
KNOWN: dict[str, Failure] = {
    "subscription_sharing_usage_limit_exceeded": Failure(Kind.PLAN_LIMIT, LIMIT),
    "subscription_sharing_usage_unavailable": Failure(Kind.OVERLOADED, UNAVAILABLE),
    "subscription_sharing_user_unavailable": Failure(Kind.OVERLOADED, UNAVAILABLE),
    "subscription_sharing_user_not_eligible": Failure(
        Kind.LOGIN,
        "This ChatGPT account or workspace can't use its plan in other apps. Choose a Claude "
        "model in Settings > Models.",
    ),
    "subscription_sharing_unsupported_capability": Failure(
        Kind.LOGIN,
        "ChatGPT plan usage doesn't support this model or request. Choose another model in "
        "Settings > Models.",
    ),
}


def failure(status: int, code: str) -> Failure:
    """A plan request's error, in plain words, and whether it's worth trying again. Plan usage
    never moves to other billing: at the limit, it waits."""
    if code in KNOWN:
        return KNOWN[code]
    if status == 429:
        return KNOWN["subscription_sharing_usage_limit_exceeded"]
    if status == 401 or code == "subscription_sharing_invalid_user":
        return _signed_out("OpenAI didn't accept Ringkeeper's ChatGPT sign-in.")
    if status >= 500:
        return Failure(
            Kind.OVERLOADED,
            f"OpenAI answered with an error ({status}). Ringkeeper tries again in a moment.",
        )
    return Failure(Kind.LOGIN, f"OpenAI refused the request ({code or status}).")


def models(vault: Vault) -> list[str]:
    """The models this plan offers, as `chatgpt:<id>`."""
    token = access_token(vault)
    with _http() as http:
        try:
            response = http.get(f"{API}/models", headers={"Authorization": f"Bearer {token}"})
            data = cast(dict[str, Any], response.json())
        except (httpx2.HTTPError, ValueError):
            raise PlanError(
                Failure(Kind.NETWORK, "Couldn't reach OpenAI to list the models.")
            ) from None
    if response.status_code >= 400:
        raise PlanError(failure(response.status_code, _error(data, response.status_code)))
    listed = cast(list[dict[str, Any]], data.get("data") or [])
    return [f"{PREFIX}{m['id']}" for m in listed if m.get("id") and m.get("visibility") == "list"]


def _events(lines: Iterator[str]) -> Iterator[dict[str, Any]]:
    for line in lines:
        if line.startswith("data:"):
            payload = line[5:].strip()
            if payload and payload != "[DONE]":
                with contextlib.suppress(ValueError):
                    yield cast(dict[str, Any], json.loads(payload))


def _event_code(event: dict[str, Any]) -> str:
    """The error code in a `response.failed` or `error` event."""
    response: Any = event.get("response")
    inner: Any = cast(dict[str, Any], response).get("error") if isinstance(response, dict) else None
    found: Any = inner or event.get("error")
    if isinstance(found, dict):
        return str(cast(dict[str, Any], found).get("code") or "")
    return str(event.get("code") or "")


def answer(vault: Vault, instructions: str, prompt: str, model: str) -> str:
    """One answer, no tools: the router's and the Help assistant's kind of call. Only a
    `response.completed` event counts as an answer."""
    body = {
        "model": bare(model), "instructions": instructions,
        "input": [{"role": "user", "content": [{"type": "input_text", "text": prompt}]}],
        "store": False, "stream": True,
    }  # fmt: skip
    headers = {"Authorization": f"Bearer {access_token(vault)}", "Accept": "text/event-stream"}
    texts: list[str] = []
    try:
        with (
            _http(ANSWER_TIMEOUT) as http,
            http.stream("POST", f"{API}/responses", json=body, headers=headers) as response,
        ):
            if response.status_code >= 400:
                response.read()
                try:
                    said = cast(dict[str, Any], response.json())
                except ValueError:
                    said = {}
                raise PlanError(failure(response.status_code, _error(said, response.status_code)))
            for event in _events(response.iter_lines()):
                kind = event.get("type")
                if kind == "response.output_text.delta":
                    texts.append(str(event.get("delta") or ""))
                elif kind == "response.completed":
                    return "".join(texts)
                elif kind in ("response.failed", "error"):
                    raise PlanError(failure(0, _event_code(event)))
    except httpx2.TimeoutException:
        raise PlanError(
            Failure(
                Kind.SLOW, "ChatGPT took too long to answer. Ringkeeper tries again in a moment."
            )
        ) from None
    except httpx2.HTTPError:
        raise PlanError(
            Failure(Kind.NETWORK, "Couldn't reach OpenAI. Check this computer is online.")
        ) from None
    raise PlanError(
        Failure(
            Kind.NETWORK, "ChatGPT's answer stopped before it finished. Ringkeeper tries again."
        )
    )
