"""Undo for data changes: what an approved or auto-run write changes is kept for 7 days.

Before a write runs (approved, or run without asking on a staging or development
connection), its tool's Undoer reads what it will change, and only that: the rows
an UPDATE or DELETE touches, the Airtable fields it sets, the n8n workflow it
replaces. That goes into the data folder, undo/<project>/<id>.json, with how
things were left after it ran.

Reverting is a held action like any other (services.calls.request_revert): Revert
on the task page or in the conversation asks for approval in Approvals, showing
what will be put back. Once approved, Ringkeeper checks nothing changed since the
revert was asked for, then puts the old state back.

A write that can't be taken back (an email sent, a text, a deploy, an INSERT) is
listed too, saying why; the approval card says so before it runs. Each project's
kept changes have a size cap: past it, a change runs without being kept and says
so. Kept changes are deleted after 7 days.
"""

import json
import os
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.orm import Session

from ringkeeper.adapters import ADAPTERS
from ringkeeper.adapters.base import JSON, Hold, Target, ToolError, ToolKind, ToolSpec, Undoer
from ringkeeper.adapters.undo_sql import CantUndoError
from ringkeeper.calls import Caller, ToolCall
from ringkeeper.config import get_settings
from ringkeeper.errors import NotFoundError, RuleViolationError
from ringkeeper.models import Decision, DecisionStatus, InstanceType, Task, WorkerSession
from ringkeeper.scrub import scrub
from ringkeeper.sql_text import sql_of

KEEP = timedelta(days=7)
PROJECT_CAP_BYTES = 50 * 1024 * 1024
REVERT_TOOL = "revert_change"

# Why a write can't be taken back, when its tool has no Undoer.
CANT: dict[InstanceType, str] = {
    InstanceType.GMAIL: "A sent email can't be unsent.",
    InstanceType.TWILIO: "A text or a call can't be taken back.",
    InstanceType.SMTP: "A sent email can't be unsent.",
    InstanceType.RESEND: "A sent email can't be unsent.",
    InstanceType.MAILBOX: "Changes to a mailbox can't be undone here.",
    InstanceType.HTTP: "Ringkeeper can't undo what an API call does.",
    InstanceType.S3: "Uploads and deletes can't be undone here.",
    InstanceType.VERCEL: (
        "A deploy can't be undone here; put the earlier deployment live in Vercel if you need to."
    ),
    InstanceType.GITHUB: "Changes on GitHub can't be undone here.",
    InstanceType.MCP: "Ringkeeper can't undo what another tool's server does.",
    InstanceType.GOOGLE: (
        "Ringkeeper can't undo changes in Google (sent mail, invites, shared or edited files)."
    ),
}
DEFAULT_CANT = "Ringkeeper can't undo this kind of change."


def why_not(service: InstanceType, spec: ToolSpec, args: JSON) -> str | None:
    """None if this write can be undone for 7 days after it runs; otherwise why not."""
    if spec.undo is not None:
        return spec.undo.why_not(args)
    return CANT.get(service, DEFAULT_CANT)


def note(service: InstanceType | None, tool: str | None, args: JSON | None) -> JSON | None:
    """For an approval card: whether the held call can be undone once it runs."""
    if service is None or tool is None or tool == REVERT_TOOL:
        return None
    try:
        spec = ADAPTERS[service].tool(tool)
    except (KeyError, ToolError):
        return None
    if spec.kind is ToolKind.READ:
        return None
    why = why_not(service, spec, args or {})
    return {"possible": why is None, "why": why, "days": KEEP.days}


async def check(service: InstanceType, spec: ToolSpec, target: Target, args: JSON) -> JSON | None:
    """For a held call, before its approval card is shown: can it really be undone?
    Reads what it would change, as the write itself will (a table's primary key and
    rows, the Airtable records, the n8n workflow), and throws that away: the card then
    never promises an undo the write can't have."""
    if spec.kind is ToolKind.READ:
        return None
    why = why_not(service, spec, args)
    if why is None and spec.undo is not None:
        try:
            await spec.undo.before(target, args)
        except CantUndoError as error:
            why = f"It can't be undone: {error}."
        except ToolError as error:
            why = (
                "Ringkeeper couldn't read what it would change, so it can't be undone "
                f"({scrub(str(error))})."
            )
    return {"possible": why is None, "why": why, "days": KEEP.days}


def note_for(decision: Decision) -> JSON | None:
    """The approval card's undo line: what was checked when it was held, else the rule."""
    checked = (decision.payload or {}).get("undo")
    if isinstance(checked, dict):
        return cast(JSON, checked)
    return note(decision.service, decision.tool, decision.args)


# --- What's kept ---


