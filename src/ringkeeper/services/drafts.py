"""Drafts a task saved, linked from its result.

Saving a draft reaches nobody, so it runs without asking (unless the connection holds drafts
too) and is only logged. The task's result then says where each draft is, so the user can
check it and send it: a link when the service has one, otherwise which connection's drafts.
"""

import json
import re
from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.orm import Session

from ringkeeper.models import AuditEvent, AuditOutcome, Task

DRAFT_TOOL = "create_draft"
VENDOR_TOOL = "call_operation"
_FIELD = r'"{0}":\s*"((?:[^"\\]|\\.)*)"'


def _found(text: str, *keys: str) -> str | None:
    """A value from a result that may have been cut short (too long to keep whole)."""
    try:
        parsed: Any = json.loads(text)
    except ValueError:
        parsed = None
    for key in keys:
        if isinstance(parsed, dict):
            value = cast(dict[str, Any], parsed).get(key)
            if isinstance(value, str) and value:
                return value
        if match := re.search(_FIELD.format(re.escape(key)), text):
            return json.loads(f'"{match[1]}"')
    return None


def _is_draft(row: AuditEvent) -> bool:
    if row.tool == DRAFT_TOOL:
        return True
    operation = str((row.args or {}).get("operation") or "")
    return row.tool == VENDOR_TOOL and operation.endswith(f".{DRAFT_TOOL}")


def saved(session: Session, task: Task) -> list[str]:
    """One line per draft the task saved."""
    sessions = [str(s.id) for s in task.sessions]
    if not sessions:
        return []
    rows = session.scalars(
        select(AuditEvent)
        .where(
            AuditEvent.session_id.in_(sessions),
            AuditEvent.outcome == AuditOutcome.EXECUTED,
            AuditEvent.tool.in_((DRAFT_TOOL, VENDOR_TOOL)),
        )
        .order_by(AuditEvent.id)
    ).all()
    lines: list[str] = []
    for row in rows:
        if not _is_draft(row):
            continue
        text = row.result or ""
        args = row.args or {}
        body = cast(dict[str, Any], args.get("body") or {})
        subject = _found(text, "subject") or str(args.get("subject") or body.get("subject") or "")
        subject = subject or "no subject"
        link = _found(text, "link", "webLink")
        where = row.instance or "the mailbox"
        lines.append(
            f"- [{subject}]({link})" if link else f"- {subject} (in {where}'s drafts folder)"
        )
    return lines


def linked(session: Session, task: Task, summary: str) -> str:
    """The result, with the drafts it saved that it doesn't already link."""
    missing = [line for line in saved(session, task) if _link_of(line) not in summary]
    if not missing:
        return summary
    head = "Draft saved:" if len(missing) == 1 else "Drafts saved:"
    return f"{summary.rstrip()}\n\n{head}\n" + "\n".join(missing)


def _link_of(line: str) -> str:
    match = re.search(r"\]\(([^)]+)\)$", line)
    return match[1] if match else line
