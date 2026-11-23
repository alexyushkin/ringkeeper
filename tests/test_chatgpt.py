"""Your ChatGPT plan: signing in through OpenAI, keeping the sign-in fresh, and the router's
calls on it. OpenAI is a fake here (httpx2.MockTransport), as Google is in test_google."""

import json
import time
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx2
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from jwt.algorithms import RSAAlgorithm

from ringkeeper import claude_failures
from ringkeeper.db import session_scope
from ringkeeper.models import Setting
from ringkeeper.router.model import AgentSdkModel, ClaudeUnavailableError
from ringkeeper.services import chatgpt, runtime
from ringkeeper.vault import get_vault
from tests import test_web_api

setup_code, admin, owner = test_web_api.setup_code, test_web_api.admin, test_web_api.owner

ISSUED = "oaiapp_ringkeeper_test"
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


class FakeOpenAI:
    def __init__(self) -> None:
        self.nonce = ""
        self.forms: list[dict[str, str]] = []
        self.revoked: list[str] = []
        self.spent: set[str] = set()
        self.scope = chatgpt.SCOPES
        self.refreshes = 0
        self.stream: list[dict[str, Any]] = [
            {"type": "response.output_text.delta", "delta": "Hello"},
            {"type": "response.output_text.delta", "delta": " there"},
            {"type": "response.completed", "response": {}},
        ]
        self.answer_status = 200
        self.bodies: list[dict[str, Any]] = []

    def id_token(self, client: str) -> str:
        claims = {
            "iss": chatgpt.AUTH, "aud": client, "sub": "user-123", "email": "owner@example.com",
            "nonce": self.nonce, "iat": int(time.time()), "exp": int(time.time()) + 600,
        }  # fmt: skip
        return jwt.encode(claims, KEY, algorithm="RS256", headers={"kid": "k1"})

    def tokens(self, client: str, n: int) -> dict[str, Any]:
        return {
            "access_token": f"access-{n}-{'x' * 20}", "refresh_token": f"refresh-{n}-{'y' * 20}",
            "id_token": self.id_token(client), "token_type": "Bearer", "expires_in": 3600,
            "scope": self.scope,
        }  # fmt: skip

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        url = str(request.url).split("?")[0]
        if url == chatgpt.DISCOVERY:
            return httpx2.Response(200, json={
                "issuer": chatgpt.AUTH, "jwks_uri": f"{chatgpt.AUTH}/jwks",
                "revocation_endpoint": f"{chatgpt.AUTH}/revoke",
            })  # fmt: skip
        if url == f"{chatgpt.AUTH}/jwks":
            key = json.loads(RSAAlgorithm.to_jwk(KEY.public_key()))
            return httpx2.Response(200, json={"keys": [{**key, "kid": "k1", "use": "sig"}]})
        if url == chatgpt.TOKEN:
            form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
            self.forms.append(form)
            return self.token(form)
        if url == f"{chatgpt.AUTH}/revoke":
            self.revoked.append(parse_qs(request.content.decode())["token"][0])
            return httpx2.Response(200)
        if url == f"{chatgpt.API}/models":
            return httpx2.Response(200, json={"data": [
                {"id": "gpt-5.5", "visibility": "list"}, {"id": "gpt-hidden", "visibility": "hide"},
            ]})  # fmt: skip
        if url == f"{chatgpt.API}/responses":
            assert request.headers["authorization"].startswith("Bearer access-")
            self.bodies.append(json.loads(request.content))
            if self.answer_status != 200:
                return httpx2.Response(self.answer_status, json={"detail": "no"})
            lines = "".join(f"data: {json.dumps(event)}\n\n" for event in self.stream)
            return httpx2.Response(
                200, content=lines.encode(), headers={"content-type": "text/event-stream"}
            )
        return httpx2.Response(404)

    def token(self, form: dict[str, str]) -> httpx2.Response:
        if form.get("client_id") != ISSUED or form.get("resource") != chatgpt.RESOURCE:
            return httpx2.Response(400, json={"error": "invalid_client"})
        if form["grant_type"] == "authorization_code":
            if form["code"] != "good-code" or not form.get("code_verifier"):
                return httpx2.Response(400, json={"error": "invalid_grant"})
            return httpx2.Response(200, json=self.tokens(ISSUED, 0))
        token = form["refresh_token"]
        if token in self.spent:  # each refresh token works once
            return httpx2.Response(400, json={"error": "refresh_token_reused"})
        self.spent.add(token)
        self.refreshes += 1
        return httpx2.Response(200, json=self.tokens(ISSUED, self.refreshes))