@dataclass
class Change:
    id: str
    project: str
    instance: str
    service: str
    tool: str
    what: str  # the write, in a few words
    at: str
    expires: str
    task_id: str | None
    session_id: str | None
    token_id: str | None
    kept: JSON | None = None  # what the write changed, as it was (None: not kept)
    after: JSON | None = None  # how it was left
    restores: str | None = None  # what reverting puts back
    why_not: str | None = None  # why it can't be undone (kept is None)
    reverted_at: str | None = None
    revert_decision: str | None = None
    extra: JSON = field(default_factory=dict[str, Any])

    @property
    def caller(self) -> Caller:
        return Caller(self.project, self.session_id, self.token_id)


def root() -> Path:
    return get_settings().data_dir / "undo"


def _save(change: Change) -> None:
    path = root() / change.project / f"{change.id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(asdict(change), default=str), encoding="utf-8")
    os.replace(temp, path)


def _load(path: Path) -> Change | None:
    try:
        return Change(**json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError, TypeError):
        return None


def _all() -> list[Change]:
    return [c for p in root().glob("*/*.json") if (c := _load(p)) is not None]


def find(change_id: str) -> Change:
    try:
        uuid.UUID(change_id)
    except ValueError:
        raise NotFoundError("That change isn't here. Refresh the page.") from None
    for path in root().glob(f"*/{change_id}.json"):
        found = _load(path)
        if found is not None:
            return found
    raise NotFoundError("That change isn't kept any more: changes are kept for 7 days.")


def mark(change: Change, **fields: Any) -> None:
    for name, value in fields.items():
        setattr(change, name, value)
    _save(change)


def _used(project: str) -> int:
    folder = root() / project
    return sum(p.stat().st_size for p in folder.glob("*.json")) if folder.exists() else 0


def expire(now: datetime | None = None) -> int:
    """Delete kept changes past their 7 days. Returns how many."""
    now = now or datetime.now(UTC)
    gone = 0
    for path in root().glob("*/*.json"):
        change = _load(path)
        if change is None or datetime.fromisoformat(change.expires) <= now:
            path.unlink(missing_ok=True)
            gone += 1
    return gone


def remove_project(project: str) -> None:
    """A project's kept changes, when the project goes (the demo)."""
    for path in (root() / project).glob("*.json"):
        path.unlink(missing_ok=True)


# --- Keeping a change, around the write ---


@dataclass
class Pending:
    change: Change
    undo: Undoer | None


def task_of(session: Session, session_id: str | None) -> str | None:
    if not session_id:
        return None
    try:
        row = session.get(WorkerSession, uuid.UUID(session_id))
    except ValueError:
        return None
    return str(row.task_id) if row is not None else None


def what(call: ToolCall) -> str:
    """The write in a few words, for the task page."""
    from ringkeeper.wording import action  # noqa: PLC0415 (wording imports models only)

    sql = sql_of(call.args)
    if sql:
        short = " ".join(sql.split())
        return short if len(short) <= 160 else short[:157] + "..."
    return action(call.service, call.tool, call.args)


async def prepare(task_id: str | None, call: ToolCall, spec: ToolSpec, target: Target) -> Pending:
    """Before the write runs: read what it will change (or note why it can't be undone)."""
    now = datetime.now(UTC)
    change = Change(
        id=str(uuid.uuid4()), project=call.caller.project or "", instance=target.name,
        service=call.service.value, tool=call.tool, what=what(call), at=now.isoformat(),
        expires=(now + KEEP).isoformat(), task_id=task_id,
        session_id=call.caller.session_id, token_id=call.caller.token_id,
    )  # fmt: skip
    reason = why_not(call.service, spec, call.args)
    if reason is not None or spec.undo is None:
        change.why_not = reason or DEFAULT_CANT
        return Pending(change, None)
    try:
        change.kept = await spec.undo.before(target, call.args)
    except CantUndoError as error:
        change.why_not = f"It can't be undone: {error}."
    except ToolError as error:
        change.why_not = (
            "Ringkeeper couldn't read what it would change, so it can't be undone "
            f"({scrub(str(error))})."
        )
    return Pending(change, spec.undo if change.kept is not None else None)


async def finish(pending: Pending, target: Target, call: ToolCall, result: JSON) -> None:
    """The write ran: keep the change (or that it can't be undone)."""
    change = pending.change
    if change.kept is not None and pending.undo is not None:
        try:
            change.after = await pending.undo.after(target, call.args, change.kept, result)
            change.restores = pending.undo.describe(change.kept)
        except ToolError as error:
            change.kept = None
            change.why_not = (
                "Ringkeeper couldn't read how it was left, so it can't be undone "
                f"({scrub(str(error))})."
            )
    size = len(json.dumps(asdict(change), default=str).encode())
    if change.kept is not None and _used(change.project) + size > PROJECT_CAP_BYTES:
        change.kept = change.after = change.restores = None
        cap = PROJECT_CAP_BYTES // (1024 * 1024)
        change.why_not = (
            f"Not kept for undo: this project's undo space ({cap} MB) is full. Space "
            "frees up as kept changes pass 7 days."
        )
    if change.project:
        _save(change)


