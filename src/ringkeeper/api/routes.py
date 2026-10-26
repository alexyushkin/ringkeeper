"""HTTP endpoints. Responses use the *Out schemas, which have no secret_ref.

Everything but /health needs the admin token (`public` vs `router`).
"""

from typing import Literal

from fastapi import APIRouter, Response
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

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
from ringkeeper.services import bindings, decisions, inbox, instances, projects, threads

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


@router.post("/messages", status_code=202)
def post_message(data: MessageIn, session: WriteSessionDep) -> MessageAccepted:
    """Queue a message for the router. Its reply lands in the thread."""
    message = threads.post_message(
        session, data.body, author=data.author, source=data.source, thread=data.thread_id,
        input_kind=data.input,
    )  # fmt: skip
    return MessageAccepted(message_id=str(message.id), thread_id=str(message.thread_id))


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


def _decision_out(decision: Decision) -> DecisionOut:
    return DecisionOut.model_validate(
        {
            **{c: getattr(decision, c) for c in DecisionOut.model_fields if hasattr(decision, c)},
            "kind": decision.kind.value,
            "status": decision.status.value,
            "summary": inbox.summary(decision),
            "heading": inbox.heading(decision),
            "options": inbox.options(decision),
        }
    )


@router.get("/decisions")
def list_decisions(
    session: WriteSessionDep,
    status: DecisionStatus | None = DecisionStatus.PENDING,
    kind: DecisionKind | None = None,
    project: str | None = None,
    limit: int = 50,
) -> list[DecisionOut]:
    # A write session: listing marks stale decisions expired first.
    found = inbox.list_decisions(
        session, status=status, kind=kind, project=project, limit=min(max(limit, 1), 1000)
    )
    return [_decision_out(d) for d in found]


@router.get("/decisions/{decision_id}")
def get_decision(decision_id: str, session: SessionDep) -> DecisionOut:
    return _decision_out(decisions.get_decision(session, decision_id))


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
