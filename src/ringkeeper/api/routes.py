"""HTTP endpoints. Responses use the *Out schemas, which have no secret_ref.

Everything but /health needs the admin token (`public` vs `router`).
"""

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query, Response
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import object_session

from ringkeeper import claude_failures
from ringkeeper.api.auth import AdminDep
from ringkeeper.api.deps import SessionDep, WriteSessionDep
from ringkeeper.config import get_settings
from ringkeeper.db import get_engine
from ringkeeper.models import Decision, DecisionKind, DecisionStatus, Env, InstanceType
from ringkeeper.schemas import (
    AnswerIn,
    BoundInstanceOut,
    DecisionOut,
    InstanceOut,
    MessageAccepted,
    MessageIn,
    MessageOut,
    ProjectDetailOut,
    ProjectOut,
    ThreadOut,
)
from ringkeeper.services import (
    admin_audit,
    bindings,
    decisions,
    inbox,
    instances,
    mentions,
    projects,
    threads,
    undo,
)

public = APIRouter()
router = APIRouter(dependencies=[AdminDep])


class Health(BaseModel):
    status: Literal["ok", "error"]
    database: Literal["ok", "unreachable"]


@public.get("/health")
def health(response: Response) -> Health:
    try:
        with get_engine().connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError:
        response.status_code = 503
        return Health(status="error", database="unreachable")
    return Health(status="ok", database="ok")


@router.get("/projects")
def list_projects(session: SessionDep) -> list[ProjectOut]:
    return [ProjectOut.model_validate(p) for p in projects.list_projects(session)]


@router.get("/projects/{name}")
def get_project(name: str, session: SessionDep) -> ProjectDetailOut:
    project = projects.get_project(session, name)
    bound = [
        BoundInstanceOut(
            **InstanceOut.model_validate(b.instance).model_dump(),
            is_default=b.is_default,
            allowed_tools=b.allowed_tools,
            preapproved_writes=b.preapproved_writes,
            scope=dict(b.scope or {}),
        )
        for b in bindings.sorted_bindings(project)
    ]
    return ProjectDetailOut(**ProjectOut.model_validate(project).model_dump(), instances=bound)


@router.get("/instances")
def list_instances(
    session: SessionDep, type: InstanceType | None = None, env: Env | None = None
) -> list[InstanceOut]:
    return [
        InstanceOut.model_validate(i) for i in instances.list_instances(session, type=type, env=env)
    ]


@router.get("/mentions")
def list_mentions(session: SessionDep) -> list[dict[str, Any]]:
    """What a request can @-mention: clients, projects, connections and agents."""
    return mentions.offered(session)


@router.post("/messages", status_code=202)
def post_message(data: MessageIn, session: WriteSessionDep) -> MessageAccepted:
    """Queue a message for the router. Its reply lands in the thread."""
    message = threads.post_message(
        session, data.body, author=data.author, source=data.source, thread=data.thread_id,
        input_kind=data.input, attachments=data.attachments, run_at=data.run_at,
        target_client=data.client, target_project=data.project,
    )  # fmt: skip
    return MessageAccepted(message_id=str(message.id), thread_id=str(message.thread_id))


@router.get("/scheduled")
def list_scheduled(session: SessionDep) -> list[dict[str, Any]]:
    """Requests sent for later that haven't run yet, soonest first."""
    return [
        {
            "id": str(m.id), "thread_id": str(m.thread_id), "body": m.body[:300],
            "run_at": m.run_at.isoformat() if m.run_at else None,
        }
        for m in threads.scheduled(session)
    ]  # fmt: skip


@router.post("/messages/{message_id}/retry")
def retry_message(message_id: uuid.UUID, session: WriteSessionDep) -> MessageOut:
    """A request whose routing failed, routed again."""
    return MessageOut.model_validate(threads.retry_routing(session, str(message_id)))


@router.get("/claude-status")
def claude_status(session: SessionDep) -> dict[str, Any]:
    """While Claude's usage limit lasts: when it resets, and what Home says."""
    pause = claude_failures.paused(session)
    if pause is None:
        return {"waiting_until": None, "message": None}
    return {"waiting_until": pause.until.isoformat(), "message": pause.message}


