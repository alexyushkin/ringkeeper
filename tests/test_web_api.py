"""The web app's API: setup, sign-in, settings, secrets, projects, connections,
tasks, activity, voice and live events. Every endpoint needs the admin token
and (except setup and sign-in) a signed-in owner; secrets never come back."""

import json
import threading
import time
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx2
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text, update
from typer.testing import CliRunner

from ringkeeper import bootstrap, voice
from ringkeeper.admin_token import stored_web_token
from ringkeeper.api.app import create_app
from ringkeeper.cli.main import app as cli
from ringkeeper.config import get_settings
from ringkeeper.db import session_scope
from ringkeeper.models import (
    AuditEvent,
    AuditOutcome,
    Instance,
    LoginAttempt,
    OwnerSession,
    Passkey,
    Setting,
    Task,
    TaskStatus,
)
from ringkeeper.services import runtime, secrets_in, tasks
from ringkeeper.telegram import api as telegram_api
from ringkeeper.vault import get_vault
from tests.fake_telegram import AUDIO_MARKER, BOT_TOKEN, FakeStt, FakeTelegram
from tests.helpers import table_text

PASSWORD = "correct horse battery staple"
API_KEY = "sk-ant-api03-" + "k" * 40


@pytest.fixture
def setup_code(database_url: str, data_dir: Path) -> str:
    code = bootstrap.ensure_setup_code(get_settings())
    assert code is not None
    return code


@pytest.fixture
def admin(database_url: str, data_dir: Path) -> Iterator[TestClient]:
    result = CliRunner().invoke(cli, ["auth", "api-token"])
    token = result.stdout.strip()
    headers = {"Authorization": f"Bearer {token}", "X-Ringkeeper-Client": "198.51.100.7"}
    with TestClient(create_app(), headers=headers) as client:
        yield client


@pytest.fixture
def owner(admin: TestClient, setup_code: str) -> TestClient:
    """The admin client, signed in as the owner."""
    response = admin.post("/setup/owner", json={"password": PASSWORD, "setup_code": setup_code})
    assert response.status_code == 201, response.text
    admin.headers["X-Ringkeeper-Session"] = response.json()["token"]
    return admin


def audit_text() -> str:
    with session_scope() as session:
        rows = table_text(session, "audit_events")
        return "\n".join(rows)


# --- Setup ---


def test_setup_needs_the_setup_code_and_happens_once(
    admin: TestClient, setup_code: str, data_dir: Path
) -> None:
    status = admin.get("/setup/status").json()
    assert status["owner"] is False
    assert "setup-code.txt" in status["setup_code_hint"]
    assert (data_dir / "setup-code.txt").read_text().strip() == setup_code

    wrong = admin.post("/setup/owner", json={"password": PASSWORD, "setup_code": "AAAA-BBBB"})
    assert wrong.status_code == 403
    short = admin.post("/setup/owner", json={"password": "short", "setup_code": setup_code})
    assert short.status_code == 409
    assert "12 characters" in short.json()["detail"]

    # Lowercase and without the dash is fine.
    code = setup_code.replace("-", "").lower()
    created = admin.post("/setup/owner", json={"password": PASSWORD, "setup_code": code})
    assert created.status_code == 201
    assert created.json()["token"]
    assert not (data_dir / "setup-code.txt").exists()  # the code is gone
    again = admin.post("/setup/owner", json={"password": PASSWORD, "setup_code": setup_code})
    assert again.status_code == 409
    assert admin.get("/setup/status").json()["setup_code_hint"] is None


def test_the_setup_code_survives_a_restart_until_used(database_url: str, data_dir: Path) -> None:
    first = bootstrap.ensure_setup_code(get_settings())
    assert bootstrap.ensure_setup_code(get_settings()) == first


def test_bootstrap_creates_the_master_key_and_admin_token(
    database_url: str, data_dir: Path
) -> None:
    bootstrap.run(get_settings())
    assert (data_dir / "master.key").exists()
    assert (data_dir / "setup-code.txt").exists()
    result = CliRunner().invoke(cli, ["auth", "api-token"])
    assert result.stdout.startswith("rk_admin_")
    assert "created" not in result.output  # it existed already


def age_confirmation(minutes: int = 11) -> None:
    """Pretend the owner signed in (or confirmed) a while ago."""
    with session_scope() as session:
        session.execute(
            update(OwnerSession).values(confirmed_at=datetime.now(UTC) - timedelta(minutes=minutes))
        )


