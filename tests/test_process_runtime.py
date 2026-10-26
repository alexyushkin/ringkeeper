"""Integration: workers as local processes (desktop mode), the stub agent (no
LLM), the real gateway. Runs on Windows, macOS and Linux, without Docker.
"""

import json
import os
import subprocess
import sys
import time
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select

from ringkeeper.claude_auth import BILLING_ENV_VARS
from ringkeeper.config import get_settings
from ringkeeper.db import session_scope
from ringkeeper.models import TERMINAL_STATUSES, SessionEvent, TaskStatus
from ringkeeper.services import revocations, tasks
from ringkeeper.vault import get_vault
from ringkeeper.workers.env import TASK_ENV, TOKEN_ENV
from ringkeeper.workers.manager import Manager
from ringkeeper.workers.process import ProcessRuntime, parse_memory
from ringkeeper.workers.runtime import WorkerSpec
from tests.conftest import CLAUDE_API_KEY, CLAUDE_OAUTH_TOKEN
from tests.helpers import bind, make_instance, make_project

GIT = (
    "git",
    "-c",
    "user.name=t",
    "-c",
    "user.email=t@example.com",
    "-c",
    "init.defaultBranch=main",
)


def git(*args: str) -> str:
    done = subprocess.run([*GIT, *args], capture_output=True, text=True, check=True)  # noqa: S603
    return done.stdout


def alive(pid: int) -> bool:
    if sys.platform == "win32":
        import ctypes  # noqa: PLC0415

        kernel32: Any = getattr(ctypes, "WinDLL")("kernel32")  # noqa: B009
        handle = kernel32.OpenProcess(0x00100000 | 0x1000, False, pid)  # SYNCHRONIZE | QUERY
        if not handle:
            return False
        try:
            return int(kernel32.WaitForSingleObject(handle, 0)) == 0x102  # WAIT_TIMEOUT
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


@dataclass
class Run:
    task_id: str
    manager: Manager
    runtime: ProcessRuntime


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    path = tmp_path / "acme-web"
    path.mkdir()
    return path


@pytest.fixture
def run_stub(
    bare_gateway: str, workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Callable[..., Run]]:
    monkeypatch.setenv("RINGKEEPER_GATEWAY_INTERNAL_URL", bare_gateway)
    monkeypatch.setenv("RINGKEEPER_WORKER_RUNTIME", "process")
    settings = get_settings()
    get_vault(settings).store(settings.claude_api_key_ref, CLAUDE_API_KEY)
    get_vault(settings).store(settings.claude_oauth_token_ref, CLAUDE_OAUTH_TOKEN)
    with session_scope() as session:
        make_project(session, "acme-web", workspace_path=str(workspace))
        # A connection, so the worker gets a gateway endpoint (only its project's
        # services); the stub only lists instances, so no database is needed.
        make_instance(session, "acme-db", secret_ref="file://acme/db")
    bind("acme-web", "acme-db", is_default=True)
    runtime = ProcessRuntime(tmp_path / "workers")
    managers: list[Manager] = []

    def run(stub: dict[str, Any], *, max_runtime_s: int = 120) -> Run:
        monkeypatch.setenv("RINGKEEPER_WORKER_STUB", json.dumps(stub))
        with session_scope() as session:
            task = tasks.create_task(
                session, get_settings(), project="acme-web", prompt="stub run",
                max_runtime_s=max_runtime_s,
            )  # fmt: skip
            task_id = str(task.id)
        manager = Manager(runtime, get_vault())
        managers.append(manager)
        deadline = time.monotonic() + 90
        manager.tick()
        while status_of(task_id) not in TERMINAL_STATUSES:  # ticks enforce the time limit
            assert time.monotonic() < deadline, "the worker didn't finish"
            time.sleep(0.2)
            manager.tick()
        manager.wait_idle(30)
        return Run(task_id, manager, runtime)

    yield run
    for manager in managers:
        manager.wait_idle(30)


