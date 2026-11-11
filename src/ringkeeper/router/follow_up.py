"""A short message that answers what this conversation left open.

When Ringkeeper's last word in a conversation asked which project, and the next message
is just a name ("Alexey, My Project", "the Acme one"), that's the answer: the original
request runs there, with no model call and no second question. When it asked for a
go-ahead on production, "yes" or "do it" is the go-ahead. When the router said it couldn't
place the last request, a message that's just a project's name routes that request there.

Only then: a question that isn't the conversation's last word is left alone, and a message
that says anything besides a name ("List Acme's active workflows") is a request of its own.

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
from ringkeeper.services import clients, inbox, titles

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


# Words that can sit around a name in an answer without making it a request.
FILLER = frozenset({
    "the", "one", "project", "projects", "for", "it", "it's", "its", "use", "in", "on", "to",
    "a", "an", "please", "that", "this", "is", "and", "&", "with", "client", "go", "ok",
    "okay", "yes", "sure", "pls", "thanks", "thank", "you",
})  # fmt: skip
# A client's name is also what people call it without the company's suffix.
SUFFIX = re.compile(r"[\s,]+(llc|l\.l\.c\.|inc|inc\.|ltd|ltd\.|co|co\.|corp|corp\.|gmbh|plc)$")


def client_names(client: Client) -> list[str]:
    name = client.name.lower().strip()
    return [n for n in {name, SUFFIX.sub("", name), client.slug} if n]


def just_names(session: Session, text: str) -> bool:
    """Whether a message is only names (projects, clients) and filler: nothing else to do."""
    said = _said(text)
    names = [n.lower() for p in session.scalars(select(Project)) for n in _names(p)]
    for client in session.scalars(select(Client)):
        names += client_names(client)
    for name in sorted(set(names), key=len, reverse=True):
        said = re.sub(rf"(?<![\w-]){re.escape(name)}(?![\w-])", " ", said)
    words = [w for w in re.split(r"[\s,.;:!?()\"]+", said) if w]
    return all(w in FILLER for w in words)


def _core(said: str) -> str:
    """What's left of a message without filler: "the Demo site one" is "demo site"."""
    return " ".join(w for w in re.split(r"[\s,.;:!?()\"]+", said) if w and w not in FILLER)


def _whole_names(project: Project) -> set[str]:
    return {n.lower() for n in (project.title, project.name, project.name.replace("-", " ")) if n}


def named_in(
    session: Session, text: str, among: list[str] | None = None
) -> tuple[list[str], list[str]]:
    """The projects and clients a message names, the most specific first: a name that sits
    inside a longer name it also matches doesn't count ("Demo site" names Demo site, not the
    project with the alias "demo"; "Demo Co's folder" names the client, not that project)."""
    said = _said(text)
    projects = session.scalars(select(Project).order_by(Project.name)).all()
    if among:
        projects = [p for p in projects if p.name in among]
    hit_projects = {p.name: [n.lower() for n in _names(p) if _mentions(said, n)] for p in projects}
    hit_projects = {k: v for k, v in hit_projects.items() if v}
    hit_clients = {
        c.slug: [n for n in client_names(c) if _mentions(said, n)]
        for c in session.scalars(select(Client).order_by(Client.slug))
    }
    hit_clients = {k: v for k, v in hit_clients.items() if v}
    every = [(("p", k), n) for k, v in hit_projects.items() for n in v]
    every += [(("c", k), n) for k, v in hit_clients.items() for n in v]

    def specific(who: tuple[str, str], names: list[str]) -> bool:
        """Some name of it isn't just part of a longer name something else matches."""
        return any(
            not any(other != who and len(m) > len(n) and _mentions(m, n) for other, m in every)
            for n in names
        )

    return (
        [k for k, v in hit_projects.items() if specific(("p", k), v)],
        [k for k, v in hit_clients.items() if specific(("c", k), v)],
    )


