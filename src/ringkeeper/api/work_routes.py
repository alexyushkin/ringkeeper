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
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import String, cast, func, or_, select
from sqlalchemy.orm import object_session

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
from ringkeeper.services import (
    branches,
    calls,
    cleanup,
    clients,
    interrupted,
    link_offer,
    model_usage,
    paging,
    projects,
    task_files,
    task_result,
    task_support,
    tasks,
    titles,
    undo,
)
from ringkeeper.services.runtime import current_settings
from ringkeeper.vault import get_vault

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
    archived: bool = False
    # Where its tasks went: "client > project" labels, one per project.
    places: list[str] = []


def _like(text: str) -> str:
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


@router.get("/threads")
def list_threads(
    session: SessionDep,
    response: Response,
    limit: Annotated[int, Query(ge=1, le=paging.MOST_PER_PAGE)] = 50,
    archived: bool = False,
    q: Annotated[str | None, Query(max_length=200)] = None,
    client: str | None = None,
    project: str | None = None,
    cursor: Annotated[str | None, Query(max_length=300)] = None,
) -> list[ThreadSummary]:
    """The latest conversations: archived ones only when asked, or when searching
    (a search finds archived ones too, and every message). `client`/`project`: ones with work
    there. The headers say how many there are and how to get the next page."""
    query = select(Thread)
    names = projects.in_scope(session, client, project)
    if names is not None:
        query = query.where(
            Thread.id.in_(select(Task.thread_id).where(Task.project.in_(names)))
            | Thread.id.in_(select(Decision.thread_id).where(Decision.project.in_(names)))
        )
    if q and q.strip():
        pattern = _like(q.strip())
        said = select(Message.thread_id).where(Message.body.ilike(pattern, escape="\\"))
        query = query.where(Thread.title.ilike(pattern, escape="\\") | Thread.id.in_(said))
    else:
        query = query.where(
            Thread.archived_at.is_not(None) if archived else Thread.archived_at.is_(None)
        )
    page = paging.fetch(session, query, Thread.updated_at, Thread.id, limit=limit, cursor=cursor)
    page.tell(response)
    found = page.items
    ids = [t.id for t in found]
    counts: dict[uuid.UUID, dict[str, int]] = {}
    for thread_id, status, count in session.execute(
        select(Task.thread_id, Task.status, func.count())
        .where(Task.thread_id.in_(ids))
        .group_by(Task.thread_id, Task.status)
    ):
        if thread_id is not None:
            counts.setdefault(thread_id, {})[str(status)] = int(count)
    named = clients.names(session)  # as people read them
    owners = {p: named.get(o, o) for p, o in projects.owners(session).items()}
    shown = titles.mapping(session)
    places: dict[uuid.UUID, list[str]] = {}
    for thread_id, name in session.execute(
        select(Task.thread_id, Task.project).where(Task.thread_id.in_(ids)).distinct()
    ):
        if thread_id is not None:
            places.setdefault(thread_id, []).append(_place(owners, name, shown))
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
            pending_approvals=int(pending.get(t.id, 0)), archived=t.archived_at is not None,
            places=sorted(places.get(t.id, [])),
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


def _place(owners: dict[str, str], project: str, shown: dict[str, str]) -> str:
    client = owners.get(project)
    title = shown.get(project) or titles.spoken(project)
    return f"{client} \u203a {title}" if client else title


CARD_CHARS = 2000  # a card shows the start of a result; the task's page shows all of it


def _preview(text: str | None) -> str | None:
    if text is None or len(text) <= CARD_CHARS:
        return text
    end = text.rfind("\n", 0, CARD_CHARS)
    return text[: end if end > 0 else CARD_CHARS].rstrip() + "\n…"


def _task_card(task: Task, owners: dict[str, str] | None = None) -> dict[str, Any]:
    rows = list(task.sessions)
    session = object_session(task)
    if owners is None:
        owners = projects.owners(session) if session is not None else {}
    return {
        "id": str(task.id), "project": task.project, "status": task.status.value,
        "client": owners.get(task.project),
        "kind": task.kind.value if task.kind else None,
        "phase": task.phase.value if task.phase else None, "outcome": task.outcome,
        "summary": _preview(task.summary), "error": task.error, "plan_limit": task.plan_limit,
        "offer": task.offer, "partly_done": task.partly_done,
        # How it ended, from what the gateway ran or refused (services.task_result).
        "ended": task_result.ended(session, task) if session is not None else None,
        # Queued behind another task working in the same project folder.
        "waiting_for_folder": bool(session and tasks.waits_for_folder(session, task)),
        "thread_id": task.thread_id,
        "created_at": task.created_at, "started_at": task.started_at,
        "finished_at": task.finished_at, "turns": sum(s.turns or 0 for s in rows),
        "steps": sum(s.tool_calls for s in rows),  # tool calls, counted as they happen
        "cost_usd": _cost(rows), "sessions": len(rows),
        "archived": task.archived_at is not None,
        "files": len(task.changed_files),  # files it made or changed (GET .../files)
    }  # fmt: skip


