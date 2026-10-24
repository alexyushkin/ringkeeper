"""Tasks and worker sessions: creation, lookup, stop requests, and the manager's bookkeeping.

The CLI never talks to Docker. It writes tasks and stop requests here and
pokes the session manager with NOTIFY; the manager (in the api service)
does the rest.
"""

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import String, cast, func, select, text
from sqlalchemy.orm import Session, selectinload

from ringkeeper.config import Settings
from ringkeeper.errors import NotFoundError, RuleViolationError
from ringkeeper.models import (
    TERMINAL_STATUSES,
    AuthMode,
    SessionEvent,
    Task,
    TaskStatus,
    WorkerSession,
)
from ringkeeper.services import projects, revocations
from ringkeeper.services.decisions import MIN_ID_PREFIX, current_user

NOTIFY_CHANNEL = "ringkeeper_tasks"


def notify(session: Session) -> None:
    """Wake the session manager (delivered when the transaction commits)."""
    session.execute(text(f"NOTIFY {NOTIFY_CHANNEL}"))


def state_volume_name(task_id: uuid.UUID) -> str:
    return f"ringkeeper-state-{task_id}"


def branch_name(task_id: uuid.UUID) -> str:
    return f"ringkeeper/{task_id}"


def create_task(
    session: Session,
    settings: Settings,
    *,
    project: str,
    prompt: str,
    max_turns: int | None = None,
    max_runtime_s: int | None = None,
    budget_usd: float | None = None,
) -> Task:
    record = projects.get_project(session, project)
    if not prompt.strip():
        raise RuleViolationError("the prompt is empty")
    mode = settings.claude_auth_mode
    if budget_usd is not None and mode is not AuthMode.API_KEY:
        raise RuleViolationError(
            "a USD budget only applies in api_key mode (subscription usage isn't billed per task)"
        )
    if budget_usd is not None and budget_usd <= 0:
        raise RuleViolationError("the budget must be more than 0")
    turns = max_turns or settings.default_max_turns
    runtime = max_runtime_s or settings.default_max_runtime_seconds
    if turns < 1:
        raise RuleViolationError("max turns must be at least 1")
    # The session's gateway token lives as long as the task may run.
    if not 0 < runtime <= settings.token_max_ttl_seconds:
        raise RuleViolationError(
            f"max runtime must be between 1s and {settings.token_max_ttl_seconds}s"
        )
    task = Task(
        project=record.name,
        prompt=prompt,
        status=TaskStatus.QUEUED,
        auth_mode=mode,
        max_turns=turns,
        max_runtime_s=runtime,
        budget_usd=budget_usd,
        changed_files=[],
    )
    session.add(task)
    session.flush()
    task.sessions = [
        WorkerSession(
            task_id=task.id,
            ordinal=1,
            prompt=prompt,
            status=TaskStatus.QUEUED,
            state_volume=state_volume_name(task.id),
        )
    ]
    session.add_all(task.sessions)
    session.flush()
    notify(session)
    return task


def get_task(session: Session, ident: str, *, lock: bool = False) -> Task:
    ident = ident.strip().lower()
    query = select(Task).options(selectinload(Task.sessions))
    try:
        query = query.where(Task.id == uuid.UUID(ident))
    except ValueError:
        if len(ident) < MIN_ID_PREFIX:
            raise NotFoundError(
                f"task '{ident}' not found; use the full id or 8+ characters"
            ) from None
        query = query.where(cast(Task.id, String).startswith(ident))
    if lock:
        query = query.with_for_update()
    matches = session.scalars(query.limit(2)).all()
    if not matches:
        raise NotFoundError(f"task '{ident}' not found")
    if len(matches) > 1:
        raise NotFoundError(f"task prefix '{ident}' is ambiguous; use more characters")
    return matches[0]


def list_tasks(
    session: Session,
    *,
    status: TaskStatus | None = None,
    project: str | None = None,
    limit: int = 50,
) -> Sequence[Task]:
    query = (
        select(Task)
        .options(selectinload(Task.sessions))
        .order_by(Task.created_at.desc())
        .limit(limit)
    )
    if status is not None:
        query = query.where(Task.status == status)
    if project is not None:
        query = query.where(Task.project == project)
    return session.scalars(query).all()


def latest_session(task: Task) -> WorkerSession:
    return max(task.sessions, key=lambda s: s.ordinal)