def test_the_recovery_key_needs_a_recent_confirmation(owner: TestClient) -> None:
    first = owner.post("/setup/recovery-key")  # right after setup: just signed in
    assert first.status_code == 200
    body = first.json()
    assert body["note"] == "If you lose this file, you'll need to re-enter your connection secrets."
    assert len(body["key"]) == 44
    assert owner.post("/setup/recovery-key/saved").status_code == 204
    assert owner.get("/setup/status").json()["recovery_saved"] is True
    age_confirmation()
    stale = owner.post("/setup/recovery-key")
    assert stale.status_code == 403
    assert stale.headers["X-Ringkeeper-Confirm"] == "password"
    assert owner.get("/auth/session").json()["confirmed"] is False
    assert owner.post("/auth/confirm", json={"password": "wrong"}).status_code == 401
    assert owner.post("/auth/confirm", json={"password": PASSWORD}).status_code == 204
    again = owner.post("/setup/recovery-key")
    assert again.json()["key"] == body["key"]


# --- Sign-in ---


def test_sign_in_sessions_and_sign_out(owner: TestClient) -> None:
    del owner.headers["X-Ringkeeper-Session"]
    assert owner.get("/settings").status_code == 401  # needs an owner session
    assert owner.post("/auth/login", json={"password": "nope"}).status_code == 401
    login = owner.post("/auth/login", json={"password": PASSWORD})
    assert login.status_code == 200
    token = login.json()["token"]
    owner.headers["X-Ringkeeper-Session"] = token
    assert owner.get("/auth/session").json()["method"] == "password"
    assert owner.get("/settings").status_code == 200
    assert owner.post("/auth/logout").status_code == 204
    assert owner.get("/settings").status_code == 401
    with session_scope() as session:  # only a hash of the token is stored
        stored = session.execute(text("SELECT token_hash FROM owner_sessions")).scalars().all()
        assert token not in stored


def age_attempts(seconds: int) -> None:
    with session_scope() as session:
        for attempt in session.scalars(select(LoginAttempt)):
            attempt.at -= timedelta(seconds=seconds)


def test_sign_in_is_rate_limited_per_client(owner: TestClient) -> None:
    for _ in range(5):
        assert owner.post("/auth/login", json={"password": "wrong"}).status_code == 401
    blocked = owner.post("/auth/login", json={"password": PASSWORD})
    assert blocked.status_code == 429
    assert blocked.headers["Retry-After"] == "900"
    # Everyone else is paced (one guess every few seconds), never locked out.
    other = {"X-Ringkeeper-Client": "203.0.113.9"}
    paced = owner.post("/auth/login", json={"password": PASSWORD}, headers=other)
    assert paced.status_code == 429
    assert paced.headers["Retry-After"] == "3"
    age_attempts(5)
    assert owner.post("/auth/login", json={"password": PASSWORD}, headers=other).status_code == 200
    assert owner.post("/auth/login", json={"password": PASSWORD}).status_code == 429  # still


def test_clients_without_an_address_are_paced_not_blocked(owner: TestClient) -> None:
    direct = {"X-Ringkeeper-Client": "direct"}  # the web app without a trusted proxy
    for _ in range(6):
        assert owner.post("/auth/login", json={"password": "x"}, headers=direct).status_code == 401
        age_attempts(5)
    assert owner.post("/auth/login", json={"password": PASSWORD}, headers=direct).status_code == 200


def test_changing_the_password_removes_passkeys(owner: TestClient) -> None:
    add_passkey()
    assert len(owner.get("/auth/passkeys").json()) == 1
    changed = owner.post(
        "/auth/password", json={"current": PASSWORD, "new": "a much better passphrase"}
    )
    assert changed.status_code == 204
    assert owner.get("/auth/passkeys").json() == []


def test_changing_the_password_signs_out_other_browsers(owner: TestClient) -> None:
    other = owner.post("/auth/login", json={"password": PASSWORD}).json()["token"]
    changed = owner.post(
        "/auth/password", json={"current": PASSWORD, "new": "a much better passphrase"}
    )
    assert changed.status_code == 204
    assert owner.get("/settings").status_code == 200  # this browser stays in
    gone = owner.get("/settings", headers={"X-Ringkeeper-Session": other})
    assert gone.status_code == 401


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/settings"), ("PATCH", "/settings"), ("POST", "/stop-all"),
        ("PUT", "/secrets/claude"), ("GET", "/connections"), ("POST", "/projects"),
        ("GET", "/tasks"), ("GET", "/audit"), ("GET", "/audit.csv"), ("GET", "/threads"),
        ("POST", "/voice/transcribe"), ("GET", "/events"), ("GET", "/auth/passkeys"),
    ],
)  # fmt: skip
def test_owner_endpoints_need_the_admin_token_and_a_session(
    owner: TestClient, database_url: str, method: str, path: str
) -> None:
    session = owner.headers["X-Ringkeeper-Session"]
    with TestClient(create_app()) as anonymous:
        no_admin = anonymous.request(method, path, headers={"X-Ringkeeper-Session": session})
        assert no_admin.status_code == 401
    del owner.headers["X-Ringkeeper-Session"]
    assert owner.request(method, path).status_code == 401
    bogus = owner.request(method, path, headers={"X-Ringkeeper-Session": "made-up"})
    assert bogus.status_code == 401


