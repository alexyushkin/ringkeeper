"""What Slack and Discord get, and what their buttons may do.

A message is a short summary plus buttons, never secrets, SQL or tool arguments:
the kind, project and connection, a scrubbed line or two with code and SQL
hidden, and Open (a page in Ringkeeper, when there's an address for it).

Buttons follow the same rules as a desktop notification's (inbox.quick_approval):
Approve only for a held call that isn't a production change and doesn't need
the connection's name typed; those, plans and questions are opened in
Ringkeeper. Deny is always safe. A "which project?" offers its projects.

Only the approver set up for the channel can press them; anyone else in the
channel is told they're not allowed, and nothing happens.
"""

import uuid
from dataclasses import dataclass, field

from sqlalchemy.orm import object_session

from ringkeeper.config import Settings
from ringkeeper.db import session_scope
from ringkeeper.errors import RingkeeperError
from ringkeeper.models import Decision, DecisionKind, DecisionStatus, Routine, Task, TaskStatus
from ringkeeper.scrub import scrub
from ringkeeper.services import inbox, titles
from ringkeeper.telegram import text as shown_text
from ringkeeper.telegram.api import defang
from ringkeeper.wording import decision_status, person, plain, task_status

PREFIX = "rk"
TEST_ACTION = "rk:test"
SUMMARY_CHARS = 200
QUESTION_CHARS = 600
TASK_CHARS = 300
NOT_ALLOWED = "Not allowed: only the person set up for this channel can answer here."
TEST_TEXT = (
    "Ringkeeper test: this channel works. Press the button to check that answers "
    "from you are accepted here."
)
SHORTENED = "Shortened. The full text is in Ringkeeper."
KIND_LABELS = {
    DecisionKind.ROUTE: "Which project?",
    DecisionKind.APPROVE: "Approval",
    DecisionKind.PLAN: "Plan",
    DecisionKind.QUESTION: "Question",
}
CHOICE_LABELS = {"approve": "Approve", "deny": "Deny", inbox.NO_PROJECT: "None of these"}
FAILED = (TaskStatus.FAILED, TaskStatus.TIMED_OUT, TaskStatus.BUDGET_EXCEEDED)


@dataclass(frozen=True)
class Button:
    label: str
    action: str | None = None  # what pressing it does: "rk:<decision id>:<option>"
    url: str | None = None  # or a link
    style: str | None = None  # primary, danger


@dataclass(frozen=True)
class Card:
    text: str
    buttons: list[Button] = field(default_factory=list[Button])


def _clean(text: str) -> str:
    return defang(scrub(text))


def _clip(text: str, limit: int) -> tuple[str, bool]:
    hidden, cut = shown_text.hide_code(text)
    hidden = plain(hidden).strip()
    if len(hidden) > limit:
        return hidden[: limit - 1].rstrip() + "…", True
    return hidden, cut


def _open(url: str | None, label: str = "Open in Ringkeeper") -> list[Button]:
    return [Button(label, url=url)] if url else []


def _title(task: Task) -> str:
    return titles.of(object_session(task), task.project)


def _project(decision: Decision) -> str | None:
    project = decision.project
    return titles.of(object_session(decision), project) if project else None


def _label(decision: Decision, choice: str) -> str:
    return CHOICE_LABELS.get(choice, inbox.chosen(decision, choice))


def decision_card(decision: Decision, open_url: str | None) -> Card:
    head = [KIND_LABELS[decision.kind]]
    head += [part for part in (_project(decision), decision.instance) if part]
    shown = shown_text.body(decision, SUMMARY_CHARS, QUESTION_CHARS)
    lines = [" · ".join(head), shown.text]
    if shown.cut:
        lines.append(SHORTENED)
    buttons: list[Button] = []
    options = inbox.options(decision)
    if decision.kind is DecisionKind.APPROVE and inbox.quick_approval(decision):
        buttons.append(Button("Approve", _action(decision, options, "approve"), style="primary"))
    elif decision.kind in (DecisionKind.APPROVE, DecisionKind.PLAN):
        why = "a plan to read" if decision.kind is DecisionKind.PLAN else _why(decision)
        lines.append(f"Approve it in Ringkeeper: {why}.")
    elif decision.kind is DecisionKind.QUESTION:
        lines.append("Answer it in Ringkeeper.")
    if decision.kind in (DecisionKind.APPROVE, DecisionKind.PLAN):
        buttons.append(Button("Deny", _action(decision, options, "deny"), style="danger"))
    if decision.kind is DecisionKind.ROUTE:
        buttons += [
            Button(_label(decision, option), _action(decision, options, option))
            for option in options
        ]
    return Card(_clean("\n".join(lines)), buttons + _open(open_url))


