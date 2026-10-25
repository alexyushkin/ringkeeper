"""Tasks and worker sessions: creation, lookup, stop requests, and the manager's bookkeeping.

The CLI never talks to Docker. It writes tasks and stop requests here and
pokes the session manager with NOTIFY; the manager (in the api service)
does the rest.
"""

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import String, cast, func, select, update
from sqlalchemy.orm import Session, selectinload

from ringkeeper.config import Settings
from ringkeeper.errors import NotFoundError, RuleViolationError
from ringkeeper.model_choice import session_model
from ringkeeper.models import (
    RESUMABLE_STATUSES,
    TERMINAL_STATUSES,
    ArchitectPhase,
    AuthMode,
    Complexity,
    Decision,
    DecisionStatus,
    SessionEvent,
    SessionRole,
    Task,
    TaskKind,
    TaskRisk,
    TaskStatus,
    WorkerSession,
)
from ringkeeper.services import projects, revocations
from ringkeeper.services.architect import brief
from ringkeeper.services.decisions import MIN_ID_PREFIX, current_user
from ringkeeper.storage import events as storage_events

NOTIFY_CHANNEL = "ringkeeper_tasks"
# Not finished, but no container running: stopping just marks them stopped.
IDLE_STATUSES = (TaskStatus.QUEUED, TaskStatus.NEEDS_INPUT, TaskStatus.WAITING)


def notify(session: Session) -> None:
    """Wake the session manager (delivered when the transaction commits)."""
    storage_events.notify(session, NOTIFY_CHANNEL)


def state_volume_name(task_id: uuid.UUID, role: SessionRole) -> str:
    """The SDK state (transcripts for resume) for one role of one task: an architect
    session (which reads but never writes the workspace) and a worker session never
    share files."""
    return f"ringkeeper-state-{task_id}-{role.value}"


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
    kind: TaskKind | None = None,
    risk: TaskRisk | None = None,
    complexity: Complexity | None = None,
    outcome: str | None = None,
    thread_id: uuid.UUID | None = None,
    message_id: uuid.UUID | None = None,
) -> Task:
    """Queue a task. A feature starts with the architect (plan first), anything
    else with a worker."""
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
        kind=kind,
        risk=risk,
        complexity=complexity,
        outcome=outcome,
        thread_id=thread_id,
        message_id=message_id,
        phase=ArchitectPhase.PLANNING if kind is TaskKind.FEATURE else None,
    )
    session.add(task)
    session.flush()
    role = SessionRole.ARCHITECT if kind is TaskKind.FEATURE else SessionRole.WORKER
    first_prompt = brief(record.name, prompt, outcome) if role is SessionRole.ARCHITECT else prompt
    task.sessions = [
        WorkerSession(
            task_id=task.id,
            ordinal=1,
            prompt=first_prompt,
            status=TaskStatus.QUEUED,
            role=role,
            model=session_model(settings, record, role, complexity),
            state_volume=state_volume_name(task.id, role),
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


def last_sdk_session(task: Task, role: SessionRole) -> str | None:
    """The SDK session to resume for a role (a feature task has two: architect and worker)."""
    return next(
        (
            s.sdk_session_id
            for s in sorted(task.sessions, key=lambda s: -s.ordinal)
            if s.sdk_session_id and s.role is role
        ),
        None,
    )


def add_session(
    session: Session,
    settings: Settings,
    task: Task,
    prompt: str,
    role: SessionRole,
    *,
    sdk_session_id: str | None = None,
) -> WorkerSession:
    """Queue the next session of a task (a resume, or the next architect-loop step)."""
    worker_session = WorkerSession(
        task_id=task.id,
        ordinal=latest_session(task).ordinal + 1,
        prompt=prompt,
        status=TaskStatus.QUEUED,
        role=role,
        model=session_model(
            settings,
            projects.find_project(session, task.project),
            role,
            task.complexity,
            task.failed_check_rounds,
        ),
        sdk_session_id=sdk_session_id,
        # Tasks from before per-role volumes keep the volume their transcripts are on.
        state_volume=next(
            (
                s.state_volume
                for s in sorted(task.sessions, key=lambda s: -s.ordinal)
                if s.role is role
            ),
            state_volume_name(task.id, role),
        ),
    )
    session.add(worker_session)
    task.sessions.append(worker_session)
    task.status = TaskStatus.QUEUED
    task.stop_requested = False
    task.finished_at = None
    task.error = None
    session.flush()
    notify(session)
    return worker_session


def resume_task(session: Session, settings: Settings, ident: str, prompt: str) -> WorkerSession:
    """Continue the latest session's SDK conversation with a follow-up prompt."""
    task = get_task(session, ident, lock=True)
    if task.status not in RESUMABLE_STATUSES:
        raise RuleViolationError(f"task {task.id} is {task.status}; wait for it to finish first")
    role = latest_session(task).role
    sdk_session = last_sdk_session(task, role)
    if sdk_session is None:
        raise RuleViolationError(
            f"task {task.id} never started an SDK session, so there's nothing to resume"
        )
    if not prompt.strip():
        raise RuleViolationError("the follow-up prompt is empty")
    return add_session(session, settings, task, prompt, role, sdk_session_id=sdk_session)


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
    if task.status in IDLE_STATUSES:
        task.status = TaskStatus.STOPPED
        task.finished_at = now
    # Nothing left to decide for a stopped task.
    session.execute(
        update(Decision)
        .where(Decision.task_id == task.id, Decision.status == DecisionStatus.PENDING)
        .values(status=DecisionStatus.DENIED, decided_at=now, decided_by=by, reason="task stopped")
        .execution_options(synchronize_session=False)
    )
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
        .where(Task.status.in_([TaskStatus.RUNNING, *IDLE_STATUSES]))
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
