"""Route one message: one model call (one retry on invalid output), then
dispatch each task or ask the operator where it goes.

A task is dispatched when it touches one project and the router is at least
`router_confidence_threshold` sure. Otherwise it becomes a route decision with
the top two candidates. A saved routing example that matches the message
exactly settles a single-task message without asking.
"""

import logging
import threading
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from ringkeeper.config import Settings, get_settings
from ringkeeper.db import session_scope
from ringkeeper.errors import RingkeeperError
from ringkeeper.listen import start_listener
from ringkeeper.model_choice import router_model
from ringkeeper.models import Decision, DecisionKind, Message, MessageRole, Task
from ringkeeper.router.model import RouterModel
from ringkeeper.router.prompt import build_context, system_prompt
from ringkeeper.router.schema import (
    Candidate,
    InvalidOutputError,
    RoutedTask,
    RouterOutput,
    parse,
)
from ringkeeper.services import examples, inbox, tasks, threads
from ringkeeper.services.runtime import current_settings

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 2  # one retry on invalid output
CANDIDATES = 2


@dataclass
class Routed:
    tasks: list[Task] = field(default_factory=list[Task])
    decisions: list[Decision] = field(default_factory=list[Decision])
    reply: str = ""
    error: str | None = None


def _retry_prompt(prompt: str, previous: str, error: str) -> str:
    return (
        f"{prompt}\n\nYOUR PREVIOUS ANSWER WAS INVALID: {error}\n"
        f"Previous answer:\n{previous[:4000]}\n\n"
        "Answer again with one JSON object that fixes this."
    )


def candidates(task: RoutedTask) -> list[Candidate]:
    """The top projects for a task, best first, one entry per project."""
    best: dict[str, float] = {}
    named = [
        Candidate(project=task.project, confidence=task.confidence),
        *(Candidate(project=p, confidence=task.confidence) for p in task.touches),
        *task.alternatives,
    ]
    for candidate in named:
        best[candidate.project] = max(best.get(candidate.project, 0.0), candidate.confidence)
    ranked = sorted(best.items(), key=lambda item: -item[1])
    return [Candidate(project=p, confidence=c) for p, c in ranked[:CANDIDATES]]


def _task_fields(task: RoutedTask) -> dict[str, Any]:
    return {
        "prompt": task.prompt,
        "kind": task.kind.value,
        "risk": task.risk.value,
        "complexity": task.complexity.value,
        "outcome": task.outcome,
    }


def _why_asking(task: RoutedTask, threshold: float) -> str:
    if task.touches:
        return f"it touches {', '.join([task.project, *task.touches])}"
    return f"confidence {task.confidence:.2f} is below {threshold:.2f}"


def _ask(session: Session, settings: Settings, message: Message, task: RoutedTask) -> Decision:
    payload = {
        "message": message.body,
        "candidates": [c.model_dump() for c in candidates(task)],
        "task": _task_fields(task),
        "reason": _why_asking(task, settings.router_confidence_threshold),
    }
    return inbox.create(
        session,
        settings,
        DecisionKind.ROUTE,
        payload,
        thread_id=message.thread_id,
        message_id=message.id,
    )


def _dispatch(session: Session, settings: Settings, message: Message, task: RoutedTask) -> Task:
    return tasks.create_task(
        session,
        settings,
        project=task.project,
        prompt=task.prompt,
        kind=task.kind,
        risk=task.risk,
        complexity=task.complexity,
        outcome=task.outcome,
        thread_id=message.thread_id,
        message_id=message.id,
    )


def _apply_example(session: Session, message: Message, output: RouterOutput) -> RouterOutput:
    if len(output.tasks) != 1:
        return output
    example = examples.match(session, message.body)
    if example is None:
        return output
    [task] = output.tasks
    settled = task.model_copy(
        update={"project": example.project, "confidence": 1.0, "touches": [], "alternatives": []}
    )
    return output.model_copy(update={"tasks": [settled]})


