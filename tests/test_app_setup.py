"""A pass added to Claude Code and Cursor in one click, by their own settings files: only their
list of servers changes, a copy of the file is kept first, and a file that can't be read is
left alone. Every test runs in a home folder of its own; conftest stops the run if one reaches
the real files."""

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ringkeeper.api import passes_routes
from ringkeeper.config import Settings, get_settings
from ringkeeper.db import session_scope
from ringkeeper.services import app_setup, passes
from tests import test_web_api
from tests.helpers import make_project

setup_code, admin, owner = test_web_api.setup_code, test_web_api.admin, test_web_api.owner

ACME = "acme-web"
SERVER = "ringkeeper-acme-code"
STARTED: dict[str, Any] = {
    "command": "C:/Ringkeeper/python/python.exe",
    "args": ["-m", "ringkeeper", "mcp", "--pass", "acme-code"],
    "env": {},
}
# Cursor's entry: no type, and no env when there's nothing in it.
IN_CURSOR: dict[str, Any] = {"command": STARTED["command"], "args": STARTED["args"]}

# Claude Code's file as it writes it: everything it remembers, with a server of the user's own.
CLAUDE_CODE: dict[str, Any] = {
    "numStartups": 41,
    "installMethod": "native",
    "projects": {"C:/Users/demo/acme": {"allowedTools": [], "hasTrustDialogAccepted": True}},
    "mcpServers": {
        "notes": {"type": "stdio", "command": "npx", "args": ["-y", "notes-mcp"], "env": {}}
    },
    "oauthAccount": {"emailAddress": "owner@acme.example", "organizationRole": "admin"},
    "tipsHistory": {"new-user-warmup": 3},
}
CURSOR: dict[str, Any] = {
    "mcpServers": {"github": {"command": "npx", "args": ["-y", "github-mcp"]}}
}


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A home folder of its own: Claude Code and Cursor's settings are looked for here."""
    folder = tmp_path / "home"
    folder.mkdir()
    for name in ("HOME", "USERPROFILE"):
        monkeypatch.setenv(name, str(folder))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    assert Path.home() == folder
    return folder


def write(path: Path, data: object, indent: int | str | None = 2, eol: str = "\n") -> bytes:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, indent=indent).replace("\n", eol) + eol
    path.write_bytes(text.encode())
    return path.read_bytes()


def settings() -> Settings:
    return get_settings()


def claude_file(home: Path) -> Path:
    return home / ".claude.json"


def cursor_file(home: Path) -> Path:
    return home / ".cursor" / "mcp.json"


def test_adding_to_claude_code_changes_only_its_servers(home: Path) -> None:
    before = write(claude_file(home), CLAUDE_CODE)
    assert app_setup.add(settings(), "claude-code", SERVER, STARTED)
    after = json.loads(claude_file(home).read_text())
    assert list(after) == list(CLAUDE_CODE)  # every setting kept, in its place
    assert {k: v for k, v in after.items() if k != "mcpServers"} == {
        k: v for k, v in CLAUDE_CODE.items() if k != "mcpServers"
    }
    assert after["mcpServers"]["notes"] == CLAUDE_CODE["mcpServers"]["notes"]
    assert after["mcpServers"][SERVER] == {"type": "stdio", **STARTED}
    assert claude_file(home).read_text().startswith('{\n  "numStartups"')  # its own layout
    # A copy of the file as it was, encrypted: these files often hold keys.
    [kept] = app_setup.backup_folder(settings()).glob("claude-code-*.bak")
    assert app_setup.read_copy(settings(), "claude-code", kept) == before
    assert b"owner@acme.example" not in kept.read_bytes()


def test_adding_twice_changes_nothing_the_second_time(home: Path) -> None:
    write(claude_file(home), CLAUDE_CODE)
    assert app_setup.add(settings(), "claude-code", SERVER, STARTED)
    once = claude_file(home).read_bytes()
    assert not app_setup.add(settings(), "claude-code", SERVER, STARTED)
    assert claude_file(home).read_bytes() == once
    assert len(list(app_setup.backup_folder(settings()).glob("claude-code-*.bak"))) == 1


def test_an_older_entry_is_brought_up_to_date(home: Path) -> None:
    earlier: dict[str, Any] = {
        "type": "stdio",
        "command": "C:/old/python.exe",
        "args": [],
        "env": {},
    }
    old = {**CLAUDE_CODE, "mcpServers": {SERVER: earlier}}
    write(claude_file(home), old)
    assert app_setup.status("claude-code", SERVER, STARTED) | {} == {
        "app": "claude-code", "label": "Claude Code", "found": True, "added": True,
        "current": False, "problem": None,
    }  # fmt: skip
    assert app_setup.add(settings(), "claude-code", SERVER, STARTED)
    assert app_setup.status("claude-code", SERVER, STARTED)["current"]


