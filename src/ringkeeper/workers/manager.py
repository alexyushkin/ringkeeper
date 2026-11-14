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
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any, cast

from sqlalchemy.orm import Session

from ringkeeper import claude_failures, scrub
from ringkeeper.adapters import ADAPTERS, agent_name
from ringkeeper.channels import links
from ringkeeper.config import Settings
from ringkeeper.db import session_scope
from ringkeeper.errors import RingkeeperError
from ringkeeper.listen import start_listener
from ringkeeper.model_choice import session_effort
from ringkeeper.models import (
    TERMINAL_STATUSES,
    AuthMode,
    Autonomy,
    Complexity,
    DecisionKind,
    SessionRole,
    Task,
    TaskStatus,
    WorkerSession,
)
from ringkeeper.services import (
    agents,
    architect,
    architect_loop,
    claude_place,
    cleanup,
    connection_note,
    drafts,
    goes_on,
    holds,
    inbox,
    interrupted,
    link_offer,
    memory,
    projects,
    revocations,
    setup_check,
    subscription,
    tasks,
    web_access,
)
from ringkeeper.services.runtime import current_settings
from ringkeeper.tokens import TokenRole, issue_token, model_token_id
from ringkeeper.vault import Vault
from ringkeeper.workers.env import worker_env
from ringkeeper.workers.runtime import (
    LABEL_SESSION,
    LABEL_TASK,
    STATE_PREFIX,
    ContainerRuntime,
    WorkerSpec,
)

logger = logging.getLogger(__name__)

# Tools removed from the agent's context by project autonomy, and the web's unless the
# project allows it (services.web_access). The container is the sandbox either way; these
# narrow what the agent can try inside it.
DISALLOWED_TOOLS = {
    Autonomy.STRICT: ["Bash"],
    Autonomy.STANDARD: [],
    Autonomy.RELAXED: [],
}
# Claude Code's own tools no worker uses: each one's definition is in every request, and
# together with the list of Claude Code's bundled skills (it goes with Skill) they were more
# than half of a data task's first request (docs/developers.md, "A worker's first request").
UNUSED_TOOLS = ["Workflow", "ScheduleWakeup", "ReportFindings", "Skill", "ListAgents"]
# Subagents help with work on code; anywhere else they're one more definition to send.
SUBAGENT_TOOLS = ["Agent", "Task"]
MAX_EVENT_BYTES = 64 * 1024
REVOKED_BY = "session-manager"
VOLUME_SWEEP_SECONDS = 3600


# A task's last message is its result, kept whole. Past this (a runaway result), it's cut
# at a line break and says where the rest is: the transcript keeps a result event up to
# RESULT_EVENT_BYTES whole.
SUMMARY_CHARS = 200_000
RESULT_EVENT_BYTES = 2 * 1024 * 1024
CUT_HERE = "Cut here. The full result is in the transcript"


def tool_calls(message: dict[str, Any]) -> int:
    """How many tools an assistant message calls."""
    content = message.get("content")
    blocks = cast(list[Any], content) if isinstance(content, list) else []
    kinds = [cast(dict[str, Any], b).get("_kind") for b in blocks if isinstance(b, dict)]
    return kinds.count("ToolUseBlock")


def kept_result(said: str, link: str) -> str:
    """The whole result; a runaway one cut at a line break, saying where the rest is."""
    said = said.strip()
    if len(said) <= SUMMARY_CHARS:
        return said
    end = said.rfind("\n", 0, SUMMARY_CHARS)
    head = said[: end if end > 0 else SUMMARY_CHARS].rstrip()
    return f"{head}\n\n{CUT_HERE}: [open the task]({link})."


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
    feature: bool = False  # the task runs the architect loop
    complexity: Complexity | None = None
    task_budget_usd: float | None = None  # the whole task's cap; budget_usd is what's left
    estimated: bool = False  # the cap is an estimate: a subscription isn't billed per task

    @classmethod
    def of(cls, row: WorkerSession) -> "Job":
        task = row.task
        spent = sum(s.cost_usd or 0 for s in task.sessions if s.id != row.id)
        cap = task.budget_usd if task.budget_usd is not None else task.estimate_cap_usd
        left = None if cap is None else max(cap - spent, 0.01)
        return cls(
            session_id=row.id,
            task_id=task.id,
            ordinal=row.ordinal,
            prompt=row.prompt,
            project=task.project,
            auth_mode=task.auth_mode,
            max_turns=task.max_turns,
            max_runtime_s=task.max_runtime_s,
            budget_usd=left,
            sdk_session_id=row.sdk_session_id,
            state_volume=row.state_volume,
            started_at=row.started_at or datetime.now(UTC),
            role=row.role,
            model=row.model,
            feature=task.phase is not None,  # architect-led (tasks.planned)
            complexity=task.complexity,
            task_budget_usd=cap,
            estimated=task.budget_usd is None and cap is not None,
        )