def named_project(session: Session, text: str, among: list[str] | None = None) -> str | None:
    """The one project a short message names, by its title, name or alias, or by its
    client's name (that client's default or only project). None: none, or more than one.
    A project whose whole title or name is the message (besides filler) wins outright."""
    said = _said(text)
    projects = session.scalars(select(Project).order_by(Project.name)).all()
    if among:
        projects = [p for p in projects if p.name in among]
    core = _core(said)
    exact = [p.name for p in projects if core and core in _whole_names(p)]
    if len(exact) == 1:
        return exact[0]
    owners = {c.slug: c for c in session.scalars(select(Client))}
    hits, named_clients = named_in(session, text, among)
    if len(hits) == 1:  # "Alexey, My Project": the project decides
        return hits[0]
    if hits:
        return None
    named = [owners[slug] for slug in named_clients if slug in owners]
    if len(named) != 1:
        return None
    theirs = [p.name for p in projects if clients.key(p.owner) == named[0].slug]
    if len(theirs) == 1:
        return theirs[0]
    default = clients.default_project(session, named[0].slug)
    return default if default in theirs else None


def _last_request(session: Session, message: Message) -> Message | None:
    """The conversation's request before this message."""
    return session.scalars(
        select(Message)
        .where(
            Message.thread_id == message.thread_id,
            Message.role == MessageRole.USER,
            Message.created_at < message.created_at,
        )
        .order_by(Message.created_at.desc())
        .limit(1)
    ).first()


def _open_question(session: Session, message: Message) -> Decision | None:
    """The question the conversation's last word asked, if it's still open: about the
    request just before this message, with nothing sent in between."""
    last = _last_request(session, message)
    if last is None:
        return None
    return session.scalars(
        select(Decision)
        .where(
            Decision.thread_id == message.thread_id,
            Decision.message_id == last.id,
            Decision.kind == DecisionKind.ROUTE,
            Decision.status == DecisionStatus.PENDING,
            Decision.answer.is_(None),
        )
        .order_by(Decision.created_at.desc())
        .limit(1)
    ).first()


def _choice(session: Session, question: Decision, body: str) -> str | None:
    """What a reply chooses: a project, "none", or the go-ahead's project. None: it isn't
    an answer (it says more than a name, or names nothing)."""
    payload = question.payload or {}
    said = _said(body)
    if payload.get("production"):
        candidates = [str(c["project"]) for c in payload.get("candidates", [])]
        if said in YES and candidates:
            return candidates[0]
        return inbox.NO_PROJECT if said in NO else None
    if said in NO or said in {"none", "none of these", "neither"}:
        return inbox.NO_PROJECT
    if not just_names(session, body):
        return None  # says more than a name: a request of its own
    return named_project(session, body)


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
    choice = _choice(session, question, message.body)
    if choice is None:
        return None
    via = VIA.get(message.source, inbox.Via.API)
    done = inbox.answer(
        session, settings, str(question.id), inbox.Answer(choice=choice), by=message.author, via=via
    )
    if choice == inbox.NO_PROJECT:
        return Answered(inbox.NOT_STARTED, question, None)
    return Answered(f"OK: {titles.of(session, choice)}.", question, done.task)


def _unplaced(message: Message) -> bool:
    """The router said it couldn't tell where this request goes (`unplaced` in its routing
    record). A greeting, an answer or a task started is never that."""
    return bool((message.routing or {}).get("unplaced"))


@dataclass(frozen=True)
class Unplaced:
    request: str  # the request that couldn't be placed
    project: str  # where the follow-up says it goes


def unplaced_request(session: Session, message: Message) -> Unplaced | None:
    """The conversation's last request couldn't be placed, and this message names a project
    for it: route that request there."""
    if len(message.body) > SHORT or not just_names(session, message.body):
        return None
    last = _last_request(session, message)
    if last is None or not _unplaced(last):
        return None
    project = named_project(session, message.body)
    return Unplaced(last.body, project) if project is not None else None
