"""MCP servers from Claude Desktop: run behind the gateway as local processes,
started with a clean environment, every call held unless marked read-only."""

import json
import sys
from pathlib import Path

import pytest

from ringkeeper import destructive
from ringkeeper.adapters import mcp
from ringkeeper.adapters.base import Target, ToolError, ToolKind
from ringkeeper.models import Env, InstanceType
from ringkeeper.vault import Secret

pytestmark = pytest.mark.anyio

SERVER = Path(__file__).parent / "fixtures" / "fake_mcp_server.py"
KEY = "fake-key-1234567890"
DSN = "postgresql://notes:hunter2-not-real@db.example/notes"


def spec(**extra: object) -> str:
    return json.dumps(
        {"command": sys.executable, "args": [str(SERVER), DSN], "env": {"FAKE_API_KEY": KEY}}
        | extra
    )


def target(read_only: list[str] | None = None) -> Target:
    config = {"read_only_tools": read_only or []}
    return Target("notes", Env.PROD, None, Secret("file://x/notes", spec()), config)


def test_secrets_are_found_and_masked() -> None:
    parsed = mcp.server_spec(spec())
    assert mcp.preview(parsed) == f"{sys.executable} {SERVER} ••••"
    assert set(mcp.secret_fragments(spec())) == {KEY, DSN}
    for arg in ("sk-ant-api03-abc", "--api-key=abc123", "ghp_0123456789", "postgres://a:b@h/d"):
        assert mcp.looks_secret(arg), arg
    for arg in ("-y", "@modelcontextprotocol/server-filesystem", "C:\\Users\\me\\Documents"):
        assert not mcp.looks_secret(arg), arg
    with pytest.raises(ToolError, match="neither a command nor a URL"):
        mcp.server_spec(json.dumps({"env": {}}))


def test_only_the_marked_tools_read() -> None:
    assert mcp.call_tool.classify is not None
    assert mcp.call_tool.classify(target(["read_notes"]), {"tool": "read_notes"}) is ToolKind.READ
    assert mcp.call_tool.classify(target(["read_notes"]), {"tool": "what_i_got"}) is ToolKind.WRITE
    assert destructive.reason(InstanceType.MCP, "call_tool", {"tool": "delete_note"})
    assert destructive.reason(InstanceType.MCP, "call_tool", {"tool": "read_notes"}) is None


async def test_the_server_runs_with_its_secrets_and_nothing_else(
    monkeypatch: pytest.MonkeyPatch, data_dir: Path
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api03-must-not-reach-it")
    monkeypatch.setenv("RINGKEEPER_SOMETHING", "nor-this")
    listed = await mcp.list_tools.handler(target(["read_notes"]))
    tools = {t["name"]: t for t in listed["tools"]}
    assert set(tools) == {"read_notes", "delete_note", "what_i_got"}
    assert tools["read_notes"]["read_only"] is True
    assert tools["delete_note"]["read_only"] is False

    got = await mcp.call_tool.handler(target(), tool="what_i_got")
    report = got["structured"]
    assert report["api_key_length"] == len(KEY)  # its env reached it
    assert report["first_arg_length"] == len(DSN)  # and its args
    assert report["claude_credential"] is False
    assert report["ringkeeper_env"] == []
    assert Path(report["cwd"]) == data_dir / "mcp" / "notes"

    with pytest.raises(ToolError, match=r"Unknown tool|not found|unknown"):
        await mcp.call_tool.handler(target(), tool="no_such_tool")
