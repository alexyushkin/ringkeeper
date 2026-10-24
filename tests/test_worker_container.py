"""Integration: real worker containers, a stub agent (no LLM), the real gateway.

The stub reports what the container can see; these tests assert the
isolation rules on that report, and that the session's token is dead after.
"""

import json
import platform
import subprocess
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import docker
import httpx2
import pytest
import uvicorn
from sqlalchemy import select, update

from ringkeeper.claude_auth import BILLING_ENV_VARS
from ringkeeper.config import get_settings
from ringkeeper.db import session_scope
from ringkeeper.gateway.app import create_app
from ringkeeper.models import Project, SessionEvent, TaskStatus
from ringkeeper.services import tasks
from ringkeeper.vault import Vault, get_vault
from ringkeeper.workers.manager import Manager
from ringkeeper.workers.runtime import LABEL_SESSION, DockerRuntime, WorkerSpec
from tests.conftest import CLAUDE_API_KEY, CLAUDE_OAUTH_TOKEN, free_port

pytestmark = pytest.mark.container

ROOT = Path(__file__).parents[1]
IMAGE = "ringkeeper-worker:test"
NETWORK = "ringkeeper_test_workers"
GATEWAY_HOST = "rk-test-gateway"
# What a worker sees, besides the files Docker always injects.
DOCKER_INJECTED = {"/", "/etc/hosts", "/etc/hostname", "/etc/resolv.conf"}
EXPECTED_MOUNTS = {"/workspace", "/state", "/tmp", "/home/worker"}  # noqa: S108


@pytest.fixture(scope="session")
def docker_client() -> Any:
    return docker.from_env()


@pytest.fixture(scope="session")
def worker_image(docker_client: Any) -> str:
    docker_client.images.build(path=str(ROOT / "worker"), tag=IMAGE, rm=True)
    return IMAGE


@pytest.fixture(scope="session")
def worker_network(docker_client: Any) -> Iterator[str]:
    for stale in docker_client.networks.list(names=[NETWORK]):
        stale.remove()
    network = docker_client.networks.create(NETWORK, driver="bridge")
    yield NETWORK
    network.remove()


@pytest.fixture
def exposed_gateway(registry: None, vault: Vault) -> Iterator[str]:
    """The real gateway, reachable from containers via host-gateway."""
    # Docker Desktop forwards host-gateway to the host's loopback; on Linux it is
    # the bridge address, so listen on every interface there.
    host = "127.0.0.1" if platform.system() in {"Windows", "Darwin"} else "0.0.0.0"  # noqa: S104
    port = free_port()
    server = uvicorn.Server(
        uvicorn.Config(create_app(vault), host=host, port=port, log_level="warning")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        assert time.monotonic() < deadline, "gateway did not start"
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(10)


@dataclass
class Run:
    task_id: str
    runtime: "RecordingRuntime"
    manager: Manager


class RecordingRuntime(DockerRuntime):
    def __init__(self) -> None:
        super().__init__()
        self.specs: list[WorkerSpec] = []

    def start(self, spec: WorkerSpec) -> str:
        self.specs.append(spec)
        return super().start(spec)


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    path = tmp_path / "acme-web"
    path.mkdir()
    path.chmod(0o777)  # the worker runs as uid 1000
    return path


@pytest.fixture
def worker_setup(
    exposed_gateway: str,
    worker_image: str,
    worker_network: str,
    workspace: Path,
    database_url: str,
    monkeypatch: pytest.MonkeyPatch,
    docker_client: Any,
) -> Iterator[Callable[..., Run]]:
    port = exposed_gateway.rsplit(":", 1)[1]
    monkeypatch.setenv("RINGKEEPER_WORKER_IMAGE", worker_image)
    monkeypatch.setenv("RINGKEEPER_WORKER_NETWORK", worker_network)
    monkeypatch.setenv("RINGKEEPER_GATEWAY_INTERNAL_URL", f"http://{GATEWAY_HOST}:{port}")
    monkeypatch.setenv("RINGKEEPER_WORKER_EXTRA_HOSTS", json.dumps({GATEWAY_HOST: "host-gateway"}))
    settings = get_settings()
    get_vault(settings).store(settings.claude_api_key_ref, CLAUDE_API_KEY)
    get_vault(settings).store(settings.claude_oauth_token_ref, CLAUDE_OAUTH_TOKEN)
    with session_scope() as session:
        session.execute(
            update(Project).where(Project.name == "acme-web").values(workspace_path=str(workspace))
        )
    volumes: list[str] = []

    def run(stub: dict[str, Any], *, max_runtime_s: int = 120, wait: bool = True) -> Run:
        monkeypatch.setenv("RINGKEEPER_WORKER_STUB", json.dumps(stub))
        with session_scope() as session:
            task = tasks.create_task(
                session,
                get_settings(),
                project="acme-web",
                prompt="stub run",
                max_runtime_s=max_runtime_s,
            )
            task_id = str(task.id)
        volumes.append(tasks.state_volume_name(task.id))
        runtime = RecordingRuntime()
        manager = Manager(runtime, get_vault())
        manager.tick()
        if wait:
            manager.wait_idle(180)
        return Run(task_id, runtime, manager)

    yield run
    for name in volumes:
        for volume in docker_client.volumes.list(filters={"name": name}):
            volume.remove(force=True)


def events_of(task_id: str) -> dict[str, list[dict[str, Any]]]:
    by_type: dict[str, list[dict[str, Any]]] = {}
    with session_scope() as session:
        task = tasks.get_task(session, task_id)
        ids = [s.id for s in task.sessions]
        for event in session.scalars(select(SessionEvent).where(SessionEvent.session_id.in_(ids))):
            by_type.setdefault(event.type, []).append(event.payload)
    return by_type


def status_of(task_id: str) -> TaskStatus:
    with session_scope() as session:
        return tasks.get_task(session, task_id).status


def test_worker_sees_only_its_workspace_and_one_credential(
    worker_setup: Callable[..., Run],
    workspace: Path,
    exposed_gateway: str,
    database_url: str,
    docker_client: Any,
) -> None:
    db_port = int(database_url.rsplit(":", 1)[1].split("/", 1)[0])
    run = worker_setup(
        {
            "write": {"NOTES.md": "hello from the worker\n"},
            "probe": [["host.docker.internal", db_port]],
        }
    )
    task_id, runtime = run.task_id, run.runtime
    events = events_of(task_id)
    assert status_of(task_id) is TaskStatus.SUCCEEDED, events.get("ringkeeper.error")

    [report] = events["stub.environment"]
    assert report["uid"] == 1000
    assert set(report["mounts"]) - DOCKER_INJECTED == EXPECTED_MOUNTS
    assert report["forbidden_present"] == []  # no Docker socket, no data dir
    assert report["master_keys"] == []
    assert report["workspace_writable"] is True
    assert [n for n in BILLING_ENV_VARS if n in report["env_names"]] == ["ANTHROPIC_API_KEY"]

    gateways = {e["server"]: e for e in events["stub.gateway"]}
    assert gateways["ringkeeper-supabase"]["ok"] is True, gateways
    assert "query" in gateways["ringkeeper-supabase"]["tools"]
    assert gateways["ringkeeper-n8n"]["ok"] is True

    [probe] = events["stub.probe"]
    assert probe["reachable"] is False  # host services aren't reachable by name

    # The work landed in acme's workspace, and the task recorded it.
    assert (workspace / "NOTES.md").read_text(encoding="utf-8") == "hello from the worker\n"
    with session_scope() as session:
        assert tasks.get_task(session, task_id).changed_files == ["NOTES.md"]

    # The session's token is dead now, and the container is gone.
    token = runtime.specs[0].env["RINGKEEPER_GATEWAY_TOKEN"]
    response = httpx2.post(
        f"{exposed_gateway}/mcp/supabase",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json, text/event-stream",
        },
    )
    assert response.status_code == 401
    assert response.json()["error_description"] == "worker token has been revoked"
    session_id = runtime.specs[0].labels[LABEL_SESSION]
    assert (
        docker_client.containers.list(all=True, filters={"label": f"{LABEL_SESSION}={session_id}"})
        == []
    )