def resume_task(session: Session, ident: str, prompt: str) -> WorkerSession:
    task = get_task(session, ident, lock=True)
    if task.status not in TERMINAL_STATUSES:
        raise RuleViolationError(f"task {task.id} is {task.status}; wait for it to finish first")
    previous = latest_session(task)
    sdk_session = next(
        (
            s.sdk_session_id
            for s in sorted(task.sessions, key=lambda s: -s.ordinal)
            if s.sdk_session_id
        ),
        None,
    )
    if sdk_session is None:
        raise RuleViolationError(
            f"task {task.id} never started an SDK session, so there's nothing to resume"
        )
    if not prompt.strip():
        raise RuleViolationError("the follow-up prompt is empty")
    worker_session = WorkerSession(
        task_id=task.id,
        ordinal=previous.ordinal + 1,
        prompt=prompt,
        status=TaskStatus.QUEUED,
        sdk_session_id=sdk_session,
        state_volume=previous.state_volume,
    )
    session.add(worker_session)
    task.status = TaskStatus.QUEUED
    task.stop_requested = False
    task.finished_at = None
    task.error = None
    session.flush()
    notify(session)
    return worker_session


def _stop(session: Session, task: Task, by: str) -> bool:
    """Returns True if anything was still queued or running."""
    if task.status in TERMINAL_STATUSES:
        return False
    task.stop_requested = True
    now = datetime.now(UTC)
    for worker_session in task.sessions:
        if worker_session.status is TaskStatus.QUEUED:
            worker_session.status = TaskStatus.STOPPED
            worker_session.finished_at = now
        elif worker_session.status is TaskStatus.RUNNING and worker_session.token_jti:
            # Revoke right away; the manager kills the container next.
            revocations.revoke(
                session, worker_session.token_jti, reason=f"task {task.id} stopped", by=by
            )
    if task.status is TaskStatus.QUEUED:
        task.status = TaskStatus.STOPPED
        task.finished_at = now
    return True


def request_stop(session: Session, ident: str, by: str | None = None) -> Task:
    task = get_task(session, ident, lock=True)
    if not _stop(session, task, by or current_user()):
        raise RuleViolationError(f"task {task.id} is already {task.status}")
    session.flush()
    notify(session)
    return task


def stop_all(session: Session, by: str | None = None) -> list[Task]:
    active = session.scalars(
        select(Task)
        .options(selectinload(Task.sessions))
        .where(Task.status.in_([TaskStatus.QUEUED, TaskStatus.RUNNING]))
        .with_for_update()
    ).all()
    stopped = [t for t in active if _stop(session, t, by or current_user())]
    session.flush()
    notify(session)
    return stopped


# --- Session manager bookkeeping ---


def count_running(session: Session) -> int:
    return (
        session.scalar(
            select(func.count())
            .select_from(WorkerSession)
            .where(WorkerSession.status == TaskStatus.RUNNING)
        )
        or 0
    )


def claim_next(session: Session) -> WorkerSession | None:
    """The oldest queued session, locked, marked running (container not started yet)."""
    worker_session = session.scalars(
        select(WorkerSession)
        .options(selectinload(WorkerSession.task))
        .where(WorkerSession.status == TaskStatus.QUEUED)
        .order_by(WorkerSession.created_at)
        .limit(1)
        .with_for_update(skip_locked=True)
    ).first()
    if worker_session is None:
        return None
    now = datetime.now(UTC)
    worker_session.status = TaskStatus.RUNNING
    worker_session.started_at = now
    task = worker_session.task
    task.status = TaskStatus.RUNNING
    task.started_at = task.started_at or now
    session.flush()
    return worker_session


def running_sessions(session: Session) -> Sequence[WorkerSession]:
    return session.scalars(
        select(WorkerSession)
        .options(selectinload(WorkerSession.task))
        .where(WorkerSession.status == TaskStatus.RUNNING)
    ).all()


def add_event(
    session: Session, session_id: uuid.UUID, seq: int, event_type: str, payload: dict[str, Any]
) -> None:
    session.add(SessionEvent(session_id=session_id, seq=seq, type=event_type, payload=payload))
    session.flush()


def events(
    session: Session, session_ids: Sequence[uuid.UUID], *, after_id: int = 0, limit: int = 500
) -> Sequence[SessionEvent]:
    return session.scalars(
        select(SessionEvent)
        .where(SessionEvent.session_id.in_(session_ids), SessionEvent.id > after_id)
        .order_by(SessionEvent.id)
        .limit(limit)
    ).all()
