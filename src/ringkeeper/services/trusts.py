"""Trust an action for an hour: one tool, on one connection, in one project, runs without
asking until the trust ends.

Never for a change that needs the connection's name typed (deletes, schema changes,
anything destructive decides it can't call ordinary), checked when the trust is given
and again on every call, with that call's own arguments. Never for undoing a change.
Every trust and every call made under one is logged.
"""

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ringkeeper import destructive
from ringkeeper.errors import NotFoundError, RuleViolationError
from ringkeeper.models import Decision, DecisionKind, Instance, InstanceType, Trust
from ringkeeper.services import admin_audit

HOURS = 1
REVERT_TOOL = "revert_change"


def grant(session: Session, decision: Decision, *, by: str, hours: int = HOURS) -> Trust:
    """Trust what this held call does, for its project and connection."""
    if decision.kind is not DecisionKind.APPROVE or not decision.instance or not decision.tool:
        raise RuleViolationError("Only a held action can be trusted.")
    if decision.tool == REVERT_TOOL:
        raise RuleViolationError("Undoing a change always asks.")
    config = _config(session, decision.instance)
    why = destructive.reason(decision.service, decision.tool, decision.args or {}, config)
    if why is not None:
        raise RuleViolationError(f"This change {why}, so it always asks.")
    now = datetime.now(UTC)
    session.execute(delete(Trust).where(Trust.expires_at <= now))
    trust = Trust(
        project=decision.project or "", instance=decision.instance, tool=decision.tool,
        expires_at=now + timedelta(hours=hours), created_by=by,
    )  # fmt: skip
    session.add(trust)
    session.flush()
    admin_audit.record(
        session, "trust.add",
        f"trusted {decision.tool} on {decision.instance} in {decision.project} for {hours}h",
        by=by, project=decision.project, instance=decision.instance,
    )  # fmt: skip
    return trust


def _config(session: Session, instance: str) -> dict[str, Any]:
    found = session.scalar(select(Instance).where(Instance.name == instance))
    return dict(found.config or {}) if found is not None else {}


def allows(
    session: Session,
    project: str,
    instance: str,
    service: InstanceType,
    tool: str,
    args: dict[str, Any],
) -> bool:
    """Whether this very call may run without asking."""
    if tool == REVERT_TOOL:
        return False
    found = session.scalar(
        select(Trust).where(
            Trust.project == project, Trust.instance == instance, Trust.tool == tool,
            Trust.expires_at > datetime.now(UTC),
        )
    )  # fmt: skip
    if found is None:
        return False
    return destructive.reason(service, tool, args, _config(session, instance)) is None


def active(session: Session) -> Sequence[Trust]:
    return session.scalars(
        select(Trust).where(Trust.expires_at > datetime.now(UTC)).order_by(Trust.expires_at)
    ).all()


def revoke(session: Session, ident: str, *, by: str) -> None:
    trust = session.get(Trust, uuid.UUID(ident))
    if trust is None:
        raise NotFoundError("That trust has already ended.")
    session.delete(trust)
    admin_audit.record(
        session, "trust.remove", f"stopped trusting {trust.tool} on {trust.instance}",
        by=by, project=trust.project, instance=trust.instance,
    )  # fmt: skip
