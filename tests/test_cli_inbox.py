"""`ringkeeper ask` and `ringkeeper inbox`, plus the messages and decisions API."""

import threading
import time
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from typer.testing import CliRunner

from ringkeeper.api.app import create_app
from ringkeeper.cli import inbox as inbox_cli
from ringkeeper.cli.main import app
from ringkeeper.config import get_settings
from ringkeeper.db import session_scope
from ringkeeper.models import (
    AuditEvent,
    AuditOutcome,
    Decision,
    DecisionKind,
    DecisionStatus,
    InstanceType,
    Task,
    TaskStatus,
)
from ringkeeper.router.service import route_next
from ringkeeper.services import inbox, tasks
from tests.fake_router import FakeModel, output, routed

pytestmark = pytest.mark.usefixtures("task_registry")
runner = CliRunner()


def invoke(*args: str) -> str:
    result = runner.invoke(app, list(args))
    assert result.exit_code == 0, result.output
    return result.stdout


def route_soon(model: FakeModel) -> threading.Thread:
    def work() -> None:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if route_next(get_settings(), model) is not None:
                return
            time.sleep(0.1)

    thread = threading.Thread(target=work, daemon=True)
    thread.start()
    return thread


def test_ask_waits_for_the_router_and_prints_its_reply(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(inbox_cli, "POLL_SECONDS", 0.05)
    worker = route_soon(FakeModel([output(routed("acme-web"), reply="On it.")]))
    stdout = invoke("ask", "count acme customers")
    worker.join()
    assert stdout.startswith("thread ")
    assert "On it." in stdout
    assert "→ acme-web: task" in stdout


def test_ask_without_a_router_times_out_but_keeps_the_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(inbox_cli, "POLL_SECONDS", 0.05)
    result = runner.invoke(app, ["ask", "hello", "--timeout", "0.2"])
    assert result.exit_code == 1
    assert "RINGKEEPER_ROUTER=1" in result.output
    assert "thread " in invoke("ask", "again", "--no-wait")


def question(task_id: str | None = None) -> str:
    with session_scope() as session:
        if task_id is None:
            task = tasks.create_task(session, get_settings(), project="acme-web", prompt="go")
            task.sessions[0].sdk_session_id = "sdk-1"
            task.status = task.sessions[0].status = TaskStatus.NEEDS_INPUT
        else:
            task = tasks.get_task(session, task_id)
        decision = inbox.create(
            session, get_settings(), DecisionKind.QUESTION,
            {"question": "Which table has plans?", "role": "worker"},
            project="acme-web", task=task,
        )  # fmt: skip
        return str(decision.id)


def test_inbox_list_show_and_answer_a_question() -> None:
    decision_id = question()
    listed = invoke("inbox", "list")
    assert decision_id[:8] in listed
    assert "Which table has plans?" in listed
    shown = invoke("inbox", "show", decision_id[:8])
    assert "question:  Which table has plans?" in shown
    assert "options:   free text" in shown

    answered = invoke("inbox", "answer", decision_id[:8], "the plans table", "--by", "bob")
    assert f"answered decision {decision_id} (answered)" in answered
    assert "inbox is empty" in invoke("inbox", "list")
    with session_scope() as session:
        event = session.scalars(
            select(AuditEvent).where(AuditEvent.outcome == AuditOutcome.ANSWERED)
        ).one()
        assert event.args == {"via": "cli", "by": "bob", "kind": "question"}


def test_inbox_answer_route_queues_the_task() -> None:
    with session_scope() as session:
        decision = inbox.create(
            session, get_settings(), DecisionKind.ROUTE,
            {
                "message": "tidy up",
                "candidates": [{"project": "acme-web", "confidence": 0.5}],
                "task": {"prompt": "Tidy up.", "kind": "content", "risk": "write",
                         "complexity": "low", "outcome": "tidy"},
            },
        )  # fmt: skip
        decision_id = str(decision.id)
    out = invoke("inbox", "answer", decision_id, "acme-web")
    assert "queued task " in out
    bad = runner.invoke(app, ["inbox", "answer", decision_id, "acme-web"])
    assert bad.exit_code == 1
    assert "already answered" in bad.output


# --- API ---


@pytest.fixture
def client(data_dir: Path) -> Iterator[TestClient]:
    token = invoke("auth", "api-token").strip()
    with TestClient(create_app(), headers={"Authorization": f"Bearer {token}"}) as client:
        yield client


def test_api_posts_messages_and_reads_threads(client: TestClient) -> None:
    accepted = client.post("/messages", json={"body": "count globex customers"})
    assert accepted.status_code == 202
    thread_id = accepted.json()["thread_id"]
    route_next(get_settings(), FakeModel([output(routed("acme-web"))]))
    thread = client.get(f"/threads/{thread_id}").json()
    assert [m["role"] for m in thread["messages"]] == ["user", "router"]
    assert thread["messages"][0]["source"] == "api"
    assert thread["messages"][0]["routing"]["tasks"]

    follow_up = client.post("/messages", json={"body": "and more", "thread_id": thread_id})
    assert follow_up.json()["thread_id"] == thread_id
    assert client.post("/messages", json={"body": "  "}).status_code == 422
    assert client.get("/threads/00000000-0000-0000-0000-000000000000").status_code == 404


def test_api_lists_and_answers_decisions(client: TestClient) -> None:
    decision_id = question()
    listed = client.get("/decisions").json()
    assert [d["id"] for d in listed] == [decision_id]
    assert listed[0]["summary"] == "Which table has plans?"
    assert listed[0]["options"] == []
    assert client.get("/decisions", params={"kind": "route"}).json() == []

    answered = client.post(f"/decisions/{decision_id}/answer", json={"text": "plans", "by": "eve"})
    assert answered.status_code == 200
    body = answered.json()
    assert (body["status"], body["answered_via"], body["decided_by"]) == (
        "answered", "api", "api:eve",
    )  # fmt: skip
    again = client.post(f"/decisions/{decision_id}/answer", json={"text": "plans"})
    assert again.status_code == 409


def test_api_approval_of_a_held_write_is_left_for_the_gateway(client: TestClient) -> None:
    with session_scope() as session:
        held = Decision(
            kind=DecisionKind.APPROVE, project="acme-web", session_id="s", token_id="t",
            instance="acme-supabase-prod", service=InstanceType.SUPABASE, tool="execute",
            args={"sql": "DELETE FROM notes"}, args_hash="h",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )  # fmt: skip
        session.add(held)
        session.flush()
        held_id = str(held.id)
    detail = client.get(f"/decisions/{held_id}").json()
    assert detail["args"] == {"sql": "DELETE FROM notes"}  # full details stay in the API
    assert "DELETE" not in detail["summary"]
    body = client.post(f"/decisions/{held_id}/answer", json={"choice": "approve"}).json()
    assert body["status"] == "pending"
    assert body["answer"] == {"choice": "approve", "by": "api:api"}
    with session_scope() as session:
        assert session.scalars(select(Task)).all() == []
        stored = session.get(Decision, held_id)
        assert stored is not None
        assert stored.status is DecisionStatus.PENDING
