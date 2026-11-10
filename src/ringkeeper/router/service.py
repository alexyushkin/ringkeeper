"""Route one message: one model call (one retry on invalid output), then
dispatch each task or ask the operator where it goes.

A task is dispatched when it touches one project, the router is at least
`router_confidence_threshold` sure, and it won't use production. Otherwise the
operator is asked in the conversation first (a route decision): which project,
with the top two candidates, or whether to go ahead on production. A saved
routing example that matches the message exactly settles a single-task message
without asking which project.

"Which project?" is asked only when it has to be (`settle_project`): never with one
option; not again in a conversation where it was answered; and not for work any of a
client's projects would do (its email, Google, Slack), which goes to the client's
default project. Asking says why in one line, and offers to set that default.

A question about Ringkeeper itself (its connections, projects, tasks, approvals,
settings, costs) is answered from Ringkeeper's own records (router.self_answer):
no project, no worker. A reply that starts nothing is recorded on the message
(`answer`), so a chat that asked gets the same words the app shows.
"""

import logging
import threading
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ringkeeper import claude_failures, sensitive
from ringkeeper.config import Settings, get_settings
from ringkeeper.db import session_scope
from ringkeeper.errors import RingkeeperError
from ringkeeper.listen import start_listener
from ringkeeper.model_choice import router_model
from ringkeeper.models import (
    Agent,
    Client,
    Decision,
    DecisionKind,
    DecisionStatus,
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
from ringkeeper.router import follow_up, self_answer
from ringkeeper.router.model import ClaudeUnavailableError, RouterModel
from ringkeeper.router.prompt import build_context, system_prompt
from ringkeeper.router.schema import (
    Candidate,
    InvalidOutputError,
    RoutedTask,
    RouterOutput,
    parse,
)
from ringkeeper.services import clients, examples, inbox, mentions, tasks, threads, titles
from ringkeeper.services import help as help_answers
from ringkeeper.services.runtime import current_settings
from ringkeeper.workers.manager import DISALLOWED_TOOLS

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 2  # one retry on invalid output
CANDIDATES = 2


@dataclass
class Routed:
    tasks: list[Task] = field(default_factory=list[Task])
    decisions: list[Decision] = field(default_factory=list[Decision])
    reply: str = ""
    error: str | None = None
    notes: list[str] = field(default_factory=list[str])  # why something didn't start


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


def _why_asking(session: Session, task: RoutedTask) -> str:
    """One plain line: why it's asked."""
    if task.touches:
        places = [titles.of(session, p) for p in [task.project, *task.touches]]
        return f"It would change {_either(places, 'and')}. Pick one, or send it as two requests."
    if task.why.strip():
        return task.why.strip()
    places = [titles.of(session, c.project) for c in candidates(task)]
    return f"It could be for {_either(places, 'or')}."


def _either(names: list[str], word: str) -> str:
    return names[0] if len(names) == 1 else f"{', '.join(names[:-1])} {word} {names[-1]}"


def _options(task: RoutedTask) -> list[str]:
    """Every project the task might be for, best first."""
    return list(dict.fromkeys([task.project, *(c.project for c in task.alternatives)]))


def _firm(task: RoutedTask, project: str) -> RoutedTask:
    update: dict[str, Any] = {
        "project": project,
        "confidence": 1.0,
        "alternatives": [],
        "touches": [],
    }
    return task.model_copy(update=update)


def _general(session: Session, message: Message, task: RoutedTask) -> RoutedTask:
    """Work that needs no client's connection runs in the project chosen at the top of the
    app (already settled, like a mention), or in the operator's own client's default."""
    if not task.general or message.target_project:
        return task
    own = clients.own_client(session)
    where = clients.default_project(session, own) if own is not None else None
    return _firm(task, where) if where is not None else task


def no_web(session: Session, task: RoutedTask) -> str | None:
    """Why a task that needs the web can't have it in its project, in plain words."""
    if not task.needs_web:
        return None
    project = session.scalar(select(Project).where(Project.name == task.project))
    if project is None or "WebSearch" not in DISALLOWED_TOOLS[project.autonomy]:
        return None
    title = titles.of(session, project.name)
    return (
        f"Tasks in {title} can't use the web: its autonomy is {project.autonomy.value}, which "
        f"turns web access off. To allow it, set {title} to relaxed on its Settings tab, "
        "then send this again."
    )


def remembered(session: Session, message: Message) -> str | None:
    """The project the operator chose when asked earlier in this conversation."""
    return session.scalar(
        select(Decision.project)
        .where(
            Decision.thread_id == message.thread_id,
            Decision.kind == DecisionKind.ROUTE,
            Decision.status == DecisionStatus.ANSWERED,
            Decision.project.is_not(None),
            Decision.created_at < message.created_at,
        )
        .order_by(Decision.created_at.desc())
        .limit(1)
    )


def settle_project(
    session: Session,
    settings: Settings,
    message: Message,
    task: RoutedTask,
    owners: dict[str, str],
) -> RoutedTask:
    """Where an unsure task goes without asking, if it can: the project chosen earlier in
    this conversation, the only one it could be, or (for work any of a client's projects
    would do) the client's default project."""
    if task.touches or task.confidence >= settings.router_confidence_threshold:
        return task
    options = _options(task)
    before = remembered(session, message)
    if before is not None and (before in options or owners.get(before) == owners.get(task.project)):
        return _firm(task, before)
    if len(options) == 1:
        return _firm(task, options[0])
    whose = {owners.get(p) for p in options}
    if len(whose) == 1 and not task.project_specific:
        default = clients.default_project(session, str(whose.pop()))
        if default is not None:
            return _firm(task, default)
    return task


def _offer_default(session: Session, task: RoutedTask, owners: dict[str, str]) -> str | None:
    """The client to offer a default project for: asked about work any of its projects
    would do, and none chosen yet."""
    whose = {owners.get(p) for p in _options(task)}
    if task.project_specific or task.touches or len(whose) != 1:
        return None
    client = session.get(Client, str(next(iter(whose))))
    return client.slug if client is not None and client.default_project is None else None


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
    owners: dict[str, str] | None = None,
) -> Decision:
    payload: dict[str, Any] = {
        "message": message.body,
        "candidates": [c.model_dump() for c in candidates(task)],
        "task": _task_fields(task),
        "reason": _why_asking(session, task),
    }
    if production is None and (offer := _offer_default(session, task, owners or {})):
        payload["offer_default"] = offer
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
    question itself is shown under the reply, with its buttons).

    The model's own line is kept only when everything it proposed started: it's written
    as if the work were starting ("I'll read the request..."), which isn't true of a task
    that waits for an answer."""
    lines = [threads.working_on(session, task) for task in routed.tasks]
    for decision in routed.decisions:
        payload = decision.payload or {}
        if payload.get("production"):
            lines.append(f"Before I start: this would use {payload['production']} (production).")
        else:
            lines.append(threads.ASKS_PROJECT)
    if output.reply and not routed.decisions:
        lines.insert(0, output.reply)
    return "\n".join(lines) or "Nothing to do."


def _route_tasks(
    session: Session,
    settings: Settings,
    message: Message,
    output: RouterOutput,
    owners: dict[str, str],
) -> Routed:
    """Start each task, or ask first (which project, or a go-ahead on production)."""
    routed = Routed()
    threshold = settings.router_confidence_threshold
    for found in output.tasks:
        task = settle_project(session, settings, message, _general(session, message, found), owners)
        if task.touches or task.confidence < threshold:
            routed.decisions.append(_ask(session, settings, message, task, owners=owners))
        elif (live := production_connection(session, task)) is not None:
            routed.decisions.append(_ask(session, settings, message, task, live))
        elif (blocked := no_web(session, task)) is not None:
            routed.notes.append(blocked)
        else:
            routed.tasks.append(_dispatch(session, settings, message, task))
    said = _reply(session, routed, output)
    nothing = not routed.tasks and not routed.decisions
    lines = routed.notes if nothing and routed.notes else [*routed.notes, said]
    routed.reply = chr(10).join(lines)
    return routed


def _answer_meta(routed: Routed, output: RouterOutput) -> dict[str, Any]:
    """What a reply that started nothing records: its words, and whether the router said it
    couldn't place the request (then a name sent next places it: router.follow_up)."""
    said: dict[str, Any] = {"answer": sensitive.mask(routed.reply)}
    if output.unplaced:
        said["unplaced"] = True
    return said


