"""The session manager: queue, start, watch, time out, stop and record workers.

It runs inside the api service (the only service with the Docker socket).
One manager per deployment. For every queued session it issues a gateway
token scoped to the project (TTL = the task's max runtime), starts a worker
with only the project's workspace and the task's state volume mounted,
stores the worker's JSON lines as session events, and when the worker ends
(however it ends) records the outcome and revokes the token.
"""

import json
import logging
import threading
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any, cast

from ringkeeper import scrub
from ringkeeper.adapters import ADAPTERS
from ringkeeper.claude_auth import resolve as resolve_credential
from ringkeeper.config import Settings, get_settings
from ringkeeper.db import session_scope
from ringkeeper.errors import RingkeeperError
from ringkeeper.listen import start_listener
from ringkeeper.models import (
    AuthMode,
    Autonomy,
    DecisionKind,
    SessionRole,
    TaskStatus,
    WorkerSession,
)
from ringkeeper.services import inbox, projects, revocations, tasks
from ringkeeper.tokens import issue_token
from ringkeeper.vault import Vault
from ringkeeper.workers.env import worker_env
from ringkeeper.workers.runtime import LABEL_SESSION, LABEL_TASK, ContainerRuntime, WorkerSpec

logger = logging.getLogger(__name__)

# Tools removed from the agent's context by project autonomy. The container is
# the sandbox either way; these narrow what the agent can try inside it.
DISALLOWED_TOOLS = {
    Autonomy.STRICT: ["Bash", "WebFetch", "WebSearch"],
    Autonomy.STANDARD: ["WebFetch", "WebSearch"],
    Autonomy.RELAXED: [],
}
MAX_EVENT_BYTES = 64 * 1024
REVOKED_BY = "session-manager"


@dataclass(frozen=True)
class Job:
    session_id: uuid.UUID
    task_id: uuid.UUID
    ordinal: int
    prompt: str
    project: str
    auth_mode: AuthMode
    max_turns: int
    max_runtime_s: int
    budget_usd: float | None
    sdk_session_id: str | None
    state_volume: str
    started_at: datetime
    role: SessionRole = SessionRole.WORKER
    model: str | None = None

    @classmethod
    def of(cls, row: WorkerSession) -> "Job":
        task = row.task
        return cls(
            session_id=row.id,
            task_id=task.id,
            ordinal=row.ordinal,
            prompt=row.prompt,
            project=task.project,
            auth_mode=task.auth_mode,
            max_turns=task.max_turns,
            max_runtime_s=task.max_runtime_s,
            budget_usd=task.budget_usd,
            sdk_session_id=row.sdk_session_id,
            state_volume=row.state_volume,
            started_at=row.started_at or datetime.now(UTC),
            role=row.role,
            model=row.model,
        )


@dataclass
class Collected:
    """What the watcher learned from a worker's stream."""

    result: dict[str, Any] | None = None
    workspace: dict[str, Any] | None = None
    errors: list[str] | None = None
    exit_code: int | None = None
    question: str | None = None  # the worker called ask_user


def resolve_workspace(path: str | None, root: str | None) -> str:
    if not path:
        raise RingkeeperError("the project has no workspace_path; set one in the registry")
    if PurePosixPath(path).is_absolute() or PureWindowsPath(path).is_absolute():
        return path
    if not root:
        raise RingkeeperError(
            f"workspace_path '{path}' is relative but RINGKEEPER_WORKSPACES_ROOT isn't set"
        )
    separator = "\\" if PureWindowsPath(root).drive else "/"
    return root.rstrip("/\\") + separator + path.replace("/", separator)


def _cap(payload: dict[str, Any]) -> dict[str, Any]:
    text = json.dumps(payload, default=str)
    if len(text) <= MAX_EVENT_BYTES:
        return payload
    return {"type": payload.get("type"), "truncated": True, "preview": text[:4000]}


def outcome_status(  # noqa: PLR0911 (one return per outcome)
    job: Job, collected: Collected, killed: TaskStatus | None
) -> tuple[TaskStatus, str | None]:
    if killed is TaskStatus.TIMED_OUT:
        return killed, f"exceeded the max runtime of {job.max_runtime_s}s; the worker was killed"
    if killed is TaskStatus.STOPPED:
        return killed, "stopped on request"
    if collected.question:
        return TaskStatus.NEEDS_INPUT, None
    result = collected.result
    errors = "; ".join(collected.errors or [])
    if result is None:
        return (
            TaskStatus.FAILED,
            errors or f"the worker exited ({collected.exit_code}) without a result",
        )
    subtype = str(result.get("subtype", ""))
    cost = result.get("total_cost_usd")
    over_budget = (
        job.budget_usd is not None and isinstance(cost, int | float) and cost > job.budget_usd
    )
    if subtype == "error_max_budget_usd" or over_budget:
        return TaskStatus.BUDGET_EXCEEDED, f"reached the budget of ${job.budget_usd}"
    if subtype == "success" and not result.get("is_error") and collected.exit_code == 0:
        return TaskStatus.SUCCEEDED, None
    if subtype == "error_max_turns":
        return TaskStatus.FAILED, f"reached the max of {job.max_turns} turns"
    return TaskStatus.FAILED, errors or f"the agent ended with {subtype or 'an error'}"


