"""The architect loop on the fake runtime: plan gate, checks gating, continue rounds,
model escalation, the review cap, and token scoping."""

import json
from collections.abc import Callable
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from ringkeeper.calls import Caller
from ringkeeper.config import get_settings
from ringkeeper.db import session_scope
from ringkeeper.errors import RuleViolationError
from ringkeeper.models import (
    ArchitectPhase,
    Complexity,
    Decision,
    DecisionKind,
    Project,
    SessionRole,
    Task,
    TaskKind,
    TaskStatus,
)
from ringkeeper.services import architect, inbox, tasks
from ringkeeper.services.inbox import Answer, Via
from ringkeeper.tokens import TokenRole, verify_token
from ringkeeper.vault import get_vault
from ringkeeper.workers.manager import Manager
from ringkeeper.workers.runtime import WorkerSpec
from tests.fake_runtime import FakeRuntime, Script, line, result_line, workspace_line

pytestmark = pytest.mark.usefixtures("task_registry")

Step = Callable[["Steps"], object]


def caller_of(spec: WorkerSpec) -> Caller:
    claims = verify_token(spec.env["RINGKEEPER_GATEWAY_TOKEN"])
    return Caller(claims.project, claims.session_id, claims.token_id)


def config_of(spec: WorkerSpec) -> dict[str, Any]:
    return json.loads(spec.env["RINGKEEPER_TASK"])


def checks(passed: bool) -> str:
    result = {"command": "pytest -q", "exit_code": 0 if passed else 1, "passed": passed,
              "output": "1 passed" if passed else "E   AssertionError: plan label"}  # fmt: skip
    return line("ringkeeper.checks", passed=passed, results=[result])


class Director:
    """Plays the architect and the worker: each session runs the next scripted step."""

    def __init__(self) -> None:
        self.architect_steps: list[Step] = []
        self.worker_checks: list[bool] = []
        self.specs: list[WorkerSpec] = []
        self.errors: list[str] = []

    def __call__(self, spec: WorkerSpec) -> Script:
        self.specs.append(spec)
        config = config_of(spec)
        if config["role"] == "architect":
            step = self.architect_steps.pop(0)
            with session_scope() as session:
                try:
                    step(Steps(session, caller_of(spec)))
                except RuleViolationError as error:
                    self.errors.append(str(error))
            return Script(after=[result_line(session_id="arch-1")])
        passed = self.worker_checks.pop(0)
        return Script(
            after=[
                result_line(session_id="work-1"),
                workspace_line(["src/acme_web/plans.py"], branch="ringkeeper/t"),
                checks(passed),
                line("ringkeeper.diff", text="+def format_plan(plan): ..."),
            ]
        )


class Steps:
    """The architect tools, bound to one DB session and the session's own caller."""

    def __init__(self, session: Session, caller: Caller) -> None:
        self.session = session
        self.caller = caller

    def submit_plan(self, *, title: str, plan: str, worker_prompt: str) -> str:
        return architect.submit_plan(
            self.session, self.caller, title=title, plan=plan, worker_prompt=worker_prompt
        )

    def dispatch_worker(self, *, prompt: str) -> str:
        return architect.dispatch_worker(self.session, self.caller, prompt=prompt)

    def continue_worker(self, *, feedback: str) -> str:
        return architect.continue_worker(self.session, self.caller, feedback=feedback)

    def approve_work(self, *, summary: str) -> str:
        return architect.approve_work(self.session, self.caller, summary=summary)


def submit(a: "Steps") -> str:
    return a.submit_plan(title="Add format_plan()", plan="Add src/acme_web/plans.py + test",
                         worker_prompt="Write format_plan and a test.")  # fmt: skip


@pytest.fixture
def project_checks(task_registry: None) -> None:
    with session_scope() as session:
        project = session.scalars(select(Project).where(Project.name == "acme-web")).one()
        project.checks = ["pytest -q"]


def feature(complexity: str = "medium") -> str:
    with session_scope() as session:
        task = tasks.create_task(
            session, get_settings(), project="acme-web", prompt="Add a plan formatter",
            kind=TaskKind.FEATURE, complexity=Complexity(complexity),
            outcome="format_plan() exists with tests",
        )  # fmt: skip
        return str(task.id)


