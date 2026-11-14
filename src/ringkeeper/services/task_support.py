"""A task's details for support, from the task page's Copy details for support.

How it ran, never what it was about: its sessions in order and why each started (the request,
the operator's follow-up, or Ringkeeper going on by itself: services.goes_on), how each ended,
its held calls and the app's version. No prompt, result, project, client or error text: those
can hold a client's data.
"""

from collections import Counter
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ringkeeper import __version__
from ringkeeper.config import get_settings
from ringkeeper.models import Decision, Task, WorkerSession
from ringkeeper.services import goes_on, task_result, tasks

JSON = dict[str, object]


def _when(at: datetime | None) -> str:
    return at.strftime("%Y-%m-%d %H:%M:%S UTC") if at else "-"


def _session(row: WorkerSession) -> str:
    parts = [
        row.role.value,
        f"started for {goes_on.why(row)}",
        row.status.value,
        f"{row.turns} turns" if row.turns is not None else "turns unknown",
        row.model or "model unknown",
        f"{_when(row.started_at)} to {_when(row.finished_at)}",
    ]
    if row.error:
        parts.append("with an error")
    return " · ".join(parts)


def support_details(session: Session, ident: str) -> JSON:
    task: Task = tasks.get_task(session, ident)
    settings = get_settings()
    held = Counter(
        d.status.value for d in session.scalars(select(Decision).where(Decision.task_id == task.id))
    )
    lines: dict[str, str] = {
        "Task": f"{task.kind.value if task.kind else 'unset'} · {task.status.value}",
        "Ended": task_result.ended(session, task) or "-",
        "Created": _when(task.created_at),
        "Finished": _when(task.finished_at),
        "Result": f"{len(task.summary)} characters" if task.summary else "none",
        "Held calls": ", ".join(f"{n} {status}" for status, n in sorted(held.items())) or "none",
    }
    for row in sorted(task.sessions, key=lambda s: s.ordinal):
        lines[f"Session {row.ordinal}"] = _session(row)
    lines["Ringkeeper"] = f"{settings.app_version or __version__} ({settings.mode})"
    text = "\n".join(f"{k}: {v}" for k, v in lines.items())
    return {"lines": lines, "text": text}
