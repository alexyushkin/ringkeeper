"""Settings > Developer apps: your own registered apps, and signing accounts in through them.

Xero stands in for the vendors that come back to this computer; Intuit's production keys
for the one that can't. A fake vendor table lets this run before the real ones are in."""

import base64
import hashlib
import json
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx2
import pytest
from fastapi.testclient import TestClient

from ringkeeper.adapters import vendor
from ringkeeper.adapters.vendor_spec import Op, Secret, Vendor
from ringkeeper.config import Settings, get_settings
from ringkeeper.db import session_scope
from ringkeeper.errors import RuleViolationError
from ringkeeper.models import Env, InstanceType
from ringkeeper.services import developer_apps, instances
from ringkeeper.services.developer_apps import SignInError
from ringkeeper.vault import get_vault
from tests import test_web_api
from tests.helpers import make_project

setup_code, admin, owner = test_web_api.setup_code, test_web_api.admin, test_web_api.owner
READ_TEST = Op("who", "GET", "/who", "Who", "read")
HOST = Secret("api_host", "Host", hidden=False, pattern=r"^[a-z-]+\.example$")
FAKES = (
    Vendor("xero_fake", "Xero", "Accounting", "", "https://x.example", (READ_TEST,),
           provider="xero"),
    Vendor("intuit_fake", "QuickBooks", "Accounting", "", "https://{api_host}", (READ_TEST,),
           provider="intuit", secrets=(HOST,)),
)  # fmt: skip


class FakeVendor:
    def __init__(self) -> None:
        self.forms: list[dict[str, list[str]]] = []
        self.headers: list[httpx2.Headers] = []
        self.next_refresh = "rt-2"
        self.refuse: str | None = None

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        url = str(request.url)
        if "connections" in url:
            return httpx2.Response(200, json=[{"tenantId": "t1", "tenantName": "Acme Ltd"}])
        form = parse_qs(request.content.decode())
        self.forms.append(form)
        self.headers.append(request.headers)
        if self.refuse:
            return httpx2.Response(400, json={"error": self.refuse})
        if form["grant_type"] == ["refresh_token"]:
            return httpx2.Response(200, json={
                "access_token": "at-2", "refresh_token": self.next_refresh, "expires_in": 1800,
            })  # fmt: skip
        return httpx2.Response(200, json={
            "access_token": "at-1", "refresh_token": "rt-1", "expires_in": 1800,
        })  # fmt: skip


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch, database_url: str) -> FakeVendor:
    made = FakeVendor()
    monkeypatch.setattr(developer_apps, "transport", httpx2.MockTransport(made.handle))
    for found in FAKES:
        monkeypatch.setitem(vendor.VENDORS, found.key, found)
    return made


def settings() -> Settings:
    """Server mode, so the vault keeps the tests' own key: the same redirect address, from
    the web origin. test_the_address_that_doesnt_change covers the desktop's."""
    return get_settings().model_copy(update={"web_origin": "http://localhost:17845"})


def on_the_desktop() -> Settings:
    return settings().model_copy(update={"mode": "desktop", "hooks_port": 17845})


def xero_app() -> developer_apps.App:
    return developer_apps.save_app(settings(), "xero", "Mine", "client-1", "secret-123")


def test_a_secret_stays_in_the_vault_and_a_second_app_is_kept_apart(fake: FakeVendor) -> None:
    first = xero_app()
    second = developer_apps.save_app(settings(), "xero", "Acme's", "client-2", "secret-456")
    assert [a.id for a in developer_apps.list_apps(settings(), "xero")] == [first.id, second.id]
    with pytest.raises(RuleViolationError, match="already set up"):
        developer_apps.save_app(settings(), "xero", "Again", "client-1", "secret-789")
    developer_apps.forget_app(settings(), "xero", first.id)
    assert [a.id for a in developer_apps.list_apps(settings(), "xero")] == [second.id]