def always(_settings: object) -> bool:
    """As in the desktop app, without its keychain."""
    return True


@pytest.fixture
def openai(monkeypatch: pytest.MonkeyPatch) -> FakeOpenAI:
    world = FakeOpenAI()
    monkeypatch.setattr(chatgpt, "transport", httpx2.MockTransport(world.handle))
    monkeypatch.setattr(chatgpt, "available", always)
    return world


def started(owner: TestClient) -> dict[str, list[str]]:
    """The sign-in page's query, as Ringkeeper asked for it."""
    response = owner.post("/chatgpt/signin")
    assert response.status_code == 200, response.text
    return parse_qs(urlsplit(str(response.json()["url"])).query)


def sign_in(
    owner: TestClient, openai: FakeOpenAI, **back: str
) -> tuple[dict[str, list[str]], httpx2.Response]:
    query = started(owner)
    openai.nonce = query["nonce"][0]
    params: dict[str, str] = {
        "state": query["state"][0], "code": "good-code", "client_id": ISSUED, **back,
    }  # fmt: skip
    return query, owner.get("/oauth/callback", params=params)


def no_wait(_seconds: float) -> None:
    return None


def test_signing_in_registers_this_install_and_keeps_the_tokens_in_the_vault(
    openai: FakeOpenAI, owner: TestClient
) -> None:
    assert owner.get("/chatgpt").json()["signed_in"] is False
    query, back = sign_in(owner, openai)
    # The first time: OpenAI's registration client, our name and this install's host ID.
    assert query["client_id"] == [chatgpt.FIRST_CLIENT]
    assert query["agent_name_hint"] == ["Ringkeeper"]
    assert query["ext_agent_host_id"][0].startswith("urn:uuid:")
    assert query["code_challenge_method"] == ["S256"]
    assert query["resource"] == [chatgpt.RESOURCE]
    assert chatgpt.PLAN_SCOPE in query["scope"][0].split()
    assert query["redirect_uri"][0].endswith("/oauth/callback")
    assert back.status_code == 200, back.text
    assert "owner@example.com" in back.text
    status = owner.get("/chatgpt").json()
    assert status["signed_in"] is True
    assert status["email"] == "owner@example.com"
    assert status["usage_url"] == "https://chatgpt.com/settings/usage"
    # The exchange used the issued client ID, the verifier and the same redirect.
    exchange = openai.forms[0]
    assert exchange["client_id"] == ISSUED
    assert exchange["redirect_uri"] == query["redirect_uri"][0]
    # Tokens only in the vault: not in the settings, the audit or the answer.
    assert "access-0-" in get_vault().resolve(chatgpt.REF).reveal()
    assert "refresh-0-" not in test_web_api.audit_text()
    with session_scope() as session:
        saved = session.get(Setting, chatgpt.STATE_KEY)
        assert saved is not None
        assert "refresh-0" not in json.dumps(saved.value)
    # The next sign-in is the same app: the issued client ID, and no name hint.
    again = started(owner)
    assert again["client_id"] == [ISSUED]
    assert "agent_name_hint" not in again
    assert again["ext_agent_host_id"] == query["ext_agent_host_id"]


def test_a_sign_in_without_plan_usage_or_for_another_app_is_refused(
    openai: FakeOpenAI, owner: TestClient
) -> None:
    openai.scope = "openid profile email offline_access"
    _, back = sign_in(owner, openai)
    assert back.status_code == 400
    assert "plan wasn&#x27;t allowed" in back.text
    assert owner.get("/chatgpt").json()["signed_in"] is False
    _, declined = sign_in(owner, openai, error="access_denied")
    assert "chose not to allow" in declined.text
    _, missing = sign_in(owner, openai, client_id="")
    assert "didn&#x27;t finish registering" in missing.text


