"""Audit rows for changes made by the owner (web app, API), next to the tool calls.

They use outcome `changed`, the action as `tool` (e.g. `project.update`) and
the project or connection they touched. Details never carry secret values:
callers pass only what changed, and everything is scrubbed again here.
"""

from typing import Any

from sqlalchemy.orm import Session

from ringkeeper.models import AuditEvent, AuditOutcome
from ringkeeper.scrub import scrub_obj
from ringkeeper.services.audit import safe_text


def record(
    session: Session,
    action: str,
    reason: str,
    *,
    by: str,
    project: str | None = None,
    instance: str | None = None,
    details: dict[str, Any] | None = None,
) -> AuditEvent:
    event = AuditEvent(
        project=project,
        instance=instance,
        tool=action,
        outcome=AuditOutcome.CHANGED,
        reason=safe_text(f"{reason} (by {by})"),
        args=scrub_obj({"by": by, **(details or {})}),
    )
    session.add(event)
    session.flush()
    return event
