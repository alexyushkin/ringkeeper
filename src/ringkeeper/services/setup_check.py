"""Check my setup: real read-only requests on this install's own projects and connections.

The self-test runs made-up clients in a scratch folder; this runs on what's here. Each
connection gets one read-only request (the kind Home suggests), in the first project it's linked
to, through the real router, worker and gateway; a project none of them went to gets one of its
own, to check that requests reach it. Then a question about the setup, a web lookup, and a
follow-up in the same conversation. Three run at a time. A connection linked to no project is a
gap: reported, not run.

Nothing in it changes anything. Its conversations carry a tag, and the work they start:
- gets no tool that edits files or runs commands (Edit, Write, Bash and the like), and
- has every write held by the gateway, even one that would run without asking elsewhere.
Whatever is held, or asks to go ahead on production or to turn the web on, is left for the
user and reported. A route question is answered with the project the request was written for,
and a worker's question with "only read". The tag lets the conversations be archived in one
click.
"""

import contextlib
import json
import logging
import threading
import time
import uuid
from collections.abc import Callable, Sequence
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, selectinload

from ringkeeper.config import Settings
from ringkeeper.db import session_scope
from ringkeeper.errors import RingkeeperError, RuleViolationError
from ringkeeper.models import (
    TERMINAL_STATUSES,
    AuditEvent,
    AuditOutcome,
    Client,
    Decision,
    DecisionKind,
    DecisionStatus,
    Instance,
    InstanceType,
    Message,
    MessageRole,
    Project,
    ProjectInstance,
    RoutingExample,
    SessionEvent,
    Task,
    TaskStatus,
    Thread,
    WorkerSession,
)
from ringkeeper.services import cleanup, inbox, instances, onboarding, tasks, threads, titles
from ringkeeper.services import probes as test_button

logger = logging.getLogger(__name__)

TAG = "setup-check"
AUTHOR = "setup-check"
TITLE = "Check my setup: "
FOLDER = "setup-check"
# Tools a check's worker doesn't get: it reads, and changes nothing in the project's folder.
READ_ONLY_TOOLS = ("Edit", "Write", "MultiEdit", "NotebookEdit", "Bash")
NOTHING_KEPT = "Check my setup only reads, so nothing is kept from it. Don't call this again."
ONLY_READ = "Only read: don't change anything. Use your best judgement and carry on."
PROBE_MINUTES = 6.0
POLL_SECONDS = 1.0
DEFAULT_CEILING = 5.0  # USD: no more requests are started past this (an API key's spend)
START_SECONDS = 90  # the first request not routed by then: the app isn't running
AT_ONCE = 3  # requests under way at the same time
SETTLED = frozenset({*TERMINAL_STATUSES, TaskStatus.NEEDS_INPUT})

SETUP_QUESTION = "Which projects do I have, and which connections does each one use?"
WEB_LOOKUP = "Look up the latest stable Python release on python.org and tell me its version."
FOLLOW_UP = "Thanks. Now say that again in one or two sentences."
# Tested without a model: connect, and count what it offers (the connection's Test button).
DIRECT = frozenset({InstanceType.MCP, InstanceType.VENDOR, InstanceType.HTTP})
DIRECT_REQUEST = "Connect, and count the tools it offers (without a model)"
TESTED = "tested directly, without a model"


@dataclass(frozen=True)
class Probe:
    kind: str  # connection, project, setup, web, follow-up
    request: str
    project: str | None = None  # where it should go (a project's name)
    project_title: str = ""
    connection: str | None = None
    connection_title: str = ""
    service: str = ""
    direct: bool = False  # tested by Ringkeeper itself, without a model or the router

    @property
    def label(self) -> str:
        if self.direct:
            return self.connection_title
        if self.kind == "connection":
            return f"{self.connection_title} in {self.project_title}"
        if self.kind == "project":
            return f"A request for {self.project_title}"
        return {"setup": "A question about your setup", "web": "A web lookup"}.get(
            self.kind, "A follow-up in the same conversation"
        )


@dataclass(frozen=True)
class Gap:
    connection: str
    title: str
    client: str


