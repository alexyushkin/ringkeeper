"""Keeping what a service's plan doesn't allow: on the connection, so its card can say so and the
same call isn't sent again, on the task, so a stopped task says why, and on the session, so that
call doesn't count as a failed attempt while everything else does. See ringkeeper.plan_limits."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from ringkeeper.db import session_scope
from ringkeeper.models import SessionRole, Task, WorkerSession
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


def note_task(session_id: str | None, refused: str, message: str) -> None:
    """`refused`: the connection and tool, so that a retry of it is known for one."""
    try:
        ident = uuid.UUID(str(session_id))
    except ValueError:
        return  # not a worker: nothing to tell
    with session_scope() as session:
        row = session.get(WorkerSession, ident, with_for_update=True)
        if row is not None:
            row.task.plan_limit = message
            row.plan_refused = {**(row.plan_refused or {}), refused: message}


def _rounds(task: Task, row: WorkerSession) -> list[tuple[dict[str, str], set[str]]]:
    """What each worker round up to `row` had refused and failing, oldest first. An architect
    session starts a round, which runs over several sessions if it stopped for an approval."""
    rounds: list[tuple[dict[str, str], set[str]]] = []
    new_round = True
    for turn in sorted(task.sessions, key=lambda s: s.ordinal):
        if turn.ordinal > row.ordinal:
            break
        if turn.role is SessionRole.ARCHITECT:
            new_round = True
            continue
        if new_round:
            rounds.append(({}, set()))
            new_round = False
        refused = rounds[-1][0]
        refused.update(turn.plan_refused or {})
        if turn.checks is not None:
            results = cast(list[dict[str, Any]], turn.checks.get("results") or [])
            failing = {str(r.get("command")) for r in results if not r.get("passed")}
            rounds[-1] = (refused, failing)
    return rounds


def refused_now(task: Task, row: WorkerSession) -> str | None:
    """What a plan refused in the worker round `row` ended, if anything."""
    return " ".join(dict.fromkeys(_rounds(task, row)[-1][0].values())) or None


def explains(task: Task, row: WorkerSession) -> bool:
    """Whether a plan refusal accounts for all of the checks failing in this round.

    Checks are commands run after a round, so what ties one to a refusal is when it fails. It's
    the refusal's if it first fails in the round a call is first refused. It isn't if it also
    fails in a round with no refusal, or first fails in a round that only retried the call.
    """
    seen: set[str] = set()
    theirs: set[str] = set()
    others: set[str] = set()
    rounds = _rounds(task, row)
    for refused, failed in rounds:
        if set(refused) - seen:
            theirs |= failed
        elif not refused:
            others |= failed
        seen |= set(refused)
    refused, failed = rounds[-1]
    return bool(refused) and failed <= theirs - others
