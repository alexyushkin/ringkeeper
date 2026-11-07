"""Plays the person: sends each request, answers what's asked, and gathers what happened."""

import asyncio
import json
import sqlite3
import time
import uuid
from typing import Any, cast

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ringkeeper.adapters import ADAPTERS
from ringkeeper.calls import Caller, ToolCall
from ringkeeper.config import Settings
from ringkeeper.db import session_scope
from ringkeeper.errors import RingkeeperError, RuleViolationError
from ringkeeper.models import (
    Agent,
    AuditEvent,
    AuditOutcome,
    Decision,
    DecisionKind,
    DecisionStatus,
    InstanceType,
    Message,
    MessageRole,
    RoutingExample,
    SessionEvent,
    Task,
    TaskStatus,
    Thread,
    WorkerSession,
)
from ringkeeper.scenarios.catalog import APPROVE, DENY, TYPED, Row, Scenario
from ringkeeper.scenarios.checks import Evidence
from ringkeeper.scenarios.world import World
from ringkeeper.services import calls, decisions, inbox, routines, tasks, threads, undo
from ringkeeper.services.calls import RefusalError
from ringkeeper.vault import Vault

AUTHOR = "self-test"
POLL_SECONDS = 0.5
ANSWER = "Use your best judgement and carry on. Don't ask again."
RESTING = (
    TaskStatus.SUCCEEDED, TaskStatus.FAILED, TaskStatus.TIMED_OUT, TaskStatus.STOPPED,
    TaskStatus.BUDGET_EXCEEDED, TaskStatus.NEEDS_INPUT,
)  # fmt: skip
OPEN = (DecisionStatus.PENDING, DecisionStatus.APPROVED)
UNDO_SECONDS = 120


def _blocks(event: SessionEvent) -> list[dict[str, Any]]:
    message = cast(dict[str, Any], event.payload.get("message") or {})
    return cast(list[dict[str, Any]], message.get("content") or [])


def tool_names(events: list[SessionEvent]) -> list[str]:
    """The tools that ran. A call that came back as an error doesn't count as using the tool."""
    called: dict[str, str] = {}
    for event in events:
        if event.type == "sdk.assistant":
            for block in _blocks(event):
                if block.get("_kind") == "ToolUseBlock":
                    called[str(block.get("id"))] = str(block.get("name"))
    failed = {
        str(block.get("tool_use_id"))
        for event in events
        if event.type == "sdk.user"
        for block in _blocks(event)
        if block.get("_kind") == "ToolResultBlock" and block.get("is_error")
    }
    return [name for ident, name in called.items() if ident not in failed]


