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
    Agent,
    Decision,
    DecisionStatus,
    Message,
    Project,
    Task,
)
from ringkeeper.router.schema import json_schema
from ringkeeper.services import clients, examples, mentions, threads

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
- Use project names exactly as listed. Titles, aliases, descriptions and instance
  names help you match the operator's words to a project.
- A project's `name` is its ID, for your answer's fields. In `reply`, and anywhere
  else the operator reads, call it by its `title`.
- ROUTING EXAMPLES are past answers from the operator: follow them for the
  same or very similar phrasing.
- `agent`: when the operator asks one of a project's agents by name ("ask the
  Seaside follow-up agent to..."), that agent's name exactly as listed under the
  project's `agents`. Otherwise leave it out. Keep the agent even if the job doesn't
  look like one for it: an agent is limited to its own connections on purpose, and
  a task without it could reach more than the operator chose. If it can't do the
  job, it will say so.
- kind: feature (new behaviour in code), fix (broken code), data (read or
  change data in the project's instances), content (copy, docs), ops
  (workflows, deploys, configuration).
- risk: read (only looks), write (changes non-prod things or code on a
  branch), prod-write (changes production data or workflows).
- complexity: low (a lookup, or a small, clear change: one file or a few
  lines, like adding a file or fixing a typo), medium (a few files or steps),
  high (design work, many files, or unclear requirements). Low features skip
  planning and run on a cheaper model, so don't inflate it.
- project_specific: true when the work is about one project's own things (its code,
  site, database, workflows). false when any of the client's projects would do, because
  it only uses the client's own accounts (email, Google, Slack, calendar, documents):
  then Ringkeeper sends it to the client's default project without asking.
- why: only when you're unsure between projects (low confidence, with alternatives): one
  short line for the operator saying why it could be either, naming them by title, like
  "Both Website and Blog have a contact form." Otherwise leave it out.
- general: true for work that needs no client's connection: a web lookup, writing,
  summarizing or translating text the operator pasted, brainstorming. It's still a task,
  never a reply: pick any project, and Ringkeeper runs it in the project the operator
  chose, or in their own client's default project. Never answer that no project covers it.
- needs_web: true when the work has to look something up on the web.
- outcome: one sentence saying what done looks like.
- prompt: complete instructions for the worker who does this task. It sees only
  this prompt and its own project, so restate everything it needs from the
  message. Don't mention other projects. Its working directory is the
  project's root, so never tell it to look for a project folder. If the
  operator wants to be asked something first, tell the worker to use its
  ask_user tool.
- When the operator asks to remember something for a project, that's a task for
  the project's memory: write the prompt as "Use the remember tool to save this
  note: ...". Never ask for a notes file, because a file changed by a task stays
  on that task's branch and the next task won't see it. When the operator asks
  what was remembered, tell the worker to use the recall tool.
- Questions about Ringkeeper itself or the operator's own setup ("how many
  connections do we have?", "what's waiting for me?", "what did we spend this
  week?", "which projects does Acme have?"): their connections, projects,
  clients, tasks, approvals, settings or costs. Set `about_ringkeeper` to true
  and return no tasks; Ringkeeper answers from its own records. Prefer this
  whenever the message asks about "we", "our" or "my" setup and names no
  client's system. Never send such a question to a project: a project's
  database is the client's data, not Ringkeeper's.
- A request that is vague about what to read or change (no table, record,
  workflow or outcome named) gets a low confidence, so the operator is asked.
- Questions about how to use Ringkeeper itself ("how do I link a connection?",
  "what's a routine?", "can Ringkeeper do X?"): also `about_ringkeeper`, no tasks.
- `@name` in a message is the operator naming a client, project, connection or
  agent on purpose: route to it, with high confidence.
- If the message asks for nothing to be done (a greeting, thanks), return no
  tasks and answer in `reply`.
- A short follow-up continues the open request in THREAD SO FAR: "yes", "do it", "the
  Acme one", "use my own Google instead". Make the task the earlier request asked for,
  changed as the follow-up says; don't ask what they'd like you to do.
- `reply` is one short line for the operator.

Answer with one JSON object and nothing else, matching this JSON schema:
"""


@dataclass(frozen=True)
class Context:
    owners: dict[str, str]  # project -> owner, for checking the answer
    prompt: str
    aliases: tuple[str, ...] = ()  # the projects' other names


def _snippet(text: str, limit: int = SNIPPET_CHARS) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def _project_card(
    project: Project, agents: dict[str, list[Agent]], owners: dict[str, str]
) -> dict[str, Any]:
    return {
        "name": project.name,
        "title": project.title,
        "owner": owners.get(project.owner, project.owner),  # the client as people read it
        "description": project.description,
        "aliases": project.aliases,
        "instances": [
            {"name": b.instance.name, "type": b.instance.type.value, "env": b.instance.env.value}
            for b in sorted(project.bindings, key=lambda b: b.instance.name)
        ],
        "agents": [
            {"name": a.name, "does": _snippet(a.instructions, 200)}
            for a in agents.get(project.name, [])
        ],
    }


def _task_line(task: Task) -> str:
    line = f"- {task.project} [{task.status}]: {_snippet(task.outcome or task.prompt)}"
    return line + (f" -> result: {_snippet(task.summary, 400)}" if task.summary else "")


def _decision_line(decision: Decision) -> str:
    payload = decision.payload or {}
    what = payload.get("question") or payload.get("message") or decision.tool or ""
    project = decision.project or "?"
    return f"- {decision.kind} for {project}: {_snippet(str(what))}"


def system_prompt() -> str:
    return SYSTEM_PROMPT + json_schema()


def build_context(
    session: Session, settings: Settings, message: Message, request: str | None = None
) -> Context:
    """`request`: what to route instead of the message itself (an earlier request its
    follow-up placed)."""
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
    agents: dict[str, list[Agent]] = {}
    for agent in session.scalars(select(Agent).order_by(Agent.name)):
        agents.setdefault(agent.project.name, []).append(agent)
    owners = clients.names(session)

    sections = [
        "PROJECTS:\n" + json.dumps([_project_card(p, agents, owners) for p in projects], indent=1),
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
        'THIS CONVERSATION\'S TASKS (what "it", "the result" or "do it for..." mean):\n'
        + (
            "\n".join(_task_line(t) for t in threads.thread_tasks(session, message.thread_id))
            or "(none)"
        ),
        "ROUTING EXAMPLES:\n"
        + (
            "\n".join(
                f'- "{_snippet(e.text)}" -> {e.project}' + (f" ({e.kind})" if e.kind else "")
                for e in saved
            )
            or "(none)"
        ),
    ]
    named = mentions.for_message(session, message)
    if named:
        sections.append(mentions.note(named))
    sections.append("MESSAGE TO ROUTE:\n" + (request or message.body))
    return Context(
        owners={p.name: p.owner for p in projects},
        prompt="\n\n".join(sections),
        aliases=tuple(a for p in projects for a in (p.title, *p.aliases)),
    )
