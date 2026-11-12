"""The worker runner's pure parts: config, SDK options, output, git rules."""

import io
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, cast

import pytest
from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock

from ringkeeper_worker import emit as emitter
from ringkeeper_worker import inbox
from ringkeeper_worker.agent import build_options
from ringkeeper_worker.checks import run_checks
from ringkeeper_worker.config import ConfigError, WorkerConfig, load
from ringkeeper_worker.workspace import Workspace, WorkspaceError

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
    options = build_options(config(model="claude-sonnet-5-5"))
    assert options.cwd == "/workspace"
    assert options.setting_sources == []  # no user, project or local settings
    assert options.strict_mcp_config is True
    servers = dict(cast(dict[str, Any], options.mcp_servers))
    assert servers.pop(inbox.SERVER_NAME)["type"] == "sdk"  # ask_user, in-process
    assert servers == {
        "ringkeeper-supabase": {
            "type": "http",
            "url": "http://gateway:8100/mcp/supabase",
            "headers": {"Authorization": "Bearer gw-token"},
        }
    }
    assert options.model == "claude-sonnet-5-5"
    prompt = cast(dict[str, Any], options.system_prompt)
    assert (prompt["type"], prompt["preset"]) == ("preset", "claude_code")
    assert "call the ask_user tool" in prompt["append"]
    assert "never leave part of it to a\nbackground task or agent" in prompt["append"]
    assert options.env == {"CLAUDE_CODE_DISABLE_BACKGROUND_TASKS": "1"}  # nothing outlives it
    assert options.max_turns == 7
    assert options.max_budget_usd == 1.5
    assert options.permission_mode == "bypassPermissions"
    assert options.disallowed_tools == ["WebFetch", "WebSearch"]
    assert options.resume is None


def test_work_off_the_code_gets_a_short_prompt() -> None:
    """Data, ops and general tasks (no branch) get a short prompt with the same note."""
    prompt = build_options(config(git=False)).system_prompt
    assert isinstance(prompt, str)
    assert prompt.startswith("You are an agent doing one task for the operator")
    # The date, which Claude Code's own prompt gives.
    assert re.search(r"Today is\n\w+day, \d{4}-\d\d-\d\d\.", prompt)
    assert "call the ask_user tool" in prompt
    assert "never leave part of it to a\nbackground task or agent" in prompt
    assert len(prompt) < 4000  # Claude Code's own is several times this


@pytest.mark.anyio
async def test_ask_user_records_the_question_then_blocks_every_tool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    out = io.StringIO()
    monkeypatch.setattr(emitter, "_stream", out)
    tools: list[Any] = []
    real_server = inbox.create_sdk_mcp_server

    def capture(name: str, tools: list[Any]) -> Any:
        captured.extend(tools)
        return real_server(name, tools=tools)

    captured = tools
    monkeypatch.setattr(inbox, "create_sdk_mcp_server", capture)
    state = inbox.AskState()
    _, hooks = inbox.build(state)
    [hook] = hooks["PreToolUse"][0].hooks
    handler = tools[0].handler
    context = cast(Any, {"signal": None})

    assert await hook(cast(Any, {}), "t1", context) == {}  # tools run normally before asking
    empty = await handler({"question": "  "})
    assert empty["is_error"] is True
    reply = await handler({"question": "Which column?"})
    assert "End your turn now" in reply["content"][0]["text"]
    await handler({"question": "And another?"})  # only the first question counts
    assert state.question == "Which column?"
    assert [json.loads(x)["question"] for x in out.getvalue().splitlines()] == ["Which column?"]
    blocked = cast(dict[str, Any], await hook(cast(Any, {}), "t2", context))
    assert blocked["hookSpecificOutput"]["permissionDecision"] == "deny"


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


