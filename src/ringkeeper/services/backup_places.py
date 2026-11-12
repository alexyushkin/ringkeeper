"""Where a backup can go so it outlives this computer.

A backup on the same disk as Ringkeeper dies with the computer. A synced folder (OneDrive,
Google Drive, Dropbox, iCloud Drive) leaves it: Ringkeeper only writes the file there, and the
service's own app uploads it. Another drive works too, and so does a folder the owner backs up
another way (iDrive, Backblaze, Time Machine): they say so once per folder.

A synced folder is offered only when its app has an account signed in on this computer, at the
folder that account's own settings name: an empty ~/OneDrive with no account syncs nowhere, and
offering it would also hide the same-disk warning. An app that's signed in but not running is
offered with a note that nothing uploads until it runs.
"""

import json
import os
import plistlib
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from sqlalchemy.orm import Session

from ringkeeper.models import Setting

FOLDER_NAME = "Ringkeeper backups"
ELSEWHERE_KEY = "backup-backed-up-elsewhere"  # folders the owner backs up another way
SAME_DISK = (
    "This backup is on the same disk as Ringkeeper. If this computer is lost, so is the backup. "
    "Choose a synced folder or another drive."
)
# Each app's process as `tasklist` (Windows) or `ps` (macOS, Linux) names it, lowercased.
PROCESSES = {
    "win32": {"OneDrive": "onedrive.exe", "Dropbox": "dropbox.exe"},
    "darwin": {"OneDrive": "onedrive", "Google Drive": "google drive", "Dropbox": "dropbox"},
    "linux": {"Dropbox": "dropbox"},
}
ONEDRIVE_ACCOUNTS = r"Software\Microsoft\OneDrive\Accounts"


@dataclass(frozen=True)
class Place:
    service: str  # "OneDrive", "Google Drive", "Dropbox", "iCloud Drive"
    label: str  # the account: "OneDrive", "OneDrive (Acme Inc)"
    root: str  # the synced folder itself
    folder: str  # where backups go in it
    note: str | None = None  # "OneDrive isn't running, so this won't upload until it is."


def _nothing_running() -> set[str]:
    return set()


@dataclass
class Machine:
    """What this computer says about its sync apps. Tests pass made-up ones."""

    home: Path
    env: dict[str, str]
    platform: str
    drives: list[Path] = field(default_factory=list[Path])
    # OneDrive on Windows: (account key, its UserFolder) from the registry.
    onedrive: list[tuple[str, Path]] = field(default_factory=list[tuple[str, Path]])
    running: Callable[[], set[str]] = _nothing_running