def _reply(routed: Routed, output: RouterOutput) -> str:
    lines: list[str] = []
    for task in routed.tasks:
        how = "architect plans first" if task.phase is not None else "worker queued"
        lines.append(f"→ {task.project}: task {str(task.id)[:8]} ({task.kind}, {how})")
    for decision in routed.decisions:
        names = ", ".join(c["project"] for c in (decision.payload or {}).get("candidates", []))
        lines.append(f"? which project ({names}): decision {str(decision.id)[:8]}")
    if output.reply:
        lines.insert(0, output.reply)
    return "\n".join(lines) or "Nothing to do."


def route_message(
    session: Session, settings: Settings, message: Message, model: RouterModel
) -> Routed:
    """Route a claimed message inside the caller's transaction."""
    context = build_context(session, settings, message)
    system = system_prompt()
    model_id = router_model(settings)
    prompt = context.prompt
    output: RouterOutput | None = None
    error = ""
    attempts = 0
    cost = 0.0
    for attempts in range(1, MAX_ATTEMPTS + 1):  # noqa: B007 (attempts is recorded below)
        completion = model.complete(system, prompt, model_id)
        cost += completion.cost_usd or 0.0
        try:
            output = parse(completion.text, context.owners)
            break
        except InvalidOutputError as invalid:
            error = str(invalid)
            prompt = _retry_prompt(context.prompt, completion.text, error)
    meta: dict[str, Any] = {"model": model_id, "attempts": attempts, "cost_usd": cost or None}
    if output is None:
        reply = f"I couldn't route this message: {error}"
        threads.mark_routed(session, message, meta, error=error)
        threads.add_reply(session, message.thread_id, reply, role=MessageRole.SYSTEM)
        return Routed(reply=reply, error=error)

    output = _apply_example(session, message, output)
    routed = Routed()
    threshold = settings.router_confidence_threshold
    for task in output.tasks:
        if not task.touches and task.confidence >= threshold:
            routed.tasks.append(_dispatch(session, settings, message, task))
        else:
            routed.decisions.append(_ask(session, settings, message, task))
    routed.reply = _reply(routed, output)
    meta |= {
        "output": output.model_dump(mode="json"),
        "tasks": [str(t.id) for t in routed.tasks],
        "decisions": [str(d.id) for d in routed.decisions],
    }
    threads.mark_routed(session, message, meta)
    threads.add_reply(session, message.thread_id, routed.reply)
    return routed


def route_next(settings: Settings, model: RouterModel) -> uuid.UUID | None:
    """Route the oldest unrouted message, if any. Returns its id."""
    with session_scope() as session:
        message = threads.claim_unrouted(session)
        if message is None:
            return None
        try:
            with session.begin_nested():  # all of the message's tasks, or none
                route_message(session, settings, message, model)
        except Exception as error:
            # Recorded on the message, so a failing message isn't retried forever.
            if not isinstance(error, RingkeeperError):
                logger.exception("routing message %s failed", message.id)
            threads.mark_routed(session, message, None, error=str(error) or type(error).__name__)
            threads.add_reply(
                session, message.thread_id, f"Routing failed: {error}", role=MessageRole.SYSTEM
            )
        return message.id


class Router:
    """Routes messages as they arrive (runs in the api service)."""

    def __init__(self, model: RouterModel, settings: Settings | None = None) -> None:
        self.model = model
        self.settings = settings or get_settings()
        self._wake = threading.Event()

    def _on_notify(self, _channel: str, _payload: str) -> None:
        self._wake.set()

    def drain(self) -> int:
        routed = 0
        if current_settings(self.settings).paused:  # "Stop everything": messages wait
            return 0
        while route_next(self.settings, self.model) is not None:
            routed += 1
        return routed

    def run_forever(self, stop: threading.Event) -> None:
        start_listener(
            self.settings, [threads.MESSAGES_CHANNEL], self._on_notify, stop, "rk-router-listen"
        )
        while not stop.is_set():
            try:
                self.drain()
            except Exception:
                logger.exception("router pass failed")
            self._wake.wait(self.settings.manager_poll_seconds * 5)
            self._wake.clear()
