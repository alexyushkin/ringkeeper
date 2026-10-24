from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ringkeeper.errors import AlreadyExistsError, NotFoundError
from ringkeeper.models import Project, ProjectInstance
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
