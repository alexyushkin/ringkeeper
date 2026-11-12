"""Files a task made or changed: listed on its page and under its result, opened or shown
in their folder (desktop), and attached to its result in Slack, Discord and Telegram when
they're small enough.

Where a file is: in the project's folder (a task that didn't use a branch, or changes that
were kept), or only on the task's branch (a code task's changes, until you keep them). A
file on the branch is opened from a copy taken from the branch, under the data folder, so
the folder you work in isn't touched. Only files the task recorded as changed are ever
read, and never outside the project's folder. Only documents (PDF, Office files, text,
Markdown, CSV, JSON and pictures) open in their app; anything else is shown in its folder.
"""

import contextlib
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from sqlalchemy.orm import Session

from ringkeeper.config import Settings
from ringkeeper.errors import NotFoundError, RuleViolationError
from ringkeeper.models import Task
from ringkeeper.scrub import scrub
from ringkeeper.services import branches, projects

ATTACH_BYTES = 8 * 1024 * 1024  # each, so Discord (10 MB for a bot), Slack and Telegram take it
MOST_ATTACHED = 5
COPIES = "task-files"  # under the data folder: files opened from a task's branch
# Opened in their app: documents only. Anything else (a program, a script, a web page, a
# shortcut, a file with no type) is shown in its folder instead, so nothing runs by a click.
OPENS = frozenset(
    [
        ".pdf",
        ".doc",
        ".docx",
        ".xls",
        ".xlsx",
        ".ppt",
        ".pptx",
        ".odt",
        ".ods",
        ".odp",
        ".rtf",
        ".txt",
        ".log",
        ".md",
        ".markdown",
        ".csv",
        ".tsv",
        ".json",
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".webp",
        ".bmp",
        ".tif",
        ".tiff",
        ".heic",
    ]
)
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


@dataclass(frozen=True)
class TaskFile:
    path: str  # in the project's folder, with /
    size: int | None  # None: gone (the task deleted it)
    on_branch: bool  # only on the task's branch, not in the folder yet
    runs: bool  # not a document (see OPENS): shown in its folder, never opened

    @property
    def attachable(self) -> bool:
        return self.size is not None and 0 < self.size <= ATTACH_BYTES


def shown_only(path: str | Path) -> bool:
    """Not a document type: shown in its folder, never opened."""
    return Path(path).suffix.lower() not in OPENS


def _root(session: Session, settings: Settings, task: Task) -> Path:
    from ringkeeper.workers.manager import resolve_workspace  # noqa: PLC0415 (a heavy import)

    project = projects.get_project(session, task.project)
    return Path(resolve_workspace(project.workspace_path, settings.workspaces_root))


def _branch(settings: Settings, root: Path, task: Task) -> str | None:
    """The task's branch, while its changes are only there (not kept, not discarded)."""
    if not task.git_branch or task.branch_state or not (root / ".git").exists():
        return None
    return task.git_branch if branches.has_branch(settings, root, task.git_branch) else None


def _checked(task: Task, path: str) -> str:
    """One of the task's own changed files, as a safe relative path."""
    if path not in task.changed_files:
        raise NotFoundError("That file isn't one this task changed.")
    parts = PurePosixPath(path).parts
    if not parts or ".." in parts or PurePosixPath(path).is_absolute() or ":" in parts[0]:
        raise RuleViolationError("That file is outside the project's folder.")
    return path


def _inside(root: Path, path: str) -> Path:
    full = (root / path).resolve()
    if not full.is_relative_to(root.resolve()):
        raise RuleViolationError("That file is outside the project's folder.")
    return full


def _on_branch_size(settings: Settings, root: Path, branch: str, path: str) -> int | None:
    said = branches.git_text(settings, root, "cat-file", "-s", f"{branch}:{path}")
    return int(said) if said.isdigit() else None


