"""Check my setup (services.setup_check): what it sends on this install's own projects and
connections, that nothing it starts can change anything, and one whole check against a
stand-in for the router and workers."""

import threading
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from typer.testing import CliRunner

from ringkeeper.calls import Caller, ToolCall
from ringkeeper.cli.main import app
from ringkeeper.config import get_settings
from ringkeeper.db import session_scope
from ringkeeper.errors import RuleViolationError
from ringkeeper.models import (
    AuditEvent,
    AuditOutcome,
    Client,
    Decision,
    DecisionKind,
    DecisionStatus,
    InstanceType,
    Message,
    MessageRole,
    RoutingExample,
    SessionEvent,
    SessionRole,
    Task,
    TaskStatus,
    Thread,
    WorkerSession,
)
from ringkeeper.services import decisions, inbox, instances, memory, setup_check, tasks, threads
from tests import test_github, test_web_api
from tests.helpers import bind, make_instance, make_project
from tests.test_github import FakeGit, FakeGitHub, call
from tests.test_manager import manager, run_task, task_env

world = test_github.world
owner = test_web_api.owner
admin = test_web_api.admin
setup_code = test_web_api.setup_code


def seeded() -> None:
    with session_scope() as session:
        make_project(session, "acme-web", title="Acme web")
        make_project(session, "acme-shop", title="Acme shop")
        make_project(session, "globex-portal", title="Globex portal", owner="globex")
        make_instance(session, "acme-db", title="Acme DB", secret_ref="file://a/db")
        make_instance(session, "acme-gh", type=InstanceType.GITHUB, secret_ref="file://a/gh")
        make_instance(
            session, "globex-n8n", type=InstanceType.N8N, secret_ref="file://g/n8n",
            owner="globex", base_url="https://n8n.globex.example",
        )  # fmt: skip
        make_instance(
            session, "browser", type=InstanceType.MCP, secret_ref="file://me/browser",
            owner=instances.SHARED_OWNER, shared=True, all_projects=True,
        )  # fmt: skip
        make_instance(session, "acme-sheets", type=InstanceType.GOOGLE, secret_ref="file://a/s")
    with session_scope() as session:  # the client as named, not its slug
        found = session.get(Client, "acme")
        assert found is not None
        found.name = "Acme Inc"
    bind("acme-web", "acme-db", is_default=True)
    bind("acme-web", "acme-gh")
    bind("acme-shop", "acme-db")
    bind("globex-portal", "globex-n8n")


def test_one_read_only_request_per_project_and_linked_connection(database_url: str) -> None:
    del database_url
    seeded()
    with session_scope() as session:
        probes, gaps = setup_check.plan(session)
    connections = [(p.project, p.connection) for p in probes if p.kind == "connection"]
    assert connections == [
        ("acme-shop", "acme-db"),
        ("acme-shop", "browser"),  # linked to every project: checked once, from the first
        ("acme-web", "acme-db"),
        ("acme-web", "acme-gh"),
        ("globex-portal", "globex-n8n"),
    ]
    first = probes[0]
    assert first.request == "List the tables in Acme shop's database"
    assert first.label == "Acme DB in Acme shop"
    assert [p.kind for p in probes[-2:]] == ["setup", "web"]
    # Linked to nothing: a gap, reported and not run.
    assert [(g.connection, g.client) for g in gaps] == [("acme-sheets", "Acme Inc")]


# --- Nothing it starts changes anything ---


def from_a_check(project: str = "acme-web") -> str:
    """A worker session in a conversation Check my setup started."""
    with session_scope() as session:
        place = Thread(title="Check my setup: x", source="web", tag=setup_check.TAG)
        session.add(place)
        session.flush()
        task = tasks.create_task(
            session, get_settings(), project=project, prompt="x", thread_id=place.id
        )
        [worker] = task.sessions  # its first, made with it
        return str(worker.id)


