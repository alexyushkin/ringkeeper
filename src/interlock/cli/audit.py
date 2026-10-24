from datetime import UTC, datetime
from typing import Annotated

import typer

from interlock.cli.common import echo_table, handle_errors
from interlock.db import session_scope
from interlock.errors import InterlockError
from interlock.models import AuditOutcome
from interlock.services import audit
from interlock.tokens import parse_ttl

app = typer.Typer(help="Read the audit log (append-only).", no_args_is_help=True)

SUMMARY_WIDTH = 60


def _since(value: str | None) -> datetime | None:
    """'2h' / '30m' / '1d' ago, or an ISO timestamp."""
    if value is None:
        return None
    try:
        return datetime.now(UTC) - parse_ttl(value)
    except InterlockError:
        pass
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise InterlockError(f"invalid --since '{value}': use e.g. 2h, 1d or an ISO time") from None
    return parsed if parsed.tzinfo else parsed.astimezone()


def _short(text: str | None) -> str:
    if not text:
        return "-"
    text = " ".join(text.split())
    # ASCII on purpose: Windows consoles may not render an ellipsis.
    return text if len(text) <= SUMMARY_WIDTH else text[: SUMMARY_WIDTH - 3] + "..."


@app.command("list")
def list_(
    project: Annotated[str | None, typer.Option()] = None,
    instance: Annotated[str | None, typer.Option()] = None,
    outcome: Annotated[AuditOutcome | None, typer.Option()] = None,
    since: Annotated[str | None, typer.Option(help="e.g. 2h, 1d, or an ISO time.")] = None,
    limit: Annotated[int, typer.Option(min=1, max=1000)] = 50,
) -> None:
    """List audit events, newest first. Args and results are already redacted."""
    with handle_errors():
        start = _since(since)
        with session_scope() as session:
            rows = [
                [
                    e.at.astimezone().strftime("%Y-%m-%d %H:%M:%S"),
                    e.outcome.value,
                    e.project or "-",
                    e.instance or "-",
                    e.tool or "-",
                    str(e.decision_id)[:8] if e.decision_id else "-",
                    _short(e.reason if e.reason else e.result),
                ]
                for e in audit.list_events(
                    session,
                    project=project,
                    instance=instance,
                    outcome=outcome,
                    since=start,
                    limit=limit,
                )
            ]
    echo_table(
        ["AT", "OUTCOME", "PROJECT", "INSTANCE", "TOOL", "DECISION", "DETAIL"],
        rows,
        empty="no audit events",
    )
