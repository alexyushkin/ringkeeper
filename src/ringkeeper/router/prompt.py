"""The router's prompt: fixed instructions plus everything it may route on.

The router is stateless: each message is routed from scratch with the
project cards, the thread's recent messages, open tasks, pending decisions
and saved routing examples. It never sees secrets or instance credentials.
"""

import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ringkeeper.config import Settings
from ringkeeper.models import (
    TERMINAL_STATUSES,
    Decision,
    DecisionStatus,
    Message,
    Project,
    Task,
)
from ringkeeper.router.schema import json_schema
from ringkeeper.services import examples, threads

SNIPPET_CHARS = 160

SYSTEM_PROMPT = """\
You are Ringkeeper's router. You read one message from the operator and turn it
into tasks for the projects listed under PROJECTS. You never do the work
yourself and you have no tools.

Rules:
- Split the message into independent tasks, one per project. A request that
  touches two projects is two tasks.
- Never give one task to projects with different owners. If you think a task
  also has to change another project, list it in `touches`; prefer splitting.
- `project` is your best guess. `confidence` (0 to 1) is how sure you are that
  this task belongs to that project; be honest, the operator is asked when
  you're unsure. Put other plausible projects in `alternatives`, best first.
- Use project names exactly as listed. Aliases, descriptions and instance names
  help you match the operator's words to a project.
- ROUTING EXAMPLES are past answers from the operator: follow them for the
  same or very similar phrasing.
- kind: feature (new behaviour in code), fix (broken code), data (read or
  change data in the project's instances), content (copy, docs), ops
  (workflows, deploys, configuration).
- risk: read (only looks), write (changes non-prod things or code on a
  branch), prod-write (changes production data or workflows).
- complexity: low (a lookup, or a small, clear change: one file or a few
  lines, like adding a file or fixing a typo), medium (a few files or steps),
  high (design work, many files, or unclear requirements). Low features skip
  planning and run on a cheaper model, so don't inflate it.
- outcome: one sentence saying what done looks like.
- prompt: complete instructions for the worker who does this task. It sees only
  this prompt and its own project, so restate everything it needs from the
  message. Don't mention other projects. Its working directory is the
  project's root, so never tell it to look for a project folder. If the
  operator wants to be asked something first, tell the worker to use its
  ask_user tool.
- If the message asks for nothing to be done (a greeting, a question about
  Ringkeeper itself), return no tasks and answer in `reply`.
- `reply` is one short line for the operator.

Answer with one JSON object and nothing else, matching this JSON schema:
"""


@dataclass(frozen=True)
class Context:
    owners: dict[str, str]  # project -> owner, for checking the answer
    prompt: str


def _snippet(text: str, limit: int = SNIPPET_CHARS) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def _project_card(project: Project) -> dict[str, Any]:
    return {
        "name": project.name,
        "owner": project.owner,
        "description": project.description,
        "aliases": project.aliases,
        "instances": [
            {"name": b.instance.name, "type": b.instance.type.value, "env": b.instance.env.value}
            for b in sorted(project.bindings, key=lambda b: b.instance.name)
        ],
    }


def _decision_line(decision: Decision) -> str:
    payload = decision.payload or {}
    what = payload.get("question") or payload.get("message") or decision.tool or ""
    project = decision.project or "?"
    return f"- {decision.kind} for {project}: {_snippet(str(what))}"


def system_prompt() -> str:
    return SYSTEM_PROMPT + json_schema()


def build_context(session: Session, settings: Settings, message: Message) -> Context:
    projects = session.scalars(
        select(Project).options(selectinload(Project.bindings)).order_by(Project.name)
    ).all()
    history = threads.recent_messages(
        session, message.thread_id, limit=settings.router_recent_messages, before=message.created_at
    )
    open_tasks = session.scalars(
        select(Task)
        .where(Task.status.not_in(list(TERMINAL_STATUSES)))
        .order_by(Task.created_at.desc())
        .limit(20)
    ).all()
    pending = session.scalars(
        select(Decision)
        .where(Decision.status == DecisionStatus.PENDING)
        .order_by(Decision.created_at.desc())
        .limit(20)
    ).all()
    saved = examples.list_examples(session, limit=settings.router_examples)

    sections = [
        "PROJECTS:\n" + json.dumps([_project_card(p) for p in projects], indent=1),
        "THREAD SO FAR:\n"
        + ("\n".join(f"- {m.role}: {_snippet(m.body)}" for m in history) or "(new thread)"),
        "OPEN TASKS:\n"
        + (
            "\n".join(
                f"- {t.project} [{t.status}]: {_snippet(t.outcome or t.prompt)}" for t in open_tasks
            )
            or "(none)"
        ),
        "PENDING DECISIONS:\n" + ("\n".join(_decision_line(d) for d in pending) or "(none)"),
        "ROUTING EXAMPLES:\n"
        + (
            "\n".join(
                f'- "{_snippet(e.text)}" -> {e.project}' + (f" ({e.kind})" if e.kind else "")
                for e in saved
            )
            or "(none)"
        ),
        "MESSAGE TO ROUTE:\n" + message.body,
    ]
    return Context(owners={p.name: p.owner for p in projects}, prompt="\n\n".join(sections))