@dataclass
class Outcome:
    probe: Probe
    thread: str | None = None
    routed: str = ""  # where it went, in words
    projects: list[str] = field(default_factory=list[str])
    asked: list[str] = field(default_factory=list[str])
    left: list[str] = field(default_factory=list[str])  # held or waiting, left for the user
    ran: list[str] = field(default_factory=list[str])  # tools that ran (not failed calls)
    result: str = ""
    error: str = ""
    notes: list[str] = field(default_factory=list[str])  # refused calls it got past, and such
    cost: float = 0.0
    timed_out: bool = False

    @property
    def reached(self) -> bool:
        """A connection's request used that connection (or left something for the user)."""
        if self.probe.kind != "connection" or self.probe.direct or self.left:
            return True
        return any(r.endswith(f" on {self.probe.connection}") for r in self.ran)

    @property
    def ok(self) -> bool:
        return not self.error and not self.timed_out and self.reached


def _of(instance: Instance) -> str:
    return instance.title or instance.name


def plan(session: Session) -> tuple[list[Probe], list[Gap]]:
    """One read-only request per connection, in the first project it's linked to, and one
    for each project none of those went to (so every project is checked for routing). Then
    the setup question and the web lookup. The follow-up goes into the first conversation
    and isn't listed here."""
    found = session.scalars(
        select(Project)
        .options(selectinload(Project.bindings).selectinload(ProjectInstance.instance))
        .order_by(Project.title, Project.name)
    ).all()
    probes: list[Probe] = []
    done: set[uuid.UUID] = set()
    everywhere = session.scalars(select(Instance).where(Instance.all_projects.is_(True))).all()
    for project in found:
        linked = [b.instance for b in project.bindings]
        linked += [i for i in everywhere if i not in linked and _reaches(i, project)]
        asked = [i for i in linked if onboarding.BY_SERVICE.get(i.type) is not None]
        mine = [_probe(project, i, linked) for i in sorted(asked, key=lambda i: i.name)
                if i.id not in done]  # fmt: skip
        done.update(i.id for i in asked)
        if asked and all(p.direct for p in mine):  # none go through the router: is it reached?
            mine += [
                Probe(
                    "project", onboarding.FOLDER.format(project=project.title),
                    project=project.name, project_title=project.title,
                )
            ]  # fmt: skip
        probes += mine
    probes.append(Probe("setup", SETUP_QUESTION))
    probes.append(Probe("web", WEB_LOOKUP))
    gaps = [Gap(i.name, _of(i), _client(session, i)) for i in onboarding.unlinked(session)]
    return probes, gaps


def _probe(project: Project, instance: Instance, linked: Sequence[Instance]) -> Probe:
    asks = onboarding.BY_SERVICE[instance.type]
    said = asks.format(project=project.title, connection=_of(instance))
    if "{connection}" not in asks and sum(i.type is instance.type for i in linked) > 1:
        said += f", using {_of(instance)}"  # which of its databases (say) is meant
    direct = instance.type in DIRECT
    return Probe(
        "connection", DIRECT_REQUEST if direct else said,
        project=project.name, project_title=project.title,
        connection=instance.name, connection_title=_of(instance), service=instance.type.value,
        direct=direct,
    )  # fmt: skip


def _client(session: Session, instance: Instance) -> str:
    if instance.shared or instance.owner == instances.SHARED_OWNER:
        return "yours"
    found = session.get(Client, instance.owner)
    return found.name if found is not None else instance.owner


def _reaches(instance: Instance, project: Project) -> bool:
    """A connection linked to every project: every project of its client, or every project
    there is for a shared tool, but those it leaves out."""
    if project.name in instance.except_projects:
        return False
    if instance.shared:
        squash = "".join(project.owner.lower().split())
        return all("".join(c.lower().split()) != squash for c in instance.except_clients)
    return project.owner == instance.owner


# --- Its conversations and their work ---


def from_check(session: Session, task: Task | None) -> bool:
    """Whether a task was started by Check my setup (its conversation carries the tag)."""
    if task is None or task.thread_id is None:
        return False
    thread = session.get(Thread, task.thread_id)
    return thread is not None and thread.tag == TAG


def session_from_check(session: Session, session_id: str | None) -> bool:
    """The same, for a worker session the gateway was called from."""
    if not session_id:
        return False
    try:
        ident = uuid.UUID(session_id)
    except ValueError:
        return False
    worker = session.get(WorkerSession, ident)
    return worker is not None and from_check(session, session.get(Task, worker.task_id))


