"""What a request sent from a chat (Telegram, a Slack or Discord DM) gets back: that it
arrived, what the router did with it, and each of its tasks' end. Projects, task ids and
kinds only: never the worker prompt or outcome, so it can go to a chat. A request the
router answered without starting anything (a question about Ringkeeper, how to do
something) gets the answer the app shows."""

from typing import Any, cast

from sqlalchemy.orm import Session, object_session

from ringkeeper.models import Message, Task, TaskStatus
from ringkeeper.router.self_answer import is_question
from ringkeeper.services import titles
from ringkeeper.telegram import text as telegram_text
from ringkeeper.wording import task_status

GOT_IT = "Got it. Working out which project it's for…"
LOOKING = "Got it. Looking into it…"


def got_it(body: str, link: str | None = None) -> str:
    """The first reply to a request. A question may need no project (Ringkeeper answers
    it from its own records), so it isn't told one is being found. A new conversation
    says where to follow it in the app."""
    said = LOOKING if is_question(body) else GOT_IT
    return f"{said}\nNew conversation: {link}" if link else said


RESULT_CHARS = 1500  # fits Slack, Discord (2,000) and Telegram (4,096), with the link


def result_text(task: Task, link: str | None, limit: int = RESULT_CHARS) -> str:
    """A task's end, for the chat it was asked from: what it found or did, as its last
    message said (tables as lines, no code or SQL), then where to read the rest."""
    mark = "✔" if task.status is TaskStatus.SUCCEEDED else "✖"
    head = f"{mark} {titles.of(object_session(task), task.project)}: {task_status(task.status)}"
    said = (task.summary if task.status is TaskStatus.SUCCEEDED else task.error) or ""
    if not said.strip():
        return head + (f"\n{link}" if link else "")
    shown = telegram_text.question(said, limit)
    tail = (
        [f"The rest is in Ringkeeper: {link}" if link else telegram_text.SHORTENED]
        if shown.cut
        else []
    )
    return "\n".join([head, shown.text, *tail])


def routed_text(session: Session, message: Message, limit: int) -> str:
    """Projects, task ids and kinds only: never the worker prompt or outcome."""
    head = "Your request"
    if message.route_error:
        return (
            "I couldn't tell which project your request is for. Send it again and name "
            "the project, or open Ringkeeper."
        )
    routing = message.routing or {}
    lines: list[str] = []
    for ident in routing.get("tasks", []):
        task = session.get(Task, ident)
        if task is not None:
            lines.append(f"→ {titles.of(session, task.project)}: started")
    for _ident in routing.get("decisions", []):
        lines.append("? I need to know which project. I've sent that question separately.")
    if not lines:
        # The router answered instead of making tasks: the words the app shows, meant to
        # be read, so they get the room a question gets, with the same rules (no SQL or code).
        output = cast(dict[str, Any], routing.get("output") or {})
        said = routing.get("answer") or output.get("reply") or "Nothing to do."
        shown = telegram_text.question(str(said), limit)
        return "\n".join([shown.text, *([telegram_text.SHORTENED] if shown.cut else [])])
    return "\n".join([f"{head}:", *lines])


def finished_text(task: Task) -> str:
    mark = "✔" if task.status is TaskStatus.SUCCEEDED else "✖"
    return f"{mark} {titles.of(object_session(task), task.project)}: {task_status(task.status)}"
