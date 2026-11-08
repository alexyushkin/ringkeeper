"""1.5 conveniences: a request sent for later (waits, can be canceled, dropped if long
late), snoozing an approval (out of Approvals, its badge and notifications, then back and
notified again), and a plan's card (the model, and what tasks like it cost)."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from ringkeeper.config import get_settings
from ringkeeper.db import session_scope
from ringkeeper.errors import RuleViolationError
from ringkeeper.models import (
    ArchitectPhase,
    AuditEvent,
    Complexity,
    Decision,
    DecisionKind,
    DecisionStatus,
    InstanceType,
    Message,
    MessageRole,
    Task,
    TaskStatus,
)
from ringkeeper.router.service import route_next
from ringkeeper.services import inbox, plan_cost, tasks, threads
from tests import test_web_api
from tests.fake_router import FakeModel, output, routed
from tests.helpers import make_project

owner, admin, setup_code = test_web_api.owner, test_web_api.admin, test_web_api.setup_code

SITE = "gi-site"


@pytest.fixture(autouse=True)
def project(database_url: str) -> None:
    del database_url
    with session_scope() as session:
        make_project(session, SITE, owner="general-informatics", description="The website")


def later(minutes: float) -> datetime:
    return datetime.now(UTC) + timedelta(minutes=minutes)


def send(body: str, run_at: datetime | None = None) -> uuid.UUID:
    with session_scope() as session:
        return threads.post_message(session, body, author="owner", source="web", run_at=run_at).id


def message(ident: uuid.UUID) -> tuple[datetime | None, datetime | None, datetime | None]:
    """(routed_at, canceled_at, run_at)"""
    with session_scope(commit=False) as session:
        found = session.get_one(Message, ident)
        return found.routed_at, found.canceled_at, found.run_at


def replies(ident: uuid.UUID) -> list[str]:
    with session_scope(commit=False) as session:
        thread = session.get_one(Message, ident).thread_id
        return [
            m.body
            for m in threads.thread_messages(session, thread)
            if m.role is not MessageRole.USER
        ]


# --- A request sent for later ---


def test_a_request_sent_for_later_waits_for_its_time() -> None:
    ident = send("Send the Friday report", later(60))
    model = FakeModel([output(routed(SITE))])
    assert route_next(get_settings(), model) is None  # not yet
    assert model.calls == []
    with session_scope() as session:
        assert [m.id for m in threads.scheduled(session)] == [ident]
        session.get_one(Message, ident).run_at = datetime.now(UTC) - timedelta(seconds=5)
    assert route_next(get_settings(), model) == ident
    assert message(ident)[0] is not None
    with session_scope(commit=False) as session:
        assert threads.scheduled(session) == []


def test_a_request_sent_for_later_can_be_canceled_once_and_then_never_runs() -> None:
    ident = send("Send the Friday report", later(60))
    with session_scope() as session:
        threads.cancel(session, ident)
    assert message(ident)[1] is not None
    assert replies(ident) == [threads.CANCELED]
    with session_scope() as session, pytest.raises(RuleViolationError, match="already canceled"):
        threads.cancel(session, ident)
    with session_scope() as session:
        session.get_one(Message, ident).run_at = datetime.now(UTC) - timedelta(seconds=5)
    assert route_next(get_settings(), FakeModel([])) is None


def test_only_a_request_sent_for_later_can_be_canceled_and_not_after_it_ran() -> None:
    now = send("Check the site")
    with session_scope() as session, pytest.raises(RuleViolationError, match="sent for later"):
        threads.cancel(session, now)
    ident = send("Check the site", later(60))
    with session_scope() as session:
        session.get_one(Message, ident).run_at = datetime.now(UTC) - timedelta(seconds=5)
    route_next(get_settings(), FakeModel([output(reply="It's up.")]))
    route_next(get_settings(), FakeModel([output(reply="It's up.")]))
    with session_scope() as session, pytest.raises(RuleViolationError, match="already ran"):
        threads.cancel(session, ident)


def test_one_long_past_its_time_is_dropped_and_says_so() -> None:
    ident = send("Send the Friday report", later(60))
    with session_scope() as session:
        session.get_one(Message, ident).run_at = (
            datetime.now(UTC) - threads.LATE - timedelta(minutes=1)
        )
    model = FakeModel([])
    assert route_next(get_settings(), model) == ident
    assert model.calls == []  # never routed
    routed_at, canceled_at, _ = message(ident)
    assert routed_at is None
    assert canceled_at is not None
    assert "wasn't running then, so it didn't run" in replies(ident)[0]


@pytest.mark.parametrize(
    ("when", "said"),
    [
        (later(0.2), "at least a minute"),
        (later(91 * 24 * 60), "next 90 days"),
        (datetime.now() + timedelta(hours=1), "time zone"),
    ],
)
def test_a_time_too_soon_too_far_or_without_its_zone_is_refused(when: datetime, said: str) -> None:
    with pytest.raises(RuleViolationError, match=said):
        send("Send the Friday report", when)


def test_sent_for_later_through_the_api(owner: TestClient) -> None:
    when = later(120)
    sent = owner.post(
        "/messages", json={"body": "Send the report", "source": "web", "run_at": when.isoformat()}
    )
    assert sent.status_code == 202, sent.text
    [listed] = owner.get("/scheduled").json()
    assert listed["body"] == "Send the report"
    assert datetime.fromisoformat(listed["run_at"]) == pytest.approx(when, abs=timedelta(seconds=1))
    canceled = owner.post(f"/messages/{sent.json()['message_id']}/cancel")
    assert canceled.status_code == 200, canceled.text
    assert canceled.json()["canceled_at"]
    assert owner.get("/scheduled").json() == []
    with session_scope(commit=False) as session:
        assert session.scalars(select(AuditEvent).where(AuditEvent.tool == "request.cancel")).one()


# --- Snoozing an approval ---


def held(expires_in: timedelta = timedelta(hours=24)) -> str:
    with session_scope() as session:
        decision = Decision(
            kind=DecisionKind.APPROVE, project=SITE, session_id="s", token_id="t",
            instance="gi-db", service=InstanceType.SUPABASE, tool="execute",
            args={"sql": "UPDATE pages SET title = 'Home'"}, args_hash="h",
            expires_at=datetime.now(UTC) + expires_in,
        )  # fmt: skip
        session.add(decision)
        session.flush()
        return str(decision.id)


def waiting_ids() -> list[str]:
    with session_scope(commit=False) as session:
        return [inbox.notice_key(d) for d in inbox.list_waiting(session)]


def test_a_snoozed_approval_leaves_approvals_and_comes_back_as_a_new_notice() -> None:
    ident = held()
    assert waiting_ids() == [ident]
    with session_scope() as session:
        inbox.snooze(session, ident, later(60), by="owner")
    assert waiting_ids() == []
    with session_scope(commit=False) as session:
        awake = inbox.page_of_decisions(session, approvals=True, snoozed=False).items
        asleep = inbox.page_of_decisions(session, approvals=True, snoozed=True).items
        assert (awake, [str(d.id) for d in asleep]) == ([], [ident])
    with session_scope() as session:  # its time comes
        session.get_one(Decision, ident).snoozed_until = datetime.now(UTC) - timedelta(seconds=1)
    [back] = waiting_ids()
    assert back.startswith(f"{ident}:")  # not the key the desktop app already notified
    with session_scope() as session:
        assert (
            inbox.snooze(session, ident, None, by="owner").snoozed_until is not None
        )  # not asleep: nothing to wake


def test_waking_one_now_and_what_cannot_be_snoozed() -> None:
    ident = held()
    with session_scope() as session:
        inbox.snooze(session, ident, later(60), by="owner")
    with session_scope() as session:
        assert inbox.snooze(session, ident, None, by="owner").snoozed_until is None
    assert waiting_ids() == [ident]
    soon = held(timedelta(minutes=30))
    with (
        session_scope() as session,
        pytest.raises(RuleViolationError, match="runs out before then"),
    ):
        inbox.snooze(session, soon, later(60), by="owner")
    with session_scope() as session:
        question = inbox.create(
            session,
            get_settings(),
            DecisionKind.QUESTION,
            {"question": "Which page?"},
            project=SITE,
        )
        question_id = str(question.id)
    with session_scope() as session, pytest.raises(RuleViolationError, match="Only an approval"):
        inbox.snooze(session, question_id, later(60), by="owner")
    with session_scope(commit=False) as session:
        actions = session.scalars(
            select(AuditEvent.tool).where(AuditEvent.tool.startswith("decision."))
        ).all()
        assert list(actions) == ["decision.snooze", "decision.wake"]


def test_snooze_through_the_api_and_the_badge_leaves_it_out(owner: TestClient) -> None:
    ident = held()
    snoozed = owner.post(f"/decisions/{ident}/snooze", json={"until": later(90).isoformat()})
    assert snoozed.status_code == 200, snoozed.text
    assert snoozed.json()["snoozed_until"]
    badge = owner.get(
        "/decisions", params={"status": "pending", "category": "approval", "snoozed": False}
    )
    assert (badge.json(), badge.headers.get("x-total-count")) == ([], "0")
    asleep = owner.get(
        "/decisions", params={"status": "pending", "category": "approval", "snoozed": True}
    )
    assert [d["id"] for d in asleep.json()] == [ident]
    woken = owner.post(f"/decisions/{ident}/snooze", json={"until": None})
    assert woken.json()["snoozed_until"] is None


# --- A plan's card ---


def finished_feature(
    cost: float, complexity: Complexity = Complexity.MEDIUM, project: str = SITE
) -> None:
    with session_scope() as session:
        task = tasks.create_task(session, get_settings(), project=project, prompt="build it")
        task.status, task.phase, task.complexity = (
            TaskStatus.SUCCEEDED,
            ArchitectPhase.DONE,
            complexity,
        )
        task.finished_at = datetime.now(UTC)
        task.sessions[0].cost_usd = cost


def test_a_plan_says_its_model_and_what_tasks_like_it_cost() -> None:
    for cost in (0.4, 0.6, 0.8, 1.0, 5.0):
        finished_feature(cost)
    finished_feature(9.0, Complexity.HIGH)  # not like it
    with session_scope() as session:
        task = tasks.create_task(session, get_settings(), project=SITE, prompt="add a page")
        task.complexity = Complexity.MEDIUM
        task.sessions[0].cost_usd = 0.05
        session.flush()
        found = plan_cost.estimate(session, get_settings(), task)
    assert found["model"] == get_settings().model_medium
    assert (found["low"], found["high"], found["based_on"]) == (0.6, 5.0, 5)
    assert found["spent"] == 0.05
    assert "Model: " in plan_cost.said(found)
    assert "$0.60 to $5.00" in plan_cost.said(found)


def test_with_nothing_like_it_the_plan_says_so() -> None:
    with session_scope() as session:
        task = tasks.create_task(session, get_settings(), project=SITE, prompt="add a page")
        task.complexity = Complexity.LOW
        session.flush()
        found = plan_cost.estimate(session, get_settings(), task)
    assert (found["low"], found["high"], found["based_on"]) == (None, None, 0)
    assert "cost" not in plan_cost.said(found)


def test_too_few_in_the_project_counts_every_projects() -> None:
    with session_scope() as session:
        make_project(session, "acme-portal", owner="acme")
    for cost in (1.0, 2.0, 3.0):
        finished_feature(cost, project="acme-portal")
    with session_scope() as session:
        task = tasks.create_task(session, get_settings(), project=SITE, prompt="add a page")
        task.complexity = Complexity.MEDIUM
        session.flush()
        found = plan_cost.estimate(session, get_settings(), task)
    assert found["based_on"] == 3


def test_a_waiting_plan_decision_is_unchanged_by_old_payloads() -> None:
    """A plan made before this release has no estimate; its card still works."""
    with session_scope() as session:
        task = session.scalars(select(Task)).first()
        decision = inbox.create(
            session, get_settings(), DecisionKind.PLAN,
            {"title": "t", "plan": "p", "worker_prompt": "w"}, project=SITE, task=task,
        )  # fmt: skip
        assert decision.status is DecisionStatus.PENDING
