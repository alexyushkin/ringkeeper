"""A short message that answers what this conversation left open.

When Ringkeeper's last word in a conversation asked which project, and the next message
just names one ("Alexey, My Project", "the Acme one"), that's the answer: the original
request runs there, with no model call and no second question. When it asked for a
go-ahead on production, "yes" or "do it" is the go-ahead. When it couldn't place the last
request at all, a message naming a project routes that request there.

Anything else goes to the router's model, which sees the conversation and is told that a
short follow-up continues the open request.
"""

import re
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from ringkeeper.config import Settings
from ringkeeper.models import (
    Client,
    Decision,
    DecisionKind,
    DecisionStatus,
    Message,
    MessageRole,
    Project,
    Task,
)
from ringkeeper.services import clients, inbox, threads, titles

SHORT = 60  # longer than this, it's a request of its own
YES = frozenset({
    "yes", "y", "yep", "yeah", "ok", "okay", "sure", "go", "go ahead", "do it", "please do",
    "yes please", "yes, go ahead", "yes do it", "approved", "run it",
})  # fmt: skip
NO = frozenset({"no", "nope", "don't", "dont", "cancel", "stop", "no thanks", "leave it"})
VIA = {"slack": inbox.Via.SLACK, "discord": inbox.Via.DISCORD, "telegram": inbox.Via.TELEGRAM}


def _said(text: str) -> str:
    return " ".join(text.lower().replace(chr(0x2019), "'").split()).strip(" .!")


def _names(project: Project) -> list[str]:
    found = [project.title, project.name, project.name.replace("-", " "), *project.aliases]
    return [n for n in found if n and len(n) > 1]


def _mentions(text: str, name: str) -> bool:
    return re.search(rf"(?<![\w-]){re.escape(name.lower())}(?![\w-])", text) is not None


def named_project(session: Session, text: str, among: list[str] | None = None) -> str | None:
    """The one project a short message names, by its title, name or alias, or by its
    client's name (that client's default or only project). None: none, or more than one."""
    said = _said(text)
    projects = session.scalars(select(Project).order_by(Project.name)).all()
    if among:
        projects = [p for p in projects if p.name in among]
    owners = {c.slug: c for c in session.scalars(select(Client))}
    hits = {p.name for p in projects if any(_mentions(said, n) for n in _names(p))}
    if len(hits) == 1:  # "Alexey, My Project": the project decides
        return next(iter(hits))
    if hits:
        return None
    named = [c for c in owners.values() if _mentions(said, c.name) or _mentions(said, c.slug)]
    if len(named) != 1:
        return None
    theirs = [p.name for p in projects if clients.key(p.owner) == named[0].slug]
    if len(theirs) == 1:
        return theirs[0]
    default = clients.default_project(session, named[0].slug)
    return default if default in theirs else None


def _open_question(session: Session, message: Message) -> Decision | None:
    return session.scalars(
        select(Decision)
        .where(
            Decision.thread_id == message.thread_id,
            Decision.kind == DecisionKind.ROUTE,
            Decision.status == DecisionStatus.PENDING,
            Decision.answer.is_(None),
            Decision.created_at < message.created_at,
        )
        .order_by(Decision.created_at.desc())
        .limit(1)
    ).first()


@dataclass(frozen=True)
class Answered:
    reply: str
    decision: Decision
    task: Task | None


def answer_open_question(session: Session, settings: Settings, message: Message) -> Answered | None:
    """The message answers the conversation's open question: it's answered, and the
    original request runs (or doesn't). None: it's something else."""
    if len(message.body) > SHORT:
        return None
    question = _open_question(session, message)
    if question is None:
        return None
    payload = question.payload or {}
    said = _said(message.body)
    if payload.get("production"):
        candidates = [str(c["project"]) for c in payload.get("candidates", [])]
        if said in YES and candidates:
            choice = candidates[0]
        elif said in NO:
            choice = inbox.NO_PROJECT
        else:
            return None
    elif said in NO or said in {"none", "none of these", "neither"}:
        choice = inbox.NO_PROJECT
    else:
        found = named_project(session, message.body)
        if found is None:
            return None
        choice = found
    via = VIA.get(message.source, inbox.Via.API)
    done = inbox.answer(
        session, settings, str(question.id), inbox.Answer(choice=choice), by=message.author, via=via
    )
    if choice == inbox.NO_PROJECT:
        return Answered(inbox.NOT_STARTED, question, None)
    return Answered(f"OK: {titles.of(session, choice)}.", question, done.task)


def _unplaced(message: Message) -> bool:
    """Routing failed, or the router started nothing and asked nothing (it said it couldn't
    tell where it goes), and it wasn't a question about Ringkeeper."""
    if message.route_error:
        return True
    routing = message.routing or {}
    if routing.get("about_ringkeeper") or routing.get("help"):
        return False
    return (
        message.routed_at is not None and not routing.get("tasks") and not routing.get("decisions")
    )


@dataclass(frozen=True)
class Unplaced:
    request: str  # the request that couldn't be placed
    project: str  # where the follow-up says it goes


def unplaced_request(session: Session, message: Message) -> Unplaced | None:
    """The conversation's last request couldn't be placed, and this message names a project
    for it: route that request there."""
    if len(message.body) > SHORT:
        return None
    earlier = threads.recent_messages(
        session, message.thread_id, limit=6, before=message.created_at
    )
    asked = [m for m in earlier if m.role is MessageRole.USER]
    if not asked or not _unplaced(asked[-1]):
        return None
    project = named_project(session, message.body)
    return Unplaced(asked[-1].body, project) if project is not None else None