def tagged(session: Session) -> list[Thread]:
    return list(
        session.scalars(select(Thread).where(Thread.tag == TAG, Thread.archived_at.is_(None))).all()
    )


def archive(session: Session, *, by: str) -> int:
    """Archive every conversation Check my setup started, and the tasks in them. Returns how
    many conversations."""
    found = tagged(session)
    ids = [str(t.id) for t in found]
    if not ids:
        return 0
    work = session.scalars(
        select(Task.id).where(Task.thread_id.in_([t.id for t in found]), Task.archived_at.is_(None))
    ).all()
    if work:
        cleanup.archive(session, "task", [str(i) for i in work], True, by=by)
    return cleanup.archive(session, "thread", ids, True, by=by)


# --- Running it ---


class NotPickedUpError(RingkeeperError):
    pass


def _work(
    session: Session, thread: uuid.UUID, since: datetime
) -> tuple[list[Task], list[WorkerSession]]:
    """A request's work: tasks it started, and earlier ones in its conversation it resumed."""
    sessions = list(
        session.scalars(
            select(WorkerSession)
            .join(Task, WorkerSession.task_id == Task.id)
            .where(Task.thread_id == thread, WorkerSession.created_at >= since)
        ).all()
    )
    resumed = {s.task_id for s in sessions}
    found = list(
        session.scalars(
            select(Task).where(
                Task.thread_id == thread, or_(Task.created_at >= since, Task.id.in_(resumed))
            )
        ).all()
    )
    return found, sessions


