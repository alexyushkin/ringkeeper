"""Threads, tasks (with their transcripts) and the activity log, for the web app.

Needs the admin token and a signed-in owner. Transcripts are the scrubbed
session events; they are deleted after `retention_days`, while the task's
summary, decisions and the audit log stay.
"""

import csv
import io
import uuid
from collections.abc import Iterator
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import func, select

from ringkeeper.api.auth import AdminDep
from ringkeeper.api.deps import OWNER, SessionDep, WriteSessionDep, require_owner
from ringkeeper.models import (
    AuditEvent,
    AuditOutcome,
    Decision,
    DecisionStatus,
    Message,
    MessageRole,
    SessionEvent,
    Task,
    TaskStatus,
    Thread,
    WorkerSession,
)
from ringkeeper.schemas import Input, NonEmpty
from ringkeeper.services import tasks
from ringkeeper.services.runtime import current_settings

router = APIRouter(dependencies=[AdminDep, Depends(require_owner)])

CSV_COLUMNS = (
    "at", "project", "instance", "service", "tool", "outcome", "reason", "session_id",
    "decision_id",
)  # fmt: skip


# --- Threads ---


class ThreadSummary(BaseModel):
    id: uuid.UUID
    title: str
    source: str
    updated_at: datetime
    last_message: str | None
    tasks: dict[str, int]  # status -> count
    pending_approvals: int


@router.get("/threads")
def list_threads(
    session: SessionDep, limit: Annotated[int, Query(ge=1, le=200)] = 50
) -> list[ThreadSummary]:
    found = session.scalars(select(Thread).order_by(Thread.updated_at.desc()).limit(limit)).all()
    ids = [t.id for t in found]
    counts: dict[uuid.UUID, dict[str, int]] = {}
    for thread_id, status, count in session.execute(
        select(Task.thread_id, Task.status, func.count())
        .where(Task.thread_id.in_(ids))
        .group_by(Task.thread_id, Task.status)
    ):
        if thread_id is not None:
            counts.setdefault(thread_id, {})[str(status)] = int(count)
    pending: dict[uuid.UUID | None, int] = {
        thread_id: int(count)
        for thread_id, count in session.execute(
            select(Decision.thread_id, func.count())
            .where(Decision.thread_id.in_(ids), Decision.status == DecisionStatus.PENDING)
            .group_by(Decision.thread_id)
        )
    }
    # Each thread's latest user message (a window function: works on both databases).
    newest = (
        select(
            Message.thread_id,
            Message.body,
            func.row_number()
            .over(partition_by=Message.thread_id, order_by=Message.created_at.desc())
            .label("rank"),
        )
        .where(Message.thread_id.in_(ids), Message.role == MessageRole.USER)
        .subquery()
    )
    last: dict[uuid.UUID, str] = {}
    for thread_id, body in session.execute(
        select(newest.c.thread_id, newest.c.body).where(newest.c.rank == 1)
    ):
        last[thread_id] = body[:200]
    return [
        ThreadSummary(
            id=t.id, title=t.title, source=t.source, updated_at=t.updated_at,
            last_message=last.get(t.id), tasks=counts.get(t.id, {}),
            pending_approvals=int(pending.get(t.id, 0)),
        )
        for t in found
    ]  # fmt: skip


@router.get("/threads/{thread_id}/tasks")
def thread_tasks(thread_id: uuid.UUID, session: SessionDep) -> list[dict[str, Any]]:
    found = session.scalars(
        select(Task).where(Task.thread_id == thread_id).order_by(Task.created_at)
    ).all()
    return [_task_card(t) for t in found]


# --- Tasks ---


def _cost(rows: list[WorkerSession]) -> float | None:
    costs = [s.cost_usd for s in rows if s.cost_usd is not None]
    return round(sum(costs), 6) if costs else None


def _task_card(task: Task) -> dict[str, Any]:
    rows = list(task.sessions)
    return {
        "id": str(task.id), "project": task.project, "status": task.status.value,
        "kind": task.kind.value if task.kind else None,
        "phase": task.phase.value if task.phase else None, "outcome": task.outcome,
        "summary": task.summary, "error": task.error, "thread_id": task.thread_id,
        "created_at": task.created_at, "started_at": task.started_at,
        "finished_at": task.finished_at, "turns": sum(s.turns or 0 for s in rows),
        "cost_usd": _cost(rows), "sessions": len(rows),
    }  # fmt: skip


