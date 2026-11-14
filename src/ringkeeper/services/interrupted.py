"""Tasks a restart or an update cut short: listed on Home after startup, each with Resume,
and Resume all. A resumed task picks up its conversation where it stopped (or starts over,
if it hadn't got going). It leaves the list once resumed, followed up, or a few days old."""

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ringkeeper.config import Settings
from ringkeeper.errors import RuleViolationError
from ringkeeper.models import Task, TaskStatus, WorkerSession
from ringkeeper.services import goes_on, tasks

SAID = "Ringkeeper restarted during this task."
ERROR = f"{SAID} Resume it from Home, or send a follow-up to continue."
RESUME = goes_on.RESUMED
LISTED_FOR = timedelta(days=3)
MOST = 50


def _is(task: Task) -> bool:
    return task.status is TaskStatus.FAILED and (task.error or "").startswith(SAID)


def listed(session: Session) -> Sequence[Task]:
    since = datetime.now(UTC) - LISTED_FOR
    found = session.scalars(
        select(Task)
        .where(
            Task.status == TaskStatus.FAILED,
            Task.error.startswith(SAID),
            Task.finished_at >= since,
            Task.archived_at.is_(None),
        )
        .order_by(Task.finished_at.desc())
        .limit(MOST)
    ).all()
    return found


def resume(session: Session, settings: Settings, ident: str) -> WorkerSession:
    task = tasks.get_task(session, ident, lock=True)
    if not _is(task):
        raise RuleViolationError("This task isn't waiting to be resumed.")
    role = tasks.latest_session(task).role
    sdk_session = tasks.last_sdk_session(task, role)
    if sdk_session is None:  # it hadn't got going: start it over
        return tasks.add_session(session, settings, task, task.prompt, role)
    return tasks.resume_task(session, settings, str(task.id), RESUME)


def resume_all(session: Session, settings: Settings) -> int:
    ids = [str(task.id) for task in listed(session)]
    for ident in ids:
        resume(session, settings, ident)
    return len(ids)
