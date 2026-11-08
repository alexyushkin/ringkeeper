"""A pass in the installed app: where Claude Desktop's extension starts Ringkeeper from, and
how it keeps working when Ringkeeper quits for an update and comes back.

The update replaces the install folder, and Windows can't replace files a program runs from,
so the bridge leaves when Ringkeeper does. Claude Desktop's relay (on Claude's own Node, from
Claude's extension folder) answers Claude meanwhile, and starts the bridge again once
Ringkeeper is back, from the same path.
"""

import io
import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
import zipfile
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from ringkeeper import __version__, bridge
from ringkeeper.config import get_settings
from ringkeeper.services import passes

NODE = shutil.which("node")  # Claude Desktop runs the relay on its own
BUNDLED = "python.exe" if sys.platform == "win32" else "bin/python3"  # shell/src/backend.rs


def manifest(blob: bytes) -> dict[str, Any]:
    with zipfile.ZipFile(io.BytesIO(blob)) as bundle:
        return json.loads(bundle.read("manifest.json"))


def test_the_installed_app_s_extension_starts_ringkeeper_from_its_install_folder(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    install = tmp_path / "Programs" / "Ringkeeper"  # %LOCALAPPDATA%\Programs\Ringkeeper
    python = install / "python" / BUNDLED
    python.parent.mkdir(parents=True)
    python.write_bytes(b"")
    # The installed backend runs this Python, as the app starts it.
    monkeypatch.setattr(sys, "prefix", str(install / "python"))
    monkeypatch.setattr(sys, "executable", str(python))

    started = manifest(passes.extension(get_settings(), "acme-desk", "Acme web"))["server"]
    assert started["type"] == "node"
    assert started["mcp_config"]["command"] == "node"  # Claude's own
    relay, program, *rest = started["mcp_config"]["args"]
    assert relay == "${__dirname}/server/relay.js"  # in Claude's extension folder
    assert Path(program) == python
    assert Path(program).is_absolute()
    assert Path(program).is_relative_to(install)
    for dev in (".venv", "worktree", "site-packages", __version__):
        assert dev not in program
    assert rest[:5] == ["-m", "ringkeeper", "mcp", "--pass", "acme-desk"]

    # After an update the new version is at the same path, so the saved extension still works:
    # a link to Python by its version (python3.12) would break when Python's version changes.
    monkeypatch.setattr(sys, "executable", str(python.parent / "python3.12"))
    again = manifest(passes.extension(get_settings(), "acme-desk", "Acme web"))["server"]
    assert Path(again["mcp_config"]["args"][1]) == python


def test_a_developer_s_copy_starts_its_own_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    venv = tmp_path / ".venv"
    venv.mkdir()
    own = venv / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    monkeypatch.setattr(sys, "prefix", str(venv))
    monkeypatch.setattr(sys, "executable", str(own))
    assert passes.python() == str(own)  # no Python at the top of a virtual environment


# --- the bridge leaves when Ringkeeper does ---


class Health(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        self.send_response(200 if self.path == "/health" else 404)
        self.end_headers()
        self.wfile.write(b'{"status": "ok"}')

    def log_message(self, format: str, *args: object) -> None:
        pass


@pytest.fixture
def gateway() -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), Health)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def running(data_dir: Path, gateway: str) -> None:
    (data_dir / "desktop.json").write_text(json.dumps({"gateway": gateway}), "utf-8")


def test_the_bridge_leaves_when_ringkeeper_quits_or_restarts(tmp_path: Path, gateway: str) -> None:
    running(tmp_path, gateway)
    assert bridge.still_running(tmp_path, gateway)
    assert not bridge.still_running(tmp_path, "http://127.0.0.1:1")  # restarted elsewhere

    left: list[int] = []
    quick = {"seconds": 0.02, "leave": left.append}
    watching = threading.Thread(target=bridge.watch, args=(tmp_path, gateway), kwargs=quick)
    watching.start()
    watching.join(0.3)
    assert watching.is_alive()  # Ringkeeper is still there
    (tmp_path / "desktop.json").unlink()  # Quit removes it, and an update quits first
    watching.join(5)
    assert left == [bridge.STOPPED]


# --- Claude Desktop's relay ---

FAKE_BRIDGE = """
import json, sys
from pathlib import Path
folder = Path(sys.argv[sys.argv.index("--data-dir") + 1])
starts = folder / "starts"
n = int(starts.read_text()) + 1 if starts.exists() else 1
starts.write_text(str(n))
for line in sys.stdin:
    m = json.loads(line)
    with (folder / f"seen-{n}").open("a") as seen:
        seen.write(m.get("method", "answer") + " ")
    if "id" not in m:
        continue
    if m["method"] == "initialize":
        result = {"protocolVersion": m["params"]["protocolVersion"],
                  "capabilities": {"tools": {"listChanged": True}},
                  "serverInfo": {"name": "fake", "version": str(n)}}
    elif m["method"] == "tools/list":
        result = {"tools": [{"name": f"tool_{n}", "inputSchema": {"type": "object"}}]}
    elif (folder / "quit").exists():
        sys.exit(75)  # as bridge.watch does once Ringkeeper has gone
    else:
        result = {"content": [{"type": "text", "text": f"from bridge {n}"}]}
    print(json.dumps({"jsonrpc": "2.0", "id": m["id"], "result": result}), flush=True)
"""