@dataclass
class Collected:
    """What the watcher learned from a worker's stream."""

    result: dict[str, Any] | None = None
    workspace: dict[str, Any] | None = None
    errors: list[str] | None = None
    exit_code: int | None = None
    question: str | None = None  # the worker called ask_user
    claude_error: str | None = None  # an assistant message's error type (rate_limit, ...)
    not_done: str | None = None  # the worker called not_done: why
    partly: str | None = None  # ...and what it did finish
    needs: str = ""  # the kind of connection it said it lacked
    checks: dict[str, Any] | None = None  # architect loop: this worker turn's checks
    diff: str | None = None


REPORT = "Try again; if it happens again, use Report a problem on the Help page."
# What a task is told when it's run again after Claude's side failed.
CONTINUE = goes_on.CONTINUE
NO_RESULT = "The task stopped without finishing. Send a follow-up to continue, or try again."


def resolve_workspace(path: str | None, root: str | None) -> str:
    if not path:
        raise RingkeeperError("This project has no folder. Set one on the project's page.")
    if ".." in PurePosixPath(path).parts or ".." in PureWindowsPath(path).parts:
        raise RingkeeperError(f"The project's folder {path} can't contain '..'. Pick another.")
    if PurePosixPath(path).is_absolute() or PureWindowsPath(path).is_absolute():
        return path
    if not root:
        raise RingkeeperError(
            f"The project's folder {path} isn't a full path, and there's no workspaces folder "
            "to put it in. Set the project's folder to a full path."
        )
    separator = "\\" if PureWindowsPath(root).drive else "/"
    return root.rstrip("/\\") + separator + path.replace("/", separator)


def _cap(payload: dict[str, Any]) -> dict[str, Any]:
    text = json.dumps(payload, default=str)
    most = RESULT_EVENT_BYTES if payload.get("type") == "sdk.result" else MAX_EVENT_BYTES
    if len(text) <= most:
        return payload
    return {"type": payload.get("type"), "truncated": True, "preview": text[:4000]}


def claude_failure(collected: Collected) -> claude_failures.Failure | None:
    """The session failed on Claude's side: a limit, the login, overload, the network. From
    the SDK's own signals and Claude Code's own words only: not the worker's errors, which
    can quote a service's ("Airtable rate limit reached")."""
    result = collected.result or {}
    if result and not result.get("is_error") and collected.exit_code == 0:
        return None
    text = "\n".join([str(result.get("result") or ""), *map(str, result.get("errors") or [])])
    status = result.get("api_error_status")
    return claude_failures.classify(
        text, status=status if isinstance(status, int) else None, error=collected.claude_error
    )


def outcome_status(  # noqa: PLR0911 (one return per outcome)
    job: Job, collected: Collected, killed: TaskStatus | None
) -> tuple[TaskStatus, str | None]:
    if killed is TaskStatus.TIMED_OUT:
        minutes = max(1, round(job.max_runtime_s / 60))
        return killed, (
            f"Stopped at its time limit ({minutes} min). Send a follow-up to continue, or "
            "ask for a smaller piece of the work."
        )
    if killed is TaskStatus.STOPPED:
        return killed, "Stopped, as you asked."
    if collected.question:
        return TaskStatus.NEEDS_INPUT, None
    result = collected.result
    errors = "; ".join(collected.errors or [])
    if result is None:
        return (
            TaskStatus.FAILED,
            errors or NO_RESULT,
        )
    subtype = str(result.get("subtype", ""))
    cost = result.get("total_cost_usd")
    over_budget = (
        job.budget_usd is not None and isinstance(cost, int | float) and cost > job.budget_usd
    )
    if subtype == "error_max_budget_usd" or over_budget:
        estimate = ", estimated" if job.estimated else ""
        return TaskStatus.BUDGET_EXCEEDED, (
            f"Stopped at its spending cap (${job.budget_usd or 0:.2f}{estimate}). Send a "
            "follow-up to continue."
        )
    if subtype == "success" and not result.get("is_error") and collected.exit_code == 0:
        if collected.not_done:  # it ran, but said it couldn't do it: never "done"
            return TaskStatus.NOT_DONE, collected.not_done
        return TaskStatus.SUCCEEDED, None
    if subtype == "error_max_turns":
        return TaskStatus.FAILED, (
            f"Stopped at the step limit ({job.max_turns} steps). Raise it in Settings > "
            "Advanced, or send a follow-up to continue."
        )
    return (
        TaskStatus.FAILED,
        errors or f"The task ended with an error. {REPORT}",
    )