def test_subscription_mode_gives_only_the_oauth_token(
    worker_setup: Callable[..., Run], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RINGKEEPER_CLAUDE_AUTH_MODE", "subscription")
    task_id = worker_setup({}).task_id
    [report] = events_of(task_id)["stub.environment"]
    assert [n for n in BILLING_ENV_VARS if n in report["env_names"]] == ["CLAUDE_CODE_OAUTH_TOKEN"]
    assert "ANTHROPIC_API_KEY" not in report["env_names"]


def test_git_workspace_gets_a_task_branch(
    worker_setup: Callable[..., Run], workspace: Path
) -> None:
    def git(*args: str) -> str:
        return subprocess.run(  # noqa: S603
            ["git", "-C", str(workspace), *args],  # noqa: S607
            capture_output=True, text=True, check=True,
        ).stdout.strip()  # fmt: skip

    git("init", "-q", "-b", "main")
    git("config", "user.email", "dev@example.com")
    git("config", "user.name", "Dev")
    (workspace / "README.md").write_text("acme\n", encoding="utf-8")
    git("add", "-A")
    git("commit", "-q", "-m", "initial")
    main = git("rev-parse", "main")
    for path in workspace.rglob("*"):
        path.chmod(0o777)

    task_id = worker_setup({"write": {"NOTES.md": "notes\n"}}).task_id
    assert status_of(task_id) is TaskStatus.SUCCEEDED, events_of(task_id).get("ringkeeper.error")
    with session_scope() as session:
        task = tasks.get_task(session, task_id)
        branch, commit, changed = task.git_branch, task.git_commit, task.changed_files
    assert branch == f"ringkeeper/{task_id}"
    assert changed == ["NOTES.md"]
    assert branch is not None
    assert git("rev-parse", "main") == main  # main untouched
    assert git("rev-parse", branch) == commit
    assert git("show", f"{branch}:NOTES.md") == "notes"


def test_max_runtime_kills_a_real_worker(
    worker_setup: Callable[..., Run], docker_client: Any
) -> None:
    run = worker_setup({"sleep": 120}, max_runtime_s=4, wait=False)
    deadline = time.monotonic() + 60
    while status_of(run.task_id) is not TaskStatus.TIMED_OUT:
        assert time.monotonic() < deadline, status_of(run.task_id)
        run.manager.tick()  # the manager's loop does this every second
        time.sleep(0.5)
    runtime = run.runtime
    session_id = runtime.specs[0].labels[LABEL_SESSION]
    deadline = time.monotonic() + 30
    while docker_client.containers.list(
        all=True, filters={"label": f"{LABEL_SESSION}={session_id}"}
    ):
        assert time.monotonic() < deadline
        time.sleep(0.5)
