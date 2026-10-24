from sqlalchemy.orm import Session

from interlock.models import Env, Instance, InstanceType, Project
from interlock.schemas import InstanceIn, ProjectIn
from interlock.services import instances, projects


def make_project(session: Session, name: str = "acme-web", **fields: object) -> Project:
    data = ProjectIn.model_validate({"name": name, "owner": "acme", **fields})
    return projects.create_project(session, data)


def make_instance(
    session: Session,
    name: str,
    type: InstanceType = InstanceType.SUPABASE,
    env: Env = Env.DEV,
    **fields: object,
) -> Instance:
    data = InstanceIn.model_validate(
        {
            "name": name,
            "type": type,
            "env": env,
            "secret_ref": f"aws-sm://interlock/{name}",
            "owner": "acme",
            **fields,
        }
    )
    return instances.create_instance(session, data)
