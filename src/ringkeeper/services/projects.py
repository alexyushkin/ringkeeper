from collections.abc import Sequence

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, selectinload

from ringkeeper.errors import AlreadyExistsError, NotFoundError, RuleViolationError
from ringkeeper.models import (
    Decision,
    DecisionKind,
    DecisionStatus,
    Instance,
    Project,
    ProjectInstance,
    RoutingExample,
    Task,
    TaskStatus,
)
from ringkeeper.schemas import ProjectIn
from ringkeeper.services import clients, titles
from ringkeeper.services.instances import excluded, link_everywhere


def find_project(session: Session, name: str) -> Project | None:
    return session.scalars(
        select(Project)
        .where(Project.name == name)
        .options(selectinload(Project.bindings).selectinload(ProjectInstance.instance))
    ).one_or_none()


def get_project(session: Session, name: str) -> Project:
    project = find_project(session, name)
    if project is None:
        raise NotFoundError(
            f"There's no project named {name}. It may have been renamed or removed: go back to "
            "Clients & projects."
        )
    return project


def list_projects(session: Session) -> Sequence[Project]:
    return session.scalars(select(Project).order_by(Project.name)).all()


def create_project(session: Session, data: ProjectIn) -> Project:
    if find_project(session, data.name) is not None:
        raise AlreadyExistsError(f"There's already a project named {data.name}. Pick another name.")
    data = data.model_copy(
        update={
            "owner": clients.ensure(session, data.owner),
            "title": data.title or titles.spoken(data.name),
        }
    )
    project = Project(**data.model_dump(), bindings=[])
    session.add(project)
    # What's for all projects comes with every new one it reaches: shared tools, unless
    # they leave out its client, and its client's own connections.
    for tool in session.scalars(select(Instance).where(Instance.all_projects)):
        if not excluded(tool, project):
            session.add(ProjectInstance(project=project, instance=tool))
    session.flush()
    return project


def update_project(session: Session, data: ProjectIn) -> Project:
    project = get_project(session, data.name)
    data = data.model_copy(
        update={
            "owner": clients.ensure(session, data.owner),
            "title": data.title or project.title,
        }
    )
    moved = data.owner != project.owner
    for field, value in data.model_dump(exclude={"name"}).items():
        setattr(project, field, value)
    session.flush()
    if moved:  # what's for all of a client's projects follows the project
        for tool in session.scalars(select(Instance).where(Instance.all_projects)):
            link_everywhere(session, tool)
    return project


# A task in one of these still works under the project's name (its token names it).
BUSY = (TaskStatus.QUEUED, TaskStatus.RUNNING, TaskStatus.WAITING)


def rename_project(session: Session, name: str, new: str) -> Project:
    """Give a project another name, everywhere it is named: its tasks, decisions,
    routing examples and shared tools' exceptions. The old name becomes one of its
    other names, so asking by it still finds the project. The audit log keeps old
    entries as they were (it is append-only). Not while a task of it is at work."""
    project = get_project(session, name)
    if new == name:
        return project
    if find_project(session, new) is not None:
        raise AlreadyExistsError(f"There's already a project named '{new}'.")
    busy = session.scalar(
        select(func.count()).select_from(Task).where(Task.project == name, Task.status.in_(BUSY))
    )
    if busy:
        raise RuleViolationError(
            f"{project.title} has {busy} task(s) running or waiting to start. Rename it once "
            "they're done or stopped."
        )
    project.name = new
    project.aliases = [*(a for a in project.aliases if a != new), name]
    for model in (Task, Decision, RoutingExample):
        session.execute(update(model).where(model.project == name).values(project=new))
    for decision in session.scalars(
        select(Decision).where(
            Decision.kind == DecisionKind.ROUTE, Decision.status == DecisionStatus.PENDING
        )
    ):
        payload = dict(decision.payload or {})
        candidates = [
            {**c, "project": new} if c.get("project") == name else c
            for c in payload.get("candidates", [])
        ]
        if candidates != payload.get("candidates", []):
            decision.payload = {**payload, "candidates": candidates}
    for tool in session.scalars(select(Instance).where(Instance.all_projects)):
        if name in tool.except_projects:
            tool.except_projects = [new if p == name else p for p in tool.except_projects]
    session.flush()
    return project


def remove_project(session: Session, name: str) -> int:
    """Remove a project and its bindings. Returns how many bindings went with it."""
    project = get_project(session, name)
    removed_bindings = len(project.bindings)
    # A shared tool that left it out forgets it (a new project may take the name).
    for tool in session.scalars(select(Instance).where(Instance.all_projects)):
        if name in tool.except_projects:
            tool.except_projects = [p for p in tool.except_projects if p != name]
    session.delete(project)
    session.flush()
    return removed_bindings


def owners(session: Session) -> dict[str, str]:
    """Project name -> its client, for labelling lists ("client > project")."""
    return dict(session.execute(select(Project.name, Project.owner)).tuples().all())


def in_scope(session: Session, client: str | None, project: str | None) -> list[str] | None:
    """The projects a client/project filter picks (None: no filter). A client is
    matched like everywhere in the app: ignoring case and surrounding spaces."""
    if project:
        return [project]
    if not client:
        return None
    wanted = client.strip().casefold()
    return [name for name, owner in owners(session).items() if owner.strip().casefold() == wanted]