@dataclass
class Thought:
    """What the model made of a message, worked out with the database read only: applied
    later in a short write (route_next), so no writer waits on a model call."""

    meta: dict[str, Any]
    output: RouterOutput | None = None
    error: str = ""
    owners: dict[str, str] = field(default_factory=dict[str, str])
    named: mentions.Mentions | None = None
    helped: help_answers.Help | None = None  # a how-to, answered from the guide
    answer: str | None = None  # a question about Ringkeeper, answered from its records
    spent: float = 0.0


def think(session: Session, settings: Settings, message: Message, model: RouterModel) -> Thought:
    """Every model call routing a message makes. Reads only."""
    placed = follow_up.unplaced_request(session, message)
    if placed is not None:  # the last request, routed to the project this one names
        message.target_project = placed.project
    request = placed.request if placed is not None else None
    context = build_context(session, settings, message, request)
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
    thought = Thought(meta, output, error, context.owners)
    if output is None:
        return thought
    named = mentions.for_message(session, message)
    thought.named = named
    known = [*context.owners, *set(context.owners.values()), *context.aliases]
    if (output.about_ringkeeper and help_answers.is_how_to(message.body)) or (
        not named and help_answers.asks_how(message.body, known)
    ):
        thought.helped = help_answers.answer(session, settings, model, message.body)
        thought.spent = thought.helped.cost
    elif output.about_ringkeeper or (
        not named and self_answer.asks_about_setup(message.body, known)
    ):
        thought.answer, thought.spent = self_answer.answer(session, settings, model, message.body)
    return thought


