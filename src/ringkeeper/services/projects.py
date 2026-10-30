from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ringkeeper.errors import AlreadyExistsError, NotFoundError
from ringkeeper.models import Instance, Project, ProjectInstance
from ringkeeper.schemas import ProjectIn


def find_project(session: Session, name: str) -> Project | None:
    return session.scalars(
        select(Project)
        .where(Project.name == name)
        .options(selectinload(Project.bindings).selectinload(ProjectInstance.instance))
    ).one_or_none()


def get_project(session: Session, name: str) -> Project:
    project = find_project(session, name)
    if project is None:
        raise NotFoundError(f"project '{name}' does not exist")
    return project


def list_projects(session: Session) -> Sequence[Project]:
    return session.scalars(select(Project).order_by(Project.name)).all()


def create_project(session: Session, data: ProjectIn) -> Project:
    if find_project(session, data.name) is not None:
        raise AlreadyExistsError(f"project '{data.name}' already exists")
    project = Project(**data.model_dump(), bindings=[])
    session.add(project)
    # Shared tools for all projects come with every new one.
    for tool in session.scalars(select(Instance).where(Instance.all_projects)):
        session.add(ProjectInstance(project=project, instance=tool))
    session.flush()
    return project


def update_project(session: Session, data: ProjectIn) -> Project:
    project = get_project(session, data.name)
    for field, value in data.model_dump(exclude={"name"}).items():
        setattr(project, field, value)
    session.flush()
    return project


def remove_project(session: Session, name: str) -> int:
    """Remove a project and its bindings. Returns how many bindings went with it."""
    project = get_project(session, name)
    removed_bindings = len(project.bindings)
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
