"""A request that names another client's data, said up front in the first reply: the work went
to one client's project, and tasks there can't reach the other client's connections.

Named means a connection of the other client (its name or title), the client itself, or one of
its projects by name ("@demo-shop", or "demo-shop" with its hyphen). Project titles and one-word
names without the @ aren't matched: "Website" is too plain a word. A shared tool is no client's.
A request split between two clients' projects says it for each: the Demo Co task can't read
what the Acme task reads, so it can't put it in a summary either."""

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from ringkeeper.models import Client, Instance, Project, Task


def _says(body: str, name: str | None) -> bool:
    if not name or len(name) < 3:
        return False
    return re.search(rf"(?<![\w-]){re.escape(name.casefold())}(?![\w-])", body) is not None


def _named_clients(session: Session, body: str) -> set[str]:
    """The clients (slugs) whose connections, projects or own name the request writes."""
    text = body.casefold()
    found: set[str] = set()
    for name, title, owner in session.execute(
        select(Instance.name, Instance.title, Instance.owner).where(Instance.shared.is_(False))
    ).all():
        if _says(text, name) or _says(text, title):
            found.add(owner)
    for name, owner in session.execute(select(Project.name, Project.owner)).all():
        if owner and (_says(text, f"@{name}") or ("-" in name and _says(text, name))):
            found.add(owner)
    for client, shown in session.execute(select(Client.slug, Client.name)).all():
        if _says(text, shown) or _says(text, client):
            found.add(client)
    return found


def lines(session: Session, body: str, started: list[Task]) -> list[str]:
    """ "This went to Demo Co's project. Tasks there can't reach Acme Bakery's connections.",
    once per client the work went to, when the request names another one."""
    owners = dict(
        session.execute(
            select(Project.name, Project.owner).where(
                Project.name.in_({t.project for t in started})
            )
        )
        .tuples()
        .all()
    )
    went = [o for o in dict.fromkeys(owners.get(t.project) for t in started) if o]
    named: set[str] = _named_clients(session, body) if went else set()
    shown = dict(session.execute(select(Client.slug, Client.name)).tuples().all())
    said: list[str] = []
    for client in went:
        names = [shown.get(o, o) for o in sorted(named - {client})]
        if not names:
            continue
        them = names[0] if len(names) == 1 else ", ".join(names[:-1]) + f" or {names[-1]}"
        said.append(
            f"This went to {shown.get(client, client)}'s project. Tasks there can't reach "
            f"{them}'s connections."
        )
    return said