@router.get("/tasks")
def list_tasks(
    session: SessionDep,
    project: str | None = None,
    status: TaskStatus | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[dict[str, Any]]:
    return [
        _task_card(t)
        for t in tasks.list_tasks(session, status=status, project=project, limit=limit)
    ]


@router.get("/tasks/{task_id}")
def get_task(task_id: str, session: SessionDep) -> dict[str, Any]:
    task = tasks.get_task(session, task_id)
    rows = sorted(task.sessions, key=lambda s: s.ordinal)
    diff = session.scalars(
        select(SessionEvent)
        .where(
            SessionEvent.session_id.in_([s.id for s in rows]),
            SessionEvent.type == "ringkeeper.diff",
        )
        .order_by(SessionEvent.id.desc())
        .limit(1)
    ).one_or_none()
    return {
        **_task_card(task),
        "prompt": task.prompt, "risk": task.risk.value if task.risk else None,
        "complexity": task.complexity.value if task.complexity else None,
        "branch": task.git_branch, "commit": task.git_commit,
        "changed_files": task.changed_files, "auth_mode": task.auth_mode.value,
        "review_round": task.review_round, "stop_requested": task.stop_requested,
        "diff": (diff.payload.get("text") if diff else None),
        "session_list": [
            {
                "id": str(s.id), "ordinal": s.ordinal, "role": s.role.value, "model": s.model,
                "status": s.status.value, "turns": s.turns, "cost_usd": s.cost_usd,
                "prompt": s.prompt, "checks": s.checks, "action": s.action,
                "result": s.result_subtype, "error": s.error, "created_at": s.created_at,
            }
            for s in rows
        ],
    }  # fmt: skip


@router.get("/tasks/{task_id}/events")
def task_events(
    task_id: str,
    session: SessionDep,
    after: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=2000)] = 500,
) -> dict[str, Any]:
    """The transcript, in order. Page with `after` = the last event id you have."""
    task = tasks.get_task(session, task_id)
    by_id = {s.id: s for s in task.sessions}
    found = tasks.events(session, list(by_id), after_id=after, limit=limit)
    return {
        "events": [
            {
                "id": e.id, "session": by_id[e.session_id].ordinal,
                "role": by_id[e.session_id].role.value, "seq": e.seq, "at": e.at,
                "type": e.type, "payload": e.payload,
            }
            for e in found
        ],
        "retention_days": current_settings().retention_days,
    }  # fmt: skip


class ResumeIn(Input):
    prompt: NonEmpty


@router.post("/tasks/{task_id}/stop")
def stop_task(task_id: str, session: WriteSessionDep) -> dict[str, Any]:
    return _task_card(tasks.request_stop(session, task_id, by=OWNER))


@router.post("/tasks/{task_id}/resume")
def resume_task(task_id: str, data: ResumeIn, session: WriteSessionDep) -> dict[str, Any]:
    tasks.resume_task(session, current_settings(), task_id, data.prompt)
    return _task_card(tasks.get_task(session, task_id))


# --- Activity (the audit log) ---


def _audit_query(
    project: str | None,
    instance: str | None,
    outcome: AuditOutcome | None,
    since: datetime | None,
    until: datetime | None,
) -> Any:
    query = select(AuditEvent).order_by(AuditEvent.at.desc(), AuditEvent.id.desc())
    if project is not None:
        query = query.where(AuditEvent.project == project)
    if instance is not None:
        query = query.where(AuditEvent.instance == instance)
    if outcome is not None:
        query = query.where(AuditEvent.outcome == outcome)
    if since is not None:
        query = query.where(AuditEvent.at >= since)
    if until is not None:
        query = query.where(AuditEvent.at < until)
    return query


def _audit_row(event: AuditEvent) -> dict[str, Any]:
    return {
        "id": event.id, "at": event.at, "project": event.project, "instance": event.instance,
        "service": event.service.value if event.service else None, "tool": event.tool,
        "outcome": event.outcome.value, "reason": event.reason, "args": event.args,
        "result": event.result, "session_id": event.session_id,
        "decision_id": str(event.decision_id) if event.decision_id else None,
    }  # fmt: skip


@router.get("/audit")
def list_audit(
    session: SessionDep,
    project: str | None = None,
    instance: str | None = None,
    outcome: AuditOutcome | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    before_id: int | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[dict[str, Any]]:
    query = _audit_query(project, instance, outcome, since, until)
    if before_id is not None:
        query = query.where(AuditEvent.id < before_id)
    return [_audit_row(e) for e in session.scalars(query.limit(limit))]


MAX_EXPORT_ROWS = 50_000


@router.get("/audit.csv")
def export_audit(
    session: SessionDep,
    project: str | None = None,
    instance: str | None = None,
    outcome: AuditOutcome | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
) -> StreamingResponse:
    """The audit log as CSV, e.g. per client (`?project=`). Arguments are left out.
    At most MAX_EXPORT_ROWS rows, newest first: narrow it with since/until for more."""
    query = _audit_query(project, instance, outcome, since, until).limit(MAX_EXPORT_ROWS)
    rows = session.scalars(query).all()

    def lines() -> Iterator[str]:
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(CSV_COLUMNS)
        for event in rows:
            row = _audit_row(event)
            writer.writerow([_csv_safe(row[c]) for c in CSV_COLUMNS])
            yield buffer.getvalue()
            buffer.seek(0)
            buffer.truncate()
        yield buffer.getvalue()

    name = f"ringkeeper-activity-{project or 'all'}.csv"
    return StreamingResponse(
        lines(), media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )  # fmt: skip


def _csv_safe(value: object) -> str:
    """Spreadsheets run cells starting with = + - @ as formulas; prefix those with '."""
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text
