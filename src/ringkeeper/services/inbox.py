"""The inbox: every decision waiting for a human, and what answering one does.

route     choice = a project (or "none")  -> creates the task, saves a routing example
approve   choice = approve | deny         -> runs or drops a held prod write
plan      choice = approve | deny (+text) -> the architect dispatches a worker, or revises
question  text                            -> resumes the session that asked

Every answer is audited with who answered and how (cli, api, telegram).
Approving a held write from the API or Telegram only records the answer: the
gateway, which can reach the instances, runs it (see `pending_approvals`).
The CLI runs it in place, like `ringkeeper decision approve`.
"""

import enum
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ringkeeper.config import Settings
from ringkeeper.errors import RuleViolationError
from ringkeeper.models import (
    ArchitectPhase,
    AuditEvent,
    AuditOutcome,
    Complexity,
    Decision,
    DecisionKind,
    DecisionStatus,
    SessionRole,
    Task,
    TaskKind,
    TaskRisk,
    TaskStatus,
)
from ringkeeper.scrub import scrub, scrub_obj
from ringkeeper.services import decisions, examples, projects, tasks
from ringkeeper.services.architect import MAX_REVIEWS
from ringkeeper.services.audit import safe_text
from ringkeeper.services.decisions import expire_stale, get_decision, notify_new
from ringkeeper.storage import events

APPROVALS_CHANNEL = "ringkeeper_approvals"
NO_PROJECT = "none"


class Via(enum.StrEnum):
    CLI = "cli"
    API = "api"
    TELEGRAM = "telegram"


@dataclass(frozen=True)
class Answer:
    choice: str | None = None
    text: str | None = None
    voice: bool = False  # text is a voice note's transcript, confirmed by the user
    confirm: str | None = None  # the connection's name, typed to approve a destructive write


def create(
    session: Session,
    settings: Settings,
    kind: DecisionKind,
    payload: dict[str, Any],
    *,
    project: str | None = None,
    task: Task | None = None,
    thread_id: uuid.UUID | None = None,
    message_id: uuid.UUID | None = None,
) -> Decision:
    if kind is DecisionKind.APPROVE:
        raise RuleViolationError("held writes are created by the gateway, not the inbox")
    now = datetime.now(UTC)
    decision = Decision(
        kind=kind,
        status=DecisionStatus.PENDING,
        project=project,
        payload=payload,
        task_id=task.id if task is not None else None,
        thread_id=thread_id if thread_id is not None else (task.thread_id if task else None),
        message_id=message_id if message_id is not None else (task.message_id if task else None),
        expires_at=now + timedelta(seconds=settings.decision_ttl_seconds),
    )
    session.add(decision)
    session.flush()
    notify_new(session, decision)
    return decision


def list_pending(session: Session, *, kind: DecisionKind | None = None) -> Sequence[Decision]:
    expire_stale(session)
    query = (
        select(Decision)
        .where(Decision.status == DecisionStatus.PENDING)
        .order_by(Decision.created_at)
    )
    if kind is not None:
        query = query.where(Decision.kind == kind)
    return session.scalars(query).all()


def list_decisions(
    session: Session,
    *,
    status: DecisionStatus | None = DecisionStatus.PENDING,
    kind: DecisionKind | None = None,
    project: str | None = None,
    limit: int = 50,
) -> Sequence[Decision]:
    expire_stale(session)
    query = select(Decision).order_by(Decision.created_at.desc()).limit(limit)
    if status is not None:
        query = query.where(Decision.status == status)
    if kind is not None:
        query = query.where(Decision.kind == kind)
    if project is not None:
        query = query.where(Decision.project == project)
    return session.scalars(query).all()


def options(decision: Decision) -> list[str]:
    """The choices a decision offers (empty: it takes free text)."""
    if decision.kind in (DecisionKind.APPROVE, DecisionKind.PLAN):
        return ["approve", "deny"]
    if decision.kind is DecisionKind.ROUTE:
        candidates = (decision.payload or {}).get("candidates", [])
        return [c["project"] for c in candidates] + [NO_PROJECT]
    return []


def _audit(session: Session, decision: Decision, answer: Answer, by: str, via: Via) -> None:
    details = {"via": via.value, "by": by, "kind": decision.kind.value}
    if answer.choice is not None:
        details["choice"] = answer.choice
    if answer.voice:
        details["input"] = "voice"
    session.add(
        AuditEvent(
            project=decision.project,
            outcome=AuditOutcome.ANSWERED,
            reason=safe_text(f"{decision.kind} decision answered via {via} by {by}"),
            args=scrub_obj(details),
            decision_id=decision.id,
        )
    )


