"""Held gateway calls and the task that made them.

A held call answers the agent at once: the user already sees it in Approvals,
so the agent ends its turn instead of asking about it or calling it again.
When its session ends with held calls still undecided, the task waits. Once
every one of them is decided (run, failed, denied or expired), the same
session is resumed with what happened to each, so the result reaches the
agent and nothing is held twice.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from ringkeeper.config import Settings
from ringkeeper.models import (
    Decision,
    DecisionKind,
    DecisionStatus,
    InstanceType,
    Task,
    TaskStatus,
    WorkerSession,
)
from ringkeeper.services import decisions, tasks

RESULT_CHARS = 4000


def held_by(session: Session, row: WorkerSession) -> list[Decision]:
    """The held calls one worker session made, oldest first."""
    query = (
        select(Decision)
        .where(Decision.kind == DecisionKind.APPROVE, Decision.session_id == str(row.id))
        .order_by(Decision.created_at)
    )
    return list(session.scalars(query))


def label(decision: Decision) -> str:
    """'tool' on connection, naming an MCP server's own tool rather than call_tool."""
    tool = decision.tool or "a call"
    if decision.service is InstanceType.MCP and isinstance(decision.args, dict):
        tool = str(decision.args.get("tool") or tool)
    return f"'{tool}' on {decision.instance}"


def _outcome(decision: Decision) -> str:
    match decision.status:
        case DecisionStatus.EXECUTED:
            result = (decision.result or "")[:RESULT_CHARS]
            return f"{label(decision)}: approved and run. Result: {result or '(empty)'}"
        case DecisionStatus.FAILED:
            return f"{label(decision)}: approved, but it failed: {decision.error}"
        case DecisionStatus.DENIED:
            why = f" ({decision.reason})" if decision.reason else ""
            return f"{label(decision)}: the user said no{why}. Don't try it again."
        case _:
            return f"{label(decision)}: no one decided in time, so it didn't run."


def report(held: list[Decision]) -> str:
    lines = "\n".join(f"- {_outcome(d)}" for d in held)
    return (
        f"The calls that were waiting for the user's approval are decided:\n{lines}\n\n"
        "Don't repeat a call that ran. Continue the task from here."
    )


def _resume_if_decided(
    session: Session, settings: Settings, task: Task, row: WorkerSession, held: list[Decision]
) -> bool:
    if any(d.status in decisions.UNSETTLED for d in held):
        return False
    tasks.add_session(
        session, settings, task, report(held), row.role, sdk_session_id=row.sdk_session_id
    )
    return True


def session_ended(
    session: Session, settings: Settings, row: WorkerSession, status: TaskStatus
) -> bool:
    """Called as a session ends. True if its task now waits on (or resumed after) held calls.

    A question asked alongside held calls is dropped: it's almost always about
    them, and the user already sees them. If it wasn't, the agent can ask
    again once it's resumed.
    """
    if status not in (TaskStatus.SUCCEEDED, TaskStatus.NEEDS_INPUT):
        return False
    held = held_by(session, row)
    if not held:
        return False
    task = row.task
    task.status = TaskStatus.WAITING
    task.finished_at = None
    _resume_if_decided(session, settings, task, row, held)
    return True


def resume_decided(session: Session, settings: Settings) -> int:
    """Resume each waiting task whose held calls are all decided (the manager's tick)."""
    decisions.expire_stale(session)
    resumed = 0
    waiting = session.scalars(select(Task).where(Task.status == TaskStatus.WAITING)).all()
    for task in waiting:
        row = tasks.latest_session(task)
        held = held_by(session, row)  # none: it waits on something else (a plan)
        if held and _resume_if_decided(session, settings, task, row, held):
            resumed += 1
    return resumed
