"""A service saying the plan doesn't include this: for any connector, it's said plainly, the call
isn't repeated, and that call doesn't count against the task, while everything else does."""

import asyncio
import json
import sys
import uuid
from datetime import UTC, datetime, timedelta
from functools import partial
from pathlib import Path
from typing import Any

import httpx2
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from ringkeeper import plan_limits, remote
from ringkeeper.adapters import vendor
from ringkeeper.adapters.base import PlanLimitError, ToolError
from ringkeeper.calls import Caller, ToolCall
from ringkeeper.config import get_settings
from ringkeeper.db import session_scope
from ringkeeper.models import (
    AuthMode,
    DecisionKind,
    Env,
    InstanceType,
    Project,
    SessionRole,
    Task,
    TaskStatus,
    WorkerSession,
)
from ringkeeper.schemas import BindingIn, InstanceIn
from ringkeeper.services import (
    architect,
    bindings,
    instances,
    mcp_tools,
    plan_notes,
    probes,
    remote_connections,
    secrets_in,
)
from ringkeeper.services.calls import run_call
from ringkeeper.vault import get_vault
from ringkeeper.workers.manager import Manager
from ringkeeper.workers.runtime import WorkerSpec
from tests import test_web_api
from tests.fake_runtime import FakeRuntime, Script, line, result_line, workspace_line
from tests.helpers import make_project
from tests.test_architect import (
    Director,
    answer,
    caller_of,
    config_of,
    feature,
    load,
    pending,
    run,
    submit,
)

setup_code, admin, owner = test_web_api.setup_code, test_web_api.admin, test_web_api.owner
SERVER = Path(__file__).parent / "fixtures" / "fake_leadfeeder_mcp.py"
NAME = "acme-leadfeeder"
SAID = (
    "Your Leadfeeder plan doesn't include API access to this. Only sign-in and the account list "
    "work on the free plan."
)


@pytest.mark.parametrize(
    "text",
    [
        '403 access_denied: "Your plan doesn\'t include access to this endpoint"',
        "hubspot returned 403: This feature is not available on your plan",
        "The current subscription does not include the API",
        "error: not_entitled",
        "403 Forbidden: requires a paid plan. Upgrade to Pro",
        "402 Payment Required: upgrade your plan",
        "returned 403: this tier isn't eligible for exports",
        "code=plan_required",
    ],
)
def test_a_plan_refusal_is_recognised(text: str) -> None:
    assert plan_limits.found(text)


@pytest.mark.parametrize(
    "text",
    [
        "hubspot returned 403: You don't have permission to read deals",
        "returned 401: invalid token",
        "returned 429: rate limit reached, upgrade your plan for higher limits",
        "Too many requests. Your plan does not allow more than 10 a minute",
        "returned 404: the plan 'Gold' doesn't exist",
        "Upgrade to version 3 of the client",
        "returned 500: something went wrong",
    ],
)
def test_other_failures_arent(text: str) -> None:
    assert not plan_limits.found(text)


# --- Leadfeeder on the free plan, as a real account answered ---


def add(name: str, ran: Path, plan: str = "free") -> None:
    secret = json.dumps({
        "command": sys.executable, "args": [str(SERVER)],
        "env": {"FAKE_LEADFEEDER_RAN": str(ran), "FAKE_LEADFEEDER_PLAN": plan},
    })  # fmt: skip
    with session_scope() as session:
        ref = secrets_in.store_connection(session, get_settings(), name, InstanceType.MCP, secret)
        instances.create_instance(session, InstanceIn(
            name=name, title=name, type=InstanceType.MCP, env=Env.PROD, secret_ref=ref,
            owner="acme", config={"remote": "leadfeeder"},
        ))  # fmt: skip


def connect(file: Path) -> None:
    add(NAME, file)
    with session_scope() as session:
        bindings.set_binding(session, BindingIn(
            project="acme-web", instance=NAME, scope={"account": "acc-1"},
        ))  # fmt: skip
    mcp_tools.refresh(NAME)


@pytest.fixture
def free(database_url: str, tmp_path: Path) -> Path:
    file = tmp_path / "ran.json"
    with session_scope() as session:
        make_project(session, "acme-web", owner="acme")
    connect(file)
    return file


def worker_session() -> tuple[str, Any]:
    with session_scope() as session:
        task = Task(project="acme-web", prompt="p", status=TaskStatus.RUNNING, max_turns=5,
                    max_runtime_s=60, changed_files=[], auth_mode=AuthMode.API_KEY)  # fmt: skip
        session.add(task)
        session.flush()
        row = WorkerSession(task_id=task.id, ordinal=1, role=SessionRole.WORKER,
                            status=TaskStatus.RUNNING, prompt="p", state_volume="v")  # fmt: skip
        session.add(row)
        session.flush()
        return str(row.id), task.id


