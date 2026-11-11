"""Tasks and worker sessions: creation, lookup, stop requests, and the manager's bookkeeping.

The CLI never talks to Docker. It writes tasks and stop requests here and
pokes the session manager with NOTIFY; the manager (in the api service)
does the rest.
"""

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import String, cast, func, or_, select, update
from sqlalchemy.orm import Session, selectinload

from ringkeeper.config import Settings
from ringkeeper.errors import NotFoundError, RingkeeperError, RuleViolationError
from ringkeeper.model_choice import session_model
from ringkeeper.models import (
    RESUMABLE_STATUSES,
    TERMINAL_STATUSES,
    ArchitectPhase,
    AuthMode,
    Complexity,
    Decision,
    DecisionStatus,
    Message,
    Project,
    SessionEvent,
    SessionRole,
    Task,
    TaskKind,
    TaskRisk,
    TaskStatus,
    Thread,
    WorkerSession,
)
from ringkeeper.services import attachments, client_month, paging, projects, revocations
from ringkeeper.services.architect import brief
from ringkeeper.services.decisions import MIN_ID_PREFIX, TASKS_CHANNEL, current_user
from ringkeeper.services.runtime import effective as effective_settings
from ringkeeper.storage import events as storage_events
from ringkeeper.wording import task_status

NOTIFY_CHANNEL = TASKS_CHANNEL
# Not finished, but no container running: stopping just marks them stopped.
IDLE_STATUSES = (TaskStatus.QUEUED, TaskStatus.NEEDS_INPUT, TaskStatus.WAITING)


def agent_workspace(settings: Settings, workspace_path: str | None) -> str:
    """The workspace as the agent sees it: /workspace in a container, the real
    folder for a local process (desktop)."""
    if settings.worker_runtime == "docker":
        return "/workspace"
    from ringkeeper.workers.manager import resolve_workspace  # noqa: PLC0415 (cycle)

    try:
        return resolve_workspace(workspace_path, settings.workspaces_root)
    except RingkeeperError:
        return "the project's folder (your current directory)"


def planned(kind: TaskKind | None, complexity: Complexity | None) -> bool:
    """Whether a task goes through the architect loop (plan, approval, build, review).

    Only features that aren't low complexity: a small, clear change goes straight
    to a worker on the cheap tier, since planning and reviewing it would cost more
    than the change itself.
    """
    return kind is TaskKind.FEATURE and complexity is not Complexity.LOW


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


def task_branch(session: Session, task: Task) -> str:
    """The branch a task works on: its own, or, for a follow-up in the same conversation and
    project, the earlier task's (not yet kept or discarded), so it sees that work."""
    if task.git_branch:
        return task.git_branch
    if task.thread_id is not None:
        earlier = session.scalar(
            select(Task.git_branch)
            .where(
                Task.thread_id == task.thread_id,
                Task.project == task.project,
                Task.id != task.id,
                Task.git_branch.is_not(None),
                Task.branch_state.is_(None),
                Task.created_at <= task.created_at,
            )
            .order_by(Task.created_at.desc())
            .limit(1)
        )
        if earlier:
            return earlier
    return branch_name(task.id)


def _attached(
    session: Session, settings: Settings, project: Project, message_id: uuid.UUID | None
) -> str:
    """The request's files, copied into the project: a line for the worker's prompt."""
    message = session.get(Message, message_id) if message_id else None
    if message is None or not message.attachments:
        return ""
    from ringkeeper.workers.manager import resolve_workspace  # noqa: PLC0415 (cycle)

    try:
        folder = resolve_workspace(project.workspace_path, settings.workspaces_root)
        copied = attachments.bring_in(settings, message.attachments, folder)
    except (OSError, RingkeeperError):
        return "\n\nThe files attached to this request couldn't be copied into this project."
    return f"\n\nFiles attached to this request: {', '.join(copied)}" if copied else ""


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
    agent_id: uuid.UUID | None = None,
    general: bool = False,
) -> Task:
    """Queue a task. A feature starts with the architect (plan first), unless it's
    small and clear (see `planned`); anything else starts with a worker."""
    # The Settings screen's choices (subscription mode, the spending cap), whoever
    # creates the task: the router, an answer in the app or Telegram, the CLI.
    settings = effective_settings(session, settings)
    record = projects.get_project(session, project)
    if not prompt.strip():
        raise RuleViolationError("Type what the task should do first.")
    prompt += _attached(session, settings, record, message_id)
    mode = settings.claude_auth_mode
    if budget_usd is not None and mode is not AuthMode.API_KEY:
        raise RuleViolationError(
            "A spending cap only applies when Claude is connected with an API key "
            "(Settings > Claude): a subscription isn't billed per task."
        )
    if budget_usd is not None and budget_usd <= 0:
        raise RuleViolationError("The spending cap must be more than $0.")
    if budget_usd is None and mode is AuthMode.API_KEY:
        budget_usd = settings.task_budget_usd  # paused and asked at the cap
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
        phase=ArchitectPhase.PLANNING if planned(kind, complexity) else None,
        agent_id=agent_id,
        general=general,
    )
    session.add(task)
    session.flush()
    role = SessionRole.ARCHITECT if task.phase is not None else SessionRole.WORKER
    first_prompt = (
        brief(record.name, prompt, outcome, agent_workspace(settings, record.workspace_path))
        if role is SessionRole.ARCHITECT
        else prompt
    )
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
    # Over the client's monthly cap it waits for the user: run it this once, or raise the cap.
    if not client_month.ask_over_cap(session, settings, task):
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
        raise NotFoundError("That task isn't here any more. Go back to Tasks.")
    if len(matches) > 1:
        raise NotFoundError(f"task prefix '{ident}' is ambiguous; use more characters")
    return matches[0]


