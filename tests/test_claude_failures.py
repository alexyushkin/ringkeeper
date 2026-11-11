"""Failures on Claude's side: recognized, said plainly, and handled. A usage limit pauses new
work until it resets; overload, rate limits and the network are tried again; a login says
where to sign in; a routing message that failed on Claude's side waits and is routed again,
and any other routing failure gets a Try again button."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from ringkeeper import claude_failures
from ringkeeper.claude_failures import Kind, classify
from ringkeeper.config import get_settings
from ringkeeper.db import session_scope
from ringkeeper.models import Message, MessageRole, Task
from ringkeeper.router.model import AgentSdkModel, ClaudeUnavailableError, Completion
from ringkeeper.router.service import BACK, GAVE_UP, route_next
from ringkeeper.services import claude_place, threads
from ringkeeper.vault import get_vault
from ringkeeper.workers.manager import Collected, claude_failure
from tests import test_web_api
from tests.fake_router import FakeModel, output, routed
from tests.helpers import make_project

setup_code, admin, owner = test_web_api.setup_code, test_web_api.admin, test_web_api.owner
NOON = datetime(2026, 11, 10, 17, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("text", "status", "error", "kind"),
    [
        ("Claude AI usage limit reached|1731272400", None, None, Kind.USAGE_LIMIT),
        ("You've hit your limit · resets 3pm", None, None, Kind.USAGE_LIMIT),
        ("Credit balance is too low", None, "billing_error", Kind.USAGE_LIMIT),
        ("Invalid API key · Please run /login", None, None, Kind.LOGIN),
        ("OAuth token has expired", 401, "authentication_failed", Kind.LOGIN),
        ("API Error: 429 rate_limit_error", 429, "rate_limit", Kind.RATE_LIMIT),
        ('API Error: 529 {"type":"overloaded_error"}', 529, "server_error", Kind.OVERLOADED),
        ("API Error: Connection error.", None, None, Kind.NETWORK),
        ("API Error (Connection error.) · Retrying in 1 seconds", None, None, Kind.NETWORK),
    ],
)
def test_each_failure_is_recognized(
    text: str, status: int | None, error: str | None, kind: Kind
) -> None:
    found = classify(text, status=status, error=error, now=NOON)
    assert found is not None
    assert found.kind is kind
    assert found.transient is (kind in (Kind.RATE_LIMIT, Kind.OVERLOADED, Kind.NETWORK))


def test_other_failures_are_left_alone() -> None:
    assert classify("the task ended with an error", now=NOON) is None


@pytest.mark.parametrize(
    "text",
    [
        "I couldn't finish: the Airtable rate limit reached its cap for this base.",
        "n8n returned Internal Server Error when I activated the workflow.",
        "Airtable rate limit reached",
        "The API says: usage limit reached for this key; too many requests.",
        "Connection error from the client's database. Check its address.",
    ],
)
def test_a_task_s_own_words_are_never_claude_s_failure(text: str) -> None:
    """What a task says about another service, in the model's text or the worker's errors,
    isn't Claude's own limit or outage."""
    failed = Collected(
        errors=["n8n returned Internal Server Error", "Airtable rate limit reached"],
        result={"subtype": "error_during_execution", "is_error": True, "result": text},
        exit_code=1,
    )
    assert claude_failure(failed) is None


def test_claude_s_own_signals_still_count() -> None:
    by_type = Collected(
        errors=[], result={"is_error": True, "result": ""}, claude_error="rate_limit"
    )
    assert (found := claude_failure(by_type)) is not None
    assert found.kind is Kind.RATE_LIMIT
    by_status = Collected(errors=[], result={"is_error": True, "api_error_status": 529})
    assert (found := claude_failure(by_status)) is not None
    assert found.kind is Kind.OVERLOADED
    by_words = Collected(
        errors=[], result={"is_error": True, "errors": ["Claude AI usage limit reached|1731272400"]}
    )
    assert (found := claude_failure(by_words)) is not None
    assert found.kind is Kind.USAGE_LIMIT


def test_a_usage_limit_says_when_it_resets() -> None:
    found = classify("Claude AI usage limit reached|1731272400", now=NOON)
    assert found is not None
    assert found.resets_at == datetime.fromtimestamp(1731272400, UTC)
    assert found.message.startswith("Your Claude plan's limit is reached; it resets at ")
    assert found.message.endswith(". Tasks will wait until then.")
    clock = classify("You've hit your limit · resets 3pm", now=NOON)
    assert clock is not None
    assert clock.resets_at is not None
    assert clock.resets_at.astimezone().hour == 15


class Failing:
    """A router model call that fails on Claude's side `times` times, then answers."""

    def __init__(self, text: str, times: int, then: str = "{}") -> None:
        self.text, self.times, self.then, self.calls = text, times, then, 0

    def __call__(self) -> Completion:
        self.calls += 1
        if self.calls <= self.times:
            found = classify(self.text)
            assert found is not None
            raise ClaudeUnavailableError(found)
        return Completion(self.then)


