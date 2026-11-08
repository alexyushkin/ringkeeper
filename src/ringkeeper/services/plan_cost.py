"""What a plan's card says about running it: the model that would do the work, and what
tasks like it have cost, from the record of finished ones, not a guess.

"Like it": planned tasks of the same size (complexity) that finished, the project's own
when it has enough of them, else every project's. The range leaves out the cheapest and
dearest fifth, so one odd task doesn't stretch it. With none to go by, it says so; the task's
own limit, if it has one, is the most it can spend either way.
"""

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ringkeeper import model_choice
from ringkeeper.config import Settings
from ringkeeper.models import AuthMode, SessionRole, Task, TaskStatus, WorkerSession
from ringkeeper.services import projects

LOOK_BACK = 30  # the latest this many like it
ENOUGH = 3  # fewer than this from the project: every project's count


def _costs(session: Session, task: Task, project: str | None) -> list[float]:
    total = func.coalesce(func.sum(WorkerSession.cost_usd), 0.0)
    query = (
        select(total)
        .select_from(Task)
        .join(WorkerSession, WorkerSession.task_id == Task.id)
        .where(
            Task.status == TaskStatus.SUCCEEDED,
            Task.phase.is_not(None),
            Task.id != task.id,
        )
        .group_by(Task.id)
        .order_by(func.max(Task.finished_at).desc())
        .limit(LOOK_BACK)
    )
    if task.complexity is not None:
        query = query.where(Task.complexity == task.complexity)
    if project is not None:
        query = query.where(Task.project == project)
    return sorted(float(cost) for cost in session.scalars(query) if cost)


def _at(costs: list[float], share: float) -> float:
    return costs[min(int(len(costs) * share), len(costs) - 1)]


def estimate(session: Session, settings: Settings, task: Task) -> dict[str, Any]:
    """{model, low, high, based_on, spent, limit, subscription}: low/high are None with no
    finished task like it to go by."""
    owner = projects.find_project(session, task.project)
    costs = _costs(session, task, task.project)
    if len(costs) < ENOUGH:
        costs = _costs(session, task, None)
    spent = session.scalar(
        select(func.coalesce(func.sum(WorkerSession.cost_usd), 0.0)).where(
            WorkerSession.task_id == task.id
        )
    )
    return {
        "model": model_choice.session_model(settings, owner, SessionRole.WORKER, task.complexity),
        "low": round(_at(costs, 0.2), 2) if costs else None,
        "high": round(_at(costs, 0.8), 2) if costs else None,
        "based_on": len(costs),
        "spent": round(float(spent or 0.0), 2),
        "limit": task.budget_usd,
        "subscription": task.auth_mode is AuthMode.SUBSCRIPTION,
    }


def said(estimate: dict[str, Any]) -> str:
    """The estimate in one line, for a notification."""
    parts = [f"Model: {estimate.get('model')}"]
    low, high = estimate.get("low"), estimate.get("high")
    if low is not None and high is not None:
        parts.append(f"tasks like it cost ${low:,.2f} to ${high:,.2f}")
    if estimate.get("limit"):
        parts.append(f"stops at ${float(estimate['limit']):,.2f}")
    return " · ".join(parts)
