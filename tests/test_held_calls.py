"""A held call ends the worker's turn; once the user decides, the same session is
resumed with what happened. No retry, no second hold, no question about it.

Service, SQL and MCP holds all go through the same flow, so each is run end to
end: held by the gateway, approved (or denied) in the app, run by the gateway's
approval pass, and the outcome handed back to the agent."""

import json
import sys
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import anyio
import psycopg
import pytest
from sqlalchemy import select

from ringkeeper.calls import Caller, ToolCall
from ringkeeper.config import get_settings
from ringkeeper.db import session_scope
from ringkeeper.gateway import approvals
from ringkeeper.models import (
    Decision,
    DecisionKind,
    DecisionStatus,
    Env,
    InstanceType,
    TaskStatus,
)
from ringkeeper.services import calls, inbox
from ringkeeper.vault import get_vault
from ringkeeper.workers.runtime import LABEL_SESSION, WorkerSpec
from tests.fake_n8n import API_KEY as N8N_KEY
from tests.fake_n8n import FakeN8n
from tests.fake_runtime import Script, line, result_line, workspace_line
from tests.helpers import bind, make_instance
from tests.test_manager import drive, manager, run_task, task

FAKE_MCP = Path(__file__).parent / "fixtures" / "fake_mcp_server.py"

HELD = {
    "service": (InstanceType.N8N, "activate_workflow", "acme-n8n", {"workflow_id": "wf1"}),
    "sql": (
        InstanceType.SUPABASE, "execute", "acme-db",
        {"sql": "INSERT INTO notes (body) VALUES ('held')"},
    ),
    "mcp": (InstanceType.MCP, "call_tool", "notes", {"tool": "what_i_got"}),
}  # fmt: skip


@pytest.fixture
def connections(task_registry: None, fake_n8n: FakeN8n, target_dsn: str) -> Iterator[None]:
    vault = get_vault()
    vault.store("file://acme/n8n", N8N_KEY)
    vault.store("file://acme/db", target_dsn)
    server = {"command": sys.executable, "args": [str(FAKE_MCP)]}
    vault.store("file://acme/notes", json.dumps(server))
    with session_scope() as session:
        make_instance(
            session, "acme-n8n", type=InstanceType.N8N, env=Env.PROD,
            secret_ref="file://acme/n8n", base_url="https://n8n.acme.example",
        )  # fmt: skip
        make_instance(session, "acme-db", env=Env.PROD, secret_ref="file://acme/db")
        make_instance(
            session, "notes", type=InstanceType.MCP, env=Env.DEV, secret_ref="file://acme/notes"
        )
    for name in ("acme-n8n", "acme-db", "notes"):
        bind("acme-web", name)
    with psycopg.connect(target_dsn, autocommit=True) as conn:
        conn.execute("CREATE TABLE notes (id serial PRIMARY KEY, body text)")
    yield
    with psycopg.connect(target_dsn, autocommit=True) as conn:
        conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")


class Worker:
    """The first session makes the held call (and maybe asks about it); later ones don't."""

    def __init__(self, kind: str, *, also_asks: bool = False) -> None:
        self.kind = kind
        self.also_asks = also_asks
        self.answers: list[dict[str, Any]] = []
        self.prompts: list[str] = []

    def script(self, spec: WorkerSpec) -> Script:
        config = json.loads(spec.env["RINGKEEPER_TASK"])
        self.prompts.append(config["prompt"])
        if config["session_ordinal"] > 1:
            return Script()
        service, tool, instance, args = HELD[self.kind]
        caller = Caller("acme-web", spec.labels[LABEL_SESSION], "token-1")
        call = ToolCall(caller, service, tool, instance, args)
        self.answers.append(anyio.run(calls.run_call, call, get_vault()))
        before = [line("ringkeeper.ask_user", question="Approve it?")] if self.also_asks else []
        return Script(before=before, after=[result_line(), workspace_line([])])


def held(task_id: str) -> list[Decision]:
    with session_scope() as session:
        query = select(Decision).where(Decision.task_id == uuid.UUID(task_id))
        return list(session.scalars(query.order_by(Decision.created_at)))