def apply(session: Session, settings: Settings, message: Message, thought: Thought) -> Routed:
    """Start what the model's answer says, inside the caller's transaction. No model calls."""
    meta, output = thought.meta, thought.output
    if output is None:
        reply = f"I couldn't route this message: {thought.error}"
        threads.mark_routed(session, message, meta, error=thought.error)
        threads.add_reply(session, message.thread_id, reply, role=MessageRole.SYSTEM)
        return Routed(reply=reply, error=thought.error)
    cost = float(meta.get("cost_usd") or 0.0) + thought.spent
    if thought.helped is not None or thought.answer is not None:
        said = thought.helped.text if thought.helped is not None else str(thought.answer)
        meta |= {
            "output": output.model_dump(mode="json"), "about_ringkeeper": True,
            "cost_usd": cost or None, "tasks": [], "decisions": [],
            "answer": sensitive.mask(said),
        }  # fmt: skip
        threads.mark_routed(session, message, meta)
        extra = {"help": thought.helped.as_dict()} if thought.helped is not None else None
        threads.add_reply(session, message.thread_id, said, routing=extra)
        return Routed(reply=said)
    named = mentions.for_message(session, message)
    output = mentions.settle(session, _apply_example(session, message, output), named)
    routed = _route_tasks(session, settings, message, output, thought.owners)
    meta |= {
        "output": output.model_dump(mode="json"),
        "tasks": [str(t.id) for t in routed.tasks],
        "decisions": [str(d.id) for d in routed.decisions],
    }
    if not routed.tasks and not routed.decisions:  # an answer: what the thread shows
        meta |= _answer_meta(routed, output)
    threads.mark_routed(session, message, meta)
    threads.add_reply(session, message.thread_id, routed.reply)
    return routed


def route_message(
    session: Session, settings: Settings, message: Message, model: RouterModel
) -> Routed:
    """Route a message in one transaction (tests and tools; the router uses route_next)."""
    answered = answer_follow_up(session, settings, message)
    if answered is not None:
        return answered
    return apply(session, settings, message, think(session, settings, message, model))


