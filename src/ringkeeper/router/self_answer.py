"""Questions about Ringkeeper itself, answered from its own data: no project, no worker.

"How many connections do we have?", "what's waiting for me?", "what did tasks cost
this week?" are about the operator's setup, not a client's systems. The router
marks them (`about_ringkeeper`), and one model call answers them from a snapshot
of Ringkeeper's own records, read only: projects and clients, connections (names,
services, whose, which projects; never a secret or an address), tasks, what is
waiting, the settings that aren't secret, and costs.
"""

import json
import re
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from ringkeeper.config import Settings
from ringkeeper.model_choice import router_model
from ringkeeper.models import (
    Decision,
    DecisionStatus,
    Instance,
    Project,
    Task,
    WorkerSession,
)
from ringkeeper.router.model import RouterModel
from ringkeeper.services import inbox

RECENT_TASKS = 25

SYSTEM_PROMPT = """\
You are Ringkeeper, answering the operator's question about their own Ringkeeper
setup. RINGKEEPER DATA below is everything you know: their projects and clients,
connections, tasks, what is waiting for them, settings and costs. Answer from it
only. Count carefully and give exact numbers and names. If the data doesn't say,
say so plainly; never guess and never invent. You can't change anything: if they
want something changed, say where in the app to do it (Connectors, Clients &
projects, Settings, Approvals). Answer in a few short sentences or a short list, plain text.
"""

# A question about the setup itself, naming no client: "how many connections do we have?"
_SETUP_WORDS = re.compile(
    r"\b(connections?|projects?|clients?|tasks?|approvals?|settings?|costs?|spend|spent|"
    r"spending|budget|models?|waiting|pending)\b",
    re.I,
)
_OURS = re.compile(r"\b(we|our|ours|my|mine|i|me|ringkeeper)\b", re.I)
_ASKS = re.compile(
    r"^\s*(how|what|which|who|where|when|list|show|tell|do|does|are|is|did)\b|\?\s*$", re.I
)


def asks_about_setup(message: str, names: list[str]) -> bool:
    """A question about the operator's own setup that names no project, client or alias.
    The router's model decides too; this catches the plain cases it might send to a
    project anyway."""
    lowered = message.lower()
    if any(re.search(rf"(?<![\w-]){re.escape(n.lower())}(?![\w-])", lowered) for n in names if n):
        return False
    return bool(_ASKS.search(message) and _SETUP_WORDS.search(message) and _OURS.search(message))


def _since(session: Session, start: datetime) -> float:
    total = session.scalar(
        select(func.sum(WorkerSession.cost_usd)).where(WorkerSession.created_at >= start)
    )
    return round(float(total or 0), 2)


def snapshot(session: Session, settings: Settings) -> dict[str, Any]:
    """Ringkeeper's own records, without secrets or addresses."""
    projects = session.scalars(
        select(Project).options(selectinload(Project.bindings)).order_by(Project.name)
    ).all()
    connections = session.scalars(
        select(Instance).options(selectinload(Instance.bindings)).order_by(Instance.name)
    ).all()
    counts = dict(
        session.execute(select(Task.status, func.count()).group_by(Task.status)).tuples().all()
    )
    recent = session.scalars(select(Task).order_by(Task.created_at.desc()).limit(RECENT_TASKS))
    waiting = session.scalars(
        select(Decision).where(Decision.status == DecisionStatus.PENDING)
    ).all()
    now = datetime.now(UTC)
    clients = sorted({p.owner for p in projects}, key=str.lower)
    shown = {p.name: p.title for p in projects}
    return {
        "today": now.date().isoformat(),
        "clients": [
            {"name": c, "projects": sorted(p.title for p in projects if p.owner == c)}
            for c in clients
        ],
        "projects": [
            {
                "name": p.title,
                "client": p.owner,
                "description": p.description,
                "other_names": [p.name, *p.aliases],
                "autonomy": p.autonomy.value,
                "connections": sorted(b.instance.name for b in p.bindings),
            }
            for p in projects
        ],
        "connections": [
            {
                "name": i.name,
                "service": i.type.value,
                "kind_of_data": i.env.value,
                "whose": "yours (a shared tool)" if i.shared else i.owner,
                "all_projects": i.all_projects,
                "projects": sorted(b.project.title for b in i.bindings),
                "last_test": None
                if i.last_test_ok is None
                else ("works" if i.last_test_ok else "failed"),
            }
            for i in connections
        ],
        "tasks": {
            "by_status": {s.value: n for s, n in counts.items()},
            "recent": [
                {
                    "project": shown.get(t.project, t.project),
                    "status": t.status.value,
                    "what": (t.outcome or t.prompt)[:200],
                    "created": t.created_at.isoformat(),
                }
                for t in recent
            ],
        },
        "waiting_for_you": [
            {
                "kind": "approval" if inbox.is_approval(d) else "question",
                "project": shown.get(d.project or "", d.project),
                "what": inbox.summary(d),
            }
            for d in waiting
        ],
        "settings": {
            "claude": settings.claude_auth_mode.value,
            "models": {
                "router": settings.model_router,
                "architect": settings.model_architect,
                "worker": settings.model_worker,
            },
            "spending_cap_per_task_usd": settings.task_budget_usd,
            "tasks_at_once": settings.max_concurrent_workers,
            "everything_stopped": settings.paused,
            "telegram": settings.telegram,
        },
        "costs_usd": {
            "today": _since(session, now.replace(hour=0, minute=0, second=0, microsecond=0)),
            "last_7_days": _since(session, now - timedelta(days=7)),
            "this_month": _since(
                session, now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            ),
            "all_time": _since(session, datetime(2000, 1, 1, tzinfo=UTC)),
            "note": "What Ringkeeper recorded for tasks' model use (a subscription isn't "
            "billed per task).",
        },
    }


def answer(
    session: Session, settings: Settings, model: RouterModel, question: str
) -> tuple[str, float]:
    """The answer, and what the model call cost."""
    data = json.dumps(snapshot(session, settings), indent=1, default=str)
    prompt = f"RINGKEEPER DATA:\n{data}\n\nQUESTION:\n{question}"
    completion = model.complete(SYSTEM_PROMPT, prompt, router_model(settings))
    text = completion.text.strip() or "I couldn't find that in Ringkeeper's records."
    return text, completion.cost_usd or 0.0
