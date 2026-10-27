"""Desktop mode end to end: `ringkeeper desktop`'s server in a thread, SQLite,
an in-memory keychain, the front door's lock, and a task run by a local
worker process (the stub agent, no LLM). No Docker, no Postgres.
"""

import asyncio
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx2
import keyring
import pytest

from ringkeeper import voice_local
from ringkeeper.calls import Caller, ToolCall
from ringkeeper.config import get_settings
from ringkeeper.db import dispose_engines, session_scope
from ringkeeper.desktop import server
from ringkeeper.desktop.update import updates
from ringkeeper.models import AuthMode, DecisionKind, InstanceType, TaskStatus
from ringkeeper.services import claude_desktop, inbox, instances, subscription, tasks
from ringkeeper.services.calls import run_call
from ringkeeper.vault import get_vault
from tests.conftest import CLAUDE_API_KEY
from tests.helpers import fake_key_check
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
        "paused": False, "start_at_login": True, "waiting": [], "running": 0,
        "install_update": False,
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


def test_an_update_waits_for_the_user_and_for_running_work(desktop: dict[str, Any]) -> None:
    updates.__init__()  # the backend keeps it in memory; start clean
    tray, win = shell(desktop), window(desktop)
    assert win.get("/bff/desktop/update").json()["ready"] is None
    assert win.post("/bff/desktop/update/restart").status_code == 404  # nothing to restart into

    assert (
        tray.post(
            "/desktop/api/desktop/update", json={"version": "0.1.1", "notes": "Fixes."}
        ).status_code
        == 204
    )
    shown = win.get("/bff/desktop/update").json()
    assert shown["ready"] == {"version": "0.1.1", "notes": "Fixes."}
    assert shown["restart_requested"] is False
    assert tray.get("/desktop/api/desktop/status").json()["install_update"] is False

    # A task waiting to run (paused, so it stays queued): the shell holds off.
    assert tray.patch("/desktop/api/settings", json={"paused": True}).status_code == 200
    created = win.post(
        "/bff/projects", json={"name": "acme-web", "owner": "acme", "workspace_path": "acme-web"}
    )
    assert created.status_code == 201, created.text
    with session_scope() as session:
        tasks.create_task(session, get_settings(), project="acme-web", prompt="write notes")
    asked = win.post("/bff/desktop/update/restart").json()
    assert asked["restart_requested"] is True
    assert asked["running"] == 1
    status = tray.get("/desktop/api/desktop/status").json()
    assert (status["install_update"], status["running"]) == (True, 1)
    updates.__init__()


