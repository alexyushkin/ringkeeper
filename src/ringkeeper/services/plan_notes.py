"""Keeping what a service's plan doesn't allow: on the connection, so its card can say so and the
same call isn't sent again, and on the task, so a stopped task says why and doesn't count as a
failed attempt. See ringkeeper.plan_limits."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from ringkeeper.db import session_scope
from ringkeeper.models import Task, WorkerSession
from ringkeeper.services import instances

KEY = "plan_limit"
# Long enough to stop a retry, short enough to notice an upgrade. Test clears it at once.
REMEMBERED = timedelta(hours=1)


def note(name: str, tool: str | None, message: str) -> None:
    """`tool`: what was refused. None: only that the connection is limited (from Test)."""
    with session_scope() as session:
        instance = instances.find_instance(session, name)
        if instance is None:
            return
        config = dict(instance.config or {})
        kept = cast(dict[str, Any], config.get(KEY) or {})
        tools = sorted({*cast(list[str], kept.get("tools") or []), *([tool] if tool else [])})
        at = datetime.now(UTC).isoformat()
        instance.config = {**config, KEY: {"message": message, "tools": tools, "at": at}}


def clear(name: str, tool: str | None = None) -> None:
    """It worked, so the plan allows it. No tool: everything (Test passed)."""
    with session_scope() as session:
        instance = instances.find_instance(session, name)
        config = dict((instance.config if instance else None) or {})
        kept = cast(dict[str, Any], config.get(KEY) or {})
        if instance is None or not kept:
            return
        left = [t for t in cast(list[str], kept.get("tools") or []) if tool and t != tool]
        if left:
            config[KEY] = {**kept, "tools": left}
        else:
            config.pop(KEY)
        instance.config = config


def blocked(config: dict[str, Any], tool: str, now: datetime | None = None) -> str | None:
    """The message, if this was just refused for its plan: no point asking again yet."""
    kept = cast(dict[str, Any], config.get(KEY) or {})
    if tool not in cast(list[str], kept.get("tools") or []):
        return None
    at = datetime.fromisoformat(str(kept.get("at")))
    return str(kept.get("message")) if (now or datetime.now(UTC)) - at < REMEMBERED else None


def note_task(session_id: str | None, message: str) -> None:
    try:
        ident = uuid.UUID(str(session_id))
    except ValueError:
        return  # not a worker: nothing to tell
    with session_scope() as session:
        row = session.get(WorkerSession, ident)
        task = session.get(Task, row.task_id) if row is not None else None
        if task is not None:
            task.plan_limit = message
