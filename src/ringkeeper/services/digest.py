"""The morning digest: per client, what ran, what failed and what waits for you.

Home always shows it for the last day. With `digest_enabled` it's also sent at
`digest_time` (this computer's clock) to the channels that are on, once a day.
"""

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ringkeeper.config import Settings
from ringkeeper.models import Decision, DecisionStatus, Project, Setting, Task, TaskStatus
from ringkeeper.services import announce, clients, inbox

LAST_KEY = "digest.last"
WINDOW = timedelta(days=1)
FAILED = (TaskStatus.FAILED, TaskStatus.TIMED_OUT, TaskStatus.BUDGET_EXCEEDED)


@dataclass
class ClientDay:
    client: str
    name: str
    ran: int = 0
    failed: list[str] = field(default_factory=list[str])
    waiting: list[str] = field(default_factory=list[str])


def build(session: Session, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    since = now - WINDOW
    owners = dict(session.execute(select(Project.name, Project.owner)).all())
    names = clients.names(session)
    days: dict[str, ClientDay] = {}

    def day(project: str | None) -> ClientDay:
        slug = owners.get(project or "", "")
        return days.setdefault(slug, ClientDay(slug, names.get(slug, slug) or "Not a project yet"))

    for task in session.scalars(select(Task).where(Task.created_at >= since)):
        one = day(task.project)
        one.ran += 1
        if task.status in FAILED:
            one.failed.append(f"{task.project}: {(task.outcome or task.prompt)[:120]}")
    pending = select(Decision).where(Decision.status == DecisionStatus.PENDING)
    for decision in session.scalars(pending):
        day(decision.project).waiting.append(inbox.summary(decision, 120))
    listed = sorted(days.values(), key=lambda d: (not d.waiting, not d.failed, d.name.lower()))
    return {"since": since.isoformat(), "clients": [asdict(d) for d in listed]}


def text(digest: dict[str, Any]) -> str:
    """As a plain message."""
    lines = ["Ringkeeper this morning"]
    for one in digest["clients"]:
        parts = [f"{one['ran']} ran"]
        if one["failed"]:
            parts.append(f"{len(one['failed'])} failed")
        if one["waiting"]:
            parts.append(f"{len(one['waiting'])} waiting for you")
        lines.append(f"{one['name']}: {', '.join(parts)}")
        lines += [f"  ✖ {item}" for item in one["failed"][:3]]
        lines += [f"  ⏳ {item}" for item in one["waiting"][:3]]
    if len(lines) == 1:
        lines.append("Nothing ran in the last day, and nothing is waiting.")
    return "\n".join(lines)


def due(session: Session, settings: Settings, now: datetime | None = None) -> bool:
    if not settings.digest_enabled:
        return False
    local = (now or datetime.now(UTC)).astimezone()
    row = session.get(Setting, LAST_KEY)
    if row is not None and row.value.get("day") == local.date().isoformat():
        return False
    return local.strftime("%H:%M") >= settings.digest_time


def send_if_due(session: Session, settings: Settings, now: datetime | None = None) -> bool:
    if not due(session, settings, now):
        return False
    day = (now or datetime.now(UTC)).astimezone().date().isoformat()
    row = session.get(Setting, LAST_KEY)
    if row is None:
        session.add(Setting(key=LAST_KEY, value={"day": day}))
    else:
        row.value = {"day": day}
    session.flush()
    announce.send(session, settings, text(build(session, now)))
    return True
