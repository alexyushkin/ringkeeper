"""Tasks from the command line. The CLI only writes to the database; the session
manager in the api service starts, watches and stops the workers."""

import time
from collections.abc import Sequence
from datetime import datetime
from typing import Annotated, Any, cast

import typer

from ringkeeper.cli.common import echo_table, handle_errors, join
from ringkeeper.config import get_settings
from ringkeeper.db import session_scope
from ringkeeper.models import TERMINAL_STATUSES, SessionEvent, Task, TaskStatus
from ringkeeper.services import tasks
from ringkeeper.tokens import parse_ttl

app = typer.Typer(help="Run Claude workers on a project's workspace.", no_args_is_help=True)

POLL_SECONDS = 0.5
QUEUED_HINT_SECONDS = 15
TEXT_WIDTH = 300


def _short(text: str, width: int = TEXT_WIDTH) -> str:
    text = " ".join(text.split())
    return text if len(text) <= width else text[: width - 3] + "..."


def _when(value: datetime | None) -> str:
    return value.astimezone().strftime("%Y-%m-%d %H:%M:%S") if value else "-"


def _blocks(payload: dict[str, Any]) -> list[dict[str, Any]]:
    message = payload.get("message")
    content = cast(dict[str, Any], message).get("content") if isinstance(message, dict) else None
    return cast(list[dict[str, Any]], content) if isinstance(content, list) else []


def render_event(event_type: str, payload: dict[str, Any]) -> str | None:  # noqa: PLR0911
    """One readable line per interesting event, or None to skip it."""
    if event_type == "sdk.assistant":
        parts: list[str] = []
        for block in _blocks(payload):
            kind = block.get("_kind")
            if kind == "TextBlock" and str(block.get("text", "")).strip():
                parts.append(f"assistant: {_short(str(block['text']))}")
            elif kind == "ToolUseBlock":
                parts.append(
                    f"tool: {block.get('name')} {_short(str(block.get('input', '')), 160)}"
                )
        return "\n".join(parts) or None
    if event_type == "sdk.user":
        results = [b for b in _blocks(payload) if b.get("_kind") == "ToolResultBlock"]
        lines = [
            f"{'tool error' if b.get('is_error') else 'tool result'}: "
            f"{_short(str(b.get('content', '')), 160)}"
            for b in results
        ]
        return "\n".join(lines) or None
    if event_type == "sdk.result":
        message = cast(dict[str, Any], payload.get("message") or {})
        cost = message.get("total_cost_usd")
        cost_text = f", ~${cost:.4f} (SDK estimate)" if isinstance(cost, int | float) else ""
        return f"result: {message.get('subtype')}, {message.get('num_turns')} turns{cost_text}"
    if event_type == "ringkeeper.workspace":
        where = payload.get("branch") or "workspace (no git repo)"
        restored = f" ({payload['restored']})" if payload.get("restored") else ""
        changed = join(cast(list[str], payload.get("changed_files") or []))
        return f"changes on {where}: {changed}{restored}"
    if event_type == "ringkeeper.error":
        return f"error: {_short(str(payload.get('message', '')))}"
    if event_type == "worker.raw":
        return f"worker: {_short(str(payload.get('line', '')))}"
    return None


def _session_ids(task: Task) -> list[Any]:
    return [s.id for s in task.sessions]


def _summary(task: Task) -> str:
    latest = tasks.latest_session(task)
    turns = sum(s.turns or 0 for s in task.sessions)
    cost = sum(s.cost_usd or 0.0 for s in task.sessions)
    parts = [f"task {task.id}: {task.status}", f"{turns} turns"]
    if any(s.cost_usd is not None for s in task.sessions):
        parts.append(f"~${cost:.4f} (SDK estimate)")
    if task.error:
        parts.append(task.error)
    if latest.status is TaskStatus.QUEUED:
        parts.append("queued")
    return ", ".join(parts)


def follow(task_id: str, *, from_session: int = 1) -> int:
    """Print the task's events until it finishes. Ctrl-C detaches; the task keeps running.

    `from_session` skips earlier sessions, so a resume shows only the new run.
    """
    last_id = 0
    queued_since = time.monotonic()
    hinted = False
    try:
        while True:
            with session_scope() as session:
                task = tasks.get_task(session, task_id)
                ids = [s.id for s in task.sessions if s.ordinal >= from_session]
                new: Sequence[SessionEvent] = tasks.events(session, ids, after_id=last_id)
                rendered = [(e.id, render_event(e.type, e.payload)) for e in new]
                status = task.status
                summary = _summary(task)
            for event_id, text in rendered:
                last_id = event_id
                if text:
                    typer.echo(text)
            if status in TERMINAL_STATUSES and not new:
                typer.echo(summary)
                return 0 if status is TaskStatus.SUCCEEDED else 1
            waited = time.monotonic() - queued_since
            if status is TaskStatus.QUEUED and not hinted and waited > QUEUED_HINT_SECONDS:
                typer.echo(
                    "still queued: other tasks may be using every worker slot, or the "
                    "session manager (the api service) isn't running",
                    err=True,
                )
                hinted = True
            time.sleep(POLL_SECONDS)
    except KeyboardInterrupt:
        typer.echo(f"\ndetached; the task keeps running (`ringkeeper task show {task_id}`)")
        return 0