def list_tasks(
    session: Session,
    *,
    status: TaskStatus | None = None,
    project: str | None = None,
    limit: int = 50,
    archived: bool = False,
    search: str | None = None,
    projects: list[str] | None = None,
) -> Sequence[Task]:
    return page_of_tasks(
        session, status=status, project=project, limit=limit, archived=archived, search=search,
        projects=projects,
    ).items  # fmt: skip


def page_of_tasks(
    session: Session,
    *,
    status: TaskStatus | None = None,
    project: str | None = None,
    limit: int = 50,
    archived: bool = False,
    search: str | None = None,
    projects: list[str] | None = None,
    cursor: str | None = None,
) -> paging.Page[Task]:
    """`search`: a LIKE pattern over the request, outcome, summary, project and the words of its
    conversation; it finds archived tasks too. Otherwise archived ones are listed only when
    asked."""
    query = select(Task)
    if search is not None:
        fields = (Task.prompt, Task.outcome, Task.summary, Task.project)
        said = select(Message.thread_id).where(Message.body.ilike(search, escape="\\"))
        titled = select(Thread.id).where(Thread.title.ilike(search, escape="\\"))
        query = query.where(
            or_(
                *(f.ilike(search, escape="\\") for f in fields),
                Task.thread_id.in_(said),
                Task.thread_id.in_(titled),
            )
        )
    else:
        query = query.where(
            Task.archived_at.is_not(None) if archived else Task.archived_at.is_(None)
        )
    if status is not None:
        query = query.where(Task.status == status)
    if project is not None:
        query = query.where(Task.project == project)
    if projects is not None:
        query = query.where(Task.project.in_(projects))
    return paging.fetch(
        session, query, Task.created_at, Task.id, limit=limit, cursor=cursor,
        load=(selectinload(Task.sessions),),
    )  # fmt: skip


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
        raise RuleViolationError(
            f"This task is {task_status(task.status)}. Send your follow-up once it's finished."
        )
    role = latest_session(task).role
    sdk_session = last_sdk_session(task, role)
    if sdk_session is None:
        raise RuleViolationError(
            "This task never got started, so it can't be continued. Start a new task instead."
        )
    if not prompt.strip():
        raise RuleViolationError("Type your follow-up first.")
    if client_month.ask_over_cap(session, settings, task, follow_up=prompt, role=role.value):
        return latest_session(task)  # the follow-up waits for the user's answer
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
        raise RuleViolationError(f"This task is already {task_status(task.status)}.")
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


CLAIM_LOOKAHEAD = 50  # queued sessions looked at for one whose folder is free


# Work on the code gets a branch of its own and the folder to itself (one at a time).
# Data and ops work, and general work, use connections: no branch, and no waiting.
NO_BRANCH_KINDS = frozenset({TaskKind.DATA, TaskKind.OPS})


def on_branch(task: Task) -> bool:
    """The task works on the project's code: on its own branch, one at a time per folder."""
    return not task.general and task.kind not in NO_BRANCH_KINDS


def _folder(project: Project | None, name: str) -> str:
    """What two tasks share when they work in the same place: the project's folder."""
    path = (project.workspace_path or "").strip() if project is not None else ""
    return path.replace(chr(92), "/").rstrip("/").lower() or f"project:{name}"


def busy_folders(session: Session) -> set[str]:
    """The folders a running session is working on the code in (on its branch)."""
    rows = session.execute(
        select(Task, Project)
        .join(WorkerSession, WorkerSession.task_id == Task.id)
        .outerjoin(Project, Project.name == Task.project)
        .where(WorkerSession.status == TaskStatus.RUNNING)
    ).all()
    return {_folder(project, task.project) for task, project in rows if on_branch(task)}


def folder_busy(session: Session, project_name: str) -> bool:
    """A session is working in this project's folder now."""
    project = session.scalar(select(Project).where(Project.name == project_name))
    return _folder(project, project_name) in busy_folders(session)


def waits_for_folder(session: Session, task: Task) -> bool:
    """A queued task waiting because another task is working in its project's folder."""
    if task.status is not TaskStatus.QUEUED or not on_branch(task):
        return False
    project = session.scalar(select(Project).where(Project.name == task.project))
    return _folder(project, task.project) in busy_folders(session)


def claim_next(session: Session) -> WorkerSession | None:
    """The oldest queued session that can start, locked and marked running (container not
    started yet). Work on the code waits while another code task is in its project's folder
    (two would switch branches under each other); work that doesn't touch the code never
    waits."""
    busy = busy_folders(session)
    queued = session.scalars(
        select(WorkerSession)
        .options(selectinload(WorkerSession.task))
        .where(WorkerSession.status == TaskStatus.QUEUED)
        .order_by(WorkerSession.created_at)
        .limit(CLAIM_LOOKAHEAD)
        .with_for_update(skip_locked=True)
    ).all()
    places = {p.name: p for p in session.scalars(select(Project))}
    worker_session = next(
        (
            s
            for s in queued
            if not on_branch(s.task)
            or _folder(places.get(s.task.project), s.task.project) not in busy
        ),
        None,
    )
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