def decide(decision_id: str, choice: str) -> None:
    with session_scope() as session:
        inbox.answer(
            session, get_settings(), decision_id, inbox.Answer(choice=choice),
            by="owner", via=inbox.Via.API,
        )  # fmt: skip
    anyio.run(approvals.run_pending, get_vault())  # what the gateway's approval pass does


@pytest.mark.usefixtures("connections")
@pytest.mark.parametrize("kind", HELD)
def test_an_approved_hold_runs_once_and_resumes_the_same_session(kind: str) -> None:
    worker = Worker(kind)
    m, runtime = manager(worker.script)
    task_id = run_task()
    m.tick()
    m.wait_idle()

    [answer] = worker.answers
    assert answer["status"] == "held_for_approval"
    assert "ringkeeper decision" not in answer["message"]  # no CLI in what the user may see
    assert "don't ask them about it" in answer["message"]
    assert task(task_id).status is TaskStatus.WAITING
    [decision] = held(task_id)
    assert decision.kind is DecisionKind.APPROVE  # one item in Approvals, no question

    m.tick()  # nothing decided yet: still waiting, nothing started
    m.wait_idle()
    assert len(runtime.started) == 1

    decide(str(decision.id), "approve")
    drive(m, lambda: task(task_id).status is TaskStatus.SUCCEEDED)

    [decision] = held(task_id)
    assert decision.status is DecisionStatus.EXECUTED
    assert len(runtime.started) == 2
    resumed = task(task_id).sessions[-1]
    assert resumed.sdk_session_id == "sdk-1"  # the same conversation, resumed
    assert "approved and run. Result:" in worker.prompts[1]
    assert "Don't repeat a call that ran" in worker.prompts[1]


@pytest.mark.usefixtures("connections")
def test_a_denied_hold_resumes_with_the_refusal() -> None:
    worker = Worker("service")
    m, _ = manager(worker.script)
    task_id = run_task()
    m.tick()
    m.wait_idle()
    [decision] = held(task_id)
    decide(str(decision.id), "deny")
    drive(m, lambda: task(task_id).status is TaskStatus.SUCCEEDED)
    assert "'activate_workflow' on acme-n8n: the user said no" in worker.prompts[1]
    [decision] = held(task_id)
    assert decision.status is DecisionStatus.DENIED


@pytest.mark.usefixtures("connections")
def test_a_question_about_a_held_call_is_not_asked_twice() -> None:
    worker = Worker("service", also_asks=True)
    m, _ = manager(worker.script)
    task_id = run_task()
    m.tick()
    m.wait_idle()
    assert task(task_id).status is TaskStatus.WAITING
    assert len(held(task_id)) == 1
    with session_scope() as session:
        questions = inbox.list_pending(session, kind=DecisionKind.QUESTION)
        assert list(questions) == []


@pytest.mark.usefixtures("connections")
def test_a_hold_decided_before_the_turn_ends_resumes_at_once() -> None:
    worker = Worker("service")
    original = worker.script

    def decide_during_the_turn(spec: WorkerSpec) -> Script:
        script = original(spec)
        if worker.answers and len(worker.prompts) == 1:
            decide(worker.answers[0]["decision_id"], "approve")
        return script

    m, _ = manager(decide_during_the_turn)
    task_id = run_task()
    drive(m, lambda: task(task_id).status is TaskStatus.SUCCEEDED)
    assert "approved and run" in worker.prompts[1]


@pytest.mark.usefixtures("connections")
@pytest.mark.parametrize(
    ("kind", "heading"),
    [
        ("sql", "A change to live client data"),  # a production database
        ("mcp", "'what_i_got' on notes"),  # a local tool: no talk of client data
        ("service", "'activate_workflow' on acme-n8n"),
    ],
)
def test_approvals_say_what_a_held_call_is(kind: str, heading: str) -> None:
    worker = Worker(kind)
    m, _ = manager(worker.script)
    task_id = run_task()
    m.tick()
    m.wait_idle()
    with session_scope() as session:
        query = select(Decision).where(Decision.task_id == uuid.UUID(task_id))
        [decision] = session.scalars(query).all()
        assert inbox.heading(decision) == heading
        assert inbox.summary(decision).endswith(": waits for your OK")
