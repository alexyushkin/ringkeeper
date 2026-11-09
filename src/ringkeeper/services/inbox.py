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
from typing import Any, cast

from sqlalchemy import ColumnElement, and_, not_, or_, select
from sqlalchemy.orm import Session, object_session

from ringkeeper import destructive, remote
from ringkeeper.adapters import mcp, vendor
from ringkeeper.adapters.vendor_spec import Money
from ringkeeper.config import Settings
from ringkeeper.errors import RuleViolationError
from ringkeeper.models import (
    Agent,
    ArchitectPhase,
    AuditEvent,
    AuditOutcome,
    Complexity,
    Decision,
    DecisionKind,
    DecisionStatus,
    Env,
    Instance,
    InstanceType,
    Project,
    RoutineRun,
    SessionRole,
    Task,
    TaskKind,
    TaskRisk,
    TaskStatus,
)
from ringkeeper.names import slug
from ringkeeper.scrub import scrub, scrub_obj
from ringkeeper.services import (
    admin_audit,
    client_month,
    decisions,
    examples,
    paging,
    projects,
    tasks,
    threads,
    titles,
)
from ringkeeper.services.architect import MAX_REVIEWS
from ringkeeper.services.audit import safe_text
from ringkeeper.services.decisions import expire_stale, get_decision, notify_new
from ringkeeper.storage import events
from ringkeeper.wording import action, task_status

APPROVALS_CHANNEL = "ringkeeper_approvals"
NO_PROJECT = "none"


class Via(enum.StrEnum):
    CLI = "cli"
    API = "api"
    TELEGRAM = "telegram"
    SLACK = "slack"
    DISCORD = "discord"


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


def list_waiting(session: Session) -> Sequence[Decision]:
    """Pending, not yet past expiry and not snoozed, without marking anything: a read for
    polling."""
    now = datetime.now(UTC)
    query = (
        select(Decision)
        .where(
            Decision.status == DecisionStatus.PENDING,
            Decision.expires_at > now,
            awake(now),
        )
        .order_by(Decision.created_at)
    )
    return session.scalars(query).all()


def awake(now: datetime | None = None) -> ColumnElement[bool]:
    """Not snoozed (any more), as a query."""
    return or_(
        Decision.snoozed_until.is_(None), Decision.snoozed_until <= (now or datetime.now(UTC))
    )


def notice_key(decision: Decision) -> str:
    """What a notification is for: a snoozed decision coming back is a new one."""
    if decision.snoozed_until is None:
        return str(decision.id)
    return f"{decision.id}:{decision.snoozed_until.isoformat()}"


SNOOZE_SPARE = timedelta(minutes=5)  # it comes back at least this long before it runs out


def snooze(session: Session, ident: str, until: datetime | None, *, by: str) -> Decision:
    """An approval out of Approvals, its badge and notifications until `until`, then back
    and notified again. None wakes it now. Never past when it runs out."""
    expire_stale(session)
    decision = get_decision(session, ident, lock=True)
    _require_open(decision)
    if not is_approval(decision):
        raise RuleViolationError("Only an approval can be snoozed. Answer a question instead.")
    if decision.answer:
        raise RuleViolationError("This was already approved, so it can't be snoozed.")
    now = datetime.now(UTC)
    if until is None:
        if decision.snoozed_until is None or decision.snoozed_until <= now:
            return decision
        decision.snoozed_until = None
        admin_audit.record(
            session, "decision.wake", f"woke a snoozed approval: {summary(decision, 80)}",
            by=by, project=decision.project,
        )  # fmt: skip
        return decision
    if until.tzinfo is None:
        raise RuleViolationError("Give the time with its time zone.")
    until = until.astimezone(UTC)
    if until <= now + timedelta(minutes=1):
        raise RuleViolationError("Choose a time at least a minute from now.")
    if until > decision.expires_at - SNOOZE_SPARE:
        raise RuleViolationError(
            "It runs out before then, and nothing would change. Snooze it for less."
        )
    decision.snoozed_until = until
    admin_audit.record(
        session, "decision.snooze",
        f"snoozed an approval until {until.isoformat(timespec='minutes')}: {summary(decision, 80)}",
        by=by, project=decision.project,
    )  # fmt: skip
    session.flush()
    return decision


