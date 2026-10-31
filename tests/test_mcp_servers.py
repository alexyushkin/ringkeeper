"""MCP servers from Claude Desktop: run behind the gateway as local processes,
started with a clean environment; each tool held, run or confirmed by the user's
choice, else the server's annotations, else its name."""

import json
import sys
from pathlib import Path
from typing import Any

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


def target(config: dict[str, object] | None = None) -> Target:
    return Target("notes", Env.PROD, None, Secret("file://x/notes", spec()), config or {})


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


HINTS = {
    "read_notes": {"read_only": True, "destructive": None},
    "delete_note": {"read_only": False, "destructive": True},
    "remove_tag": {"read_only": False, "destructive": False},
}


@pytest.mark.parametrize(
    ("tool", "level", "source"),
    [
        ("read_notes", mcp.READ, mcp.BY_ANNOTATIONS),
        ("delete_note", mcp.CONFIRM, mcp.BY_ANNOTATIONS),
        ("remove_tag", mcp.APPROVE, mcp.BY_ANNOTATIONS),  # its hints beat its name
        ("purge_cache", mcp.CONFIRM, mcp.BY_NAME),  # no hints: its name decides
        ("what_i_got", mcp.APPROVE, mcp.BY_DEFAULT),
    ],
)
def test_defaults_come_from_annotations_then_names(tool: str, level: str, source: str) -> None:
    assert mcp.rule({"annotations": HINTS}, tool) == mcp.Rule(level, source)


def test_the_users_choice_wins_and_decides_holds_and_typed_names() -> None:
    config: dict[str, object] = {
        "annotations": HINTS,
        "tool_rules": {"read_notes": "approve", "delete_note": "approve", "what_i_got": "confirm"},
    }
    assert mcp.rule(config, "read_notes") == mcp.Rule(mcp.APPROVE, mcp.BY_USER)
    assert mcp.rule({"read_only_tools": ["what_i_got"]}, "what_i_got").level == mcp.READ

    def kind(tool: str, config: dict[str, object]) -> ToolKind:
        assert mcp.call_tool.classify is not None
        return mcp.call_tool.classify(target(config), {"tool": tool})

    def typed(tool: str, config: dict[str, object] | None) -> str | None:
        return destructive.reason(InstanceType.MCP, "call_tool", {"tool": tool}, config)

    defaults: dict[str, object] = {"annotations": HINTS}
    assert kind("read_notes", defaults) is ToolKind.READ
    assert kind("remove_tag", defaults) is ToolKind.WRITE
    assert kind("read_notes", config) is ToolKind.WRITE
    assert typed("delete_note", defaults) == (
        "runs 'delete_note', which its server marks as destructive"
    )
    assert typed("purge_cache", defaults) == "runs 'purge_cache', which sounds like it deletes"
    assert typed("remove_tag", defaults) is None
    assert typed("delete_note", config) is None
    assert typed("what_i_got", config) == (
        "runs 'what_i_got', which you set to need the connection's name"
    )
    assert typed("delete_note", None) == "runs 'delete_note', which sounds like it deletes"


@pytest.mark.parametrize(
    ("endpoint", "level"),
    [
        ("list_agents", mcp.READ), ("retrieve_call", mcp.READ), ("getAgent", mcp.READ),
        ("create_agent", mcp.APPROVE), ("update_agent", mcp.APPROVE),
        ("create_phone_call", mcp.APPROVE), ("publish_agent", mcp.APPROVE),
        ("delete_call", mcp.CONFIRM), ("", mcp.CONFIRM),
    ],
)  # fmt: skip
def test_retell_decides_each_call_by_its_endpoint(endpoint: str, level: str) -> None:
    """Retell's server calls any API endpoint through one tool: reads run, creating or
    updating agents and placing calls wait, deleting (or not saying) needs the name typed."""
    config: dict[str, object] = {"remote": "retell"}
    arguments: dict[str, Any] = {"endpoint_name": endpoint, "args": {}}
    args: dict[str, Any] = {"tool": "invoke_api_endpoint", "arguments": arguments}
    assert mcp.call_tool.classify is not None
    kind = mcp.call_tool.classify(target(config), args)
    assert kind is (ToolKind.READ if level == mcp.READ else ToolKind.WRITE)
    typed = destructive.reason(InstanceType.MCP, "call_tool", args, config)
    assert (typed is not None) == (level == mcp.CONFIRM)
    assert mcp.rule(config, "list_api_endpoints") == mcp.Rule(mcp.READ, mcp.BY_SERVICE)
    # The owner's own rule for the tool still wins.
    mine = {**config, "tool_rules": {"invoke_api_endpoint": "approve"}}
    assert mcp.call_rule(mine, "invoke_api_endpoint", arguments).level == mcp.APPROVE


async def test_the_server_runs_with_its_secrets_and_nothing_else(
    monkeypatch: pytest.MonkeyPatch, data_dir: Path
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api03-must-not-reach-it")
    monkeypatch.setenv("RINGKEEPER_SOMETHING", "nor-this")
    listed = await mcp.list_tools.handler(target({"tool_rules": {"what_i_got": "read"}}))
    tools = {t["name"]: t for t in listed["tools"]}
    assert set(tools) == {"read_notes", "delete_note", "remove_tag", "what_i_got"}
    assert mcp.annotations_of(listed)["read_notes"] == HINTS["read_notes"]
    assert mcp.annotations_of(listed)["what_i_got"] == {"read_only": None, "destructive": None}
    read_notes = tools["read_notes"]
    assert (read_notes["rule"], read_notes["rule_from"]) == ("read", "annotations")
    assert tools["read_notes"]["runs"] == "without asking"
    assert (tools["delete_note"]["rule"], tools["delete_note"]["read_only"]) == ("confirm", False)
    assert (tools["what_i_got"]["rule"], tools["what_i_got"]["rule_from"]) == ("read", "you")
    assert (tools["what_i_got"]["default"], tools["what_i_got"]["default_from"]) == (
        "approve",
        "default",
    )

    got = await mcp.call_tool.handler(target(), tool="what_i_got")
    report = got["structured"]
    assert report["api_key_length"] == len(KEY)  # its env reached it
    assert report["first_arg_length"] == len(DSN)  # and its args
    assert report["claude_credential"] is False
    assert report["ringkeeper_env"] == []
    assert Path(report["cwd"]) == data_dir / "mcp" / "notes"

    with pytest.raises(ToolError, match=r"Unknown tool|not found|unknown"):
        await mcp.call_tool.handler(target(), tool="no_such_tool")
