"""Back up, wipe, restore: end to end in the installed app (a clean Windows runner only:
it installs Ringkeeper for the current user, deletes its data and its keychain entry).

    python shell/scripts/restore_check.py --installer SETUP.EXE --scratch DIR

1. Install, and make some data through the app: the demo client (a project, a
   connection with a key, its files), another client's project, and an agent.
2. Settings > Backup's export: one password-protected file, in DIR.
3. Wipe it all, as on a new computer: uninstall, delete the data folder and the
   master key in Windows Credential Manager, install again.
4. Restore through the app: the preview lists what comes in; restoring makes the
   backend stop by itself, and the shell starts it again (no one restarts it here).
5. The restarted app has the projects, the connection, the agent, and the
   connection's key in this computer's (new) vault; the replaced database is kept.
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

LOCAL = Path(os.environ["LOCALAPPDATA"])
INSTALL = LOCAL / "Programs" / "Ringkeeper"
DATA = LOCAL / "Ringkeeper"
PASSWORD = "restore-check-password"  # noqa: S105 (a test value for a throwaway install)
CONNECTION_REF = "file://ringkeeper/connections/demo-shop-db"


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

    def call(self, method: str, path: str, json_body: object = None, **extra: Any) -> Any:
        headers = {"Authorization": f"Bearer {self.info['secret']}", **extra.pop("headers", {})}
        response = httpx2.request(
            method, f"{self.info['url']}/desktop/api{path}", json=json_body, timeout=120,
            headers=headers, **extra,
        )  # fmt: skip
        if response.status_code >= 400:
            raise CheckFailedError(f"{method} {path}: {response.status_code} {response.text}")
        return response.json() if response.content else None


def launch() -> App:
    subprocess.Popen([str(INSTALL / "ringkeeper.exe"), "--hidden"])  # noqa: S603
    app = App()
    wait_for("the backend", app.started)
    return app


def quit_app() -> None:
    subprocess.run([str(INSTALL / "ringkeeper.exe"), "--quit"], check=False, timeout=120)  # noqa: S603
    wait_for("the app to quit", lambda: not (DATA / "desktop.json").exists(), 120)


def install(installer: Path) -> App:
    subprocess.run([str(installer), "/S"], check=True, timeout=600)  # noqa: S603
    if (DATA / "desktop.json").exists():  # the installer started it
        app = App()
        wait_for("the app", app.started)
        return app
    return launch()


def in_app_python(code: str) -> str:
    """Run Python with the app's own interpreter and data folder (as the app sees it)."""
    env = {**os.environ, "RINGKEEPER_MODE": "desktop", "RINGKEEPER_DATA_DIR": str(DATA)}
    python = INSTALL / "python" / "python.exe"
    done = subprocess.run(  # noqa: S603
        [str(python), "-I", "-c", code], check=True, env=env, timeout=120,
        capture_output=True, text=True,
    )  # fmt: skip
    return done.stdout.strip()


def make_data(app: App) -> None:
    step("install, and make some data through the app")
    app.call("POST", "/setup/done")
    demo = app.call("POST", "/demo")
    print("demo:", demo["client"], demo["project"], demo["connection"])
    app.call("POST", "/projects", {"name": "acme-web", "owner": "Acme Inc"})
    app.call(
        "POST", "/projects/demo-shop/agents",
        {"name": "order-follow-up", "instructions": "Draft notes for late orders.",
         "connections": ["demo-shop-db"]},
    )  # fmt: skip
    projects = sorted(p["name"] for p in app.call("GET", "/projects"))
    expect(projects == ["acme-web", "demo-shop"], projects)


def back_up(app: App, folder: Path) -> Path:
    step("back up")
    body = {"password": PASSWORD, "folder": str(folder)}
    saved = app.call("POST", "/desktop/backup/export", body)
    path = Path(saved["path"])
    print("backup:", path, path.stat().st_size, "bytes")
    expect(path.is_file() and path.suffix == ".rkbackup", saved)
    return path


