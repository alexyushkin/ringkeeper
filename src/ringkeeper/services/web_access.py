"""Whether a project's tasks can use the web: a setting of its own, apart from autonomy.
Unset, it's on for the operator's own client's projects and off for a client's (their data
stays put unless you allow it). A task that needs the web in a project without it asks once,
with "Allow web for this project and retry"."""

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ringkeeper.models import Project
from ringkeeper.services import admin_audit, clients, titles

TOOLS = ["WebFetch", "WebSearch"]
ALLOW = "Allow web for this project and retry"
NOT_NOW = "Not now"
ASKS = "Use the web?"


def allowed(session: Session, project: Project) -> bool:
    if project.web is not None:
        return project.web
    return project.owner == clients.own_client(session)


def default(session: Session, project: Project) -> bool:
    """What it is when it isn't set."""
    return project.owner == clients.own_client(session)


def refusal(session: Session, project: str) -> str:
    return f"Tasks in {titles.of(session, project)} can't use the web: it's off for this project."


def ask_payload(
    session: Session, project: str, message: str, task: dict[str, Any]
) -> dict[str, Any]:
    """A route decision that asks to allow the web for one project, then runs the task."""
    return {
        "message": message,
        "candidates": [{"project": project, "confidence": 1.0}],
        "task": task,
        "web": titles.of(session, project),
        "reason": refusal(session, project),
    }


def allow(session: Session, name: str, *, by: str) -> None:
    project = session.scalar(select(Project).where(Project.name == name))
    if project is None or allowed(session, project):
        return
    project.web = True
    admin_audit.record(
        session, "project.web", f"allowed the web for {titles.of(session, name)}", by=by,
        project=name,
    )  # fmt: skip
