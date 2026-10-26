"""Archiving and deleting conversations and tasks.

Archived: hidden from the lists, still found by search, and brought back with
one click. Deleted: gone with their messages, sessions and events, never
while a task in them is queued or running. The audit log is never touched
(its rows don't reference tasks or conversations), and each delete is itself
audited. A task's state (its agent transcripts) is removed by the session
manager, which owns the runtime (`pending_state`).

A task's git branch is deleted only when asked, and only where Ringkeeper can
reach the project's folder (the desktop app; a server's api container can't).
A branch with commits that aren't on the default branch is kept unless the
user ticked it for that one task.
"""

import os
import subprocess
import uuid
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ringkeeper.config import Settings
from ringkeeper.errors import NotFoundError, RingkeeperError, RuleViolationError
from ringkeeper.models import Setting, Task, TaskStatus, Thread
from ringkeeper.services import admin_audit, projects, tasks

ACTIVE = (TaskStatus.QUEUED, TaskStatus.RUNNING)
PENDING_STATE_KEY = "pending_state_removals"
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _ids(values: Iterable[str]) -> list[uuid.UUID]:
    try:
        return [uuid.UUID(str(v)) for v in values]
    except ValueError:
        raise NotFoundError("one of those ids isn't a valid id") from None


def _tasks(session: Session, ids: Iterable[str]) -> Sequence[Task]:
    wanted = _ids(ids)
    found = session.scalars(select(Task).where(Task.id.in_(wanted))).all()
    if len(found) != len(set(wanted)):
        raise NotFoundError("some of those tasks don't exist (any more)")
    return found


def _threads(session: Session, ids: Iterable[str]) -> Sequence[Thread]:
    wanted = _ids(ids)
    found = session.scalars(select(Thread).where(Thread.id.in_(wanted))).all()
    if len(found) != len(set(wanted)):
        raise NotFoundError("some of those conversations don't exist (any more)")
    return found


def archive(session: Session, kind: str, ids: list[str], archived: bool, *, by: str) -> int:
    """Archive (or bring back) tasks or conversations. Returns how many."""
    rows: Sequence[Task | Thread] = (
        _tasks(session, ids) if kind == "task" else _threads(session, ids)
    )
    when = datetime.now(UTC) if archived else None
    for row in rows:
        row.archived_at = when
    verb = "archived" if archived else "unarchived"
    noun = "task" if kind == "task" else "conversation"
    admin_audit.record(
        session, f"{noun}.{verb}", f"{verb} {len(rows)} {noun}{'s' if len(rows) != 1 else ''}",
        by=by, details={"ids": [str(r.id) for r in rows]},
    )  # fmt: skip
    return len(rows)


# --- The task branch ---


def _git(settings: Settings, folder: Path, *args: str) -> subprocess.CompletedProcess[str]:
    search = os.pathsep.join([*settings.worker_tools_path, os.environ.get("PATH", "")])
    return subprocess.run(  # noqa: S603
        ["git", "-c", "core.hooksPath=", "-c", "core.fsmonitor=false", "-C", str(folder), *args],  # noqa: S607
        capture_output=True, text=True, check=False, timeout=30,
        env={**os.environ, "PATH": search}, creationflags=_NO_WINDOW,
    )  # fmt: skip


def _folder(session: Session, settings: Settings, task: Task) -> Path | None:
    project = projects.find_project(session, task.project)
    if project is None or not project.workspace_path:
        return None
    from ringkeeper.workers.manager import resolve_workspace  # noqa: PLC0415 (cycle)

    try:
        folder = Path(resolve_workspace(project.workspace_path, settings.workspaces_root))
    except RingkeeperError:
        return None
    # A server's api container doesn't see the host's folders: then there's nothing here.
    return folder if (folder / ".git").exists() else None


