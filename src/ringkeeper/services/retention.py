"""Retention: full transcripts go after `retention_days` (default 90).

Only session events (the transcript lines, tool arguments and results) are
deleted. Tasks with their summaries, sessions with their models, turns and
costs, decisions and the audit log stay. The api's supervisor runs this once
a day.
"""

from datetime import UTC, datetime, timedelta
from typing import Any, cast

from sqlalchemy import CursorResult, delete
from sqlalchemy.orm import Session

from ringkeeper.models import SessionEvent, Setting
from ringkeeper.storage.sql import insert

LAST_RUN_KEY = "retention.last_run"
EVERY = timedelta(days=1)


def purge(session: Session, days: int, now: datetime | None = None) -> int:
    """Delete session events older than `days`. Returns how many went."""
    now = now or datetime.now(UTC)
    result = cast(
        CursorResult[Any],
        session.execute(delete(SessionEvent).where(SessionEvent.at < now - timedelta(days=days))),
    )
    deleted = int(result.rowcount or 0)
    statement = insert(session, Setting).values(
        key=LAST_RUN_KEY, value={"at": now.isoformat(), "deleted": deleted}
    )
    session.execute(
        statement.on_conflict_do_update(
            index_elements=["key"], set_={"value": statement.excluded.value}
        )
    )
    return deleted


def due(session: Session, now: datetime | None = None) -> bool:
    now = now or datetime.now(UTC)
    row = session.get(Setting, LAST_RUN_KEY)
    return row is None or datetime.fromisoformat(row.value["at"]) + EVERY <= now
