"""Other AI apps' connections, read to import like Claude Desktop's (services.ai_apps). Each
test's home folder is an empty one (conftest.other_ai_apps), never this computer's."""

import json
import sys
from pathlib import Path

import pytest

from ringkeeper.services import ai_apps, claude_desktop


@pytest.fixture(autouse=True)
def no_claude_desktop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Claude Desktop's own settings are another test's: none here."""
    monkeypatch.setattr(claude_desktop, "config_paths", lambda: [tmp_path / "no-claude.json"])


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def found_in(app: str) -> dict[str, claude_desktop.Server]:
    return {s.name: s for s in claude_desktop.everything() if s.app == app}


def test_nothing_is_listed_when_no_ai_app_is_here() -> None:
    assert claude_desktop.everything() == []
    assert claude_desktop.apps_seen() == []


def test_cursor_s_file_is_read_with_its_comments_and_trailing_commas(other_ai_apps: Path) -> None:
    write(
        other_ai_apps / ".cursor" / "mcp.json",
        """{
          // the team's tools
          "mcpServers": {
            "notes": {"command": PYTHON, "args": ["--url", "https://a.example/x//y"],},
            /* a hosted one */
            "tickets": {"url": "https://tickets.example/mcp", "headers": {"X-Key": "k-123"}},
          },
        }""".replace("PYTHON", json.dumps(sys.executable)),
    )
    servers = found_in("cursor")
    assert servers["notes"].key == "cursor:notes"
    assert servers["notes"].app_label == "Cursor"
    assert servers["notes"].problem is None
    assert servers["notes"].spec["args"] == ["--url", "https://a.example/x//y"]  # not a comment
    assert servers["tickets"].transport == "url"
    assert servers["tickets"].secrets() == 1
    seen = {a["id"]: a for a in claude_desktop.apps_seen()}
    assert seen["cursor"] == {"id": "cursor", "label": "Cursor", "count": 2, "problems": []}


def test_each_app_s_own_shape_is_read(other_ai_apps: Path) -> None:
    python = sys.executable
    write(
        other_ai_apps / ".codeium" / "windsurf" / "mcp_config.json",
        json.dumps({"mcpServers": {"docs": {"serverUrl": "https://docs.example/mcp"}}}),
    )
    write(
        other_ai_apps / ".gemini" / "settings.json",
        json.dumps(
            {"theme": "dark", "mcpServers": {"search": {"httpUrl": "https://s.example/mcp"}}}
        ),
    )
    zed = ("AppData/Roaming/Zed" if sys.platform == "win32" else ".config/zed") + "/settings.json"
    write(
        other_ai_apps / zed,
        json.dumps({"context_servers": {"files": {"command": {"path": python, "args": ["-V"]}}}}),
    )
    write(
        other_ai_apps / ".continue" / "config.yaml",
        "name: mine\nmcpServers:\n  - name: sheets\n"
        f"    command: {json.dumps(python)}\n    args: [-V]\n",
    )
    write(
        other_ai_apps / ".codex" / "config.toml",
        f'model = "x"\n[mcp_servers.calendar]\ncommand = {json.dumps(python)}\nargs = ["-V"]\n',
    )
    by_key = {s.key: s for s in claude_desktop.everything()}
    assert by_key["windsurf:docs"].spec["url"] == "https://docs.example/mcp"
    assert by_key["gemini-cli:search"].spec["url"] == "https://s.example/mcp"
    assert by_key["zed:files"].spec == {"command": python, "args": ["-V"], "env": {}}
    assert by_key["continue:sheets"].spec["command"] == python
    assert by_key["codex:calendar"].spec["args"] == ["-V"]
    assert {k: s.problem for k, s in by_key.items() if s.problem} == {}


def test_a_variable_is_filled_from_this_computer_or_named(
    other_ai_apps: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NOTES_TOKEN", "t-set-here")
    monkeypatch.delenv("TICKETS_TOKEN", raising=False)
    write(
        other_ai_apps / ".cursor" / "mcp.json",
        json.dumps(
            {
                "mcpServers": {
                    "notes": {
                        "url": "https://n.example/mcp",
                        "headers": {"Auth": "${env:NOTES_TOKEN}"},
                    },
                    "tickets": {
                        "url": "https://t.example/mcp",
                        "headers": {"Auth": "${TICKETS_TOKEN}"},
                    },
                }
            }
        ),
    )
    servers = found_in("cursor")
    assert servers["notes"].spec["headers"] == {"Auth": "t-set-here"}
    assert servers["notes"].problem is None
    assert servers["tickets"].problem == "uses TICKETS_TOKEN, which isn't set on this computer"


def test_a_ringkeeper_pass_an_app_was_given_isn_t_offered_back(other_ai_apps: Path) -> None:
    write(
        other_ai_apps / ".claude.json",
        json.dumps(
            {
                "mcpServers": {
                    "ringkeeper-acme-code": {
                        "type": "stdio",
                        "command": sys.executable,
                        "args": ["-m", "ringkeeper", "mcp", "--pass", "acme-code"],
                    },
                    "github": {"type": "http", "url": "https://gh.example/mcp"},
                }
            }
        ),
    )
    assert set(found_in("claude-code")) == {"github"}


def test_a_file_that_can_t_be_read_is_named_and_the_rest_still_listed(other_ai_apps: Path) -> None:
    write(other_ai_apps / ".kiro" / "settings" / "mcp.json", "{ not json")
    write(
        other_ai_apps / ".cursor" / "mcp.json",
        json.dumps({"mcpServers": {"docs": {"url": "https://docs.example/mcp"}}}),
    )
    seen = {a["id"]: a for a in claude_desktop.apps_seen()}
    assert seen["kiro"]["count"] == 0
    [problem] = seen["kiro"]["problems"]
    assert problem.startswith("Couldn't read Kiro's settings (")
    assert problem.endswith(": it isn't valid.")
    assert set(found_in("cursor")) == {"docs"}


def test_jsonc_leaves_slashes_inside_strings_alone() -> None:
    text = '{"a": "http://x//y", /* c */ "b": [1, 2,], // end\n}'
    assert ai_apps.jsonc(text) == {"a": "http://x//y", "b": [1, 2]}