def list_decisions(
    session: Session,
    *,
    status: DecisionStatus | None = DecisionStatus.PENDING,
    kind: DecisionKind | None = None,
    project: str | None = None,
    limit: int = 50,
    projects: list[str] | None = None,
) -> Sequence[Decision]:
    return page_of_decisions(
        session, status=status, kind=kind, project=project, limit=limit, projects=projects
    ).items


def _approvals() -> ColumnElement[bool]:
    """is_approval, as a query."""
    return or_(
        Decision.kind.in_((DecisionKind.APPROVE, DecisionKind.PLAN)),
        and_(
            Decision.kind == DecisionKind.QUESTION,
            or_(
                Decision.payload["budget"].as_string().is_not(None),
                Decision.payload["client_cap"].as_string().is_not(None),
            ),
        ),
    )


def page_of_decisions(
    session: Session,
    *,
    status: DecisionStatus | None = DecisionStatus.PENDING,
    kind: DecisionKind | None = None,
    project: str | None = None,
    limit: int = 50,
    projects: list[str] | None = None,
    approvals: bool | None = None,
    thread: uuid.UUID | None = None,
    task: uuid.UUID | None = None,
    routine: uuid.UUID | None = None,
    snoozed: bool | None = None,
    cursor: str | None = None,
) -> paging.Page[Decision]:
    """`projects`: only decisions about these (the client/project switcher). `approvals`: only
    actions (True) or only questions (False). `thread`, `task`, `routine`: only those that
    belong to it, directly or through its tasks. `snoozed`: only snoozed ones (True) or only
    the rest (False)."""
    expire_stale(session)
    query = select(Decision)
    if snoozed is not None:
        query = query.where(not_(awake()) if snoozed else awake())
    if approvals is not None:
        query = query.where(_approvals() if approvals else not_(_approvals()))
    if thread is not None:
        theirs = select(Task.id).where(Task.thread_id == thread)
        query = query.where(or_(Decision.thread_id == thread, Decision.task_id.in_(theirs)))
    if task is not None:
        query = query.where(Decision.task_id == task)
    if routine is not None:
        ran = select(RoutineRun.task_id).where(RoutineRun.routine_id == routine)
        query = query.where(Decision.task_id.in_(ran))
    if projects is not None:
        query = query.where(Decision.project.in_(projects))
    if status is not None:
        query = query.where(Decision.status == status)
    if kind is not None:
        query = query.where(Decision.kind == kind)
    if project is not None:
        query = query.where(Decision.project == project)
    return paging.fetch(
        session, query, Decision.created_at, Decision.id, limit=limit, cursor=cursor
    )


def is_approval(decision: Decision) -> bool:
    """An action waiting for the owner's OK: a held call, a plan, more money for a task.
    The rest ("which project?", a worker's question) are questions, asked in the
    conversation, never in Approvals, and not counted in its badge."""
    if decision.kind in (DecisionKind.APPROVE, DecisionKind.PLAN):
        return True
    payload = decision.payload or {}
    asks_money = bool(payload.get("budget") or payload.get("client_cap"))
    return decision.kind is DecisionKind.QUESTION and asks_money


def quick_approval(decision: Decision) -> bool:
    """A held call safe to approve from a notification, without seeing its details: not
    a production change, and not one that needs the connection's name typed."""
    return (
        decision.kind is DecisionKind.APPROVE
        and not production_hold(decision)
        and not (decision.payload or {}).get("destructive")
    )


def options(decision: Decision) -> list[str]:
    """The choices a decision offers (empty: it takes free text)."""
    if decision.kind in (DecisionKind.APPROVE, DecisionKind.PLAN):
        return ["approve", "deny"]
    if decision.kind is DecisionKind.ROUTE:
        candidates = (decision.payload or {}).get("candidates", [])
        return [c["project"] for c in candidates] + [NO_PROJECT]
    return []


