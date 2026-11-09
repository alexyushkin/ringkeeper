from collections.abc import Sequence

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, object_session, selectinload

from ringkeeper.errors import AlreadyExistsError, NotFoundError, RuleViolationError
from ringkeeper.models import Env, Instance, InstanceType, Project, ProjectInstance
from ringkeeper.schemas import InstanceIn
from ringkeeper.services import clients, titles
from ringkeeper.wording import service

# The owner shown for a shared tool: yours, not a client's (Instance.shared decides).
SHARED_OWNER = "you"


def find_instance(session: Session, name: str) -> Instance | None:
    return session.scalars(
        select(Instance)
        .where(Instance.name == name)
        .options(selectinload(Instance.bindings).selectinload(ProjectInstance.project))
    ).one_or_none()


def get_instance(session: Session, name: str) -> Instance:
    instance = find_instance(session, name)
    if instance is None:
        raise NotFoundError(
            f"There's no connection named {name}. It may have been removed: refresh the page."
        )
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


def _bound_titles(instance: Instance) -> str:
    return ", ".join(sorted(binding.project.title for binding in instance.bindings))


def _check_kind(session: Session, data: InstanceIn, before: Instance | None = None) -> None:
    """Any kind of connection is a client's (only their projects, maybe all of them except
    some) or yours (a shared tool: any project, or all of them, maybe except some clients
    and projects)."""
    if data.except_clients and not data.shared:
        raise RuleViolationError(
            f"'{data.name}' is a client's connection, so it has no other clients to leave out."
        )
    if (data.except_clients or data.except_projects) and not data.all_projects:
        raise RuleViolationError(
            f"'{data.name}' isn't for all projects, so it has no exceptions: link it to "
            "the projects that should have it instead."
        )
    # Newly named projects must exist (an old name may outlive its project).
    known = set(before.except_projects) if before is not None else set[str]()
    names = set(session.scalars(select(Project.name)))
    missing = [p for p in data.except_projects if p not in known and p not in names]
    if missing:
        raise RuleViolationError(
            f"There's no project named {', '.join(missing)}. Check the spelling."
        )


def excluded(instance: Instance, project: Project) -> bool:
    """A connection for all projects except some: whether `project` is left out, by its
    name or its client (matched ignoring case and spaces). A client's connection only
    ever reaches that client's projects."""
    if not instance.all_projects:
        return False
    client = clients.key(project.owner)
    if not instance.shared and client != clients.key(instance.owner):
        return True
    return project.name in instance.except_projects or any(
        clients.key(c) == client for c in instance.except_clients
    )


def google_title(session: Session, owner: str, email: str, *, shared: bool) -> str:
    """A Google account's name: "Google · General Informatics LLC · info@…", so two clients'
    accounts on one address don't read the same; your own is "Google · info@…"."""
    if shared or owner == SHARED_OWNER:
        return f"Google · {email}"
    return f"Google · {clients.display(session, owner)} · {email}"


def suggested_title(instance: Instance) -> str | None:
    """A name to tell this connection apart, when another one has the same name."""
    session = object_session(instance)
    shown = (instance.title or instance.name).strip()
    if session is None or not shown:
        return None
    same = session.scalars(
        select(Instance.name).where(
            Instance.id != instance.id,
            func.lower(func.coalesce(Instance.title, Instance.name)) == shown.lower(),
        )
    ).first()
    if same is None:
        return None
    email = str((instance.config or {}).get("email") or "")
    if instance.type is InstanceType.GOOGLE and email:
        better = google_title(session, instance.owner, email, shared=instance.shared)
    elif instance.shared:
        better = f"{shown} · yours"
    else:
        better = f"{shown} · {clients.display(session, instance.owner)}"
    return better if better.lower() != shown.lower() else None


