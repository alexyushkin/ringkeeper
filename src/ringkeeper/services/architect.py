"""The architect loop for feature tasks: plan, approve, build, check, review.

planning       the architect reads the workspace (read-only) and calls submit_plan
awaiting_plan  a plan decision waits for the operator
building       plan approved: the architect calls dispatch_worker; the worker builds
reviewing      after each worker turn the checks run in the worker container, and
               the architect reviews checks + diff: approve_work or continue_worker
done           approved: the branch is ready and the task has a summary

Each architect tool call records one action on the architect's session and
tells it to end its turn; the session manager acts on it when the session
ends, so the architect holds no container or slot while a worker runs. After
MAX_REVIEWS reviews, continue_worker escalates to the operator instead.
"""

import uuid
from typing import Any

from sqlalchemy.orm import Session

from ringkeeper.calls import Caller
from ringkeeper.errors import RuleViolationError
from ringkeeper.models import ArchitectPhase, SessionRole, Task, TaskStatus, WorkerSession

MAX_REVIEWS = 3
DIFF_CHARS = 30_000
CHECK_OUTPUT_CHARS = 4_000
END_TURN = "Recorded. End your turn now with a one-line note; you'll be resumed when it's done."

# Tools an architect never gets: it reads, plans and reviews; the worker writes.
DISALLOWED_TOOLS = [
    "Write", "Edit", "MultiEdit", "NotebookEdit", "Bash", "WebFetch", "WebSearch",
]  # fmt: skip

BRIEF = """\
You are the architect for a feature in the project "{project}". You can read the
workspace at {workspace}, but you can't change it: a worker writes the code on a
branch, and you plan and review. Use the ringkeeper-architect tools; each call
ends your turn, and you are resumed when the next step is ready.

1. Read what you need, then call submit_plan with a one-line title, the plan
   (what changes, where, and how it will be tested) and the worker prompt
   (complete instructions for the worker, who sees only that prompt and the
   workspace). The operator approves or rejects the plan.
2. When you're told the plan is approved, call dispatch_worker with the final
   worker prompt.
3. After each worker turn you get the project's checks output and the diff.
   Call approve_work with a short summary when the work meets the plan and the
   checks pass, or continue_worker with concrete feedback. You have at most
   {max_reviews} reviews; after that the operator decides.

If something only the operator can answer blocks you, call ask_user.

The feature request:
{prompt}
{outcome}"""


def brief(project: str, prompt: str, outcome: str | None, workspace: str = "/workspace") -> str:
    return BRIEF.format(
        project=project,
        workspace=workspace,
        prompt=prompt,
        outcome=f"\nWhat done looks like: {outcome}" if outcome else "",
        max_reviews=MAX_REVIEWS,
    )


def _clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n… [{len(text) - limit} more characters]"


def checks_block(checks: dict[str, Any] | None) -> str:
    results: list[dict[str, Any]] = (checks or {}).get("results", [])
    if not results:
        return "No checks are configured for this project."
    lines: list[str] = []
    for result in results:
        mark = "PASS" if result.get("passed") else "FAIL"
        lines.append(f"[{mark}] {result.get('command')} (exit {result.get('exit_code')})")
        output = str(result.get("output") or "").strip()
        if output and not result.get("passed"):
            lines.append(_clip(output, CHECK_OUTPUT_CHARS))
    return "\n".join(lines)


def review_prompt(
    task: Task,
    worker_status: TaskStatus,
    checks: dict[str, Any] | None,
    diff: str | None,
    worker_result: str | None,
    error: str | None,
) -> str:
    passed = (checks or {}).get("passed", True)
    status = f"ended with {worker_status}" + (f": {error}" if error else "")
    if worker_status is TaskStatus.SUCCEEDED:
        status = "finished"
    parts = [
        f"Worker turn {task.review_round} {status}. This is review {task.review_round} of "
        f"{MAX_REVIEWS}.",
        f"Checks: {'all passed' if passed else 'FAILED'}\n{checks_block(checks)}",
        "Diff of the task branch:\n```diff\n" + _clip(diff or "(no changes)", DIFF_CHARS) + "\n```",
    ]
    if worker_result:
        parts.append("The worker's final message:\n" + _clip(worker_result, 4000))
    if passed:
        parts.append("Call approve_work(summary) if this meets the plan, or continue_worker.")
    else:
        parts.append("The checks fail, so call continue_worker with what to fix.")
    return "\n\n".join(parts)


