"""The session manager against a fake container runtime (no Docker, no LLM)."""

import json
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from ringkeeper.claude_auth import BILLING_ENV_VARS
from ringkeeper.config import Settings, get_settings
from ringkeeper.db import session_scope
from ringkeeper.errors import RuleViolationError
from ringkeeper.model_choice import session_effort
from ringkeeper.models import (
    Complexity,
    Decision,
    DecisionKind,
    SessionEvent,
    SessionRole,
    Task,
    TaskStatus,
    TokenRevocation,
    WorkerSession,
)
from ringkeeper.services import inbox, revocations, tasks
from ringkeeper.vault import get_vault
from ringkeeper.workers.manager import Manager, resolve_workspace
from ringkeeper.workers.runtime import WorkerSpec, daemon_path
from tests.conftest import CLAUDE_API_KEY as API_KEY
from tests.fake_runtime import FakeRuntime, Script, line, result_line, workspace_line
from tests.helpers import bind, make_instance


def manager(script: Callable[[WorkerSpec], Script] | None = None) -> tuple[Manager, FakeRuntime]:
    runtime = FakeRuntime(script)
    return Manager(runtime, get_vault()), runtime


def run_task(
    project: str = "acme-web",
    prompt: str = "do it",
    *,
    max_turns: int | None = None,
    max_runtime_s: int | None = None,
    budget_usd: float | None = None,
) -> str:
    with session_scope() as session:
        task = tasks.create_task(
            session, get_settings(), project=project, prompt=prompt, max_turns=max_turns,
            max_runtime_s=max_runtime_s, budget_usd=budget_usd,
        )  # fmt: skip
        return str(task.id)


def task(task_id: str) -> Task:
    with session_scope() as session:
        found = tasks.get_task(session, task_id)
        _ = [s.status for s in found.sessions]
        return found


def session_of(task_id: str, ordinal: int = 1) -> WorkerSession:
    return next(s for s in task(task_id).sessions if s.ordinal == ordinal)


def revoked(jti: str | None) -> bool:
    assert jti is not None
    with session_scope() as session:
        return revocations.is_revoked(session, jti)


def drive(
    m: Manager, until: Callable[[], bool], timeout: float = 10.0, *, idle: bool = True
) -> None:
    deadline = time.monotonic() + timeout
    while not until():
        assert time.monotonic() < deadline, "condition not reached"
        m.tick()
        time.sleep(0.05)
    if idle:
        m.wait_idle()


def task_env(spec: WorkerSpec) -> dict[str, object]:
    return json.loads(spec.env["RINGKEEPER_TASK"])


# --- The happy path ---


@pytest.mark.usefixtures("task_registry")
def test_a_task_runs_and_everything_is_recorded() -> None:
    m, runtime = manager(
        lambda _spec: Script(
            before=[line("sdk.assistant", message={"content": [{"text": "hi"}]})],
            after=[
                result_line(turns=4, cost=0.42),
                workspace_line(["NOTES.md"], branch="ringkeeper/t"),
            ],
        )
    )
    task_id = run_task()
    m.tick()
    m.wait_idle()

    done = task(task_id)
    assert done.status is TaskStatus.SUCCEEDED
    assert done.git_branch == "ringkeeper/t"
    assert done.changed_files == ["NOTES.md"]
    worker_session = session_of(task_id)
    assert (worker_session.turns, worker_session.cost_usd) == (4, 0.42)
    assert worker_session.sdk_session_id == "sdk-1"
    assert worker_session.usage == {"input_tokens": 10, "output_tokens": 20}
    assert worker_session.exit_code == 0
    assert revoked(worker_session.token_jti)  # revoked as soon as the session ended

    with session_scope() as session:
        types = session.scalars(
            select(SessionEvent.type)
            .where(SessionEvent.session_id == worker_session.id)
            .order_by(SessionEvent.seq)
        ).all()
    assert types == ["sdk.assistant", "sdk.result", "ringkeeper.workspace"]

    [spec] = runtime.started
    assert spec.workspace == "/srv/workspaces/acme-web"
    assert spec.state_volume == f"ringkeeper-state-{task_id}-worker"
    assert spec.network == "ringkeeper_workers"
    assert runtime.removed  # containers are cleaned up


