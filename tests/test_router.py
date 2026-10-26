"""The router on a fake model: splitting, thresholds, cross-owner splits,
retries, routing examples and model choice."""

import uuid

import pytest
from sqlalchemy import select
from typer.testing import CliRunner

from ringkeeper.claude_auth import BILLING_ENV_VARS, ClaudeCredential
from ringkeeper.cli.main import app as cli
from ringkeeper.config import get_settings
from ringkeeper.db import session_scope
from ringkeeper.models import (
    ArchitectPhase,
    AuthMode,
    Decision,
    DecisionKind,
    Message,
    MessageRole,
    Project,
    SessionRole,
    Task,
    TaskKind,
)
from ringkeeper.router.model import Completion, sdk_env
from ringkeeper.router.service import Router, route_next
from ringkeeper.services import examples, inbox, threads
from ringkeeper.services.inbox import Answer, Via
from ringkeeper.vault import Secret
from tests.fake_router import FakeModel, output, routed
from tests.helpers import bind, make_instance, make_project


@pytest.fixture(autouse=True)
def projects(database_url: str) -> None:
    with session_scope() as session:
        make_project(
            session, "acme-web", owner="acme", description="Acme's customer portal",
            aliases=["acme"], workspace_path="acme-web",
        )  # fmt: skip
        make_project(session, "acme-reports", owner="acme", description="Acme reporting")
        make_project(session, "globex-portal", owner="globex", description="Globex portal")
        make_instance(session, "globex-supabase-dev", secret_ref="aws-sm://globex/very-secret")
    bind("globex-portal", "globex-supabase-dev", is_default=True)


def ask(body: str, thread: str | None = None) -> str:
    with session_scope() as session:
        message = threads.post_message(session, body, author="alice", source="cli", thread=thread)
        return str(message.thread_id)


def route(model: FakeModel) -> None:
    assert route_next(get_settings(), model) is not None


def all_tasks() -> list[Task]:
    with session_scope() as session:
        rows = list(session.scalars(select(Task).order_by(Task.project)).all())
        for row in rows:
            _ = row.sessions
        session.expunge_all()
        return rows


def route_decisions() -> list[Decision]:
    with session_scope() as session:
        rows = list(
            session.scalars(select(Decision).where(Decision.kind == DecisionKind.ROUTE)).all()
        )
        session.expunge_all()
        return rows


def thread_bodies(thread_id: str) -> list[tuple[MessageRole, str]]:
    with session_scope() as session:
        rows = threads.thread_messages(session, uuid.UUID(thread_id))
        return [(m.role, m.body) for m in rows]


def test_a_message_is_split_into_one_task_per_project() -> None:
    thread_id = ask("Add a plan formatter in acme-web. Also how many customers has globex?")
    model = FakeModel(
        [
            output(
                routed("acme-web", 0.9, kind="feature", complexity="medium"),
                routed("globex-portal", 0.92, kind="data", complexity="low"),
                reply="Two tasks.",
            )
        ]
    )
    route(model)

    feature, data = all_tasks()
    assert (feature.project, feature.kind, feature.phase) == (
        "acme-web", TaskKind.FEATURE, ArchitectPhase.PLANNING,
    )  # fmt: skip
    assert feature.sessions[0].role is SessionRole.ARCHITECT
    assert feature.sessions[0].model == "claude-opus-5-5"
    assert (data.project, data.kind, data.outcome) == (
        "globex-portal", TaskKind.DATA, "done in globex-portal",
    )  # fmt: skip
    assert data.sessions[0].role is SessionRole.WORKER
    assert data.sessions[0].model == "claude-haiku-5-5"  # low complexity
    assert str(data.thread_id) == thread_id
    assert route_decisions() == []

    [(_, _, model_id)] = model.calls
    assert model_id == "claude-haiku-5-5"
    roles = [role for role, _ in thread_bodies(thread_id)]
    assert roles == [MessageRole.USER, MessageRole.ROUTER]
    reply = thread_bodies(thread_id)[1][1]
    assert "Two tasks." in reply
    assert "acme-web" in reply
    assert "globex-portal" in reply


