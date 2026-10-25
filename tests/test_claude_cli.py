"""The real Claude Code CLI as a desktop worker (a local process), talking to
a fake Anthropic API: no key, no bill, no network.

It checks the whole path a desktop task takes: the session manager, the
process runtime, the worker runner, the Agent SDK, the bundled CLI, and the
shell tool the CLI picks. On Windows without Git Bash, Claude Code falls back
to PowerShell; CI runs this once that way (RINGKEEPER_EXPECT_SHELL=PowerShell,
with Git for Windows hidden and MinGit instead, like the desktop bundle).
"""

import os
import time
from pathlib import Path

import pytest

from ringkeeper.config import get_settings
from ringkeeper.db import session_scope
from ringkeeper.models import TERMINAL_STATUSES, Autonomy, TaskStatus
from ringkeeper.services import tasks
from ringkeeper.vault import get_vault
from ringkeeper.workers.manager import Manager
from ringkeeper.workers.process import ProcessRuntime
from tests.conftest import CLAUDE_API_KEY, free_port
from tests.fake_anthropic import FakeAnthropic
from tests.helpers import make_project

COMMANDS = {
    "Bash": "echo hello from claude > OUT.txt",
    "PowerShell": "Set-Content -Path OUT.txt -Value 'hello from claude'",
}


def test_a_desktop_task_runs_the_real_cli(
    bare_gateway: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeAnthropic(COMMANDS)
    url = fake.start(free_port())
    try:
        workspace = tmp_path / "acme-web"
        workspace.mkdir()
        monkeypatch.setenv("RINGKEEPER_GATEWAY_INTERNAL_URL", bare_gateway)
        monkeypatch.setenv("RINGKEEPER_WORKER_RUNTIME", "process")
        monkeypatch.setenv("RINGKEEPER_WORKER_ANTHROPIC_BASE_URL", url)
        settings = get_settings()
        get_vault(settings).store(settings.claude_api_key_ref, CLAUDE_API_KEY)
        with session_scope() as session:  # relaxed: the shell tool is allowed
            make_project(
                session, "acme-web", workspace_path=str(workspace), autonomy=Autonomy.RELAXED
            )
            task = tasks.create_task(session, settings, project="acme-web", prompt="write OUT")
            task_id = str(task.id)
        tools = settings.worker_tools_path  # CI: MinGit, standing in for the bundled git
        manager = Manager(ProcessRuntime(tmp_path / "workers", tools_path=tools), get_vault())
        manager.tick()
        deadline = time.monotonic() + 180
        while True:
            with session_scope() as session:
                found = tasks.get_task(session, task_id)
                status, error = found.status, found.error
            if status in TERMINAL_STATUSES:
                break
            assert time.monotonic() < deadline, "the CLI didn't finish"
            time.sleep(0.5)
            manager.tick()
        manager.wait_idle(30)
    finally:
        fake.stop()

    assert status is TaskStatus.SUCCEEDED, error
    assert fake.requests, "the CLI never called the (fake) API"
    offered = fake.shell_tools()
    expected = os.environ.get("RINGKEEPER_EXPECT_SHELL")
    if expected:
        assert offered[0] == expected, offered
    assert offered
    assert "hello from claude" in (workspace / "OUT.txt").read_text(encoding="utf-8-sig")
