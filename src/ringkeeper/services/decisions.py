"""Held prod writes and their lifecycle.

pending -> approved -> executed | failed     (approve, then run the call)
pending -> denied                            (a human said no)
pending -> expired                           (not decided within the TTL)

`approved` is claimed under a row lock before the call runs, so a decision
can never execute twice, even if two people approve at once or the process
dies mid-call (it then stays `approved`, visibly unfinished).
"""

import getpass
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import String, cast, select, update
from sqlalchemy.orm import Session

from ringkeeper import destructive
from ringkeeper.calls import Caller, ToolCall, args_hash
from ringkeeper.config import get_settings
from ringkeeper.errors import NotFoundError, RuleViolationError
from ringkeeper.models import Decision, DecisionKind, DecisionStatus
from ringkeeper.services.audit import safe_text
from ringkeeper.storage import events

MIN_ID_PREFIX = 8
# New decisions of every kind are announced here (Telegram listens).
DECISIONS_CHANNEL = "ringkeeper_decisions"


def notify_new(session: Session, decision: Decision) -> None:
    """Delivered when the transaction commits."""
    events.notify(session, DECISIONS_CHANNEL, str(decision.id))


def current_user() -> str:
    try:
        return getpass.getuser()
    except OSError:
        return "unknown"


def hold(
    session: Session,
    call: ToolCall,
    instance: str,
    *,
    precondition: dict[str, Any] | None = None,
) -> Decision:
    caller = call.caller
    if caller.project is None or caller.session_id is None or caller.token_id is None:
        raise RuleViolationError("only a verified caller can create a decision")
    now = datetime.now(UTC)
    decision = Decision(
        status=DecisionStatus.PENDING,
        project=caller.project,
        session_id=caller.session_id,
        token_id=caller.token_id,
        instance=instance,
        service=call.service,
        tool=call.tool,
        args=call.args,
        args_hash=args_hash(call.args),
        kind=DecisionKind.APPROVE,
        precondition=precondition,
        # Deleting or dropping data needs the connection's name typed to approve.
        payload={"destructive": why}
        if (why := destructive.reason(call.service, call.tool, call.args))
        else None,
        created_at=now,
        expires_at=now + timedelta(seconds=get_settings().decision_ttl_seconds),
    )
    session.add(decision)
    session.flush()
    # Notification channels (Telegram) pick new decisions up from here.
    notify_new(session, decision)
    return decision


def get_decision(session: Session, ident: str, *, lock: bool = False) -> Decision:
    """By full id, or by a unique prefix of at least 8 characters."""
    ident = ident.strip().lower()
    query = select(Decision)
    try:
        query = query.where(Decision.id == uuid.UUID(ident))
    except ValueError:
        if len(ident) < MIN_ID_PREFIX:
            raise NotFoundError(
                f"decision '{ident}' not found; use the full id or 8+ characters"
            ) from None
        query = query.where(cast(Decision.id, String).startswith(ident))
    if lock:
        query = query.with_for_update()
    matches = session.scalars(query.limit(2)).all()
    if not matches:
        raise NotFoundError(f"decision '{ident}' not found")
    if len(matches) > 1:
        raise NotFoundError(f"decision prefix '{ident}' is ambiguous; use more characters")
    return matches[0]


def list_decisions(
    session: Session,
    *,
    status: DecisionStatus | None = None,
    project: str | None = None,
    limit: int = 50,
) -> Sequence[Decision]:
    query = select(Decision).order_by(Decision.created_at.desc()).limit(limit)
    if status is not None:
        query = query.where(Decision.status == status)
    if project is not None:
        query = query.where(Decision.project == project)
    return session.scalars(query).all()


def expire_stale(session: Session) -> int:
    """Mark every pending decision past its expiry as expired. Returns how many."""
    now = datetime.now(UTC)
    expired = session.scalars(
        update(Decision)
        .where(Decision.status == DecisionStatus.PENDING, Decision.expires_at <= now)
        .values(
            status=DecisionStatus.EXPIRED,
            decided_at=now,
            decided_by="ringkeeper",
            reason="not decided before it expired",
        )
        .returning(Decision.id)
        .execution_options(synchronize_session=False)
    ).all()
    session.expire_all()
    return len(expired)


