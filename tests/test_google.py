"""Google accounts through the owner's own Google app (a fake Google): Settings >
Google, signing in accounts (a client's or your own), the scopes granted, many
accounts side by side, a project's default and naming an account, writes that
ask first unless the owner lets a tool run, and two clients' accounts never
crossing while your own works in both clients' projects."""

import asyncio
import base64
import json
import time
from email import message_from_bytes
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx2
import pytest
from fastapi.testclient import TestClient

from ringkeeper.adapters import google
from ringkeeper.adapters.base import ToolError
from ringkeeper.api import google_routes
from ringkeeper.calls import Caller, ToolCall
from ringkeeper.config import Settings
from ringkeeper.db import session_scope
from ringkeeper.models import InstanceType
from ringkeeper.services import decisions, google_oauth, inbox, instances
from ringkeeper.services.calls import RefusalError, bound_instances, run_call
from ringkeeper.vault import get_vault
from tests import test_web_api

setup_code, admin, owner = test_web_api.setup_code, test_web_api.admin, test_web_api.owner

CLIENT_ID = "1234-abc.apps.googleusercontent.com"
ACME_CLIENT = "5678-acme.apps.googleusercontent.com"  # a client's own Google Cloud project
CLIENTS = {CLIENT_ID: "s3cret-value", ACME_CLIENT: "acme-s3cret"}
G = "https://www.googleapis.com/auth/"
ACCOUNTS = {  # the code Google gives back -> the account that signed in
    "acme": "info@acme.example",
    "acme-ops": "ops@acme.example",
    "globex": "sales@globex.example",
    "mine": "me@gmail.com",
    "partial": "info@acme.example",  # unticked everything but reading mail
    "acme-team": "team@acme.example",
}


class FakeGoogle:
    def __init__(self) -> None:
        self.sent: list[tuple[str, dict[str, Any]]] = []  # (account, message)
        self.revoked: list[str] = []
        self.refreshes = 0
        self.scopes: dict[str, str] = {}  # code -> scope string asked for
        self.issued: dict[str, str] = {}  # refresh token -> the client it was given to

    def account(self, request: httpx2.Request) -> str:
        token = request.headers.get("Authorization", "").removeprefix("Bearer ")
        return token.split(":", 1)[1]

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        url = str(request.url)
        if url == google_oauth.TOKEN:
            form = parse_qs(request.content.decode())
            client = form["client_id"][0]
            assert form["client_secret"] == [CLIENTS[client]]
            if form["grant_type"] == ["refresh_token"]:
                if self.issued.get(form["refresh_token"][0]) != client:
                    return httpx2.Response(400, json={"error": "unauthorized_client"})
                self.refreshes += 1
                email = form["refresh_token"][0].split(":", 1)[1]
                return httpx2.Response(
                    200, json={"access_token": f"at2:{email}", "expires_in": 3600}
                )
            code = form["code"][0]
            email = ACCOUNTS[code]
            scope = "openid email " + G + "gmail.readonly" if code == "partial" else self.asked
            self.issued[f"rt:{email}"] = client
            return httpx2.Response(200, json={
                "access_token": f"at:{email}", "refresh_token": f"rt:{email}",
                "expires_in": 3600, "scope": scope,
            })  # fmt: skip
        if url == google_oauth.REVOKE:
            self.revoked.append(parse_qs(request.content.decode())["token"][0])
            return httpx2.Response(200)
        email = self.account(request)
        if url == google_oauth.USERINFO:
            return httpx2.Response(200, json={"email": email})
        path = request.url.path
        if path.endswith("/messages") and request.method == "GET":
            return httpx2.Response(200, json={"messages": [{"id": f"m-{email.split('@')[0]}"}]})
        if "/messages/m-" in path:
            return httpx2.Response(200, json={
                "id": path.rsplit("/", 1)[1], "threadId": "t1", "snippet": f"mail for {email}",
                "payload": {"headers": [{"name": "To", "value": email},
                                        {"name": "Subject", "value": f"hello {email}"}]},
            })  # fmt: skip
        if path.endswith("/messages/send"):
            raw = json.loads(request.content)["raw"]
            message = message_from_bytes(base64.urlsafe_b64decode(raw))
            self.sent.append((email, {"from": message["From"], "to": message["To"]}))
            return httpx2.Response(200, json={"id": "sent-1"})
        return httpx2.Response(404, json={"error": {"message": f"no {path}"}})

    asked = ""