@app.command("run")
def run(
    prompt: Annotated[str, typer.Argument(help="What the worker should do.")],
    project: Annotated[str, typer.Option(help="Project whose workspace and instances it uses.")],
    max_turns: Annotated[int | None, typer.Option(min=1, help="Default 30.")] = None,
    max_runtime: Annotated[str | None, typer.Option(help="e.g. 30m, 2h. Default 30m.")] = None,
    budget: Annotated[
        float | None, typer.Option(help="USD cap (api_key mode only); the SDK's estimate.")
    ] = None,
    detach: Annotated[bool, typer.Option("--detach", help="Queue it and return.")] = False,
) -> None:
    """Queue a task for a project and follow it until it finishes."""
    with handle_errors():
        runtime_s = int(parse_ttl(max_runtime).total_seconds()) if max_runtime else None
        with session_scope() as session:
            task = tasks.create_task(
                session,
                get_settings(),
                project=project,
                prompt=prompt,
                max_turns=max_turns,
                max_runtime_s=runtime_s,
                budget_usd=budget,
            )
            task_id = str(task.id)
    typer.echo(f"queued task {task_id}")
    if not detach:
        raise typer.Exit(code=follow(task_id))


@app.command("resume")
def resume(
    task_id: Annotated[str, typer.Argument(help="Full id or 8+ char prefix.")],
    prompt: Annotated[str, typer.Argument(help="The follow-up.")],
    detach: Annotated[bool, typer.Option("--detach")] = False,
) -> None:
    """Continue a finished task in the same Claude session, with a follow-up prompt."""
    with handle_errors(), session_scope() as session:
        worker_session = tasks.resume_task(session, get_settings(), task_id, prompt)
        full_id = str(worker_session.task_id)
        ordinal = worker_session.ordinal
    typer.echo(f"queued session {ordinal} of task {full_id}")
    if not detach:
        raise typer.Exit(code=follow(full_id, from_session=ordinal))


@app.command("list")
def list_(
    status: Annotated[TaskStatus | None, typer.Option()] = None,
    project: Annotated[str | None, typer.Option()] = None,
    limit: Annotated[int, typer.Option(min=1, max=1000)] = 30,
) -> None:
    """List tasks, newest first."""
    with handle_errors(), session_scope() as session:
        rows = [
            [
                str(t.id)[:8],
                t.status.value,
                t.project,
                str(sum(s.turns or 0 for s in t.sessions)),
                str(len(t.sessions)),
                _when(t.created_at),
                _short(t.prompt, 50),
            ]
            for t in tasks.list_tasks(session, status=status, project=project, limit=limit)
        ]
    echo_table(
        ["ID", "STATUS", "PROJECT", "TURNS", "SESSIONS", "CREATED", "PROMPT"],
        rows,
        empty="no tasks",
    )


@app.command("show")
def show(
    task_id: Annotated[str, typer.Argument(help="Full id or 8+ char prefix.")],
    messages: Annotated[int, typer.Option(min=0, help="How many recent messages to show.")] = 12,
) -> None:
    """Summary, sessions, changed files, cost and the last messages of a task."""
    with handle_errors(), session_scope() as session:
        task = tasks.get_task(session, task_id)
        turns = sum(s.turns or 0 for s in task.sessions)
        costs = [s.cost_usd for s in task.sessions if s.cost_usd is not None]
        details = [
            ("id", str(task.id)),
            ("status", task.status.value),
            ("project", task.project),
            ("auth mode", task.auth_mode.value),
            ("turns", str(turns)),
            ("cost", f"~${sum(costs):.4f} (SDK estimate)" if costs else "-"),
            ("limits", f"{task.max_turns} turns, {task.max_runtime_s}s"
             + (f", ${task.budget_usd}" if task.budget_usd else "")),
            ("branch", f"{task.git_branch} @ {(task.git_commit or '')[:12]}"
             if task.git_branch else "-"),
            ("changed", join(task.changed_files)),
            ("error", task.error or "-"),
            ("created", _when(task.created_at)),
            ("finished", _when(task.finished_at)),
            ("prompt", _short(task.prompt)),
        ]  # fmt: skip
        session_rows = [
            [
                str(s.ordinal), s.status.value, str(s.turns if s.turns is not None else "-"),
                f"{s.cost_usd:.4f}" if s.cost_usd is not None else "-",
                _when(s.started_at), _when(s.finished_at), _short(s.prompt, 40),
            ]
            for s in sorted(task.sessions, key=lambda s: s.ordinal)
        ]  # fmt: skip
        recent = (
            [
                text
                for e in tasks.events(session, _session_ids(task), limit=100_000)
                if (text := render_event(e.type, e.payload))
            ][-messages:]
            if messages
            else []
        )
    for key, value in details:
        typer.echo(f"{key + ':':<11}{value}")
    typer.echo("")
    echo_table(
        ["SESSION", "STATUS", "TURNS", "COST", "STARTED", "FINISHED", "PROMPT"],
        session_rows,
        empty="",
    )
    if recent:
        typer.echo("\nlast messages:")
        for text in recent:
            typer.echo("  " + text.replace("\n", "\n  "))


@app.command("stop")
def stop(task_id: Annotated[str, typer.Argument(help="Full id or 8+ char prefix.")]) -> None:
    """Stop a task: its token is revoked now, and its worker is killed."""
    with handle_errors(), session_scope() as session:
        task = tasks.request_stop(session, task_id)
        full_id, status = str(task.id), task.status
    note = "" if status is TaskStatus.STOPPED else " (the worker is being killed)"
    typer.echo(f"stopping task {full_id}{note}")


def stop_all() -> None:
    """Stop every queued and running task, kill their workers and revoke their tokens."""
    with handle_errors(), session_scope() as session:
        stopped = [str(t.id) for t in tasks.stop_all(session)]
    if not stopped:
        typer.echo("nothing was running")
        return
    typer.echo(f"stopping {len(stopped)} task(s): {', '.join(s[:8] for s in stopped)}")
