import json
import time
import uuid
from datetime import datetime
from typing import Annotated

import anyio
import typer

from ringkeeper.cli.common import echo_table, fail, handle_errors
from ringkeeper.config import get_settings
from ringkeeper.db import session_scope
from ringkeeper.models import Decision, DecisionKind, DecisionStatus, MessageRole
from ringkeeper.services import calls, inbox, threads
from ringkeeper.services.decisions import current_user, get_decision
from ringkeeper.vault import get_vault

app = typer.Typer(
    help="Everything waiting for you: routes, held writes, plans and questions.",
    no_args_is_help=True,
)

POLL_SECONDS = 0.5


def _when(value: datetime | None) -> str:
    return value.astimezone().strftime("%Y-%m-%d %H:%M") if value else "-"


@app.command("list")
def list_(
    kind: Annotated[
        DecisionKind | None, typer.Option(help="route, approve, plan or question.")
    ] = None,
    status: Annotated[str, typer.Option(help="A decision status, or all.")] = "pending",
    limit: Annotated[int, typer.Option(min=1, max=1000)] = 50,
) -> None:
    """List decisions (pending by default), oldest first for pending ones."""
    with handle_errors(), session_scope() as session:
        wanted = None if status == "all" else DecisionStatus(status)
        found = inbox.list_decisions(session, status=wanted, kind=kind, limit=limit)
        if wanted is DecisionStatus.PENDING:
            found = sorted(found, key=lambda d: d.created_at)
        rows = [
            [
                str(d.id)[:8],
                d.kind.value,
                d.status.value,
                d.project or "-",
                _when(d.created_at),
                inbox.summary(d, 70),
            ]
            for d in found
        ]
    echo_table(
        ["ID", "KIND", "STATUS", "PROJECT", "CREATED", "SUMMARY"],
        rows,
        empty="inbox is empty" if status == "pending" else f"no {status} decisions",
    )


def _show(decision: Decision) -> list[str]:
    lines = [
        f"id:        {decision.id}",
        f"kind:      {decision.kind}",
        f"status:    {decision.status}",
        f"project:   {decision.project or '-'}",
        f"task:      {decision.task_id or '-'}",
        f"created:   {_when(decision.created_at)}",
        f"expires:   {_when(decision.expires_at)}",
    ]
    if decision.decided_at:
        how = f" via {decision.answered_via}" if decision.answered_via else ""
        lines.append(f"decided:   {_when(decision.decided_at)} by {decision.decided_by}{how}")
    if decision.answer:
        lines.append(f"answer:    {json.dumps(decision.answer, ensure_ascii=False)}")
    if options := inbox.options(decision):
        lines.append(f"options:   {', '.join(options)}")
    else:
        lines.append("options:   free text")
    if decision.kind is DecisionKind.APPROVE:
        lines.append(f"call:      {decision.tool} on {decision.instance} ({decision.service})")
        args = json.dumps(decision.args, indent=2, ensure_ascii=False)
        lines.append("args:\n  " + args.replace("\n", "\n  "))
    payload = dict(decision.payload or {})
    for key in ("question", "title", "message", "reason"):
        if key in payload:
            lines.append(f"{key + ':':<11}{payload.pop(key)}")
    for key in ("plan", "worker_prompt"):
        if key in payload:
            lines.append(f"{key}:\n  " + str(payload.pop(key)).replace("\n", "\n  "))
    if payload:
        details = json.dumps(payload, indent=2, ensure_ascii=False)
        lines.append("details:\n  " + details.replace("\n", "\n  "))
    return lines


@app.command("show")
def show(decision_id: Annotated[str, typer.Argument(help="Full id or 8+ char prefix.")]) -> None:
    """Show one decision in full (plans, held arguments and all)."""
    with handle_errors(), session_scope() as session:
        lines = _show(get_decision(session, decision_id))
    for item in lines:
        typer.echo(item)


@app.command("answer")
def answer(
    decision_id: Annotated[str, typer.Argument(help="Full id or 8+ char prefix.")],
    reply: Annotated[
        str,
        typer.Argument(
            help="approve or deny (held writes, plans), a project (routes), or your answer "
            "(questions)."
        ),
    ],
    note: Annotated[
        str | None,
        typer.Option("--note", "-m", help="Feedback for a plan, or why you denied."),
    ] = None,
    by: Annotated[str | None, typer.Option(help="Name to record (default: OS user).")] = None,
    confirm: Annotated[
        str | None,
        typer.Option(
            "--confirm",
            help="The connection's name, to approve a held write that deletes or drops data.",
        ),
    ] = None,
) -> None:
    """Answer a decision. Approving a held write runs it from here."""
    who = by or current_user()
    with handle_errors():
        with session_scope() as session:
            decision = get_decision(session, decision_id)
            reply_obj = (
                inbox.Answer(text=reply)
                if decision.kind is DecisionKind.QUESTION
                else inbox.Answer(choice=reply, text=note, confirm=confirm)
            )
            answered = inbox.answer(
                session, get_settings(), decision_id, reply_obj, by=who, via=inbox.Via.CLI
            )
            full_id = str(answered.decision.id)
            created = str(answered.task.id) if answered.task else None
            run_now = answered.run_held_write
            status = answered.decision.status
        if run_now:
            vault = get_vault()

            async def run() -> Decision:
                return await calls.approve(full_id, by=who, vault=vault)

            executed = anyio.run(run)
            if executed.status is not DecisionStatus.EXECUTED:
                raise fail(f"decision {full_id} {executed.status.value}: {executed.error}")
            typer.echo(f"executed decision {full_id}")
            typer.echo(f"result: {executed.result}")
            return
    typer.echo(f"answered decision {full_id} ({status.value})")
    if created:
        typer.echo(f"queued task {created}")


def ask(
    message: Annotated[str, typer.Argument(help="What you want done, in plain words.")],
    thread: Annotated[
        str | None, typer.Option("--thread", help="Continue a thread (id or 8+ char prefix).")
    ] = None,
    wait: Annotated[
        bool, typer.Option("--wait/--no-wait", help="Wait for the router's reply.")
    ] = True,
    timeout: Annotated[float, typer.Option(help="Seconds to wait for the router.")] = 180.0,
) -> None:
    """Send Ringkeeper a message; the router turns it into tasks (or asks you)."""
    with handle_errors(), session_scope() as session:
        posted = threads.post_message(
            session, message, author=current_user(), source="cli", thread=thread
        )
        message_id, thread_id = posted.id, posted.thread_id
    typer.echo(f"thread {thread_id}")
    if not wait:
        return
    reply = _wait_for_reply(message_id, thread_id, timeout)
    if reply is None:
        raise fail(
            f"no reply within {timeout:.0f}s; the message stays queued. Is the api running "
            "with RINGKEEPER_ROUTER=1?"
        )
    typer.echo(reply)


def _wait_for_reply(message_id: uuid.UUID, thread_id: uuid.UUID, timeout: float) -> str | None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with session_scope() as session:
            posted = threads.get_message(session, message_id)
            if posted.routed_at is not None:
                later = [
                    m
                    for m in threads.thread_messages(session, thread_id)
                    if m.created_at >= posted.created_at and m.role is not MessageRole.USER
                ]
                return "\n".join(m.body for m in later) or "(routed)"
        time.sleep(POLL_SECONDS)
    return None
