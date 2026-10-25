"""The desktop app shell (shell/, Tauri) and its backend, as the user runs them.

Needs a built shell: RINGKEEPER_SHELL_EXE=shell/target/debug/ringkeeper.exe
(a debug build runs the backend from this repository's .venv). Skipped
otherwise. Uses its own data folder and keychain entry, and `--hidden`, so no
window opens.

What M3 promises: the backend comes back when it dies, the shell finds its
backend again after the shell itself crashed, and Quit leaves nothing running.
"""

import contextlib
import ctypes
import json
import os
import subprocess
import sys
import time
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx2
import keyring
import pytest
from keyring.errors import PasswordDeleteError

from ringkeeper.desktop import server

SHELL = os.environ.get("RINGKEEPER_SHELL_EXE")

pytestmark = pytest.mark.skipif(not SHELL, reason="set RINGKEEPER_SHELL_EXE to a built shell")


def alive(pid: int) -> bool:
    if sys.platform == "win32":
        kernel32: Any = getattr(ctypes, "windll").kernel32  # noqa: B009
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        code = ctypes.c_ulong()
        kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        kernel32.CloseHandle(handle)
        return code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def kill(pid: int) -> None:
    """Hard, like a crash: no clean-up runs."""
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/PID", str(pid), "/F"], check=False, capture_output=True)  # noqa: S603, S607
    else:
        os.kill(pid, 9)


def wait_for(what: str, check: Any, seconds: float = 90) -> Any:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        with contextlib.suppress(Exception):
            if result := check():
                return result
        time.sleep(0.25)
    raise AssertionError(f"timed out waiting for {what}")


def answering(data_dir: Path, *, not_pid: int | None = None) -> dict[str, Any] | None:
    info = json.loads((data_dir / server.RUNTIME_FILE).read_text(encoding="utf-8"))
    if info["pid"] == not_pid:
        return None
    health = httpx2.get(f"{info['url']}/desktop/health", timeout=2)
    return info if health.status_code == 200 else None


@pytest.fixture
def app(tmp_path: Path) -> Iterator[dict[str, Any]]:
    service = f"Ringkeeper-test-{uuid.uuid4().hex[:8]}"
    env = {
        **os.environ,
        "RINGKEEPER_SHELL_DATA_DIR": str(tmp_path / "data"),
        "RINGKEEPER_KEYCHAIN_SERVICE": service,
        "RINGKEEPER_WORKSPACES_ROOT": str(tmp_path / "workspaces"),
    }
    started: list[subprocess.Popen[bytes]] = []

    def launch(*args: str) -> subprocess.Popen[bytes]:
        process = subprocess.Popen([str(SHELL), *args], env=env)  # noqa: S603
        started.append(process)
        return process

    yield {"data_dir": tmp_path / "data", "launch": launch}
    for process in started:
        if process.poll() is None:
            process.kill()
    with contextlib.suppress(Exception):
        info = json.loads((tmp_path / "data" / server.RUNTIME_FILE).read_text(encoding="utf-8"))
        if alive(info["pid"]):
            kill(info["pid"])
    with contextlib.suppress(PasswordDeleteError):
        keyring.delete_password(service, "master-key")


def test_the_backend_comes_back_and_quit_leaves_nothing_running(app: dict[str, Any]) -> None:
    data_dir: Path = app["data_dir"]
    shell = app["launch"]("--hidden")
    first = wait_for("the backend", lambda: answering(data_dir))

    # The backend dies: the shell starts a new one.
    kill(first["pid"])
    second = wait_for("a new backend", lambda: answering(data_dir, not_pid=first["pid"]))
    assert second["url"] != first["url"] or second["secret"] != first["secret"]

    # The shell dies: the backend keeps running, and the next launch uses it.
    kill(shell.pid)
    shell.wait(30)
    assert answering(data_dir) == second
    shell = app["launch"]("--hidden")
    time.sleep(5)
    assert answering(data_dir) == second  # found, not replaced
    assert (
        (data_dir / "logs" / "shell.log")
        .read_text(encoding="utf-8")
        .strip()
        .endswith(f"found the backend (pid {second['pid']})")
    )

    # Quit from a second launch: everything stops, cleanly.
    asked = app["launch"]("--quit")
    assert asked.wait(30) == 0
    assert shell.wait(90) == 0
    wait_for("the backend to exit", lambda: not alive(second["pid"]), seconds=30)
    assert not (data_dir / server.RUNTIME_FILE).exists()  # removed on a clean shutdown
    with server.single_instance(data_dir):  # and nothing holds the data folder
        pass
    log = (data_dir / "logs" / "shell.log").read_text(encoding="utf-8")
    assert "the backend stopped" in log
    assert "killing it" not in log