def branch_info(session: Session, settings: Settings, task: Task) -> dict[str, Any] | None:
    """The task's branch in the project's folder: whether it's there, and how many of
    its commits aren't on the default branch. None if the folder can't be reached."""
    folder = _folder(session, settings, task)
    if folder is None:
        return None
    name = tasks.branch_name(task.id)

    def has(branch: str) -> bool:
        found = _git(settings, folder, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}")
        return found.returncode == 0

    try:
        if not has(name):
            return {"name": name, "exists": False, "unmerged": 0}
        base = next((b for b in ("main", "master") if has(b)), None)
        if base is None:
            return {"name": name, "exists": True, "unmerged": None}
        counted = _git(settings, folder, "rev-list", "--count", f"{base}..{name}")
        unmerged = int(counted.stdout.strip() or 0) if counted.returncode == 0 else None
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    return {"name": name, "exists": True, "unmerged": unmerged}


def _delete_branch(session: Session, settings: Settings, task: Task, *, unmerged_too: bool) -> str:
    info = branch_info(session, settings, task)
    if info is None or not info["exists"]:
        return "none"
    if info["unmerged"] != 0 and not unmerged_too:
        return "kept (it has work that isn't merged)"
    folder = _folder(session, settings, task)
    if folder is None:
        return "none"
    done = _git(settings, folder, "branch", "-D", str(info["name"]))
    return "deleted" if done.returncode == 0 else f"kept ({done.stderr.strip()[:200]})"


# --- Deleting ---


def _refuse_active(found: Iterable[Task]) -> None:
    if busy := [t for t in found if t.status in ACTIVE]:
        names = ", ".join(str(t.id)[:8] for t in busy)
        raise RuleViolationError(
            f"Stop {'it' if len(busy) == 1 else 'them'} first: {names} "
            f"{'is' if len(busy) == 1 else 'are'} still running or about to."
        )


def _queue_state(session: Session, found: Iterable[Task]) -> None:
    """The session manager removes these state volumes (transcripts) on its next sweep."""
    names = sorted({s.state_volume for t in found for s in t.sessions if s.state_volume})
    if not names:
        return
    row = session.get(Setting, PENDING_STATE_KEY)
    if row is None:
        session.add(Setting(key=PENDING_STATE_KEY, value={"volumes": names}))
    else:
        row.value = {"volumes": sorted(set(row.value.get("volumes", [])) | set(names))}


def take_pending_state(session: Session) -> list[str]:
    row = session.get(Setting, PENDING_STATE_KEY)
    if row is None:
        return []
    names = [str(n) for n in row.value.get("volumes", [])]
    session.delete(row)
    return names


def delete_tasks(
    session: Session,
    settings: Settings,
    ids: list[str],
    *,
    delete_branches: bool,
    unmerged_too: bool = False,
    by: str,
) -> dict[str, str]:
    """Delete tasks (never running ones). Returns task id -> what happened to its branch."""
    found = _tasks(session, ids)
    _refuse_active(found)
    branches = {
        str(t.id): _delete_branch(session, settings, t, unmerged_too=unmerged_too)
        if delete_branches else "kept"
        for t in found
    }  # fmt: skip
    _queue_state(session, found)
    for task in found:
        admin_audit.record(
            session, "task.delete",
            f"deleted task {str(task.id)[:8]} ({task.project}); branch {branches[str(task.id)]}",
            by=by, project=task.project, details={"task": str(task.id)},
        )  # fmt: skip
    # In SQL, so the database's cascades take the sessions and their events along.
    session.execute(delete(Task).where(Task.id.in_([t.id for t in found])))
    session.expire_all()
    return branches


def delete_threads(
    session: Session, settings: Settings, ids: list[str], *, delete_branches: bool, by: str
) -> int:
    """Delete conversations with their tasks (none may be running). Branches with
    unmerged work are kept. Returns how many tasks went with them."""
    found = _threads(session, ids)
    owned = session.scalars(select(Task).where(Task.thread_id.in_([t.id for t in found]))).all()
    _refuse_active(owned)
    if owned:
        delete_tasks(
            session, settings, [str(t.id) for t in owned], delete_branches=delete_branches, by=by
        )
    for thread in found:
        admin_audit.record(
            session, "conversation.delete", f"deleted conversation '{thread.title[:80]}'",
            by=by, details={"thread": str(thread.id)},
        )  # fmt: skip
        session.delete(thread)
    session.flush()
    return len(owned)
