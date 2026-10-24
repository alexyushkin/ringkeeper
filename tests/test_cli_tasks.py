"""The task CLI against a live manager loop on the fake runtime."""

import threading
import time
from collections.abc import Callable, Iterator

import pytest
from typer.testing import CliRunner, Result

from ringkeeper.cli.main import app
from ringkeeper.cli.tasks import render_event
from ringkeeper.db import session_scope
from ringkeeper.models import TaskStatus
from ringkeeper.services import tasks
from ringkeeper.vault import get_vault
from ringkeeper.workers.manager import Manager
from ringkeeper.workers.runtime import WorkerSpec
from tests.fake_runtime import FakeRuntime, Script, line, result_line, workspace_line

runner = CliRunner()


def cli(*args: str) -> Result:
    return runner.invoke(app, list(args))


@pytest.fixture
def live(
    task_registry: None, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Callable[[Callable[[WorkerSpec], Script]], FakeRuntime]]:
    monkeypatch.setattr("ringkeeper.cli.tasks.POLL_SECONDS", 0.05)
    monkeypatch.setenv("RINGKEEPER_MANAGER_POLL_SECONDS", "0.05")
    stop = threading.Event()
    threads: list[threading.Thread] = []

    def start(script: Callable[[WorkerSpec], Script]) -> FakeRuntime:
        runtime = FakeRuntime(script)
        thread = threading.Thread(
            target=Manager(runtime, get_vault()).run_forever, args=(stop,), daemon=True
        )
        thread.start()
        threads.append(thread)
        return runtime

    yield start
    stop.set()
    for thread in threads:
        thread.join(5)


def chatty(_spec: WorkerSpec) -> Script:
    return Script(
        before=[
            line("sdk.assistant", message={"content": [
                {"_kind": "TextBlock", "text": "Listing acme customers."},
                {"_kind": "ToolUseBlock", "name": "mcp__ringkeeper-supabase__query",
                 "input": {"sql": "SELECT * FROM customers"}},
            ]}),
            line("sdk.user", message={"content": [
                {"_kind": "ToolResultBlock", "content": "instance 'globex' is not available",
                 "is_error": True},
            ]}),
        ],
        after=[
            result_line(turns=5, cost=0.0321),
            workspace_line(["NOTES.md"], branch="ringkeeper/t"),
        ],
    )  # fmt: skip


def test_run_follows_until_done(
    live: Callable[[Callable[[WorkerSpec], Script]], FakeRuntime],
) -> None:
    live(chatty)
    result = cli("task", "run", "--project", "acme-web", "List acme customers")
    assert result.exit_code == 0, result.output
    out = result.stdout
    assert out.startswith("queued task ")
    assert "assistant: Listing acme customers." in out
    assert "tool: mcp__ringkeeper-supabase__query" in out
    assert "tool error: instance 'globex' is not available" in out
    assert "result: success, 5 turns, ~$0.0321 (SDK estimate)" in out
    assert "changes on ringkeeper/t: NOTES.md" in out
    assert ": succeeded, 5 turns" in out.splitlines()[-1]


def test_list_and_show(live: Callable[[Callable[[WorkerSpec], Script]], FakeRuntime]) -> None:
    live(chatty)
    run = cli("task", "run", "--project", "acme-web", "List acme customers")
    task_id = run.stdout.split()[2]
    listed = cli("task", "list")
    assert task_id[:8] in listed.stdout
    assert "succeeded" in listed.stdout
    shown = cli("task", "show", task_id[:8])
    assert shown.exit_code == 0, shown.output
    assert "status:    succeeded" in shown.stdout
    assert "turns:     5" in shown.stdout
    assert "cost:      ~$0.0321 (SDK estimate)" in shown.stdout
    assert "changed:   NOTES.md" in shown.stdout
    assert "branch:    ringkeeper/t @ abc123" in shown.stdout
    assert "last messages:" in shown.stdout
    assert "assistant: Listing acme customers." in shown.stdout


def test_detach_then_stop(live: Callable[[Callable[[WorkerSpec], Script]], FakeRuntime]) -> None:
    runtime = live(lambda _spec: Script(delay=30))
    run = cli("task", "run", "--project", "acme-web", "--detach", "long job")
    task_id = run.stdout.split()[2]
    deadline = time.monotonic() + 5
    while runtime.running == 0:
        assert time.monotonic() < deadline
        time.sleep(0.05)
    stopped = cli("task", "stop", task_id)
    assert "the worker is being killed" in stopped.stdout
    follow = cli("task", "show", task_id)
    deadline = time.monotonic() + 5
    while "status:    stopped" not in follow.stdout:
        assert time.monotonic() < deadline, follow.stdout
        time.sleep(0.1)
        follow = cli("task", "show", task_id)
    again = cli("task", "stop", task_id)
    assert again.exit_code == 1
    assert "already stopped" in again.stderr


def test_stop_all(live: Callable[[Callable[[WorkerSpec], Script]], FakeRuntime]) -> None:
    runtime = live(lambda _spec: Script(delay=30))
    ids = [
        cli("task", "run", "--project", "acme-web", "--detach", f"job {i}").stdout.split()[2]
        for i in range(2)
    ]
    deadline = time.monotonic() + 5
    while runtime.running < 2:
        assert time.monotonic() < deadline
        time.sleep(0.05)
    result = cli("stop-all")
    assert "stopping 2 task(s)" in result.stdout
    deadline = time.monotonic() + 5
    while True:
        with session_scope() as session:
            statuses = {tasks.get_task(session, i).status for i in ids}
        if statuses == {TaskStatus.STOPPED}:
            break
        assert time.monotonic() < deadline, statuses
        time.sleep(0.05)
    assert "nothing was running" in cli("stop-all").stdout


def test_resume(live: Callable[[Callable[[WorkerSpec], Script]], FakeRuntime]) -> None:
    runtime = live(chatty)
    run = cli("task", "run", "--project", "acme-web", "write notes")
    task_id = run.stdout.split()[2]
    resumed = cli("task", "resume", task_id[:8], "add a line to the notes")
    assert resumed.exit_code == 0, resumed.output
    assert "queued session 2 of task" in resumed.stdout
    # Only the new session's events, not session 1's again.
    assert resumed.stdout.count("result: success") == 1
    assert len(runtime.started) == 2
    shown = cli("task", "show", task_id).stdout
    assert "turns:     10" in shown  # both sessions
    assert "add a line to the notes" in shown


def test_run_rejects_budget_in_subscription_mode(
    task_registry: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RINGKEEPER_CLAUDE_AUTH_MODE", "subscription")
    result = cli("task", "run", "--project", "acme-web", "--budget", "1", "--detach", "x")
    assert result.exit_code == 1
    assert "only applies in api_key mode" in result.stderr


def test_run_unknown_project(database_url: str) -> None:
    result = cli("task", "run", "--project", "ghost", "--detach", "x")
    assert result.exit_code == 1
    assert "project 'ghost' does not exist" in result.stderr


def test_render_event_skips_noise() -> None:
    assert render_event("sdk.system", {"message": {"subtype": "init"}}) is None
    assert (
        render_event("sdk.assistant", {"message": {"content": [{"_kind": "ThinkingBlock"}]}})
        is None
    )
    assert render_event("ringkeeper.error", {"message": "boom"}) == "error: boom"
