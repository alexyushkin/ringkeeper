"""Desktop mode end to end: `ringkeeper desktop`'s server in a thread, SQLite,
an in-memory keychain, the front door's lock, and a task run by a local
worker process (the stub agent, no LLM). No Docker, no Postgres.
"""

import json
import os
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
from ringkeeper.models import TaskStatus
from ringkeeper.services import tasks
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