def test_a_new_task_branch_starts_from_main_not_from_an_earlier_task(repo: Path) -> None:
    earlier = Workspace(repo, "ringkeeper/t-1")
    earlier.start()
    (repo / "plans.py").write_text("def format_plan(): ...\n", encoding="utf-8")
    earlier.finish("session 1")
    # The checkout is left on t-1, which nobody merged.
    assert git(repo, "rev-parse", "--abbrev-ref", "HEAD") == "ringkeeper/t-1"

    later = Workspace(repo, "ringkeeper/t-2")
    later.start()
    assert git(repo, "merge-base", "main", "HEAD") == git(repo, "rev-parse", "main")
    assert not (repo / "plans.py").exists()
    (repo / "NOTES.md").write_text("voice works\n", encoding="utf-8")
    outcome = later.finish("session 1")
    assert outcome.changed_files == ["NOTES.md"]
    assert outcome.protected_branch == "main"
    assert git(repo, "rev-list", "--count", "main..ringkeeper/t-2") == "1"


def test_a_resume_diffs_against_where_the_branch_left_main(repo: Path) -> None:
    first = Workspace(repo, "ringkeeper/t-1")
    first.start()
    (repo / "NOTES.md").write_text("one\n", encoding="utf-8")
    first.finish("session 1")
    # Meanwhile main moves on (someone merged other work).
    git(repo, "switch", "-q", "main")
    (repo / "OTHER.md").write_text("other\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "other work")

    second = Workspace(repo, "ringkeeper/t-1")
    second.start()
    (repo / "NOTES.md").write_text("one\ntwo\n", encoding="utf-8")
    outcome = second.finish("session 2")
    assert outcome.changed_files == ["NOTES.md"]
    assert "OTHER.md" not in (second.diff() or "")


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


def test_only_files_touched_during_the_run_are_committed(repo: Path) -> None:
    # The user has a file of their own, not committed; it must not be swept in.
    (repo / "scratch.txt").write_text("mine\n", encoding="utf-8")
    (repo / ".gitignore").write_text("*.log\n", encoding="utf-8")
    git(repo, "add", ".gitignore")
    git(repo, "commit", "-q", "-m", "ignore logs")
    workspace = Workspace(repo, "ringkeeper/t-1")
    workspace.start()
    (repo / "NOTES.md").write_text("notes\n", encoding="utf-8")
    (repo / "debug.log").write_text("noise\n", encoding="utf-8")  # gitignored

    outcome = workspace.finish("ringkeeper: task t-1 session 1")
    assert outcome.changed_files == ["NOTES.md"]
    assert git(repo, "show", "--name-only", "--format=", "ringkeeper/t-1") == "NOTES.md"
    assert "scratch.txt" in git(repo, "status", "--porcelain")  # still the user's


# --- The user's checkout is theirs ---


def test_the_checkout_is_put_back_when_the_session_ends(repo: Path) -> None:
    git(repo, "switch", "-q", "-c", "feature/login")
    workspace = Workspace(repo, "ringkeeper/t-1")
    workspace.start()
    (repo / "NOTES.md").write_text("notes\n", encoding="utf-8")
    outcome = workspace.release(workspace.finish("session 1"))
    assert outcome is not None
    assert outcome.branch == "ringkeeper/t-1"  # the work stays on its branch
    assert git(repo, "rev-parse", "--abbrev-ref", "HEAD") == "feature/login"
    assert not (repo / "NOTES.md").exists()  # it's on the task branch, not here


def test_a_read_only_task_leaves_no_branch(repo: Path) -> None:
    workspace = Workspace(repo, "ringkeeper/t-1")
    workspace.start()
    outcome = workspace.release(workspace.finish("session 1"))
    assert outcome is not None
    assert (outcome.branch, outcome.commit, outcome.changed_files) == (None, None, [])
    assert git(repo, "branch", "--list", "ringkeeper/*") == ""
    assert git(repo, "rev-parse", "--abbrev-ref", "HEAD") == "main"


def test_a_resume_with_nothing_new_keeps_the_earlier_work(repo: Path) -> None:
    first = Workspace(repo, "ringkeeper/t-1")
    first.start()
    (repo / "NOTES.md").write_text("one\n", encoding="utf-8")
    first.release(first.finish("session 1"))
    second = Workspace(repo, "ringkeeper/t-1")
    second.start()
    outcome = second.release(second.finish("session 2"))  # read only this time
    assert outcome is not None
    assert outcome.branch == "ringkeeper/t-1"
    assert git(repo, "rev-list", "--count", "main..ringkeeper/t-1") == "1"


def test_uncommitted_changes_are_never_carried_onto_a_task_branch(repo: Path) -> None:
    (repo / "README.md").write_text("acme, edited by a human\n", encoding="utf-8")
    workspace = Workspace(repo, "ringkeeper/t-1")
    with pytest.raises(WorkspaceError) as refused:
        workspace.start()
    said = str(refused.value)
    assert said.startswith("The project folder has changes that aren't committed (README.md)")
    assert "git" not in said.lower()  # no raw git error
    assert git(repo, "rev-parse", "--abbrev-ref", "HEAD") == "main"  # nothing changed
    assert git(repo, "branch", "--list", "ringkeeper/*") == ""
    assert "README.md" in git(repo, "status", "--porcelain")


@pytest.mark.skipif(sys.platform == "win32", reason="Windows files have no executable bit")
def test_a_mode_alone_isnt_uncommitted_work(repo: Path) -> None:
    """A mounted folder (Docker Desktop) can show every file as executable."""
    (repo / "README.md").chmod(0o777)
    workspace = Workspace(repo, "ringkeeper/t-1")
    workspace.start()
    assert git(repo, "rev-parse", "--abbrev-ref", "HEAD") == "ringkeeper/t-1"


def test_checks_run_without_secrets_and_report_each_result(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    out = io.StringIO()
    monkeypatch.setattr(emitter, "_stream", out)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api03-must-not-leak")
    monkeypatch.setenv("RINGKEEPER_GATEWAY_TOKEN", "gw-must-not-leak")
    python = f'"{sys.executable}"'
    leak = (
        f'{python} -c "import os, sys; '
        "print(os.environ.get('ANTHROPIC_API_KEY'), os.environ.get('RINGKEEPER_GATEWAY_TOKEN'));"
        ' sys.exit(3)"'
    )
    summary = run_checks([f'{python} -c "print(1)"', leak], str(tmp_path))
    assert summary["passed"] is False
    first, second = cast(list[dict[str, Any]], summary["results"])
    assert (first["passed"], first["exit_code"]) == (True, 0)
    assert (second["passed"], second["exit_code"]) == (False, 3)
    assert "None None" in second["output"]
    assert "must-not-leak" not in out.getvalue()
    [event] = [json.loads(x) for x in out.getvalue().splitlines()]
    assert event["type"] == "ringkeeper.checks"


# --- What the agent may not leave in .git ---


def test_gitguard_puts_config_hooks_and_info_back(tmp_path: Path) -> None:
    from ringkeeper_worker import gitguard  # noqa: PLC0415

    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)  # noqa: S603, S607
    git = tmp_path / ".git"
    config = (git / "config").read_bytes()
    before = gitguard.capture(tmp_path)
    assert before is not None
    assert gitguard.differences(before, tmp_path) == []

    (git / "hooks" / "post-commit").write_text("#!/bin/sh\necho owned\n", encoding="utf-8")
    (git / "config").write_bytes(config + b"[core]\n\tfsmonitor = evil\n")
    (git / "info" / "attributes").write_text("* filter=evil\n", encoding="utf-8")
    (git / "hooks" / "deep").mkdir()
    (git / "hooks" / "deep" / "x").write_text("x", encoding="utf-8")
    (tmp_path / "vendor" / ".git").mkdir(parents=True)
    assert gitguard.differences(before, tmp_path) == [
        "config", "hooks/deep/x", "hooks/post-commit", "info/attributes",
    ]  # fmt: skip
    gitguard.restore(before, tmp_path)
    assert gitguard.differences(before, tmp_path) == []
    assert (git / "config").read_bytes() == config
    assert not (git / "hooks" / "post-commit").exists()
    assert not (git / "hooks" / "deep").exists()
    assert gitguard.nested_repos(tmp_path) == ["vendor/.git"]