def _why(decision: Decision) -> str:
    if inbox.destructive_reason(decision):
        return "it needs the connection's name typed"
    return "it's a production change"


def _action(decision: Decision, options: list[str], choice: str) -> str:
    return f"{PREFIX}:{decision.id}:{options.index(choice)}"


def outcome_line(decision: Decision) -> str:
    who = person(decision.decided_by or (decision.answer or {}).get("by"))
    choice = (decision.answer or {}).get("choice")
    match decision.status:
        case DecisionStatus.PENDING:
            return f"Approved by {who}. Running now."
        case DecisionStatus.EXPIRED:
            return "Ran out of time, so nothing was changed."
        case DecisionStatus.FAILED:
            return f"Approved by {who}, but it failed. Open it in Ringkeeper to see why."
        case DecisionStatus.ANSWERED if choice:
            return f"Answered by {who}: {_label(decision, choice)}."
        case _:
            return f"{decision_status(decision.status).capitalize()} by {who}."


def closed_card(decision: Decision, open_url: str | None) -> Card:
    """The same message once it's decided: what happened, and no buttons but Open."""
    card = decision_card(decision, open_url)
    return Card(_clean(f"{card.text}\n{outcome_line(decision)}"), _open(open_url))


def task_card(task: Task, open_url: str | None) -> Card:
    mark = "✖" if task.status in FAILED else "✔"
    head = f"{mark} {_title(task)}: {task_status(task.status)}"
    said = (task.error if task.status in FAILED else task.summary) or ""
    summary, cut = _clip(said, TASK_CHARS)
    lines = [head] + ([summary] if summary else []) + ([SHORTENED] if cut else [])
    return Card(_clean("\n".join(lines)), _open(open_url))


def routine_card(routine: Routine, task: Task, open_url: str | None) -> Card:
    mark = "✖" if task.status in FAILED else "✔"
    head = f"{mark} Routine {routine.name} · {_title(task)}: {task_status(task.status)}"
    summary, cut = _clip(task.summary or task.error or "", TASK_CHARS)
    lines = [head] + ([summary] if summary else []) + ([SHORTENED] if cut else [])
    return Card(_clean("\n".join(lines)), _open(open_url))


def test_card() -> Card:
    return Card(TEST_TEXT, [Button("That's me", TEST_ACTION, style="primary")])


def press(  # noqa: PLR0911 (one answer per way a press can go)
    settings: Settings, action: str, *, user: str, approver: str, who: str, via: inbox.Via
) -> str:
    """A button pressed in Slack or Discord: what to tell the person who pressed it."""
    if not approver or user != approver:
        return NOT_ALLOWED
    if action == TEST_ACTION:
        return "That's you: answers from you here are accepted."
    parts = action.split(":")
    if len(parts) != 3 or parts[0] != PREFIX:
        return "This button no longer works. Open Ringkeeper to answer."
    try:
        uuid.UUID(parts[1])
        with session_scope() as session:
            decision = session.get(Decision, parts[1])
            if decision is None:
                return "This was removed, so there's nothing to answer."
            choice = inbox.options(decision)[int(parts[2])]
            if choice == "approve" and not inbox.quick_approval(decision):
                # The button isn't offered for these; a forged press gets nowhere.
                return "Approve this one in Ringkeeper."
            answered = inbox.answer(
                session, settings, str(decision.id), inbox.Answer(choice=choice), by=who, via=via
            )
            label = _label(decision, choice).lower()
            return f"Done: {label}. It's {decision_status(answered.decision.status)}."
    except (ValueError, IndexError):
        return "This button no longer works. Open Ringkeeper to answer."
    except RingkeeperError as error:
        return f"Not done: {error}"
