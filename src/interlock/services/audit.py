"""The audit log: every write attempt and every refusal, append-only."""

import json
import uuid
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from interlock.calls import ToolCall, args_hash
from interlock.models import AuditEvent, AuditOutcome
from interlock.scrub import scrub, scrub_obj

RESULT_LIMIT = 2000


def as_text(value: object) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, default=str, ensure_ascii=False)


def truncate(text: str, limit: int = RESULT_LIMIT) -> str:
    if len(text) <= limit:
        return text
    return f"{text[:limit]}… [{len(text) - limit} more chars]"


def safe_text(value: object, limit: int = RESULT_LIMIT) -> str:
    """Scrub before truncating, so a cut can't split a secret past the scrubber."""
    return truncate(scrub(as_text(value)), limit)


def record(
    session: Session,
    call: ToolCall,
    outcome: AuditOutcome,
    *,
    reason: str | None = None,
    result: object = None,
    decision_id: uuid.UUID | None = None,
) -> AuditEvent:
    event = AuditEvent(
        project=call.caller.project,
        session_id=call.caller.session_id,
        token_id=call.caller.token_id,
        instance=call.instance,
        service=call.service,
        tool=call.tool,
        outcome=outcome,
        reason=None if reason is None else safe_text(reason),
        args=scrub_obj(call.args),
        args_hash=args_hash(call.args),
        result=None if result is None else safe_text(result),
        decision_id=decision_id,
    )
    session.add(event)
    session.flush()
    return event


def list_events(
    session: Session,
    *,
    project: str | None = None,
    instance: str | None = None,
    outcome: AuditOutcome | None = None,
    since: datetime | None = None,
    limit: int = 50,
) -> Sequence[AuditEvent]:
    query = select(AuditEvent).order_by(AuditEvent.at.desc(), AuditEvent.id.desc()).limit(limit)
    if project is not None:
        query = query.where(AuditEvent.project == project)
    if instance is not None:
        query = query.where(AuditEvent.instance == instance)
    if outcome is not None:
        query = query.where(AuditEvent.outcome == outcome)
    if since is not None:
        query = query.where(AuditEvent.at >= since)
    return session.scalars(query).all()
