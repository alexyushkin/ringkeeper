"""A task that wasn't done for want of a connection: offered as one click.

- The client has one its project isn't linked to: "Link Harbor Bakery's Google to
  Website and retry", in the app and as a chat button.
- It has none: "Add a Meta Ads connection to Website", which opens the add form for that
  client and links what's added to the project (services.needs).
"""

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ringkeeper.config import Settings
from ringkeeper.errors import RuleViolationError
from ringkeeper.models import Instance, InstanceType, Project, Task
from ringkeeper.schemas import BindingIn
from ringkeeper.services import bindings, clients, needs, tasks, titles

# A built-in connection that can't do something the service's own server can (remote.py),
# when the client hasn't added that server: a lookup, not a model call. What the worker said
# it lacked (needs), or words in why it couldn't, point to it.
RECOMMENDED: dict[str, tuple[InstanceType, tuple[str, ...], str]] = {
    "airtable": (
        InstanceType.AIRTABLE,
        ("automation", "interface"),
        "Airtable's own server can see automations and interfaces: add it",
    ),
}


def recommend(session: Session, task: Task, lacked: str) -> dict[str, Any] | None:
    """One line, with a link to add the service's own server, when that's what was missing."""
    project = session.scalar(
        select(Project).options(selectinload(Project.bindings)).where(Project.name == task.project)
    )
    if project is None:
        return None
    why = f"{task.error or ''} {task.summary or ''}".lower()
    kinds = {b.instance_type for b in project.bindings}
    owned = session.scalars(select(Instance).where(Instance.owner == project.owner)).all()
    for remote, (built_in, words, says) in RECOMMENDED.items():
        # The project has the built-in connection, and it lacked what only the server has.
        pointed = built_in in kinds and (lacked == remote or any(w in why for w in words))
        has_server = any(
            i.type is InstanceType.MCP and (i.config or {}).get("remote") == remote for i in owned
        )
        if pointed and not has_server:
            return {
                "recommend": remote,
                "label": says,
                "href": f"/connections/new?service={remote}&client={project.owner}",
            }
    return None


def add_offer(session: Session, task: Task, lacked: str, owner: str) -> dict[str, Any] | None:
    """The add form for a kind of connection the client has none of: once it's added, the
    task can run again."""
    if needs.need(lacked) is None:
        return None
    return {
        "add": lacked,
        "label": needs.add_label(lacked, titles.of(session, task.project)),
        "href": needs.add_href(lacked, owner, task.project, run=f"task:{task.id}"),
    }


def find(session: Session, task: Task, lacked: str) -> dict[str, Any] | None:
    """The client's connection of that kind that the task's project isn't linked to, or the
    form to add one."""
    lacked = lacked.strip().lower()
    project = session.scalar(
        select(Project).options(selectinload(Project.bindings)).where(Project.name == task.project)
    )
    if project is None:
        return None
    if not lacked:
        return recommend(session, task, lacked)
    linked = {b.instance_id for b in project.bindings}
    owned = session.scalars(
        select(Instance).where(Instance.owner == project.owner).order_by(Instance.name)
    ).all()
    found = next((i for i in owned if i.id not in linked and needs.fits(i, lacked)), None)
    if found is None:
        return recommend(session, task, lacked) or add_offer(session, task, lacked, project.owner)
    return {
        "instance": found.name,
        "project": project.name,
        "label": label(session, found, project),
    }


def label(session: Session, instance: Instance, project: Project) -> str:
    client = clients.display(session, project.owner)
    what = instance.title or instance.name
    return f"Link {client}'s {what} to {titles.of(session, project.name)} and retry"


def take(session: Session, settings: Settings, task_id: str) -> Task:
    """Link the offered connection and run the task again. Returns the new task."""
    task = session.get(Task, task_id)
    if task is None or not task.offer or "instance" not in task.offer:
        raise RuleViolationError("There's nothing to link for this task any more.")
    offer = task.offer
    bindings.set_binding(
        session, BindingIn(project=str(offer["project"]), instance=str(offer["instance"]))
    )
    task.offer = None
    return tasks.create_task(
        session, settings, project=task.project, prompt=task.prompt, kind=task.kind,
        risk=task.risk, complexity=task.complexity, outcome=task.outcome,
        thread_id=task.thread_id, message_id=task.message_id, agent_id=task.agent_id,
    )  # fmt: skip


def run_again(session: Session, settings: Settings, task_id: str) -> Task:
    """A task not done for want of a kind of connection, run again now its project has one."""
    task = session.get(Task, task_id)
    if task is None or not task.offer or "add" not in task.offer:
        raise RuleViolationError("There's nothing waiting for a connection here any more.")
    lacked = str(task.offer["add"])
    if needs.still_missing(session, task.project, lacked):
        title = titles.of(session, task.project)
        said = needs.no_connection(lacked).replace("This project", title, 1)
        raise RuleViolationError(said.replace(" connection.", " connection yet."))
    task.offer = None
    return tasks.create_task(
        session, settings, project=task.project, prompt=task.prompt, kind=task.kind,
        risk=task.risk, complexity=task.complexity, outcome=task.outcome,
        thread_id=task.thread_id, message_id=task.message_id, agent_id=task.agent_id,
    )  # fmt: skip