def status_of(task_id: str) -> TaskStatus:
    with session_scope() as session:
        return tasks.get_task(session, task_id).status


def events_of(task_id: str) -> dict[str, list[dict[str, Any]]]:
    found: dict[str, list[dict[str, Any]]] = {}
    with session_scope() as session:
        ids = [s.id for s in tasks.get_task(session, task_id).sessions]
        rows = session.scalars(
            select(SessionEvent).where(SessionEvent.session_id.in_(ids)).order_by(SessionEvent.id)
        )
        for event in rows:
            found.setdefault(event.type, []).append(event.payload)
    return found


def test_a_worker_runs_in_the_project_folder_with_its_own_home(
    run_stub: Callable[..., Run], workspace: Path, tmp_path: Path
) -> None:
    run = run_stub({"write": {"NOTES.md": "hello from the worker\n"}})
    events = events_of(run.task_id)
    assert status_of(run.task_id) is TaskStatus.SUCCEEDED, events.get("ringkeeper.error")
    assert (workspace / "NOTES.md").read_text(encoding="utf-8") == "hello from the worker\n"

    report = events["stub.environment"][0]
    state = tmp_path / "workers"
    assert Path(report["workspace"]) == workspace
    assert report["workspace_writable"] is True
    assert Path(report["home"]).is_relative_to(state)  # not the user's own home
    assert Path(report["claude_config_dir"]).is_relative_to(state)
    names = set(report["env_names"])
    assert not {TASK_ENV, TOKEN_ENV} & names  # popped before the agent starts
    assert {n for n in names if n.startswith("RINGKEEPER_")} == set()
    assert names & set(BILLING_ENV_VARS) == {"ANTHROPIC_API_KEY"}  # exactly one credential

    gateway = events["stub.gateway"]
    assert gateway
    assert all(g["ok"] for g in gateway), gateway
    with session_scope() as session:  # the session's token is dead now
        row = tasks.get_task(session, run.task_id).sessions[0]
        assert row.token_jti
        assert revocations.is_revoked(session, row.token_jti)


def test_git_work_lands_on_the_task_branch(run_stub: Callable[..., Run], workspace: Path) -> None:
    git("init", "-q", str(workspace))
    (workspace / "README.md").write_text("acme\n", encoding="utf-8")
    git("-C", str(workspace), "add", ".")
    git("-C", str(workspace), "commit", "-qm", "start")
    run = run_stub({"write": {"NOTES.md": "notes\n"}})
    assert status_of(run.task_id) is TaskStatus.SUCCEEDED, events_of(run.task_id)
    with session_scope() as session:
        task = tasks.get_task(session, run.task_id)
        assert task.git_branch == tasks.branch_name(task.id)
        assert task.changed_files == ["NOTES.md"]
    # Reported without any checks configured: the architect reviews it.
    [diff] = events_of(run.task_id)["ringkeeper.diff"]
    assert "+++ b/NOTES.md" in diff["text"]
    log = git("-C", str(workspace), "log", "--oneline", "main")
    assert len(log.splitlines()) == 1  # main didn't move


def test_planted_git_hooks_are_undone_and_leftovers_killed(
    run_stub: Callable[..., Run], workspace: Path
) -> None:
    git("init", "-q", str(workspace))
    (workspace / "README.md").write_text("acme\n", encoding="utf-8")
    git("-C", str(workspace), "add", ".")
    git("-C", str(workspace), "commit", "-qm", "start")
    config_before = (workspace / ".git" / "config").read_text(encoding="utf-8")
    run = run_stub({"tamper": True, "leave_behind": True})
    events = events_of(run.task_id)
    assert status_of(run.task_id) is TaskStatus.SUCCEEDED, events.get("ringkeeper.error")
    time.sleep(0.5)  # the tamper loop would have re-planted its hook by now
    dotgit = workspace / ".git"
    assert (dotgit / "config").read_text(encoding="utf-8") == config_before
    assert not (dotgit / "hooks" / "post-commit").exists()
    assert not (dotgit / "hooks" / "pre-commit").exists()
    assert not (dotgit / "info" / "attributes").exists()
    assert not any(workspace.glob("*-RAN"))
    assert not alive(events["stub.left_behind"][0]["pid"])
    assert any("vendor" in w["message"] for w in events.get("ringkeeper.warning", []))


