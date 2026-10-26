"""Desktop mode end to end: `ringkeeper desktop`'s server in a thread, SQLite,
an in-memory keychain, the front door's lock, and a task run by a local
worker process (the stub agent, no LLM). No Docker, no Postgres.
"""

import json
import os
import subprocess
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx2
import keyring
import pytest

from ringkeeper.config import get_settings
from ringkeeper.db import dispose_engines, session_scope
from ringkeeper.desktop import server
from ringkeeper.models import DecisionKind, TaskStatus
from ringkeeper.services import inbox, secrets_in, tasks
from ringkeeper.vault import get_vault
from tests.conftest import CLAUDE_API_KEY
from tests.test_vault import MemoryKeyring


@pytest.fixture
def desktop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Any]]:
    saved_env = dict(os.environ)
    for name in ("RINGKEEPER_DATABASE_URL", "RINGKEEPER_MASTER_KEY"):
        monkeypatch.delenv(name, raising=False)
    workspaces = tmp_path / "Documents"
    (workspaces / "acme-web").mkdir(parents=True)
    monkeypatch.setenv("RINGKEEPER_WORKSPACES_ROOT", str(workspaces))
    monkeypatch.setenv("RINGKEEPER_WORKER_STUB", json.dumps({"write": {"NOTES.md": "desktop\n"}}))
    monkeypatch.setenv("RINGKEEPER_MANAGER_POLL_SECONDS", "0.2")
    previous = keyring.get_keyring()
    keyring.set_keyring(MemoryKeyring())
    data_dir = tmp_path / "Ringkeeper"
    ready, stop = threading.Event(), threading.Event()
    thread = threading.Thread(
        target=server.run, args=(data_dir,), kwargs={"ready": ready, "stop": stop}, daemon=True
    )
    thread.start()
    assert ready.wait(60), "the desktop backend didn't start"
    info = json.loads((data_dir / server.RUNTIME_FILE).read_text(encoding="utf-8"))
    yield {"data_dir": data_dir, "workspaces": workspaces, **info}
    stop.set()
    thread.join(30)
    deadline = time.monotonic() + 15  # quitting stops everything it started
    while lingering := [t.name for t in threading.enumerate() if t.name.startswith("rk-")]:
        assert time.monotonic() < deadline, f"still running after quit: {lingering}"
        time.sleep(0.1)
    dispose_engines()
    keyring.set_keyring(previous)
    os.environ.clear()
    os.environ.update(saved_env)


def window(info: dict[str, Any]) -> httpx2.Client:
    """What the app shell does: trade the launch secret for a code, open the window."""
    client = httpx2.Client(base_url=info["url"], timeout=30)
    code = client.post(
        "/desktop/codes", headers={"Authorization": f"Bearer {info['secret']}"}
    ).json()["code"]
    opened = client.get(f"/desktop/start?code={code}")
    assert opened.status_code == 303
    assert client.get(f"/desktop/start?code={code}").status_code == 403  # one use only
    csrf = client.cookies[server.CSRF_COOKIE]
    client.headers[server.CSRF_HEADER.decode()] = csrf
    return client


def shell(info: dict[str, Any]) -> httpx2.Client:
    """The app shell itself: the launch secret on every call, no cookies."""
    return httpx2.Client(
        base_url=info["url"], timeout=30, headers={"Authorization": f"Bearer {info['secret']}"}
    )


def test_it_runs_on_localhost_with_sqlite_and_the_keychain(desktop: dict[str, Any]) -> None:
    assert desktop["url"].startswith("http://127.0.0.1:")
    assert (desktop["data_dir"] / "ringkeeper.db").exists()
    assert not (desktop["data_dir"] / "master.key").exists()  # it's in the keychain
    assert not (desktop["data_dir"] / "setup-code.txt").exists()  # no sign-in, no setup code
    with pytest.raises(server.AlreadyRunningError):
        server.run(desktop["data_dir"])  # one Ringkeeper per data folder