class Driver:
    def __init__(self, settings: Settings, vault: Vault, world: World) -> None:
        self.settings = settings
        self.vault = vault
        self.world = world

    # --- Answering, as the person would ---

    def _say(self, session: Session, ident: str, reply: inbox.Answer) -> None:
        inbox.answer(session, self.settings, ident, reply, by=AUTHOR, via=inbox.Via.API)

    def _route(
        self, session: Session, decision: Decision, scenario: Scenario, seen: Evidence
    ) -> None:
        payload = decision.payload or {}
        candidates = [str(c["project"]) for c in payload.get("candidates", [])]
        if payload.get("production"):
            seen.confirmed_production = True
            choice = candidates[0]
        else:
            seen.asked = True
            wanted = scenario.project if isinstance(scenario.project, str) else None
            choice = scenario.pick or wanted or (candidates[0] if candidates else inbox.NO_PROJECT)
        self._say(session, str(decision.id), inbox.Answer(choice=choice))

    def _approve(self, ident: str, instance: str | None, how: str, seen: Evidence) -> None:
        if how == TYPED:
            try:
                with session_scope() as session:
                    self._say(session, ident, inbox.Answer(choice=APPROVE))
            except RuleViolationError:
                seen.typed_enforced = True
        with session_scope() as session:
            decision = decisions.get_decision(session, ident)
            if decision.status is not DecisionStatus.PENDING or decision.answer:
                return
            typed = instance if decisions.confirmation_needed(decision) else None
            self._say(
                session, ident, inbox.Answer(choice=DENY if how == DENY else APPROVE, confirm=typed)
            )

    def _settle(
        self, thread: uuid.UUID, scenario: Scenario, seen: Evidence, noted: set[str]
    ) -> bool:
        """Answer what's waiting. True once nothing more is happening."""
        holds: list[tuple[str, str | None]] = []
        with session_scope() as session:
            messages = session.scalars(
                select(Message).where(Message.thread_id == thread, Message.role == MessageRole.USER)
            ).all()
            if any(m.routed_at is None for m in messages):
                return False
            found = session.scalars(select(Task).where(Task.thread_id == thread)).all()
            waiting = session.scalars(
                select(Decision).where(
                    Decision.status.in_(OPEN),
                    (Decision.thread_id == thread) | Decision.task_id.in_([t.id for t in found]),
                )
            ).all()
            for decision in waiting:
                ident = str(decision.id)
                if decision.status is DecisionStatus.APPROVED or decision.answer:
                    continue  # the gateway is running it
                if decision.kind is DecisionKind.ROUTE:
                    self._route(session, decision, scenario, seen)
                elif decision.kind is DecisionKind.PLAN:
                    self._say(session, ident, inbox.Answer(choice=APPROVE))
                elif decision.kind is DecisionKind.QUESTION:
                    budget = (decision.payload or {}).get("budget")
                    self._say(session, ident, inbox.Answer(text="stop" if budget else ANSWER))
                else:
                    holds.append((ident, decision.instance))
                    if ident not in noted:
                        noted.add(ident)
                        seen.holds.append(f"{decision.tool} on {decision.instance}")
            calm = not waiting and all(t.status in RESTING for t in found)
        for ident, instance in holds:
            self._approve(ident, instance, scenario.answer, seen)
        return calm

    def _wait(self, thread: uuid.UUID, scenario: Scenario, seen: Evidence, deadline: float) -> None:
        noted: set[str] = set()
        calm = 0
        while time.monotonic() < deadline:
            # Twice in a row: an answer can take a moment to become a new session.
            calm = calm + 1 if self._settle(thread, scenario, seen, noted) else 0
            if calm >= 2:
                return
            time.sleep(POLL_SECONDS)
        seen.timed_out = True
        with session_scope() as session:
            for task in session.scalars(select(Task).where(Task.thread_id == thread)).all():
                if task.status not in RESTING:
                    tasks.request_stop(session, str(task.id), by=AUTHOR)

    # --- What happened ---

    def _gather(self, thread: uuid.UUID, seen: Evidence) -> list[str]:
        with session_scope(commit=False) as session:
            found = session.scalars(select(Task).where(Task.thread_id == thread)).all()
            ids = [t.id for t in found]
            sessions = session.scalars(
                select(WorkerSession).where(WorkerSession.task_id.in_(ids))
            ).all()
            events = list(
                session.scalars(
                    select(SessionEvent).where(
                        SessionEvent.session_id.in_([s.id for s in sessions])
                    )
                ).all()
            )
            messages = session.scalars(select(Message).where(Message.thread_id == thread)).all()
            said = [m.body for m in messages if m.role is not MessageRole.USER]
            said += [t.summary for t in found if t.summary]
            seen.projects = sorted({t.project for t in found})
            seen.statuses = [t.status.value for t in found]
            for task in found:
                if task.agent_id:
                    agent = session.get(Agent, task.agent_id)
                    seen.agents.append(agent.name if agent else "?")
            seen.tools = tool_names(events)
            seen.replies = "\n".join(said)
            seen.text = "\n".join([*said, *(json.dumps(e.payload, default=str) for e in events)])
            seen.refusals = [
                str(reason)
                for reason in session.scalars(
                    select(AuditEvent.reason).where(
                        AuditEvent.outcome == AuditOutcome.REFUSED,
                        AuditEvent.session_id.in_([str(s.id) for s in sessions]),
                    )
                ).all()
            ]
            seen.plan_limit = any(t.plan_limit for t in found)
            seen.by_help = any((m.routing or {}).get("help") for m in messages)
            spent = sum(s.cost_usd or 0.0 for s in sessions)
            spent += sum(float((m.routing or {}).get("cost_usd") or 0.0) for m in messages)
            seen.cost = round(spent, 4)
            seconds = sum(
                (s.finished_at - s.started_at).total_seconds()
                for s in sessions
                if s.started_at and s.finished_at
            )
            seen.minutes = round(seconds / 60, 2)
            return [str(i) for i in ids]

    def _value(self, row: Row) -> object:
        path = self.world.databases[row.database]
        conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
        try:
            found = conn.execute(row.sql).fetchone()
        finally:
            conn.close()
        return found[0] if found else None

    def _undo(self, before: Row, ids: list[str], seen: Evidence) -> None:
        with session_scope(commit=False) as session:
            changes = undo.for_tasks(session, ids)
        ready = [c for c in changes if c["can_revert"]]
        if not ready:
            seen.undone = f"nothing to undo ({[c['why_not'] for c in changes] or 'no changes'})"
            return
        decision = asyncio.run(calls.request_revert(str(ready[-1]["id"]), self.vault))
        ident, instance = str(decision.id), decision.instance
        self._approve(ident, instance, APPROVE, seen)
        deadline = time.monotonic() + UNDO_SECONDS
        while time.monotonic() < deadline:
            with session_scope(commit=False) as session:
                status = decisions.get_decision(session, ident).status
            if status not in OPEN:
                break
            time.sleep(POLL_SECONDS)
        actual = self._value(before)
        seen.undone = "ok" if actual == before.expected else f"the row is {actual!r}"

    def _probe(self, scenario: Scenario, seen: Evidence) -> None:
        spec = ADAPTERS[InstanceType.SUPABASE].tool("query")
        for probe in scenario.probes:
            call = ToolCall(
                Caller(probe.project, None, None), InstanceType.SUPABASE, "query", probe.instance
            )
            with session_scope(commit=False) as session:
                try:
                    calls.authorize(session, call, spec)
                except RefusalError:
                    continue
            seen.probes_allowed.append(f"{probe.project} on {probe.instance}")

    # --- One scenario ---

    def _start(self, scenario: Scenario) -> uuid.UUID:
        with session_scope() as session:
            if scenario.routine is None:
                sent = threads.post_message(session, scenario.request, author=AUTHOR, source="web")
                return sent.thread_id
            routine = routines.get_routine(session, scenario.routine)
            started = routines.start_run(session, self.settings, routine, "test")
            task = started.task
            if task is None:
                raise RuleViolationError(f"the routine didn't start: {started.notes}")
            if task.thread_id is None:  # so the waiting and gathering find it like any other
                place = Thread(title=scenario.title, source="web")
                session.add(place)
                session.flush()
                task.thread_id = place.id
            return task.thread_id

    def run(self, scenario: Scenario) -> Evidence:
        seen = Evidence()
        deadline = time.monotonic() + scenario.minutes * 60
        try:
            thread = self._start(scenario)
            self._wait(thread, scenario, seen, deadline)
            for follow_up in scenario.then:
                with session_scope() as session:
                    threads.post_message(
                        session, follow_up, author=AUTHOR, source="web", thread=str(thread)
                    )
                self._wait(thread, scenario, seen, deadline)
            ids = self._gather(thread, seen)
            seen.rows = [(r.sql, self._value(r), r.expected) for r in scenario.rows]
            if scenario.undo is not None:
                self._undo(scenario.undo.before, ids, seen)
            self._probe(scenario, seen)
        except RingkeeperError as error:
            seen.error = f"{type(error).__name__}: {error}"
        with session_scope() as session:  # an answer here shouldn't teach the next scenario
            session.execute(delete(RoutingExample))
        return seen
