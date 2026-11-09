"""@-mentions in a request: clients, projects, connections and agents, named on purpose.

The request box offers them as you type `@`, and writes each as `@name`: a client or
project as people read it (`@Its title`). A mention is the operator's firm choice: the
router's model is told so, and whatever it answers, `settle` holds the tasks to it. A
mention that matches nothing is just text.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ringkeeper.models import Agent, Client, Instance, Message, Project
from ringkeeper.router.schema import Candidate, RoutedTask, RouterOutput
from ringkeeper.services import clients as client_names

TOKEN = re.compile(r"(?<![\w@.])@([A-Za-z0-9][A-Za-z0-9._-]*)")


@dataclass(frozen=True)
class Mentions:
    clients: list[str] = field(default_factory=list[str])  # slugs
    projects: list[str] = field(default_factory=list[str])
    connections: list[str] = field(default_factory=list[str])
    agents: list[tuple[str, str]] = field(default_factory=list[tuple[str, str]])  # project, agent

    def __bool__(self) -> bool:
        return bool(self.clients or self.projects or self.connections or self.agents)


def offered(session: Session) -> list[dict[str, Any]]:
    """Everything that can be mentioned, for the request box's list."""
    names = client_names.names(session)
    projects = session.scalars(select(Project).order_by(Project.name)).all()
    connections = session.scalars(select(Instance).order_by(Instance.name)).all()
    agents = session.scalars(
        select(Agent).options(selectinload(Agent.project)).order_by(Agent.name)
    ).all()
    return [
        *(
            {"kind": "client", "token": name, "label": name, "detail": "client"}
            for name in sorted(names.values(), key=str.lower)
        ),
        *(
            {"kind": "project", "token": p.title, "label": p.title,
             "detail": names.get(p.owner, p.owner)}
            for p in projects
        ),
        *(
            {"kind": "connection", "token": c.name, "label": c.title or c.name,
             "detail": "your tool" if c.shared else names.get(c.owner, c.owner)}
            for c in connections
        ),
        *(
            {"kind": "agent", "token": a.name, "label": a.name,
             "detail": f"agent in {a.project.title}"}
            for a in agents
        ),
    ]  # fmt: skip


def find(session: Session, text: str) -> Mentions:
    """The mentions in a message. A name used by more than one kind counts as the most
    specific: an agent, then a connection, a project, a client."""
    if "@" not in text:
        return Mentions()
    agents = session.scalars(select(Agent).options(selectinload(Agent.project))).all()
    instances = {i.lower() for i in session.scalars(select(Instance.name))}
    projects = {p.lower() for p in session.scalars(select(Project.name))}
    slugs = {s.lower() for s in session.scalars(select(Client.slug))}
    taken = instances | {a.name.lower() for a in agents}
    titles = session.execute(select(Project.title, Project.name)).tuples().all()
    titled, text = _by_name(titles, text, taken)
    taken |= {title.lower() for title, _ in titles}
    people = session.execute(select(Client.name, Client.slug)).tuples().all()
    clients, text = _by_name(people, text, taken)
    found = Mentions(projects=titled, clients=clients)
    tokens = list(dict.fromkeys(t.rstrip(".-_").lower() for t in TOKEN.findall(text)))
    for token in tokens:
        named = [(a.project.name, a.name) for a in agents if a.name.lower() == token]
        if named:
            found.agents.extend(named)
        elif token in instances:
            found.connections.append(token)
        elif token in projects and token not in found.projects:
            found.projects.append(token)
        elif token in slugs and token not in found.clients:
            found.clients.append(token)
    return found


def for_message(session: Session, message: Message) -> Mentions:
    """What a message names: its @-mentions, and the client and project chosen at the top of
    the app when it was sent (a project chosen there decides, like an @-mention)."""
    found = find(session, message.body)
    project = message.target_project
    if project and session.scalar(select(Project.name).where(Project.name == project)):
        return Mentions(projects=[project], connections=found.connections, agents=found.agents)
    client = message.target_client
    if client and client not in found.clients and session.get(Client, client) is not None:
        return Mentions(
            clients=[*found.clients, client], projects=found.projects,
            connections=found.connections, agents=found.agents,
        )  # fmt: skip
    return found


def _by_name(rows: Sequence[tuple[str, str]], text: str, taken: set[str]) -> tuple[list[str], str]:
    """Slugs mentioned by the name people read, which may have spaces, and the text
    without them. Longest first: "@Web shop" isn't "@Web". A name that's also something
    more specific's is left for `find`, or for its own turn here."""
    found: list[str] = []
    for shown, slug in sorted(rows, key=lambda row: -len(row[0])):
        if shown and shown.lower() not in taken:
            mention = re.compile(rf"(?<![\w@.])@{re.escape(shown)}(?![\w-])", re.IGNORECASE)
            text, hits = mention.subn(" ", text)
            if hits:
                found.append(slug)
    return found, text


def note(found: Mentions) -> str:
    """For the router's prompt: what was named on purpose."""
    parts = [
        *(f"project {p}" for p in found.projects),
        *(f"client {c}" for c in found.clients),
        *(f"connection {c}" for c in found.connections),
        *(f"agent {a} (of project {p})" for p, a in found.agents),
    ]
    return (
        "THE OPERATOR NAMED THESE WITH @ (a firm choice, not a hint; route to them): "
        + ", ".join(parts)
    )


def _projects_of_connection(session: Session, name: str) -> list[str]:
    instance = session.scalar(
        select(Instance).options(selectinload(Instance.bindings)).where(Instance.name == name)
    )
    return sorted(b.project.name for b in instance.bindings) if instance is not None else []


def _projects_of_client(session: Session, slug: str) -> list[str]:
    return sorted(session.scalars(select(Project.name).where(Project.owner == slug)))


def _firm(task: RoutedTask, project: str, **more: Any) -> RoutedTask:
    update: dict[str, Any] = {"project": project, "confidence": 1.0, "alternatives": []}
    return task.model_copy(update={**update, "touches": [], **more})


def _ask_among(task: RoutedTask, choices: Sequence[str]) -> RoutedTask:
    """The mention allows several projects and the model's pick isn't one of them: ask."""
    return task.model_copy(
        update={
            "project": choices[0],
            "confidence": 0.0,
            "alternatives": [Candidate(project=p, confidence=0.0) for p in choices[1:]],
            "touches": [],
        }
    )


def _settle_task(session: Session, task: RoutedTask, found: Mentions) -> RoutedTask:
    if found.agents:
        allowed = [p for p, _ in found.agents]
        project = task.project if task.project in allowed else allowed[0]
        agent = next(a for p, a in found.agents if p == project)
        return _firm(task, project, agent=agent)
    if found.projects:
        return _firm(task, task.project if task.project in found.projects else found.projects[0])
    choices: list[str] = []
    for name in found.connections:
        choices += _projects_of_connection(session, name)
    for slug in found.clients:
        choices += _projects_of_client(session, slug)
    choices = sorted(set(choices))
    if not choices:
        return task
    if task.project in choices:
        return _firm(task, task.project)
    return _firm(task, choices[0]) if len(choices) == 1 else _ask_among(task, choices)


def settle(session: Session, output: RouterOutput, found: Mentions) -> RouterOutput:
    """Hold each task to what was named, whatever the model chose. Named connections
    are spelled out for the worker."""
    if not found:
        return output
    use = f"\n\nUse the connection {', '.join(found.connections)}." if found.connections else ""
    settled = [_settle_task(session, t, found) for t in output.tasks]
    if use:
        settled = [t.model_copy(update={"prompt": t.prompt + use}) for t in settled]
    return output.model_copy(update={"tasks": settled})