@pytest.mark.usefixtures("task_registry")
def test_worker_gets_the_session_config() -> None:
    with session_scope() as session:
        make_instance(session, "acme-db", secret_ref="file://acme/db")
    bind("acme-web", "acme-db", is_default=True)
    m, runtime = manager()
    task_id = run_task(max_turns=9)
    m.tick()
    m.wait_idle()
    config = task_env(runtime.started[0])
    assert config["max_turns"] == 9
    assert config["branch"] == f"ringkeeper/{task_id}"
    assert config["resume"] is None
    assert config["disallowed_tools"] == ["WebFetch", "WebSearch"]  # standard autonomy
    # Only the services the project is connected to.
    assert config["mcp_servers"] == {"ringkeeper-database": "http://gateway:8100/mcp/database"}
    token = runtime.started[0].env["RINGKEEPER_GATEWAY_TOKEN"]
    from ringkeeper.tokens import verify_token  # noqa: PLC0415

    claims = verify_token(token)
    assert claims.project == "acme-web"
    assert claims.session_id == str(session_of(task_id).id)
    assert (claims.expires_at - claims.issued_at).total_seconds() == 30 * 60  # = max runtime


# --- Exactly one Claude credential ---


@pytest.mark.usefixtures("task_registry")
def test_api_key_mode_gives_only_the_api_key() -> None:
    m, runtime = manager()
    run_task()
    m.tick()
    m.wait_idle()
    env = runtime.started[0].env
    present = [name for name in BILLING_ENV_VARS if name in env]
    assert present == ["ANTHROPIC_API_KEY"]
    assert env["ANTHROPIC_API_KEY"] == API_KEY


@pytest.mark.usefixtures("task_registry")
def test_subscription_mode_never_includes_an_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RINGKEEPER_CLAUDE_AUTH_MODE", "subscription")
    m, runtime = manager()
    run_task()
    m.tick()
    m.wait_idle()
    env = runtime.started[0].env
    assert [name for name in BILLING_ENV_VARS if name in env] == ["CLAUDE_CODE_OAUTH_TOKEN"]
    assert "ANTHROPIC_API_KEY" not in env


@pytest.mark.usefixtures("task_registry")
def test_worker_env_is_an_allowlist() -> None:
    m, runtime = manager()
    run_task()
    m.tick()
    m.wait_idle()
    assert set(runtime.started[0].env) == {
        "LANG", "CLAUDE_CONFIG_DIR", "CLAUDE_CODE_PROJECT_DIR_NAME",
        "CLAUDE_CODE_DISABLE_AUTO_MEMORY", "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC",
        "DISABLE_AUTOUPDATER", "DISABLE_TELEMETRY",
        "RINGKEEPER_TASK", "RINGKEEPER_GATEWAY_TOKEN", "ANTHROPIC_API_KEY",
    }  # fmt: skip


# --- Guardrails ---


@pytest.mark.usefixtures("task_registry")
def test_concurrency_cap_queues_extra_tasks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RINGKEEPER_MAX_CONCURRENT_WORKERS", "2")
    m, runtime = manager(lambda _spec: Script(delay=0.6))
    ids = [run_task(prompt=f"task {i}") for i in range(3)]
    m.tick()
    time.sleep(0.1)
    assert [task(i).status for i in ids] == [
        TaskStatus.RUNNING,
        TaskStatus.RUNNING,
        TaskStatus.QUEUED,
    ]
    drive(m, lambda: all(task(i).status is TaskStatus.SUCCEEDED for i in ids))
    assert runtime.peak == 2
    assert len(runtime.started) == 3


