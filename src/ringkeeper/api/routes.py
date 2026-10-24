"""Read-only endpoints. Responses use the *Out schemas, which have no secret_ref."""

from collections.abc import Generator
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from ringkeeper.db import get_engine, session_scope
from ringkeeper.models import Env, InstanceType
from ringkeeper.schemas import BoundInstanceOut, InstanceOut, ProjectDetailOut, ProjectOut
from ringkeeper.services import bindings, instances, projects

router = APIRouter()


def get_session() -> Generator[Session]:
    # Read-only API: never commit.
    with session_scope(commit=False) as session:
        yield session


SessionDep = Annotated[Session, Depends(get_session)]


class Health(BaseModel):
    status: Literal["ok", "error"]
    database: Literal["ok", "unreachable"]


@router.get("/health")
def health(response: Response) -> Health:
    try:
        with get_engine().connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError:
        response.status_code = 503
        return Health(status="error", database="unreachable")
    return Health(status="ok", database="ok")


@router.get("/projects")
def list_projects(session: SessionDep) -> list[ProjectOut]:
    return [ProjectOut.model_validate(p) for p in projects.list_projects(session)]


@router.get("/projects/{name}")
def get_project(name: str, session: SessionDep) -> ProjectDetailOut:
    project = projects.get_project(session, name)
    bound = [
        BoundInstanceOut(
            **InstanceOut.model_validate(b.instance).model_dump(),
            is_default=b.is_default,
            allowed_tools=b.allowed_tools,
            preapproved_writes=b.preapproved_writes,
        )
        for b in bindings.sorted_bindings(project)
    ]
    return ProjectDetailOut(**ProjectOut.model_validate(project).model_dump(), instances=bound)


@router.get("/instances")
def list_instances(
    session: SessionDep, type: InstanceType | None = None, env: Env | None = None
) -> list[InstanceOut]:
    return [
        InstanceOut.model_validate(i) for i in instances.list_instances(session, type=type, env=env)
    ]