class Runner:
    """Sends the requests, three at a time, and waits on them, answering only what reads."""

    def __init__(
        self,
        settings: Settings,
        *,
        ceiling: float = DEFAULT_CEILING,
        minutes: float = PROBE_MINUTES,
        stop: threading.Event | None = None,
        progress: Callable[[int, int, str | None], None] | None = None,
        arrived: Callable[[list[Outcome]], None] | None = None,
    ) -> None:
        self.settings = settings
        self.ceiling = ceiling
        self.minutes = minutes
        self.stop = stop or threading.Event()
        self.progress: Callable[[int, int, str | None], None] = progress or _quiet
        # What has come back so far, in the order planned, each time one comes back.
        self.arrived: Callable[[list[Outcome]], None] = arrived or _unheard

    # --- One request ---

    def _send(self, probe: Probe, thread: str | None = None) -> uuid.UUID:
        with session_scope() as session:
            sent = threads.post_message(
                session, probe.request, author=AUTHOR, source="web", thread=thread
            )
            place = session.get(Thread, sent.thread_id)
            if place is not None and thread is None:
                place.tag = TAG
                place.title = (TITLE + probe.request)[: threads.TITLE_CHARS]
            return sent.thread_id

    def _answer(self, session: Session, decision: Decision, probe: Probe, out: Outcome) -> bool:
        """Answer it if that only reads, and say what it was. False: it's left for the user."""
        payload = decision.payload or {}
        reply: inbox.Answer | None = None
        if decision.kind is DecisionKind.ROUTE and payload.get("production"):
            _note(
                out.left,
                f"asked to go ahead on {payload['production']}, a production connection: "
                "left for you in Approvals",
            )
        elif decision.kind is DecisionKind.ROUTE and payload.get("web"):
            _note(out.left, f"asked to let {payload['web']} use the web: left for you")
        elif decision.kind is DecisionKind.ROUTE:
            offered = [str(c.get("project")) for c in payload.get("candidates", [])]
            _note(out.asked, "which project" + (f" ({', '.join(offered)})" if offered else ""))
            _untaught(session, self.settings, decision, probe.project or inbox.NO_PROJECT)
            return True
        elif decision.kind is DecisionKind.QUESTION and payload.get("budget"):
            _note(out.asked, "whether to spend more (said stop)")
            reply = inbox.Answer(text="stop")
        elif decision.kind is DecisionKind.QUESTION:
            _note(out.asked, _short(str(payload.get("question") or "a question"), 200))
            reply = inbox.Answer(text=ONLY_READ)
        elif decision.kind is DecisionKind.APPROVE:
            _note(out.left, f"held: {inbox.summary(decision, 120)} (left for you in Approvals)")
        else:
            _note(out.left, "a plan waits for your OK in Approvals")
        if reply is None:
            return False
        inbox.answer(session, self.settings, str(decision.id), reply, by=AUTHOR, via=inbox.Via.API)
        return True

    def _settle(self, thread: uuid.UUID, probe: Probe, out: Outcome, since: datetime) -> bool:
        """Answer what can be answered by reading. True once nothing more will happen."""
        with session_scope() as session:
            said = session.scalars(
                select(Message).where(
                    Message.thread_id == thread,
                    Message.role == MessageRole.USER,
                    Message.created_at >= since,
                )
            ).all()
            if any(m.routed_at is None and not m.route_error for m in said):
                return False
            found, _ = _work(session, thread, since)
            waiting = session.scalars(
                select(Decision).where(
                    Decision.status == DecisionStatus.PENDING,
                    or_(Decision.thread_id == thread, Decision.task_id.in_([t.id for t in found])),
                )
            ).all()
            waiting = [d for d in waiting if not d.answer]
            left = [d for d in waiting if not self._answer(session, d, probe, out)]
            if len(left) < len(waiting):
                return False  # it answered something: there's more to come
            held = {d.task_id for d in left}
            return all(t.status in SETTLED or t.id in held for t in found)

    def _wait(self, thread: uuid.UUID, probe: Probe, out: Outcome, since: datetime) -> None:
        deadline = time.monotonic() + self.minutes * 60
        calm = 0
        while time.monotonic() < deadline:
            # Twice in a row: an answer can take a moment to become new work.
            calm = calm + 1 if self._settle(thread, probe, out, since) else 0
            if calm >= 2:
                return
            time.sleep(POLL_SECONDS)
        out.timed_out = True
        out.error = f"no answer within {self.minutes:g} minutes, so its work was stopped"
        with session_scope() as session:
            found, _ = _work(session, thread, since)
            for task in found:
                if task.status not in SETTLED:
                    with contextlib.suppress(RuleViolationError):
                        tasks.request_stop(session, str(task.id), by=AUTHOR)

    def _gather(self, thread: uuid.UUID, out: Outcome, since: datetime) -> None:
        with session_scope(commit=False) as session:
            found, sessions = _work(session, thread, since)
            messages = session.scalars(
                select(Message)
                .where(Message.thread_id == thread, Message.created_at >= since)
                .order_by(Message.created_at, Message.id)
            ).all()
            out.projects = sorted({t.project for t in found})
            mine = [m for m in messages if m.role is MessageRole.USER]
            meta: dict[str, Any] = (mine[-1].routing or {}) if mine else {}
            if found:
                out.routed = ", ".join(titles.of(session, p) for p in out.projects)
                if any(t.general for t in found):
                    out.routed += " (general work)"
            elif meta.get("about_ringkeeper") or meta.get("help"):
                out.routed = "answered as a question about Ringkeeper"
            elif meta.get("unplaced"):
                out.routed = "nowhere: it asked which project in its reply"
            elif any(m.route_error for m in mine):
                out.routed = "nowhere: the router failed"
            elif mine and mine[-1].routed_at is not None:
                out.routed = "answered without starting work"
            elif mine:
                out.routed = NOT_ROUTED
            out.ran = _tools(session, sessions)
            replies = [m.body for m in messages if m.role is not MessageRole.USER]
            summaries = [t.summary for t in found if t.summary]
            out.result = _short(" ".join(summaries) or (replies[-1] if replies else ""), 500)
            errors = [str(m.route_error) for m in mine if m.route_error]
            errors += [str(t.error) for t in found if t.error]
            refused = [
                f"refused: {reason}"
                for reason in session.scalars(
                    select(AuditEvent.reason).where(
                        AuditEvent.session_id.in_([str(s.id) for s in sessions]),
                        AuditEvent.outcome == AuditOutcome.REFUSED,
                    )
                ).all()
                if reason
            ]
            failed = [t for t in found if t.status in TERMINAL_STATUSES - {TaskStatus.SUCCEEDED}]
            if failed and not errors:
                errors.append(", ".join(f"{t.project}: {t.status.value}" for t in failed))
            # A refused call is the request's error only if nothing got past it.
            if failed or (refused and not found):
                errors += refused
            else:
                out.notes += [f"the gateway {r} (it went on)" for r in dict.fromkeys(refused)]
            if errors and not out.error:
                out.error = _short("; ".join(errors), 400)
            if not out.error and not out.reached:
                out.notes.append(f"it didn't use {out.probe.connection_title}")
            spent = sum(s.cost_usd or 0.0 for s in sessions)
            spent += sum(float((m.routing or {}).get("cost_usd") or 0.0) for m in mine)
            out.cost = round(spent, 4)

    def _test(self, probe: Probe) -> Outcome:
        """Connect and count what it offers, as its Test button does: no model, no router."""
        out = Outcome(probe, routed=TESTED, ran=[f"Test on {probe.connection}"])
        try:
            tested = test_button.test_connection(str(probe.connection), by=AUTHOR, counted=True)
        except RingkeeperError as error:  # removed since, say
            out.error = str(error)
            return out
        if tested.ok:
            out.result = tested.message
        else:
            out.error = tested.message
        return out

    def one(self, probe: Probe, thread: str | None = None) -> Outcome:
        if probe.direct:
            return self._test(probe)
        out = Outcome(probe)
        since = datetime.now(UTC) - timedelta(seconds=1)  # the database keeps whole seconds
        try:
            sent = self._send(probe, thread)
            out.thread = str(sent)
            self._wait(sent, probe, out, since)
            self._gather(sent, out, since)
        except RingkeeperError as error:
            out.error = str(error)
        return out

    # --- The whole check ---

    def run(self, probes: Sequence[Probe]) -> tuple[list[Outcome], list[Probe]]:
        """Three requests at a time, then the follow-up. Returns what ran (in the order
        planned) and what wasn't sent."""
        total = len(probes) + 1  # and the follow-up
        arrived: dict[int, Outcome] = {}
        under_way: dict[Future[Outcome], int] = {}
        waiting = list(enumerate(probes))
        with ThreadPoolExecutor(AT_ONCE, thread_name_prefix="rk-setup-check") as pool:
            while waiting or under_way:
                spent = sum(o.cost for o in arrived.values())
                while waiting and len(under_way) < AT_ONCE and not self._enough(spent):
                    index, probe = waiting.pop(0)
                    under_way[pool.submit(self.one, probe)] = index
                if not under_way:
                    break
                self._say(len(arrived), total, [probes[i] for i in under_way.values()])
                finished, _ = wait(under_way, return_when=FIRST_COMPLETED)
                for future in finished:
                    out = future.result()
                    sent = [o for o in arrived.values() if not o.probe.direct]
                    if not sent and out.timed_out and out.routed in ("", NOT_ROUTED):
                        self.stop.set()  # the others wait the same way: send no more
                        raise NotPickedUpError(NOT_PICKED_UP)
                    arrived[under_way.pop(future)] = out
                self.arrived([arrived[i] for i in sorted(arrived)])
        outcomes = [arrived[i] for i in sorted(arrived)]
        skipped = [p for i, p in enumerate(probes) if i not in arrived]
        first = next(
            (o for o in outcomes if o.ok and o.projects and o.probe.kind == "connection"), None
        )
        if first is not None and first.thread and not self.stop.is_set():
            follow = Probe(
                "follow-up", FOLLOW_UP, project=first.probe.project,
                project_title=first.probe.project_title, connection=first.probe.connection,
                connection_title=first.probe.connection_title, service=first.probe.service,
            )  # fmt: skip
            self._say(len(outcomes), total, [follow])
            outcomes.append(self.one(follow, first.thread))
            self.arrived(outcomes)
        self.progress(len(outcomes), len(outcomes), None)
        return outcomes, skipped

    def _enough(self, spent: float) -> bool:
        return self.stop.is_set() or bool(self.ceiling and spent >= self.ceiling)

    def _say(self, done: int, total: int, now: Sequence[Probe]) -> None:
        self.progress(done, total, ", ".join(p.label for p in now))