def test_a_write_that_would_run_without_asking_is_held_for_a_check(
    world: tuple[FakeGitHub, FakeGit, dict[str, Path]],
) -> None:
    fake, _, _ = world
    with session_scope() as session:
        found = instances.get_instance(session, "acme-gh")
        found.config = {**found.config, "write_rules": {"create_issue": "run"}}
    # Anywhere else, the owner's "run without asking" lets it through to GitHub.
    with pytest.raises(Exception, match=r"404|no /"):  # the fake has no issues to create
        call("acme-web", "create_issue", "acme-gh", title="Broken link")
    assert any(method == "POST" for _, method, _ in fake.seen)
    fake.seen.clear()
    import asyncio  # noqa: PLC0415

    from ringkeeper.services.calls import run_call  # noqa: PLC0415
    from ringkeeper.vault import get_vault  # noqa: PLC0415

    caller = Caller("acme-web", from_a_check(), "token-1")
    held = asyncio.run(
        run_call(
            ToolCall(caller, InstanceType.GITHUB, "create_issue", "acme-gh", {"title": "x"}),
            get_vault(),
        )
    )
    assert held["status"] == "held_for_approval"
    assert not any(method == "POST" for _, method, _ in fake.seen)


@pytest.mark.usefixtures("task_registry")
def test_a_check_s_worker_can_t_edit_files_run_commands_or_make_a_branch() -> None:
    m, runtime = manager()
    ident = run_task()
    with session_scope() as session:
        place = Thread(title="Check my setup: x", source="web", tag=setup_check.TAG)
        session.add(place)
        session.flush()
        found = session.get(Task, uuid.UUID(ident))
        assert found is not None
        found.thread_id = place.id
    m.tick()
    m.wait_idle()
    config = task_env(runtime.started[0])
    assert set(setup_check.READ_ONLY_TOOLS) <= set(config["disallowed_tools"])  # type: ignore[arg-type]
    assert config["git"] is False


@pytest.mark.usefixtures("task_registry")
def test_a_check_s_worker_keeps_no_notes() -> None:
    with pytest.raises(RuleViolationError, match="only reads"):
        memory.remember(Caller("acme-web", from_a_check(), "t"), "The tables are A and B.")


# --- One whole check, with a stand-in for the router and the workers ---