def pressed(decision: Decision, choice: str) -> str:
    """What a chat button says back once pressed: short, in plain words. The message itself
    then shows how it ended."""
    if choice == "approve":
        return "Approved."
    if choice == "deny":
        return "Denied."
    if choice == NO_PROJECT:
        return "OK, nothing was started."
    if decision.kind is DecisionKind.ROUTE:
        return f"OK: {chosen(decision, choice)}."
    return "Answered."


def chosen(decision: Decision, option: str) -> str:
    """A choice as people read it: a project by its title."""
    if decision.kind is DecisionKind.ROUTE and option != NO_PROJECT:
        return titles.of(object_session(decision), option)
    return option


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
        raise RuleViolationError(
            f"This was already {decision.status}, maybe from Telegram or another window. "
            "Refresh to see it."
        )
    if decision.expires_at <= datetime.now(UTC):
        raise RuleViolationError("This ran out of time, so it can't be answered. Ask again.")
    if decision.answer is not None:
        raise RuleViolationError("This was already approved. It's running now.")


CANT_CONTINUE = "This task can't continue from here. Start a new task with the same request."


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
        raise RuleViolationError("This question's task is gone, so there's nothing to answer.")
    task = tasks.get_task(session, str(decision.task_id), lock=True)
    if task.status not in (TaskStatus.NEEDS_INPUT, TaskStatus.WAITING):
        raise RuleViolationError(
            f"That task is already {task_status(task.status)}, so this answer isn't needed."
        )
    return task


def _choice(answer: Answer, allowed: list[str]) -> str:
    choice = (answer.choice or "").strip().lower()
    if choice not in allowed:
        raise RuleViolationError(f"Choose one of: {', '.join(allowed)}.")
    return choice


def _answer_approve(decision: Decision, answer: Answer, by: str, via: Via) -> bool:
    """Returns True if the caller should run the held write now (CLI only)."""
    choice = _choice(answer, ["approve", "deny"])
    if choice == "deny":
        _close(decision, DecisionStatus.DENIED, by, via, answer)
        session = object_session(decision)
        if session is not None:
            decisions.notify_settled(session)
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
        raise RuleViolationError(CANT_CONTINUE)
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
        raise RuleViolationError("Type an answer first.")
    answer = Answer(text=reply, voice=answer.voice)
    _close(decision, DecisionStatus.ANSWERED, by, via, answer)
    task = _task_of(session, decision)
    payload = decision.payload or {}
    if payload.get("budget"):
        _answer_cap(session, settings, task, payload, reply, by)
        return
    if payload.get("client_cap"):
        client_month.answer_over_cap(session, settings, task, payload, reply, by)
        return
    if payload.get("escalation"):
        # The architect loop hit its review cap: the answer goes to the architect,
        # and answering grants one more worker round.
        role = SessionRole.ARCHITECT
        task.review_round = min(task.review_round, MAX_REVIEWS - 1)
    else:
        role = SessionRole(payload.get("role", SessionRole.WORKER.value))
    sdk_session = tasks.last_sdk_session(task, role)
    if sdk_session is None:
        raise RuleViolationError(CANT_CONTINUE)
    prompt = (
        f"The user answered your question.\n\nQuestion: {payload.get('question', '')}\n"
        f"Answer: {reply}\n\nContinue the task."
    )
    tasks.add_session(session, settings, task, prompt, role, sdk_session_id=sdk_session)


CONTINUE = ("continue", "yes", "y", "ok", "go on", "more")


