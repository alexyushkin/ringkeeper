"""A task that wasn't done for want of a connection its client has, but its project
isn't linked to: offered as one click, "Link General Informatics' Google to Website and
retry", in the app and as a chat button."""

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ringkeeper.config import Settings
from ringkeeper.errors import RuleViolationError
from ringkeeper.models import Instance, InstanceType, Project, Task
from ringkeeper.schemas import BindingIn
from ringkeeper.services import bindings, clients, tasks, titles

# What a worker can say it lacked, and the kinds of connection that give it.
KINDS: dict[str, tuple[InstanceType, ...]] = {
    "email": (
        InstanceType.GOOGLE,
        InstanceType.GMAIL,
        InstanceType.MAILBOX,
        InstanceType.SMTP,
        InstanceType.RESEND,
    ),
    "google": (InstanceType.GOOGLE,),
    "database": (InstanceType.SUPABASE, InstanceType.NEON, InstanceType.ORACLE),
    "airtable": (InstanceType.AIRTABLE,),
    "n8n": (InstanceType.N8N,),
    "github": (InstanceType.GITHUB,),
    "vercel": (InstanceType.VERCEL,),
}


def _fits(instance: Instance, needs: str) -> bool:
    if instance.type in KINDS.get(needs, ()):
        return True
    named = f"{instance.name} {instance.title or ''} {instance.config.get('vendor', '')}"
    return needs == "slack" and "slack" in named.lower()


def find(session: Session, task: Task, needs: str) -> dict[str, Any] | None:
    """The client's connection of that kind that the task's project isn't linked to."""
    needs = needs.strip().lower()
    project = session.scalar(
        select(Project).options(selectinload(Project.bindings)).where(Project.name == task.project)
    )
    if not needs or project is None:
        return None
    linked = {b.instance_id for b in project.bindings}
    owned = session.scalars(
        select(Instance).where(Instance.owner == project.owner).order_by(Instance.name)
    ).all()
    found = next((i for i in owned if i.id not in linked and _fits(i, needs)), None)
    if found is None:
        return None
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
    if task is None or not task.offer:
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