def run(manager: Manager) -> None:
    manager.tick()
    manager.wait_idle()


def load(task_id: str) -> Task:
    with session_scope() as session:
        task = tasks.get_task(session, task_id)
        _ = [s.checks for s in task.sessions]
        session.expunge_all()
        return task


def pending(kind: DecisionKind) -> list[Decision]:
    with session_scope() as session:
        found = list(inbox.list_pending(session, kind=kind))
        session.expunge_all()
        return found


def answer(decision: Decision, **reply: str) -> None:
    with session_scope() as session:
        inbox.answer(
            session, get_settings(), str(decision.id), Answer(**reply), by="alice", via=Via.CLI
        )


@pytest.mark.usefixtures("project_checks")
def test_the_full_loop_with_checks_gating_escalation_and_the_review_cap() -> None:
    director = Director()
    manager = Manager(FakeRuntime(director), get_vault())
    task_id = feature()

    # 1. The architect plans, read-only, with only the architect endpoint.
    director.architect_steps.append(submit)
    run(manager)
    spec = director.specs[0]
    assert spec.workspace_read_only is True
    config = config_of(spec)
    assert config["mcp_servers"] == {"ringkeeper-architect": "http://gateway:8100/mcp/architect"}
    assert {"Write", "Edit", "Bash"} <= set(config["disallowed_tools"])
    assert config["model"] == "claude-opus-5-5"
    assert config["checks"] == []
    assert "Add a plan formatter" in config["prompt"]
    assert verify_token(spec.env["RINGKEEPER_GATEWAY_TOKEN"]).role is TokenRole.ARCHITECT
    task = load(task_id)
    assert (task.status, task.phase) == (TaskStatus.WAITING, ArchitectPhase.AWAITING_PLAN)
    [plan] = pending(DecisionKind.PLAN)
    assert plan.payload == {
        "title": "Add format_plan()", "plan": "Add src/acme_web/plans.py + test",
        "worker_prompt": "Write format_plan and a test.",
    }  # fmt: skip

    # 2. Plan gate: nothing runs until the plan is approved.
    run(manager)
    assert len(director.specs) == 1
    answer(plan, choice="approve")
    director.architect_steps.append(lambda a: a.dispatch_worker(prompt="Write format_plan."))
    run(manager)
    assert config_of(director.specs[1])["resume"] == "arch-1"

    # 3. The worker builds; its checks fail. The architect may not approve.
    director.worker_checks.append(False)
    run(manager)
    worker = config_of(director.specs[2])
    assert (worker["role"], worker["checks"], worker["resume"]) == ("worker", ["pytest -q"], None)
    assert worker["prompt"] == "Write format_plan."
    assert worker["model"] == "claude-sonnet-5-5"  # medium complexity
    assert director.specs[2].workspace_read_only is False
    assert verify_token(director.specs[2].env["RINGKEEPER_GATEWAY_TOKEN"]).role is TokenRole.WORKER
    task = load(task_id)
    assert (task.phase, task.review_round, task.failed_check_rounds) == (
        ArchitectPhase.REVIEWING, 1, 1,
    )  # fmt: skip
    review = tasks.latest_session(task)
    assert review.role is SessionRole.ARCHITECT
    assert review.sdk_session_id == "arch-1"
    assert "Checks: FAILED" in review.prompt
    assert "AssertionError: plan label" in review.prompt
    assert "+def format_plan(plan)" in review.prompt
    assert "review 1 of 3" in review.prompt

    def approve_then_continue(a: Steps) -> None:
        try:
            a.approve_work(summary="looks fine")
        except RuleViolationError as error:
            director.errors.append(str(error))
            a.continue_worker(feedback="Fix the plan label.")

    director.architect_steps.append(approve_then_continue)
    run(manager)
    assert "checks fail" in director.errors[-1]

    # 4. Second worker turn resumes the same worker session; checks fail again.
    director.worker_checks.append(False)
    run(manager)
    second = config_of(director.specs[4])
    assert (second["resume"], second["prompt"]) == ("work-1", "Fix the plan label.")
    assert second["model"] == "claude-sonnet-5-5"
    director.architect_steps.append(lambda a: a.continue_worker(feedback="Still wrong."))
    run(manager)

    # 5. After two failed check rounds the worker moves up a tier.
    director.worker_checks.append(True)
    run(manager)
    third = config_of(director.specs[6])
    assert third["model"] == "claude-opus-5-5"
    with session_scope() as session:
        models = [(s.role, s.model) for s in tasks.get_task(session, task_id).sessions]
        assert models[-2] == (SessionRole.WORKER, "claude-opus-5-5")  # recorded per session
    assert load(task_id).review_round == 3

    # 6. Review 3 of 3: continuing escalates to the operator instead.
    director.architect_steps.append(lambda a: a.continue_worker(feedback="One more pass."))
    run(manager)
    task = load(task_id)
    assert task.status is TaskStatus.NEEDS_INPUT
    [escalation] = pending(DecisionKind.QUESTION)
    assert (escalation.payload or {})["escalation"] is True
    assert "One more pass." in (escalation.payload or {})["question"]
    # 5 architect turns, 3 worker turns, and no fourth worker turn.
    assert [config_of(x)["role"] for x in director.specs].count("worker") == 3
    assert len(director.specs) == 8

    # 7. The operator answers; the architect approves; the task succeeds.
    answer(escalation, text="Good enough, approve it.")
    director.architect_steps.append(lambda a: a.approve_work(summary="format_plan() + test."))
    run(manager)
    resumed = config_of(director.specs[8])
    assert resumed["role"] == "architect"
    assert "Good enough, approve it." in resumed["prompt"]
    done = load(task_id)
    assert (done.status, done.phase) == (TaskStatus.SUCCEEDED, ArchitectPhase.DONE)
    assert done.summary == "format_plan() + test."
    assert done.git_branch == "ringkeeper/t"
    assert done.changed_files == ["src/acme_web/plans.py"]


