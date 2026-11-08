"""ringkeeper_ask: an AI app with a pass hands Ringkeeper a request, as if it were typed in the app.

It becomes a normal task in the pass's project, and only there: a conversation in the app (so
the user sees it), the usual task limits and spending cap, the gateway's checks and holds on
every call it makes, and a line in the audit log. The app can follow only the tasks its own
pass started.
"""

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ringkeeper.calls import Caller
from ringkeeper.config import Settings
from ringkeeper.errors import NotFoundError, RingkeeperError
from ringkeeper.models import (
    TERMINAL_STATUSES,
    Decision,
    DecisionStatus,
    Message,
    Task,
    TaskStatus,
)
from ringkeeper.scrub import scrub
from ringkeeper.services import admin_audit, holds, inbox, passes, tasks, threads

SOURCE = "pass"
WAIT_SECONDS = 120.0  # how long ringkeeper_ask waits for the task before saying it's running
CHECK_SECONDS = 1.0
RESULT_CHARS = 4000
OUTCOME_CHARS = 80
NOT_YOURS = "There's no task with that id for this pass."
STILL_RUNNING = (
    "It's still running in Ringkeeper. Call ringkeeper_task_status with this task_id later to "
    "get its result; the user can follow it at the task link."
)
SAYS = {
    "running": "It's running in Ringkeeper.",
    "held": (
        "It's waiting for the user's approval of a change in Ringkeeper. The task goes on once "
        "they decide; ringkeeper_task_status says how it went."
    ),
    "asking": "It's asking the user something. They answer it in Ringkeeper, at the task link.",
    "waiting": "It's waiting for the user in Ringkeeper, at the task link.",
    "done": "It's done.",
    "failed": "It didn't finish.",
    "stopped": "It was stopped.",
}


def _outcome(request: str) -> str:
    line = request.strip().splitlines()[0] if request.strip() else ""
    return line if len(line) <= OUTCOME_CHARS else line[: OUTCOME_CHARS - 1] + "…"


def start(session: Session, settings: Settings, caller: Caller, request: str) -> Task:
    """The request as a task in the pass's project, in a conversation of its own."""
    if not passes.is_pass_session(caller.session_id) or caller.project is None:
        raise RingkeeperError("only a pass can hand Ringkeeper a request here")
    message = threads.post_message(session, request, author=str(caller.session_id), source=SOURCE)
    # Not for the router: a pass reaches only its own project.
    threads.mark_routed(session, message, {"via": SOURCE, "project": caller.project})
    task = tasks.create_task(
        session, settings, project=caller.project, prompt=message.body,
        outcome=_outcome(message.body), thread_id=message.thread_id, message_id=message.id,
    )  # fmt: skip
    name = str(caller.session_id).removeprefix(passes.SESSION)
    said = f"Asked through the pass {name}. {threads.working_on(session, task)}"
    threads.add_reply(session, message.thread_id, said)
    admin_audit.record(
        session, "task.asked", f"started a task: {task.outcome}", by=str(caller.session_id),
        project=caller.project, details={"task": str(task.id)}, session_id=caller.session_id,
    )  # fmt: skip
    return task


def _mine(session: Session, caller: Caller, task_id: str) -> Task:
    try:
        task = tasks.get_task(session, task_id)
    except (NotFoundError, RingkeeperError):
        raise RingkeeperError(NOT_YOURS) from None
    asked = session.get(Message, task.message_id) if task.message_id else None
    if asked is None or asked.author != caller.session_id or task.project != caller.project:
        raise RingkeeperError(NOT_YOURS)
    return task


def _waiting_on(session: Session, task_id: uuid.UUID) -> list[Decision]:
    query = select(Decision).where(
        Decision.task_id == task_id, Decision.status == DecisionStatus.PENDING
    )
    return list(session.scalars(query.order_by(Decision.created_at)))


def _state(task: Task, pending: list[Decision]) -> str:  # noqa: PLR0911 (one per state)
    if any(inbox.is_approval(d) for d in pending):
        return "held"
    if task.status is TaskStatus.SUCCEEDED:
        return "done"
    if task.status is TaskStatus.STOPPED:
        return "stopped"
    if task.status in TERMINAL_STATUSES:
        return "failed"
    if task.status is TaskStatus.NEEDS_INPUT:
        return "asking"
    if pending or task.status is TaskStatus.WAITING:
        return "waiting"
    return "running"


def status(session: Session, settings: Settings, caller: Caller, task_id: str) -> dict[str, Any]:
    """Where a task this pass started stands, with its result once it's done, or where the user
    decides when it waits on them."""
    from ringkeeper.channels import links  # noqa: PLC0415 (links reaches the inbox)

    task = _mine(session, caller, task_id)
    pending = _waiting_on(session, task.id)
    state = _state(task, pending)
    found: dict[str, Any] = {"task_id": str(task.id), "status": state, "says": SAYS[state]}
    page = links.open_url(session, settings, links.task_page(settings, task.id))
    if page:
        found["task"] = page
    if state == "held":
        found["held"] = [holds.label(d) for d in pending if inbox.is_approval(d)]
        found |= passes.approval_link(session, settings)
    if state == "done":
        found["result"] = scrub((task.summary or "")[:RESULT_CHARS])
    if state in ("failed", "stopped") and task.error:
        found["error"] = scrub(task.error)
    return found
