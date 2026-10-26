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

import json
import os
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

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
    # The workspaces folder may be set inside the data folder (a test setup does
    # that); a project folder within it is the user's, not Ringkeeper's own.
    workspaces = Path(os.path.normpath(root(settings).absolute()))
    in_workspaces = workspaces in path.parents
    if (path == data_dir or data_dir in path.parents) and not in_workspaces:
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


def claude_code_config() -> Path:
    """Claude Code's own settings file, which lists every folder it was opened in."""
    base = os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(base) / ".claude.json" if base else Path.home() / ".claude.json"


def claude_code_folders(settings: Settings, used: Iterable[str]) -> list[Path]:
    """Folders the user opened Claude Code in, to offer as projects: each once,
    still on disk, not a worktree copy, not Ringkeeper's own data folder, a
    whole drive or the home folder, and not already a Ringkeeper project's."""
    try:
        config: object = json.loads(claude_code_config().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    listed = cast(dict[str, Any], config).get("projects") if isinstance(config, dict) else None
    if not isinstance(listed, dict):
        return []
    data_dir = _key(settings.data_dir.absolute())
    seen = {_key(Path(p)) for p in used}
    found: list[Path] = []
    for text in cast(dict[str, Any], listed):
        path = Path(os.path.normpath(text))
        key = _key(path)
        if key in seen or not path.is_absolute():
            continue
        seen.add(key)
        parts = {part.lower() for part in path.parts}
        if ".claude" in parts and "worktrees" in parts:
            continue
        if path == Path(path.anchor) or path == Path.home():
            continue
        if key == data_dir or key.startswith(data_dir + os.sep):
            continue
        if path.is_dir():
            found.append(path)
    return sorted(found, key=lambda p: (p.name.lower(), str(p).lower()))


def _key(path: Path) -> str:
    """One spelling per folder: C:/a/b and c:\a\b are the same on Windows."""
    return os.path.normcase(os.path.normpath(path))
