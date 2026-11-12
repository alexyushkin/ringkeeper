"""A client's month: what Ringkeeper did for them, what it cost, and a report to send them.

Everything is counted from the client's own projects, so one client's month never shows
another's work: the tasks started that month (from the app, a routine, Telegram or a pass),
the changes that ran through the gateway, what waited for approval and how it was decided,
the calls that were refused, and the AI cost recorded for those tasks.

The month is this computer's calendar month (the server's, for a server install). A client's
monthly cap stops new tasks for them once that month's AI cost reaches it; see `check_cap`.
"""

import csv
import io
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from ringkeeper.calls import Caller, ToolCall
from ringkeeper.errors import RuleViolationError
from ringkeeper.models import (
    TERMINAL_STATUSES,
    AuditEvent,
    AuditOutcome,
    AuthMode,
    Client,
    Decision,
    DecisionKind,
    DecisionStatus,
    InstanceType,
    Project,
    ProjectInstance,
    Routine,
    SessionRole,
    Task,
    TaskStatus,
    WorkerSession,
)
from ringkeeper.scrub import scrub
from ringkeeper.services import bindings, clients
from ringkeeper.services import tasks as tasks_service

if TYPE_CHECKING:
    from ringkeeper.config import Settings

TITLE_CHARS = 120
RESULT_CHARS = 300


class CapReachedError(RuleViolationError):
    """The client's AI cost this month has reached the cap set on their page."""


def local_now() -> datetime:
    return datetime.now().astimezone()


def parse(text: str | None, now: datetime | None = None) -> tuple[int, int]:
    """'2026-11' as (2026, 11); empty is this month. A month in the future is refused."""
    today = (now or local_now()).date()
    if not text:
        return today.year, today.month
    try:
        year, month = (int(part) for part in text.split("-", 1))
        date(year, month, 1)
    except ValueError:
        raise RuleViolationError("Pick a month, like 2026-11.") from None
    if (year, month) > (today.year, today.month):
        raise RuleViolationError("That month hasn't started yet.")
    return year, month