def _require_open(decision: Decision) -> None:
    if decision.status is not DecisionStatus.PENDING:
        raise RuleViolationError(f"decision {decision.id} is already {decision.status}")
    if decision.expires_at <= datetime.now(UTC):
        raise RuleViolationError(f"decision {decision.id} expired; it can't be answered")
    if decision.answer is not None:
        raise RuleViolationError(f"decision {decision.id} is already answered; it's running")


def _close(decision: Decision, status: DecisionStatus, by: str, via: Via, answer: Answer) -> None:
    decision.status = status
    decision.decided_at = datetime.now(UTC)
    decision.decided_by = by
    decision.answered_via = via.value
    stored: dict[str, Any] = {
        k: v for k, v in (("choice", answer.choice), ("text", answer.text)) if v
    }
    if answer.voice:
        stored["input"] = "voice"
    decision.answer = stored
    if status is DecisionStatus.DENIED and answer.text:
        decision.reason = safe_text(answer.text)


def _task_of(session: Session, decision: Decision) -> Task:
    if decision.task_id is None:
        raise RuleViolationError(f"decision {decision.id} isn't linked to a task")
    task = tasks.get_task(session, str(decision.task_id), lock=True)
    if task.status not in (TaskStatus.NEEDS_INPUT, TaskStatus.WAITING):
        raise RuleViolationError(f"task {task.id} is {task.status}; nothing is waiting on this")
    return task


def _choice(answer: Answer, allowed: list[str]) -> str:
    choice = (answer.choice or "").strip().lower()
    if choice not in allowed:
        raise RuleViolationError(f"answer with one of: {', '.join(allowed)}")
    return choice


def _answer_approve(decision: Decision, answer: Answer, by: str, via: Via) -> bool:
    """Returns True if the caller should run the held write now (CLI only)."""
    choice = _choice(answer, ["approve", "deny"])
    if choice == "deny":
        _close(decision, DecisionStatus.DENIED, by, via, answer)
        return False
    decisions.check_confirmation(decision, answer.confirm, via.value)
    decision.answered_via = via.value
    decision.answer = {"choice": "approve", "by": by}
    if decisions.confirmation_needed(decision):
        # Checked again when the gateway runs it (decisions.claim_for_approval).
        decision.answer["confirm"] = (answer.confirm or "").strip()
    return via is Via.CLI


def destructive_reason(decision: Decision) -> str | None:
    """Why approving needs the connection's name typed (see decisions.confirmation_needed)."""
    return decisions.confirmation_needed(decision)


def _answer_plan(
    session: Session, settings: Settings, decision: Decision, answer: Answer, by: str, via: Via
) -> None:
    choice = _choice(answer, ["approve", "deny"])
    task = _task_of(session, decision)
    architect = tasks.last_sdk_session(task, SessionRole.ARCHITECT)
    if architect is None:
        raise RuleViolationError(f"task {task.id} has no architect session to resume")
    if choice == "approve":
        _close(decision, DecisionStatus.APPROVED, by, via, answer)
        task.phase = ArchitectPhase.BUILDING
        note = f"\n\nThe user added: {answer.text}" if answer.text else ""
        prompt = (
            "The user approved your plan. Call dispatch_worker now with the worker prompt "
            f"(refine it if needed).{note}"
        )
        tasks.add_session(
            session, settings, task, prompt, SessionRole.ARCHITECT, sdk_session_id=architect
        )
        return
    _close(decision, DecisionStatus.DENIED, by, via, answer)
    if not answer.text:
        task.status = TaskStatus.STOPPED
        task.finished_at = datetime.now(UTC)
        task.error = f"plan denied by {by}"
        return
    task.phase = ArchitectPhase.PLANNING
    prompt = (
        f"The user rejected your plan with this feedback:\n\n{answer.text}\n\n"
        "Revise the plan and submit it again with submit_plan."
    )
    tasks.add_session(
        session, settings, task, prompt, SessionRole.ARCHITECT, sdk_session_id=architect
    )