def test_the_router_tries_transient_failures_again(database_url: str) -> None:
    waits: list[float] = []
    model = AgentSdkModel(get_vault(), get_settings(), sleep=waits.append)
    call = Failing("API Error: 529 overloaded", times=2)
    assert model.with_retries(call).text == "{}"
    assert waits == list(claude_failures.RETRY_WAITS[:2])  # longer each time
    login = Failing("Invalid API key · Please run /login", times=1)
    with pytest.raises(ClaudeUnavailableError, match="Sign in again"):
        model.with_retries(login)
    assert login.calls == 1  # not retried


class Unavailable(FakeModel):
    def __init__(self, text: str) -> None:
        super().__init__([])
        self.text = text

    def complete(self, system: str, prompt: str, model: str) -> Any:
        found = classify(self.text)
        assert found is not None
        raise ClaudeUnavailableError(found)


@pytest.fixture
def project(database_url: str) -> None:
    with session_scope() as session:
        make_project(session, "gi-site", owner="General Informatics LLC")


def ask(body: str) -> str:
    with session_scope() as session:
        return str(threads.post_message(session, body, author="owner", source="web").id)


def bodies() -> list[tuple[MessageRole, str]]:
    with session_scope() as session:
        rows = session.scalars(select(Message).order_by(Message.created_at)).all()
        return [(m.role, m.body) for m in rows]


def test_a_routing_message_waits_for_claude_and_is_routed_later(project: None) -> None:
    ident = ask("Fix the footer")
    route_next(get_settings(), Unavailable("API Error: 529 overloaded"))
    with session_scope() as session:
        message = session.get_one(Message, uuid.UUID(ident))
        assert message.routed_at is None
        assert message.route_error is None  # not failed for good
        assert "retry_after" in (message.routing or {})
    assert bodies()[-1] == (
        MessageRole.SYSTEM,
        "Claude is overloaded right now. Ringkeeper tries again in a moment.",
    )
    assert route_next(get_settings(), FakeModel([])) is None  # not before its time
    with session_scope() as session:  # its time comes
        message = session.get_one(Message, uuid.UUID(ident))
        message.routing = {"retry_after": (datetime.now(UTC) - timedelta(seconds=1)).isoformat()}
    route_next(get_settings(), FakeModel([output(routed("gi-site"))]))
    with session_scope() as session:
        assert [t.project for t in session.scalars(select(Task))] == ["gi-site"]


def due(ident: str, **routing: Any) -> dict[str, Any]:
    """Its next try is due now; returns what it knew about waiting before."""
    with session_scope() as session:
        message = session.get_one(Message, uuid.UUID(ident))
        before = dict(message.routing or {})
        message.routing = {
            **before, **routing,
            "retry_after": (datetime.now(UTC) - timedelta(seconds=1)).isoformat(),
        }  # fmt: skip
        return before


OVERLOADED = "Claude is overloaded right now. Ringkeeper tries again in a moment."