def listed(session: Session, settings: Settings, task: Task) -> list[TaskFile]:
    """The task's changed files, where each is now."""
    if not task.changed_files:
        return []
    try:
        root = _root(session, settings, task)
    except Exception:
        return [TaskFile(p, None, False, shown_only(p)) for p in task.changed_files]
    branch = _branch(settings, root, task)
    found: list[TaskFile] = []
    for path in task.changed_files:
        runs = shown_only(path)
        if branch is not None:
            found.append(TaskFile(path, _on_branch_size(settings, root, branch, path), True, runs))
            continue
        try:
            full = _inside(root, path)
            size = full.stat().st_size if full.is_file() else None
        except (OSError, RuleViolationError):
            size = None
        found.append(TaskFile(path, size, False, runs))
    return found


def content(session: Session, settings: Settings, task: Task, path: str) -> bytes | None:
    """A changed file's bytes as the task left them (None: gone, or too big to attach)."""
    path = _checked(task, path)
    root = _root(session, settings, task)
    if (branch := _branch(settings, root, task)) is not None:
        size = _on_branch_size(settings, root, branch, path)
        if size is None or size > ATTACH_BYTES:
            return None
        return branches.git_bytes(settings, root, "show", f"{branch}:{path}")
    full = _inside(root, path)
    if not full.is_file() or full.stat().st_size > ATTACH_BYTES:
        return None
    return full.read_bytes()


def local_file(session: Session, settings: Settings, task: Task, path: str) -> Path:
    """Where to open it: the file in the folder, or a copy taken from the task's branch."""
    path = _checked(task, path)
    root = _root(session, settings, task)
    if (branch := _branch(settings, root, task)) is None:
        full = _inside(root, path)
        if not full.exists():
            raise NotFoundError("That file isn't in the project's folder any more.")
        return full
    data = branches.git_bytes(settings, root, "show", f"{branch}:{path}")
    if data is None:
        raise NotFoundError("That file isn't on the task's branch any more.")
    copy = (settings.data_dir / COPIES / str(task.id) / path).resolve()
    if not copy.is_relative_to((settings.data_dir / COPIES).resolve()):
        raise RuleViolationError("That file is outside the project's folder.")
    copy.parent.mkdir(parents=True, exist_ok=True)
    copy.write_bytes(data)
    return copy


# Never sent to a chat: what looks like a secret (a key, a .env file, credentials).
SECRET_NAMES = re.compile(
    r"(^\.env($|\.)|\.(pem|key|p12|pfx|kdbx|keystore|jks)$|^id_(rsa|dsa|ecdsa|ed25519)|"
    r"credential|secret|password|token)",
    re.I,
)


def attachments(session: Session, settings: Settings, task: Task) -> list[tuple[str, bytes]]:
    """The files to attach to the task's result in a chat: small ones, at most a few, never
    one that looks like a secret; text has known secrets masked, as messages do."""
    found: list[tuple[str, bytes]] = []
    try:
        files = listed(session, settings, task)
    except Exception:
        return []
    for one in files:
        name = PurePosixPath(one.path).name
        if len(found) >= MOST_ATTACHED or not one.attachable or SECRET_NAMES.search(name):
            continue
        try:
            data = content(session, settings, task, one.path)
        except (RuleViolationError, NotFoundError, OSError):
            continue
        if data is None:
            continue
        with contextlib.suppress(UnicodeDecodeError):  # not text: as it is
            data = scrub(data.decode("utf-8")).encode("utf-8")
        found.append((name, data))
    return found


def show(path: Path, *, reveal: bool) -> None:
    """Open the file in its app, or show it in its folder (anything but a document: always
    shown, never opened)."""
    reveal = reveal or shown_only(path)
    if sys.platform == "win32":
        if reveal:
            subprocess.Popen(["explorer", f"/select,{path}"], creationflags=_NO_WINDOW)  # noqa: S603, S607
        else:
            os.startfile(path)  # noqa: S606 (a document the task made: see OPENS)  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R", str(path)] if reveal else ["open", str(path)])  # noqa: S603
    else:
        subprocess.Popen(["xdg-open", str(path.parent if reveal else path)])  # noqa: S603, S607
