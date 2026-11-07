"""Route one message: one model call (one retry on invalid output), then
dispatch each task or ask the operator where it goes.

A task is dispatched when it touches one project, the router is at least
`router_confidence_threshold` sure, and it won't use production. Otherwise the
operator is asked in the conversation first (a route decision): which project,
with the top two candidates, or whether to go ahead on production. A saved
routing example that matches the message exactly settles a single-task message
without asking which project.

A question about Ringkeeper itself (its connections, projects, tasks, approvals,
settings, costs) is answered from Ringkeeper's own records (router.self_answer):
no project, no worker.
"""

import logging
import threading
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ringkeeper.config import Settings, get_settings
from ringkeeper.db import session_scope
from ringkeeper.errors import RingkeeperError
from ringkeeper.listen import start_listener
from ringkeeper.model_choice import router_model
from ringkeeper.models import (
    Agent,
    Decision,
    DecisionKind,
    Env,
    InstanceType,
    Message,
    MessageRole,
    Project,
    Task,
    TaskKind,
    TaskRisk,
)
from ringkeeper.names import slug
from ringkeeper.router import self_answer
from ringkeeper.router.model import RouterModel
from ringkeeper.router.prompt import build_context, system_prompt
from ringkeeper.router.schema import (
    Candidate,
    InvalidOutputError,
    RoutedTask,
    RouterOutput,
    parse,
)
from ringkeeper.services import examples, inbox, mentions, tasks, threads, titles
from ringkeeper.services import help as help_answers
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
        "agent": task.agent,
    }


def _why_asking(session: Session, task: RoutedTask, threshold: float) -> str:
    if task.touches:
        places = [titles.of(session, p) for p in [task.project, *task.touches]]
        return f"it touches {', '.join(places)}"
    return f"confidence {task.confidence:.2f} is below {threshold:.2f}"


def production_connection(session: Session, task: RoutedTask) -> str | None:
    """The production connection a task would use, when it would: it changes production,
    or it works with data and its project has a production connection. Then the operator
    says go ahead first, so a vague request never reaches a client's live system."""
    project = session.scalar(select(Project).where(Project.name == task.project))
    if project is None:
        return None
    live = sorted(
        b.instance.title or b.instance.name for b in project.bindings  # as people read it
        if b.instance.env is Env.PROD and b.instance.type is not InstanceType.GOOGLE
    )  # fmt: skip
    if not live:
        return None
    if task.risk is TaskRisk.PROD_WRITE or task.kind is TaskKind.DATA:
        return live[0] if len(live) == 1 else ", ".join(live)
    return None


def _ask(
    session: Session,
    settings: Settings,
    message: Message,
    task: RoutedTask,
    production: str | None = None,
) -> Decision:
    payload: dict[str, Any] = {
        "message": message.body,
        "candidates": [c.model_dump() for c in candidates(task)],
        "task": _task_fields(task),
        "reason": _why_asking(session, task, settings.router_confidence_threshold),
    }
    if production is not None:  # sure of the project; asking before it uses production
        payload["candidates"] = [{"project": task.project, "confidence": task.confidence}]
        payload["production"] = production
        payload["reason"] = f"it would use {production}, a production connection"
    return inbox.create(
        session,
        settings,
        DecisionKind.ROUTE,
        payload,
        thread_id=message.thread_id,
        message_id=message.id,
    )


def named_agent(session: Session, project: str, name: str | None) -> Agent | None:
    """The project's agent, however the model wrote its name: "Stock Checker" is stock-checker.
    A task without it could reach more than the agent can, so this mustn't miss on spelling."""
    if not name:
        return None
    return session.scalar(
        select(Agent)
        .join(Project, Project.id == Agent.project_id)
        .where(Project.name == project, Agent.name == slug(name))
    )


def _dispatch(session: Session, settings: Settings, message: Message, task: RoutedTask) -> Task:
    # The agent the operator named, if it's one of the project's (else the plain task).
    agent = named_agent(session, task.project, task.agent)
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
        agent_id=agent.id if agent is not None else None,
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


def _reply(session: Session, routed: Routed, output: RouterOutput) -> str:
    """Plain words: what started, and what Ringkeeper needs from the operator (the
    question itself is shown under the reply, with its buttons)."""
    lines: list[str] = []
    for task in routed.tasks:
        what = task.outcome or "the task"
        plan = " I'll show you a plan first." if task.phase is not None else ""
        lines.append(f"Working on it in {titles.of(session, task.project)}: {what}{plan}")
    for decision in routed.decisions:
        payload = decision.payload or {}
        if payload.get("production"):
            lines.append(f"Before I start: this would use {payload['production']} (production).")
        else:
            lines.append("Which project is this for?")
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

    named = mentions.find(session, message.body)
    known = [*context.owners, *set(context.owners.values()), *context.aliases]
    if (output.about_ringkeeper and help_answers.is_how_to(message.body)) or (
        not named and help_answers.asks_how(message.body, known)
    ):
        helped = help_answers.answer(session, settings, model, message.body)
        meta |= {
            "output": output.model_dump(mode="json"), "about_ringkeeper": True,
            "cost_usd": (cost + helped.cost) or None, "tasks": [], "decisions": [],
        }  # fmt: skip
        threads.mark_routed(session, message, meta)
        threads.add_reply(
            session, message.thread_id, helped.text, routing={"help": helped.as_dict()}
        )
        return Routed(reply=helped.text)
    if output.about_ringkeeper or (
        not named
        and self_answer.asks_about_setup(
            message.body, [*context.owners, *set(context.owners.values()), *context.aliases]
        )
    ):
        reply, spent = self_answer.answer(session, settings, model, message.body)
        meta |= {
            "output": output.model_dump(mode="json"), "about_ringkeeper": True,
            "cost_usd": (cost + spent) or None, "tasks": [], "decisions": [],
        }  # fmt: skip
        threads.mark_routed(session, message, meta)
        threads.add_reply(session, message.thread_id, reply)
        return Routed(reply=reply)

    output = mentions.settle(session, _apply_example(session, message, output), named)
    routed = Routed()
    threshold = settings.router_confidence_threshold
    for task in output.tasks:
        if task.touches or task.confidence < threshold:
            routed.decisions.append(_ask(session, settings, message, task))
        elif (live := production_connection(session, task)) is not None:
            routed.decisions.append(_ask(session, settings, message, task, live))
        else:
            routed.tasks.append(_dispatch(session, settings, message, task))
    routed.reply = _reply(session, routed, output)
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