def wipe(installer: Path) -> App:
    step("wipe it, as on a new computer: uninstall, delete the data and the master key")
    quit_app()
    # The master key first, with the app's own Python (gone once uninstalled).
    out = in_app_python(
        "import keyring\n"
        "keyring.delete_password('Ringkeeper', 'master-key')\n"
        "print(keyring.get_password('Ringkeeper', 'master-key'))\n"
    )
    expect(out == "None", f"the master key is still there: {out}")
    subprocess.run([str(INSTALL / "uninstall.exe"), "/S"], check=True, timeout=600)  # noqa: S603
    wait_for("the uninstall", lambda: not (INSTALL / "ringkeeper.exe").exists(), 120)
    shutil.rmtree(DATA, ignore_errors=True)
    expect(not DATA.exists(), f"{DATA} is still there")
    step("install again")
    app = install(installer)
    expect(app.call("GET", "/projects") == [], "a fresh install should have no projects")
    return app


def restore(app: App, backup: Path) -> App:
    step("restore: the preview, then restore; the backend restarts by itself")
    blob = backup.read_bytes()
    header = {"x-backup-password": PASSWORD, "Content-Type": "application/octet-stream"}
    try:
        app.call("POST", "/desktop/backup/preview", content=blob,
                 headers={**header, "x-backup-password": "not the password"})  # fmt: skip
        raise CheckFailedError("a wrong password opened the backup")
    except CheckFailedError as error:
        expect("doesn't open this backup" in str(error), error)
    preview = app.call("POST", "/desktop/backup/preview", content=blob, headers=header)
    names = sorted(p["name"] for p in preview["projects"])
    print("preview:", names, preview["secrets"], "keys")
    expect(names == ["acme-web", "demo-shop"], preview)
    old_pid = app.info["pid"]
    done = app.call("POST", "/desktop/backup/restore", content=blob, headers=header)
    expect(done["restarting"] is True, done)
    wait_for("the backend to come back", lambda: app.started(not_pid=old_pid), 180)
    print("restarted: pid", old_pid, "->", app.info["pid"])
    return app


def check_restored(app: App) -> None:
    step("the restarted app has everything")
    projects = sorted(p["name"] for p in app.call("GET", "/projects"))
    expect(projects == ["acme-web", "demo-shop"], projects)
    connections = [c["name"] for c in app.call("GET", "/connections")]
    expect("demo-shop-db" in connections, connections)
    agents = [a["name"] for a in app.call("GET", "/agents")]
    expect(agents == ["order-follow-up"], agents)
    key = in_app_python(
        "from ringkeeper.vault import get_vault\n"
        f"print(get_vault().resolve({CONNECTION_REF!r}).reveal().startswith('sqlite:///'))\n"
    )
    expect(key == "True", f"the connection's key didn't come back: {key}")
    expect((DATA / "ringkeeper.db.before-restore").exists(), "the replaced database isn't kept")
    expect(not (DATA / "restore-pending.db").exists(), "the staged database is still there")
    restored = (DATA / "logs" / "backend.log").read_text(encoding="utf-8", errors="replace")
    expect("restored a backup" in restored, "the backend log doesn't mention the restore")
    print("projects, connection, its key, agent: all back")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--installer", type=Path, required=True)
    parser.add_argument("--scratch", type=Path, required=True)
    args = parser.parse_args()
    args.scratch.mkdir(parents=True, exist_ok=True)
    step("install")
    app = install(args.installer)
    make_data(app)
    backup = back_up(app, args.scratch / "backups")
    app = wipe(args.installer)
    app = restore(app, backup)
    check_restored(app)
    quit_app()
    print("\nall checks passed")


if __name__ == "__main__":
    try:
        main()
    finally:
        for name in ("shell.log", "backend.log"):
            log = DATA / "logs" / name
            if log.exists():
                print(f"\n-- {name} (last 40 lines)")
                print(
                    "\n".join(log.read_text(encoding="utf-8", errors="replace").splitlines()[-40:])
                )
        sys.stdout.flush()