def search(session_id: str = "session-1") -> Any:
    args = {"tool": "search_web_visits",
            "arguments": {"start_date": "2026-11-01", "end_date": "2026-11-06"}}  # fmt: skip
    caller = Caller("acme-web", session_id, "token-1")
    made = ToolCall(caller, InstanceType.MCP, "call_tool", NAME, args)
    return asyncio.run(run_call(made, get_vault()))


def limit() -> dict[str, Any]:
    with session_scope() as session:
        return dict(instances.get_instance(session, NAME).config.get(plan_notes.KEY) or {})


def test_the_catalog_says_it_needs_a_paid_plan() -> None:
    found = next(s for s in remote_connections.catalog_shelf() if s["key"] == "leadfeeder")
    assert found["needs"] == "needs a paid Leadfeeder plan"


def test_the_agent_gets_the_clear_message_and_is_told_not_to_retry(free: Path) -> None:
    with pytest.raises(PlanLimitError) as raised:
        search()
    assert raised.value.shown == SAID
    assert "Don't try again" in str(raised.value)
    assert "upgrade_url" not in str(raised.value)


def test_the_connection_card_says_so(free: Path) -> None:
    with pytest.raises(PlanLimitError):
        search()
    assert limit()["message"] == SAID


def test_the_task_says_so(free: Path) -> None:
    session_id, task = worker_session()
    with pytest.raises(PlanLimitError):
        search(session_id)
    with session_scope() as session:
        assert session.get(Task, task).plan_limit == SAID  # type: ignore[union-attr]


