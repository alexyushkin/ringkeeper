"""Tasks on the owner's ChatGPT plan run on Codex (worker/ringkeeper_worker/codex.py). Here a
stand-in app-server (tests/fake_codex_app_server.py) plays Codex's side of the protocol, so the
worker's half is tested without the real program or a model."""

import hashlib
import io
import json
import sys
import tarfile
import time
import zipfile
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from ringkeeper_worker import codex, emit, inbox
from ringkeeper_worker.config import WorkerConfig
from ringkeeper_worker.edits import EDITS

FAKE = Path(__file__).with_name("fake_codex_app_server.py")


def config(tmp_path: Path, **changes: Any) -> WorkerConfig:
    base: dict[str, Any] = {
        "task_id": "t-1", "session_ordinal": 1, "prompt": "List the open orders.",
        "max_turns": 20, "branch": "rk/t-1", "gateway_token": "gw-token-123",
        "workspace": str(tmp_path), "git": False, "model": "gpt-5.5", "effort": "medium",
        "engine": "codex", "mcp_servers": {"ringkeeper-shop": "http://gateway/mcp/shop"},
    }  # fmt: skip
    return WorkerConfig(**(base | changes))


Events = Callable[[], list[dict[str, Any]]]


@pytest.fixture
def events(monkeypatch: pytest.MonkeyPatch) -> Iterator[Events]:
    """What the worker emitted so far."""
    out = io.StringIO()
    emit.set_stream(out)
    monkeypatch.setattr(EDITS, "paths", set[str]())
    monkeypatch.setattr(EDITS, "shell", False)
    yield lambda: [json.loads(line) for line in out.getvalue().splitlines()]
    emit.set_stream(sys.stdout)


def read_seen(path: Path) -> dict[str, Any]:
    """What the stand-in was told. Windows may hold the file a moment after it's replaced
    (an antivirus scan, say), so a refused read is tried again."""
    for _ in range(50):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except PermissionError:
            time.sleep(0.05)
    return json.loads(path.read_text(encoding="utf-8"))


def stand_in(_program: Path) -> list[str]:
    return [sys.executable, str(FAKE)]


