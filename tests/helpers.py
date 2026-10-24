from typing import Any

from sqlalchemy.orm import Session

from ringkeeper.db import session_scope
from ringkeeper.models import Env, Instance, InstanceType, Project
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
