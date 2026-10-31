"""Check for updates, end to end, against the real releases repo (a clean Windows
runner only: it installs Ringkeeper for the current user and edits the hosts file).

    python shell/scripts/updater_check.py --installer SETUP.EXE \\
        --old-exe OLD.EXE --current-exe CURRENT.EXE

SETUP.EXE installs a build older than the latest release (its ringkeeper.exe is
OLD.EXE); CURRENT.EXE is the same shell built as the latest release's version.

1. As the latest version: Check for updates says "You're up to date"; offline
   (github.com blocked in the hosts file), it says it couldn't reach the server.
2. As the older version: the check at start finds the latest release and
   downloads it (signature checked), with its notes; Check for updates again
   says the same. Install and restart with a task waiting doesn't restart;
   once the task is stopped, the shell installs the update and the latest
   version starts with the same data.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx2

LATEST = "https://github.com/alexyushkin/ringkeeper-releases/releases/latest/download/latest.json"
LOCAL = Path(os.environ["LOCALAPPDATA"])
INSTALL = LOCAL / "Programs" / "Ringkeeper"
DATA = LOCAL / "Ringkeeper"
HOSTS = Path(os.environ["SYSTEMROOT"]) / "System32" / "drivers" / "etc" / "hosts"
BLOCKED = ("github.com", "objects.githubusercontent.com", "release-assets.githubusercontent.com")
MARK = "# ringkeeper-updater-check"


class CheckFailedError(Exception):
    pass


def expect(ok: object, detail: object) -> None:
    if not ok:
        raise CheckFailedError(str(detail))


def step(text: str) -> None:
    print(f"\n== {text}", flush=True)


def wait_for[T](what: str, check: Callable[[], T | None], seconds: float = 120) -> T:
    deadline = time.monotonic() + seconds
    last: Exception | None = None
    while time.monotonic() < deadline:
        try:
            if result := check():
                return result
        except (httpx2.HTTPError, OSError, ValueError, KeyError) as error:
            last = error
        time.sleep(1)
    raise CheckFailedError(f"timed out waiting for {what} ({last})")


class App:
    """The installed app, through the shell's door (the launch secret)."""

    def __init__(self) -> None:
        self.info: dict[str, Any] = {}

    def started(self, not_pid: int | None = None) -> dict[str, Any] | None:
        info: dict[str, Any] = json.loads((DATA / "desktop.json").read_text(encoding="utf-8"))
        if info["pid"] == not_pid:
            return None
        if httpx2.get(f"{info['url']}/desktop/health", timeout=5).status_code != 200:
            return None
        self.info = info
        return info

    def call(self, method: str, path: str, json_body: object = None) -> Any:
        response = httpx2.request(
            method, f"{self.info['url']}/desktop/api{path}", json=json_body, timeout=30,
            headers={"Authorization": f"Bearer {self.info['secret']}"},
        )  # fmt: skip
        response.raise_for_status()
        return response.json() if response.content else None

    def update(self) -> dict[str, Any]:
        return self.call("GET", "/desktop/update")

    def finished(self, result: str | None = None, *, after: str | None = None) -> Any:
        """The last check, once it has ended (with this result, after that time)."""
        last = self.update()["last"]
        if not last or last["at"] == after or (result and last["result"] != result):
            return None
        return last

    def check(self, seconds: float = 300) -> dict[str, Any]:
        """Check for updates, as the window's button does; returns how it ended."""
        before = self.update()["last"]
        asked = self.call("POST", "/desktop/update/check")
        expect(asked["checking"], asked)
        at = before["at"] if before else None
        return wait_for("the check to end", lambda: self.finished(after=at), seconds)

    def version(self) -> str:
        return self.call("GET", "/about")["version"]


def use_exe(exe: Path) -> None:
    """Puts this shell in the install folder, once the last one has exited (its
    backend leaves first, so the shell may still hold the file for a moment)."""

    def copied() -> bool:
        try:
            shutil.copy2(exe, INSTALL / "ringkeeper.exe")
        except PermissionError:
            return False
        return True

    wait_for("the shell to exit", copied, 60)


def launch() -> App:
    subprocess.Popen([str(INSTALL / "ringkeeper.exe"), "--hidden"])  # noqa: S603
    app = App()
    wait_for("the backend", app.started)
    return app


def quit_app() -> None:
    subprocess.run([str(INSTALL / "ringkeeper.exe"), "--quit"], check=False, timeout=120)  # noqa: S603
    wait_for("the app to quit", lambda: not (DATA / "desktop.json").exists(), 120)