def test_about_shows_the_version_and_the_notices(
    desktop: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    win = window(desktop)
    assert win.get("/bff/about").json()["notices"] is False
    notices = tmp_path / "notices"
    notices.mkdir()
    (notices / "THIRD-PARTY-NOTICES.txt").write_text("MinGit 2.56: GPL-2.0", encoding="utf-8")
    listing = [{"name": "MinGit", "version": "2.56", "license": "GPL-2.0", "kind": "git"}]
    (notices / "components.json").write_text(json.dumps(listing), encoding="utf-8")
    monkeypatch.setenv("RINGKEEPER_NOTICES_DIR", str(notices))  # the shell sets these
    monkeypatch.setenv("RINGKEEPER_APP_VERSION", "0.1.0-beta.1")
    about = win.get("/bff/about").json()
    assert (about["version"], about["notices"], about["components"]) == (
        "0.1.0-beta.1",
        True,
        listing,
    )
    assert "GPL-2.0" in win.get("/bff/about/notices").json()["text"]


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
    checked = fake_key_check(monkeypatch)
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


def test_claude_desktop_servers_are_imported_with_secrets_in_the_vault(
    desktop: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server = Path(__file__).parent / "fixtures" / "fake_mcp_server.py"
    key, dsn = "fake-key-1234567890", "postgresql://notes:hunter2-not-real@db.example/notes"
    config = tmp_path / "claude_desktop_config.json"
    config.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "notes": {
                        "command": sys.executable,
                        "args": [str(server), dsn],
                        "env": {"FAKE_API_KEY": key},
                    },
                    "browser": {"command": "definitely-not-installed-xyz", "args": ["-y", "pkg"]},
                    "legacy": {"type": "sse", "url": "https://legacy.example/sse"},
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(claude_desktop, "config_paths", lambda: [config])
    win = window(desktop)
    assert win.post("/bff/projects", json={"name": "acme-web", "owner": "acme"}).status_code == 201

    listed = win.get("/bff/desktop/claude-desktop-servers").json()
    assert listed["config"] == str(config)
    servers = {s["name"]: s for s in listed["servers"]}
    assert servers["notes"]["problem"] is None
    assert servers["notes"]["secrets"] == 2  # the env value and the connection string
    assert servers["notes"]["preview"].endswith("••••")
    assert "isn't on this computer" in servers["browser"]["problem"]
    assert "SSE" in servers["legacy"]["problem"]
    assert key not in json.dumps(listed)
    assert dsn not in json.dumps(listed)

    refused = win.post(
        "/bff/desktop/claude-desktop-servers/import",
        json={"servers": [{"name": "browser", "project": "acme-web"}]},
    )
    assert refused.status_code == 422
    made = win.post(
        "/bff/desktop/claude-desktop-servers/import",
        json={"servers": [{"name": "notes", "project": "acme-web"}]},
    )
    assert made.json() == {"imported": {"notes": "notes"}}, made.text
    again = win.post(
        "/bff/desktop/claude-desktop-servers/import",
        json={"servers": [{"name": "notes", "project": "acme-web"}]},
    )
    assert again.status_code == 409
    assert win.get("/bff/desktop/claude-desktop-servers").json()["servers"][0]["imported_as"]

    database = (desktop["data_dir"] / "ringkeeper.db").read_bytes()
    assert key.encode() not in database
    assert b"hunter2" not in database
    [connection] = [c for c in win.get("/bff/connections").json() if c["name"] == "notes"]
    assert connection["type"] == "mcp"
    assert connection["env"] == "dev"  # Test: a local tool, not client data
    assert connection["projects"] == ["acme-web"]
    assert connection["config"]["server"] == "notes"

    tested = win.post("/bff/connections/notes/test").json()
    assert tested["ok"] is True, tested
    assert "4 tools" in tested["message"]
    # Testing remembered the server's hints: they decide calls without starting it.
    [connection] = [c for c in win.get("/bff/connections").json() if c["name"] == "notes"]
    assert connection["config"]["annotations"]["read_notes"]["read_only"] is True
    tools = {t["name"]: t for t in win.get("/bff/connections/notes/tools").json()["tools"]}
    assert set(tools) == {"read_notes", "delete_note", "remove_tag", "what_i_got"}
    assert tools["read_notes"]["rule"] == "read"
    assert tools["read_notes"]["rule_from"] == "annotations"
    assert tools["delete_note"]["rule"] == "confirm"

    # Through the call pipeline: annotated read-only tools run, the rest wait.
    caller = Caller("acme-web", "session-1", "token-1")
    vault = get_vault(get_settings())

    def run(tool: str) -> dict[str, Any]:
        call = ToolCall(caller, InstanceType.MCP, "call_tool", "notes", {"tool": tool})
        return asyncio.run(run_call(call, vault))

    assert run("read_notes")["structured"] == {"result": ["first note"]}
    assert run("what_i_got")["status"] == "held_for_approval"
    deleting = run("delete_note")
    assert deleting["status"] == "held_for_approval"
    held = win.get("/bff/decisions").json()
    [pending] = [d for d in held if d["id"] == deleting["decision_id"]]
    assert "its server marks as destructive" in str(pending)

    # The user's choices win over the hints, and can go back to them.
    chosen = win.put(
        "/bff/connections/notes/tool-rules",
        json={"rules": {"read_notes": "approve", "what_i_got": "read"}},
    )
    assert chosen.json() == {"tool_rules": {"read_notes": "approve", "what_i_got": "read"}}
    assert run("read_notes")["status"] == "held_for_approval"
    assert run("what_i_got")["structured"]["claude_credential"] is False
    bad = win.put("/bff/connections/notes/tool-rules", json={"rules": {"x": "sometimes"}})
    assert bad.status_code == 422
    win.put("/bff/connections/notes/tool-rules", json={"rules": {}})
    assert run("read_notes")["structured"] == {"result": ["first note"]}  # its hints again

    # The agent listing the tools refreshes the hints too.
    with session_scope() as session:
        found = instances.get_instance(session, "notes")
        found.config = {k: v for k, v in found.config.items() if k != "annotations"}
    assert run("read_notes")["status"] == "held_for_approval"  # no hints: it waits
    listing = ToolCall(caller, InstanceType.MCP, "list_tools", "notes", {})
    assert len(asyncio.run(run_call(listing, vault))["tools"]) == 4
    assert run("read_notes")["structured"] == {"result": ["first note"]}


def test_personal_subscription_mode_is_off_until_turned_on(
    desktop: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    login = tmp_path / "claude-code"
    login.mkdir()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(login))
    first, second = "sk-ant-oat01-" + "a" * 40, "sk-ant-oat01-" + "b" * 40

    def signed_in(token: str, expires_in: float = 3600) -> None:
        oauth = {"accessToken": token, "expiresAt": (time.time() + expires_in) * 1000}
        (login / ".credentials.json").write_text(json.dumps({"claudeAiOauth": oauth}))

    win = window(desktop)
    token = {"mode": "subscription", "value": "sk-ant-oat01-" + "s" * 40}
    assert win.put("/bff/secrets/claude", json=token).status_code == 422
    assert win.post("/bff/secrets/claude/from-claude-code").status_code == 422
    claude = win.get("/bff/settings").json()["claude"]
    assert claude["subscription_allowed"] is False

    win.patch("/bff/settings", json={"allow_subscription": True})  # Settings#personal
    assert win.get("/bff/settings").json()["claude"]["subscription_allowed"] is True
    signed_in(first, expires_in=-60)
    expired = win.post("/bff/secrets/claude/from-claude-code")
    assert expired.status_code == 409
    assert "expired" in expired.json()["detail"]
    signed_in(first)
    assert win.post("/bff/secrets/claude/from-claude-code").status_code == 200
    settings = win.get("/bff/settings").json()
    assert settings["values"]["claude_auth_mode"] == "subscription"
    assert settings["values"]["subscription_source"] == "claude-code"

    # Claude Code refreshed its login: the next task or routed request takes the new token.
    signed_in(second)
    current = get_settings().model_copy(
        update={"claude_auth_mode": AuthMode.SUBSCRIPTION, "subscription_source": "claude-code",
                "data_dir": desktop["data_dir"]}
    )  # fmt: skip
    subscription.refresh(get_vault(current), current)
    ref = current.claude_oauth_token_ref
    assert get_vault(current).resolve(ref).reveal() == second
    assert first not in json.dumps(settings)


def test_claude_desktop_extensions_are_imported_with_what_they_need(
    desktop: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    claude = tmp_path / "Claude"
    folder = claude / "Claude Extensions" / "ant.dir.gh.acme.notes"
    folder.mkdir(parents=True)
    shutil.copy(Path(__file__).parent / "fixtures" / "fake_mcp_server.py", folder / "server.py")
    manifest = {
        "name": "notes", "display_name": "Acme Notes",
        "server": {"type": "python", "entry_point": "server.py", "mcp_config": {
            "command": sys.executable,
            "args": ["${__dirname}/server.py", "${user_config.folders}"],
            "env": {"FAKE_API_KEY": "${user_config.api_key}"},
        }},
        "user_config": {
            "api_key": {"type": "string", "title": "Acme API key", "sensitive": True,
                        "required": True},
            "folders": {"type": "directory", "title": "Folders", "multiple": True,
                        "required": True},
        },
    }  # fmt: skip
    (folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    off = claude / "Claude Extensions" / "ant.dir.gh.acme.off"
    off.mkdir()
    (off / "manifest.json").write_text(
        json.dumps({"name": "off", "server": {"mcp_config": {"command": sys.executable}}}),
        encoding="utf-8",
    )
    saved = claude / "Claude Extensions Settings"
    saved.mkdir()
    folders = [str(tmp_path / "a"), str(tmp_path / "b")]
    (saved / "ant.dir.gh.acme.notes.json").write_text(
        json.dumps({"isEnabled": True, "userConfig": {"folders": folders}}), encoding="utf-8"
    )
    (saved / "ant.dir.gh.acme.off.json").write_text('{"isEnabled": false}', encoding="utf-8")
    monkeypatch.setattr(
        claude_desktop, "config_paths", lambda: [claude / "claude_desktop_config.json"]
    )
    win = window(desktop)
    assert win.post("/bff/projects", json={"name": "acme-web", "owner": "acme"}).status_code == 201

    listed = win.get("/bff/desktop/claude-desktop-servers").json()
    assert listed["config"] is None  # no config file, only extensions
    servers = {s["key"]: s for s in listed["servers"]}
    notes = servers["extension:ant.dir.gh.acme.notes"]
    assert (notes["name"], notes["kind"], notes["problem"]) == ("Acme Notes", "extension", None)
    assert notes["needs"] == [{"key": "api_key", "title": "Acme API key", "secret": True}]
    assert str(folder) in notes["preview"]
    assert servers["extension:ant.dir.gh.acme.off"]["problem"] == "turned off in Claude Desktop"

    choice = {"name": notes["key"], "project": "acme-web"}
    missing = win.post("/bff/desktop/claude-desktop-servers/import", json={"servers": [choice]})
    assert missing.status_code == 422
    assert "needs Acme API key" in missing.text
    key = "typed-key-0123456789"
    made = win.post(
        "/bff/desktop/claude-desktop-servers/import",
        json={"servers": [{**choice, "values": {"api_key": key}}]},
    )
    assert made.json() == {"imported": {notes["key"]: "acme-notes"}}, made.text
    with session_scope() as session:
        ref = instances.get_instance(session, "acme-notes").secret_ref
    spec = json.loads(get_vault().resolve(ref).reveal())
    assert spec["env"] == {"FAKE_API_KEY": key}
    assert spec["args"] == [f"{folder}/server.py", *folders]  # one argument per folder
    listed = win.get("/bff/desktop/claude-desktop-servers").json()
    assert {s["key"]: s["imported_as"] for s in listed["servers"]}[notes["key"]] == "acme-notes"
    assert key not in json.dumps(listed)


def test_voice_downloads_its_engine_and_model_on_first_use(
    desktop: dict[str, Any], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from tests.test_voice_local import FakeHub, fake_wheel, wheel_list  # noqa: PLC0415

    wheel = fake_wheel()
    monkeypatch.setenv("RINGKEEPER_VOICE_PACKAGES", str(wheel_list(tmp_path, wheel)))
    monkeypatch.setattr(voice_local, "ENGINE", "fakevoice")
    monkeypatch.setattr(sys, "path", list(sys.path))
    hub = FakeHub(wheel=wheel)
    monkeypatch.setattr(voice_local, "transport", httpx2.MockTransport(hub.handle))
    win = window(desktop)
    first = win.get("/bff/desktop/voice").json()
    assert (first["engine"], first["state"], first["total"]) == (
        True,
        "absent",
        50_006 + len(wheel),
    )
    assert win.post("/bff/desktop/voice/download").status_code == 202
    deadline = time.monotonic() + 10
    while win.get("/bff/desktop/voice").json()["state"] != "ready":
        assert time.monotonic() < deadline
        time.sleep(0.1)
    assert (desktop["data_dir"] / "voice" / "tiny" / "model.bin").is_file()
    chosen = win.put("/bff/desktop/voice/choice", json={"choice": "accuracy"})
    assert (chosen.status_code, chosen.json()["state"]) == (200, "absent")
    assert win.put("/bff/desktop/voice/choice", json={"choice": "huge"}).status_code == 422
    assert win.delete("/bff/desktop/voice").status_code == 204
    assert win.get("/bff/desktop/voice").json()["state"] == "absent"
    sys.modules.pop("fakevoice", None)