def answer_follow_up(session: Session, settings: Settings, message: Message) -> Routed | None:
    """A reply that's just the name the open question asked for: answered, no model call."""
    answered = follow_up.answer_open_question(session, settings, message)
    if answered is None:
        return None
    noted: dict[str, Any] = {
        "answered": str(answered.decision.id), "decisions": [], "answer": answered.reply,
        "tasks": [str(answered.task.id)] if answered.task is not None else [],
    }  # fmt: skip
    threads.mark_routed(session, message, noted)
    return Routed(tasks=[answered.task] if answered.task else [], reply=answered.reply)


RETRY_ROUTING = timedelta(minutes=2)  # after Claude's side failed (not a usage limit)


def _failed(message_id: uuid.UUID, error: Exception) -> None:
    """Recorded on the message, so a failing message isn't retried by itself: its reply has
    a Try again button (POST /messages/{id}/retry). On Claude's side, it waits instead."""
    if isinstance(error, ClaudeUnavailableError):
        _wait_for_claude(message_id, error.failure)
        return
    if not isinstance(error, RingkeeperError):
        logger.exception("routing message %s failed", message_id)
    with session_scope() as session:
        message = session.get(Message, message_id)
        if message is None or message.routed_at is not None:
            return
        threads.mark_routed(session, message, None, error=str(error) or type(error).__name__)
        threads.add_reply(
            session, message.thread_id, f"Routing failed: {error}", role=MessageRole.SYSTEM,
            routing={"retry": str(message_id)},
        )  # fmt: skip


def _wait_for_claude(message_id: uuid.UUID, failure: claude_failures.Failure) -> None:
    """The message goes back to be routed later: after a usage limit resets (nothing new
    starts until then), or in a couple of minutes. The conversation is told once."""
    with session_scope() as session:
        message = session.get(Message, message_id)
        if message is None or message.routed_at is not None:
            return
        if failure.kind is claude_failures.Kind.USAGE_LIMIT:
            later = claude_failures.pause_until(session, failure).until
        else:
            later = datetime.now(UTC) + RETRY_ROUTING
        told = bool((message.routing or {}).get("told"))
        threads.wait_to_route(session, message, later, failure.message)
        if not told:
            threads.add_reply(session, message.thread_id, failure.message, role=MessageRole.SYSTEM)


def _claim(settings: Settings) -> tuple[uuid.UUID | None, bool]:
    """Take the next message in a short write: (its id, whether more is needed). A late one
    is dropped, and a reply that answers the open question is answered here. While Claude's
    usage limit lasts, nothing is taken."""
    caught: Exception | None = None
    with session_scope() as session:
        if claude_failures.paused(session) is not None:
            return None, False
        message = threads.claim_unrouted(session)
        if message is None:
            return None, False
        ident = message.id
        if threads.drop_if_late(session, message):
            return ident, False
        try:
            with session.begin_nested():
                if answer_follow_up(session, settings, message) is not None:
                    return ident, False  # no model call needed
        except Exception as error:
            caught = error
        else:
            threads.claim(session, message)
    if caught is not None:
        _failed(ident, caught)
        return ident, False
    return ident, True


def route_next(settings: Settings, model: RouterModel) -> uuid.UUID | None:
    """Route the oldest unrouted message, if any. Returns its id.

    Three steps, so a model call never holds the database: claim the message (a short
    write), ask the model with the database read only, then apply the answer (a short write)
    unless the message was canceled or routed meanwhile."""
    ident, more = _claim(settings)
    if ident is None or not more:
        return ident
    try:
        with session_scope(commit=False) as session:
            message = session.get_one(Message, ident)
            thought = think(session, settings, message, model)
            target = message.target_project
        with session_scope() as session:
            message = session.get_one(Message, ident)
            if message.routed_at is None and message.canceled_at is None:
                message.target_project = target or message.target_project
                with session.begin_nested():  # all of the message's tasks, or none
                    apply(session, settings, message, thought)
    except Exception as error:
        _failed(ident, error)
    return ident


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