def _answer_question(
    session: Session, settings: Settings, decision: Decision, answer: Answer, by: str, via: Via
) -> None:
    reply = (answer.text or answer.choice or "").strip()
    if not reply:
        raise RuleViolationError("a question needs a text answer")
    answer = Answer(text=reply, voice=answer.voice)
    _close(decision, DecisionStatus.ANSWERED, by, via, answer)
    task = _task_of(session, decision)
    payload = decision.payload or {}
    if payload.get("escalation"):
        # The architect loop hit its review cap: the answer goes to the architect,
        # and answering grants one more worker round.
        role = SessionRole.ARCHITECT
        task.review_round = min(task.review_round, MAX_REVIEWS - 1)
    else:
        role = SessionRole(payload.get("role", SessionRole.WORKER.value))
    sdk_session = tasks.last_sdk_session(task, role)
    if sdk_session is None:
        raise RuleViolationError(f"task {task.id} has no {role} session to resume")
    prompt = (
        f"The user answered your question.\n\nQuestion: {payload.get('question', '')}\n"
        f"Answer: {reply}\n\nContinue the task."
    )
    tasks.add_session(session, settings, task, prompt, role, sdk_session_id=sdk_session)


def _answer_route(
    session: Session, settings: Settings, decision: Decision, answer: Answer, by: str, via: Via
) -> Task | None:
    payload = decision.payload or {}
    choice = (answer.choice or "").strip()
    if choice != NO_PROJECT:
        # Any registered project is fine, not only the two candidates.
        choice = projects.get_project(session, choice).name
    _close(decision, DecisionStatus.ANSWERED, by, via, Answer(choice=choice, text=answer.text))
    decision.project = None if choice == NO_PROJECT else choice
    if choice == NO_PROJECT:
        return None
    routed: dict[str, Any] = payload.get("task", {})
    kind = TaskKind(routed["kind"]) if routed.get("kind") else None
    message = payload.get("message")
    if message:
        examples.save(session, message, choice, kind=kind, decision_id=decision.id, by=by)
    return tasks.create_task(
        session,
        settings,
        project=choice,
        prompt=routed.get("prompt") or message or "",
        kind=kind,
        risk=TaskRisk(routed["risk"]) if routed.get("risk") else None,
        complexity=Complexity(routed["complexity"]) if routed.get("complexity") else None,
        outcome=routed.get("outcome"),
        thread_id=decision.thread_id,
        message_id=decision.message_id,
    )


@dataclass(frozen=True)
class Answered:
    decision: Decision
    run_held_write: bool = False  # CLI: approve the held write now
    task: Task | None = None  # route: the task it created


def answer(
    session: Session,
    settings: Settings,
    ident: str,
    reply: Answer,
    *,
    by: str,
    via: Via,
    expected_kind: DecisionKind | None = None,
) -> Answered:
    expire_stale(session)
    decision = get_decision(session, ident, lock=True)
    if expected_kind is not None and decision.kind is not expected_kind:
        raise RuleViolationError(f"decision {decision.id} is a {decision.kind} decision")
    _require_open(decision)
    run_now = False
    created: Task | None = None
    match decision.kind:
        case DecisionKind.APPROVE:
            run_now = _answer_approve(decision, reply, by, via)
        case DecisionKind.PLAN:
            _answer_plan(session, settings, decision, reply, by, via)
        case DecisionKind.QUESTION:
            _answer_question(session, settings, decision, reply, by, via)
        case DecisionKind.ROUTE:
            created = _answer_route(session, settings, decision, reply, by, via)
    _audit(session, decision, reply, by, via)
    if decision.kind is DecisionKind.APPROVE and decision.status is DecisionStatus.PENDING:
        events.notify(session, APPROVALS_CHANNEL, str(decision.id))
    session.flush()
    return Answered(decision, run_held_write=run_now, task=created)


def pending_approvals(session: Session) -> Sequence[Decision]:
    """Held writes approved from the API or Telegram, waiting for the gateway to run them."""
    return session.scalars(
        select(Decision)
        .where(
            Decision.kind == DecisionKind.APPROVE,
            Decision.status == DecisionStatus.PENDING,
            Decision.answer.is_not(None),
        )
        .order_by(Decision.created_at)
    ).all()


def _one_line(text: str, limit: int) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[: max(limit - 1, 1)] + "…"


def summary(decision: Decision, limit: int = 160) -> str:
    """A short, scrubbed description: never SQL, arguments, diffs or plan text.

    This is all Telegram ever gets; full details stay in the CLI and API.
    """
    payload = decision.payload or {}
    match decision.kind:
        case DecisionKind.APPROVE:
            text = f"{decision.tool} on {decision.instance} (prod write held)"
        case DecisionKind.ROUTE:
            names = " or ".join(c["project"] for c in payload.get("candidates", []))
            text = f"Which project ({names})? {payload.get('task', {}).get('outcome', '')}"
        case DecisionKind.PLAN:
            text = f"Plan ready: {payload.get('title') or 'review the plan'}"
        case DecisionKind.QUESTION:
            text = str(payload.get("question", ""))
    return _one_line(scrub(text), limit)
