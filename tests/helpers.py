import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ringkeeper.db import session_scope
from ringkeeper.models import Base, Decision, Env, Instance, InstanceType, Project
from ringkeeper.schemas import BindingIn, InstanceIn, ProjectIn
from ringkeeper.services import bindings, instances, projects


def make_project(session: Session, name: str = "acme-web", **fields: object) -> Project:
    data = ProjectIn.model_validate({"name": name, "owner": "acme", **fields})
    return projects.create_project(session, data)


def make_instance(
    session: Session,
    name: str,
    type: InstanceType = InstanceType.SUPABASE,
    env: Env = Env.DEV,
    secret_ref: str | None = None,
    **fields: object,
) -> Instance:
    data = InstanceIn.model_validate(
        {
            "name": name,
            "type": type,
            "env": env,
            "secret_ref": secret_ref or f"aws-sm://ringkeeper/{name}",
            "owner": "acme",
            **fields,
        }
    )
    return instances.create_instance(session, data)


def bind(project: str, instance: str, **fields: Any) -> None:
    with session_scope() as session:
        bindings.set_binding(session, BindingIn(project=project, instance=instance, **fields))


def age_decisions(session: Session, decision_id: uuid.UUID | str | None = None) -> None:
    """Make held decisions (one, or all) two days old and expired a day ago."""
    now = datetime.now(UTC)
    statement = update(Decision).values(
        created_at=now - timedelta(days=2), expires_at=now - timedelta(days=1)
    )
    if decision_id is not None:
        statement = statement.where(Decision.id == uuid.UUID(str(decision_id)))
    session.execute(statement)


def table_text(session: Session, table: str) -> list[str]:
    """Every row of a table as text, to look for things that must never be stored."""
    rows = session.execute(select(Base.metadata.tables[table])).all()
    return [repr(tuple(row)) for row in rows]