REAL_DESKTOP = google_routes.desktop


def always(_settings: Settings) -> bool:
    return True


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeGoogle:
    world = FakeGoogle()
    monkeypatch.setattr(google_oauth, "transport", httpx2.MockTransport(world.handle))

    async def handle(request: httpx2.Request) -> httpx2.Response:
        await request.aread()
        return world.handle(request)

    monkeypatch.setattr(google, "transport", httpx2.MockTransport(handle))
    # Signing in is the desktop app's for now; the rest stays as the tests' own settings
    # (the vault keeps its test key, never the OS keychain's).
    monkeypatch.setattr(google_routes, "desktop", always)
    return world


def sign_in(http: TestClient, fake: FakeGoogle, code: str, **body: Any) -> dict[str, Any]:
    """Start a sign-in, then come back from Google with `code`; how it went."""
    started = http.post("/google/signin", json={"access": ["mail_read", "mail_send"], **body})
    assert started.status_code == 200, started.text
    query = parse_qs(urlsplit(str(started.json()["url"])).query)
    fake.asked = query["scope"][0]
    state = started.json()["state"]
    back = http.get("/oauth/callback", params={"state": state, "code": code})
    status = http.get(f"/google/signin/{state}").json()
    assert (back.status_code == 200) == (status["state"] == "done"), back.text
    return status


def call(project: str, tool: str, instance: str | None = None, **args: Any) -> Any:
    caller = Caller(project, "session-1", "token-1")
    return asyncio.run(
        run_call(ToolCall(caller, InstanceType.GOOGLE, tool, instance, args), get_vault())
    )


def test_your_google_app_is_kept_in_the_vault(owner: TestClient, fake: FakeGoogle) -> None:
    assert owner.get("/google/app").json()["configured"] is False
    refused = owner.post("/google/signin", json={"access": ["mail_read"], "owner": "acme"})
    assert refused.status_code == 409
    assert "Settings > Developer apps" in refused.text
    bad = owner.put("/google/app", json={"client_id": "not-a-client-id", "client_secret": "x" * 8})
    assert bad.status_code == 409
    saved = owner.put("/google/app", json={"client_id": CLIENT_ID, "client_secret": "s3cret-value"})
    assert saved.status_code == 200, saved.text
    assert saved.json()["configured"] is True
    assert saved.json()["client_id"] == CLIENT_ID
    assert "s3cret-value" not in saved.text
    assert "s3cret-value" not in test_web_api.audit_text()
    stored = json.loads(get_vault().resolve(google_oauth.APP_REF).reveal())
    assert stored["apps"][0]["client_secret"] == "s3cret-value"
    started = owner.post(
        "/google/signin", json={"access": ["mail_read", "drive_files"], "owner": "acme"}
    )
    query = parse_qs(urlsplit(str(started.json()["url"])).query)
    assert query["client_id"] == [CLIENT_ID]
    assert query["access_type"] == ["offline"]
    assert query["code_challenge_method"] == ["S256"]
    assert query["redirect_uri"][0].endswith("/oauth/callback")
    assert query["scope"][0].split() == ["openid", "email", G + "gmail.readonly", G + "drive.file"]
    assert owner.delete("/google/app").status_code == 204
    assert owner.get("/google/app").json()["configured"] is False