def test_a_small_clear_feature_skips_the_architect() -> None:
    thread_id = ask("Add hello.txt to acme-web")
    route(FakeModel([output(routed("acme-web", 0.95, kind="feature", complexity="low"))]))
    [task] = all_tasks()
    assert (task.kind, task.phase) == (TaskKind.FEATURE, None)
    [first] = task.sessions
    assert (first.role, first.model) == (SessionRole.WORKER, "claude-haiku-5-5")
    assert "worker queued" in thread_bodies(thread_id)[1][1]


def test_the_router_sees_cards_history_tasks_decisions_and_no_secrets() -> None:
    thread_id = ask("first")
    route(FakeModel([output(routed("acme-web"))]))
    ask("second", thread=thread_id)
    model = FakeModel([output()])
    route(model)

    system, prompt, _ = model.calls[0]
    assert "Never give one task to projects with different owners" in system
    assert system.endswith('"title":"RouterOutput","type":"object"}')
    assert "Acme's customer portal" in prompt
    assert "globex-supabase-dev" in prompt
    assert "- user: first" in prompt  # the thread so far
    assert "acme-web [queued]" in prompt  # open tasks
    assert prompt.rstrip().endswith("MESSAGE TO ROUTE:\nsecond")
    assert "very-secret" not in prompt
    assert "aws-sm://" not in prompt


def test_low_confidence_asks_with_the_top_two_candidates() -> None:
    ask("update the report numbers")
    route(
        FakeModel(
            [
                output(
                    routed(
                        "acme-reports", 0.6,
                        alternatives=[
                            {"project": "acme-web", "confidence": 0.3},
                            {"project": "globex-portal", "confidence": 0.45},
                        ],
                    )
                )
            ]
        )
    )  # fmt: skip
    assert all_tasks() == []
    [decision] = route_decisions()
    payload = decision.payload or {}
    assert payload["candidates"] == [
        {"project": "acme-reports", "confidence": 0.6},
        {"project": "globex-portal", "confidence": 0.45},
    ]
    assert payload["message"] == "update the report numbers"
    assert payload["task"]["prompt"] == "Work in acme-reports."
    assert "below 0.80" in payload["reason"]


def test_the_threshold_is_configurable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RINGKEEPER_ROUTER_CONFIDENCE_THRESHOLD", "0.5")
    ask("update the report numbers")
    route(FakeModel([output(routed("acme-reports", 0.6))]))
    assert [t.project for t in all_tasks()] == ["acme-reports"]


def test_a_task_touching_two_projects_of_one_owner_is_asked_about() -> None:
    ask("sync the portal and the reports")
    route(FakeModel([output(routed("acme-web", 0.95, touches=["acme-reports"]))]))
    assert all_tasks() == []
    [decision] = route_decisions()
    assert {c["project"] for c in (decision.payload or {})["candidates"]} == {
        "acme-web", "acme-reports",
    }  # fmt: skip
    assert "touches" in (decision.payload or {})["reason"]


def test_a_task_across_owners_is_sent_back_to_be_split() -> None:
    ask("copy acme's customers into globex")
    model = FakeModel(
        [
            output(routed("acme-web", 0.9, touches=["globex-portal"])),
            output(routed("acme-web", 0.9), routed("globex-portal", 0.9)),
        ]
    )
    route(model)
    assert [t.project for t in all_tasks()] == ["acme-web", "globex-portal"]
    retry = model.calls[1][1]
    assert "spans owners acme, globex" in retry
    assert "split it into one task per project" in retry


def test_invalid_output_is_retried_once_then_recorded() -> None:
    thread_id = ask("do something")
    model = FakeModel(["not json at all", output(routed("ghost-project"))])
    route(model)
    assert len(model.calls) == 2
    assert "the answer must be one JSON object" in model.calls[1][1]
    assert all_tasks() == []
    with session_scope() as session:
        message = session.scalars(select(Message).where(Message.role == MessageRole.USER)).one()
        assert message.route_error is not None
        assert "unknown projects: ghost-project" in message.route_error
        assert message.routed_at is not None
    assert thread_bodies(thread_id)[-1][0] is MessageRole.SYSTEM


