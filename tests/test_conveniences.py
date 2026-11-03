"""The conveniences: files on a request, shortcuts, Run again, trust for an hour, the
digest, the daily connection check, pins, and keys found in a project's own files."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from ringkeeper.calls import Caller, ToolCall
from ringkeeper.config import get_settings
from ringkeeper.db import session_scope
from ringkeeper.errors import RuleViolationError
from ringkeeper.models import (
    AuditEvent,
    Instance,
    InstanceType,
    TaskStatus,
)
from ringkeeper.services import (
    attachments,
    decisions,
    digest,
    env_scan,
    health,
    tasks,
    threads,
    trusts,
)
from ringkeeper.services.probes import ProbeResult
from tests.helpers import make_instance, make_project
from tests.test_web_api import (  # noqa: F401 (fixtures)
    admin,  # pyright: ignore[reportUnusedImport]
    owner,  # pyright: ignore[reportUnusedImport]
    setup_code,  # pyright: ignore[reportUnusedImport]
)


@pytest.fixture
def project(owner: TestClient, tmp_path: Path) -> Path:  # noqa: F811
    folder = tmp_path / "acme-web"
    folder.mkdir()
    with session_scope() as session:
        make_project(session, "acme-web", owner="acme", workspace_path=str(folder))
    return folder


def keep_in(sent: list[str]) -> Callable[..., int]:
    """Stands in for announce.send: what it was told to send."""

    def send(_session: object, _settings: object, text: str, **_: object) -> int:
        sent.append(text)
        return 1

    return send


def works(_name: str, *, by: str) -> ProbeResult:
    return ProbeResult(True, f"ok {by}")


# --- Files on a request ---


def test_a_dropped_file_reaches_the_project_with_its_task(
    owner: TestClient,  # noqa: F811
    project: Path,
) -> None:
    saved = owner.post(
        "/attachments", content=b"hello", headers={"x-file-name": "..%2F..%2Fbrief%20v2.txt"}
    ).json()
    assert saved["name"] == "brief v2.txt"  # never a path: only the file's own name
    sent = owner.post(
        "/messages", json={"body": "Use this", "source": "web", "attachments": [saved["id"]]}
    ).json()
    with session_scope() as session:
        message = threads.get_message(session, sent["message_id"])  # type: ignore[arg-type]
        task = tasks.create_task(
            session, get_settings(), project="acme-web", prompt="Use this", message_id=message.id
        )
        assert "Files attached to this request: attachments/brief v2.txt" in task.prompt
    assert (project / "attachments" / "brief v2.txt").read_bytes() == b"hello"


def test_an_attachment_id_can_only_name_the_attachments_folder(tmp_path: Path) -> None:
    settings = get_settings()
    outside = tmp_path / "secret.txt"
    outside.write_text("no")
    bad = ["../../secret.txt", f"{'0' * 32}/../../secret.txt", str(outside)]
    assert attachments.bring_in(settings, bad, str(tmp_path / "work")) == []


# --- Run again and shortcuts ---


def test_run_again_makes_the_same_request_a_new_task(
    owner: TestClient,  # noqa: F811
    project: Path,
) -> None:
    with session_scope() as session:
        first = tasks.create_task(session, get_settings(), project="acme-web", prompt="Do it")
        first.status = TaskStatus.FAILED
        first_id = str(first.id)
    made = owner.post(f"/tasks/{first_id}/again")
    assert made.status_code == 201, made.text
    with session_scope() as session:
        again = tasks.get_task(session, made.json()["id"])
        assert (again.project, again.prompt) == ("acme-web", "Do it")
        assert str(again.id) != first_id


@pytest.mark.usefixtures("project")
def test_a_starred_request_is_a_shortcut_on_its_project(owner: TestClient) -> None:  # noqa: F811
    made = owner.post(
        "/shortcuts", json={"project": "acme-web", "label": "Weekly", "prompt": "Send the report"}
    ).json()
    assert made["client"] == "acme"
    assert [s["label"] for s in owner.get("/shortcuts").json()] == ["Weekly"]
    assert owner.delete(f"/shortcuts/{made['id']}").status_code == 204
    assert owner.get("/shortcuts").json() == []


# --- Trust for an hour ---


def _held(tool: str, args: dict[str, Any], service: InstanceType = InstanceType.SUPABASE) -> str:
    call = ToolCall(Caller("acme-web", "s1", "t1"), service, tool, "acme-db", args)
    with session_scope() as session:
        return str(decisions.hold(session, call, "acme-db").id)


@pytest.fixture
def database(project: Path) -> None:
    with session_scope() as session:
        make_instance(session, "acme-db", owner="acme")


@pytest.mark.usefixtures("database")
def test_trust_runs_one_tool_for_a_while_and_never_a_delete() -> None:
    ordinary = _held("execute", {"sql": "UPDATE orders SET status = 'x' WHERE id = 1"})
    with session_scope() as session:
        trust = trusts.grant(session, decisions.get_decision(session, ordinary), by="owner")
        assert trust.expires_at - datetime.now(UTC) <= timedelta(hours=1)
        same = {"sql": "UPDATE orders SET status = 'y' WHERE id = 2"}
        args = ("acme-web", "acme-db", InstanceType.SUPABASE, "execute")
        assert trusts.allows(session, *args, same)
        # Even under a trust, a delete still asks, and so does another project or tool.
        assert not trusts.allows(session, *args, {"sql": "DELETE FROM orders"})
        assert not trusts.allows(session, "other", *args[1:], same)
        assert not trusts.allows(session, "acme-web", "acme-db", args[2], "revert_change", {})
        assert session.scalars(select(AuditEvent).where(AuditEvent.tool == "trust.add")).first()


@pytest.mark.usefixtures("database")
def test_a_change_that_needs_its_name_typed_cant_be_trusted() -> None:
    deleting = _held("execute", {"sql": "DELETE FROM orders"})
    with session_scope() as session, pytest.raises(RuleViolationError, match="always asks"):
        trusts.grant(session, decisions.get_decision(session, deleting), by="owner")


@pytest.mark.usefixtures("database")
def test_a_trust_ends(owner: TestClient) -> None:  # noqa: F811
    held = _held("execute", {"sql": "UPDATE orders SET status = 'x' WHERE id = 1"})
    made = owner.post("/trusts", json={"decision": held})
    assert made.status_code == 201, made.text
    assert [t["tool"] for t in owner.get("/trusts").json()] == ["execute"]
    assert owner.delete(f"/trusts/{made.json()['id']}").status_code == 204
    assert owner.get("/trusts").json() == []
    with session_scope() as session:
        args = {"sql": "UPDATE orders SET status = 'x' WHERE id = 1"}
        kind = InstanceType.SUPABASE
        assert not trusts.allows(session, "acme-web", "acme-db", kind, "execute", args)


# --- The digest ---


def test_the_digest_says_per_client_what_ran_failed_and_waits(
    owner: TestClient,  # noqa: F811
    project: Path,
) -> None:
    with session_scope() as session:
        make_instance(session, "acme-db", owner="acme")
        done = tasks.create_task(session, get_settings(), project="acme-web", prompt="One")
        done.status = TaskStatus.SUCCEEDED
        broke = tasks.create_task(session, get_settings(), project="acme-web", prompt="Two")
        broke.status = TaskStatus.FAILED
    _held("execute", {"sql": "UPDATE orders SET status = 'x' WHERE id = 1"})
    [acme] = owner.get("/digest").json()["clients"]
    assert (acme["client"], acme["ran"], len(acme["failed"]), len(acme["waiting"])) == (
        "acme", 2, 1, 1,
    )  # fmt: skip
    with session_scope() as session:
        text = digest.text(digest.build(session))
    assert "2 ran, 1 failed, 1 waiting for you" in text


@pytest.mark.usefixtures("project")
def test_the_digest_is_sent_once_a_day_after_its_time(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[str] = []
    monkeypatch.setattr(digest.announce, "send", keep_in(sent))
    settings = get_settings().model_copy(update={"digest_enabled": True, "digest_time": "08:00"})
    morning = datetime(2026, 11, 3, 8, 30).astimezone()
    with session_scope() as session:
        assert not digest.send_if_due(session, settings, morning - timedelta(hours=1))
        assert digest.send_if_due(session, settings, morning)
        assert not digest.send_if_due(session, settings, morning + timedelta(hours=2))
        assert digest.send_if_due(session, settings, morning + timedelta(days=1))
    assert len(sent) == 2


# --- The daily connection check ---


def test_a_connection_that_stops_working_says_so_once(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    del project
    with session_scope() as session:
        make_instance(session, "acme-db", owner="acme")
        session.scalars(select(Instance)).one().last_test_ok = True
    said: list[str] = []
    monkeypatch.setattr(health.announce, "send", keep_in(said))

    def refuses(name: str, *, by: str) -> ProbeResult:
        assert by == "daily check"
        with session_scope() as session:
            session.scalars(
                select(Instance).where(Instance.name == name)
            ).one().last_test_ok = False
        return ProbeResult(False, "The key was refused.")

    monkeypatch.setattr(health.probes, "test_connection", refuses)
    report = health.check_all(get_settings())
    assert report.failed == ["acme-db: The key was refused."]
    assert said == ["Connection stopped working: acme-db: The key was refused."]
    health.check_all(get_settings())  # still failing: nothing new to say
    assert len(said) == 1


def test_a_key_about_to_expire_warns_once(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    del project
    soon = (datetime.now(UTC) + timedelta(days=5)).date().isoformat()
    with session_scope() as session:
        make_instance(session, "acme-db", owner="acme", config={"expires_at": soon})
    said: list[str] = []
    monkeypatch.setattr(health.announce, "send", keep_in(said))
    monkeypatch.setattr(health.probes, "test_connection", works)
    health.check_all(get_settings())
    health.check_all(get_settings())
    assert said == ["Key expiring: acme-db: key expires in 5 days"]


def test_the_check_runs_a_day_after_the_last(project: Path) -> None:
    del project
    now = datetime(2026, 11, 3, tzinfo=UTC)
    with session_scope() as session:
        assert health.due(session, now)
        health._save(session, {"at": now.isoformat()})  # pyright: ignore[reportPrivateUsage]
        assert not health.due(session, now + timedelta(hours=23))
        assert health.due(session, now + timedelta(hours=24))


# --- Pins ---


def test_pins_and_recents_are_kept(owner: TestClient) -> None:  # noqa: F811
    assert owner.get("/ui-prefs").json() == {"pins": [], "recents": []}
    kept = {"pins": ["project:acme-web"], "recents": ["client:acme"]}
    assert owner.put("/ui-prefs", json=kept).status_code == 200
    assert owner.get("/ui-prefs").json() == kept


# --- A project's own keys ---


ENV = """\
# keys
DATABASE_URL="postgresql://app:s3cretpw@db.geninfos.example:5432/app"
VERCEL_TOKEN=vercel-token-1234567890
N8N_API_KEY=n8n-key-1234567890
N8N_BASE_URL=https://n8n.geninfos.example
TWILIO_ACCOUNT_SID=AC123
TWILIO_AUTH_TOKEN=only-the-auth-token
SUPABASE_URL=https://abc.supabase.co
OTHER_THING=not-a-service
GITHUB_TOKEN=<your-token>
"""


def test_only_known_keys_are_found_and_all_are_masked(project: Path) -> None:
    (project / ".env").write_text(ENV, encoding="utf-8")
    found = {f.label: f for f, _, _ in env_scan.scan(project)}
    assert set(found) == {"Database", "Vercel", "n8n", "Twilio"}  # not a placeholder token
    assert found["Database"].masked == "postgresql://…@db.geninfos.example"
    assert found["Database"].title == "Database · db.geninfos.example"
    assert found["n8n"].title == "n8n · n8n.geninfos.example"
    assert "1234567890" not in found["Vercel"].masked
    assert not found["Twilio"].usable
    assert "auth token" in found["Twilio"].why


def test_a_supabase_url_alone_says_why_it_cant_connect(project: Path) -> None:
    (project / ".env.local").write_text("SUPABASE_URL=https://abc.supabase.co\n", encoding="utf-8")
    [(finding, _, _)] = env_scan.scan(project)
    assert not finding.usable
    assert "connection string" in finding.why


def test_a_link_out_of_the_project_is_never_read(project: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside.env"
    outside.write_text("VERCEL_TOKEN=vercel-token-1234567890\n", encoding="utf-8")
    try:
        (project / ".env").symlink_to(outside)
    except OSError:
        pytest.skip("this computer doesn't allow symlinks here")
    assert env_scan.scan(project) == []


def test_a_found_key_is_connected_only_when_confirmed(
    owner: TestClient,  # noqa: F811
    project: Path,
) -> None:
    (project / ".env").write_text(
        "N8N_API_KEY=n8n-key-1234567890\nN8N_BASE_URL=https://n8n.geninfos.example\n",
        encoding="utf-8",
    )
    with session_scope() as session:
        assert session.scalars(select(Instance)).first() is None
    scanned = owner.get("/projects/acme-web/scan").json()
    assert "n8n-key-1234567890" not in str(scanned)  # the value never leaves the server
    [finding] = scanned["found"]
    with session_scope() as session:
        assert session.scalars(select(Instance)).first() is None  # nothing silently imported
    done = owner.post(
        "/projects/acme-web/scan/connect",
        json={"id": finding["id"], "env": "prod", "name": "acme-n8n", "title": finding["title"]},
    )
    assert done.status_code == 201, done.text
    connections = {c["name"]: c for c in owner.get("/connections").json()}
    assert connections["acme-n8n"]["projects"] == ["acme-web"]
    assert connections["acme-n8n"]["base_url"] == "https://n8n.geninfos.example"
    assert owner.get("/projects/acme-web/scan").json()["found"][0]["added"] is True


def test_a_skipped_key_isnt_offered_again(
    owner: TestClient,  # noqa: F811
    project: Path,
) -> None:
    (project / ".env").write_text("VERCEL_TOKEN=vercel-token-1234567890\n", encoding="utf-8")
    [finding] = owner.get("/projects/acme-web/scan").json()["found"]
    assert owner.post("/projects/acme-web/scan/skip", json={"id": finding["id"]}).status_code == 204
    assert owner.get("/projects/acme-web/scan").json()["found"] == []