def block_github(on: bool) -> None:
    lines = [line for line in HOSTS.read_text(encoding="utf-8").splitlines() if MARK not in line]
    if on:
        lines += [f"0.0.0.0 {host} {MARK}" for host in BLOCKED]
    HOSTS.write_text("\n".join(lines) + "\n", encoding="utf-8")
    subprocess.run(["ipconfig", "/flushdns"], check=True, capture_output=True)  # noqa: S607


def add_waiting_task(app: App) -> None:
    """A task that stays queued (everything paused): it counts as running work."""
    app.call("PATCH", "/settings", {"paused": True})
    project = {"name": "acme-web", "owner": "acme", "workspace_path": "acme-web"}
    app.call("POST", "/projects", project)
    code = (
        "from ringkeeper.config import get_settings\n"
        "from ringkeeper.db import session_scope\n"
        "from ringkeeper.services import tasks\n"
        "with session_scope() as session:\n"
        "    tasks.create_task(session, get_settings(), project='acme-web', prompt='write notes')\n"
    )
    env = {**os.environ, "RINGKEEPER_MODE": "desktop", "RINGKEEPER_DATA_DIR": str(DATA)}
    python = INSTALL / "python" / "python.exe"
    subprocess.run([str(python), "-I", "-c", code], check=True, env=env, timeout=120)  # noqa: S603


def install(installer: Path) -> None:
    step("install the older build")
    subprocess.run([str(installer), "/S"], check=True, timeout=600)  # noqa: S603
    if (DATA / "desktop.json").exists():  # the installer started it
        wait_for("the app", App().started)
        quit_app()


def up_to_date_then_offline(current_exe: Path, newest: str) -> None:
    step(f"as {newest}: up to date, then offline")
    use_exe(current_exe)
    app = launch()
    expect(app.update()["version"] == newest, app.update())
    last = app.check()
    print("check:", last)
    expect(last["result"] == "current", last)
    block_github(True)
    try:
        last = app.check()
    finally:
        block_github(False)
    print("check offline:", last)
    expect(last["result"] == "failed", last)
    expect("Couldn't reach the update server" in (last["error"] or ""), last)
    quit_app()


def found_and_installed(old_exe: Path, newest: str) -> None:
    step("as the older build: the update is found, downloaded, and offered")
    use_exe(old_exe)
    app = launch()
    old_pid, version = app.info["pid"], app.version()
    print(f"running {version}")
    # The last check (offline, above) shows until the one at start ends.
    print("check at start:", wait_for("the check at start", lambda: app.finished("ready"), 600))
    ready = app.update()["ready"]
    print("ready:", ready and ready["version"])
    expect(ready and ready["version"] == newest, ready)
    expect(ready and ready["notes"].strip(), "no release notes")
    last = app.check()
    print("check again:", last)
    expect(last["result"] == "ready", last)

    step("Install and restart waits for running work")
    add_waiting_task(app)
    asked = app.call("POST", "/desktop/update/restart")
    expect((asked["restart_requested"], asked["running"]) == (True, 1), asked)
    time.sleep(30)
    expect(app.started() and app.info["pid"] == old_pid, "it restarted mid-task")
    expect(app.version() == version, "it updated mid-task")
    print("still running the older build with a task waiting")

    step("the task stops: the update installs, and the new version starts")
    app.call("POST", "/stop-all")
    wait_for("the new version", lambda: app.started(not_pid=old_pid), 900)
    after = app.version()
    print(f"now running {after}")
    expect(after == newest, after)
    projects = [p["name"] for p in app.call("GET", "/projects")]
    expect("acme-web" in projects, projects)  # the same data
    quit_app()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--installer", type=Path, required=True)
    parser.add_argument("--old-exe", type=Path, required=True)
    parser.add_argument("--current-exe", type=Path, required=True)
    args = parser.parse_args()
    newest = httpx2.get(LATEST, follow_redirects=True, timeout=30).json()["version"]
    print(f"latest release: {newest}")
    install(args.installer)
    up_to_date_then_offline(args.current_exe, newest)
    found_and_installed(args.old_exe, newest)
    print("\nall checks passed")


if __name__ == "__main__":
    try:
        main()
    finally:
        log = DATA / "logs" / "shell.log"
        if log.exists():
            print("\n-- shell.log (last 40 lines)")
            print("\n".join(log.read_text(encoding="utf-8").splitlines()[-40:]))
        sys.stdout.flush()