def test_an_architect_that_ends_without_a_step_fails_the_task() -> None:
    director = Director()
    manager = Manager(FakeRuntime(director), get_vault())
    task_id = feature()
    director.architect_steps.append(lambda a: None)
    run(manager)
    task = load(task_id)
    assert task.status is TaskStatus.FAILED
    assert task.error == "the architect ended its turn without calling submit_plan"


def test_steps_out_of_phase_are_refused() -> None:
    director = Director()
    manager = Manager(FakeRuntime(director), get_vault())
    feature()

    def eager(a: Steps) -> None:
        for attempt in (
            lambda: a.dispatch_worker(prompt="go"),
            lambda: a.continue_worker(feedback="x"),
            lambda: a.approve_work(summary="x"),
        ):
            try:
                attempt()
            except RuleViolationError as error:
                director.errors.append(str(error))
        submit(a)
        submit(a)  # one step per turn

    director.architect_steps.append(eager)
    run(manager)
    assert len(director.errors) == 4
    assert all("planning phase" in e for e in director.errors[:3])
    assert "already called submit_plan" in director.errors[3]


def test_a_denied_plan_with_feedback_goes_back_to_planning() -> None:
    director = Director()
    manager = Manager(FakeRuntime(director), get_vault())
    task_id = feature()
    director.architect_steps.append(submit)
    run(manager)
    [plan] = pending(DecisionKind.PLAN)
    answer(plan, choice="deny", text="Use a dataclass.")
    director.architect_steps.append(submit)
    run(manager)
    assert "Use a dataclass." in config_of(director.specs[1])["prompt"]
    assert len(pending(DecisionKind.PLAN)) == 1
    assert load(task_id).phase is ArchitectPhase.AWAITING_PLAN


def test_a_worker_session_cannot_take_architect_steps() -> None:
    with session_scope() as session:
        task = tasks.create_task(session, get_settings(), project="acme-web", prompt="p")
        caller = Caller("acme-web", str(task.sessions[0].id), "jti")
        with pytest.raises(RuleViolationError, match="isn't tied to an architect session"):
            architect.submit_plan(session, caller, title="t", plan="p", worker_prompt="w")