def test_the_access_token_is_refreshed_once_and_a_spent_sign_in_says_so(
    openai: FakeOpenAI, owner: TestClient
) -> None:
    sign_in(owner, openai)
    vault = get_vault()
    assert chatgpt.access_token(vault) == f"access-0-{'x' * 20}"  # still fresh
    saved = json.loads(vault.resolve(chatgpt.REF).reveal())
    vault.store(chatgpt.REF, json.dumps({**saved, "expires_at": int(time.time()) + 10}))
    assert chatgpt.access_token(vault).startswith("access-1-")
    assert chatgpt.access_token(vault).startswith("access-1-")  # not refreshed again
    assert openai.refreshes == 1
    # OpenAI ended it (here: the refresh token was used elsewhere): sign in again.
    saved = json.loads(vault.resolve(chatgpt.REF).reveal())
    openai.spent.add(saved["refresh_token"])
    vault.store(chatgpt.REF, json.dumps({**saved, "expires_at": 0}))
    with pytest.raises(chatgpt.PlanError) as ended:
        chatgpt.access_token(vault)
    assert ended.value.failure.kind is claude_failures.Kind.LOGIN
    assert "Sign in again" in str(ended.value)
    assert chatgpt.stored(vault) is None


def test_the_router_answers_on_the_plan_without_claude(
    openai: FakeOpenAI, owner: TestClient
) -> None:
    sign_in(owner, openai)
    model = AgentSdkModel(get_vault(), runtime.current_settings(), sleep=no_wait)
    done = model.complete("You sort requests.", "Where does this go?", "chatgpt:gpt-5.5")
    assert done.text == "Hello there"
    body = openai.bodies[-1]
    assert body["model"] == "gpt-5.5"
    assert body["instructions"] == "You sort requests."
    assert body["store"] is False
    assert body["stream"] is True
    # At the plan's limit it waits, and pauses nothing on Claude's side.
    openai.stream = [{"type": "response.failed", "response": {"error": {
        "code": "subscription_sharing_usage_limit_exceeded"}}}]  # fmt: skip
    with pytest.raises(ClaudeUnavailableError) as limited:
        model.complete("s", "p", "chatgpt:gpt-5.5")
    assert limited.value.failure.kind is claude_failures.Kind.PLAN_LIMIT
    assert "chatgpt.com/settings/usage" in str(limited.value)
    assert not limited.value.failure.transient
    openai.answer_status = 401
    with pytest.raises(ClaudeUnavailableError) as refused:
        model.complete("s", "p", "chatgpt:gpt-5.5")
    assert refused.value.failure.kind is claude_failures.Kind.LOGIN


def test_the_plan_models_are_offered_and_signing_out_puts_claude_back(
    openai: FakeOpenAI, owner: TestClient
) -> None:
    assert not any(m.get("plan") for m in owner.get("/models").json()["models"])
    sign_in(owner, openai)
    offered = owner.get("/models").json()["models"]
    assert [m["id"] for m in offered if m.get("plan")] == ["chatgpt:gpt-5.5"]
    assert owner.patch("/settings", json={"model_router": "chatgpt:gpt-5.5"}).status_code == 200
    assert owner.patch("/settings", json={"model_worker": "chatgpt:gpt-5.5"}).status_code == 200
    # Signing out revokes it at OpenAI and puts every role back on its Claude model.
    assert owner.delete("/chatgpt").json() == {"signed_in": False}
    assert not runtime.current_settings().model_worker.startswith("chatgpt:")
    assert openai.revoked == [f"refresh-0-{'y' * 20}"]
    assert not runtime.current_settings().model_router.startswith("chatgpt:")
    assert owner.get("/chatgpt").json()["signed_in"] is False


def test_only_the_desktop_app_offers_it(owner: TestClient) -> None:
    assert owner.get("/chatgpt").json() == {"signed_in": False, "available": False}
    refused = owner.post("/chatgpt/signin")
    assert refused.status_code == 409
    assert "desktop app" in refused.text