def test_removing_takes_out_only_the_pass(home: Path) -> None:
    write(cursor_file(home), CURSOR)
    assert app_setup.add(settings(), "cursor", SERVER, STARTED)
    assert json.loads(cursor_file(home).read_text())["mcpServers"][SERVER] == IN_CURSOR
    assert app_setup.remove(settings(), "cursor", SERVER)
    assert json.loads(cursor_file(home).read_text()) == CURSOR
    assert not app_setup.remove(settings(), "cursor", SERVER)  # not there: nothing to do


def test_cursors_file_is_made_when_it_isnt_there_yet(home: Path) -> None:
    (home / ".cursor").mkdir()
    assert app_setup.add(settings(), "cursor", SERVER, STARTED)
    assert json.loads(cursor_file(home).read_text()) == {"mcpServers": {SERVER: IN_CURSOR}}
    # Nothing was there to keep a copy of.
    assert not list(app_setup.backup_folder(settings()).glob("cursor-*.bak"))


def test_claude_codes_file_is_never_made_up(home: Path) -> None:
    (home / ".claude").mkdir()  # signed in, but it hasn't written its settings yet
    with pytest.raises(app_setup.AppSetupError, match="Open Claude Code once"):
        app_setup.add(settings(), "claude-code", SERVER, STARTED)
    assert not claude_file(home).exists()


def test_an_app_that_isnt_here_is_said(home: Path) -> None:
    assert not app_setup.app("cursor").found()
    with pytest.raises(app_setup.AppSetupError, match="Cursor isn't set up on this computer"):
        app_setup.add(settings(), "cursor", SERVER, STARTED)
    assert app_setup.status("cursor", SERVER, STARTED)["found"] is False


@pytest.mark.parametrize(
    ("text", "said"),
    [
        ('{\n  // my servers\n  "mcpServers": {}\n}\n', "isn't valid JSON"),
        ('{"mcpServers": ', "isn't valid JSON"),
        ('["not", "settings"]', "isn't in the shape expected"),
        ('{"mcpServers": ["a"]}', "its list of servers isn't in the shape expected"),
    ],
)
def test_a_file_that_cant_be_read_is_left_alone(home: Path, text: str, said: str) -> None:
    cursor_file(home).parent.mkdir()
    cursor_file(home).write_text(text)
    with pytest.raises(app_setup.AppSetupError, match=said):
        app_setup.add(settings(), "cursor", SERVER, STARTED)
    assert cursor_file(home).read_text() == text
    shown = app_setup.status("cursor", SERVER, STARTED)
    assert shown["added"] is False
    assert said in shown["problem"]


def test_windows_line_endings_and_four_spaces_stay(home: Path) -> None:
    write(cursor_file(home), CURSOR, indent=4, eol="\r\n")
    assert app_setup.add(settings(), "cursor", SERVER, STARTED)
    raw = cursor_file(home).read_bytes()
    assert b'\r\n    "mcpServers"' in raw
    assert raw.endswith(b"\r\n")
    assert b"\n" not in raw.replace(b"\r\n", b"")


