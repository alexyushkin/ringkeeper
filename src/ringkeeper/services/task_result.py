"""How a finished task ended, from what happened: what the gateway executed or refused for it,
and the files it changed. Never from the request or the worker's own words: a task that read a
record and left it alone isn't "Done" because the request said "handle it".

- Done: the gateway recorded a call on a connection as executed, or the task changed files.
- Refused: other client's data: the gateway refused a call on another client's connection,
  and nothing was executed.
- Nothing changed: a change was asked for, or tried (held, refused, failed), and none ran.
- Answered: a read that changed nothing, as asked.

A task that didn't succeed keeps its own status (Not done, Failed...), unless the other
client's data was refused.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from ringkeeper.models import (
    AuditEvent,
    AuditOutcome,
    Instance,
    Project,
    Task,
    TaskRisk,
    TaskStatus,
)
from ringkeeper.wording import task_status

DONE = "done"
ANSWERED = "answered"
NOTHING_CHANGED = "nothing_changed"
REFUSED_OTHER = "refused_other_client"

LABELS = {
    DONE: "Done",
    ANSWERED: "Answered",
    NOTHING_CHANGED: "Nothing changed",
    REFUSED_OTHER: "Refused: other client's data",
}
TRIED = frozenset({AuditOutcome.HELD, AuditOutcome.REFUSED, AuditOutcome.FAILED})
CHANGES = frozenset({TaskRisk.WRITE, TaskRisk.PROD_WRITE})


def _other_client(session: Session, project: str, names: set[str]) -> bool:
    """Whether any of these connections is another client's (not shared, owned elsewhere)."""
    if not names:
        return False
    owner = session.scalar(select(Project.owner).where(Project.name == project))
    found = session.execute(
        select(Instance.owner, Instance.shared).where(Instance.name.in_(names))
    ).all()
    return any(not shared and owner is not None and other != owner for other, shared in found)


def executed(session: Session, task: Task) -> list[AuditEvent]:
    """The calls on a connection the gateway ran for this task (notes it kept don't count)."""
    return [e for e in _events(session, task) if e.outcome is AuditOutcome.EXECUTED and e.instance]


def _events(session: Session, task: Task) -> list[AuditEvent]:
    ids = [str(s.id) for s in task.sessions]
    if not ids:
        return []
    return list(
        session.scalars(
            select(AuditEvent).where(
                AuditEvent.project == task.project, AuditEvent.session_id.in_(ids)
            )
        ).all()
    )


def ended(session: Session, task: Task) -> str | None:
    """One of the keys above for a task that ended by doing its work or saying it couldn't;
    None for any other status, which says enough on its own."""
    if task.status not in (TaskStatus.SUCCEEDED, TaskStatus.NOT_DONE):
        return None
    events = _events(session, task)
    ran = any(e.outcome is AuditOutcome.EXECUTED and e.instance for e in events)
    refused = {e.instance for e in events if e.outcome is AuditOutcome.REFUSED and e.instance}
    if not ran and _other_client(session, task.project, refused):
        return REFUSED_OTHER
    if task.status is not TaskStatus.SUCCEEDED:
        return None
    if ran or task.changed_files:
        return DONE
    tried = any(e.outcome in TRIED and e.instance for e in events)
    return NOTHING_CHANGED if tried or task.risk in CHANGES else ANSWERED


def words(session: Session | None, task: Task) -> str:
    """The status in a chat's words: "done", "nothing changed", "not done"..."""
    key = ended(session, task) if session is not None else None
    return LABELS[key].lower() if key else task_status(task.status)
