"""Where a backup can go so it outlives this computer.

A backup on the same disk as Ringkeeper dies with the computer. A synced folder (OneDrive,
Google Drive, Dropbox, iCloud Drive) leaves it: Ringkeeper only writes the file there, and the
service's own app uploads it. Another drive works too, and so does a folder the owner backs up
another way (iDrive, Backblaze, Time Machine): they say so once per folder.
"""

import json
import os
import sys
from dataclasses import dataclass
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


@dataclass(frozen=True)
class Place:
    service: str  # "OneDrive", "Google Drive", "Dropbox", "iCloud Drive"
    label: str  # what the button says: "OneDrive", "OneDrive (Acme Inc)"
    root: str  # the synced folder itself
    folder: str  # where backups go in it


def _dropbox_roots(info_files: list[Path]) -> list[tuple[str, Path]]:
    """Dropbox says where its folders are in info.json: personal and business."""
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


def _candidates(
    home: Path, env: dict[str, str], platform: str, drives: list[Path]
) -> list[tuple[str, str, Path]]:
    """(service, label, root) wherever each service keeps its folder on this platform."""
    found: list[tuple[str, str, Path]] = []
    if platform == "win32":
        for var in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
            if env.get(var):
                root = Path(env[var])
                found.append(("OneDrive", _onedrive_label(root.name), root))
        found += [("OneDrive", _onedrive_label(p.name), p) for p in sorted(home.glob("OneDrive*"))]
        # Google Drive for desktop: a drive of its own (G: by default) holding "My Drive".
        found += [("Google Drive", "Google Drive", drive / "My Drive") for drive in drives]
        found.append(("Google Drive", "Google Drive", home / "Google Drive"))  # older app
        appdata = [Path(env[v]) / "Dropbox" / "info.json" for v in ("APPDATA", "LOCALAPPDATA")
                   if env.get(v)]  # fmt: skip
        found += [("Dropbox", label, root) for label, root in _dropbox_roots(appdata)]
        found.append(("Dropbox", "Dropbox", home / "Dropbox"))
        found.append(("iCloud Drive", "iCloud Drive", home / "iCloudDrive"))
    elif platform == "darwin":
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
        found += [("Dropbox", "Dropbox", p) for p in sorted(storage.glob("Dropbox*"))]
        found.append(("Dropbox", "Dropbox", home / "Dropbox"))
        found.append(("iCloud Drive", "iCloud Drive",
                      home / "Library" / "Mobile Documents" / "com~apple~CloudDocs"))  # fmt: skip
    else:
        found += [
            ("Dropbox", label, root)
            for label, root in _dropbox_roots([home / ".dropbox" / "info.json"])
        ]
        found.append(("Dropbox", "Dropbox", home / "Dropbox"))
    return found


def _onedrive_label(name: str) -> str:
    """ "OneDrive - Acme Inc" (a work account) says whose: "OneDrive (Acme Inc)"."""
    rest = name.removeprefix("OneDrive").strip(" -")
    return f"OneDrive ({rest})" if rest else "OneDrive"


def synced_folders(
    home: Path | None = None,
    env: dict[str, str] | None = None,
    platform: str | None = None,
    drives: list[Path] | None = None,
) -> list[Place]:
    """The synced folders on this computer, each once, with where backups would go in it."""
    home = home or Path.home()
    env = dict(os.environ) if env is None else env
    platform = platform or sys.platform
    if drives is None:
        drives = local_drives() if platform == "win32" else []
    seen: set[str] = set()
    places: list[Place] = []
    for service, label, root in _candidates(home, env, platform, drives):
        key = _key(root)
        if key in seen or not root.is_dir():
            continue
        seen.add(key)
        places.append(Place(service, label, str(root), str(root / FOLDER_NAME)))
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
        "same_disk": on_same_disk,
        "backed_up_elsewhere": other_way,
        "warning": SAME_DISK if on_same_disk and not synced and not other_way else None,
    }
