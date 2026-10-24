"""The inbox service: answering each decision kind, and what that sets in motion."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from ringkeeper.config import get_settings
from ringkeeper.db import session_scope
from ringkeeper.errors import NotFoundError, RuleViolationError
from ringkeeper.models import (
    ArchitectPhase,
    AuditEvent,
    AuditOutcome,
    Decision,
    DecisionKind,
    DecisionStatus,
    InstanceType,
    RoutingExample,
    SessionRole,
    Task,
    TaskKind,
    TaskStatus,
)
from ringkeeper.services import examples, inbox, tasks, threads
from ringkeeper.services.inbox import Answer, Via

pytestmark = pytest.mark.usefixtures("task_registry")


def task_waiting(status: TaskStatus = TaskStatus.NEEDS_INPUT, kind: TaskKind | None = None) -> str:
    """A task whose first session ran (sdk-1) and now waits on the user."""
    with session_scope() as session:
        task = tasks.create_task(
            session, get_settings(), project="acme-web", prompt="do it", kind=kind
        )
        first = task.sessions[0]
        first.sdk_session_id = "sdk-1"
        first.status = status
        task.status = status
        return str(task.id)


def decide(kind: DecisionKind, payload: dict[str, object], task_id: str | None = None) -> str:
    with session_scope() as session:
        task = tasks.get_task(session, task_id) if task_id else None
        decision = inbox.create(session, get_settings(), kind, payload, task=task)
        return str(decision.id)


def answer(ident: str, *, via: Via = Via.API, **reply: str) -> inbox.Answered:
    with session_scope() as session:
        result = inbox.answer(session, get_settings(), ident, Answer(**reply), by="alice", via=via)
        session.expunge_all()
        return result


def load[M: (Task, Decision)](model: type[M], ident: str) -> M:
    with session_scope() as session:
        row = session.get(model, ident)
        assert row is not None
        session.expunge(row)
        return row


def audit_answers() -> list[AuditEvent]:
    with session_scope() as session:
        return list(
            session.scalars(
                select(AuditEvent).where(AuditEvent.outcome == AuditOutcome.ANSWERED)
            ).all()
        )


def test_question_answer_resumes_the_same_sdk_session() -> None:
    task_id = task_waiting()
    decision_id = decide(DecisionKind.QUESTION, {"question": "Which table?"}, task_id)
    answer(decision_id, text="customers", via=Via.TELEGRAM)

    decision = load(Decision, decision_id)
    assert decision.status is DecisionStatus.ANSWERED
    assert decision.answer == {"text": "customers"}
    assert (decision.answered_via, decision.decided_by) == ("telegram", "alice")
    with session_scope() as session:
        task = tasks.get_task(session, task_id)
        assert task.status is TaskStatus.QUEUED
        resumed = tasks.latest_session(task)
        assert (resumed.ordinal, resumed.sdk_session_id) == (2, "sdk-1")
        assert "Which table?" in resumed.prompt
        assert "customers" in resumed.prompt
        assert resumed.model == "claude-sonnet-5-5"
    [event] = audit_answers()
    assert event.args == {"via": "telegram", "by": "alice", "kind": "question"}


def test_a_question_needs_text_and_a_waiting_task() -> None:
    task_id = task_waiting()
    decision_id = decide(DecisionKind.QUESTION, {"question": "?"}, task_id)
    with pytest.raises(RuleViolationError, match="text answer"):
        answer(decision_id, text="  ")
    with session_scope() as session:
        tasks.request_stop(session, task_id)
    # Stopping the task closed its open decisions.
    assert load(Decision, decision_id).status is DecisionStatus.DENIED
    with pytest.raises(RuleViolationError, match="already denied"):
        answer(decision_id, text="late")


def test_a_decision_is_answered_once() -> None:
    decision_id = decide(DecisionKind.QUESTION, {"question": "?"}, task_waiting())
    answer(decision_id, text="yes")
    with pytest.raises(RuleViolationError, match="already answered"):
        answer(decision_id, text="again")


def test_expired_decisions_cannot_be_answered() -> None:
    decision_id = decide(DecisionKind.QUESTION, {"question": "?"}, task_waiting())
    with session_scope() as session:
        decision = session.get(Decision, decision_id)
        assert decision is not None
        decision.created_at = datetime.now(UTC) - timedelta(days=3)
        decision.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    with pytest.raises(RuleViolationError, match="already expired"):
        answer(decision_id, text="too late")


def route_payload(message: str = "fix the thing") -> dict[str, object]:
    return {
        "message": message,
        "candidates": [
            {"project": "acme-web", "confidence": 0.55},
            {"project": "globex-portal", "confidence": 0.4},
        ],
        "task": {
            "prompt": "Fix the thing.",
            "kind": "fix",
            "risk": "write",
            "complexity": "low",
            "outcome": "the thing works",
        },
    }


def test_route_answer_creates_the_task_and_remembers_the_phrasing() -> None:
    decision_id = decide(DecisionKind.ROUTE, route_payload("Fix   the THING"))
    with session_scope() as session:
        assert inbox.options(inbox.list_pending(session)[0]) == [
            "acme-web", "globex-portal", "none",
        ]  # fmt: skip
    result = answer(decision_id, choice="globex-portal", via=Via.CLI)

    assert result.task is not None
    task = load(Task, str(result.task.id))
    assert (task.project, task.kind, task.complexity) == ("globex-portal", TaskKind.FIX, "low")
    assert task.prompt == "Fix the thing."
    with session_scope() as session:
        example = examples.match(session, "fix the thing")
        assert example is not None
        assert (example.project, example.created_by) == ("globex-portal", "alice")
        assert str(example.decision_id) == decision_id
        first = tasks.get_task(session, str(task.id)).sessions[0]
        assert first.model == "claude-haiku-5-5"  # low complexity
    decision = load(Decision, decision_id)
    assert (decision.status, decision.project) == (DecisionStatus.ANSWERED, "globex-portal")


def test_route_answer_none_drops_the_task_and_unknown_projects_are_refused() -> None:
    decision_id = decide(DecisionKind.ROUTE, route_payload())
    with pytest.raises(NotFoundError):
        answer(decision_id, choice="ghost")
    result = answer(decision_id, choice="none")
    assert result.task is None
    with session_scope() as session:
        assert session.scalars(select(RoutingExample)).all() == []
        assert session.scalars(select(Task)).all() == []


def test_examples_can_be_listed_and_deleted() -> None:
    with session_scope() as session:
        saved = examples.save(session, "Count customers", "acme-web")
        examples.save(session, "count  customers", "globex-portal")  # same phrasing: replaced
        listed = examples.list_examples(session)
        assert [(e.text, e.project) for e in listed] == [("count  customers", "globex-portal")]
        examples.delete(session, str(saved.id)[:8])
        assert examples.match(session, "count customers") is None


def test_plan_approval_resumes_the_architect() -> None:
    task_id = task_waiting(TaskStatus.WAITING, TaskKind.FEATURE)
    decision_id = decide(DecisionKind.PLAN, {"plan": "p", "worker_prompt": "w"}, task_id)
    answer(decision_id, choice="approve")

    assert load(Decision, decision_id).status is DecisionStatus.APPROVED
    with session_scope() as session:
        task = tasks.get_task(session, task_id)
        assert (task.status, task.phase) == (TaskStatus.QUEUED, ArchitectPhase.BUILDING)
        resumed = tasks.latest_session(task)
        assert (resumed.role, resumed.sdk_session_id) == (SessionRole.ARCHITECT, "sdk-1")
        assert resumed.model == "claude-opus-5-5"
        assert "dispatch_worker" in resumed.prompt


def test_plan_denial_with_feedback_revises_and_without_stops() -> None:
    task_id = task_waiting(TaskStatus.WAITING, TaskKind.FEATURE)
    first = decide(DecisionKind.PLAN, {"plan": "p"}, task_id)
    answer(first, choice="deny", text="use a dataclass")
    with session_scope() as session:
        task = tasks.get_task(session, task_id)
        assert task.phase is ArchitectPhase.PLANNING
        assert "use a dataclass" in tasks.latest_session(task).prompt
        task.status = TaskStatus.WAITING
    second = decide(DecisionKind.PLAN, {"plan": "p2"}, task_id)
    answer(second, choice="deny")
    stopped = load(Task, task_id)
    assert stopped.status is TaskStatus.STOPPED
    assert stopped.error == "plan denied by alice"


def held_write() -> str:
    with session_scope() as session:
        decision = Decision(
            kind=DecisionKind.APPROVE,
            project="acme-web",
            session_id="s",
            token_id="t",
            instance="acme-supabase-prod",
            service=InstanceType.SUPABASE,
            tool="execute",
            args={"sql": "DELETE FROM notes"},
            args_hash="h",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
        session.add(decision)
        session.flush()
        return str(decision.id)


def test_approving_a_held_write_remotely_leaves_it_for_the_gateway() -> None:
    decision_id = held_write()
    result = answer(decision_id, choice="approve", via=Via.TELEGRAM)
    assert result.run_held_write is False
    decision = load(Decision, decision_id)
    assert decision.status is DecisionStatus.PENDING  # the gateway claims and runs it
    assert decision.answer == {"choice": "approve", "by": "alice"}
    with session_scope() as session:
        assert [str(d.id) for d in inbox.pending_approvals(session)] == [decision_id]


def test_approving_a_held_write_from_the_cli_runs_it_here() -> None:
    assert answer(held_write(), choice="approve", via=Via.CLI).run_held_write is True


def test_denying_a_held_write_closes_it() -> None:
    decision_id = held_write()
    answer(decision_id, choice="deny", text="not today")
    decision = load(Decision, decision_id)
    assert (decision.status, decision.reason) == (DecisionStatus.DENIED, "not today")
    with pytest.raises(RuleViolationError, match="approve, deny"):
        answer(held_write(), choice="maybe")


def test_messages_go_into_threads() -> None:
    with session_scope() as session:
        first = threads.post_message(session, "hello\nworld", author="alice", source="cli")
        thread_id = first.thread_id
        threads.post_message(session, "again", author="alice", source="cli", thread=str(thread_id))
        assert threads.get_thread(session, str(thread_id)).title == "hello"
        assert [m.body for m in threads.thread_messages(session, thread_id)] == [
            "hello\nworld", "again",
        ]  # fmt: skip
        with pytest.raises(RuleViolationError, match="empty"):
            threads.post_message(session, "  ", author="alice", source="cli")