def test_server_mode_waits(
    owner: TestClient, fake: FakeGoogle, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(google_routes, "desktop", REAL_DESKTOP)
    refused = owner.post("/google/signin", json={"access": ["mail_read"], "owner": "acme"})
    assert refused.status_code == 409
    assert "desktop app" in refused.text


@pytest.fixture
def accounts(owner: TestClient, fake: FakeGoogle) -> TestClient:
    """Two clients' projects (acme has two), acme's two accounts, globex's, and yours."""
    owner.put("/google/app", json={"client_id": CLIENT_ID, "client_secret": "s3cret-value"})
    for name, client in (("acme-web", "acme"), ("acme-blog", "acme"), ("globex-app", "globex")):
        assert owner.post("/projects", json={"name": name, "owner": client}).status_code == 201
    assert sign_in(owner, fake, "acme", owner="acme", project="acme-web")["name"] == "acme-info"
    assert sign_in(owner, fake, "acme-ops", owner="acme", project="acme-web")["name"] == "acme-ops"
    assert (
        sign_in(owner, fake, "globex", owner="globex", project="globex-app")["name"]
        == "globex-sales"
    )
    mine = sign_in(owner, fake, "mine", shared=True, all_projects=True)
    assert mine == {"state": "done", "name": "me-gmail", "error": None}
    return owner


def test_accounts_side_by_side_never_cross(accounts: TestClient, fake: FakeGoogle) -> None:
    owner = accounts
    with session_scope() as session:
        acme = instances.get_instance(session, "acme-info")
        assert (acme.type, acme.owner, acme.shared) == (InstanceType.GOOGLE, "acme", False)
        assert acme.config["email"] == "info@acme.example"
        assert acme.config["access"] == ["mail_read", "mail_send"]
        mine = instances.get_instance(session, "me-gmail")
        assert (mine.owner, mine.shared, mine.all_projects) == ("you", True, True)
        stored = get_vault().resolve(acme.secret_ref).reveal()
        assert json.loads(stored)["refresh_token"] == "rt:info@acme.example"

    # Test: Google says which account it is (at its exact address, no trailing slash).
    tested = owner.post("/connections/acme-info/test").json()
    assert tested["ok"] is True, tested
    assert "Connected to Google as info@acme.example" in tested["message"]

    # A client's account stays with that client; yours goes anywhere (it's in all already).
    crossed = owner.put("/projects/globex-app/connections/acme-info", json={})
    assert crossed.status_code == 409
    assert "only be used by that client's projects" in crossed.text
    assert owner.put("/projects/acme-blog/connections/acme-info", json={}).status_code == 200
    wrong_project = owner.post(
        "/google/signin", json={"access": ["mail_read"], "owner": "acme", "project": "globex-app"}
    )
    assert wrong_project.status_code == 409

    # The project's default answers when no account is named; naming one picks it.
    made = owner.put("/projects/acme-web/connections/acme-info", json={"is_default": True})
    assert made.status_code == 200, made.text
    assert call("acme-web", "search_mail")["messages"][0]["to"] == "info@acme.example"
    assert call("acme-web", "search_mail", "acme-ops")["messages"][0]["to"] == "ops@acme.example"
    assert call("acme-web", "search_mail", "me-gmail")["messages"][0]["to"] == "me@gmail.com"
    assert call("globex-app", "search_mail", "globex-sales")["account"] == "sales@globex.example"
    assert call("globex-app", "search_mail", "me-gmail")["account"] == "me@gmail.com"
    with pytest.raises(RefusalError, match="not available to project 'globex-app'"):
        call("globex-app", "search_mail", "acme-info")
    with pytest.raises(RefusalError, match="not available to project 'acme-web'"):
        call("acme-web", "search_mail", "globex-sales")
    with pytest.raises(RefusalError, match="no default"):
        call("globex-app", "search_mail")  # two accounts there, no default: name one

    # Agents see which address each account is ("send from the info@ mailbox").
    listed = {i["name"]: i.get("account") for i in bound_instances(Caller("acme-web", "s", "t"))}
    assert listed == {
        "acme-info": "info@acme.example",
        "acme-ops": "ops@acme.example",
        "me-gmail": "me@gmail.com",
    }


def test_every_write_asks_first_unless_the_owner_lets_it_run(
    accounts: TestClient, fake: FakeGoogle
) -> None:
    owner = accounts
    email: dict[str, Any] = {"to": ["anna@client.example"], "subject": "Hi", "body": "Hello"}
    with pytest.raises(RefusalError, match="explicit instance"):
        call("acme-web", "send_mail", **email)  # a write names its account
    held = call("acme-web", "send_mail", "acme-info", **email)
    assert held["status"] == "held_for_approval"
    with session_scope() as session:  # Approvals says what it is
        assert inbox.heading(decisions.get_decision(session, held["decision_id"])) == (
            "A message to real people"
        )
    assert fake.sent == []  # dev data or not: it waits
    with pytest.raises(ToolError, match='wasn\'t given "Move mail to the trash"'):
        call("acme-web", "trash_mail", "acme-info", message_id="m-info1")

    tools = {t["name"]: t for t in owner.get("/connections/acme-info/google").json()["tools"]}
    assert (tools["send_mail"]["rule"], tools["send_mail"]["allowed"]) == ("ask", True)
    assert (tools["trash_mail"]["allowed"], tools["search_mail"]["rule"]) == (False, None)
    assert tools["send_mail"]["fixed"] is True
    # Sending follows the rule for every email connection: nothing lets it run by itself.
    let = owner.put("/connections/acme-info/write-rules", json={"rules": {"send_mail": "run"}})
    assert let.status_code == 409, let.text
    with session_scope() as session:  # set before that was so: it stops counting
        found = instances.get_instance(session, "acme-info")
        found.config = {**found.config, "write_rules": {"send_mail": "run"}}
    assert call("acme-web", "send_mail", "acme-info", **email)["status"] == "held_for_approval"
    assert fake.sent == []
    # Any other write can run, on this account only.
    with session_scope() as session:
        for name in ("acme-info", "acme-ops"):
            found = instances.get_instance(session, name)
            found.config = {**found.config, "access": [*found.config["access"], "mail_trash"]}
    let = owner.put("/connections/acme-info/write-rules", json={"rules": {"trash_mail": "run"}})
    assert let.status_code == 200, let.text
    assert call("acme-web", "trash_mail", "acme-info", message_id="m-info1")["trashed"]
    held = call("acme-web", "trash_mail", "acme-ops", message_id="m-info1")
    assert held["status"] == "held_for_approval"
    reads = owner.put("/connections/acme-info/write-rules", json={"rules": {"search_mail": "run"}})
    assert reads.status_code == 409  # only writes have a rule
    back = owner.put("/connections/acme-info/write-rules", json={"rules": {}})
    assert back.json()["tools"][2]["rule"] == "ask"
    assert call("acme-web", "send_mail", "acme-info", **email)["status"] == "held_for_approval"


def test_scopes_refresh_sign_in_again_and_removal(accounts: TestClient, fake: FakeGoogle) -> None:
    owner = accounts
    # Unticked on Google's page: only what was granted is kept.
    partial = sign_in(owner, fake, "partial", owner="acme", name="acme-partial")
    assert partial["state"] == "done"
    with session_scope() as session:
        assert instances.get_instance(session, "acme-partial").config["access"] == ["mail_read"]

    # The access token near expiry: refreshed once, and stored.
    with session_scope() as session:
        ref = instances.get_instance(session, "acme-info").secret_ref
    data = json.loads(get_vault().resolve(ref).reveal())
    get_vault().store(ref, json.dumps({**data, "expires_at": int(time.time()) - 10}))
    call("acme-web", "search_mail", "acme-info")
    assert fake.refreshes == 1
    assert json.loads(get_vault().resolve(ref).reveal())["access_token"] == "at2:info@acme.example"

    # Signing in again (more access) must be the same account.
    other = sign_in(
        owner, fake, "acme-ops", access=["mail_read", "drive_files"], replace="acme-info"
    )
    assert other["state"] == "failed"
    assert "Sign in as info@acme.example" in other["error"]
    again = sign_in(owner, fake, "acme", access=["mail_read", "drive_files"], replace="acme-info")
    assert again == {"state": "done", "name": "acme-info", "error": None}
    with session_scope() as session:
        assert instances.get_instance(session, "acme-info").config["access"] == [
            "mail_read",
            "drive_files",
        ]

    # Cancelled on Google's page.
    started = owner.post("/google/signin", json={"access": ["mail_read"], "owner": "acme"})
    state = started.json()["state"]
    assert (
        owner.get("/oauth/callback", params={"state": state, "error": "access_denied"}).status_code
        == 400
    )
    assert owner.get(f"/google/signin/{state}").json()["state"] == "failed"

    # Removed: Google is told, and the tokens leave the vault.
    assert owner.delete("/connections/acme-ops", params={"force": True}).status_code == 204
    assert fake.revoked == ["rt:ops@acme.example"]


def test_more_than_one_google_app(accounts: TestClient, fake: FakeGoogle) -> None:
    """A client's own Google Cloud project as a second app: an account signs in and
    refreshes through the app it chose; the others keep the first."""
    owner = accounts
    added = owner.post(
        "/google/apps",
        json={
            "name": "Acme's Google Cloud", "client_id": ACME_CLIENT,
            "client_secret": "acme-s3cret",
        },
    )  # fmt: skip
    assert added.status_code == 201, added.text
    assert "acme-s3cret" not in added.text
    apps = {a["id"]: a for a in added.json()["apps"]}
    assert apps["google-app"]["accounts"] == 4  # the fixture's, signed in through the first
    assert apps["acme-s-google-cloud"] == {
        "id": "acme-s-google-cloud", "name": "Acme's Google Cloud", "client_id": ACME_CLIENT,
        "accounts": 0,
    }  # fmt: skip
    twice = owner.post(
        "/google/apps", json={"name": "Again", "client_id": ACME_CLIENT, "client_secret": "x" * 8}
    )
    assert twice.status_code == 409
    assert "already set up, as 'Acme's Google Cloud'" in twice.text

    # Through Acme's app: Google is asked with its client, and the account remembers it.
    started = owner.post(
        "/google/signin",
        json={"access": ["mail_read"], "owner": "acme", "app": "acme-s-google-cloud"},
    )
    assert parse_qs(urlsplit(str(started.json()["url"])).query)["client_id"] == [ACME_CLIENT]
    done = sign_in(
        owner, fake, "acme-team", owner="acme", name="acme-own", app="acme-s-google-cloud"
    )
    assert done["state"] == "done"
    listed = {a["name"]: a for a in owner.get("/google/app").json()["accounts"]}
    assert listed["acme-own"] == {
        "name": "acme-own", "email": "team@acme.example", "owner": "acme", "shared": False,
        "app": "acme-s-google-cloud",
    }  # fmt: skip
    assert listed["acme-info"]["app"] == "google-app"

    # A refresh goes through the account's own app (the fake refuses any other client).
    with session_scope() as session:
        own = instances.get_instance(session, "acme-own").secret_ref
        first = instances.get_instance(session, "acme-info").secret_ref
    for ref in (own, first):
        data = json.loads(get_vault().resolve(ref).reveal())
        get_vault().store(ref, json.dumps({**data, "expires_at": int(time.time()) - 10}))
    assert owner.put("/projects/acme-web/connections/acme-own", json={}).status_code == 200
    call("acme-web", "search_mail", "acme-own")
    call("acme-web", "search_mail", "acme-info")
    assert fake.refreshes == 2
    assert json.loads(get_vault().resolve(own).reveal())["app"] == "acme-s-google-cloud"

    # Signed in before there could be more than one app: the first one, as before.
    data = json.loads(get_vault().resolve(first).reveal())
    del data["app"]
    get_vault().store(first, json.dumps({**data, "expires_at": int(time.time()) - 10}))
    call("acme-web", "search_mail", "acme-info")
    assert fake.refreshes == 3

    # Its app removed: the account says so, and signing in again through another works.
    assert owner.delete("/google/apps/acme-s-google-cloud").status_code == 204
    data = json.loads(get_vault().resolve(own).reveal())
    get_vault().store(own, json.dumps({**data, "expires_at": int(time.time()) - 10}))
    with pytest.raises(ToolError, match="signed in through was removed"):
        call("acme-web", "search_mail", "acme-own")
    again = sign_in(owner, fake, "acme-team", replace="acme-own", app="google-app")
    assert again == {"state": "done", "name": "acme-own", "error": None}
    with session_scope() as session:
        assert instances.get_instance(session, "acme-own").config["app"] == "google-app"


def test_an_app_stored_before_there_could_be_several(owner: TestClient, fake: FakeGoogle) -> None:
    get_vault().store(
        google_oauth.APP_REF, json.dumps({"client_id": CLIENT_ID, "client_secret": "s3cret-value"})
    )
    apps = owner.get("/google/app").json()["apps"]
    assert apps == [
        {"id": "google-app", "name": "Your Google app", "client_id": CLIENT_ID, "accounts": 0}
    ]