def bounds(year: int, month: int) -> tuple[datetime, datetime]:
    """The month on this computer's calendar, as UTC times: [start, end)."""
    zone = local_now().tzinfo
    start = datetime(year, month, 1, tzinfo=zone)
    end = datetime(year + month // 12, month % 12 + 1, 1, tzinfo=zone)
    return start.astimezone(UTC), end.astimezone(UTC)


def label(year: int, month: int) -> str:
    return date(year, month, 1).strftime("%B %Y")


def _projects(session: Session, client: str) -> dict[str, str]:
    """The client's projects: name -> title."""
    rows = session.execute(
        select(Project.name, Project.title).where(Project.owner == client).order_by(Project.name)
    ).tuples()
    return {name: title or name for name, title in rows}


def spend(session: Session, client: str, year: int, month: int) -> float:
    """The AI cost recorded for the client's tasks started that month."""
    start, end = bounds(year, month)
    total = session.scalar(
        select(func.coalesce(func.sum(WorkerSession.cost_usd), 0.0))
        .join(Task, Task.id == WorkerSession.task_id)
        .join(Project, Project.name == Task.project)
        .where(Project.owner == client, Task.created_at >= start, Task.created_at < end)
    )
    return float(total or 0.0)


def spend_by_client(session: Session, year: int, month: int) -> dict[str, float]:
    start, end = bounds(year, month)
    rows = session.execute(
        select(Project.owner, func.sum(WorkerSession.cost_usd))
        .join(Task, Task.project == Project.name)
        .join(WorkerSession, WorkerSession.task_id == Task.id)
        .where(Task.created_at >= start, Task.created_at < end)
        .group_by(Project.owner)
    ).tuples()
    return {owner: float(total or 0.0) for owner, total in rows}


def money(value: float) -> str:
    cents = Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return f"${cents:,.2f}"


@dataclass(frozen=True)
class Over:
    """A client whose AI cost this month has reached their cap."""

    client: Client
    used: float
    cap: float
    month: str

    def said(self) -> str:
        return (
            f"{self.client.name} is over its monthly cap: {money(self.used)} of "
            f"{money(self.cap)} in {self.month}."
        )


def over_cap(session: Session, project: str, now: datetime | None = None) -> Over | None:
    owner = session.scalar(select(Project.owner).where(Project.name == project))
    row = session.get(Client, owner) if owner is not None else None
    if row is None or row.monthly_cap_usd is None:
        return None
    when = (now or local_now()).astimezone()
    used = spend(session, row.slug, when.year, when.month)
    if used < row.monthly_cap_usd:
        return None
    return Over(row, used, row.monthly_cap_usd, label(when.year, when.month))


def check_cap(session: Session, project: str, now: datetime | None = None) -> None:
    """For routines, which don't ask: refuse once the client's cap is reached."""
    over = over_cap(session, project, now)
    if over is not None:
        raise CapReachedError(
            f"{over.said()} Routines for {over.client.name} wait until you raise the cap on the "
            "client's page, or until next month."
        )


RUN_ONCE, RAISE, DONT = "run once", "raise", "stop"


def ask_over_cap(
    session: Session,
    settings: "Settings",
    task: Task,
    *,
    follow_up: str | None = None,
    role: str | None = None,
) -> bool:
    """Over the client's cap, a new task (or a follow-up) waits for the user instead of
    running: run it anyway this once, raise the cap, or don't run it. Returns whether it asked."""
    from ringkeeper.services import inbox  # noqa: PLC0415 (inbox answers through this module)

    over = over_cap(session, task.project)
    if over is None:
        return False
    was = task.status
    task.status = TaskStatus.NEEDS_INPUT
    for row in task.sessions:
        if row.status is TaskStatus.QUEUED:
            row.status = TaskStatus.NEEDS_INPUT
    what = "this follow-up" if follow_up else "this task"
    payload: dict[str, Any] = {
        "question": f"{over.said()} Run {what} anyway, just this once, or raise the cap?",
        "client_cap": {
            "client": over.client.slug, "name": over.client.name,
            "used": round(over.used, 2), "cap": over.cap,
        },
        "options": [RUN_ONCE, RAISE, DONT],
        "was": was.value,
    }  # fmt: skip
    if follow_up is not None:
        payload |= {"follow_up": follow_up, "role": role}
    inbox.create(session, settings, DecisionKind.QUESTION, payload, project=task.project, task=task)
    return True


def answer_over_cap(
    session: Session,
    settings: "Settings",
    task: Task,
    payload: dict[str, Any],
    reply: str,
    by: str,
) -> None:
    """Run anyway this once ("run once"), raise the cap ("raise:150"), or don't ("stop").
    Running past the cap and raising it are both in the audit log."""
    from ringkeeper.services import admin_audit, tasks  # noqa: PLC0415

    choice, amount = _read(reply)
    asked = payload["client_cap"]
    client = clients.get(session, str(asked["client"]))
    follow_up = payload.get("follow_up")
    if choice == DONT:
        _dont(task, payload, client.name)
        return
    now = local_now()
    used = spend(session, client.slug, now.year, now.month)
    if choice == RAISE:
        try:
            new = round(float(amount.replace("$", "").replace(",", "")), 2)
        except ValueError:
            raise RuleViolationError("Type the new monthly cap in dollars, like 100.") from None
        if new <= used:
            raise RuleViolationError(
                f"Raise it above what {client.name} has used this month ({money(used)})."
            )
        old = client.monthly_cap_usd
        client.monthly_cap_usd = new
        admin_audit.record(
            session, "client.cap",
            f"raised {client.name}'s monthly cap to {money(new)}"
            + (f" (from {money(old)})" if old is not None else "") + " to run a task",
            by=by, project=task.project, details={"task": str(task.id)},
        )  # fmt: skip
    elif choice == RUN_ONCE:
        admin_audit.record(
            session, "client.cap.once",
            f"ran a task for {client.name} past its monthly cap, this once "
            f"({money(used)} of {money(client.monthly_cap_usd or 0)})",
            by=by, project=task.project, details={"task": str(task.id)},
        )  # fmt: skip
    else:
        raise RuleViolationError("Choose Run anyway this once, Raise the cap, or Don't run it.")
    if follow_up:
        task.status = TaskStatus(payload["was"])
        role = SessionRole(payload.get("role") or SessionRole.WORKER.value)
        sdk_session = tasks.last_sdk_session(task, role)
        if sdk_session is None:
            raise RuleViolationError("This task can't be continued. Start a new task instead.")
        tasks.add_session(session, settings, task, str(follow_up), role, sdk_session_id=sdk_session)
        return
    task.status = TaskStatus.QUEUED
    for row in task.sessions:
        if row.status is TaskStatus.NEEDS_INPUT:
            row.status = TaskStatus.QUEUED
    tasks.notify(session)


def _read(reply: str) -> tuple[str, str]:
    """The answer as the app sends it ("raise:150") or as typed in a chat ("raise 150",
    "run anyway this once", "don't run it")."""
    text = " ".join(reply.strip().lower().replace(":", " ").split())
    if text.startswith(RAISE):
        return RAISE, text.removeprefix(RAISE).replace("the cap", "").replace("to", "").strip()
    if text.startswith("run"):
        return RUN_ONCE, ""
    if text in (DONT, "no", "don't run it", "dont run it", "don't"):
        return DONT, ""
    return text, ""


def _dont(task: Task, payload: dict[str, Any], name: str) -> None:
    if payload.get("follow_up"):  # the task stays as it was; the follow-up isn't sent
        task.status = TaskStatus(payload["was"])
        return
    task.status = TaskStatus.STOPPED
    task.finished_at = datetime.now(UTC)
    task.error = f"not run: {name} is over its monthly cap"
    for row in task.sessions:
        if row.status is TaskStatus.NEEDS_INPUT:
            row.status = TaskStatus.STOPPED


@dataclass(frozen=True)
class Held:
    """A client over its cap whose routines are waiting (for Home)."""

    client: str
    name: str
    used: float
    cap: float
    routines: int


def held_routines(session: Session, now: datetime | None = None) -> list[Held]:
    """Clients over their cap with routines turned on: those routines skip their runs."""
    when = (now or local_now()).astimezone()
    capped = session.scalars(select(Client).where(Client.monthly_cap_usd.is_not(None)))
    found: list[Held] = []
    for client in capped:
        cap = client.monthly_cap_usd or 0.0
        used = spend(session, client.slug, when.year, when.month)
        if used < cap:
            continue
        count = session.scalar(
            select(func.count(Routine.id))
            .join(Project, Project.id == Routine.project_id)
            .where(Project.owner == client.slug, Routine.paused.is_(False))
        )
        if count:
            found.append(Held(client.slug, client.name, used, cap, int(count)))
    return found


@dataclass
class TaskLine:
    when: datetime
    project: str
    title: str
    status: str
    result: str
    cost_usd: float | None


@dataclass
class Month:
    client: str
    name: str
    year: int
    month: int
    projects: dict[str, str]
    tasks: list[TaskLine] = field(default_factory=list[TaskLine])
    done: int = 0
    failed: int = 0  # broke: failed, out of time, over its cap
    not_done: int = 0  # ran, and said it couldn't do what was asked
    stopped: int = 0
    open: int = 0
    changes: int = 0
    held: int = 0
    approved: int = 0
    denied: int = 0
    expired: int = 0
    waiting: int = 0
    refused: int = 0
    cost_usd: float = 0.0
    cap_usd: float | None = None
    estimated: bool = False  # a Claude subscription: the SDK's estimate, not a bill

    @property
    def label(self) -> str:
        return label(self.year, self.month)


STATUS_WORDS = {
    TaskStatus.SUCCEEDED: "done",
    TaskStatus.FAILED: "failed",
    TaskStatus.TIMED_OUT: "ran out of time",
    TaskStatus.BUDGET_EXCEEDED: "stopped at its spending cap",
    TaskStatus.STOPPED: "stopped",
    TaskStatus.NOT_DONE: "not done",
}


def _title(task: Task) -> str:
    text = (task.outcome or task.prompt or "").strip().splitlines()
    first = text[0] if text else "A task"
    return scrub(first if len(first) <= TITLE_CHARS else first[: TITLE_CHARS - 1] + "…")


def _result(task: Task) -> str:
    text = " ".join((task.summary or task.error or "").split())
    return scrub(text if len(text) <= RESULT_CHARS else text[: RESULT_CHARS - 1] + "…")


def month_of(session: Session, client: str, year: int, month: int, mode: AuthMode) -> Month:
    owner = clients.get(session, client)
    projects = _projects(session, owner.slug)
    start, end = bounds(year, month)
    found = Month(
        client=owner.slug, name=owner.name, year=year, month=month, projects=projects,
        cap_usd=owner.monthly_cap_usd, estimated=mode is not AuthMode.API_KEY,
    )  # fmt: skip
    if not projects:
        return found
    names = list(projects)
    rows = session.scalars(
        select(Task)
        .where(Task.project.in_(names), Task.created_at >= start, Task.created_at < end)
        .where(tasks_service.real_work())  # not Check my setup's requests
        .options(selectinload(Task.sessions))
        .order_by(Task.created_at)
    )
    for task in rows:
        costs = [s.cost_usd for s in task.sessions if s.cost_usd is not None]
        cost = sum(costs) if costs else None
        found.cost_usd += cost or 0.0
        if task.status is TaskStatus.SUCCEEDED:
            found.done += 1
        elif task.status is TaskStatus.STOPPED:
            found.stopped += 1
        elif task.status is TaskStatus.NOT_DONE:
            found.not_done += 1
        elif task.status in TERMINAL_STATUSES:
            found.failed += 1
        else:
            found.open += 1
        found.tasks.append(
            TaskLine(
                when=task.created_at,
                project=projects[task.project],
                title=_title(task),
                status=STATUS_WORDS.get(task.status, "not finished yet"),
                result=_result(task),
                cost_usd=cost,
            )
        )
    outcomes = Counter(
        session.scalars(
            select(AuditEvent.outcome).where(
                AuditEvent.project.in_(names), AuditEvent.at >= start, AuditEvent.at < end
            )
        )
    )
    found.changes = outcomes[AuditOutcome.EXECUTED]
    found.held = outcomes[AuditOutcome.HELD]
    found.refused = outcomes[AuditOutcome.REFUSED]
    decided = Counter(
        session.scalars(
            select(Decision.status).where(
                Decision.kind == DecisionKind.APPROVE,
                Decision.project.in_(names),
                Decision.created_at >= start,
                Decision.created_at < end,
            )
        )
    )
    found.approved = decided[DecisionStatus.EXECUTED] + decided[DecisionStatus.APPROVED]
    found.approved += decided[DecisionStatus.FAILED]
    found.denied = decided[DecisionStatus.DENIED]
    found.expired = decided[DecisionStatus.EXPIRED]
    found.waiting = decided[DecisionStatus.PENDING]
    return found


def _count(n: int, one: str, many: str | None = None) -> str:
    return f"{n} {one if n == 1 else (many or one + 's')}"


def summary_lines(found: Month, *, cost: bool) -> list[str]:
    tasks = _count(len(found.tasks), "task")
    parts = [f"{found.done} done"]
    parts += [f"{found.not_done} not done"] if found.not_done else []
    parts += [f"{found.failed} failed"] if found.failed else []
    parts += [f"{found.stopped} stopped"] if found.stopped else []
    parts += [f"{found.open} still open"] if found.open else []
    lines = [f"{tasks}: {', '.join(parts)}" if found.tasks else "No tasks"]
    lines.append(f"{_count(found.changes, 'change')} made to {found.name}'s services")
    if found.held:
        decided = (
            (found.approved, "approved"), (found.denied, "declined"),
            (found.expired, "not decided in time"), (found.waiting, "still waiting"),
        )  # fmt: skip
        how = ", ".join(f"{n} {word}" for n, word in decided if n)
        held = f"{_count(found.held, 'change')} waited for your approval"
        lines.append(f"{held}: {how}" if how else held)
    if found.refused:
        lines.append(f"{_count(found.refused, 'call')} refused by Ringkeeper's checks")
    if cost:
        lines.append(
            f"AI cost: {money(found.cost_usd)}" + (" (estimated)" if found.estimated else "")
        )
    return lines


def markdown(found: Month, *, cost: bool) -> str:
    """The month as a report to send the client."""

    def cell(text: str) -> str:
        return " ".join(text.replace("|", "/").split())

    out = [
        f"# {found.name}: {found.label}",
        "",
        f"Work done for {found.name} with Ringkeeper, {found.label}.",
        "",
        "## Summary",
        "",
        *(f"- {line}" for line in summary_lines(found, cost=cost)),
        "",
    ]
    if found.tasks:
        out += ["## Tasks", ""]
        head = ["Date", "Project", "Task", "Result"] + (["AI cost"] if cost else [])
        out += ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
        for line in found.tasks:
            result = line.status + (f": {line.result}" if line.result else "")
            row = [line.when.astimezone().strftime("%b %d"), line.project, line.title, result]
            row += [money(line.cost_usd) if line.cost_usd is not None else ""] if cost else []
            out.append("| " + " | ".join(cell(c) for c in row) + " |")
        out.append("")
    return "\n".join(out)


CSV_HEAD = ["Date", "Project", "Task", "Status", "Result", "AI cost (USD)"]


def csv_cell(value: object) -> str:
    """A spreadsheet doesn't run it: text starting like a formula gets a leading quote."""
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


def as_csv(found: Month) -> str:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\r\n")
    writer.writerow(CSV_HEAD)
    for line in found.tasks:
        cost = f"{line.cost_usd:.4f}" if line.cost_usd is not None else ""
        row = [line.when.astimezone().strftime("%Y-%m-%d %H:%M"), line.project, line.title,
               line.status, line.result, cost]  # fmt: skip
        writer.writerow([csv_cell(c) for c in row])
    return out.getvalue()


def file_name(found: Month, extension: str) -> str:
    return f"{found.client}-{found.year}-{found.month:02d}.{extension}"


# --- sending it: a held email through one of the client's mail connections ---

MAIL_SERVICES = frozenset(
    {InstanceType.SMTP, InstanceType.MAILBOX, InstanceType.RESEND, InstanceType.GMAIL}
)
SENDER = "report:"  # the session the held send is recorded under, as a pass's is "pass:"


@dataclass(frozen=True)
class MailRoute:
    connection: str
    title: str
    project: str  # the client's project it's linked to: the gateway checks it from there
    service: InstanceType


def mail_routes(session: Session, client: str) -> list[MailRoute]:
    """The mail connections the client's projects may use: the client's own, or your own
    mail linked to one of their projects. Never another client's."""
    owner = clients.get(session, client)
    rows = session.scalars(
        select(Project)
        .where(Project.owner == owner.slug)
        .options(selectinload(Project.bindings).selectinload(ProjectInstance.instance))
        .order_by(Project.name)
    )
    found: dict[str, MailRoute] = {}
    for project in rows:
        for link in project.bindings:
            instance = link.instance
            if (
                link.instance_type in MAIL_SERVICES
                and not bindings.crosses_clients(project, instance)
                and instance.name not in found
            ):
                found[instance.name] = MailRoute(
                    instance.name, instance.title or instance.name, project.name, link.instance_type
                )
    return list(found.values())


def email_call(session: Session, found: Month, to: str, connection: str, *, cost: bool) -> ToolCall:
    """The report as an email from that connection, for the gateway (which holds every send)."""
    route = next(
        (r for r in mail_routes(session, found.client) if r.connection == connection), None
    )
    if route is None:
        raise RuleViolationError(
            f"{connection} isn't a mail connection {found.name}'s projects can use. Pick one "
            "from the list, or link one to one of their projects."
        )
    caller = Caller(route.project, SENDER + found.client, SENDER + found.client)
    subject = f"{found.name}: {found.label}"
    args = {"to": [to.strip()], "subject": subject, "body": markdown(found, cost=cost)}
    return ToolCall(caller, route.service, "send_email", route.connection, args)