def test_three_failed_tries_tell_the_conversation_once(project: None) -> None:
    ident = ask("Fix the footer")
    waits: list[float] = []
    for _ in range(3):
        start = datetime.now(UTC)
        route_next(get_settings(), Unavailable("API Error: 529 overloaded"))
        before = due(ident)
        waits.append((datetime.fromisoformat(before["retry_after"]) - start).total_seconds() / 60)
    assert [round(w) for w in waits] == [2, 5, 15]  # backed off, not every 2 minutes
    assert bodies().count((MessageRole.SYSTEM, OVERLOADED)) == 1
    route_next(get_settings(), FakeModel([output(routed("gi-site"))]))
    assert bodies().count((MessageRole.SYSTEM, BACK)) == 1  # and once more when it's back
    with session_scope() as session:
        assert [t.project for t in session.scalars(select(Task))] == ["gi-site"]


def test_later_tries_wait_half_an_hour(project: None) -> None:
    ident = ask("Fix the footer")
    route_next(get_settings(), Unavailable("API Error: 529 overloaded"))
    due(ident, tries=7)
    start = datetime.now(UTC)
    route_next(get_settings(), Unavailable("API Error: 529 overloaded"))
    with session_scope() as session:
        later = (session.get_one(Message, uuid.UUID(ident)).routing or {})["retry_after"]
    assert round((datetime.fromisoformat(later) - start).total_seconds() / 60) == 30


def test_after_twelve_hours_it_gives_up_with_try_again(project: None) -> None:
    ident = ask("Fix the footer")
    route_next(get_settings(), Unavailable("API Error: 529 overloaded"))
    due(ident, since=(datetime.now(UTC) - timedelta(hours=13)).isoformat())
    route_next(get_settings(), Unavailable("API Error: 529 overloaded"))
    with session_scope() as session:
        message = session.get_one(Message, uuid.UUID(ident))
        assert message.route_error == OVERLOADED  # given up: no more tries by itself
        last = threads.thread_messages(session, message.thread_id)[-1]
        assert (last.body, (last.routing or {}).get("retry")) == (GAVE_UP, ident)
    assert bodies().count((MessageRole.SYSTEM, OVERLOADED)) == 1


def test_a_usage_limit_pauses_routing_until_it_resets(project: None, owner: TestClient) -> None:
    ask("Fix the footer")
    route_next(get_settings(), Unavailable("Claude AI usage limit reached|4102444800"))  # 2100
    status = owner.get("/claude-status").json()
    assert status["waiting_until"] is not None
    assert status["message"].startswith("Your Claude plan's limit is reached; it resets at")
    ask("Another request")
    assert route_next(get_settings(), FakeModel([])) is None  # nothing is taken meanwhile


def a_pause() -> None:
    failure = classify("Claude AI usage limit reached|4102444800")  # 2100
    assert failure is not None
    with session_scope() as session:
        claude_failures.pause_until(session, failure)


def test_try_now_stops_waiting_for_the_reset(project: None, owner: TestClient) -> None:
    a_pause()
    assert owner.get("/claude-status").json()["waiting_until"] is not None
    done = owner.post("/claude-status/try-now")
    assert done.status_code == 200, done.text
    assert done.json() == {"waiting_until": None, "message": None}
    ask("Fix the footer")
    route_next(get_settings(), FakeModel([output(routed("gi-site"))]))  # routed again now
    with session_scope() as session:
        assert [t.project for t in session.scalars(select(Task))] == ["gi-site"]


def test_a_new_place_for_claude_stops_waiting(project: None) -> None:
    a_pause()
    with session_scope() as session:
        claude_place.set_project(session, "gi-site", None)
        assert claude_failures.paused(session) is None


def test_another_routing_failure_offers_try_again(project: None, owner: TestClient) -> None:
    ident = ask("Fix the footer")

    class Broken(FakeModel):
        def complete(self, system: str, prompt: str, model: str) -> Any:
            raise RuntimeError("something else broke")

    route_next(get_settings(), Broken([]))
    with session_scope() as session:
        reply = session.scalars(select(Message).where(Message.role == MessageRole.SYSTEM)).one()
        assert (reply.routing or {})["retry"] == ident
    again = owner.post(f"/messages/{ident}/retry")
    assert again.status_code == 200, again.text
    route_next(get_settings(), FakeModel([output(routed("gi-site"))]))
    with session_scope() as session:
        assert [t.project for t in session.scalars(select(Task))] == ["gi-site"]