@router.post("/messages/{message_id}/cancel")
def cancel_message(message_id: uuid.UUID, session: WriteSessionDep) -> MessageOut:
    """Stops a request sent for later before it runs."""
    message = threads.cancel(session, message_id)
    admin_audit.record(
        session, "request.cancel", f"canceled a request sent for later: {message.body[:80]}",
        by="owner",
    )  # fmt: skip
    return MessageOut.model_validate(message)


@router.get("/threads/{thread_id}")
def get_thread(thread_id: str, session: SessionDep) -> ThreadOut:
    thread = threads.get_thread(session, thread_id)
    messages = [MessageOut.model_validate(m) for m in threads.thread_messages(session, thread.id)]
    return ThreadOut(
        id=thread.id,
        title=thread.title,
        source=thread.source,
        created_at=thread.created_at,
        messages=messages,
        archived=thread.archived_at is not None,
    )


def _decision_out(decision: Decision, owners: dict[str, str] | None = None) -> DecisionOut:
    session = object_session(decision)
    if owners is None:
        owners = projects.owners(session) if session is not None else {}
    return DecisionOut.model_validate(
        {
            **{c: getattr(decision, c) for c in DecisionOut.model_fields if hasattr(decision, c)},
            "kind": decision.kind.value,
            "status": decision.status.value,
            "client": owners.get(decision.project or ""),
            "production": inbox.production_hold(decision),
            "summary": inbox.summary(decision),
            "heading": inbox.heading(decision),
            "money": inbox.money(decision),
            "trustable": inbox.trustable(decision),
            "declares": inbox.declares(decision),
            "options": inbox.options(decision),
            "approval": inbox.is_approval(decision),
            "undo": undo.note_for(decision) if decision.kind is DecisionKind.APPROVE else None,
        }
    )


@router.get("/decisions")
def list_decisions(
    session: WriteSessionDep,
    response: Response,
    status: DecisionStatus | None = DecisionStatus.PENDING,
    kind: DecisionKind | None = None,
    project: str | None = None,
    limit: int = 50,
    client: str | None = None,
    category: Literal["approval", "question"] | None = None,
    thread: uuid.UUID | None = None,
    task: uuid.UUID | None = None,
    routine: uuid.UUID | None = None,
    snoozed: bool | None = None,
    cursor: Annotated[str | None, Query(max_length=300)] = None,
) -> list[DecisionOut]:
    """Newest first; `client`/`project` narrow it to those projects' decisions.
    `category`: approvals (actions: Approvals and its badge) or questions (asked in
    the conversation). `thread`, `task`, `routine`: only what belongs to it. `snoozed`: only
    snoozed ones (true) or only the rest (false). The headers say how many there are and how
    to get the next page."""
    # A write session: listing marks stale decisions expired first.
    page = inbox.page_of_decisions(
        session, status=status, kind=kind, limit=min(max(limit, 1), 1000), cursor=cursor,
        projects=projects.in_scope(session, client, project),
        approvals=None if category is None else category == "approval",
        thread=thread, task=task, routine=routine, snoozed=snoozed,
    )  # fmt: skip
    page.tell(response)
    owners = projects.owners(session)
    return [_decision_out(d, owners) for d in page.items]


@router.get("/decisions/{decision_id}")
def get_decision(decision_id: str, session: SessionDep) -> DecisionOut:
    return _decision_out(decisions.get_decision(session, decision_id))


class SnoozeIn(BaseModel):
    until: datetime | None  # None: wake it now


@router.post("/decisions/{decision_id}/snooze")
def snooze_decision(decision_id: str, data: SnoozeIn, session: WriteSessionDep) -> DecisionOut:
    """Out of Approvals and its notifications until then; back, and notified again, after."""
    decision = inbox.snooze(session, decision_id, data.until, by="owner")
    return _decision_out(decision)


@router.post("/decisions/{decision_id}/answer")
def answer_decision(decision_id: str, data: AnswerIn, session: WriteSessionDep) -> DecisionOut:
    """Answer a decision. An approved held write is run by the gateway, not here."""
    answered = inbox.answer(
        session,
        get_settings(),
        decision_id,
        inbox.Answer(choice=data.choice, text=data.text, confirm=data.confirm),
        by=f"api:{data.by}",
        via=inbox.Via.API,
    )
    return _decision_out(answered.decision)
