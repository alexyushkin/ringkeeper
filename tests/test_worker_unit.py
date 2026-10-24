"""The worker runner's pure parts: config, SDK options, output, git rules."""

import io
import json
import subprocess
from pathlib import Path

import pytest
from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock

from ringkeeper_worker import emit as emitter
from ringkeeper_worker.agent import build_options
from ringkeeper_worker.config import ConfigError, WorkerConfig, load
from ringkeeper_worker.workspace import Workspace

TASK = {
    "task_id": "t-1",
    "session_ordinal": 1,
    "prompt": "do things",
    "max_turns": 7,
    "branch": "ringkeeper/t-1",
    "mcp_servers": {"ringkeeper-supabase": "http://gateway:8100/mcp/supabase"},
    "disallowed_tools": ["WebFetch", "WebSearch"],
    "max_budget_usd": 1.5,
}


def config(**overrides: object) -> WorkerConfig:
    env = {
        "RINGKEEPER_TASK": json.dumps({**TASK, **overrides}),
        "RINGKEEPER_GATEWAY_TOKEN": "gw-token",
    }
    return load(env)


# --- Config and SDK options ---


def test_config_round_trip() -> None:
    loaded = config(resume="sdk-session-9")
    assert loaded.max_turns == 7
    assert loaded.resume == "sdk-session-9"
    assert loaded.workspace == "/workspace"
    assert "gw-token" not in repr(loaded)


@pytest.mark.parametrize(
    "env",
    [{}, {"RINGKEEPER_TASK": "{}"}, {"RINGKEEPER_TASK": "{}", "RINGKEEPER_GATEWAY_TOKEN": "x"}],
)
def test_config_errors(env: dict[str, str]) -> None:
    with pytest.raises(ConfigError):
        load(env)


def test_options_isolate_the_session() -> None:
    options = build_options(config())
    assert options.cwd == "/workspace"
    assert options.setting_sources == []  # no user, project or local settings
    assert options.strict_mcp_config is True
    assert options.mcp_servers == {
        "ringkeeper-supabase": {
            "type": "http",
            "url": "http://gateway:8100/mcp/supabase",
            "headers": {"Authorization": "Bearer gw-token"},
        }
    }
    assert options.max_turns == 7
    assert options.max_budget_usd == 1.5
    assert options.permission_mode == "bypassPermissions"
    assert options.disallowed_tools == ["WebFetch", "WebSearch"]
    assert options.resume is None


# --- Output ---


def test_sdk_messages_become_json_lines(monkeypatch: pytest.MonkeyPatch) -> None:
    out = io.StringIO()
    monkeypatch.setattr(emitter, "_stream", out)
    result = ResultMessage(
        subtype="success", duration_ms=10, duration_api_ms=5, is_error=False, num_turns=3,
        session_id="s-1", total_cost_usd=0.01, usage={"input_tokens": 1},
    )  # fmt: skip
    assistant = AssistantMessage(content=[TextBlock(text="hi")], model="claude-test")
    emitter.emit(emitter.message_type(assistant), message=assistant)
    emitter.emit(emitter.message_type(result), message=result)
    lines = [json.loads(line) for line in out.getvalue().splitlines()]
    assert [line["type"] for line in lines] == ["sdk.assistant", "sdk.result"]
    assert lines[0]["message"]["content"][0] == {"text": "hi", "_kind": "TextBlock"}
    assert lines[1]["message"]["num_turns"] == 3
    assert lines[1]["message"]["session_id"] == "s-1"


# --- Git rules ---


def git(root: Path, *args: str) -> str:
    return subprocess.run(  # noqa: S603 (test helper, fixed argv)
        ["git", "-C", str(root), *args],  # noqa: S607
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "acme-web"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "dev@example.com")
    git(root, "config", "user.name", "Dev")
    (root / "README.md").write_text("acme\n", encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "initial")
    return root


def test_work_lands_on_the_task_branch(repo: Path) -> None:
    main_before = git(repo, "rev-parse", "main")
    workspace = Workspace(repo, "ringkeeper/t-1")
    workspace.start()
    assert git(repo, "rev-parse", "--abbrev-ref", "HEAD") == "ringkeeper/t-1"
    (repo / "NOTES.md").write_text("notes\n", encoding="utf-8")

    outcome = workspace.finish("ringkeeper: task t-1 session 1")
    assert outcome.git
    assert outcome.changed_files == ["NOTES.md"]
    assert outcome.branch == "ringkeeper/t-1"
    assert outcome.commit == git(repo, "rev-parse", "ringkeeper/t-1")
    assert outcome.restored is None
    assert git(repo, "rev-parse", "main") == main_before
    assert git(repo, "log", "-1", "--format=%an", "ringkeeper/t-1") == "Ringkeeper worker"


def test_main_moved_by_the_agent_is_put_back(repo: Path) -> None:
    main_before = git(repo, "rev-parse", "main")
    workspace = Workspace(repo, "ringkeeper/t-1")
    workspace.start()
    # The agent ignores the rules: switches to main and commits there.
    git(repo, "switch", "-q", "main")
    (repo / "sneaky.txt").write_text("x\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "on main")
    sneaky = git(repo, "rev-parse", "main")

    outcome = workspace.finish("ringkeeper: task t-1 session 1")
    assert git(repo, "rev-parse", "main") == main_before
    assert outcome.restored is not None
    assert "restored" in outcome.restored
    assert git(repo, "rev-parse", "ringkeeper/t-1-from-main") == sneaky  # nothing lost
    assert git(repo, "rev-parse", "--abbrev-ref", "HEAD") == "ringkeeper/t-1"


def test_resume_reuses_the_task_branch(repo: Path) -> None:
    first = Workspace(repo, "ringkeeper/t-1")
    first.start()
    (repo / "NOTES.md").write_text("one\n", encoding="utf-8")
    first.finish("session 1")

    second = Workspace(repo, "ringkeeper/t-1")
    second.start()
    (repo / "NOTES.md").write_text("one\ntwo\n", encoding="utf-8")
    outcome = second.finish("session 2")
    assert outcome.changed_files == ["NOTES.md"]
    assert git(repo, "rev-list", "--count", "main..ringkeeper/t-1") == "2"
    assert outcome.protected_branch == "main"


def test_no_repo_records_changed_files(tmp_path: Path) -> None:
    (tmp_path / "keep.txt").write_text("same", encoding="utf-8")
    (tmp_path / "edit.txt").write_text("before", encoding="utf-8")
    (tmp_path / "gone.txt").write_text("bye", encoding="utf-8")
    workspace = Workspace(tmp_path, "ringkeeper/t-1")
    workspace.start()
    (tmp_path / "edit.txt").write_text("after, longer", encoding="utf-8")
    (tmp_path / "gone.txt").unlink()
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "new.md").write_text("new", encoding="utf-8")
    outcome = workspace.finish("unused")
    assert not outcome.git
    assert outcome.changed_files == ["edit.txt", "gone.txt", "sub/new.md"]