def _require_pending(decision: Decision) -> None:
    if decision.status == DecisionStatus.EXPIRED:
        raise RuleViolationError(
            f"decision {decision.id} expired at {decision.expires_at.isoformat()} and can no "
            "longer be approved; ask the agent to retry the call"
        )
    if decision.status != DecisionStatus.PENDING:
        raise RuleViolationError(f"decision {decision.id} is already {decision.status}")
    if decision.expires_at <= datetime.now(UTC):
        raise RuleViolationError(
            f"decision {decision.id} expired at {decision.expires_at.isoformat()} and can no "
            "longer be approved; ask the agent to retry the call"
        )


def held_call(decision: Decision) -> ToolCall:
    """The exact call a held-write (approve) decision would run."""
    if (
        decision.kind is not DecisionKind.APPROVE
        or decision.service is None
        or decision.tool is None
        or decision.args is None
    ):
        raise RuleViolationError(
            f"decision {decision.id} is a {decision.kind} decision, not a held write"
        )
    caller = Caller(decision.project, decision.session_id, decision.token_id)
    return ToolCall(caller, decision.service, decision.tool, decision.instance, decision.args)


def confirmation_needed(decision: Decision) -> str | None:
    """Why approving this held write needs the connection's name typed, or None.

    Worked out from the held arguments every time, with today's rules: never
    from a flag stored with the decision, which a database write could change.
    """
    if decision.kind is not DecisionKind.APPROVE:
        return None
    return destructive.reason(decision.service, decision.tool, decision.args)


def check_confirmation(decision: Decision, confirm: str | None, via: str) -> None:
    """A write that needs confirmation: never from Telegram, and only with the name typed."""
    why = confirmation_needed(decision)
    if why is None:
        return
    if via == "telegram":
        raise RuleViolationError(
            f"This write {why}. Approve it in the Ringkeeper app, where you can see all of it."
        )
    if (confirm or "").strip() != decision.instance:
        raise RuleViolationError(
            f"This write {why}. To approve it, type the connection's name "
            f"({decision.instance}) to confirm."
        )


def claim_for_approval(
    session: Session, ident: str, by: str | None = None, *, confirm: str | None = None
) -> Decision:
    """Claim a held write to run it. Re-checks, at run time, that its arguments are
    the ones that were held, and that a write needing confirmation got it: either
    `confirm` now (the CLI) or with the recorded answer (the app, the API)."""
    decision = get_decision(session, ident, lock=True)
    held_call(decision)  # only held writes are approved here; other kinds are answered
    _require_pending(decision)
    if args_hash(decision.args or {}) != decision.args_hash:
        raise RuleViolationError(
            f"decision {decision.id}'s arguments changed after it was held; it can't run"
        )
    answer = decision.answer or {}
    if answer:
        check_confirmation(decision, answer.get("confirm"), decision.answered_via or "")
    else:
        check_confirmation(decision, confirm, "cli")
    decision.status = DecisionStatus.APPROVED
    decision.decided_at = datetime.now(UTC)
    decision.decided_by = by or current_user()
    session.flush()
    return decision


def deny(session: Session, ident: str, *, by: str | None = None, reason: str | None) -> Decision:
    decision = get_decision(session, ident, lock=True)
    _require_pending(decision)
    decision.status = DecisionStatus.DENIED
    decision.decided_at = datetime.now(UTC)
    decision.decided_by = by or current_user()
    decision.reason = reason
    session.flush()
    return decision


def finish(
    session: Session, decision: Decision, *, result: object = None, error: str | None = None
) -> Decision:
    if decision.status != DecisionStatus.APPROVED:
        raise RuleViolationError(f"decision {decision.id} is {decision.status}, not approved")
    decision.status = DecisionStatus.FAILED if error is not None else DecisionStatus.EXECUTED
    decision.executed_at = datetime.now(UTC)
    decision.result = None if result is None else safe_text(result)
    decision.error = None if error is None else safe_text(error)
    session.flush()
    return decision
