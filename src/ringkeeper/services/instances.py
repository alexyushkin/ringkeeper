from collections.abc import Sequence

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, selectinload

from ringkeeper.errors import AlreadyExistsError, NotFoundError, RuleViolationError
from ringkeeper.models import Env, Instance, InstanceType, ProjectInstance
from ringkeeper.schemas import InstanceIn


def find_instance(session: Session, name: str) -> Instance | None:
    return session.scalars(
        select(Instance)
        .where(Instance.name == name)
        .options(selectinload(Instance.bindings).selectinload(ProjectInstance.project))
    ).one_or_none()


def get_instance(session: Session, name: str) -> Instance:
    instance = find_instance(session, name)
    if instance is None:
        raise NotFoundError(f"instance '{name}' does not exist")
    return instance


def list_instances(
    session: Session, *, type: InstanceType | None = None, env: Env | None = None
) -> Sequence[Instance]:
    query = select(Instance).order_by(Instance.name)
    if type is not None:
        query = query.where(Instance.type == type)
    if env is not None:
        query = query.where(Instance.env == env)
    return session.scalars(query).all()


def bound_project_names(instance: Instance) -> list[str]:
    return sorted(binding.project.name for binding in instance.bindings)


def create_instance(session: Session, data: InstanceIn) -> Instance:
    if find_instance(session, data.name) is not None:
        raise AlreadyExistsError(f"instance '{data.name}' already exists")
    instance = Instance(**data.model_dump(), bindings=[])
    session.add(instance)
    session.flush()
    return instance


def update_instance(session: Session, data: InstanceIn) -> Instance:
    instance = get_instance(session, data.name)
    if data.type != instance.type and instance.bindings:
        raise RuleViolationError(
            f"cannot change the type of instance '{instance.name}' while it is bound to: "
            f"{', '.join(bound_project_names(instance))}. Unbind it first."
        )
    if data.env == Env.PROD and instance.env != Env.PROD:
        defaults = sorted(b.project.name for b in instance.bindings if b.is_default)
        if defaults:
            raise RuleViolationError(
                f"cannot make instance '{instance.name}' prod: it is the default "
                f"{instance.type} instance for {', '.join(defaults)}, and a prod instance "
                "can never be a default. Rebind it without --default first."
            )
    for field, value in data.model_dump(exclude={"name"}).items():
        setattr(instance, field, value)
    session.flush()
    # The FK cascade rewrote instance_type/instance_env on the bindings.
    for binding in instance.bindings:
        session.refresh(binding)
    return instance


def remove_instance(session: Session, name: str, *, force: bool = False) -> list[str]:
    """Remove an instance. Returns the projects it was unbound from (only with force)."""
    instance = get_instance(session, name)
    projects = bound_project_names(instance)
    if projects and not force:
        raise RuleViolationError(
            f"instance '{name}' is still bound to: {', '.join(projects)}. "
            "Unbind it first, or remove it with --force to drop those bindings too."
        )
    if projects:
        session.execute(delete(ProjectInstance).where(ProjectInstance.instance_id == instance.id))
        session.expire(instance, ["bindings"])
    session.delete(instance)
    session.flush()
    return projects