def test_passkeys_need_the_https_origin(owner: TestClient) -> None:
    response = owner.post("/auth/passkeys/register/options")
    assert response.status_code == 409
    assert "RINGKEEPER_WEB_ORIGIN" in response.json()["detail"]


def test_passkey_registration_options(owner: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RINGKEEPER_WEB_ORIGIN", "https://ringkeeper.example.ts.net")
    options = owner.post("/auth/passkeys/register/options").json()
    assert options["options"]["rp"]["id"] == "ringkeeper.example.ts.net"
    assert options["challenge_id"]
    bad = owner.post(
        "/auth/passkeys/register",
        json={"challenge_id": options["challenge_id"], "credential": {"id": "x"}},
    )
    assert bad.status_code == 409
    replay = owner.post(
        "/auth/passkeys/register",
        json={"challenge_id": options["challenge_id"], "credential": {"id": "x"}},
    )
    assert replay.status_code == 401  # a challenge works once
    options = owner.post("/auth/passkeys/register/options").json()
    assert options["options"]["authenticatorSelection"]["userVerification"] == "required"


def test_adding_a_passkey_needs_a_recent_confirmation(
    owner: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RINGKEEPER_WEB_ORIGIN", "https://ringkeeper.example.ts.net")
    age_confirmation()
    assert owner.post("/auth/passkeys/register/options").status_code == 403
    assert (
        owner.post(
            "/auth/passkeys/register", json={"challenge_id": "x", "credential": {}}
        ).status_code
        == 403
    )
    assert owner.post("/auth/confirm", json={"password": PASSWORD}).status_code == 204
    assert owner.post("/auth/passkeys/register/options").status_code == 200


def add_passkey() -> None:
    with session_scope() as session:
        session.add(Passkey(credential_id=b"cred", public_key=b"key", name="Phone"))


def test_passkey_sign_in_isnt_held_up_by_password_guesses(
    owner: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RINGKEEPER_WEB_ORIGIN", "https://ringkeeper.example.ts.net")
    add_passkey()
    for _ in range(5):
        owner.post("/auth/login", json={"password": "wrong"})
    assert owner.post("/auth/login", json={"password": PASSWORD}).status_code == 429
    options = owner.post("/auth/passkeys/login/options").json()
    attempt = owner.post(
        "/auth/passkeys/login",
        json={"challenge_id": options["challenge_id"], "credential": {"id": "AAAA"}},
    )
    assert attempt.status_code == 401  # checked (and refused), not rate limited
    with session_scope() as session:  # and not counted as a guess
        keys = session.execute(
            text("SELECT count(*) FROM login_attempts WHERE NOT ok")
        ).scalar_one()
        assert keys == 5


def test_passkey_challenges_are_capped(owner: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RINGKEEPER_WEB_ORIGIN", "https://ringkeeper.example.ts.net")
    add_passkey()
    for _ in range(20):
        assert owner.post("/auth/passkeys/login/options").status_code == 200
    assert owner.post("/auth/passkeys/login/options").status_code == 429
    with session_scope() as session:  # expired ones are cleared on the next request
        expired = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
        for row in session.scalars(
            select(Setting).where(Setting.key.startswith("webauthn.challenge."))
        ):
            row.value = {**row.value, "expires": expired}
    assert owner.post("/auth/passkeys/login/options").status_code == 200


# --- The web token ---


def web_token() -> str:
    bootstrap.ensure_admin_token(get_settings())  # creates the web token too
    token = stored_web_token()
    assert token is not None
    return token


def test_the_web_token_needs_a_signed_in_owner(owner: TestClient) -> None:
    session = owner.headers["X-Ringkeeper-Session"]
    web = {"Authorization": f"Bearer {web_token()}"}
    with TestClient(create_app(), headers=web) as client:
        # Setup and sign-in work on their own, and say little.
        status = client.get("/setup/status").json()
        assert status == {
            "owner": True, "signed_in": False, "setup_code_hint": None, "recovery_saved": None,
            "claude": None, "telegram": None, "projects": None, "connections": None,
            "done": None, "workspaces_root": None,
        }  # fmt: skip
        assert client.post("/auth/login", json={"password": "wrong"}).status_code == 401
        # Everything else needs the session, even what the admin token reads alone.
        for path in ("/projects", "/decisions", "/instances", "/settings"):
            assert client.get(path).status_code == 401, path
            ok = client.get(path, headers={"X-Ringkeeper-Session": session})
            assert ok.status_code == 200, path
        signed_in = client.get("/setup/status", headers={"X-Ringkeeper-Session": session})
        assert signed_in.json()["signed_in"] is True
        assert signed_in.json()["projects"] == 0
        assert client.post("/messages", json={"text": "hi"}).status_code == 401
    del owner.headers["X-Ringkeeper-Session"]
    assert owner.get("/projects").status_code == 200  # the admin token alone still works
    assert owner.get("/setup/status").json()["projects"] == 0


# --- Changes that need a recent confirmation ---


def test_redirecting_approvals_needs_a_recent_confirmation(owner: TestClient) -> None:
    age_confirmation()
    for change in ({"telegram_chat_ids": [42]}, {"telegram": True}, {"telegram_requests": True}):
        stale = owner.patch("/settings", json=change)
        assert stale.status_code == 403, change
        assert stale.headers["X-Ringkeeper-Confirm"] == "password"
    assert owner.patch("/settings", json={"retention_days": 30}).status_code == 200
    assert owner.put("/secrets/claude", json={"value": API_KEY}).status_code == 403
    assert owner.put("/secrets/telegram", json={"token": BOT_TOKEN}).status_code == 403
    assert owner.post("/auth/confirm", json={"password": PASSWORD}).status_code == 204
    assert owner.patch("/settings", json={"telegram_chat_ids": [42]}).status_code == 200


def test_loosening_connections_needs_a_recent_confirmation(
    owner: TestClient, target_dsn: str
) -> None:
    assert owner.post("/projects", json={"name": "acme-web", "owner": "acme"}).status_code == 201
    connection = {"name": "acme-db", "type": "supabase", "env": "prod", "owner": "acme",
                  "secret": target_dsn}  # fmt: skip
    assert owner.post("/connections", json=connection).status_code == 201
    age_confirmation()
    assert owner.post("/connections", json={**connection, "name": "other"}).status_code == 403
    assert owner.patch("/connections/acme-db", json={"env": "staging"}).status_code == 403
    assert owner.put("/connections/acme-db/secret", json={"secret": target_dsn}).status_code == 403
    assert owner.patch("/connections/acme-db", json={"owner": "acme inc"}).status_code == 200
    preapproved = {"preapproved_writes": ["execute"]}
    bind = owner.put("/projects/acme-web/connections/acme-db", json=preapproved)
    assert bind.status_code == 403
    assert owner.put("/projects/acme-web/connections/acme-db", json={}).status_code == 200
    assert owner.patch("/projects/acme-web", json={"autonomy": "relaxed"}).status_code == 403
    assert owner.patch("/projects/acme-web", json={"description": "x"}).status_code == 200


# --- Input limits ---


def test_validation_errors_never_echo_the_input(owner: TestClient) -> None:
    secret = "sk-ant-api03-" + "z" * 40
    response = owner.put("/secrets/claude", json={"mode": "nonsense", "value": secret})
    assert response.status_code == 422
    assert secret not in response.text
    assert response.json()["detail"][0]["loc"] == ["body", "mode"]


def test_request_bodies_are_capped(owner: TestClient) -> None:
    big = {"name": "acme-web", "owner": "acme", "description": "x" * 1_100_000}
    assert owner.post("/projects", json=big).status_code == 413
    chunked = owner.post(
        "/projects",
        content=iter(
            [b'{"name": "acme-web", "owner": "acme", "description": "', b"x" * 1_100_000, b'"}']
        ),
        headers={"Content-Type": "application/json"},
    )
    assert chunked.status_code == 413


@pytest.mark.parametrize(
    "path", ["/var/run/docker.sock", "/", "../outside", "acme/../../etc", "C:\\Users", "~/x"]
)
def test_workspaces_from_the_app_stay_inside_the_root(owner: TestClient, path: str) -> None:
    project = {"name": "acme-web", "owner": "acme", "workspace_path": path}
    assert owner.post("/projects", json=project).status_code == 422
    assert (
        owner.post("/projects", json={**project, "workspace_path": "acme/web"}).status_code == 201
    )
    assert owner.patch("/projects/acme-web", json={"workspace_path": path}).status_code == 422


def test_a_weak_database_password_is_warned_about(caplog: pytest.LogCaptureFixture) -> None:
    settings = get_settings().model_copy(
        update={"database_url": "postgresql+psycopg://ringkeeper:ringkeeper@postgres/ringkeeper"}
    )
    bootstrap.warn_weak_database_password(settings)
    assert "database password is weak" in caplog.text
    caplog.clear()
    strong = settings.model_copy(
        update={"database_url": "postgresql+psycopg://ringkeeper:Qm7x2Lp9Zr4Tn8Wv@postgres/rk"}
    )
    bootstrap.warn_weak_database_password(strong)
    assert caplog.text == ""


def test_too_many_live_streams_are_refused(owner: TestClient) -> None:
    from ringkeeper.api import live_routes  # noqa: PLC0415

    live_routes._streams["open"] = live_routes.MAX_STREAMS  # pyright: ignore[reportPrivateUsage]
    try:
        assert owner.get("/events").status_code == 429
    finally:
        live_routes._streams["open"] = 0  # pyright: ignore[reportPrivateUsage]


# --- Settings ---


def test_settings_change_live_and_reset_to_env(owner: TestClient) -> None:
    before = owner.get("/settings").json()
    assert before["values"]["telegram_requests"] is False
    assert before["overridden"] == []
    changed = owner.patch(
        "/settings", json={"telegram_requests": True, "max_concurrent_workers": 2}
    )
    assert changed.status_code == 200
    body = changed.json()
    assert body["values"]["telegram_requests"] is True
    assert body["overridden"] == ["max_concurrent_workers", "telegram_requests"]
    assert runtime.current_settings().max_concurrent_workers == 2
    reset = owner.patch("/settings", json={"max_concurrent_workers": None}).json()
    assert reset["overridden"] == ["telegram_requests"]
    assert owner.patch("/settings", json={"database_url": "x"}).status_code == 422
    assert owner.patch("/settings", json={"max_concurrent_workers": 0}).status_code == 422
    assert "changed settings: max_concurrent_workers, telegram_requests" in audit_text()


def test_stop_everything_pauses_and_stops_tasks(owner: TestClient, task_registry: None) -> None:
    with session_scope() as session:
        task = tasks.create_task(session, get_settings(), project="acme-web", prompt="go")
        task_id = task.id
    stopped = owner.post("/stop-all")
    assert stopped.json() == {"stopped": 1}
    assert runtime.current_settings().paused is True
    with session_scope() as session:
        assert session.get_one(Task, task_id).status is TaskStatus.STOPPED
    owner.post("/resume-all")
    assert runtime.current_settings().paused is False


# --- Secrets ---


def test_the_claude_key_is_checked_stored_and_never_returned(
    owner: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    checked: list[str] = []
    monkeypatch.setattr(secrets_in, "check_anthropic_key", checked.append)
    wrong_kind = owner.put("/secrets/claude", json={"value": "sk-ant-oat01-" + "x" * 40})
    assert wrong_kind.status_code == 422
    assert "sk-ant-oat01" not in wrong_kind.text
    stored = owner.put("/secrets/claude", json={"value": f"  {API_KEY}\n"})
    assert stored.status_code == 200
    assert stored.json() == {
        "stored": True, "verified": True, "message": "Anthropic accepted the key.",
    }  # fmt: skip
    assert checked == [API_KEY]
    settings = get_settings()
    assert get_vault().resolve(settings.claude_api_key_ref).reveal() == API_KEY
    for path in ("/settings", "/setup/status", "/audit"):
        assert API_KEY not in owner.get(path).text
    assert API_KEY not in audit_text()
    assert owner.get("/settings").json()["claude"]["api_key"] is True


def test_a_rejected_claude_key_is_not_stored(
    owner: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def reject(_key: str) -> None:
        raise secrets_in.RuleViolationError("Anthropic didn't accept this key.")

    monkeypatch.setattr(secrets_in, "check_anthropic_key", reject)
    response = owner.put("/secrets/claude", json={"value": API_KEY})
    assert response.status_code == 422
    assert owner.get("/settings").json()["claude"]["api_key"] is False


def test_subscription_mode_switches_the_mode(owner: TestClient) -> None:
    token = "sk-ant-oat01-" + "s" * 40
    response = owner.put("/secrets/claude", json={"mode": "subscription", "value": token})
    assert response.json()["verified"] is None
    assert owner.get("/settings").json()["values"]["claude_auth_mode"] == "subscription"


def test_telegram_token_and_chat_detection(
    owner: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeTelegram()
    monkeypatch.setattr(telegram_api, "transport", httpx2.MockTransport(fake.handle))
    assert owner.put("/secrets/telegram", json={"token": "nonsense"}).status_code == 422
    stored = owner.put("/secrets/telegram", json={"token": BOT_TOKEN})
    assert stored.json() == {"stored": True, "bot": "@ringkeeper_test_bot"}
    assert BOT_TOKEN not in stored.text
    assert BOT_TOKEN not in audit_text()
    fake.text("hi")
    assert owner.get("/telegram/detect").status_code == 422  # the pairing code is required
    assert owner.get("/telegram/detect", params={"code": "K7M2QX"}).json()["chats"] == []
    fake.text("k7m2qx")
    chats = owner.get("/telegram/detect", params={"code": "K7M2QX"}).json()["chats"]
    assert chats == [{"id": 1001, "type": "private", "name": "Alice", "username": None}]


# --- Projects and connections ---


def test_projects_and_connections(
    owner: TestClient, target_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = {"name": "acme-web", "owner": "acme", "description": "Acme's site"}
    assert owner.post("/projects", json=project).status_code == 201
    assert owner.post("/projects", json=project).status_code == 409
    patched = owner.patch("/projects/acme-web", json={"aliases": ["acme site"], "checks": []})
    assert patched.status_code == 200
    assert owner.get("/projects/acme-web").json()["aliases"] == ["acme site"]

    # A connection: the secret goes in once and never comes back.
    connection = {"name": "acme-db", "type": "supabase", "env": "prod", "owner": "acme",
                  "secret": target_dsn}  # fmt: skip
    bad = owner.post("/connections", json={**connection, "secret": "hunter2"})
    assert bad.status_code == 422
    assert "postgresql://" in bad.json()["detail"]
    created = owner.post("/connections", json=connection)
    assert created.status_code == 201, created.text
    assert created.json()["secret_stored"] is True
    n8n = {"name": "acme-flows", "type": "n8n", "env": "dev", "owner": "acme", "secret": "k"}
    assert owner.post("/connections", json=n8n).status_code == 422  # needs its address

    tested = owner.post("/connections/acme-db/test").json()
    assert tested["ok"] is True, tested
    assert tested["message"].startswith("Connected: read")
    listed = owner.get("/connections").json()
    assert listed[0]["last_test_ok"] is True

    # Prod can't be a default; non-default is fine.
    prod_default = owner.put("/projects/acme-web/connections/acme-db", json={"is_default": True})
    assert prod_default.status_code == 409
    bound = owner.put("/projects/acme-web/connections/acme-db", json={"is_default": False})
    assert bound.status_code == 200
    assert owner.get("/connections").json()[0]["projects"] == ["acme-web"]

    # A wrong secret: the test fails with a readable message, without the secret.
    wrong = target_dsn.replace("pw-target", "pw-wrong")
    assert owner.put("/connections/acme-db/secret", json={"secret": wrong}).status_code == 204
    failed = owner.post("/connections/acme-db/test").json()
    assert failed["ok"] is False
    assert "pw-wrong" not in failed["message"]

    for path in ("/connections", "/projects/acme-web", "/audit", "/instances"):
        body = owner.get(path).text
        assert target_dsn not in body
        assert "pw-target" not in body
        assert "file://" not in body
    assert "pw-target" not in audit_text()
    assert "pw-wrong" not in audit_text()

    assert owner.delete("/connections/acme-db").status_code == 409  # still connected
    assert owner.delete("/projects/acme-web/connections/acme-db").status_code == 204
    assert owner.delete("/connections/acme-db").status_code == 204
    assert owner.delete("/projects/acme-web").status_code == 204
    with session_scope() as session:
        assert session.scalars(select(Instance)).all() == []
        actions = session.scalars(
            select(AuditEvent.tool).where(AuditEvent.outcome == AuditOutcome.CHANGED)
        ).all()
    assert {"project.create", "connection.create", "connection.test", "binding.set",
            "secret.connection", "connection.delete", "project.delete"} <= set(actions)  # fmt: skip


# --- Tasks, threads and activity ---


def test_tasks_transcripts_and_threads(owner: TestClient, task_registry: None) -> None:
    with session_scope() as session:
        from ringkeeper.services import threads  # noqa: PLC0415

        message = threads.post_message(session, "Add a plan formatter", author="o", source="api")
        task = tasks.create_task(
            session, get_settings(), project="acme-web", prompt="go", thread_id=message.thread_id,
            message_id=message.id,
        )  # fmt: skip
        first = task.sessions[0]
        tasks.add_event(session, first.id, 1, "sdk.assistant", {"message": {"content": []}})
        tasks.add_event(session, first.id, 2, "ringkeeper.diff", {"text": "+def f(): ..."})
        task_id = str(task.id)
    cards = owner.get("/tasks", params={"project": "acme-web"}).json()
    assert [c["id"] for c in cards] == [task_id]
    detail = owner.get(f"/tasks/{task_id}").json()
    assert detail["diff"] == "+def f(): ..."
    assert detail["session_list"][0]["role"] == "worker"
    events = owner.get(f"/tasks/{task_id}/events").json()
    assert [e["type"] for e in events["events"]] == ["sdk.assistant", "ringkeeper.diff"]
    assert events["retention_days"] == 90
    later = owner.get(f"/tasks/{task_id}/events", params={"after": events["events"][0]["id"]})
    assert len(later.json()["events"]) == 1
    [thread] = owner.get("/threads").json()
    assert thread["last_message"] == "Add a plan formatter"
    assert thread["tasks"] == {"queued": 1}
    assert owner.post(f"/tasks/{task_id}/stop").json()["status"] == "stopped"


def test_activity_filters_and_csv_export(owner: TestClient) -> None:
    owner.post("/projects", json={"name": "acme-web", "owner": "acme"})
    owner.post("/projects", json={"name": "=cmd", "owner": "x"})  # refused: not a slug
    owner.post("/projects", json={"name": "globex", "owner": "globex"})
    acme = owner.get("/audit", params={"project": "acme-web"}).json()
    assert [r["tool"] for r in acme] == ["project.create"]
    export = owner.get("/audit.csv", params={"project": "acme-web"})
    assert export.headers["content-type"].startswith("text/csv")
    assert 'filename="ringkeeper-activity-acme-web.csv"' in export.headers["content-disposition"]
    lines = export.text.strip().splitlines()
    assert lines[0].startswith("at,project,instance")
    assert len(lines) == 2
    assert "globex" not in export.text


def test_csv_cells_that_look_like_formulas_are_neutralized() -> None:
    from ringkeeper.api.work_routes import _csv_safe  # pyright: ignore[reportPrivateUsage]  # noqa: PLC0415

    assert _csv_safe("=HYPERLINK(1)") == "'=HYPERLINK(1)"
    assert _csv_safe("-2") == "'-2"
    assert _csv_safe("fine") == "fine"


# --- Voice ---


@pytest.fixture
def stt(monkeypatch: pytest.MonkeyPatch) -> FakeStt:
    fake = FakeStt(text="count globex customers")
    monkeypatch.setattr(voice, "transport", httpx2.MockTransport(fake.handle))
    return fake


def test_voice_upload_returns_only_the_transcript(owner: TestClient, stt: FakeStt) -> None:
    audio = AUDIO_MARKER + b"\x00" * 3000
    response = owner.post(
        "/voice/transcribe", content=audio, headers={"Content-Type": "audio/webm;codecs=opus"}
    )
    assert response.status_code == 200, response.text
    assert response.json() == {"transcript": "count globex customers"}
    assert stt.received == [audio]
    with session_scope() as session:  # nothing about it is stored
        for table in ("messages", "audit_events", "settings_kv", "voice_confirmations"):
            rows = table_text(session, table)
            assert not any("OggS" in r for r in rows)


def test_voice_upload_limits(
    owner: TestClient, stt: FakeStt, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RINGKEEPER_STT_MAX_BYTES", "1000")
    big = owner.post(
        "/voice/transcribe", content=b"x" * 2000, headers={"Content-Type": "audio/mp4"}
    )
    assert big.status_code == 413
    assert "1 KB" in big.json()["detail"]
    text_body = owner.post(
        "/voice/transcribe", content=b"hi", headers={"Content-Type": "text/plain"}
    )
    assert text_body.status_code == 415
    stt.status, stt.error, stt.seconds = 413, "too_long", 200.0
    long = owner.post("/voice/transcribe", content=b"x" * 10, headers={"Content-Type": "audio/ogg"})
    assert long.status_code == 422
    assert "3:20" in long.json()["detail"]
    assert stt.received == [b"x" * 10]


# --- Live events ---


def test_events_stream_task_changes(owner: TestClient, task_registry: None) -> None:
    received: list[dict[str, Any]] = []

    def create_later() -> None:
        time.sleep(1.0)
        with session_scope() as session:
            tasks.create_task(session, get_settings(), project="acme-web", prompt="go")

    threading.Thread(target=create_later, daemon=True).start()
    with owner.stream("GET", "/events") as stream:
        assert stream.headers["content-type"].startswith("text/event-stream")
        kind = None
        for line in stream.iter_lines():
            if line.startswith("event: "):
                kind = line.removeprefix("event: ")
            elif line.startswith("data: ") and kind == "task":
                received.append(json.loads(line.removeprefix("data: ")))
                break
    assert received[0]["status"] == "queued"
    assert received[0]["project"] == "acme-web"


def test_messages_from_the_web_app_record_source_and_input(owner: TestClient) -> None:
    sent = owner.post(
        "/messages", json={"body": "count globex customers", "source": "web", "input": "voice"}
    )
    assert sent.status_code == 202
    thread = owner.get(f"/threads/{sent.json()['thread_id']}").json()
    assert thread["source"] == "web"
    assert thread["messages"][0]["input"] == "voice"
    bad = owner.post("/messages", json={"body": "x", "source": "telegram"})
    assert bad.status_code == 422  # only the Telegram bridge posts as telegram


def test_a_forgotten_password_is_reset_from_the_cli(owner: TestClient, data_dir: Path) -> None:
    owner.post("/projects", json={"name": "acme-web", "owner": "acme"})
    result = CliRunner().invoke(cli, ["web", "reset-login", "--yes"])
    assert result.exit_code == 0, result.output
    code = result.stdout.split("New setup code: ")[1].split()[0]
    assert (data_dir / "setup-code.txt").read_text().strip() == code
    assert owner.get("/settings").status_code == 401  # every session is gone
    status = owner.get("/setup/status").json()
    assert (status["owner"], status["projects"]) == (False, 1)  # the rest stays
    again = owner.post(
        "/setup/owner", json={"password": "a brand new passphrase", "setup_code": code}
    )
    assert again.status_code == 201


def test_the_setup_code_command(admin: TestClient, setup_code: str) -> None:
    shown = CliRunner().invoke(cli, ["web", "setup-code"])
    assert (shown.exit_code, shown.stdout.strip()) == (0, setup_code)
    admin.post("/setup/owner", json={"password": PASSWORD, "setup_code": setup_code})
    done = CliRunner().invoke(cli, ["web", "setup-code"])
    assert done.exit_code == 1
    assert "reset-login" in done.stdout


def test_a_connection_address_can_only_change_with_its_secret(owner: TestClient) -> None:
    n8n = {"name": "acme-flows", "type": "n8n", "env": "dev", "owner": "acme",
           "base_url": "https://n8n.example.com", "secret": "n8n-key-1234"}  # fmt: skip
    assert owner.post("/connections", json=n8n).status_code == 201
    moved = owner.patch("/connections/acme-flows", json={"base_url": "https://evil.example"})
    assert moved.status_code == 422
    assert "needs the secret again" in moved.json()["detail"]
    with_key = owner.patch(
        "/connections/acme-flows",
        json={"base_url": "https://n8n2.example.com", "secret": "n8n-key-5678"},
    )
    assert with_key.status_code == 200
    assert with_key.json()["base_url"] == "https://n8n2.example.com"
    bad = {**n8n, "name": "meta", "base_url": "http://169.254.169.254/latest"}
    assert owner.post("/connections", json=bad).status_code == 422
    creds = {**n8n, "name": "creds", "base_url": "https://u:p@n8n.example.com"}
    assert owner.post("/connections", json=creds).status_code == 422


def test_removing_a_connection_removes_its_secret(owner: TestClient, target_dsn: str) -> None:
    connection = {"name": "acme-db", "type": "supabase", "env": "dev", "owner": "acme",
                  "secret": target_dsn}  # fmt: skip
    assert owner.post("/connections", json=connection).status_code == 201
    ref = secrets_in.CONNECTION_REF.format(name="acme-db")
    assert get_vault().resolve(ref).reveal() == target_dsn
    assert owner.delete("/connections/acme-db").status_code == 204
    with pytest.raises(Exception, match="not in the vault"):
        get_vault().resolve(ref)
