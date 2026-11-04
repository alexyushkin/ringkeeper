"""A project as people read it.

`Project.name` is its slug: URLs, IDs and what the AI uses. `Project.title` is its
name as typed, and what the app says. Clients work the same way (slug and name).
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from ringkeeper.models import Project


def spoken(slug: str) -> str:
    """A title for a project nobody named: "demo-shop" -> "Demo shop"."""
    return slug.replace("-", " ").capitalize()


def mapping(session: Session) -> dict[str, str]:
    """Slug -> title, for showing many."""
    return dict(session.execute(select(Project.name, Project.title)).tuples().all())


def of(session: Session | None, name: str) -> str:
    """One project's title; one that's gone reads like one nobody named."""
    title = session.scalar(select(Project.title).where(Project.name == name)) if session else None
    return title or spoken(name)