def seen(file: Path, count: int, wait: float = 10.0) -> list[str]:
    """What the fake bridge has written down, once it has `count` lines (or the wait is up)."""
    deadline = time.monotonic() + wait
    while True:
        found = file.read_text().split() if file.exists() else []
        if len(found) >= count or time.monotonic() > deadline:
            return found
        time.sleep(0.05)


class Claude:
    """Claude Desktop's side of the relay's stdio."""

    def __init__(self, relay: subprocess.Popen[str]) -> None:
        self.relay = relay
        self.said: queue.Queue[dict[str, Any]] = queue.Queue()
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self) -> None:
        assert self.relay.stdout is not None
        for line in self.relay.stdout:
            self.said.put(json.loads(line))

    def send(self, message: dict[str, Any]) -> None:
        assert self.relay.stdin is not None
        self.relay.stdin.write(json.dumps({"jsonrpc": "2.0", **message}) + "\n")
        self.relay.stdin.flush()

    def ask(self, id_: int, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self.send({"id": id_, "method": method, "params": params or {}})
        return self.next()

    def next(self) -> dict[str, Any]:
        return self.said.get(timeout=15)


@pytest.mark.skipif(NODE is None, reason="needs Node, as Claude Desktop has")
def test_claude_desktop_s_extension_keeps_working_across_an_update(
    tmp_path: Path, gateway: str
) -> None:
    fake = tmp_path / "fake_bridge.py"
    fake.write_text(FAKE_BRIDGE, "utf-8")
    relay_js = tmp_path / "relay.js"
    blob = passes.extension(get_settings(), "acme-desk", "Acme web")
    with zipfile.ZipFile(io.BytesIO(blob)) as bundle:
        relay_js.write_bytes(bundle.read("server/relay.js"))  # the one the extension carries
    data = tmp_path / "data"
    data.mkdir()
    running(data, gateway)
    relay = subprocess.Popen(  # noqa: S603 (this test's own files)
        [str(NODE), str(relay_js), sys.executable, str(fake), "--data-dir", str(data)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, encoding="utf-8",
        env={**os.environ, "RINGKEEPER_RELAY_WAIT_MS": "50"},
    )  # fmt: skip
    claude = Claude(relay)
    try:
        hello = claude.ask(1, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                             "clientInfo": {"name": "claude-ai"}})  # fmt: skip
        assert hello["result"]["serverInfo"]["version"] == "1"
        claude.send({"method": "notifications/initialized"})
        assert claude.ask(2, "tools/list")["result"]["tools"][0]["name"] == "tool_1"
        assert "from bridge 1" in json.dumps(claude.ask(3, "tools/call", {"name": "tool_1"}))

        # The update: Ringkeeper quits (removing desktop.json) and the bridge leaves.
        (data / "quit").touch()
        (data / "desktop.json").unlink()
        cut_off = claude.ask(4, "tools/call", {"name": "tool_1"})
        assert "isn't running right now" in cut_off["error"]["message"]
        # Meanwhile Claude still gets answers, and nothing runs from the install folder.
        assert claude.ask(5, "tools/list")["result"]["tools"][0]["name"] == "tool_1"
        assert "updating" in claude.ask(6, "tools/call", {"name": "tool_1"})["error"]["message"]
        assert claude.ask(7, "ping")["result"] == {}
        assert (data / "starts").read_text() == "1"

        # The new version is up: the relay starts the bridge again and repeats the handshake.
        (data / "quit").unlink()
        running(data, gateway)
        assert claude.next()["method"] == "notifications/tools/list_changed"
        assert (data / "starts").read_text() == "2"
        # The bridge writes down the replayed handshake as it reads it, just after the relay
        # tells Claude: wait for it rather than read the file at once.
        assert seen(data / "seen-2", 2) == ["initialize", "notifications/initialized"]
        assert "from bridge 2" in json.dumps(claude.ask(8, "tools/call", {"name": "tool_2"}))
        assert claude.ask(9, "tools/list")["result"]["tools"][0]["name"] == "tool_2"
    finally:
        assert relay.stdin is not None
        relay.stdin.close()  # Claude quits
        assert relay.wait(60) == 0


@pytest.mark.skipif(NODE is None, reason="needs Node, as Claude Desktop has")
def test_a_bridge_that_can_t_start_shows_its_error_in_claude(tmp_path: Path) -> None:
    fake = tmp_path / "fails.py"
    fake.write_text("import sys\nprint('Ringkeeper: no pass', file=sys.stderr)\nsys.exit(1)\n")
    relay_js = Path(passes.__file__).parents[1] / "desktop" / "relay.js"
    relay = subprocess.Popen(  # noqa: S603 (Claude keeps its end open)
        [str(NODE), str(relay_js), sys.executable, str(fake), "--data-dir", str(tmp_path)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )  # fmt: skip
    assert relay.wait(60) == 1  # Claude marks it failed, with the bridge's words in its log
    assert relay.stderr is not None
    assert "no pass" in relay.stderr.read()
    assert relay.stdin is not None
    relay.stdin.close()