@pytest.mark.usefixtures("task_registry")
def test_max_runtime_kills_the_worker() -> None:
    m, runtime = manager(lambda _spec: Script(delay=30))
    task_id = run_task(max_runtime_s=1)
    drive(m, lambda: task(task_id).status is TaskStatus.TIMED_OUT)
    done = task(task_id)
    assert done.error is not None
    assert "max runtime of 1s" in done.error
    assert runtime.killed
    assert revoked(session_of(task_id).token_jti)


@pytest.mark.usefixtures("task_registry")
def test_stop_kills_and_revokes() -> None:
    m, runtime = manager(lambda _spec: Script(delay=30))
    task_id = run_task()
    m.tick()
    drive(m, lambda: session_of(task_id).container_id is not None, timeout=5, idle=False)
    jti = session_of(task_id).token_jti
    with session_scope() as session:
        tasks.request_stop(session, task_id, by="ops")
    assert revoked(jti)  # revoked at once, before the container is even killed
    drive(m, lambda: task(task_id).status is TaskStatus.STOPPED)
    assert runtime.killed


@pytest.mark.usefixtures("task_registry")
def test_stop_all_kills_running_and_cancels_queued(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RINGKEEPER_MAX_CONCURRENT_WORKERS", "1")
    m, runtime = manager(lambda _spec: Script(delay=30))
    running, queued = run_task(), run_task()
    m.tick()
    drive(m, lambda: session_of(running).container_id is not None, timeout=5, idle=False)
    with session_scope() as session:
        stopped = tasks.stop_all(session, by="ops")
    assert {str(t.id) for t in stopped} == {running, queued}
    assert task(queued).status is TaskStatus.STOPPED  # never started
    drive(m, lambda: task(running).status is TaskStatus.STOPPED)
    assert len(runtime.started) == 1
    assert revoked(session_of(running).token_jti)


def cap_question(task_id: str) -> Decision:
    with session_scope() as session:
        [question] = [
            d for d in inbox.list_pending(session, kind=DecisionKind.QUESTION)
            if str(d.task_id) == task_id
        ]  # fmt: skip
        session.expunge(question)
        return question


def answer(decision: Decision, text: str) -> None:
    with session_scope() as session:
        inbox.answer(
            session, get_settings(), str(decision.id), inbox.Answer(text=text),
            by="owner", via=inbox.Via.API,
        )  # fmt: skip


@pytest.mark.usefixtures("task_registry")
def test_at_the_spending_cap_a_task_pauses_and_asks() -> None:
    spends = iter([1.2, 0.4])
    m, runtime = manager(
        lambda _spec: Script(after=[result_line(subtype="error_max_budget_usd", cost=next(spends))])
    )
    task_id = run_task(budget_usd=1.0)
    m.tick()
    m.wait_idle()
    assert task(task_id).status is TaskStatus.NEEDS_INPUT
    question = cap_question(task_id)
    assert question.payload is not None
    assert "spending cap ($1.00)" in question.payload["question"]
    assert question.payload["options"] == ["continue", "stop"]

    answer(question, "continue")
    assert task(task_id).budget_usd == 2.0  # another $1, the setting's default
    drive(m, lambda: len(runtime.started) == 2)
    # The new session may spend what's left: $2 minus the $1.20 already spent.
    assert task_env(runtime.started[1])["max_budget_usd"] == pytest.approx(0.8)
    assert task_env(runtime.started[1])["resume"] == "sdk-1"

    answer(cap_question(task_id), "stop")
    stopped = task(task_id)
    assert stopped.status is TaskStatus.STOPPED
    assert stopped.error == "stopped at its spending cap by owner"


@pytest.mark.usefixtures("task_registry")
def test_a_task_gets_the_default_cap_and_an_overrun_still_pauses() -> None:
    m, runtime = manager(lambda _spec: Script(after=[result_line(cost=3.5)]))
    task_id = run_task()
    assert task(task_id).budget_usd == 1.0  # the default cap
    m.tick()
    m.wait_idle()
    assert task(task_id).status is TaskStatus.NEEDS_INPUT
    assert task_env(runtime.started[0])["max_budget_usd"] == 1.0


@pytest.mark.usefixtures("task_registry")
def test_effort_follows_the_task_size_up_to_the_setting(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RINGKEEPER_EFFORT_WORKER", "medium")
    settings = get_settings()
    assert session_effort(settings, SessionRole.WORKER, Complexity.LOW) == "low"
    assert session_effort(settings, SessionRole.WORKER, Complexity.HIGH) == "medium"  # capped
    assert session_effort(settings, SessionRole.WORKER, None) == "medium"  # no size: the setting
    assert session_effort(settings, SessionRole.ARCHITECT, Complexity.HIGH) == "high"
    m, runtime = manager()
    run_task()
    m.tick()
    m.wait_idle()
    assert task_env(runtime.started[0])["effort"] == "medium"


@pytest.mark.usefixtures("task_registry")
def test_budget_needs_api_key_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RINGKEEPER_CLAUDE_AUTH_MODE", "subscription")
    with pytest.raises(RuleViolationError, match="only applies in api_key mode"):
        run_task(budget_usd=1.0)


@pytest.mark.usefixtures("task_registry")
def test_max_turns_is_reported() -> None:
    m, _ = manager(lambda _spec: Script(after=[result_line(subtype="error_max_turns", turns=5)]))
    task_id = run_task(max_turns=5)
    m.tick()
    m.wait_idle()
    done = task(task_id)
    assert done.status is TaskStatus.FAILED
    assert done.error == "reached the max of 5 turns"


@pytest.mark.usefixtures("task_registry")
def test_limits_are_validated() -> None:
    with pytest.raises(RuleViolationError, match="max runtime"):
        run_task(max_runtime_s=10 * 24 * 3600)
    with pytest.raises(RuleViolationError, match="prompt is empty"):
        run_task(prompt="  ")


# --- Resume ---


@pytest.mark.usefixtures("task_registry")
def test_resume_continues_the_same_sdk_session() -> None:
    m, runtime = manager()
    task_id = run_task()
    m.tick()
    m.wait_idle()
    with session_scope() as session:
        tasks.resume_task(session, get_settings(), task_id, "add more notes")
    assert task(task_id).status is TaskStatus.QUEUED
    m.tick()
    m.wait_idle()
    second = session_of(task_id, 2)
    assert second.status is TaskStatus.SUCCEEDED
    config = task_env(runtime.started[1])
    assert config["resume"] == "sdk-1"
    assert config["prompt"] == "add more notes"
    assert config["session_ordinal"] == 2
    assert runtime.started[1].state_volume == runtime.started[0].state_volume
    assert revoked(second.token_jti)
    assert session_of(task_id, 1).token_jti != second.token_jti


@pytest.mark.usefixtures("task_registry")
def test_resume_needs_a_finished_task() -> None:
    task_id = run_task()
    with (
        session_scope() as session,
        pytest.raises(RuleViolationError, match="wait for it to finish"),
    ):
        tasks.resume_task(session, get_settings(), task_id, "more")


# --- Failures and safety ---


@pytest.mark.usefixtures("task_registry")
def test_missing_credential_fails_without_starting_a_worker() -> None:
    settings = get_settings()
    get_vault().store(settings.claude_api_key_ref, "not-a-key")
    m, runtime = manager()
    task_id = run_task()
    m.tick()
    m.wait_idle()
    done = task(task_id)
    assert done.status is TaskStatus.FAILED
    assert done.error is not None
    assert "isn't an Anthropic API key" in done.error
    assert runtime.started == []


@pytest.mark.usefixtures("task_registry")
def test_start_failure_still_revokes_the_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RINGKEEPER_WORKER_IMAGE", "missing-image")
    m, _ = manager()
    task_id = run_task()
    m.tick()
    m.wait_idle()
    assert task(task_id).status is TaskStatus.FAILED
    assert revoked(session_of(task_id).token_jti)


@pytest.mark.usefixtures("task_registry")
def test_secrets_never_reach_session_events() -> None:
    def leaky(spec: WorkerSpec) -> Script:
        token = spec.env["RINGKEEPER_GATEWAY_TOKEN"]
        return Script(
            before=[line("sdk.user", message=f"env says {API_KEY} and {token}")],
            after=[result_line(), workspace_line([])],
        )

    m, _ = manager(leaky)
    task_id = run_task()
    m.tick()
    m.wait_idle()
    with session_scope() as session:
        payloads = session.scalars(
            select(SessionEvent.payload).where(SessionEvent.session_id == session_of(task_id).id)
        ).all()
    text = json.dumps(payloads)
    assert API_KEY not in text
    assert "[redacted]" in text


@pytest.mark.usefixtures("task_registry")
def test_recover_marks_orphaned_sessions_failed() -> None:
    with session_scope() as session:
        task_id = str(tasks.create_task(session, get_settings(), project="acme-web", prompt="x").id)
    with session_scope() as session:
        tasks.claim_next(session)  # running, but no manager owns it
        row = session.scalars(select(WorkerSession)).one()
        row.token_jti = "orphan-jti"
    m, _ = manager()
    m.recover()
    done = task(task_id)
    assert done.status is TaskStatus.FAILED
    assert done.error == "the session manager restarted during this run"
    assert revoked("orphan-jti")
    with session_scope() as session:
        assert session.get(TokenRevocation, "orphan-jti") is not None


# --- Paths ---


@pytest.mark.parametrize(
    ("path", "root", "expected"),
    [
        ("/abs/acme", None, "/abs/acme"),
        ("acme-web", "/srv/ws", "/srv/ws/acme-web"),
        ("acme-web", "C:\\Users\\me\\ws", "C:\\Users\\me\\ws\\acme-web"),
        ("C:\\work\\acme", "/ignored", "C:\\work\\acme"),
    ],
)
def test_resolve_workspace(path: str, root: str | None, expected: str) -> None:
    assert resolve_workspace(path, root) == expected


def test_relative_workspace_without_root_is_an_error() -> None:
    with pytest.raises(Exception, match="RINGKEEPER_WORKSPACES_ROOT"):
        resolve_workspace("acme-web", None)


@pytest.mark.parametrize("path", ["../etc", "acme/../../etc", "/srv/../etc", "C:\\work\\..\\x"])
def test_workspace_paths_cant_climb_out(path: str) -> None:
    with pytest.raises(Exception, match=r"can't contain '\.\.'"):
        resolve_workspace(path, "/srv/workspaces")


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("C:\\Users\\me\\ws\\acme", "/run/desktop/mnt/host/c/Users/me/ws/acme"),
        ("D:/data/x", "/run/desktop/mnt/host/d/data/x"),
        ("/srv/ws/acme", "/srv/ws/acme"),
    ],
)
def test_daemon_path(path: str, expected: str) -> None:
    assert daemon_path(path) == expected


def test_settings_defaults() -> None:
    settings = Settings()
    assert settings.max_concurrent_workers == 4
    assert settings.session_manager is False


@pytest.mark.usefixtures("task_registry")
def test_finished_tasks_state_volumes_are_swept_after_retention() -> None:
    m, runtime = manager()
    old, recent, running = run_task(), run_task(), run_task()
    m.tick()
    m.wait_idle()
    other_install = "ringkeeper-state-00000000-0000-0000-0000-000000000000-worker"
    runtime.volumes = {
        *(tasks.state_volume_name(uuid.UUID(t), SessionRole.WORKER) for t in (old, recent)),
        tasks.state_volume_name(uuid.UUID(running), SessionRole.WORKER),
        other_install,
    }
    with session_scope() as session:
        long_ago = datetime.now(UTC) - timedelta(days=get_settings().retention_days + 1)
        session.get_one(Task, old).finished_at = long_ago
        session.get_one(Task, running).status = TaskStatus.RUNNING
        session.get_one(Task, running).finished_at = long_ago
    assert m.sweep_state_volumes() == 1
    assert tasks.state_volume_name(uuid.UUID(old), SessionRole.WORKER) not in runtime.volumes
    assert len(runtime.volumes) == 3  # recent, still running, and another install's
