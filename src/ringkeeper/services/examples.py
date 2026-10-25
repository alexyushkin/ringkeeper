"""Routing examples: answered route decisions, remembered.

The router sees recent examples as hints, and an exact match (after
normalizing case and whitespace) routes a single-task message straight to
the remembered project. `ringkeeper routing examples delete` forgets a wrong
one, so a bad answer doesn't route the same phrasing wrong forever.
"""

import uuid
from collections.abc import Sequence

from sqlalchemy import String, cast, select
from sqlalchemy.orm import Session

from ringkeeper.errors import NotFoundError
from ringkeeper.models import RoutingExample, TaskKind
from ringkeeper.services.decisions import MIN_ID_PREFIX
from ringkeeper.services.threads import normalize
from ringkeeper.storage.sql import insert


def save(
    session: Session,
    text: str,
    project: str,
    *,
    kind: TaskKind | None = None,
    decision_id: uuid.UUID | None = None,
    by: str | None = None,
) -> RoutingExample:
    """Remember (or overwrite) where this phrasing goes."""
    values = {
        "normalized": normalize(text),
        "text": text,
        "project": project,
        "kind": kind,
        "decision_id": decision_id,
        "created_by": by,
    }
    statement = (
        insert(session, RoutingExample)
        .values(**values)
        .on_conflict_do_update(index_elements=["normalized"], set_=values)
        .returning(RoutingExample)
        .execution_options(populate_existing=True)
    )
    return session.scalars(statement).one()


def match(session: Session, text: str) -> RoutingExample | None:
    return session.scalars(
        select(RoutingExample).where(RoutingExample.normalized == normalize(text))
    ).one_or_none()


def list_examples(
    session: Session, *, project: str | None = None, limit: int = 50
) -> Sequence[RoutingExample]:
    query = select(RoutingExample).order_by(RoutingExample.created_at.desc()).limit(limit)
    if project is not None:
        query = query.where(RoutingExample.project == project)
    return session.scalars(query).all()


def delete(session: Session, ident: str) -> RoutingExample:
    ident = ident.strip().lower()
    query = select(RoutingExample)
    try:
        query = query.where(RoutingExample.id == uuid.UUID(ident))
    except ValueError:
        if len(ident) < MIN_ID_PREFIX:
            raise NotFoundError(
                f"routing example '{ident}' not found; use the full id or 8+ characters"
            ) from None
        query = query.where(cast(RoutingExample.id, String).startswith(ident))
    matches = session.scalars(query.limit(2)).all()
    if not matches:
        raise NotFoundError(f"routing example '{ident}' not found")
    if len(matches) > 1:
        raise NotFoundError(f"routing example prefix '{ident}' is ambiguous; use more characters")
    session.delete(matches[0])
    session.flush()
    return matches[0]