def test_every_guide_says_where_to_paste_the_address() -> None:
    for found in developer_apps.PROVIDERS.values():
        assert not found.listed or any("{redirect}" in step for step in found.steps), found.key


def test_an_option_is_checked() -> None:
    with pytest.raises(RuleViolationError, match="doesn't look right"):
        developer_apps.save_app(
            settings(), "salesforce", "Mine", "client-9", "secret-999",
            {"login_host": "evil.example.com"},
        )  # fmt: skip


def test_the_address_that_doesnt_change_is_on_this_computer() -> None:
    desktop = get_settings().model_copy(update={"mode": "desktop", "hooks_port": 17845})
    assert developer_apps.fixed_redirect(desktop) == "http://localhost:17845/oauth/callback"
    off = desktop.model_copy(update={"hooks_port": 0})
    with pytest.raises(SignInError, match="turned off"):
        developer_apps.fixed_redirect(off)


def _start(connection: dict[str, Any], key: str = "xero_fake") -> tuple[str, str]:
    return developer_apps.start(settings(), key, connection)


@pytest.mark.parametrize("key", sorted(developer_apps.PROVIDERS))
def test_every_provider_signs_in_with_pkce_and_a_state_used_once(
    key: str, fake: FakeVendor, monkeypatch: pytest.MonkeyPatch
) -> None:
    wanted = Vendor(f"{key}_any", key, "Accounting", "", "https://x.example", (READ_TEST,),
                    provider=key)  # fmt: skip
    monkeypatch.setitem(vendor.VENDORS, wanted.key, wanted)
    options = {"tenant": "common", "client_accounts": "approved"}
    app = developer_apps.App("a1", "Mine", "client-1", "secret-123", options)

    def the_app(*_args: object) -> developer_apps.App:
        return app

    def back(*_args: object) -> str:
        return "https://x.example/back"

    monkeypatch.setattr(developer_apps, "_app", the_app)
    monkeypatch.setattr(developer_apps, "redirect_for", back)
    access = [a.key for a in developer_apps.provider(key).access[:1]]
    url, state = developer_apps.start(
        settings(), wanted.key, {"owner": "acme", "env": "dev"}, access
    )
    query = parse_qs(urlsplit(url).query)
    assert query["state"] == [state]
    assert len(state) >= 40
    assert query["code_challenge_method"] == ["S256"]
    sent: dict[str, str] = {}

    def exchange(_found: object, _app: object, form: dict[str, str]) -> dict[str, Any]:
        sent.update(form)
        raise SignInError("stop here")

    monkeypatch.setattr(developer_apps, "_token_request", exchange)
    assert not developer_apps.pending("not-this-one")
    found = developer_apps.take(state)
    with session_scope() as session, pytest.raises(SignInError, match="stop here"):
        developer_apps.finish(session, settings(), found, state, "code-1", {})
    digest = hashlib.sha256(sent["code_verifier"].encode()).digest()
    assert base64.urlsafe_b64encode(digest).rstrip(b"=").decode() == query["code_challenge"][0]
    with pytest.raises(SignInError, match="expired or was already used"):
        developer_apps.take(state)


def test_signing_in_stores_the_account_and_says_which(fake: FakeVendor) -> None:
    xero_app()
    url, state = _start({"owner": "acme", "env": "prod"})
    query = parse_qs(urlsplit(url).query)
    assert query["redirect_uri"] == ["http://localhost:17845/oauth/callback"]
    assert query["code_challenge_method"] == ["S256"]
    assert "accounting.invoices" in query["scope"][0]
    assert "accounting.transactions" not in query["scope"][0]
    with session_scope() as session:
        name = developer_apps.finish(
            session, settings(), developer_apps.take(state), state, "code-1", {}
        )
    assert developer_apps.outcome(state) == developer_apps.Outcome("done", name=name)
    with session_scope() as session:
        found = instances.get_instance(session, name)
        assert (found.type, found.env, found.owner) == (InstanceType.VENDOR, Env.PROD, "acme")
        assert found.config["vendor"] == "xero_fake"
        assert found.config["account"] == "Acme Ltd"
        stored = json.loads(get_vault().resolve(found.secret_ref).reveal())
    assert stored["default_account"] == "t1"
    assert "client" not in json.dumps(found.config)
    basic = base64.b64encode(b"client-1:secret-123").decode()
    assert fake.headers[0]["Authorization"] == f"Basic {basic}"
    assert "client_secret" not in fake.forms[0]  # Xero wants it in the header