def test_the_same_call_isnt_sent_again(free: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(PlanLimitError):
        search()

    def must_not_run(*_args: Any) -> None:
        raise AssertionError("it asked the service again")

    monkeypatch.setattr("ringkeeper.adapters.mcp._client", must_not_run)
    with pytest.raises(PlanLimitError, match="Don't try again"):
        search()


def test_a_write_the_plan_refused_doesnt_come_back_as_an_approval(free: Path) -> None:
    plan_notes.note(NAME, "create_tag", SAID)
    args = {"tool": "create_tag", "arguments": {"name": "Hot"}}
    made = ToolCall(Caller("acme-web", "s", "t"), InstanceType.MCP, "call_tool", NAME, args)
    with pytest.raises(PlanLimitError):
        asyncio.run(run_call(made, get_vault()))


def test_after_an_hour_it_may_be_tried_again() -> None:
    now = datetime(2026, 11, 6, 12, tzinfo=UTC)
    config = {plan_notes.KEY: {"message": SAID, "tools": ["x"], "at": now.isoformat()}}
    assert plan_notes.blocked(config, "x", now + timedelta(minutes=59)) == SAID
    assert plan_notes.blocked(config, "x", now + timedelta(minutes=61)) is None
    assert plan_notes.blocked(config, "y", now) is None


def test_test_says_the_plan_and_still_lists_the_accounts(free: Path) -> None:
    result = probes.test_connection(NAME, by="owner")
    assert result.ok is True  # the sign-in works: it's not a broken connection
    assert result.message.startswith(SAID)
    assert "Demo (acc-1)" in result.message
    assert limit()["message"].startswith(SAID)


def test_after_upgrading_test_clears_it(free: Path) -> None:
    probes.test_connection(NAME, by="owner")
    with session_scope() as session:
        found = instances.get_instance(session, NAME)
        ref = found.secret_ref
    secret = json.loads(get_vault().resolve(ref).reveal())
    secret["env"]["FAKE_LEADFEEDER_PLAN"] = "paid"
    get_vault().store(ref, json.dumps(secret))
    assert probes.test_connection(NAME, by="owner").ok is True
    assert limit() == {}


def test_the_api_gives_the_card_and_task_what_they_show(owner: TestClient, free: Path) -> None:
    session_id, task = worker_session()
    with pytest.raises(PlanLimitError):
        search(session_id)
    connection = next(c for c in owner.get("/connections").json() if c["name"] == NAME)
    assert connection["config"]["plan_limit"]["message"] == SAID
    assert owner.get(f"/tasks/{task}").json()["plan_limit"] == SAID


# --- It doesn't count against the task ---


TESTS, LINT = "pytest -q", "ruff check ."
SENT_BACK = "don't send the worker back"


class Turns(Director):
    """Worker turns as (does it make the call the plan refuses, the checks that fail)."""

    def __init__(self) -> None:
        super().__init__()
        self.turns: list[tuple[bool, list[str]]] = []

    def __call__(self, spec: WorkerSpec) -> Script:
        if config_of(spec)["role"] == "architect":
            return super().__call__(spec)
        self.specs.append(spec)
        calls, failing = self.turns.pop(0)
        if calls:
            with pytest.raises(PlanLimitError):
                search(str(caller_of(spec).session_id))
        results = [{"command": check, "passed": check not in failing, "output": "",
                    "exit_code": int(check in failing)} for check in (TESTS, LINT)]  # fmt: skip
        return Script(
            after=[
                result_line(session_id="work-1"),
                workspace_line(["src/acme_web/visits.py"], branch="ringkeeper/t"),
                line("ringkeeper.checks", passed=not failing, results=results),
                line("ringkeeper.diff", text="+def visits(): ..."),
            ]
        )


@pytest.fixture
def looping(task_registry: None, tmp_path: Path) -> None:
    with session_scope() as session:
        project = session.scalars(select(Project).where(Project.name == "acme-web")).one()
        project.checks = [TESTS, LINT]
    connect(tmp_path / "ran.json")


def send_back(director: Turns, manager: Manager) -> None:
    director.architect_steps.append(lambda a: a.continue_worker(feedback="Fix what failed."))
    run(manager)


def play(*turns: tuple[bool, list[str]]) -> tuple[Turns, Manager, str]:
    """A real feature task through the loop, the architect sending the worker back each time."""
    director = Turns()
    manager = Manager(FakeRuntime(director), get_vault())
    task_id = feature()
    director.architect_steps.append(submit)
    run(manager)
    [plan] = pending(DecisionKind.PLAN)
    answer(plan, choice="approve")
    director.architect_steps.append(lambda a: a.dispatch_worker(prompt="Add the visits report."))
    run(manager)
    for index, turn in enumerate(turns):
        if index:
            send_back(director, manager)
        director.turns.append(turn)
        run(manager)
        run_of = load(task_id)
        assert run_of.status is not TaskStatus.FAILED, run_of.error
    return director, manager, task_id


def rounds(*turns: tuple[bool, list[str]]) -> int:
    return load(play(*turns)[2]).failed_check_rounds


def next_worker_model(director: Turns, manager: Manager) -> str:
    """Review 3 of 3 goes to the operator. Told to go on, the worker runs once more."""
    send_back(director, manager)
    [question] = pending(DecisionKind.QUESTION)
    answer(question, text="Go on.")
    director.architect_steps.append(lambda a: a.continue_worker(feedback="One more pass."))
    run(manager)
    director.turns.append((False, []))
    run(manager)
    return config_of(director.specs[-1])["model"]


@pytest.mark.usefixtures("looping")
@pytest.mark.parametrize("retrying", [False, True])
def test_a_plan_limit_then_two_unrelated_failures_still_escalates(retrying: bool) -> None:
    director, manager, task_id = play(
        (True, [TESTS]), (retrying, [LINT]), (retrying, [LINT]),
    )  # fmt: skip
    task = load(task_id)
    assert task.plan_limit == SAID  # the task still says why
    assert task.failed_check_rounds == 2
    assert next_worker_model(director, manager) == "claude-opus-5-5"


@pytest.mark.usefixtures("looping")
def test_the_refused_call_and_its_retries_dont_count() -> None:
    director, manager, task_id = play((True, [TESTS]), (True, [TESTS]), (True, [TESTS]))
    assert load(task_id).failed_check_rounds == 0
    assert next_worker_model(director, manager) == "claude-sonnet-5-5"


@pytest.mark.usefixtures("looping")
def test_a_real_failure_still_counts() -> None:
    assert rounds((False, [TESTS])) == 1


@pytest.mark.usefixtures("looping")
def test_the_same_check_failing_without_the_call_counts() -> None:
    assert rounds((True, [TESTS]), (False, [TESTS])) == 1


@pytest.mark.usefixtures("looping")
def test_a_check_already_failing_before_the_limit_counts() -> None:
    assert rounds((False, [TESTS]), (True, [TESTS])) == 2


@pytest.mark.usefixtures("looping")
def test_a_retry_with_a_new_failure_counts() -> None:
    assert rounds((True, [TESTS]), (True, [TESTS, LINT])) == 1


@pytest.mark.usefixtures("looping")
def test_the_architect_only_hears_of_it_for_the_round_it_happened() -> None:
    _, _, task_id = play((True, [TESTS]), (False, [LINT]))
    with session_scope() as session:
        reviews = [s.prompt for s in session.get(Task, uuid.UUID(task_id)).sessions  # type: ignore[union-attr]
                   if s.role is SessionRole.ARCHITECT and "Checks:" in s.prompt]  # fmt: skip
    assert SENT_BACK in reviews[0]
    assert SAID in reviews[0]
    assert SENT_BACK not in reviews[1]
    assert SAID not in reviews[1]


def turn(ordinal: int, role: SessionRole = SessionRole.WORKER, **fields: Any) -> WorkerSession:
    return WorkerSession(ordinal=ordinal, role=role, **fields)


def test_a_round_that_stopped_for_an_approval_keeps_its_refusal() -> None:
    failed = {"passed": False, "results": [{"command": TESTS, "passed": False}]}
    resumed = turn(3, checks=failed)
    task = Task(prompt="p", sessions=[
        turn(1, SessionRole.ARCHITECT),
        turn(2, plan_refused={f"{NAME}/search_web_visits": SAID}),
        resumed,
    ])  # fmt: skip
    assert plan_notes.explains(task, resumed)
    assert plan_notes.refused_now(task, resumed) == SAID


def test_the_architect_isnt_told_to_approve_what_it_cant() -> None:
    task = Task(prompt="p", review_round=1, changed_files=[], plan_limit=SAID)
    review = partial(architect.review_prompt, task, TaskStatus.SUCCEEDED)
    passed = review({"passed": True, "results": []}, None, None, None, plan_limit=SAID)
    assert SENT_BACK in passed
    assert "Call approve_work(summary) with what was done" in passed
    assert "or continue_worker" not in passed
    failed = review({"passed": False, "results": []}, None, None, None, plan_limit=SAID)
    assert SENT_BACK in failed
    assert "call continue_worker with what to fix without it" in failed
    assert "approve_work" not in failed


# --- Any connector ---


class Vendored:
    def __init__(self, status: int, body: dict[str, Any]) -> None:
        self.status, self.body = status, body

    def handle(self, _request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(self.status, json=self.body)


def vendor_call(monkeypatch: pytest.MonkeyPatch, status: int, body: dict[str, Any]) -> Any:
    monkeypatch.setattr(vendor, "transport", httpx2.MockTransport(Vendored(status, body).handle))
    fields = {"vendor": "pipedrive", "api_key": "k" * 40, "domain": "demo"}
    secret = json.dumps(fields)
    with session_scope() as session:
        make_project(session, "acme-crm", owner="acme")
        ref = secrets_in.store_connection(
            session, get_settings(), "acme-crm-tool", InstanceType.VENDOR, secret
        )
        instances.create_instance(session, InstanceIn(
            name="acme-crm-tool", title="CRM", type=InstanceType.VENDOR, env=Env.DEV,
            secret_ref=ref, owner="acme", config={"vendor": "pipedrive"},
        ))  # fmt: skip
        bindings.set_binding(session, BindingIn(project="acme-crm", instance="acme-crm-tool"))
    op = next(o for o in vendor.VENDORS["pipedrive"].ops if o.level == "read" and "{" not in o.path)
    args = {"operation": f"pipedrive.{op.name}"}
    made = ToolCall(Caller("acme-crm", "s", "t"), InstanceType.VENDOR, "call_operation",
                    "acme-crm-tool", args)  # fmt: skip
    return asyncio.run(run_call(made, get_vault()))


def test_any_vendor_gets_the_same_handling(
    database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    body = {"error": "This feature is not available on your plan"}
    with pytest.raises(PlanLimitError) as raised:
        vendor_call(monkeypatch, 403, body)
    assert raised.value.shown == "Your Pipedrive plan doesn't include API access to this."
    with session_scope() as session:
        kept = instances.get_instance(session, "acme-crm-tool").config[plan_notes.KEY]
    assert kept["message"] == raised.value.shown


def test_an_ordinary_403_stays_an_ordinary_error(
    database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(ToolError) as raised:
        vendor_call(monkeypatch, 403, {"error": "You can't read deals"})
    assert not isinstance(raised.value, PlanLimitError)
    with session_scope() as session:
        assert plan_notes.KEY not in instances.get_instance(session, "acme-crm-tool").config


def test_what_worked_is_no_longer_remembered_as_refused(free: Path) -> None:
    plan_notes.note(NAME, "other", SAID)
    plan_notes.note(NAME, "get_countries", SAID)
    plan_notes.clear(NAME, "get_countries")
    assert limit()["tools"] == ["other"]
    assert remote.BY_KEY["leadfeeder"].plan_probe