def _answer_cap(
    session: Session, settings: Settings, task: Task, payload: dict[str, Any], reply: str, by: str
) -> None:
    """The spending cap question: more money and go on, or stop here."""
    if reply.strip().lower() not in CONTINUE:
        task.status = TaskStatus.STOPPED
        task.finished_at = datetime.now(UTC)
        task.error = f"stopped at its spending cap by {by}"
        return
    task.budget_usd = (task.budget_usd or 0) + float(payload["budget"])
    role = SessionRole(payload.get("role", SessionRole.WORKER.value))
    sdk_session = tasks.last_sdk_session(task, role)
    if sdk_session is None:
        raise RuleViolationError(CANT_CONTINUE)
    prompt = (
        f"You were paused at the spending cap; the user allowed ${payload['budget']:.2f} more. "
        "Continue the task from where you were, and keep it lean."
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
        if decision.thread_id is not None:
            threads.add_reply(session, decision.thread_id, NOT_STARTED)
        return None
    routed: dict[str, Any] = payload.get("task", {})
    kind = TaskKind(routed["kind"]) if routed.get("kind") else None
    message = payload.get("message")
    if message:
        examples.save(session, message, choice, kind=kind, decision_id=decision.id, by=by)
    wanted = str(routed.get("agent") or "")
    agent = (
        session.scalar(
            select(Agent)
            .join(Project, Project.id == Agent.project_id)
            .where(Project.name == choice, Agent.name == slug(wanted))
        )
        if wanted
        else None
    )
    task = tasks.create_task(
        session,
        settings,
        project=choice,
        agent_id=agent.id if agent is not None else None,
        prompt=routed.get("prompt") or message or "",
        kind=kind,
        risk=TaskRisk(routed["risk"]) if routed.get("risk") else None,
        complexity=Complexity(routed["complexity"]) if routed.get("complexity") else None,
        outcome=routed.get("outcome"),
        thread_id=decision.thread_id,
        message_id=decision.message_id,
    )
    if decision.thread_id is not None:  # the thread's last word was the question
        threads.add_reply(session, decision.thread_id, threads.working_on(session, task))
    return task


NOT_STARTED = "OK, I haven't started anything for that."


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
        raise RuleViolationError("This can't be answered here. Refresh the page.")
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


DATA_SERVICES = (
    InstanceType.SUPABASE, InstanceType.NEON, InstanceType.AIRTABLE, InstanceType.ORACLE,
)  # fmt: skip
SENDS = (
    InstanceType.TWILIO, InstanceType.GMAIL, InstanceType.SMTP, InstanceType.RESEND,
    InstanceType.MAILBOX,
)  # fmt: skip
GITHUB_HEADINGS = {
    "push_branch": "A push to GitHub",
    "create_pull": "A pull request on GitHub",
    "merge_pull": "A merge on GitHub",
}
# A Google account's writes that reach other people (the rest change its own data).
GOOGLE_REACHES_PEOPLE = {
    "send_mail": "A message to real people",
    "share_file": "Sharing a file with someone",
    "create_event": "A calendar event (any guests get an invitation)",
}


def production_hold(decision: Decision) -> bool:
    """A held call on a production connection: listed first in Approvals."""
    if decision.kind is not DecisionKind.APPROVE or decision.instance is None:
        return False
    session = object_session(decision)
    if session is None:
        return False
    env = session.scalar(select(Instance.env).where(Instance.name == decision.instance))
    return env is Env.PROD


def _shown(decision: Decision) -> str | None:
    """The connection as people read it: its title, else its name."""
    session = object_session(decision)
    title = (
        session.scalar(select(Instance.title).where(Instance.name == decision.instance))
        if session is not None
        else None
    )
    return title or decision.instance


def _op(decision: Decision) -> str:
    return str((decision.args or {}).get("operation") or "")


def trustable(decision: Decision) -> bool:
    """Whether the card may offer to trust this: a service whose changes always ask can't."""
    args = decision.args or {}
    session = object_session(decision)
    config = (
        session.scalar(select(Instance.config).where(Instance.name == decision.instance))
        if session is not None
        else None
    )
    return not destructive.never_trusted(decision.service, decision.tool, args, config)


def money(decision: Decision) -> dict[str, str] | None:
    """Amount, currency, counterparty and account of a held call that moves money."""
    if decision.kind is not DecisionKind.APPROVE:
        return None
    args = decision.args or {}
    if decision.service is InstanceType.MCP and decision.tool == "call_tool":
        return _mcp_money(decision, args)
    if decision.service is not InstanceType.VENDOR:
        return None
    card = vendor.money_card(args)
    if card is None:
        return None
    names = cast(dict[str, Any], (decision.payload or {}).get("names") or {})
    return vars(vendor.named(card, names))


def declares(decision: Decision) -> str | None:
    """What approving this held call states to the vendor, in the owner's name."""
    if decision.kind is not DecisionKind.APPROVE or decision.service is not InstanceType.VENDOR:
        return None
    return vendor.declaration(decision.args or {})


def _mcp_money(decision: Decision, args: dict[str, Any]) -> dict[str, str] | None:
    """A payments server's call that needs the name typed: what it says, as written. Or a call
    that may spend credits: what it will cost."""
    session = object_session(decision)
    instance = (
        session.scalar(select(Instance).where(Instance.name == decision.instance))
        if session is not None
        else None
    )
    names = cast(dict[str, Any], (decision.payload or {}).get("names") or {})
    estimated = cast(float | None, names.get("credits"))
    if card := mcp.credit_card(instance.config if instance else {}, args, estimated):
        return card
    found = remote.BY_KEY.get(str((instance.config if instance else {}).get("remote") or ""))
    if found is None or found.guard != "payments":
        return None
    if (
        mcp.call_rule(instance.config if instance else {}, str(args.get("tool")), None).level
        != mcp.CONFIRM
    ):
        return None
    given = cast(dict[str, Any], args.get("arguments") or {})
    return vars(vendor.card_of(Money(minor_units=found.minor_units), given))


def heading(decision: Decision) -> str | None:  # noqa: PLR0911 (one per kind)
    """What a held call is, in a few words: live client data only when it is."""
    if decision.kind is not DecisionKind.APPROVE:
        return None
    if decision.tool == "revert_change":
        return "Undoing a change"
    if decision.service in SENDS:
        return "A message to real people"
    if decision.service is InstanceType.VENDOR and (found := vendor.operation(_op(decision))):
        return found[1].says
    if decision.service is InstanceType.GOOGLE and decision.tool in GOOGLE_REACHES_PEOPLE:
        return GOOGLE_REACHES_PEOPLE[decision.tool]
    if decision.service is InstanceType.GITHUB and decision.tool in GITHUB_HEADINGS:
        return GITHUB_HEADINGS[decision.tool]
    session = object_session(decision)
    env = (
        session.scalar(select(Instance.env).where(Instance.name == decision.instance))
        if session is not None
        else None
    )
    if decision.service in DATA_SERVICES and env is Env.PROD:
        return "A change to live client data"
    return f"{action(decision.service, decision.tool, decision.args)} on {_shown(decision)}"


def summary(decision: Decision, limit: int = 160) -> str:
    """A short, scrubbed description: never SQL, arguments, diffs or plan text.

    This is all Telegram ever gets; full details stay in the CLI and API.
    """
    payload = decision.payload or {}
    match decision.kind:
        case DecisionKind.APPROVE:
            what = action(decision.service, decision.tool, decision.args)
            if decision.tool == "revert_change":
                what = "Undoing a change"
            text = f"{what} on {_shown(decision)}: waits for your OK"
        case DecisionKind.ROUTE if payload.get("production"):
            text = f"It would use {payload['production']}, a production connection."
        case DecisionKind.ROUTE:
            candidates = payload.get("candidates", [])
            names = " or ".join(chosen(decision, c["project"]) for c in candidates)
            text = str(payload.get("reason") or f"It could be for {names}.")
        case DecisionKind.PLAN:
            text = f"Plan ready: {payload.get('title') or 'review the plan'}"
        case DecisionKind.QUESTION:
            text = str(payload.get("question", ""))
    return _one_line(scrub(text), limit)
