"""What happens when a session of a feature task ends (called by the session manager)."""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from ringkeeper.config import Settings
from ringkeeper.models import (
    ArchitectPhase,
    DecisionKind,
    SessionRole,
    Task,
    TaskStatus,
    WorkerSession,
)
from ringkeeper.services import architect, inbox, tasks

# A session that ended like this stops the loop: the task takes its status.
FINAL = frozenset({TaskStatus.STOPPED, TaskStatus.TIMED_OUT, TaskStatus.BUDGET_EXCEEDED})


def _fail(task: Task, error: str) -> None:
    task.status = TaskStatus.FAILED
    task.error = error
    task.finished_at = datetime.now(UTC)


def _architect_ended(session: Session, settings: Settings, row: WorkerSession, task: Task) -> None:
    action: dict[str, Any] = row.action or {}
    match action.get("tool"):
        case "submit_plan":
            task.phase = ArchitectPhase.AWAITING_PLAN
            task.worker_prompt = action["worker_prompt"]
            task.status = TaskStatus.WAITING
            task.finished_at = None
            payload = {k: action[k] for k in ("title", "plan", "worker_prompt")}
            inbox.create(
                session, settings, DecisionKind.PLAN, payload, project=task.project, task=task
            )
        case "dispatch_worker":
            task.worker_prompt = action["prompt"]
            tasks.add_session(session, settings, task, action["prompt"], SessionRole.WORKER)
        case "continue_worker":
            worker_sdk = tasks.last_sdk_session(task, SessionRole.WORKER)
            tasks.add_session(
                session, settings, task, action["feedback"], SessionRole.WORKER,
                sdk_session_id=worker_sdk,
            )  # fmt: skip
        case "escalate":
            task.status = TaskStatus.NEEDS_INPUT
            task.finished_at = None
            question = (
                f"The architect did {architect.MAX_REVIEWS} reviews and still wants changes: "
                f"{action['feedback']}\nShould the worker go on (and with what), or stop here?"
            )
            payload = {"question": question, "escalation": True, "role": "architect"}
            inbox.create(
                session, settings, DecisionKind.QUESTION, payload, project=task.project, task=task
            )
        case "approve_work":
            task.phase = ArchitectPhase.DONE
            task.summary = action["summary"]
            task.status = TaskStatus.SUCCEEDED
        case _:
            expected = {
                ArchitectPhase.PLANNING: "submit_plan",
                ArchitectPhase.BUILDING: "dispatch_worker",
                ArchitectPhase.REVIEWING: "approve_work or continue_worker",
            }.get(task.phase or ArchitectPhase.PLANNING, "an architect tool")
            _fail(task, f"the architect ended its turn without calling {expected}")


def _worker_ended(
    session: Session,
    settings: Settings,
    task: Task,
    status: TaskStatus,
    review: dict[str, Any],
) -> None:
    checks: dict[str, Any] | None = review.get("checks")
    if checks is not None and not checks.get("passed", True):
        task.failed_check_rounds += 1
    task.review_round = min(task.review_round + 1, architect.MAX_REVIEWS)
    task.phase = ArchitectPhase.REVIEWING
    prompt = architect.review_prompt(
        task, status, checks, review.get("diff"), review.get("result"), review.get("error")
    )
    sdk = tasks.last_sdk_session(task, SessionRole.ARCHITECT)
    tasks.add_session(session, settings, task, prompt, SessionRole.ARCHITECT, sdk_session_id=sdk)


def advance(
    session: Session,
    settings: Settings,
    row: WorkerSession,
    status: TaskStatus,
    review: dict[str, Any],
) -> None:
    """Move a feature task on after one of its sessions ended with `status`.

    `review` carries the worker turn's checks, diff, final message and error.
    """
    task = row.task
    if status in FINAL or status is TaskStatus.NEEDS_INPUT:
        return  # the task already took this status; a question resumes it later
    if row.role is SessionRole.ARCHITECT:
        if status is TaskStatus.SUCCEEDED or row.action:
            _architect_ended(session, settings, row, task)
        return
    if task.phase in (ArchitectPhase.BUILDING, ArchitectPhase.REVIEWING):
        _worker_ended(session, settings, task, status, review)
