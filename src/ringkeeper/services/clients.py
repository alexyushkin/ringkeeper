"""Clients of their own: a slug for URLs and IDs, and the name as typed.

A project's or connection's `owner` is its client's slug. Typing a new client
anywhere (a project, a connection, an import, a Google sign-in) creates it, so
every way in goes through `ensure`. A client may have no projects or
connections yet and is still listed. Clients match by slug: "Acme" and "acme"
are one client.
"""

from collections import Counter
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ringkeeper.errors import NotFoundError, RuleViolationError
from ringkeeper.models import Client, Instance, Project, ProjectInstance
from ringkeeper.names import slug

SHARED_OWNER = "you"  # a shared tool's owner (services.instances): never a client


def key(text: str) -> str:
    """The slug a client's name (or slug) matches by."""
    return slug(text)


def find(session: Session, text: str) -> Client | None:
    """The client with this slug, or this name (ignoring case and spacing)."""
    wanted = key(text)
    if not wanted:
        return None
    found = session.get(Client, wanted)
    if found is not None:
        return found
    named = text.strip().casefold()
    return next(
        (c for c in session.scalars(select(Client)) if c.name.strip().casefold() == named), None
    )


def get(session: Session, text: str) -> Client:
    found = find(session, text)
    if found is None:
        raise NotFoundError(
            f"There's no client named {text}. Check the spelling, or add the client first."
        )
    return found


def ensure(session: Session, text: str) -> str:
    """The client's slug, creating the client (named as typed) if it's new."""
    name = " ".join(text.split())
    if name.casefold() == SHARED_OWNER:
        return SHARED_OWNER
    found = find(session, name)
    if found is not None:
        return found.slug
    new = key(name)
    if not new:
        raise RuleViolationError("A client's name needs a letter or a digit.")
    session.add(Client(slug=new, name=name))
    session.flush()
    return new


@dataclass(frozen=True)
class Listed:
    slug: str
    name: str
    projects: int
    connections: int


def list_clients(session: Session) -> list[Listed]:
    """Every client, with or without projects and connections, by name. Their connections
    are their own and your tools their projects use, each once: what those projects have."""
    projects = dict(
        session.execute(select(Project.owner, func.count()).group_by(Project.owner)).tuples().all()
    )
    own = session.execute(
        select(Instance.owner, Instance.id).where(Instance.shared.is_(False))
    ).tuples()
    tools = session.execute(
        select(Project.owner, Instance.id)
        .join(ProjectInstance, ProjectInstance.project_id == Project.id)
        .join(Instance, Instance.id == ProjectInstance.instance_id)
        .where(Instance.shared.is_(True))
    ).tuples()
    connections = Counter(owner for owner, _ in {*own, *tools})
    return sorted(
        (
            Listed(c.slug, c.name, projects.get(c.slug, 0), connections.get(c.slug, 0))
            for c in session.scalars(select(Client))
        ),
        key=lambda c: c.name.casefold(),
    )


def names(session: Session) -> dict[str, str]:
    """Slug -> name, for showing owners."""
    return dict(session.execute(select(Client.slug, Client.name)).tuples().all())


def rename(session: Session, text: str, name: str) -> Client:
    """Change the name shown; the slug (URLs, IDs) stays."""
    client = get(session, text)
    name = " ".join(name.split())
    if not key(name):
        raise RuleViolationError("A client's name needs a letter or a digit.")
    other = find(session, name)
    if other is not None and other.slug != client.slug:
        raise RuleViolationError(f"There's already a client named '{other.name}'.")
    client.name = name
    session.flush()
    return client


def move(session: Session, text: str, to: str) -> int:
    """Give a client's projects and connections to another client. Returns how many."""
    client, target = get(session, text), get(session, to)
    if client.slug == target.slug:
        raise RuleViolationError("Choose another client to move them to.")
    moved = 0
    for row in [
        *session.scalars(select(Project).where(Project.owner == client.slug)),
        *session.scalars(
            select(Instance).where(Instance.owner == client.slug, Instance.shared.is_(False))
        ),
    ]:
        row.owner = target.slug
        moved += 1
    _forget_in_exceptions(session, client.slug, target.slug)
    session.flush()
    return moved


def delete(session: Session, text: str) -> None:
    """A client with no projects and no connections (move or remove those first)."""
    client = get(session, text)
    projects = session.scalar(
        select(func.count()).select_from(Project).where(Project.owner == client.slug)
    )
    connections = session.scalar(
        select(func.count())
        .select_from(Instance)
        .where(Instance.owner == client.slug, Instance.shared.is_(False))
    )
    if projects or connections:
        raise RuleViolationError(
            f"{client.name} still has {projects} project(s) and {connections} connection(s). "
            "Move them to another client or remove them first."
        )
    _forget_in_exceptions(session, client.slug, None)
    session.delete(client)
    session.flush()


def _forget_in_exceptions(session: Session, gone: str, instead: str | None) -> None:
    """Shared tools that leave the client out: now leave out `instead`, or nobody."""
    for tool in session.scalars(select(Instance).where(Instance.all_projects)):
        if any(key(c) == gone for c in tool.except_clients):
            kept = [c for c in tool.except_clients if key(c) != gone]
            tool.except_clients = [*kept, instead] if instead and instead not in kept else kept