NOT_ROUTED = "not routed yet"
NOT_PICKED_UP = (
    "The first request wasn't picked up. Check my setup sends its requests to Ringkeeper "
    "itself: is it running?"
)


def _untaught(session: Session, settings: Settings, decision: Decision, choice: str) -> None:
    """Answer which project without teaching the router: an answer saves the phrasing as a
    routing example, which would steer the user's own requests. Whatever was there before
    stays as it was."""
    said = str((decision.payload or {}).get("message") or "")
    key = threads.normalize(said)
    before = session.scalar(select(RoutingExample).where(RoutingExample.normalized == key))
    kept = (
        {c: getattr(before, c) for c in ("text", "project", "kind", "decision_id", "created_by")}
        if before is not None
        else None
    )
    inbox.answer(
        session, settings, str(decision.id), inbox.Answer(choice=choice), by=AUTHOR,
        via=inbox.Via.API,
    )  # fmt: skip
    session.flush()
    saved = session.scalar(select(RoutingExample).where(RoutingExample.normalized == key))
    if saved is None:
        return
    if kept is None:
        session.delete(saved)
    else:
        for column, value in kept.items():
            setattr(saved, column, value)


def _quiet(_done: int, _total: int, _now: str | None) -> None:
    pass


def _unheard(_so_far: list[Outcome]) -> None:
    pass