def test_a_file_the_app_changes_meanwhile_is_read_again(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write(claude_file(home), CLAUDE_CODE)
    rendered = app_setup.render
    calls: list[int] = []

    def app_writes_too(root: dict[str, Any], raw: bytes) -> bytes:
        if not calls:  # Claude Code saves its own change while Ringkeeper works
            write(claude_file(home), {**CLAUDE_CODE, "numStartups": 42})
        calls.append(1)
        return rendered(root, raw)

    monkeypatch.setattr(app_setup, "render", app_writes_too)
    assert app_setup.add(settings(), "claude-code", SERVER, STARTED)
    after = json.loads(claude_file(home).read_text())
    assert after["numStartups"] == 42  # its change kept
    assert SERVER in after["mcpServers"]
    assert len(calls) == 2


def test_claude_code_follows_its_own_config_folder(
    home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    elsewhere = tmp_path / "claude-config"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(elsewhere))
    write(elsewhere / ".claude.json", CLAUDE_CODE)
    assert app_setup.add(settings(), "claude-code", SERVER, STARTED)
    assert SERVER in json.loads((elsewhere / ".claude.json").read_text())["mcpServers"]
    assert not claude_file(home).exists()


def test_only_the_newest_copies_are_kept(home: Path) -> None:
    for n in range(app_setup.KEEP + 3):
        app_setup.keep_copy(settings(), "cursor", f"version {n}".encode())
    kept = sorted(app_setup.backup_folder(settings()).glob("cursor-*.bak"))
    assert len(kept) == app_setup.KEEP
    newest = f"version {app_setup.KEEP + 2}".encode()
    assert app_setup.read_copy(settings(), "cursor", kept[-1]) == newest


# --- In the app: Settings > Passes ---


@pytest.fixture
def desktop(monkeypatch: pytest.MonkeyPatch) -> Settings:
    found = get_settings().model_copy(update={"mode": "desktop"})
    monkeypatch.setattr(passes_routes, "get_settings", lambda: found)
    return found


def made(owner: TestClient, name: str = "acme-code") -> None:
    with session_scope() as session:
        make_project(session, ACME)
    assert owner.post("/passes", json={"name": name, "project": ACME}).status_code == 201


def test_a_pass_is_added_to_and_taken_out_of_an_app_in_one_click(
    owner: TestClient, home: Path, desktop: Settings
) -> None:
    write(claude_file(home), CLAUDE_CODE)
    made(owner)
    [shown] = owner.get("/passes").json()["passes"]
    apps = {a["app"]: a for a in shown["apps"]}
    assert apps["claude-code"] == {
        "app": "claude-code", "label": "Claude Code", "found": True, "added": False,
        "current": False, "problem": None,
    }  # fmt: skip
    assert apps["cursor"]["found"] is False

    added = owner.post("/passes/acme-code/apps/claude-code")
    assert added.status_code == 200, added.text
    assert added.json()["status"]["added"] is True
    assert "Start a new Claude Code session" in added.json()["said"]
    entry = json.loads(claude_file(home).read_text())["mcpServers"]["ringkeeper-acme-code"]
    assert entry["args"][:5] == ["-m", "ringkeeper", "mcp", "--pass", "acme-code"]
    assert entry["command"] == passes.python()
    token = passes.PREFIX
    assert token not in claude_file(home).read_text()  # the bridge reads it from the vault

    taken = owner.delete("/passes/acme-code/apps/claude-code")
    assert taken.status_code == 200, taken.text
    assert "ringkeeper-acme-code" not in json.loads(claude_file(home).read_text())["mcpServers"]
    audit = test_web_api.audit_text()
    assert "added the pass acme-code to Claude Code" in audit
    assert "took the pass acme-code out of Claude Code" in audit


def test_a_file_left_alone_says_why_in_the_app(
    owner: TestClient, home: Path, desktop: Settings
) -> None:
    cursor_file(home).parent.mkdir()
    cursor_file(home).write_text("{ // mine\n}")
    made(owner)
    refused = owner.post("/passes/acme-code/apps/cursor")
    assert refused.status_code == 409
    assert "isn't valid JSON" in refused.json()["detail"]
    assert cursor_file(home).read_text() == "{ // mine\n}"


def test_removing_a_pass_takes_it_out_of_the_apps_too(
    owner: TestClient, home: Path, desktop: Settings
) -> None:
    write(claude_file(home), CLAUDE_CODE)
    write(cursor_file(home), CURSOR)
    made(owner)
    for key in ("claude-code", "cursor"):
        assert owner.post(f"/passes/acme-code/apps/{key}").status_code == 200
    assert owner.post("/passes/acme-code/revoke").status_code == 200
    # Revoked: it can't be added again, and it can still be taken out.
    assert owner.post("/passes/acme-code/apps/cursor").status_code == 409
    gone = owner.delete("/passes/acme-code")
    assert gone.status_code == 200, gone.text
    assert gone.json() == {"removed_from": ["Claude Code", "Cursor"], "left_in": []}
    assert json.loads(cursor_file(home).read_text()) == CURSOR
    assert json.loads(claude_file(home).read_text())["mcpServers"] == CLAUDE_CODE["mcpServers"]


def test_claude_desktop_opens_its_extension(
    owner: TestClient, home: Path, desktop: Settings, tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # fmt: skip
    opened: list[Path] = []
    monkeypatch.setattr(passes_routes, "downloads", lambda: tmp_path)
    monkeypatch.setattr(passes_routes, "open_file", opened.append)
    made(owner, "acme-desk")
    done = owner.post("/passes/acme-desk/apps/claude-desktop")
    assert done.status_code == 200, done.text
    assert opened == [tmp_path / "Ringkeeper-acme-desk.mcpb"]
    assert "asking whether to install it" in done.json()["said"]
    # Claude Desktop's own extensions page takes it out.
    assert owner.delete("/passes/acme-desk/apps/claude-desktop").status_code == 409


def test_a_server_install_doesnt_offer_it(owner: TestClient, home: Path) -> None:
    write(claude_file(home), CLAUDE_CODE)
    made(owner)
    [shown] = owner.get("/passes").json()["passes"]
    assert shown["apps"] == []
    assert owner.post("/passes/acme-code/apps/claude-code").status_code == 409
    assert "ringkeeper-acme-code" not in claude_file(home).read_text()
