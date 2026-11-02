from sqlalchemy.orm import Session

from ringkeeper import remote
from ringkeeper.errors import NotFoundError, RuleViolationError
from ringkeeper.models import Env, Instance, Project, ProjectInstance
from ringkeeper.schemas import BindingIn
from ringkeeper.services import clients
from ringkeeper.services.instances import excluded, find_instance
from ringkeeper.services.projects import find_project, get_project
from ringkeeper.wording import service


def _resolve(session: Session, project_name: str, instance_name: str) -> tuple[Project, Instance]:
    project = find_project(session, project_name)
    instance = find_instance(session, instance_name)
    missing: list[str] = []
    if project is None:
        missing.append(f"no project {project_name}")
    if instance is None:
        missing.append(f"no connection {instance_name}")
    if project is None or instance is None:
        raise NotFoundError(
            f"Couldn't link them: there's {' and '.join(missing)}. Refresh the page and try again."
        )
    return project, instance


def same_client(a: str, b: str) -> bool:
    """Owners name one client when their slugs match ("Acme" and "acme")."""
    return clients.key(a) == clients.key(b)


def check_same_client(session: Session, project_name: str, instance_name: str) -> None:
    """The app's rule: a client's connection is shared only among that client's
    projects; a shared tool (no client's data) goes to any project. (A registry file
    may still link across owners, where an owner is a team.)"""
    project, instance = _resolve(session, project_name, instance_name)
    if not instance.shared and not same_client(project.owner, instance.owner):
        names = clients.names(session)
        raise RuleViolationError(
            f"{instance.name} belongs to {names.get(instance.owner, instance.owner)} and "
            f"{project.name} belongs to {names.get(project.owner, project.owner)}. A client's "
            "connection can only be used by that client's projects. To use it for several "
            "clients, make it a shared tool (only for tools that hold no client's data)."
        )


def find_binding(project: Project, instance: Instance) -> ProjectInstance | None:
    return next((b for b in project.bindings if b.instance_id == instance.id), None)


def check_default_allowed(project: Project, instance: Instance) -> None:
    if instance.env == Env.PROD:
        raise RuleViolationError(
            f"{instance.name} is a production connection, so it can't be {project.name}'s "
            "default: each change to it must name it. Link it without Default."
        )
    for binding in project.bindings:
        if (
            binding.is_default
            and binding.instance_type == instance.type
            and binding.instance_id != instance.id
        ):
            raise RuleViolationError(
                f"{project.name} already uses {binding.instance.name} as its default "
                f"{service(instance.type)} connection. Turn off Default on "
                f"{binding.instance.name} first, or link this one without it."
            )


def set_binding(session: Session, data: BindingIn) -> tuple[ProjectInstance, bool]:
    """Create the binding, or replace its settings if it exists.

    Returns the binding and whether it was newly created.
    """
    project, instance = _resolve(session, data.project, data.instance)
    if excluded(instance, project):
        raise RuleViolationError(
            f"'{instance.name}' is for all projects except some, and '{project.name}' is one "
            "of them. Remove that exception to give it this project."
        )
    if data.is_default:
        check_default_allowed(project, instance)
    rules = remote.spec_for(instance.type.value, instance.config)
    try:
        scope = None if data.scope is None else remote.check_scope(rules, data.scope)
    except remote.ScopeError as error:
        raise RuleViolationError(str(error)) from None

    binding = find_binding(project, instance)
    created = binding is None
    if binding is None:
        binding = ProjectInstance(project=project, instance=instance)
        session.add(binding)
    binding.is_default = data.is_default
    binding.allowed_tools = data.allowed_tools
    binding.preapproved_writes = data.preapproved_writes
    if scope is not None or created:
        binding.scope = scope or {}
    session.flush()
    return binding, created


def remove_binding(session: Session, project_name: str, instance_name: str) -> None:
    project = get_project(session, project_name)
    instance = find_instance(session, instance_name)
    binding = None if instance is None else find_binding(project, instance)
    if binding is None:
        raise NotFoundError(
            f"{instance_name} isn't linked to {project_name} any more. Refresh the page."
        )
    project.bindings.remove(binding)
    session.flush()


def sorted_bindings(project: Project) -> list[ProjectInstance]:
    return sorted(project.bindings, key=lambda b: (b.instance_type.value, b.instance.name))