def _save_workspace(task: Task, workspace: dict[str, Any]) -> None:
    """Its branch and the files it changed, and what happened to files, when it says."""
    if workspace.get("branch"):
        task.git_branch = workspace["branch"]
        task.git_commit = workspace.get("commit")
    changed = cast(list[str], workspace.get("changed_files") or [])
    if workspace.get("git"):
        task.changed_files = changed  # the branch's diff is cumulative
    else:
        task.changed_files = sorted(set(task.changed_files) | set(changed))
    if note := workspace.get("note"):
        task.folder_note = str(note)


def _save_result(row: WorkerSession, result: dict[str, Any]) -> None:
    """What the SDK's result says about a session: turns, cost, tokens, its conversation."""
    row.result_subtype = result.get("subtype")
    row.turns = result.get("num_turns")
    cost = result.get("total_cost_usd")
    row.cost_usd = float(cost) if isinstance(cost, int | float) else None
    row.usage = result.get("usage")
    row.model_usage = result.get("model_usage")
    row.sdk_session_id = result.get("session_id") or row.sdk_session_id


class _PausedMeanwhile(Exception):  # noqa: N818 (an outcome, not an error)
    """Claude's limit was reached while a tick was claiming the next session."""


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
        self.retry_scale = 1.0  # tests: 0, so a retry after Claude's side failed is at once

    @property
    def settings(self) -> Settings:
        # The effective settings (.env plus the Settings screen), re-read each time,
        # so concurrency and "Stop everything" apply without a restart.
        return self._fixed_settings or current_settings()

    # --- Loop ---

    def run_forever(self, stop: threading.Event) -> None:
        self.recover()
        start_listener(self.settings, [tasks.NOTIFY_CHANNEL], self._on_notify, stop, "rk-listen")
        swept = 0.0
        while not stop.is_set():
            try:
                self.tick()
                if time.monotonic() - swept > VOLUME_SWEEP_SECONDS:
                    swept = time.monotonic()
                    self.sweep_state_volumes()
            except Exception:
                logger.exception("session manager tick failed")
            self._wake.wait(self.settings.manager_poll_seconds)
            self._wake.clear()

    def wake(self) -> None:
        self._wake.set()

    def sweep_state_volumes(self) -> int:
        """Remove the SDK state (agent transcripts, so client data) of tasks that
        finished more than `retention_days` ago. Only tasks in this install's
        database: another install's volumes on the same Docker are left alone."""
        cutoff = datetime.now(UTC) - timedelta(days=self.settings.retention_days)
        removed = 0
        with session_scope() as session:
            for name in self.runtime.state_volumes():
                try:
                    task_id = uuid.UUID(name.removeprefix(STATE_PREFIX)[:36])
                except ValueError:
                    continue
                task = session.get(Task, task_id)
                if task is None or task.status not in TERMINAL_STATUSES:
                    continue
                expired = task.finished_at is not None and task.finished_at < cutoff
                if expired and self.runtime.remove_volume(name):
                    removed += 1
        if removed:
            logger.info("removed %d finished tasks' state volumes", removed)
        return removed

    def _on_notify(self, _channel: str, _payload: str) -> None:
        self._wake.set()

    def tick(self, max_new: int | None = None) -> None:
        """Start queued sessions up to the concurrency cap (or `max_new` of them:
        tests that drive the loop step by step)."""
        self._enforce_limits()
        settings = self.settings
        with session_scope() as session:
            holds.resume_decided(session, settings)
            gone = cleanup.take_pending_state(session)
        for name in gone:  # deleted tasks' transcripts
            self.runtime.remove_volume(name)
        if settings.paused:  # "Stop everything": nothing new starts until resumed
            return
        with session_scope(commit=False) as session:
            if claude_failures.paused(session) is not None:  # until Claude's limit resets
                return
        cap = settings.max_concurrent_workers
        started = 0
        while max_new is None or started < max_new:
            started += 1
            with self._lock:
                if len(self._active) >= cap:
                    return
            try:
                with session_scope() as session:
                    row = tasks.claim_next(session)
                    # A run that hit Claude's limit while this tick was getting here: whatever
                    # queued this session was committed after the pause, so the pause is seen
                    # now. The claim is undone and the session stays queued.
                    if row is not None and claude_failures.paused(session) is not None:
                        raise _PausedMeanwhile
                    job = Job.of(row) if row else None
            except _PausedMeanwhile:
                return
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
                job,
                jti,
                Collected(errors=[interrupted.ERROR]),
                None,
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
            cast(list[str], collected.errors).append(
                f"Something went wrong inside Ringkeeper ({type(error).__name__}). {REPORT}"
            )
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
        with session_scope(commit=False) as session:
            checking = setup_check.from_check(session, session.get(Task, job.task_id))
        if not checking:
            memory.follow_new(job.project)  # Notion for every project: a new one follows
        with session_scope() as session:
            project = projects.get_project(session, job.project)
            workspace = resolve_workspace(project.workspace_path, settings.workspaces_root)
            autonomy = project.autonomy
            web = web_access.allowed(session, project)
            checks = list(project.checks)
            task_row = session.get(Task, job.task_id)
            read_only = setup_check.from_check(session, task_row)
            agent = agents.of_task(session, task_row) if task_row is not None else None
            # Work on the code: on a branch. Anything else leaves git alone.
            # Check my setup only reads: no branch in the project's repository either.
            uses_git = tasks.on_branch(task_row) and not read_only if task_row is not None else True
            # Its own branch, or its conversation's earlier one in this project.
            branch = (
                tasks.task_branch(session, task_row)
                if task_row is not None
                else tasks.branch_name(job.task_id)
            )
            # Only the services this project (or its agent) is connected to: every other
            # endpoint would refuse it anyway, and each one's tools crowd the context.
            served = {
                b.instance_type for b in project.bindings
                if b.instance_type in ADAPTERS
                and (agent is None or b.instance.name in agent.connections)
            }  # fmt: skip
            prompt, model, effort = job.prompt, job.model, None
            if agent is not None:  # its instructions, and its own model, effort, autonomy
                prompt = agents.instructions_for(agent, job.prompt)
                model = agent.model or model
                effort = agent.effort
                autonomy = Autonomy(agent.autonomy) if agent.autonomy else autonomy
            if job.role is not SessionRole.ARCHITECT:
                # Which connections it has, so its first call names one (connection_note).
                told = connection_note.note(project, agent)
                # What to keep for later runs (not when it only reads), and the latest notes.
                # A resumed session has them already.
                kept = (
                    memory.project_block(session, project, agent, saving=not read_only)
                    if job.sdk_session_id is None
                    else ""
                )
                prompt = "\n\n".join([
                    prompt, *([told] if told else []), *([kept] if kept else []),
                    *memory.hints(session, project, agent, saving=not read_only),
                ])  # fmt: skip
            # Memory is every worker's: it's pinned to the token's project.
            endpoints = sorted({"memory", *(agent_name(service) for service in served)})
        architect_role = job.role is SessionRole.ARCHITECT
        subscription.refresh(self.vault, settings)  # the Claude Code login's newer token
        # Whichever is configured now (the Settings screen's mode), not what was set
        # when the task was queued.
        with session_scope(commit=False) as session:
            # Fails early when Claude isn't connected, and registers a cloud's secrets.
            claude_place.credential(session, self.vault, settings, job.project)
            place = claude_place.chosen(session, job.project)
        model = claude_place.model_for(place, model)
        token, claims = issue_token(
            job.project,
            timedelta(seconds=job.max_runtime_s),
            session_id=str(job.session_id),
            role=TokenRole.ARCHITECT if architect_role else TokenRole.WORKER,
            settings=settings,
        )
        model_token, _ = issue_token(
            job.project,
            timedelta(seconds=job.max_runtime_s),
            session_id=str(job.session_id),
            role=TokenRole.MODEL,
            settings=settings,
            token_id=model_token_id(claims.token_id),
        )
        scrub.register(token, model_token)  # not in events, even if the agent prints env
        base = settings.gateway_internal_url.rstrip("/")
        task: dict[str, Any] = {
            "task_id": str(job.task_id),
            "session_ordinal": job.ordinal,
            "prompt": prompt,
            "max_turns": job.max_turns,
            "branch": branch,
            "git": uses_git,
            "mcp_servers": (
                {"ringkeeper-architect": f"{base}/mcp/architect"}
                if architect_role
                else {f"ringkeeper-{name}": f"{base}/mcp/{name}" for name in endpoints}
            ),
            "disallowed_tools": (
                [*architect.DISALLOWED_TOOLS, *UNUSED_TOOLS]
                if architect_role
                else [
                    *DISALLOWED_TOOLS[autonomy],
                    *UNUSED_TOOLS,
                    *([] if uses_git else SUBAGENT_TOOLS),
                    *([] if web else web_access.TOOLS),
                    *(setup_check.READ_ONLY_TOOLS if read_only else ()),
                ]
            ),
            "role": job.role.value,
            "checks": checks if job.feature and not architect_role else [],
            # On a subscription the cap is the estimated cost: Claude Code stops there too.
            "max_budget_usd": job.budget_usd,
            "resume": job.sdk_session_id,
            "model": model,
            "effort": effort or session_effort(settings, job.role, job.complexity),
        }
        if settings.worker_stub:
            task["stub"] = json.loads(settings.worker_stub)
        spec = WorkerSpec(
            name=f"ringkeeper-worker-{job.session_id}",
            image=settings.worker_image,
            env=worker_env(claude_place.worker_vars(place, base, model_token), task, token),
            workspace=workspace,
            state_volume=job.state_volume,
            network=settings.worker_network,
            labels={LABEL_SESSION: str(job.session_id), LABEL_TASK: str(job.task_id)},
            memory=settings.worker_memory,
            extra_hosts=settings.worker_extra_hosts,
            workspace_read_only=architect_role,
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
            called = 0
            if event_type == "sdk.result" and isinstance(event.get("message"), dict):
                collected.result = cast(dict[str, Any], event["message"])
            elif event_type == "sdk.assistant" and isinstance(event.get("message"), dict):
                said = cast(dict[str, Any], event["message"]).get("error")
                collected.claude_error = str(said) if said else collected.claude_error
                called = tool_calls(cast(dict[str, Any], event["message"]))
            elif event_type == "ringkeeper.workspace":
                collected.workspace = event
            elif event_type == "ringkeeper.checks":
                collected.checks = {
                    "passed": bool(event.get("passed")),
                    "results": event.get("results", []),
                }
            elif event_type == "ringkeeper.diff":
                collected.diff = str(event.get("text") or "")
            elif event_type == "ringkeeper.ask_user" and not collected.question:
                collected.question = str(event.get("question", "")) or None
            elif event_type == "ringkeeper.not_done" and not collected.not_done:
                collected.not_done = str(event.get("reason", "")) or None
                collected.partly = str(event.get("done") or "").strip() or None
                collected.needs = str(event.get("needs", ""))
            elif event_type == "ringkeeper.error":
                cast(list[str], collected.errors).append(str(event.get("message", "")))
            seq += 1
            with session_scope() as session:
                tasks.add_event(session, job.session_id, seq, event_type, _cap(event))
                if called:  # the task's Steps, as they happen
                    tasks.count_tool_calls(session, job.session_id, called)

    def _pause_at_cap(
        self, session: Session, job: Job, row: WorkerSession, task: Task
    ) -> TaskStatus:
        """At the spending cap the task waits for the user: more money, or stop."""
        step = self.settings.task_budget_usd
        task.status = row.status = TaskStatus.NEEDS_INPUT
        task.finished_at = None
        task.error = None
        cap = job.task_budget_usd or 0
        question = (
            f"This task reached its spending cap (${cap:.2f}, estimated: a subscription isn't "
            f"billed per task). Let it go on for another ${step:.2f}, or stop it here?"
            if job.estimated
            else f"This task reached its spending cap (${cap:.2f}). Give it another "
            f"${step:.2f} to finish, or stop it here?"
        )
        payload = {
            "question": question,
            "budget": step,
            "role": job.role.value,
            "options": ["continue", "stop"],
        }
        inbox.create(
            session, self.settings, DecisionKind.QUESTION, payload, project=task.project, task=task
        )
        return TaskStatus.NEEDS_INPUT

    def _on_claude_failure(
        self,
        session: Session,
        job: Job,
        row: WorkerSession,
        task: Task,
        failure: claude_failures.Failure,
    ) -> tuple[TaskStatus, float | None]:
        """A usage limit: the task waits for the reset (nothing new starts until then). A
        transient failure: tried again after a wait, a few times. A login: it says so.
        Returns the status, and the wait before trying again (once this is committed)."""
        row.error = task.error = failure.message
        if failure.kind is claude_failures.Kind.LOGIN:
            return TaskStatus.FAILED, None
        tries = sum(1 for s in task.sessions if s.error == failure.message)
        if failure.kind is claude_failures.Kind.USAGE_LIMIT:
            claude_failures.pause_until(session, failure)
        elif tries > len(claude_failures.RETRY_WAITS):
            return TaskStatus.FAILED, None  # tried enough: it says what happened
        wait = (
            0.0
            if failure.kind is claude_failures.Kind.USAGE_LIMIT
            else claude_failures.RETRY_WAITS[min(tries, len(claude_failures.RETRY_WAITS)) - 1]
        )
        task.status = TaskStatus.QUEUED
        task.finished_at = None
        return TaskStatus.QUEUED, wait * self.retry_scale

    def _try_again(self, task_id: uuid.UUID) -> None:
        """Run a task again where it left off, after Claude's side failed."""
        try:
            with session_scope() as session:
                task = session.get(Task, task_id)
                if task is None or task.status is not TaskStatus.QUEUED or task.stop_requested:
                    return
                task.status = TaskStatus.FAILED  # resumable, for resume_task
                tasks.resume_task(session, self.settings, str(task_id), CONTINUE)
                if pause := claude_failures.paused(session):  # why it's waiting, on its card
                    task.error = pause.message
        except RingkeeperError as error:
            logger.warning("couldn't try task %s again: %s", task_id, error)
        self._wake.set()

    def _finalize(
        self, job: Job, jti: str | None, collected: Collected, killed: TaskStatus | None
    ) -> None:
        retry = self._record(job, jti, collected, killed)
        if retry is not None:  # it reads the queued task: only once that's committed
            threading.Timer(retry, self._try_again, args=(job.task_id,)).start()

    def _record(
        self, job: Job, jti: str | None, collected: Collected, killed: TaskStatus | None
    ) -> float | None:
        """How the session ended, saved. Returns the wait before trying it again, if any."""
        status, error = outcome_status(job, collected, killed)
        result = collected.result or {}
        workspace = collected.workspace or {}
        now = datetime.now(UTC)
        retry: float | None = None  # tried again after this wait, once committed
        with session_scope() as session:
            row = session.get(WorkerSession, job.session_id)
            if row is not None:
                row.status = status
                row.finished_at = now
                row.exit_code = collected.exit_code
                row.error = error
                _save_result(row, result)
                task = row.task
                task.status = status
                task.finished_at = now
                task.error = error
                _save_workspace(task, workspace)
                if job.feature:
                    row.checks = collected.checks
                # Held calls first: the session is resumed once the user decides them.
                waiting = holds.session_ended(session, self.settings, row, status)
                # What it said at the end is the task's result (a feature's comes from
                # the architect), shown on its card, its routine run and in channels. Kept
                # while held calls wait too: it's the answer, and what the session that goes
                # on after them says goes below it (services.goes_on).
                said = result.get("result")
                if not job.feature and isinstance(said, str) and said.strip():
                    kept = kept_result(said, links.task_page(self.settings, task.id))
                    kept = goes_on.result(task.summary, kept, row)
                    task.summary = drafts.linked(session, task, kept)  # where its drafts are
                if status is TaskStatus.NOT_DONE:
                    task.offer = link_offer.find(session, task, collected.needs)
                # A run that did part of it: "Partly done" (a later run that finishes clears it).
                task.partly_done = collected.partly if status is TaskStatus.NOT_DONE else None
                if job.feature and not waiting:
                    review = {
                        "checks": collected.checks,
                        "diff": collected.diff,
                        "result": result.get("result"),
                        "error": error,
                    }
                    architect_loop.advance(session, self.settings, row, status, review)
                if status is TaskStatus.BUDGET_EXCEEDED and job.task_budget_usd is not None:
                    status = self._pause_at_cap(session, job, row, task)
                failure = claude_failure(collected) if status is TaskStatus.FAILED else None
                if failure is not None and not job.feature:
                    status, retry = self._on_claude_failure(session, job, row, task, failure)
                if status is TaskStatus.NEEDS_INPUT and not waiting and collected.question:
                    payload = {"question": collected.question, "role": job.role.value}
                    inbox.create(
                        session,
                        self.settings,
                        DecisionKind.QUESTION,
                        payload,
                        project=task.project,
                        task=task,
                    )
            for token_id in (jti, model_token_id(jti)) if jti else ():
                revocations.revoke(
                    session,
                    token_id,
                    reason=f"session {job.session_id} ended ({status})",
                    by=REVOKED_BY,
                    expires_at=job.started_at + timedelta(seconds=job.max_runtime_s),
                )
        return retry