def test_a_rotated_refresh_token_is_kept_and_the_old_one_isnt(fake: FakeVendor) -> None:
    xero_app()
    _, state = _start({"owner": "acme"})
    with session_scope() as session:
        name = developer_apps.finish(
            session, settings(), developer_apps.take(state), state, "code-1", {}
        )
        ref = instances.get_instance(session, name).secret_ref
    vault = get_vault()
    expired = json.loads(vault.resolve(ref).reveal()) | {"expires_at": 0}
    vault.store(ref, json.dumps(expired))
    fresh = json.loads(developer_apps.resolve(vault, name, ref).reveal())
    assert (fresh["access_token"], fresh["refresh_token"]) == ("at-2", "rt-2")
    assert json.loads(vault.resolve(ref).reveal())["refresh_token"] == "rt-2"
    assert fake.forms[-1]["refresh_token"] == ["rt-1"]


def test_a_refused_refresh_says_to_sign_in_again(fake: FakeVendor) -> None:
    xero_app()
    _, state = _start({"owner": "acme"})
    with session_scope() as session:
        name = developer_apps.finish(
            session, settings(), developer_apps.take(state), state, "code-1", {}
        )
        ref = instances.get_instance(session, name).secret_ref
    vault = get_vault()
    vault.store(ref, json.dumps(json.loads(vault.resolve(ref).reveal()) | {"expires_at": 0}))
    fake.refuse = "invalid_grant"
    with pytest.raises(SignInError, match="Sign in again"):
        developer_apps.resolve(vault, name, ref)


def test_a_pasted_key_has_nothing_to_refresh(fake: FakeVendor) -> None:
    vault = get_vault()
    vault.store("file://t/clerk", json.dumps({"vendor": "clerk", "api_key": "sk_test_abc"}))
    assert "api_key" in developer_apps.resolve(vault, "t", "file://t/clerk").reveal()
    assert not fake.forms


def intuit(environment: str) -> developer_apps.App:
    return developer_apps.save_app(
        settings(), "intuit", environment, f"client-{environment}", "secret-123",
        {"environment": environment},
    )  # fmt: skip


@pytest.mark.parametrize("environment", ["sandbox", "production"])
def test_intuit_has_one_way_back_in_both_environments(
    fake: FakeVendor, monkeypatch: pytest.MonkeyPatch, environment: str
) -> None:
    app = intuit(environment)

    def hosted(*_args: Any) -> str:
        return developer_apps.HOSTED_CALLBACK

    monkeypatch.setattr(developer_apps, "redirect_for", hosted)
    url, state = developer_apps.start(settings(), "intuit_fake", {"owner": "a"}, app_id=app.id)
    assert parse_qs(urlsplit(url).query)["redirect_uri"] == [developer_apps.HOSTED_CALLBACK]
    assert developer_apps.outcome(state) == developer_apps.Outcome("waiting")


@pytest.mark.parametrize(
    ("key", "hosted"),
    [("intuit", True), ("xero", True), ("hubspot", True), ("salesforce", True),
     ("microsoft", False), ("jobber", False)],
)  # fmt: skip
def test_who_comes_back_through_the_hosted_page(key: str, hosted: bool) -> None:
    found = developer_apps.provider(key)
    address = developer_apps.redirect_for(on_the_desktop(), found)
    assert (address == developer_apps.HOSTED_CALLBACK) is hosted
    assert hosted or address.startswith("http://localhost:")