def _dropbox_roots(info_files: list[Path]) -> list[tuple[str, Path]]:
    """Dropbox writes info.json when an account is linked: where its folders are, personal and
    business."""
    for info in info_files:
        try:
            data: Any = json.loads(info.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        accounts = cast(dict[str, Any], data) if isinstance(data, dict) else {}
        found: list[tuple[str, Path]] = []
        for kind, entry in accounts.items():
            where = cast(dict[str, Any], entry).get("path") if isinstance(entry, dict) else None
            if where:
                found.append(("Dropbox" if kind == "personal" else "Dropbox (business)",
                              Path(str(where))))  # fmt: skip
        if found:
            return found
    return []


def local_drives() -> list[Path]:
    """This computer's own fixed drives (Windows): Google Drive for desktop shows up as one.
    Network, removable and CD drives are skipped: a lost network drive can take a minute to
    answer."""
    if sys.platform != "win32":
        return []
    import ctypes  # noqa: PLC0415 (Windows only)

    fixed = 3  # DRIVE_FIXED
    kernel32 = ctypes.windll.kernel32
    kernel32.GetDriveTypeW.argtypes = [ctypes.c_wchar_p]
    return [Path(drive) for drive in os.listdrives() if kernel32.GetDriveTypeW(drive) == fixed]


def onedrive_accounts() -> list[tuple[str, Path]]:
    """OneDrive's signed-in accounts on Windows: each has a key under Accounts ("Personal",
    "Business1"...) with the folder it syncs as UserFolder. A key without one isn't signed in."""
    if sys.platform != "win32":
        return []
    import winreg  # noqa: PLC0415 (Windows only)

    found: list[tuple[str, Path]] = []
    try:
        accounts = winreg.OpenKey(winreg.HKEY_CURRENT_USER, ONEDRIVE_ACCOUNTS)
    except OSError:
        return []
    with accounts:
        index = 0
        while True:
            try:
                name = winreg.EnumKey(accounts, index)
            except OSError:
                break
            index += 1
            try:
                with winreg.OpenKey(accounts, name) as account:
                    folder: object = winreg.QueryValueEx(account, "UserFolder")[0]
            except OSError:
                continue
            if isinstance(folder, str) and folder.strip():
                found.append((name, Path(folder)))
    return found


def running_processes() -> set[str]:
    """The names of the programs running now, lowercased. Empty if the list can't be read."""
    windows = sys.platform == "win32"
    command = ["tasklist", "/FO", "CSV", "/NH"] if windows else ["ps", "-axo", "comm="]
    try:
        out = subprocess.run(  # noqa: S603 (a fixed command)
            command, capture_output=True, text=True, timeout=5, check=False,
            creationflags=0x08000000 if windows else 0,  # CREATE_NO_WINDOW
        )  # fmt: skip
    except (OSError, subprocess.SubprocessError):
        return set()
    names: set[str] = set()
    for line in out.stdout.splitlines():
        name = line.split('","', 1)[0].strip('"') if windows else Path(line.strip()).name
        if name:
            names.add(name.casefold())
    return names


def this_machine() -> Machine:
    platform = sys.platform
    return Machine(
        home=Path.home(), env=dict(os.environ), platform=platform,
        drives=local_drives() if platform == "win32" else [],
        onedrive=onedrive_accounts() if platform == "win32" else [],
        running=running_processes,
    )  # fmt: skip


def _icloud_signed_in(home: Path) -> bool:
    """macOS: an Apple account signed in with iCloud Drive on (MobileMeAccounts, the list
    System Settings keeps)."""
    try:
        with (home / "Library" / "Preferences" / "MobileMeAccounts.plist").open("rb") as f:
            data: Any = plistlib.load(f)
    except (OSError, ValueError, plistlib.InvalidFileException):
        return False
    accounts = cast(dict[str, Any], data).get("Accounts") if isinstance(data, dict) else None
    for account in cast(list[Any], accounts or []):
        services = (
            cast(dict[str, Any], account).get("Services") if isinstance(account, dict) else None
        )
        for service in cast(list[Any], services or []):
            if not isinstance(service, dict):
                continue
            entry = cast(dict[str, Any], service)
            if entry.get("Name") == "MOBILE_DOCUMENTS" and entry.get("Enabled"):
                return True
    return False


def _candidates(machine: Machine) -> list[tuple[str, str, Path]]:
    """(service, label, root) for each signed-in account, at the folder its app names."""
    home, env, platform = machine.home, machine.env, machine.platform
    found: list[tuple[str, str, Path]] = []
    if platform == "win32":
        for key, root in machine.onedrive:
            personal = key.casefold() == "personal"
            found.append(("OneDrive", "OneDrive" if personal else _onedrive_label(root.name), root))
        # Google Drive for desktop: while it's signed in and running, a drive of its own (G: by
        # default) holding "My Drive". Signed out or stopped, the drive isn't there.
        found += [("Google Drive", "Google Drive", drive / "My Drive") for drive in machine.drives]
        appdata = [Path(env[v]) / "Dropbox" / "info.json" for v in ("APPDATA", "LOCALAPPDATA")
                   if env.get(v)]  # fmt: skip
        found += [("Dropbox", label, root) for label, root in _dropbox_roots(appdata)]
        # iCloud for Windows keeps no settings Ringkeeper can read to tell it's signed in: its
        # folder isn't offered, and Browse still reaches it.
    elif platform == "darwin":
        # A signed-in OneDrive or Google Drive is a File Provider folder here; signing out
        # removes it.
        storage = home / "Library" / "CloudStorage"
        found += [
            ("OneDrive", _onedrive_label(p.name), p) for p in sorted(storage.glob("OneDrive*"))
        ]
        found += [
            ("Google Drive", "Google Drive", p / "My Drive")
            for p in sorted(storage.glob("GoogleDrive*"))
        ]
        found += [
            ("Dropbox", label, root)
            for label, root in _dropbox_roots([home / ".dropbox" / "info.json"])
        ]
        if _icloud_signed_in(home):
            icloud = home / "Library" / "Mobile Documents" / "com~apple~CloudDocs"
            found.append(("iCloud Drive", "iCloud Drive", icloud))
    else:
        found += [
            ("Dropbox", label, root)
            for label, root in _dropbox_roots([home / ".dropbox" / "info.json"])
        ]
    return found


def _onedrive_label(name: str) -> str:
    """ "OneDrive - Acme Inc" (a work account) says whose: "OneDrive (Acme Inc)"."""
    rest = name.removeprefix("OneDrive").strip(" -")
    return f"OneDrive ({rest})" if rest else "OneDrive"


def synced_folders(machine: Machine | None = None) -> list[Place]:
    """The synced folders of the accounts signed in on this computer, each once, with where
    backups would go in it and, if its app isn't running, a note saying so."""
    machine = machine or this_machine()
    processes = PROCESSES.get(machine.platform, {})
    running: set[str] | None = None
    seen: set[str] = set()
    places: list[Place] = []
    for service, label, root in _candidates(machine):
        key = _key(root)
        if key in seen or not root.is_dir():
            continue
        seen.add(key)
        note = None
        if service in processes:
            running = machine.running() if running is None else running
            # Nothing listed means the list couldn't be read: say nothing rather than wrong.
            if running and processes[service] not in running:
                note = f"{service} isn't running, so this won't upload until it is."
        places.append(Place(service, label, str(root), str(root / FOLDER_NAME), note))
    return places


def _key(path: Path | str) -> str:
    """A folder as compared: resolved, and without case where the file system ignores it."""
    text = os.path.normpath(str(Path(path).expanduser()))
    return text.casefold() if sys.platform in ("win32", "darwin") else text


def _inside(path: str, root: str) -> bool:
    child, parent = _key(path), _key(root)
    return child == parent or child.startswith(parent.rstrip("\\/") + os.sep)


def _existing(path: Path) -> Path:
    """The nearest folder that exists: a backup folder may not be made yet."""
    for candidate in (path, *path.parents):
        if candidate.exists():
            return candidate
    return Path(path.anchor) if path.anchor else path


def same_disk(folder: Path, data_dir: Path) -> bool:
    """Whether the folder is on the same disk (volume) as Ringkeeper's data."""
    try:
        return os.stat(_existing(folder)).st_dev == os.stat(_existing(data_dir)).st_dev
    except OSError:
        return False


def elsewhere(session: Session) -> list[str]:
    row = session.get(Setting, ELSEWHERE_KEY)
    value = row.value if row is not None else {}
    return [str(f) for f in cast(list[Any], value.get("folders") or [])]


def set_elsewhere(session: Session, folder: str, backed_up: bool) -> list[str]:
    """Remember (or forget) that the owner backs this folder up another way."""
    kept = [f for f in elsewhere(session) if _key(f) != _key(folder)]
    if backed_up:
        kept.append(folder)
    row = session.get(Setting, ELSEWHERE_KEY)
    if row is None:
        session.add(Setting(key=ELSEWHERE_KEY, value={"folders": kept}))
    else:
        row.value = {"folders": kept}
    session.flush()
    return kept


def check(session: Session, folder: str, data_dir: Path, places: list[Place]) -> dict[str, Any]:
    """What to say about a backup folder: synced (fine), another drive (fine), backed up
    another way (fine, the owner said so), or the same disk (the warning)."""
    synced = next((p for p in places if _inside(folder, p.root)), None)
    other_way = any(_key(f) == _key(folder) for f in elsewhere(session))
    on_same_disk = same_disk(Path(folder), data_dir)
    return {
        "folder": folder,
        "synced": synced.label if synced else None,
        "service": synced.service if synced else None,
        "note": synced.note if synced else None,
        "same_disk": on_same_disk,
        "backed_up_elsewhere": other_way,
        "warning": SAME_DISK if on_same_disk and not synced and not other_way else None,
    }