@router.get("/tasks")
def list_tasks(
    session: SessionDep,
    response: Response,
    project: str | None = None,
    status: TaskStatus | None = None,
    limit: Annotated[int, Query(ge=1, le=paging.MOST_PER_PAGE)] = 100,
    archived: bool = False,
    q: Annotated[str | None, Query(max_length=200)] = None,
    client: str | None = None,
    cursor: Annotated[str | None, Query(max_length=300)] = None,
    outcome: Literal["broke", "not_done"] | None = None,
    since: datetime | None = None,
    real: bool = False,
) -> list[dict[str, Any]]:
    """The headers say how many there are and how to get the next page. `outcome`, `since` and
    `real` are what Home's last-day counts link to: what broke or wasn't done, since then, of
    the owner's own work (not Check my setup's or the demo's)."""
    page = tasks.page_of_tasks(
        session, status=status, limit=limit, archived=archived, cursor=cursor,
        search=_like(q.strip()) if q and q.strip() else None,
        projects=projects.in_scope(session, client, project),
        statuses=(
            tasks.BROKE if outcome == "broke"
            else frozenset({TaskStatus.NOT_DONE}) if outcome == "not_done" else None
        ),
        since=since, real=real,
    )  # fmt: skip
    page.tell(response)
    owners = projects.owners(session)
    return [_task_card(t, owners) for t in page.items]


@router.get("/tasks/interrupted")
def interrupted_tasks(session: SessionDep) -> list[dict[str, Any]]:
    """Tasks a restart or an update cut short, newest first (Home: Resume, Resume all)."""
    owners = projects.owners(session)
    return [{**_task_card(t, owners), "prompt": t.prompt} for t in interrupted.listed(session)]