class StandIn:
    """Does what the router and workers would, quickly: asks which project for the first
    request, holds a write for the GitHub one, asks to turn the web on for the lookup,
    answers the setup question itself, and resumes the earlier task for a follow-up."""

    def __init__(self) -> None:
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.errors: list[str] = []

    def __enter__(self) -> "StandIn":
        self.thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        self.stop.set()
        self.thread.join(10)
        assert not self.errors, self.errors

    def _loop(self) -> None:
        while not self.stop.is_set():
            try:
                self._route()
                self._work()
            except Exception as error:  # said by the test, not lost in a thread
                self.errors.append(repr(error))
                return
            time.sleep(0.05)

    def _route(self) -> None:
        settings = get_settings()
        with session_scope() as session:
            waiting = session.scalars(
                select(Message).where(
                    Message.role == MessageRole.USER,
                    Message.routed_at.is_(None),
                    Message.route_error.is_(None),
                )
            ).all()
            for message in waiting:
                message.routed_at = datetime.now(UTC)
                body = message.body
                earlier = session.scalars(
                    select(Task).where(Task.thread_id == message.thread_id)
                ).first()
                if earlier is not None:  # a follow-up: the same task goes on
                    earlier.status = TaskStatus.QUEUED
                elif body.startswith("List the tables in Acme shop"):
                    candidates = [
                        {"project": p, "confidence": 0.5} for p in ("acme-shop", "acme-web")
                    ]
                    inbox.create(
                        session, settings, DecisionKind.ROUTE,
                        {"message": body, "candidates": candidates, "task": {"prompt": body}},
                        thread_id=message.thread_id, message_id=message.id,
                    )  # fmt: skip
                elif body == setup_check.SETUP_QUESTION:
                    message.routing = {"about_ringkeeper": True, "cost_usd": 0.001}
                    threads.add_reply(session, message.thread_id, "Acme web uses Acme DB.")
                elif body == setup_check.WEB_LOOKUP:
                    ask = {
                        "message": body, "candidates": [{"project": "acme-web", "confidence": 1}],
                        "task": {"prompt": body}, "web": "Acme web", "reason": "web is off",
                    }  # fmt: skip
                    inbox.create(session, settings, DecisionKind.ROUTE, ask,
                                 thread_id=message.thread_id)  # fmt: skip
                else:
                    project = "globex-portal" if "n8n" in body else "acme-web"
                    if "Acme shop" in body:
                        project = "acme-shop"
                    tasks.create_task(
                        session, settings, project=project, prompt=body,
                        thread_id=message.thread_id, message_id=message.id,
                    )  # fmt: skip

    def _work(self) -> None:
        with session_scope() as session:
            for task in session.scalars(select(Task).where(Task.status == TaskStatus.QUEUED)).all():
                worker = WorkerSession(
                    task_id=task.id, ordinal=len(task.sessions) + 1, prompt=task.prompt,
                    status=TaskStatus.SUCCEEDED, role=SessionRole.WORKER, state_volume="v",
                    cost_usd=0.02,
                )  # fmt: skip
                session.add(worker)
                session.flush()
                call = {
                    "_kind": "ToolUseBlock", "id": "t1", "input": {"instance": "acme-db"},
                    "name": "mcp__ringkeeper-database__list_tables",
                }  # fmt: skip
                session.add(
                    SessionEvent(
                        session_id=worker.id, seq=1, type="sdk.assistant",
                        payload={"message": {"content": [call]}},
                    )
                )  # fmt: skip
                if task.prompt.startswith("List the tables in Acme shop") and worker.ordinal == 2:
                    # First a call without the connection's name: refused, then it went on.
                    tried: dict[str, object] = {"_kind": "ToolUseBlock", "id": "t0", "input": {},
                             "name": "mcp__ringkeeper-database__list_tables"}  # fmt: skip
                    back = {"_kind": "ToolResultBlock", "tool_use_id": "t0", "is_error": True}
                    session.add_all([
                        SessionEvent(session_id=worker.id, seq=2, type="sdk.assistant",
                                     payload={"message": {"content": [tried]}}),
                        SessionEvent(session_id=worker.id, seq=3, type="sdk.user",
                                     payload={"message": {"content": [back]}}),
                        AuditEvent(project=task.project, session_id=str(worker.id),
                                   outcome=AuditOutcome.REFUSED, tool="list_tables",
                                   reason="read without instance and no default"),
                    ])  # fmt: skip
                if "pull requests" in task.prompt:  # the agent tried a write: held
                    held = ToolCall(
                        Caller(task.project, str(worker.id), "token-1"), InstanceType.GITHUB,
                        "create_issue", "acme-gh", {"title": "x"},
                    )  # fmt: skip
                    decisions.hold(session, held, "acme-gh")
                    task.status = TaskStatus.WAITING
                else:
                    task.status = TaskStatus.SUCCEEDED
                    task.summary = f"Done: {task.prompt}"