def test_a_model_failure_is_recorded_and_not_retried_forever() -> None:
    class Broken:
        def complete(self, system: str, prompt: str, model: str) -> Completion:
            raise RuntimeError("cli crashed")

    ask("anything")
    route(Broken())  # type: ignore[arg-type]
    assert route_next(get_settings(), FakeModel([])) is None  # nothing left to route
    with session_scope() as session:
        message = session.scalars(select(Message).where(Message.role == MessageRole.USER)).one()
        assert message.route_error == "cli crashed"


def test_an_answered_route_routes_the_same_message_without_asking() -> None:
    body = "Refresh the numbers"
    ask(body)
    low = output(
        routed("acme-reports", 0.5, alternatives=[{"project": "acme-web", "confidence": 0.4}])
    )
    route(FakeModel([low]))
    [decision] = route_decisions()
    with session_scope() as session:
        inbox.answer(
            session, get_settings(), str(decision.id), Answer(choice="acme-web"),
            by="alice", via=Via.TELEGRAM,
        )  # fmt: skip
    assert [t.project for t in all_tasks()] == ["acme-web"]

    ask("refresh   the numbers")  # same phrasing, different case and spacing
    model = FakeModel([low])
    route(model)
    assert [t.project for t in all_tasks()] == ["acme-web", "acme-web"]
    assert len(route_decisions()) == 1  # no new question
    assert '"Refresh the numbers" -> acme-web' in model.calls[0][1]

    # Forgetting the example brings the question back.
    with session_scope() as session:
        [example] = examples.list_examples(session)
        examples.delete(session, str(example.id))
    ask(body)
    route(FakeModel([low]))
    assert len(route_decisions()) == 2


def test_complexity_picks_the_worker_model_and_projects_can_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RINGKEEPER_MODEL_HIGH", "claude-opus-5-5")
    with session_scope() as session:
        project = session.scalars(select(Project).where(Project.name == "globex-portal")).one()
        project.models = {"medium": "claude-haiku-5-5"}
    ask("three things")
    route(
        FakeModel(
            [
                output(
                    routed("acme-web", complexity="high", prompt="a"),
                    routed("acme-reports", complexity="medium", prompt="b"),
                    routed("globex-portal", complexity="medium", prompt="c"),
                )
            ]
        )
    )
    models = {t.project: t.sessions[0].model for t in all_tasks()}
    assert models == {
        "acme-web": "claude-opus-5-5",
        "acme-reports": "claude-sonnet-5-5",
        "globex-portal": "claude-haiku-5-5",  # the project's override
    }


def test_the_router_loop_drains_every_waiting_message() -> None:
    ask("one")
    ask("two")
    router = Router(FakeModel([output(), output()]))
    assert router.drain() == 2
    assert router.drain() == 0


def test_routing_examples_cli_lists_and_deletes() -> None:
    with session_scope() as session:
        saved = examples.save(session, "Count globex customers", "globex-portal", by="alice")
        prefix = str(saved.id)[:8]
    runner = CliRunner()
    listed = runner.invoke(cli, ["routing", "examples", "list"])
    assert listed.exit_code == 0, listed.output
    assert prefix in listed.stdout
    assert "Count globex customers" in listed.stdout
    deleted = runner.invoke(cli, ["routing", "examples", "delete", prefix])
    assert deleted.exit_code == 0, deleted.output
    assert "deleted routing example 'Count globex customers' -> globex-portal" in deleted.stdout
    assert "no routing examples" in runner.invoke(cli, ["routing", "examples", "list"]).stdout
    missing = runner.invoke(cli, ["routing", "examples", "delete", prefix])
    assert missing.exit_code == 1


def test_sdk_env_blanks_every_other_billing_variable() -> None:
    credential = ClaudeCredential(
        AuthMode.SUBSCRIPTION, "file://x", Secret("file://x", "sk-ant-oat01-fake-router")
    )
    env = sdk_env(credential, "/state/router")
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "sk-ant-oat01-fake-router"
    assert env["CLAUDE_CONFIG_DIR"] == "/state/router"
    blanked = {k for k, v in env.items() if v == ""}
    assert blanked == set(BILLING_ENV_VARS) - {"CLAUDE_CODE_OAUTH_TOKEN"}