def _architect_session(session: Session, caller: Caller) -> tuple[WorkerSession, Task]:
    """The running architect session behind a verified architect token, locked."""
    try:
        session_id = uuid.UUID(str(caller.session_id))
    except ValueError:
        raise RuleViolationError("this token isn't tied to an architect session") from None
    row = session.get(WorkerSession, session_id, with_for_update=True)
    if row is None or row.role is not SessionRole.ARCHITECT:
        raise RuleViolationError("this token isn't tied to an architect session")
    task = row.task
    if task.project != caller.project:
        raise RuleViolationError("this token belongs to another project")
    if row.status is not TaskStatus.RUNNING:
        raise RuleViolationError(f"this architect session is {row.status}")
    if row.action is not None:
        raise RuleViolationError(
            f"you already called {row.action['tool']} in this turn; end your turn now"
        )
    return row, task


def _require_phase(task: Task, *phases: ArchitectPhase) -> None:
    if task.phase not in phases:
        raise RuleViolationError(
            f"not now: the task is in the {task.phase} phase "
            f"(this tool is for {', '.join(p.value for p in phases)})"
        )


def _text(value: str, name: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise RuleViolationError(f"{name} is empty")
    return cleaned


def submit_plan(
    session: Session, caller: Caller, *, title: str, plan: str, worker_prompt: str
) -> str:
    row, task = _architect_session(session, caller)
    _require_phase(task, ArchitectPhase.PLANNING)
    row.action = {
        "tool": "submit_plan",
        "title": _text(title, "title").splitlines()[0][:200],
        "plan": _text(plan, "plan"),
        "worker_prompt": _text(worker_prompt, "worker_prompt"),
    }
    session.flush()
    return "Plan submitted for the operator's approval. " + END_TURN


def dispatch_worker(session: Session, caller: Caller, *, prompt: str) -> str:
    row, task = _architect_session(session, caller)
    _require_phase(task, ArchitectPhase.BUILDING)
    if any(s.role is SessionRole.WORKER for s in task.sessions):
        raise RuleViolationError("a worker was already dispatched; use continue_worker")
    row.action = {"tool": "dispatch_worker", "prompt": _text(prompt, "prompt")}
    session.flush()
    return "Worker dispatched. " + END_TURN


def continue_worker(session: Session, caller: Caller, *, feedback: str) -> str:
    row, task = _architect_session(session, caller)
    _require_phase(task, ArchitectPhase.REVIEWING)
    feedback = _text(feedback, "feedback")
    if task.review_round >= MAX_REVIEWS:
        row.action = {"tool": "escalate", "feedback": feedback}
        session.flush()
        return (
            f"That was review {MAX_REVIEWS} of {MAX_REVIEWS}, so the operator has been asked "
            "whether to go on. " + END_TURN
        )
    row.action = {"tool": "continue_worker", "feedback": feedback}
    session.flush()
    return "The worker continues with your feedback. " + END_TURN


def latest_checks(task: Task) -> dict[str, Any] | None:
    workers = [s for s in task.sessions if s.role is SessionRole.WORKER and s.checks is not None]
    return max(workers, key=lambda s: s.ordinal).checks if workers else None


def approve_work(session: Session, caller: Caller, *, summary: str) -> str:
    row, task = _architect_session(session, caller)
    _require_phase(task, ArchitectPhase.REVIEWING)
    if not (latest_checks(task) or {}).get("passed", True):
        raise RuleViolationError(
            "the checks fail on the latest worker turn; call continue_worker with what to fix"
        )
    row.action = {"tool": "approve_work", "summary": _text(summary, "summary")}
    session.flush()
    return "Work approved. " + END_TURN
