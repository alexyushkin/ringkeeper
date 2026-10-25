import json
from datetime import datetime
from typing import Annotated

import anyio
import typer

from ringkeeper.cli.common import echo_table, fail, handle_errors
from ringkeeper.db import session_scope
from ringkeeper.models import Decision, DecisionStatus
from ringkeeper.services import calls, decisions, inbox
from ringkeeper.vault import get_vault

app = typer.Typer(help="Review held prod writes: list, show, approve, deny.", no_args_is_help=True)


def _when(value: datetime | None) -> str:
    return value.astimezone().strftime("%Y-%m-%d %H:%M:%S") if value else "-"


@app.command("list")
def list_(
    status: Annotated[
        str, typer.Option(help="pending, approved, denied, executed, failed, expired or all.")
    ] = "pending",
    project: Annotated[str | None, typer.Option()] = None,
    limit: Annotated[int, typer.Option(min=1, max=1000)] = 50,
) -> None:
    """List decisions (pending by default). Expired ones are marked first."""
    with handle_errors():
        wanted = None if status == "all" else DecisionStatus(status)
        with session_scope() as session:
            decisions.expire_stale(session)
            rows = [
                [
                    str(d.id)[:8],
                    d.status.value,
                    d.project or "-",
                    d.instance or "-",
                    d.tool or "-",
                    _when(d.created_at),
                    _when(d.expires_at),
                ]
                for d in decisions.list_decisions(
                    session, status=wanted, project=project, limit=limit
                )
            ]
    echo_table(
        ["ID", "STATUS", "PROJECT", "INSTANCE", "TOOL", "CREATED", "EXPIRES"],
        rows,
        empty=f"no {status} decisions" if status != "all" else "no decisions",
    )


def _details(decision: Decision) -> list[tuple[str, str]]:
    return [
        ("id", str(decision.id)),
        ("status", decision.status.value),
        ("kind", decision.kind.value),
        ("project", decision.project or "-"),
        ("session", decision.session_id or "-"),
        ("instance", f"{decision.instance} ({decision.service})"),
        ("tool", decision.tool or "-"),
        ("args_hash", decision.args_hash or "-"),
        ("precondition", json.dumps(decision.precondition) if decision.precondition else "-"),
        ("created", _when(decision.created_at)),
        ("expires", _when(decision.expires_at)),
        ("decided", f"{_when(decision.decided_at)} by {decision.decided_by or '-'}"),
        ("reason", decision.reason or "-"),
        ("executed", _when(decision.executed_at)),
        ("result", decision.result or "-"),
        ("error", decision.error or "-"),
    ]


@app.command("show")
def show(decision_id: Annotated[str, typer.Argument(help="Full id or 8+ char prefix.")]) -> None:
    """Show one decision, including the exact arguments it would run with."""
    with handle_errors(), session_scope() as session:
        decisions.expire_stale(session)
        decision = decisions.get_decision(session, decision_id)
        details = _details(decision)
        args = json.dumps(decision.args, indent=2, ensure_ascii=False)
    for key, value in details:
        typer.echo(f"{key + ':':<14}{value}")
    typer.echo("args:")
    typer.echo("  " + args.replace("\n", "\n  "))


@app.command("approve")
def approve(
    decision_id: Annotated[str, typer.Argument(help="Full id or 8+ char prefix.")],
    by: Annotated[str | None, typer.Option(help="Approver name (default: OS user).")] = None,
    confirm: Annotated[
        str | None,
        typer.Option(
            "--confirm",
            help="The connection's name, to approve a held write that deletes or drops data.",
        ),
    ] = None,
) -> None:
    """Approve a pending decision: re-check it, run the stored call, record the result."""
    with handle_errors():
        with session_scope() as session:
            held = decisions.get_decision(session, decision_id)
            inbox.check_destructive_confirmation(held, confirm, inbox.Via.CLI)
        vault = get_vault()

        async def run() -> Decision:
            return await calls.approve(decision_id, by=by, vault=vault)

        decision = anyio.run(run)
    if decision.status is DecisionStatus.EXECUTED:
        typer.echo(f"executed decision {decision.id}")
        typer.echo(f"result: {decision.result}")
        return
    raise fail(f"decision {decision.id} {decision.status.value}: {decision.error}")


@app.command("deny")
def deny(
    decision_id: Annotated[str, typer.Argument(help="Full id or 8+ char prefix.")],
    reason: Annotated[str | None, typer.Option(help="Recorded with the decision.")] = None,
    by: Annotated[str | None, typer.Option(help="Name to record (default: OS user).")] = None,
) -> None:
    """Deny a pending decision. Nothing runs."""
    with handle_errors():
        decision = calls.deny(decision_id, by=by, reason=reason)
    typer.echo(f"denied decision {decision.id}")
