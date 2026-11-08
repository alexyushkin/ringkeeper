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
    Task,
    TaskStatus,
    WorkerSession,
)
from ringkeeper.scrub import scrub
from ringkeeper.services import bindings, clients

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


def check_cap(session: Session, project: str, now: datetime | None = None) -> None:
    """Refuse a new task once its client's AI cost this month has reached their cap."""
    owner = session.scalar(select(Project.owner).where(Project.name == project))
    if owner is None:
        return
    row = session.get(Client, owner)
    if row is None or row.monthly_cap_usd is None:
        return
    when = (now or local_now()).astimezone()
    used = spend(session, owner, when.year, when.month)
    if used >= row.monthly_cap_usd:
        raise CapReachedError(
            f"{row.name} has reached its monthly cap: {money(used)} of "
            f"{money(row.monthly_cap_usd)} in {label(when.year, when.month)}. Raise the cap on "
            f"the client's page (Settings tab), or new tasks for {row.name} start next month."
        )


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
    failed: int = 0
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
    parts += [f"{found.failed} didn't finish"] if found.failed else []
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
