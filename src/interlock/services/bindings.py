from sqlalchemy.orm import Session

from interlock.errors import NotFoundError, RuleViolationError
from interlock.models import Env, Instance, Project, ProjectInstance
from interlock.schemas import BindingIn
from interlock.services.instances import find_instance
from interlock.services.projects import find_project, get_project


def _resolve(session: Session, project_name: str, instance_name: str) -> tuple[Project, Instance]:
    project = find_project(session, project_name)
    instance = find_instance(session, instance_name)
    missing: list[str] = []
    if project is None:
        missing.append(f"project '{project_name}'")
    if instance is None:
        missing.append(f"instance '{instance_name}'")
    if project is None or instance is None:
        verb = "does" if len(missing) == 1 else "do"
        raise NotFoundError(f"cannot bind: {' and '.join(missing)} {verb} not exist")
    return project, instance


def find_binding(project: Project, instance: Instance) -> ProjectInstance | None:
    return next((b for b in project.bindings if b.instance_id == instance.id), None)


def check_default_allowed(project: Project, instance: Instance) -> None:
    if instance.env == Env.PROD:
        raise RuleViolationError(
            f"instance '{instance.name}' is prod and cannot be a default for '{project.name}': "
            "prod writes must name the instance explicitly. Bind it without --default."
        )
    for binding in project.bindings:
        if (
            binding.is_default
            and binding.instance_type == instance.type
            and binding.instance_id != instance.id
        ):
            raise RuleViolationError(
                f"project '{project.name}' already has a default {instance.type} instance: "
                f"'{binding.instance.name}'. Rebind it without --default first."
            )


def set_binding(session: Session, data: BindingIn) -> tuple[ProjectInstance, bool]:
    """Create the binding, or replace its settings if it exists.

    Returns the binding and whether it was newly created.
    """
    project, instance = _resolve(session, data.project, data.instance)
    if data.is_default:
        check_default_allowed(project, instance)

    binding = find_binding(project, instance)
    created = binding is None
    if binding is None:
        binding = ProjectInstance(project=project, instance=instance)
        session.add(binding)
    binding.is_default = data.is_default
    binding.allowed_tools = data.allowed_tools
    binding.preapproved_writes = data.preapproved_writes
    session.flush()
    return binding, created


def remove_binding(session: Session, project_name: str, instance_name: str) -> None:
    project = get_project(session, project_name)
    instance = find_instance(session, instance_name)
    binding = None if instance is None else find_binding(project, instance)
    if binding is None:
        raise NotFoundError(f"instance '{instance_name}' is not bound to project '{project_name}'")
    project.bindings.remove(binding)
    session.flush()


def sorted_bindings(project: Project) -> list[ProjectInstance]:
    return sorted(project.bindings, key=lambda b: (b.instance_type.value, b.instance.name))