@router.post("/tasks/interrupted/resume")
def resume_all_interrupted(session: WriteSessionDep) -> dict[str, int]:
    return {"resumed": interrupted.resume_all(session, current_settings())}


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
        "summary": task.summary,  # all of it, not the card's preview
        "prompt": task.prompt, "risk": task.risk.value if task.risk else None,
        "complexity": task.complexity.value if task.complexity else None,
        "branch": task.git_branch, "commit": task.git_commit, "branch_state": task.branch_state,
        "folder_note": task.folder_note,
        "changed_files": task.changed_files, "auth_mode": task.auth_mode.value,
        "review_round": task.review_round, "stop_requested": task.stop_requested,
        "diff": (diff.payload.get("text") if diff else None),
        "tokens": model_usage.totals(session, [s.id for s in rows]),
        "session_list": [
            {
                "id": str(s.id), "ordinal": s.ordinal, "role": s.role.value, "model": s.model,
                "status": s.status.value, "turns": s.turns, "steps": s.tool_calls,
                "cost_usd": s.cost_usd,
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


Ids = Annotated[list[str], Field(min_length=1, max_length=500)]


class ArchiveIn(Input):
    ids: Ids
    archived: bool = True


class DeleteIn(Input):
    ids: Ids
    delete_branches: bool = False  # branches with unmerged work are kept...
    unmerged_too: bool = False  # ...unless this is set (one task, ticked by the user)


@router.post("/tasks/archive")
def archive_tasks(data: ArchiveIn, session: WriteSessionDep) -> dict[str, int]:
    return {"changed": cleanup.archive(session, "task", data.ids, data.archived, by=OWNER)}


class ArchiveOldIn(Input):
    days: Annotated[int, Field(ge=1, le=365)] = 7


@router.get("/cleanup/old-tasks")
def old_unfinished_tasks(
    session: SessionDep, days: Annotated[int, Query(ge=1, le=365)] = 7
) -> dict[str, int]:
    """How many failed or stopped tasks ended more than `days` ago, not archived yet."""
    return {"count": len(cleanup.old_unfinished(session, days)), "days": days}


@router.post("/cleanup/old-tasks")
def archive_old_tasks(data: ArchiveOldIn, session: WriteSessionDep) -> dict[str, int]:
    return {"changed": cleanup.archive_old_unfinished(session, data.days, by=OWNER)}


@router.post("/threads/archive")
def archive_threads(data: ArchiveIn, session: WriteSessionDep) -> dict[str, int]:
    return {"changed": cleanup.archive(session, "thread", data.ids, data.archived, by=OWNER)}


@router.get("/tasks/{task_id}/branch")
def task_branch(task_id: str, session: SessionDep) -> dict[str, Any]:
    """The task's git branch: whether it's there and how much isn't merged (null if
    Ringkeeper can't reach the project's folder from here)."""
    task = tasks.get_task(session, task_id)
    return {"branch": cleanup.branch_info(session, current_settings(), task)}


@router.post("/tasks/delete")
def delete_tasks(data: DeleteIn, session: WriteSessionDep) -> dict[str, Any]:
    branches = cleanup.delete_tasks(
        session, current_settings(), data.ids, delete_branches=data.delete_branches,
        unmerged_too=data.unmerged_too and len(data.ids) == 1, by=OWNER,
    )  # fmt: skip
    return {"deleted": len(branches), "branches": branches}


@router.post("/threads/delete")
def delete_threads(data: DeleteIn, session: WriteSessionDep) -> dict[str, int]:
    removed = cleanup.delete_threads(
        session, current_settings(), data.ids, delete_branches=data.delete_branches, by=OWNER
    )
    return {"deleted": len(data.ids), "tasks": removed}


@router.post("/tasks/{task_id}/link-and-retry", status_code=201)
def link_and_retry(task_id: str, session: WriteSessionDep) -> dict[str, Any]:
    """Link the connection a task was missing to its project, and run it again."""
    return _task_card(link_offer.take(session, current_settings(), task_id))


@router.post("/tasks/{task_id}/run-again", status_code=201)
def run_again(task_id: str, session: WriteSessionDep) -> dict[str, Any]:
    """A task not done for want of a connection, run again once one is added and linked."""
    return _task_card(link_offer.run_again(session, current_settings(), task_id))


@router.post("/tasks/{task_id}/resume-interrupted")
def resume_interrupted(task_id: str, session: WriteSessionDep) -> dict[str, Any]:
    interrupted.resume(session, current_settings(), task_id)
    return _task_card(tasks.get_task(session, task_id))


class FileIn(BaseModel):
    path: str = Field(max_length=1000)
    reveal: bool = False  # show it in its folder instead of opening it


@router.get("/tasks/{task_id}/files")
def task_files_of(task_id: str, session: SessionDep) -> list[dict[str, Any]]:
    """The files the task made or changed, where each is now."""
    task = tasks.get_task(session, task_id)
    return [
        {
            "path": f.path, "size": f.size, "on_branch": f.on_branch, "runs": f.runs,
            "can_open": current_settings().mode == "desktop" and f.size is not None,
        }
        for f in task_files.listed(session, current_settings(), task)
    ]  # fmt: skip


@router.post("/tasks/{task_id}/files/open")
def open_task_file(task_id: str, data: FileIn, session: SessionDep) -> dict[str, bool]:
    """Desktop: open a file the task made in its app, or show it in its folder (anything but
    a document is only ever shown). A file only on the task's branch opens from a copy."""
    settings = current_settings()
    if settings.mode != "desktop":
        raise HTTPException(
            422, detail="Files open on the computer Ringkeeper runs on: only in the desktop app."
        )
    task = tasks.get_task(session, task_id)
    task_files.show(task_files.local_file(session, settings, task, data.path), reveal=data.reveal)
    return {"ok": True}


@router.post("/tasks/{task_id}/keep")
def keep_changes(task_id: str, session: WriteSessionDep) -> dict[str, Any]:
    """Merge the task's branch into the project's default branch."""
    said = branches.keep(session, current_settings(), task_id)
    return {"said": said, "task": _task_card(tasks.get_task(session, task_id))}


@router.post("/tasks/{task_id}/discard")
def discard_changes(task_id: str, session: WriteSessionDep) -> dict[str, Any]:
    """Delete the task's branch and its changes."""
    said = branches.discard(session, current_settings(), task_id)
    return {"said": said, "task": _task_card(tasks.get_task(session, task_id))}


@router.post("/tasks/{task_id}/stop")
def stop_task(task_id: str, session: WriteSessionDep) -> dict[str, Any]:
    return _task_card(tasks.request_stop(session, task_id, by=OWNER))


@router.post("/tasks/{task_id}/resume")
def resume_task(task_id: str, data: ResumeIn, session: WriteSessionDep) -> dict[str, Any]:
    tasks.resume_task(session, current_settings(), task_id, data.prompt)
    return _task_card(tasks.get_task(session, task_id))


# --- Activity (the audit log) ---


def _audit_query(
    names: list[str] | None,
    instance: str | None,
    outcome: AuditOutcome | None,
    since: datetime | None,
    until: datetime | None,
    search: str | None = None,
) -> Any:
    query = select(AuditEvent).order_by(AuditEvent.at.desc(), AuditEvent.id.desc())
    if search is not None:
        said = (AuditEvent.reason, AuditEvent.tool, AuditEvent.instance, AuditEvent.project,
                AuditEvent.result, cast(AuditEvent.args, String))  # fmt: skip
        query = query.where(or_(*(c.ilike(search, escape="\\") for c in said)))
    if names is not None:
        query = query.where(AuditEvent.project.in_(names))
    if instance is not None:
        query = query.where(AuditEvent.instance == instance)
    if outcome is not None:
        query = query.where(AuditEvent.outcome == outcome)
    if since is not None:
        query = query.where(AuditEvent.at >= since)
    if until is not None:
        query = query.where(AuditEvent.at < until)
    return query


def _audit_row(event: AuditEvent, owners: dict[str, str] | None = None) -> dict[str, Any]:
    return {
        "id": event.id, "at": event.at, "project": event.project,
        "client": (owners or {}).get(event.project or ""), "instance": event.instance,
        "service": event.service.value if event.service else None, "tool": event.tool,
        "outcome": event.outcome.value, "reason": event.reason, "args": event.args,
        "result": event.result, "session_id": event.session_id,
        "decision_id": str(event.decision_id) if event.decision_id else None,
    }  # fmt: skip


@router.get("/audit")
def list_audit(
    session: SessionDep,
    response: Response,
    project: str | None = None,
    instance: str | None = None,
    outcome: AuditOutcome | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    before_id: int | None = None,
    limit: Annotated[int, Query(ge=1, le=paging.MOST_PER_PAGE)] = 100,
    client: str | None = None,
    q: Annotated[str | None, Query(max_length=200)] = None,
    cursor: Annotated[str | None, Query(max_length=300)] = None,
) -> list[dict[str, Any]]:
    """Newest first. `q` searches what happened, what was sent and what came back. The headers
    say how many there are and how to get the next page."""
    names = projects.in_scope(session, client, project)
    search = _like(q.strip()) if q and q.strip() else None
    query = _audit_query(names, instance, outcome, since, until, search)
    if before_id is not None:
        query = query.where(AuditEvent.id < before_id)
    page = paging.fetch(session, query, AuditEvent.at, AuditEvent.id, limit=limit, cursor=cursor)
    page.tell(response)
    owners = projects.owners(session)
    return [_audit_row(e, owners) for e in page.items]


MAX_EXPORT_ROWS = 50_000


@router.get("/audit.csv")
def export_audit(
    session: SessionDep,
    project: str | None = None,
    instance: str | None = None,
    outcome: AuditOutcome | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    client: str | None = None,
    q: Annotated[str | None, Query(max_length=200)] = None,
) -> StreamingResponse:
    """The audit log as CSV, e.g. per client (`?project=`). Arguments are left out.
    At most MAX_EXPORT_ROWS rows, newest first: narrow it with since/until for more."""
    names = projects.in_scope(session, client, project)
    search = _like(q.strip()) if q and q.strip() else None
    query = _audit_query(names, instance, outcome, since, until, search).limit(MAX_EXPORT_ROWS)
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


# --- Undo (services.undo) ---


@router.get("/tasks/{task_id}/changes")
def task_changes(task_id: str, session: SessionDep) -> list[dict[str, Any]]:
    """What the task changed: each with Revert (for 7 days) or why it can't be undone."""
    task = tasks.get_task(session, task_id)
    return undo.for_tasks(session, [str(task.id)])


@router.get("/threads/{thread_id}/changes")
def thread_changes(thread_id: uuid.UUID, session: SessionDep) -> list[dict[str, Any]]:
    ids = session.scalars(select(Task.id).where(Task.thread_id == thread_id)).all()
    return undo.for_tasks(session, [str(i) for i in ids])


@router.post("/changes/{change_id}/revert", status_code=201)
async def revert_change(change_id: str) -> dict[str, str]:
    """Ask to put a change back: it waits for approval like any held action."""
    decision = await calls.request_revert(change_id, get_vault())
    return {"decision_id": str(decision.id)}


@router.get("/tasks/{task_id}/support")
def task_support_details(task_id: str, session: SessionDep) -> dict[str, Any]:
    """How a task ran, for support: no prompt, result, project or client (services.task_support)."""
    return task_support.support_details(session, task_id)
