"""Signing in to a remote MCP server (services.mcp_oauth) against a fake
authorization server: discovery, registration, PKCE, the code exchange with
the resource indicator, refreshing, and one-time sign-ins."""

import base64
import hashlib
import json
import time
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx2
import pytest

from ringkeeper.adapters import mcp
from ringkeeper.services import mcp_oauth

MCP_URL = "https://mcp.neon.tech/mcp"
AUTH = "https://auth.neon.example"
REDIRECT = "http://127.0.0.1:5000/oauth/callback"


class FakeAuthServer:
    def __init__(self) -> None:
        self.registered: list[dict[str, Any]] = []
        self.token_forms: list[dict[str, str]] = []
        self.challenge: str | None = None
        self.refreshes = 0

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        url = str(request.url)
        if url == "https://mcp.neon.tech/.well-known/oauth-protected-resource/mcp":
            return httpx2.Response(200, json={
                "resource": MCP_URL, "authorization_servers": [AUTH],
                "scopes_supported": ["read", "write"],
            })  # fmt: skip
        if url == f"{AUTH}/.well-known/oauth-authorization-server":
            return httpx2.Response(200, json={
                "issuer": AUTH, "authorization_endpoint": f"{AUTH}/authorize",
                "token_endpoint": f"{AUTH}/token", "registration_endpoint": f"{AUTH}/register",
            })  # fmt: skip
        if url == f"{AUTH}/register":
            self.registered.append(json.loads(request.content))
            return httpx2.Response(201, json={"client_id": "client-1"})
        if url == f"{AUTH}/token":
            form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
            self.token_forms.append(form)
            return self._token(form)
        return httpx2.Response(404)

    def _token(self, form: dict[str, str]) -> httpx2.Response:
        if form.get("grant_type") == "authorization_code":
            digest = hashlib.sha256(form["code_verifier"].encode()).digest()
            if form.get("code") != "good" or (
                base64.urlsafe_b64encode(digest).rstrip(b"=").decode() != self.challenge
            ):
                return httpx2.Response(400, json={"error": "invalid_grant"})
            return httpx2.Response(200, json={
                "access_token": "access-token-one", "refresh_token": "refresh-token-one",
                "expires_in": 3600,
            })  # fmt: skip
        if (
            form.get("grant_type") == "refresh_token"
            and form.get("refresh_token") == "refresh-token-one"
        ):
            self.refreshes += 1
            return httpx2.Response(200, json={
                "access_token": "access-token-two", "refresh_token": "refresh-token-two",
                "expires_in": 3600,
            })  # fmt: skip
        return httpx2.Response(400, json={"error": "invalid_grant", "error_description": "no"})


@pytest.fixture
def auth(monkeypatch: pytest.MonkeyPatch) -> FakeAuthServer:
    fake = FakeAuthServer()
    monkeypatch.setattr(mcp_oauth, "transport", httpx2.MockTransport(fake.handle))
    return fake


def sign_in(auth: FakeAuthServer) -> tuple[str, dict[str, str]]:
    page = mcp_oauth.start(MCP_URL, REDIRECT, {"name": "acme-neon"})
    query = {k: v[0] for k, v in parse_qs(urlsplit(page).query).items()}
    auth.challenge = query["code_challenge"]
    return page, query


def test_signing_in_registers_ringkeeper_and_asks_for_the_server(auth: FakeAuthServer) -> None:
    page, query = sign_in(auth)
    assert page.startswith(f"{AUTH}/authorize?")
    assert (query["client_id"], query["redirect_uri"], query["resource"]) == (
        "client-1", REDIRECT, MCP_URL,
    )  # fmt: skip
    assert (query["code_challenge_method"], query["scope"]) == ("S256", "read write")
    [registered] = auth.registered
    assert registered["redirect_uris"] == [REDIRECT]
    assert registered["token_endpoint_auth_method"] == "none"

    secret = mcp_oauth.finish(mcp_oauth.take(query["state"]), "good")
    oauth = json.loads(secret)["oauth"]
    assert (oauth["access_token"], oauth["refresh_token"]) == (
        "access-token-one",
        "refresh-token-one",
    )
    assert auth.token_forms[-1]["resource"] == MCP_URL
    assert mcp.server_spec(secret)["headers"] == {"Authorization": "Bearer access-token-one"}
    assert "refresh-token-one" in mcp.secret_fragments(secret)  # never shown, even in errors


def test_a_sign_in_is_used_once_and_a_bad_code_is_refused(auth: FakeAuthServer) -> None:
    _, query = sign_in(auth)
    pending = mcp_oauth.take(query["state"])
    with pytest.raises(mcp_oauth.SignInError, match="expired or was already used"):
        mcp_oauth.take(query["state"])
    with pytest.raises(mcp_oauth.SignInError, match="invalid_grant"):
        mcp_oauth.finish(pending, "forged")


class Store:
    def __init__(self, value: str) -> None:
        self.value = value
        self.saved = 0

    def current(self) -> str:
        return self.value

    def save(self, value: str) -> None:
        self.value = value
        self.saved += 1


def test_an_expiring_token_is_refreshed_once_and_stored(auth: FakeAuthServer) -> None:
    _, query = sign_in(auth)
    secret = json.loads(mcp_oauth.finish(mcp_oauth.take(query["state"]), "good"))
    assert mcp_oauth.fresh("acme-neon", json.dumps(secret), Store("")) == json.dumps(secret)
    secret["oauth"]["expires_at"] = int(time.time()) - 10
    store = Store(json.dumps(secret))
    renewed = mcp_oauth.fresh("acme-neon", store.value, store)
    assert json.loads(renewed)["oauth"]["access_token"] == "access-token-two"
    assert (store.saved, auth.refreshes) == (1, 1)
    # A second caller that read the old value sees the stored one, not a second refresh.
    assert (
        json.loads(mcp_oauth.fresh("acme-neon", json.dumps(secret), store))["oauth"]["access_token"]
        == "access-token-two"
    )
    assert auth.refreshes == 1


def test_servers_without_sign_in_say_so(auth: FakeAuthServer) -> None:
    with pytest.raises(mcp_oauth.SignInError, match="Paste a token"):
        mcp_oauth.start("https://mcp.example.org/mcp", REDIRECT, {})