# --- Listing and reverting ---


def _reverting(session: Session, change: Change) -> bool:
    """A revert waits for approval (or is running). A denied, expired or failed one
    doesn't count: it can be asked for again."""
    if change.revert_decision is None or change.reverted_at is not None:
        return False
    status = session.scalar(
        select(Decision.status).where(Decision.id == uuid.UUID(change.revert_decision))
    )
    return status in (DecisionStatus.PENDING, DecisionStatus.APPROVED)


def listed(session: Session, change: Change, now: datetime | None = None) -> JSON:
    now = now or datetime.now(UTC)
    current = datetime.fromisoformat(change.expires) > now
    reverting = _reverting(session, change)
    return {
        "id": change.id, "at": change.at, "connection": change.instance, "what": change.what,
        "expires": change.expires, "restores": change.restores, "why_not": change.why_not,
        "can_revert": change.kept is not None and change.reverted_at is None and current
        and not reverting,
        "reverting": reverting, "revert_decision": change.revert_decision,
        "reverted_at": change.reverted_at, "expired": not current,
    }  # fmt: skip


def for_tasks(session: Session, task_ids: list[str]) -> list[JSON]:
    """The changes these tasks made, oldest first."""
    wanted = set(task_ids)
    found = [c for c in _all() if c.task_id in wanted]
    return [listed(session, c) for c in sorted(found, key=lambda c: c.at)]


def for_project(session: Session, project: str) -> list[JSON]:
    found = [c for c in _all() if c.project == project]
    return [listed(session, c) for c in sorted(found, key=lambda c: c.at)]


def check_revertible(session: Session, change: Change) -> None:
    """Raises RuleViolationError when this change can't be reverted now."""
    if change.kept is None:
        raise RuleViolationError(change.why_not or DEFAULT_CANT)
    if change.reverted_at is not None:
        raise RuleViolationError("That change was already reverted.")
    if _reverting(session, change):
        raise RuleViolationError("Reverting it already waits for your approval in Approvals.")
    if datetime.fromisoformat(change.expires) <= datetime.now(UTC):
        raise RuleViolationError("That change is more than 7 days old, so it isn't kept any more.")
    if change.task_id:
        from ringkeeper.services.projects import BUSY  # noqa: PLC0415 (projects is heavier)

        status = session.scalar(select(Task.status).where(Task.id == uuid.UUID(change.task_id)))
        if status in BUSY:
            raise RuleViolationError(
                "Its task is still working. Revert the change once the task is done or stopped."
            )


def undoer(change: Change) -> Undoer:
    spec = ADAPTERS[InstanceType(change.service)].tool(change.tool)
    if spec.undo is None:
        raise ToolError("that change can't be undone")
    return spec.undo


async def _revert(target: Target, change: str) -> JSON:
    """Put a kept change back (a Revert, once it's approved)."""
    found = find(change)
    if found.kept is None or found.reverted_at is not None:
        raise ToolError("that change can't be reverted: it was already reverted, or isn't kept")
    result = await undoer(found).restore(target, found.kept)
    mark(found, reverted_at=datetime.now(UTC).isoformat())
    return {"reverted": found.restores, "result": result}


async def _revert_now(target: Target, change: str, **_ignored: object) -> JSON:
    """The revert's precondition: how the changed things are now. Approval is refused
    if they change between asking for the revert and approving it."""
    found = find(change)
    if found.kept is None:
        return {}
    return await undoer(found).current(target, found.kept)


REVERT_SPEC = ToolSpec(
    REVERT_TOOL,
    ToolKind.WRITE,
    "Put back what a change replaced or removed.",
    _revert,
    precondition=_revert_now,
    hold=Hold.ALWAYS,
)


def spec_for(service: InstanceType, tool: str) -> ToolSpec:
    """The tool a held call runs: a revert, or one of the adapter's own."""
    return REVERT_SPEC if tool == REVERT_TOOL else ADAPTERS[service].tool(tool)


def revert_call(change: Change) -> ToolCall:
    return ToolCall(
        change.caller, InstanceType(change.service), REVERT_TOOL, change.instance,
        {"change": change.id},
    )  # fmt: skip


def destroys(args: JSON) -> str | None:
    """Why approving this revert needs the connection's name typed: it deletes."""
    try:
        found = find(str(args.get("change") or ""))
    except NotFoundError:
        return "reverts a change that isn't kept any more"
    if (found.service, found.tool) == (InstanceType.AIRTABLE.value, "create_records"):
        return "deletes the records the change added"
    return None