def describe_reach(instance: Instance) -> str:
    """What it reaches, like "All projects except Acme (client), Website", or for a
    client's connection "All Acme projects except Website" ("" if not for all projects)."""
    if not instance.all_projects:
        return ""
    session = object_session(instance)
    names = clients.names(session) if session is not None else {}
    left_out = [f"{names.get(clients.key(c), c)} (client)" for c in instance.except_clients] + [
        titles.of(session, p) for p in instance.except_projects
    ]
    whose = "" if instance.shared else f"{names.get(clients.key(instance.owner), instance.owner)} "
    return f"All {whose}projects" + (f" except {', '.join(left_out)}" if left_out else "")


def link_everywhere(session: Session, instance: Instance) -> list[str]:
    """A connection for all projects: linked to every project it reaches that it isn't
    linked to yet, and unlinked from the projects left out. A link it already has keeps
    its scope. Returns the projects it was newly linked to."""
    added: list[str] = []
    for project in session.scalars(select(Project).order_by(Project.name)):
        binding = next((b for b in instance.bindings if b.project_id == project.id), None)
        if excluded(instance, project):
            if binding is not None:
                session.delete(binding)
                session.expire(project, ["bindings"])
        elif binding is None:
            session.add(ProjectInstance(project=project, instance=instance))
            added.append(project.name)
    session.flush()
    session.expire(instance, ["bindings"])
    return added


def create_instance(session: Session, data: InstanceIn) -> Instance:
    if find_instance(session, data.name) is not None:
        raise AlreadyExistsError(
            f"There's already a connection named {data.name}. Pick another name."
        )
    _check_kind(session, data)
    if not data.shared:
        data = data.model_copy(update={"owner": clients.ensure(session, data.owner)})
    instance = Instance(**data.model_dump(), bindings=[])
    session.add(instance)
    session.flush()
    if instance.all_projects:
        link_everywhere(session, instance)
    return instance


def update_instance(session: Session, data: InstanceIn) -> Instance:
    instance = get_instance(session, data.name)
    _check_kind(session, data, instance)
    if not data.shared:
        data = data.model_copy(update={"owner": clients.ensure(session, data.owner)})
    if instance.shared and not data.shared:
        others = sorted(
            b.project.name for b in instance.bindings
            if clients.key(b.project.owner) != clients.key(data.owner)
        )  # fmt: skip
        if others:
            name = clients.names(session).get(data.owner, data.owner)
            raise RuleViolationError(
                f"{instance.name} can't become {name}'s connection while other clients' "
                f"projects use it ({', '.join(others)}). Unlink it from those projects first."
            )
    if data.type != instance.type and instance.bindings:
        raise RuleViolationError(
            f"You can't change what kind of connection {instance.name} is while projects use "
            f"it ({_bound_titles(instance)}). Unlink it from those projects "
            "first, or add a new connection."
        )
    if data.env == Env.PROD and instance.env != Env.PROD:
        defaults = sorted(b.project.name for b in instance.bindings if b.is_default)
        if defaults:
            raise RuleViolationError(
                f"{instance.name} is the default {service(instance.type)} connection for "
                f"{', '.join(defaults)}, and a production connection can't be a default. "
                "Turn off Default for it on those projects, then mark it production."
            )
    keep = {"name", "title"} if data.title is None else {"name"}
    for field, value in data.model_dump(exclude=keep).items():
        setattr(instance, field, value)
    session.flush()
    # The FK cascade rewrote instance_type/instance_env on the bindings.
    for binding in instance.bindings:
        session.refresh(binding)
    if instance.all_projects:
        link_everywhere(session, instance)
    return instance


def remove_instance(session: Session, name: str, *, force: bool = False) -> list[str]:
    """Remove an instance. Returns the projects it was unbound from (only with force)."""
    instance = get_instance(session, name)
    projects = bound_project_names(instance)
    if projects and not force:
        raise RuleViolationError(
            f"{name} is still linked to {_bound_titles(instance)}. Unlink it from those projects "
            "first, then remove it."
        )
    if projects:
        session.execute(delete(ProjectInstance).where(ProjectInstance.instance_id == instance.id))
        session.expire(instance, ["bindings"])
    session.delete(instance)
    session.flush()
    return projects
