"""Keeping or discarding a task's changes: its branch merged into the project's default
branch, or deleted.

A task works on a branch of its own (worker/ringkeeper_worker/workspace.py) and leaves
the folder as it found it. "Keep these changes" merges that branch into the default branch
(origin's HEAD, else main or master) and deletes it; on a conflict nothing is merged and
it says where. "Discard" deletes it. Either way the folder ends up checked out as it was.
Neither runs while a task is working in the folder, or while it has uncommitted changes.

Git runs without hooks or an fsmonitor, as the worker's does: the repository's hooks are
the client's.
"""

import os
import subprocess
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from ringkeeper.config import Settings
from ringkeeper.errors import RuleViolationError
from ringkeeper.models import TERMINAL_STATUSES, Task, TaskStatus
from ringkeeper.services import projects, tasks
from ringkeeper.services.workspaces import GIT_IDENTITY

SAFE_GIT = ("-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false", "-c", "core.pager=cat")
KEPT, DISCARDED = "kept", "discarded"
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class _GitError(Exception):
    pass


def _git(settings: Settings, root: Path, *args: str, check: bool = True) -> str:
    search = os.pathsep.join([*settings.worker_tools_path, os.environ.get("PATH", "")])
    env = {**os.environ, "PATH": search, "GIT_TERMINAL_PROMPT": "0"}
    done = subprocess.run(  # noqa: S603 (fixed argv, no shell)
        ["git", *SAFE_GIT, "-C", str(root), *args],  # noqa: S607 (the bundled git is first)
        capture_output=True, text=True, env=env, timeout=60, check=False,
        creationflags=_NO_WINDOW,
    )  # fmt: skip
    if check and done.returncode != 0:
        raise _GitError(done.stderr.strip() or done.stdout.strip())
    return done.stdout.strip()


def _has(settings: Settings, root: Path, branch: str) -> bool:
    return bool(
        _git(
            settings, root, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}", check=False
        )
    )


def _folder(session: Session, settings: Settings, task: Task) -> Path:
    from ringkeeper.workers.manager import resolve_workspace  # noqa: PLC0415 (a heavy import)

    project = projects.get_project(session, task.project)
    root = Path(resolve_workspace(project.workspace_path, settings.workspaces_root))
    if not (root / ".git").exists():
        raise RuleViolationError("The project's folder isn't a git repository here.")
    return root


def _ready(session: Session, settings: Settings, task: Task) -> tuple[Path, str]:
    """The folder and the task's branch, when they can be touched now."""
    if not task.git_branch or task.branch_state:
        raise RuleViolationError("This task has no changes waiting to be kept.")
    if task.status not in (*TERMINAL_STATUSES, TaskStatus.NEEDS_INPUT):
        raise RuleViolationError("Wait for the task to finish first.")
    root = _folder(session, settings, task)
    if tasks.folder_busy(session, task.project):
        raise RuleViolationError("A task is working in this project's folder. Try again after.")
    if not _has(settings, root, task.git_branch):
        raise RuleViolationError(f"The branch {task.git_branch} isn't in the folder any more.")
    dirty = _git(settings, root, "status", "--porcelain", "--untracked-files=no", check=False)
    if dirty.strip():
        raise RuleViolationError(
            "The project folder has changes that aren't committed. Commit or stash them, "
            "then try again."
        )
    return root, task.git_branch


def _default(settings: Settings, root: Path) -> str:
    origin = _git(
        settings,
        root,
        "symbolic-ref",
        "--quiet",
        "--short",
        "refs/remotes/origin/HEAD",
        check=False,
    )
    for name in [*([origin.removeprefix("origin/")] if origin else []), "main", "master"]:
        if _has(settings, root, name):
            return name
    raise RuleViolationError("The folder has no main or master branch to keep the changes on.")


def _current(settings: Settings, root: Path) -> str:
    name = _git(settings, root, "rev-parse", "--abbrev-ref", "HEAD")
    return name if name != "HEAD" else _git(settings, root, "rev-parse", "HEAD")


def _back_to(settings: Settings, root: Path, where: str, instead: str) -> None:
    """Check out what was checked out before (or `instead`, if that was the task's branch)."""
    exists = _has(settings, root, where)
    target = where if exists else instead
    args = (target,) if exists or target == instead else ("--detach", target)
    _git(settings, root, "switch", "-q", *args, check=False)


def _settle(session: Session, task: Task, state: str) -> None:
    """Every task of the conversation that shared the branch is settled with it."""
    shared = select(Task).where(Task.git_branch == task.git_branch, Task.branch_state.is_(None))
    for row in session.scalars(shared):
        row.branch_state = state
    session.flush()


def keep(session: Session, settings: Settings, task_id: str) -> str:
    """Merge the task's branch into the default branch, and delete it. Returns what happened."""
    task = tasks.get_task(session, task_id)
    root, branch = _ready(session, settings, task)
    default = _default(settings, root)
    before = _current(settings, root)
    try:
        _git(settings, root, "switch", "-q", default)
        _git(
            settings, root, *GIT_IDENTITY, "merge", "--no-ff", "--no-edit", "-m",
            f"Keep Ringkeeper's changes from {branch}", branch,
        )  # fmt: skip
    except _GitError:
        conflicts = _git(settings, root, "diff", "--name-only", "--diff-filter=U", check=False)
        _git(settings, root, "merge", "--abort", check=False)
        _back_to(settings, root, before, default)
        where = ", ".join(conflicts.splitlines()[:3]) or "some files"
        raise RuleViolationError(
            f"These changes conflict with what's on {default} now ({where}). Nothing was "
            f"merged: the changes stay on {branch}."
        ) from None
    _git(settings, root, "branch", "-q", "-D", branch, check=False)
    _back_to(settings, root, before if before != branch else default, default)
    _settle(session, task, KEPT)
    return f"Kept: the changes are on {default}."


def discard(session: Session, settings: Settings, task_id: str) -> str:
    """Delete the task's branch and what's on it."""
    task = tasks.get_task(session, task_id)
    root, branch = _ready(session, settings, task)
    before = _current(settings, root)
    if before == branch:
        _back_to(settings, root, _default(settings, root), _default(settings, root))
    _git(settings, root, "branch", "-q", "-D", branch, check=False)
    _settle(session, task, DISCARDED)
    return "Discarded: the branch and its changes are gone."
