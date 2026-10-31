"""The installer's "still running" check, on a clean Windows runner only (it
installs Ringkeeper for the current user):

    python shell/scripts/installer_check.py --installer SETUP.EXE

1. A ringkeeper.exe running from another folder (a developer's .venv, say)
   doesn't stop an install over the existing one.
2. A program running from the install folder does: after the minute Quit gets,
   the message says what was found ("Ringkeeper is running from <path>"), and
   once it's gone, Retry finishes the install.
3. With nothing running from the install folder but its Python folder in use
   (a program whose current folder it is), the message says that instead.
"""

import argparse
import ctypes
import os
import shutil
import subprocess
import sys
import time
from ctypes import wintypes
from pathlib import Path

LOCAL = Path(os.environ["LOCALAPPDATA"])
INSTALL = LOCAL / "Programs" / "Ringkeeper"
DATA = LOCAL / "Ringkeeper"
PING = Path(os.environ["SYSTEMROOT"]) / "System32" / "PING.EXE"  # stays running, does nothing
WAIT = ["-n", "3600", "127.0.0.1"]
IDRETRY, WM_COMMAND = 4, 0x0111

user32 = ctypes.WinDLL("user32", use_last_error=True)
WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


class CheckFailedError(Exception):
    pass


def expect(ok: object, detail: object) -> None:
    if not ok:
        raise CheckFailedError(str(detail))


def step(text: str) -> None:
    print(f"\n== {text}", flush=True)


def window_text(hwnd: int) -> str:
    buffer = ctypes.create_unicode_buffer(1024)
    user32.GetWindowTextW(hwnd, buffer, len(buffer))
    return buffer.value


def class_name(hwnd: int) -> str:
    buffer = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buffer, len(buffer))
    return buffer.value


def children(hwnd: int) -> list[tuple[str, str]]:
    """Each child window's class and text."""
    found: list[tuple[str, str]] = []

    def each(child: int, _: int) -> bool:
        found.append((class_name(child), window_text(child)))
        return True

    user32.EnumChildWindows(hwnd, WNDENUMPROC(each), 0)
    return found


def message_box(pid: int) -> tuple[int, str] | None:
    """The Retry/Cancel box the installer (process pid) shows: its window and
    text. (The installer's own window is a dialog too, without a Retry.)"""
    dialogs: list[int] = []

    def each(hwnd: int, _: int) -> bool:
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid and class_name(hwnd) == "#32770" and user32.IsWindowVisible(hwnd):
            dialogs.append(hwnd)
        return True

    user32.EnumWindows(WNDENUMPROC(each), 0)
    for hwnd in dialogs:
        parts = children(hwnd)
        if ("Button", "&Retry") in parts:
            return hwnd, "\n".join(text for kind, text in parts if kind == "Static" and text)
    return None


def wait_for_message(setup: subprocess.Popen[bytes], seconds: float) -> tuple[int, str]:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        expect(setup.poll() is None, f"the installer ended ({setup.returncode}) without asking")
        if box := message_box(setup.pid):
            return box
        time.sleep(1)
    raise CheckFailedError("the installer never said what's running")


def quit_app() -> None:
    """The installer may start the app; this check wants it closed."""
    if (DATA / "desktop.json").exists():
        subprocess.run([str(INSTALL / "ringkeeper.exe"), "--quit"], check=False, timeout=120)  # noqa: S603
        deadline = time.monotonic() + 120
        while (DATA / "desktop.json").exists() and time.monotonic() < deadline:
            time.sleep(1)


def install(installer: Path, *flags: str) -> tuple[int, float]:
    started = time.monotonic()
    code = subprocess.run([str(installer), *flags], check=False, timeout=900).returncode  # noqa: S603
    quit_app()
    return code, time.monotonic() - started


def holder(exe: Path, cwd: Path | None = None) -> subprocess.Popen[bytes]:
    return subprocess.Popen(  # noqa: S603
        [str(exe), *WAIT], cwd=cwd, stdout=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )  # fmt: skip


def stop(process: subprocess.Popen[bytes]) -> None:
    process.kill()
    process.wait(30)


def another_ringkeeper_elsewhere(installer: Path, scratch: Path) -> None:
    step("a ringkeeper.exe from another folder doesn't block the install")
    decoy = scratch / ".venv" / "Scripts" / "ringkeeper.exe"
    decoy.parent.mkdir(parents=True)
    shutil.copy2(PING, decoy)
    running = holder(decoy)
    try:
        code, took = install(installer, "/S")
    finally:
        stop(running)
    print(f"installed over the existing copy: exit {code}, {took:.0f}s")
    expect(code == 0, f"the install failed with {decoy} running (exit {code})")


def asks_then_retries(installer: Path, running: subprocess.Popen[bytes], says: str) -> None:
    """The install waits, then says this; once running has stopped, Retry installs."""
    setup = subprocess.Popen([str(installer), "/P"])  # noqa: S603
    try:
        hwnd, text = wait_for_message(setup, 240)
        print(f"the installer says:\n{text}")
        expect(says.lower() in text.lower(), text)  # Windows paths ignore case
        stop(running)
        user32.PostMessageW(hwnd, WM_COMMAND, IDRETRY, 0)
        code = setup.wait(600)
    finally:
        if running.poll() is None:
            stop(running)
        if setup.poll() is None:
            setup.kill()
    print(f"after Retry: exit {code}")
    expect(code == 0, f"Retry didn't finish the install (exit {code})")
    quit_app()


def says_what_runs(installer: Path) -> None:
    step("a program running from the install folder: named, then Retry installs")
    held = INSTALL / "hold.exe"
    shutil.copy2(PING, held)
    asks_then_retries(installer, holder(held), f"Ringkeeper is running from {held}.")
    held.unlink()


def says_the_folder_is_in_use(installer: Path, scratch: Path) -> None:
    step("the Python folder in use, nothing running from it: says so")
    elsewhere = scratch / "ping.exe"
    shutil.copy2(PING, elsewhere)
    running = holder(elsewhere, cwd=INSTALL / "python")
    asks_then_retries(installer, running, f"Another program is using {INSTALL / 'python'}.")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--installer", type=Path, required=True)
    parser.add_argument("--scratch", type=Path, required=True)
    args = parser.parse_args()
    args.scratch.mkdir(parents=True, exist_ok=True)
    step("install")
    code, _ = install(args.installer, "/S")
    expect(code == 0 and (INSTALL / "ringkeeper.exe").exists(), f"install failed ({code})")
    another_ringkeeper_elsewhere(args.installer, args.scratch)
    says_what_runs(args.installer)
    says_the_folder_is_in_use(args.installer, args.scratch)
    print("\nall checks passed")


if __name__ == "__main__":
    try:
        main()
    finally:
        sys.stdout.flush()