def test_the_front_door_is_locked(desktop: dict[str, Any]) -> None:
    anyone = httpx2.Client(base_url=desktop["url"], timeout=30)
    assert anyone.get("/desktop/health").status_code == 200
    assert anyone.get("/bff/projects").status_code == 401  # no window cookie
    assert anyone.get("/bff/projects", headers={"Host": "evil.example"}).status_code == 421
    assert anyone.post("/desktop/codes").status_code == 401
    bad = anyone.post("/desktop/codes", headers={"Authorization": "Bearer guess"})
    assert bad.status_code == 401
    assert anyone.get("/desktop/start?code=guess").status_code == 403

    win = window(desktop)
    assert win.get("/bff/projects").json() == []
    project = {"name": "acme-web", "owner": "acme", "workspace_path": "acme-web"}
    no_csrf = httpx2.post(
        f"{desktop['url']}/bff/projects", json=project, cookies=win.cookies, timeout=30
    )
    assert no_csrf.status_code == 403
    foreign = win.post("/bff/projects", json=project, headers={"Origin": "https://evil.example"})
    assert foreign.status_code == 403
    status = win.get("/bff/setup/status").json()
    assert status["signed_in"] is True  # the window is the owner; no password exists


def test_a_task_runs_as_a_local_process(desktop: dict[str, Any]) -> None:
    win = window(desktop)
    created = win.post(
        "/bff/projects", json={"name": "acme-web", "owner": "acme", "workspace_path": "acme-web"}
    )
    assert created.status_code == 201, created.text
    settings = get_settings()
    get_vault(settings).store(settings.claude_api_key_ref, CLAUDE_API_KEY)
    with session_scope() as session:
        task = tasks.create_task(session, settings, project="acme-web", prompt="write notes")
        task_id = str(task.id)
    deadline = time.monotonic() + 90
    while (found := win.get(f"/bff/tasks/{task_id}").json())["status"] not in {
        s.value for s in (TaskStatus.SUCCEEDED, TaskStatus.FAILED, TaskStatus.TIMED_OUT)
    }:
        assert time.monotonic() < deadline, found
        time.sleep(0.3)
    assert found["status"] == "succeeded", found
    notes = desktop["workspaces"] / "acme-web" / "NOTES.md"
    assert notes.read_text(encoding="utf-8") == "desktop\n"