def _note(where: list[str], what: str) -> None:
    if what not in where:
        where.append(what)


def _short(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit].rstrip() + " ..."


def _tools(session: Session, sessions: Sequence[WorkerSession]) -> list[str]:
    """The tools that ran, by name; a gateway call with the connection it reached."""
    if not sessions:
        return []
    events = session.scalars(
        select(SessionEvent)
        .where(
            SessionEvent.session_id.in_([s.id for s in sessions]),
            SessionEvent.type == "sdk.assistant",
        )
        .order_by(SessionEvent.id)
    ).all()
    failed = {
        str(block.get("tool_use_id"))
        for event in session.scalars(
            select(SessionEvent).where(
                SessionEvent.session_id.in_([s.id for s in sessions]),
                SessionEvent.type == "sdk.user",
            )
        ).all()
        for block in cast(
            list[dict[str, Any]],
            cast(dict[str, Any], event.payload.get("message") or {}).get("content") or [],
        )
        if block.get("_kind") == "ToolResultBlock" and block.get("is_error")
    }
    seen: list[str] = []
    for event in events:
        message = cast(dict[str, Any], event.payload.get("message") or {})
        for block in cast(list[dict[str, Any]], message.get("content") or []):
            if block.get("_kind") == "ToolUseBlock" and str(block.get("id")) not in failed:
                name = str(block.get("name") or "")
                short = name.rsplit("__", 1)[-1] if name.startswith("mcp__") else name
                args = block.get("input")
                where = (
                    cast(dict[str, Any], args).get("instance") if isinstance(args, dict) else None
                )
                _note(seen, f"{short} on {where}" if where else short)
    return seen


# --- The report ---


def render(
    outcomes: Sequence[Outcome], gaps: Sequence[Gap], *, day: str, skipped: Sequence[Probe] = ()
) -> str:
    spent = sum(o.cost for o in outcomes)
    worked = sum(1 for o in outcomes if o.ok)
    lines = [
        f"# Check my setup, {day}",
        "",
        f"{worked} of {len(outcomes)} requests worked. Cost about ${spent:.2f}. "
        "Nothing was changed: anything that would have is left for you in Approvals.",
        "",
        *table(outcomes),
    ]
    if skipped:
        lines += ["", "Not sent (stopped, or past the cost limit):"]
        lines += [f"- {p.label}" for p in skipped]
    lines += ["", "## Gaps", ""]
    if gaps:
        lines += [
            f"- **{g.title}** ({g.client}): not linked to any project, so no task can use it. "
            "Link it from its card."
            for g in gaps
        ]
    else:
        lines.append("None: every connection is linked to a project.")
    lines += [
        "",
        "Its conversations are marked \u201cCheck my setup\u201d. Archive them all from "
        "Settings \u203a Advanced \u203a Check my setup, or `ringkeeper scenarios archive --mine`.",
        "",
    ]
    return "\n".join(lines)


