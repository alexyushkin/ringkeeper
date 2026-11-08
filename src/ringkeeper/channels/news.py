"""What a request sent from a chat (Telegram, a Slack or Discord DM) gets back: what the
router did with it, and each of its tasks' end. Projects, task ids and kinds only: never the
worker prompt or outcome, so it can go to a chat."""

from typing import Any, cast

from sqlalchemy.orm import Session, object_session

from ringkeeper.models import Message, Task, TaskStatus
from ringkeeper.services import titles
from ringkeeper.telegram import text as telegram_text
from ringkeeper.wording import task_status


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
        # The router answered instead of making tasks: meant to be read, so it gets
        # the room a question gets, with the same rules (no SQL or code).
        output = cast(dict[str, Any], routing.get("output") or {})
        shown = telegram_text.question(str(output.get("reply") or "Nothing to do."), limit)
        lines.append(shown.text)
        if shown.cut:
            lines.append(telegram_text.SHORTENED)
    return "\n".join([f"{head}:", *lines])


def finished_text(task: Task) -> str:
    mark = "✔" if task.status is TaskStatus.SUCCEEDED else "✖"
    return f"{mark} {titles.of(object_session(task), task.project)}: {task_status(task.status)}"