def test_a_server_comes_back_to_itself_whoever_the_vendor_is() -> None:
    server = settings().model_copy(
        update={"mode": "server", "web_origin": "https://ringkeeper.example.com"}
    )
    found = developer_apps.provider("xero")
    assert developer_apps.redirect_for(server, found) == (
        "https://ringkeeper.example.com/oauth/callback"
    )


def test_the_hosted_page_only_reaches_the_port_it_knows() -> None:
    moved = on_the_desktop().model_copy(update={"hooks_port": 18000})
    with pytest.raises(SignInError, match="port 17845"):
        developer_apps.redirect_for(moved, developer_apps.provider("xero"))
    assert developer_apps.listed_redirect(moved) is None
    assert developer_apps.listed_redirect(settings()) is None  # a server has no such address
    assert developer_apps.listed_redirect(on_the_desktop()) == (
        "http://localhost:17845/oauth/callback"
    )


def test_a_production_company_uses_the_production_host(fake: FakeVendor) -> None:
    live = intuit("production")
    _, state = developer_apps.start(settings(), "intuit_fake", {"owner": "acme"}, app_id=live.id)
    with session_scope() as session:
        name = developer_apps.finish(
            session, settings(), developer_apps.take(state), state, "code-1", {"realmId": "123"}
        )
        ref = instances.get_instance(session, name).secret_ref
    stored = json.loads(get_vault().resolve(ref).reveal())
    assert (stored["realm_id"], stored["api_host"]) == ("123", "quickbooks.api.intuit.com")


def test_a_company_id_that_isnt_one_is_refused(fake: FakeVendor) -> None:
    live = intuit("production")
    _, state = developer_apps.start(settings(), "intuit_fake", {"owner": "acme"}, app_id=live.id)
    with session_scope() as session, pytest.raises(SignInError, match="which company"):
        developer_apps.finish(
            session, settings(), developer_apps.take(state), state, "c", {"realmId": "../x"}
        )


@pytest.mark.parametrize(
    ("status", "body", "accepted"),
    [
        (400, '{"error":"invalid_grant"}', True),
        (400, '{"status":"BAD_AUTH_CODE"}', True),
        (400, '{"error":"invalid_client"}', False),
        (401, "{}", False),
        (400, '{"status":"BAD_CLIENT_ID"}', False),
        (400, '{"error":"unauthorized_client","error_description":"AADSTS700016"}', False),
    ],
)
def test_the_live_check_tells_a_bad_client_from_the_made_up_code(
    monkeypatch: pytest.MonkeyPatch, status: int, body: str, accepted: bool
) -> None:
    def answer(_request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(status, text=body)

    monkeypatch.setattr(developer_apps, "transport", httpx2.MockTransport(answer))
    args = ("https://t.example/token", "id", "secret", "http://localhost/cb", False)
    if accepted:
        assert "accepted" in developer_apps.check_client(*args)
    else:
        with pytest.raises(SignInError, match="didn't accept"):
            developer_apps.check_client(*args)


def test_a_project_of_another_client_is_refused_before_signing_in(fake: FakeVendor) -> None:
    with session_scope() as session:
        make_project(session, "globex-app", owner="globex")
        with pytest.raises(RuleViolationError, match="globex"):
            developer_apps.check_start(
                session, {"owner": "acme", "project": "globex-app"}, "xero_fake"
            )


def test_no_secret_comes_back_from_settings(owner: TestClient, fake: FakeVendor) -> None:
    made = owner.post(
        "/developer-apps/xero/apps",
        json={"name": "Mine", "client_id": "client-1", "client_secret": "secret-123"},
    )
    assert made.status_code == 201, made.text
    listed = owner.get("/developer-apps")
    assert listed.status_code == 200
    assert "secret-123" not in listed.text
    xero = next(p for p in listed.json()["providers"] if p["key"] == "xero")
    assert xero["apps"][0]["client_id"] == "client-1"
    assert "accounting.invoices" in xero["scopes"]
    assert any(xero["redirect"] in step for step in xero["steps"])