def table(outcomes: Sequence[Outcome]) -> list[str]:
    """One row per request: the report's, and what has come back while it runs."""
    lines = [
        "| Request | Routed to | Asked | Ran | Result or error | Cost |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for o in outcomes:
        what = f'**{o.probe.label}**: "{_cell(o.probe.request)}"'
        asked = "; ".join(o.asked) or "no"
        ran = ", ".join(o.ran) or "-"
        said = f"Error: {o.error}" if o.error else (o.result or "-")
        if o.left or o.notes:
            said += " " + " ".join(
                [*(f"{_upper(x)}." for x in o.left), *(f"Note: {n}." for n in o.notes)]
            )
        lines.append(
            f"| {what} | {_cell(o.routed) or '-'} | {_cell(asked)} | {_cell(ran)} | "
            f"{_cell(said)} | ${o.cost:.2f} |"
        )
    return lines


def _upper(text: str) -> str:
    return text[:1].upper() + text[1:]


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def save(settings: Settings, text: str, outcomes: Sequence[Outcome], day: str) -> Path:
    folder = (settings.data_dir / FOLDER).absolute()
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{day}.md"
    target.write_text(text, encoding="utf-8")
    (folder / "last.json").write_text(
        json.dumps(
            {
                "day": day,
                "worked": sum(1 for o in outcomes if o.ok),
                "sent": len(outcomes),
                "threads": [o.thread for o in outcomes if o.thread],
            }
        ),
        encoding="utf-8",
    )
    return target


# --- From the app: Settings > Advanced > Check my setup ---


def check(settings: Settings, runner: Runner) -> tuple[str, list[Outcome]]:
    """The whole check: plan it, run it, write the report. Returns the report and outcomes."""
    with session_scope(commit=False) as session:
        probes, gaps = plan(session)
    outcomes, skipped = runner.run(probes)
    day = datetime.now().astimezone().date().isoformat()
    text = render(outcomes, gaps, day=day, skipped=skipped)
    save(settings, text, outcomes, day)
    return text, outcomes


class InApp:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._now: dict[str, Any] | None = None
        self._so_far: list[Outcome] = []
        self._failed: str | None = None

    def running(self) -> bool:
        with self._lock:
            return self._thread is not None and self._thread.is_alive()

    def start(self, settings: Settings) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                raise RuleViolationError("Check my setup is already running.")
            self._stop = threading.Event()
            self._failed = None
            self._now = {"done": 0, "total": 0, "current": None}
            self._so_far = []
            runner = Runner(
                settings, stop=self._stop, progress=self._progress, arrived=self._arrived
            )
            self._thread = threading.Thread(
                target=self._run, args=(settings, runner), daemon=True, name="rk-setup-check"
            )
            self._thread.start()

    def _progress(self, done: int, total: int, current: str | None) -> None:
        with self._lock:
            self._now = {"done": done, "total": total, "current": current}

    def _arrived(self, so_far: list[Outcome]) -> None:
        with self._lock:
            self._so_far = list(so_far)

    def _run(self, settings: Settings, runner: Runner) -> None:
        try:
            check(settings, runner)
        except Exception as error:  # said in the app; the log has the rest
            logger.exception("Check my setup stopped")
            with self._lock:
                self._failed = (
                    str(error)
                    if isinstance(error, RingkeeperError)
                    else (f"Something went wrong ({type(error).__name__}). It's in the log.")
                )

    def stop(self) -> None:
        """Finish the requests under way, and send no more."""
        self._stop.set()

    def status(self, session: Session, settings: Settings) -> dict[str, Any]:
        folder = (settings.data_dir / FOLDER).absolute()
        reports = sorted(folder.glob("*.md")) if folder.is_dir() else []
        last = reports[-1] if reports else None
        running = self.running()
        with self._lock:
            now, failed, so_far = self._now, self._failed, self._so_far
        probes, gaps = plan(session)
        return {
            "running": running,
            "progress": now if running else None,
            # The rows that have come back, while it runs.
            "so_far": "\n".join(table(so_far)) if running and so_far else None,
            "failed": failed,
            "requests": len(probes) + 1,
            "gaps": len(gaps),
            "conversations": len(tagged(session)),
            "report": last.read_text(encoding="utf-8") if last else None,
            "report_day": last.stem if last else None,
        }


in_app = InApp()