def test_a_whole_check(database_url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    del database_url
    monkeypatch.setattr(setup_check, "POLL_SECONDS", 0.05)
    seeded()
    with session_scope() as session:  # the user's own example for another phrasing stays
        session.add(RoutingExample(normalized="ship it", text="Ship it", project="acme-web"))
    seen: list[tuple[int, int, str | None]] = []

    def said(done: int, total: int, now: str | None) -> None:
        seen.append((done, total, now))

    runner = setup_check.Runner(get_settings(), minutes=0.1, progress=said)
    with session_scope(commit=False) as session:
        probes, gaps = setup_check.plan(session)
    with StandIn():
        outcomes, skipped = runner.run(probes)
    assert not skipped
    by = {o.probe.label: o for o in outcomes}
    shop = by["Acme DB in Acme shop"]
    assert shop.asked == ["which project (acme-shop, acme-web)"]
    assert shop.routed == "Acme shop"
    assert shop.ran == ["list_tables on acme-db"]  # the refused call didn't run
    assert shop.ok  # a refused first try it got past isn't the request's error
    assert shop.notes == ["the gateway refused: read without instance and no default (it went on)"]
    assert shop.result.startswith("Done: List the tables")
    assert shop.cost == 0.02
    github = by["acme-gh in Acme web"]
    assert github.ok
    assert "left for you in Approvals" in github.left[0]
    web = by["A web lookup"]
    assert web.left == ["asked to let Acme web use the web: left for you"]
    # The stand-in never calls the browser: its request worked only if it used it.
    browser = by["browser in Acme shop"]
    assert not browser.ok
    assert browser.notes == ["it didn't use browser"]
    setup = by["A question about your setup"]
    assert setup.routed == "answered as a question about Ringkeeper"
    assert setup.result == "Acme web uses Acme DB."
    follow = outcomes[-1]
    assert follow.probe.kind == "follow-up"
    assert follow.thread == shop.thread  # the same conversation
    assert follow.routed == "Acme shop"
    assert follow.ok
    assert seen[-1][2] is None

    with session_scope() as session:
        # Answering which project taught the router nothing; the user's example is untouched.
        kept = session.scalars(select(RoutingExample)).all()
        assert [(e.text, e.project) for e in kept] == [("Ship it", "acme-web")]
        # The held write and the web question wait for the user.
        pending = session.scalars(
            select(Decision.kind).where(Decision.status == DecisionStatus.PENDING)
        ).all()
        assert sorted(pending) == [DecisionKind.APPROVE, DecisionKind.ROUTE]
        tagged = setup_check.tagged(session)
        assert len(tagged) == len(outcomes) - 1  # the follow-up went into the first one
        assert all(t.title.startswith("Check my setup: ") for t in tagged)

    text = setup_check.render(outcomes, gaps, day="2026-11-11")
    assert (
        '| **Acme DB in Acme shop**: "List the tables in Acme shop\'s database" | Acme shop |'
        in text
    )
    assert "**acme-sheets** (Acme Inc): not linked to any project" in text
    assert "Note: it didn't use browser." in text
    assert "Held: " in text
    assert "(left for you in Approvals)." in text
    assert "ringkeeper scenarios archive --mine" in text

    # One click: every conversation it started, and their tasks, archived; nothing else.
    with session_scope() as session:
        mine = threads.post_message(session, "Hello", author="owner", source="web")
        mine_thread = mine.thread_id
    with session_scope() as session:
        assert setup_check.archive(session, by="owner") == len(tagged)
    with session_scope() as session:
        assert setup_check.tagged(session) == []
        left = session.scalar(
            select(func.count()).select_from(Task).where(Task.archived_at.is_(None))
        )
        assert left == 0
        found = session.get(Thread, mine_thread)
        assert found is not None
        assert found.archived_at is None


def test_it_says_when_the_app_isn_t_picking_requests_up(
    database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    del database_url
    monkeypatch.setattr(setup_check, "POLL_SECONDS", 0.05)
    seeded()
    with session_scope(commit=False) as session:
        probes, _ = setup_check.plan(session)
    runner = setup_check.Runner(get_settings(), minutes=0.02)
    with pytest.raises(setup_check.NotPickedUpError, match="is it running"):
        runner.run(probes)


# --- From the app and the terminal ---


def test_settings_shows_what_it_would_send_and_archives_its_conversations(
    owner: TestClient,
) -> None:
    seeded()
    status = owner.get("/setup-check").json()
    assert status["running"] is False
    assert status["requests"] == 8  # five connections, the setup, the web, the follow-up
    assert status["gaps"] == 1
    assert status["conversations"] == 0
    with session_scope() as session:
        sent = threads.post_message(session, "x", author=setup_check.AUTHOR, source="web")
        found = session.get(Thread, sent.thread_id)
        assert found is not None
        found.tag = setup_check.TAG
    assert owner.get("/setup-check").json()["conversations"] == 1
    assert owner.post("/setup-check/archive").json() == {"archived": 1}
    assert owner.get("/setup-check").json()["conversations"] == 0


def test_the_terminal_archives_them_too(database_url: str) -> None:
    del database_url
    with session_scope() as session:
        sent = threads.post_message(session, "x", author=setup_check.AUTHOR, source="web")
        found = session.get(Thread, sent.thread_id)
        assert found is not None
        found.tag = setup_check.TAG
    result = CliRunner().invoke(app, ["scenarios", "archive", "--mine"])
    assert result.exit_code == 0, result.output
    assert "Archived 1 conversation." in result.output
