"""Project folders in desktop mode: any folder on this computer, made if missing.

In server mode a project's folder is a name inside RINGKEEPER_WORKSPACES_ROOT:
the api runs in a container and can't make folders on the host, and an
absolute path would let a worker container mount anything. In desktop mode a
worker is a program running as the user anyway, so a project may use any
folder the user picks. Left empty, it gets a new folder named after the
project in the workspaces folder (Documents\\Ringkeeper).

A new (or empty) folder is started as a git repository with one empty commit
on `main`, so each task's changes land on a branch of their own for review.
A folder that already has files is left exactly as it is: without git,
Ringkeeper lists the files a task changed instead.
"""

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from ringkeeper.config import Settings
from ringkeeper.desktop import paths
from ringkeeper.errors import RuleViolationError

# No console window flashing up on Windows (the backend itself has none).
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
GIT_IDENTITY = ("-c", "user.name=Ringkeeper", "-c", "user.email=ringkeeper@ringkeeper.invalid")


@dataclass(frozen=True)
class Folder:
    path: str  # absolute
    created: bool  # made just now
    git: bool  # a git repository (now)


def root(settings: Settings) -> Path:
    return Path(settings.workspaces_root) if settings.workspaces_root else paths.workspaces_dir()


def choose(settings: Settings, text: str | None, name: str) -> Path:
    """The folder a project gets: what the user typed (a path, or a name inside
    the workspaces folder), or a new one named after the project."""
    raw = (text or "").strip().strip('"').strip()  # Explorer's "Copy as path" adds quotes
    path = Path(raw).expanduser() if raw else Path(name)
    if ".." in path.parts:
        raise RuleViolationError("The folder can't contain '..'.")
    if not path.is_absolute():
        path = root(settings) / path
    path = Path(os.path.normpath(path))
    if path == Path(path.anchor) or path == Path.home():
        raise RuleViolationError(
            "Pick a folder for this project, not a whole drive or your home folder."
        )
    data_dir = Path(os.path.normpath(settings.data_dir.absolute()))
    if path == data_dir or data_dir in path.parents:
        raise RuleViolationError("That's Ringkeeper's own data folder. Pick another folder.")
    if path.exists() and not path.is_dir():
        raise RuleViolationError("That's a file, not a folder.")
    return path


def prepare(settings: Settings, path: Path) -> Folder:
    """Make the folder if it's missing, and start a git repository in a new or empty one."""
    created = not path.exists()
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise RuleViolationError(
            f"Couldn't make the folder {path}: {error.strerror or error}."
        ) from None
    git = (path / ".git").exists()
    if not git and not any(path.iterdir()):
        git = _start_repository(settings, path)
    return Folder(str(path), created, git)


def _start_repository(settings: Settings, path: Path) -> bool:
    """git init plus an empty first commit. False if git isn't there or fails:
    tasks still work, they list changed files instead of committing them."""
    search = os.pathsep.join([*settings.worker_tools_path, os.environ.get("PATH", "")])
    env = {**os.environ, "PATH": search}
    try:
        for args in (
            ("-c", "init.defaultBranch=main", "init", "-q"),
            (*GIT_IDENTITY, "commit", "-q", "--allow-empty", "--no-verify", "-m", "Start"),
        ):
            subprocess.run(  # noqa: S603
                ["git", *args],  # noqa: S607 (the bundled git is first on PATH)
                cwd=path, env=env, check=True, capture_output=True, timeout=30,
                creationflags=_NO_WINDOW,
            )  # fmt: skip
    except (OSError, subprocess.SubprocessError):
        return False
    return True