class Manager:
    def __init__(
        self,
        runtime: ContainerRuntime,
        vault: Vault,
        settings: Settings | None = None,
    ) -> None:
        self.runtime = runtime
        self.vault = vault
        self._fixed_settings = settings
        self._lock = threading.Lock()
        self._active: set[uuid.UUID] = set()
        self._containers: dict[uuid.UUID, str] = {}
        self._killed: dict[uuid.UUID, TaskStatus] = {}
        self._threads: dict[uuid.UUID, threading.Thread] = {}
        self._wake = threading.Event()

    @property
    def settings(self) -> Settings:
        return self._fixed_settings or get_settings()

    # --- Loop ---

    def run_forever(self, stop: threading.Event) -> None:
        self.recover()
        start_listener(self.settings, [tasks.NOTIFY_CHANNEL], self._on_notify, stop, "rk-listen")
        while not stop.is_set():
            try:
                self.tick()
            except Exception:
                logger.exception("session manager tick failed")
            self._wake.wait(self.settings.manager_poll_seconds)
            self._wake.clear()

    def wake(self) -> None:
        self._wake.set()

    def _on_notify(self, _channel: str, _payload: str) -> None:
        self._wake.set()

    def tick(self) -> None:
        self._enforce_limits()
        cap = self.settings.max_concurrent_workers
        while True:
            with self._lock:
                if len(self._active) >= cap:
                    return
            with session_scope() as session:
                row = tasks.claim_next(session)
                job = Job.of(row) if row else None
            if job is None:
                return
            with self._lock:
                self._active.add(job.session_id)
            thread = threading.Thread(
                target=self._run, args=(job,), daemon=True, name=f"rk-{job.session_id}"
            )
            self._threads[job.session_id] = thread
            thread.start()

    def _enforce_limits(self) -> None:
        now = datetime.now(UTC)
        with session_scope() as session:
            rows = [
                (r.id, r.task.stop_requested, r.started_at, r.task.max_runtime_s)
                for r in tasks.running_sessions(session)
            ]
        for session_id, stop_requested, started_at, max_runtime_s in rows:
            if session_id not in self._containers:
                continue
            if stop_requested:
                self._kill(session_id, TaskStatus.STOPPED)
            elif started_at and started_at + timedelta(seconds=max_runtime_s) <= now:
                self._kill(session_id, TaskStatus.TIMED_OUT)

    def _kill(self, session_id: uuid.UUID, reason: TaskStatus) -> None:
        with self._lock:
            if session_id in self._killed:
                return
            self._killed[session_id] = reason
            container_id = self._containers.get(session_id)
        if container_id:
            self.runtime.kill(container_id)

    def recover(self) -> None:
        """After a restart: workers we no longer watch are killed and marked failed."""
        for container_id in self.runtime.running_workers():
            self.runtime.kill(container_id)
            self.runtime.remove(container_id)
        with session_scope() as session:
            orphans = [Job.of(r) for r in tasks.running_sessions(session)]
        for job in orphans:
            with session_scope() as session:
                row = session.get(WorkerSession, job.session_id)
                jti = row.token_jti if row else None
            self._finalize(
                job, jti, Collected(errors=["the session manager restarted during this run"]), None
            )

    def wait_idle(self, timeout: float = 30.0) -> None:
        """Tests: wait until every started worker has been finalized."""
        for thread in list(self._threads.values()):
            thread.join(timeout)

    # --- One session ---

    def _run(self, job: Job) -> None:
        jti: str | None = None
        collected = Collected(errors=[])
        try:
            spec, jti = self._prepare(job)
            with session_scope() as session:
                row = session.get(WorkerSession, job.session_id)
                if row is not None:
                    row.token_jti = jti
            container_id = self.runtime.start(spec)
            with self._lock:
                self._containers[job.session_id] = container_id
            with session_scope() as session:
                row = session.get(WorkerSession, job.session_id)
                if row is not None:
                    row.container_id = container_id
            # A stop or timeout may have landed while starting.
            self._enforce_limits()
            self._collect(job, container_id, collected)
            collected.exit_code = self.runtime.wait(container_id)
        except RingkeeperError as error:
            cast(list[str], collected.errors).append(str(error))
        except Exception as error:
            logger.exception("worker session %s failed", job.session_id)
            cast(list[str], collected.errors).append(f"internal error: {type(error).__name__}")
        finally:
            with self._lock:
                killed = self._killed.pop(job.session_id, None)
                container_id = self._containers.pop(job.session_id, None)
            try:
                self._finalize(job, jti, collected, killed)
            finally:
                if container_id:
                    self.runtime.remove(container_id)
                with self._lock:
                    self._active.discard(job.session_id)
                self._wake.set()  # a slot is free

    def _prepare(self, job: Job) -> tuple[WorkerSpec, str]:
        settings = self.settings
        with session_scope() as session:
            project = projects.get_project(session, job.project)
            workspace = resolve_workspace(project.workspace_path, settings.workspaces_root)
            autonomy = project.autonomy
        credential = resolve_credential(self.vault, settings, job.auth_mode)
        token, claims = issue_token(
            job.project,
            timedelta(seconds=job.max_runtime_s),
            session_id=str(job.session_id),
            settings=settings,
        )
        scrub.register(token)  # never store it in events, even if the agent prints env
        base = settings.gateway_internal_url.rstrip("/")
        task: dict[str, Any] = {
            "task_id": str(job.task_id),
            "session_ordinal": job.ordinal,
            "prompt": job.prompt,
            "max_turns": job.max_turns,
            "branch": tasks.branch_name(job.task_id),
            "mcp_servers": {f"ringkeeper-{svc}": f"{base}/mcp/{svc}" for svc in ADAPTERS},
            "disallowed_tools": DISALLOWED_TOOLS[autonomy],
            "max_budget_usd": job.budget_usd if job.auth_mode is AuthMode.API_KEY else None,
            "resume": job.sdk_session_id,
            "model": job.model,
        }
        if settings.worker_stub:
            task["stub"] = json.loads(settings.worker_stub)
        spec = WorkerSpec(
            name=f"ringkeeper-worker-{job.session_id}",
            image=settings.worker_image,
            env=worker_env(credential, task, token),
            workspace=workspace,
            state_volume=job.state_volume,
            network=settings.worker_network,
            labels={LABEL_SESSION: str(job.session_id), LABEL_TASK: str(job.task_id)},
            memory=settings.worker_memory,
            extra_hosts=settings.worker_extra_hosts,
        )
        return spec, claims.token_id

    def _collect(self, job: Job, container_id: str, collected: Collected) -> None:
        seq = 0
        for line in self.runtime.lines(container_id):
            if not line.strip():
                continue
            try:
                parsed: Any = json.loads(line)
            except ValueError:
                parsed = None
            event = (
                cast(dict[str, Any], parsed)
                if isinstance(parsed, dict)
                else {"type": "worker.raw", "line": line}
            )
            event = cast(dict[str, Any], scrub.scrub_obj(event))
            event_type = str(event.get("type", "worker.raw"))
            if event_type == "sdk.result" and isinstance(event.get("message"), dict):
                collected.result = cast(dict[str, Any], event["message"])
            elif event_type == "ringkeeper.workspace":
                collected.workspace = event
            elif event_type == "ringkeeper.ask_user" and not collected.question:
                collected.question = str(event.get("question", "")) or None
            elif event_type == "ringkeeper.error":
                cast(list[str], collected.errors).append(str(event.get("message", "")))
            seq += 1
            with session_scope() as session:
                tasks.add_event(session, job.session_id, seq, event_type, _cap(event))

    def _finalize(
        self, job: Job, jti: str | None, collected: Collected, killed: TaskStatus | None
    ) -> None:
        status, error = outcome_status(job, collected, killed)
        result = collected.result or {}
        workspace = collected.workspace or {}
        now = datetime.now(UTC)
        with session_scope() as session:
            row = session.get(WorkerSession, job.session_id)
            if row is not None:
                row.status = status
                row.finished_at = now
                row.exit_code = collected.exit_code
                row.error = error
                row.result_subtype = result.get("subtype")
                row.turns = result.get("num_turns")
                cost = result.get("total_cost_usd")
                row.cost_usd = float(cost) if isinstance(cost, int | float) else None
                row.usage = result.get("usage")
                row.model_usage = result.get("model_usage")
                row.sdk_session_id = result.get("session_id") or row.sdk_session_id
                task = row.task
                task.status = status
                task.finished_at = now
                task.error = error
                if workspace.get("branch"):
                    task.git_branch = workspace["branch"]
                    task.git_commit = workspace.get("commit")
                changed = cast(list[str], workspace.get("changed_files") or [])
                if workspace.get("git"):
                    task.changed_files = changed  # the branch's diff is cumulative
                else:
                    task.changed_files = sorted(set(task.changed_files) | set(changed))
                if status is TaskStatus.NEEDS_INPUT:
                    payload = {"question": collected.question, "role": job.role.value}
                    inbox.create(
                        session,
                        self.settings,
                        DecisionKind.QUESTION,
                        payload,
                        project=task.project,
                        task=task,
                    )
            if jti:
                revocations.revoke(
                    session,
                    jti,
                    reason=f"session {job.session_id} ended ({status})",
                    by=REVOKED_BY,
                    expires_at=job.started_at + timedelta(seconds=job.max_runtime_s),
                )