def test_the_time_limit_kills_the_whole_tree(run_stub: Callable[..., Run]) -> None:
    run = run_stub({"leave_behind": True, "sleep": 120}, max_runtime_s=10)
    assert status_of(run.task_id) is TaskStatus.TIMED_OUT
    assert not alive(events_of(run.task_id)["stub.left_behind"][0]["pid"])
    assert run.runtime.running_workers() == {}


def test_state_directories_are_listed_and_removed(run_stub: Callable[..., Run]) -> None:
    run = run_stub({})
    names = run.runtime.state_volumes()
    assert names
    assert all(n.startswith("ringkeeper-state-") for n in names)
    assert run.runtime.remove_volume(names[0])
    assert names[0] not in run.runtime.state_volumes()
    assert not run.runtime.remove_volume(names[0])
    with pytest.raises(Exception, match="not a worker state name"):
        run.runtime.state_dir("../escape")


def test_the_worker_inherits_nothing_from_ringkeeper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RINGKEEPER_MASTER_KEY", "never-in-a-worker")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "the-users-own-key")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "never-either")
    runtime = ProcessRuntime(tmp_path / "workers", tools_path=[str(tmp_path / "git" / "cmd")])
    spec = WorkerSpec(
        name="w", image="", workspace=str(tmp_path), network="",
        state_volume=f"ringkeeper-state-{uuid.uuid4()}-worker",
        env={TASK_ENV: json.dumps({"task_id": "t"}), "ANTHROPIC_API_KEY": "the-tasks-key",
             "CLAUDE_CONFIG_DIR": "/state/claude"},
    )  # fmt: skip
    env = runtime.environment(spec)
    assert "RINGKEEPER_MASTER_KEY" not in env
    assert "AWS_SECRET_ACCESS_KEY" not in env
    assert env["ANTHROPIC_API_KEY"] == "the-tasks-key"
    assert Path(env["CLAUDE_CONFIG_DIR"]) == runtime.state_dir(spec.state_volume) / "claude"
    assert json.loads(env[TASK_ENV])["workspace"] == str(tmp_path)
    assert env["PATH"].split(os.pathsep)[0] == str(tmp_path / "git" / "cmd")


def test_workers_commit_as_the_user_else_as_ringkeeper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A worker's own home has no ~/.gitconfig: its identity comes from the user's."""
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "no-global-config"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    runtime = ProcessRuntime(tmp_path / "workers")

    def identity(folder: Path) -> tuple[str, ...]:
        spec = WorkerSpec(
            name="w", image="", workspace=str(folder), network="",
            state_volume=f"ringkeeper-state-{uuid.uuid4()}-worker",
            env={TASK_ENV: json.dumps({"task_id": "t"})},
        )  # fmt: skip
        env = runtime.environment(spec)
        keys = ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL")
        return tuple(env[k] for k in keys)

    plain = tmp_path / "plain"
    plain.mkdir()
    default = ("Ringkeeper", "ringkeeper@localhost")
    assert identity(plain) == default * 2

    (tmp_path / "global").write_text("[user]\n\tname = Ada\n\temail = ada@example.com\n")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "global"))
    assert identity(plain) == ("Ada", "ada@example.com") * 2

    repo = tmp_path / "repo"
    git("init", "-q", str(repo))
    git("-C", str(repo), "config", "user.name", "Ada at work")
    git("-C", str(repo), "config", "user.email", "ada@work.example")
    assert identity(repo) == ("Ada at work", "ada@work.example") * 2  # the repo's own wins


def test_memory_sizes() -> None:
    assert parse_memory("2g") == 2 * 1024**3
    assert parse_memory("512m") == 512 * 1024**2
    assert parse_memory("1048576") == 1048576