@pytest.fixture
def fake(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    seen = tmp_path / "seen.json"
    monkeypatch.setattr(codex, "command", stand_in)
    monkeypatch.setenv("FAKE_CODEX_SEEN", str(seen))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    monkeypatch.setenv(codex.MODEL_URL_ENV, "http://gateway/model/chatgpt/v1")
    return seen


def of(events: list[dict[str, Any]], kind: str) -> list[dict[str, Any]]:
    return [e for e in events if e["type"] == kind]


def test_a_turn_reads_as_claude_codes_would(
    tmp_path: Path, fake: Path, events: Events, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_CODEX_SCRIPT", "answer")
    assert codex.run(config(tmp_path), Path("codex-app-server")) is True
    found = events()
    uses = [b for e in of(found, "sdk.assistant") for b in e["message"]["content"]]
    tools = [b for b in uses if b["_kind"] == "ToolUseBlock"]
    # A connection's tool has Claude Code's name for it, so the task's page and checks read it.
    assert [t["name"] for t in tools] == ["mcp__ringkeeper-shop__list_orders", "Write", "Bash"]
    assert tools[0]["input"] == {"status": "open"}
    results = [b for e in of(found, "sdk.user") for b in e["message"]["content"]]
    assert [r["tool_use_id"] for r in results] == [t["id"] for t in tools]
    assert results[2]["content"] == "1 passed"
    # What it changed, for the commit: its file, the one a tool saved for it, and a shell.
    assert EDITS.paths == {"report.md", "attachments/orders.csv"}
    assert EDITS.shell is True
    result = of(found, "sdk.result")[-1]["message"]
    assert result["subtype"] == "success"
    assert result["is_error"] is False
    assert result["result"] == "Two open orders."
    assert result["session_id"] == "codex:thr-1"  # what the next session resumes
    assert result["num_turns"] == 2
    assert result["total_cost_usd"] is None  # the plan isn't billed by the token
    assert result["usage"] == {"input_tokens": 55, "output_tokens": 5, "cache_read_input_tokens": 2}
    seen = read_seen(fake)
    started = seen["thread/start"]
    assert started["model"] == "gpt-5.5"
    assert started["approvalPolicy"] == "never"
    assert [t["name"] for t in started["dynamicTools"]] == ["ask_user", "not_done"]
    assert "running unattended inside Ringkeeper" in started["developerInstructions"]
    assert seen["turn"]["input"][0]["text"] == "List the open orders."
    # The gateway token reaches Codex's MCP client in its environment, never its arguments.
    assert seen["env_token"] == "gw-token-123"
    assert "gw-token-123" not in json.dumps(seen["argv"])


def test_a_question_ends_the_session_there(
    tmp_path: Path, fake: Path, events: Events, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_CODEX_SCRIPT", "ask")
    assert codex.run(config(tmp_path), Path("codex-app-server")) is True
    found = events()
    assert of(found, "ringkeeper.ask_user") == [
        {"type": "ringkeeper.ask_user", "question": "Test or production?"}
    ]
    seen = read_seen(fake)
    assert seen["tool_answer"]["contentItems"][0]["text"] == inbox.ASKED
    assert seen["interrupted"] is True  # as the hook makes Claude Code's end
    result = of(found, "sdk.result")[-1]["message"]
    assert result["session_id"] == "codex:thr-1"  # answering resumes it


def test_resuming_goes_on_in_the_same_thread(
    tmp_path: Path, fake: Path, events: Events, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_CODEX_SCRIPT", "answer")
    codex.run(config(tmp_path, resume="codex:thr-1"), Path("codex-app-server"))
    seen = read_seen(fake)
    assert seen["thread/resume"]["threadId"] == "thr-1"
    assert "thread/start" not in seen
    # A Claude session's id can't go on in Codex: a new thread.
    codex.run(config(tmp_path, resume="claude-session-1"), Path("codex-app-server"))
    assert "thread/start" in read_seen(fake)


def test_codex_stopping_ends_the_task_as_failed(
    tmp_path: Path, fake: Path, events: Events, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_CODEX_SCRIPT", "crash")
    assert codex.run(config(tmp_path), Path("codex-app-server")) is False
    result = of(events(), "sdk.result")[-1]["message"]
    assert result["errors"] == ["Codex stopped before the task finished."]
    assert result["session_id"] == "codex:thr-1"  # a follow-up can still go on there


def test_the_plans_limit_says_so(
    tmp_path: Path, fake: Path, events: Events, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_CODEX_SCRIPT", "limit")
    assert codex.run(config(tmp_path), Path("codex-app-server")) is False
    result = of(events(), "sdk.result")[-1]["message"]
    assert result["is_error"] is True
    assert result["engine"] == "codex"
    assert result["engine_error"] == "usageLimitExceeded"  # services.chatgpt.engine_failure


def test_not_done_and_ask_user_are_answered_as_for_claude(events: Events) -> None:
    run = codex.Run(model="gpt-5.5")
    noted = codex.answer_tool(
        run, {"tool": "not_done", "arguments": {"reason": "No access.", "needs": "records"}}
    )
    assert noted["contentItems"][0]["text"] == inbox.NOTED
    assert of(events(), "ringkeeper.not_done")[0]["needs"] == "records"
    assert codex.answer_tool(run, {"tool": "ask_user", "arguments": {}})["success"] is False
    codex.answer_tool(run, {"tool": "ask_user", "arguments": {"question": "Which one?"}})
    blocked = codex.answer_tool(run, {"tool": "not_done", "arguments": {"reason": "x"}})
    assert blocked["contentItems"][0]["text"] == inbox.BLOCKED


def test_autonomy_and_web_decide_codexs_tools(tmp_path: Path) -> None:
    strict = codex.overrides(config(tmp_path, disallowed_tools=[
        "Bash", "PowerShell", "WebFetch", "WebSearch", "Edit", "Write",
        "mcp__ringkeeper-memory__remember",
    ], mcp_servers={"ringkeeper-memory": "http://gateway/mcp/memory"}))  # fmt: skip
    assert strict["features.shell_tool"] == "false"
    assert strict["web_search"] == '"disabled"'
    assert strict["sandbox_mode"] == '"read-only"'
    assert strict["mcp_servers.ringkeeper-memory.disabled_tools"] == '["remember"]'
    assert strict["features.apps"] == "false"  # OpenAI's own connectors: never a client's data
    assert strict["features.multi_agent"] == "false"
    relaxed = codex.overrides(config(tmp_path))
    assert relaxed["features.shell_tool"] == "true"
    assert relaxed["web_search"] == '"live"'
    assert relaxed["sandbox_mode"] == '"danger-full-access"'
    assert relaxed["mcp_servers.ringkeeper-shop.bearer_token_env_var"] == '"RK_GATEWAY_TOKEN"'
    assert relaxed["model_providers.ringkeeper.env_key"] == '"RINGKEEPER_MODEL_TOKEN"'
    assert relaxed["model_providers.ringkeeper.requires_openai_auth"] == "false"
    assert relaxed["mcp_servers.ringkeeper-shop.required"] == "true"  # no task without its gateway


def test_the_connections_tools_are_sent_without_tool_search(tmp_path: Path) -> None:
    """Plan usage refuses tool_search, which Codex would otherwise use for MCP tools with the
    models it knows (checked against the real 0.171.0 with gpt-5.5): they go in directly."""
    found = codex.overrides(config(tmp_path))
    assert found["features.tool_search"] == "false"
    assert found["features.search_tool"] == "false"
    assert found["features.tool_search_always_defer_mcp_tools"] == "false"
    assert found["mcp_servers.ringkeeper-shop.omit_tools_from"] == '["deferred", "code_mode"]'
    assert found["features.code_mode"] == "false"


# --- Fetching the program ---


def _archive(tmp_path: Path, name: str, program: bytes) -> Path:
    path = tmp_path / name
    member = f"codex-app-server-{codex.target()}"
    if name.endswith(".zip"):
        with zipfile.ZipFile(path, "w") as zipped:
            zipped.writestr(member + ".exe", program)
    else:
        with tarfile.open(path, "w:gz") as tar:
            info = tarfile.TarInfo(member)
            info.size = len(program)
            tar.addfile(info, io.BytesIO(program))
    return path


@pytest.mark.parametrize("asset", ["codex.exe.zip", "codex.tar.gz"])
def test_the_pinned_program_is_fetched_once_and_checked(tmp_path: Path, asset: str) -> None:
    release = tmp_path / "release"
    release.mkdir()
    archive = _archive(release, asset, b"the program")
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    pin = tmp_path / "codex.json"
    target = {"asset": asset, "sha256": digest}
    pin.write_text(json.dumps({"version": "9.9.9", "release": release.as_uri(),
                               "targets": {codex.target(): target}}))  # fmt: skip
    said: list[str] = []
    program = codex.ensure(tmp_path / "cache", pin, said.append)
    assert program.read_bytes() == b"the program"
    assert program.parent.name == "9.9.9"
    assert "Getting Codex 9.9.9 ready" in said[0]
    assert codex.ensure(tmp_path / "cache", pin, said.append) == program
    assert len(said) == 1  # the next task reuses it
    target["sha256"] = "0" * 64
    pin.write_text(json.dumps({"version": "9.9.10", "release": release.as_uri(),
                               "targets": {codex.target(): target}}))  # fmt: skip
    with pytest.raises(codex.InstallError, match="didn't match"):
        codex.ensure(tmp_path / "cache", pin)
    assert not (tmp_path / "cache" / "9.9.10").exists()


def test_the_pin_covers_every_desktop_and_server() -> None:
    pinned = codex.pinned()
    assert set(pinned["targets"]) >= {
        "x86_64-pc-windows-msvc", "aarch64-apple-darwin", "x86_64-apple-darwin",
        "x86_64-unknown-linux-musl", "aarch64-unknown-linux-musl",
    }  # fmt: skip
    for found in pinned["targets"].values():
        assert len(found["sha256"]) == 64
        assert found["asset"].startswith("codex-app-server-")