def test_the_front_door_serves_the_exported_app(tmp_path: Path) -> None:
    from starlette.applications import Starlette  # noqa: PLC0415
    from starlette.testclient import TestClient  # noqa: PLC0415

    web = tmp_path / "out"
    (web / "_next" / "static").mkdir(parents=True)
    (web / "index.html").write_text("home", encoding="utf-8")
    (web / "tasks.html").write_text("tasks", encoding="utf-8")
    (web / "_next" / "static" / "app.js").write_text("js", encoding="utf-8")
    (web / "404.html").write_text("missing", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("outside", encoding="utf-8")
    door = server.FrontDoor(Starlette(), server.Launch(), 8765, web)
    client = TestClient(door, base_url="http://127.0.0.1:8765")
    assert client.get("/").text == "home"
    assert client.get("/tasks").text == "tasks"  # Next exports /tasks as tasks.html
    assert client.get("/_next/static/app.js").text == "js"
    assert "default-src 'self'" in client.get("/").headers["content-security-policy"]
    missing = client.get("/nope")
    assert (missing.status_code, missing.text) == (404, "missing")
    assert client.get("/../secret.txt").text != "outside"
    assert client.get("/%2e%2e/secret.txt").text != "outside"
    assert client.get("/", headers={"Host": "127.0.0.1:9999"}).status_code == 421


def test_the_shell_reads_the_tray_state_and_pauses(desktop: dict[str, Any]) -> None:
    tray = shell(desktop)
    assert tray.get("/desktop/api/desktop/status").json() == {
        "paused": False, "start_at_login": True, "waiting": [],
    }  # fmt: skip
    with session_scope() as session:
        inbox.create(
            session, get_settings(), DecisionKind.QUESTION,
            {"question": "Which table has the plans?\n```sql\nselect * from plans;\n```",
             "role": "worker"},
            project="acme-web",
        )  # fmt: skip
    waiting = tray.get("/desktop/api/desktop/status").json()["waiting"]
    assert [(w["kind"], w["project"]) for w in waiting] == [("question", "acme-web")]
    assert "select" not in waiting[0]["summary"].lower()  # never raw SQL in a notification
    assert len(waiting[0]["summary"]) <= 160

    assert tray.patch("/desktop/api/settings", json={"paused": True}).status_code == 200
    assert tray.get("/desktop/api/desktop/status").json()["paused"] is True

    anyone = httpx2.Client(base_url=desktop["url"], timeout=30)
    assert anyone.get("/desktop/api/desktop/status").status_code == 401
    guess = anyone.get("/desktop/api/settings", headers={"Authorization": "Bearer guess"})
    assert guess.status_code == 401
    assert window(desktop).get("/desktop/api/desktop/status").status_code == 401  # cookie isn't it


def test_the_shell_stops_everything(desktop: dict[str, Any]) -> None:
    anyone = httpx2.Client(base_url=desktop["url"], timeout=30)
    assert anyone.post("/desktop/shutdown").status_code == 401
    assert shell(desktop).post("/desktop/shutdown").status_code == 202
    runtime = desktop["data_dir"] / server.RUNTIME_FILE
    deadline = time.monotonic() + 30
    while runtime.exists():  # the last thing run() does on the way out
        assert time.monotonic() < deadline, "still running after shutdown"
        time.sleep(0.1)
    with pytest.raises(httpx2.TransportError):
        anyone.get("/desktop/health")


def test_desktop_mode_ignores_a_stray_env_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".env").write_text(
        "RINGKEEPER_DATABASE_URL=postgresql+psycopg://elsewhere/db\n"
        "RINGKEEPER_WORKSPACES_ROOT=/srv/other\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    for name in ("RINGKEEPER_ENV_FILE", "RINGKEEPER_DATABASE_URL", "RINGKEEPER_WORKSPACES_ROOT"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("RINGKEEPER_MODE", "server")
    assert get_settings().workspaces_root == "/srv/other"  # server mode reads it, as before
    monkeypatch.setenv("RINGKEEPER_MODE", "desktop")
    monkeypatch.setenv("RINGKEEPER_DATA_DIR", str(tmp_path / "data"))
    settings = get_settings()
    assert settings.database_url.startswith("sqlite:///")
    assert settings.workspaces_root is None


def test_the_start_link_opens_a_page_of_this_app_only() -> None:
    from starlette.applications import Starlette  # noqa: PLC0415
    from starlette.testclient import TestClient  # noqa: PLC0415

    launch = server.Launch()
    client = TestClient(
        server.FrontDoor(Starlette(), launch, 8765, None),
        base_url="http://127.0.0.1:8765",
        follow_redirects=False,
    )
    for given, expected in [
        ("/approvals", "/approvals"),
        ("%2Fapprovals", "/approvals"),
        ("//evil.example", "/"),
        ("https://evil.example", "/"),
        ("/%5Cevil.example", "/"),
        ("", "/"),
    ]:
        opened = client.get(f"/desktop/start?code={launch.new_code()}&next={given}")
        assert (opened.status_code, opened.headers["location"]) == (303, expected), given


def test_first_run_in_the_app(
    desktop: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fresh data folder → a Claude key and a working project, all from the window."""
    win = window(desktop)
    status = win.get("/bff/setup/status").json()
    assert status["done"] is False
    assert status["claude"]["api_key"] is False
    assert status["projects"] == 0
    assert status["workspaces_root"] == str(desktop["workspaces"])

    # Claude: an API key only (no claude.ai sign-in in a third-party app), checked live.
    checked: list[str] = []
    monkeypatch.setattr(secrets_in, "check_anthropic_key", checked.append)
    token = "sk-ant-oat01-" + "x" * 40
    subscription = win.put("/bff/secrets/claude", json={"mode": "subscription", "value": token})
    assert subscription.status_code == 422
    assert "API key" in subscription.json()["detail"]
    assert win.put("/bff/secrets/claude", json={"value": CLAUDE_API_KEY}).status_code == 200
    assert checked == [CLAUDE_API_KEY]

    # A project with no folder given gets a new one, started as a git repository.
    made = win.post("/bff/projects", json={"name": "hello-check", "owner": "test"})
    assert made.status_code == 201, made.text
    folder = desktop["workspaces"] / "hello-check"
    assert made.json() == {
        "name": "hello-check",
        "folder": str(folder),
        "created": True,
        "git": True,
    }
    log = subprocess.run(
        ["git", "log", "--format=%s", "main"],  # noqa: S607
        cwd=folder, capture_output=True, text=True, check=True,
    )  # fmt: skip
    assert log.stdout.strip() == "Start"
    assert win.get("/bff/projects/hello-check").json()["workspace_path"] == str(folder)

    # Any folder on this computer (a pasted "Copy as path" too); its files are left alone.
    existing = tmp_path / "Client work" / "acme site"
    existing.mkdir(parents=True)
    (existing / "index.html").write_text("<h1>Acme</h1>", encoding="utf-8")
    used = win.post(
        "/bff/projects",
        json={"name": "acme-site", "owner": "acme", "workspace_path": f'"{existing}"'},
    )
    assert used.status_code == 201, used.text
    assert used.json() == {
        "name": "acme-site",
        "folder": str(existing),
        "created": False,
        "git": False,
    }
    assert sorted(p.name for p in existing.iterdir()) == ["index.html"]

    for bad in (str(desktop["data_dir"] / "x"), "../outside", Path.home().anchor, str(Path.home())):
        refused = win.post(
            "/bff/projects", json={"name": "bad", "owner": "x", "workspace_path": bad}
        )
        assert refused.status_code == 409, (bad, refused.text)

    # Finished: the app stops showing first-run.
    assert win.post("/bff/setup/done").status_code == 204
    assert win.get("/bff/setup/status").json()["done"] is True


def test_claude_code_folders_are_offered_as_projects(
    desktop: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    code = tmp_path / "code"
    for name in ("acme-site", "beta-app", "already-here", "acme-site/.claude/worktrees/copy"):
        (code / name).mkdir(parents=True)
    listed: dict[str, dict[str, Any]] = {
        str(code / "acme-site"): {},
        (code / "acme-site").as_posix(): {},  # the same folder, spelled another way
        str(code / "acme-site" / ".claude" / "worktrees" / "copy"): {},
        str(code / "beta-app"): {},
        str(code / "gone"): {},  # deleted since
        str(code / "already-here"): {},
        str(Path.home()): {},
        str(desktop["data_dir"]): {},
        "relative/folder": {},
    }
    config = tmp_path / "claude-config"
    config.mkdir()
    (config / ".claude.json").write_text(json.dumps({"projects": listed}), encoding="utf-8")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config))

    win = window(desktop)
    made = win.post(
        "/bff/projects",
        json={"name": "already-here", "owner": "x", "workspace_path": str(code / "already-here")},
    )
    assert made.status_code == 201, made.text
    offered = win.get("/bff/desktop/claude-code-folders").json()
    assert offered == [
        {"folder": str(code / "acme-site"), "name": "acme-site"},
        {"folder": str(code / "beta-app"), "name": "beta-app"},
    ]

    (config / ".claude.json").write_text("not json", encoding="utf-8")
    assert win.get("/bff/desktop/claude-code-folders").json() == []
