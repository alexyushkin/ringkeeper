"""What a decision may say on Telegram, and how long it may be.

Approvals, plans and routes get a one-line summary (`telegram_summary_chars`,
default 160). Questions get more room (`telegram_question_chars`, default
1000) and keep their line breaks, because a question has to be readable to
be answered. Neither ever carries raw SQL or tool arguments: code blocks and
SQL statements are replaced with a pointer to the app, and anything cut or
hidden ends with a note saying the full text is in the app.
"""

import re
from dataclasses import dataclass

from ringkeeper.models import Decision, DecisionKind
from ringkeeper.scrub import scrub
from ringkeeper.services import inbox

SHORTENED = "Shortened for Telegram. The full text is in the Ringkeeper app."
CODE_HIDDEN = "[code: see the app]"
SQL_HIDDEN = "[SQL: see the app]"

_FENCED = re.compile(r"```.*?(?:```|\Z)", re.S)
_IDENT = r"[\w.\"*()]+"
# Statement shapes, not bare keywords: "Update the welcome text?" is a question,
# "UPDATE customers SET plan = 'gold'" is SQL. A statement runs to `;`, a blank
# line or the end. Inline `code` that holds a statement goes too, backticks and all.
_SQL_START = (
    rf"select\s+(?:distinct\s+)?{_IDENT}(?:\s*,\s*{_IDENT})*\s+from\s+{_IDENT}"
    rf"|update\s+{_IDENT}\s+set\b"
    r"|insert\s+into\b"
    r"|delete\s+from\b"
    r"|merge\s+into\b"
    r"|(?:drop|alter|create(?:\s+or\s+replace)?|truncate)\s+"
    r"(?:table|schema|database|view|index|function|policy|trigger|extension|type|role)\b"
    rf"|truncate\s+{_IDENT}"
    r"|grant\s+[\w\s,]+\s+on\b"
    r"|revoke\s+[\w\s,]+\s+on\b"
)
_INLINE_SQL = re.compile(rf"`\s*(?:{_SQL_START})[^`]*`", re.I)
_SQL = re.compile(rf"\b(?:{_SQL_START})(?:[^;\n]|\n(?![ \t]*\n))*;?", re.I)


@dataclass(frozen=True)
class Shown:
    text: str
    cut: bool  # shortened or something hidden: the app has the full text


def hide_code(text: str) -> tuple[str, bool]:
    hidden = _FENCED.sub(CODE_HIDDEN, text)
    hidden = _INLINE_SQL.sub(SQL_HIDDEN, hidden)
    hidden = _SQL.sub(SQL_HIDDEN, hidden)
    return hidden, hidden != text


def _clip(text: str, limit: int) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    cut = text[: max(limit - 1, 1)]
    space = cut.rfind(" ")
    if space > limit * 0.6:  # end on a word, unless that throws away too much
        cut = cut[:space]
    return cut.rstrip() + "…", True


def question(text: str, limit: int) -> Shown:
    """The question with its line breaks kept (blank-line runs collapsed)."""
    hidden, changed = hide_code(scrub(text))
    lines = [" ".join(line.split()) for line in hidden.strip().splitlines()]
    tidy = re.sub(r"\n{3,}", "\n\n", "\n".join(lines))
    clipped, cut = _clip(tidy, limit)
    return Shown(clipped, changed or cut)


def summary(decision: Decision, limit: int) -> Shown:
    """One line: never SQL, arguments, diffs or plan text (see inbox.summary)."""
    hidden, changed = hide_code(inbox.summary(decision, limit=1_000_000))
    clipped, cut = _clip(" ".join(hidden.split()), limit)
    return Shown(clipped, changed or cut)


def body(decision: Decision, summary_chars: int, question_chars: int) -> Shown:
    if decision.kind is DecisionKind.QUESTION:
        return question(str((decision.payload or {}).get("question", "")), question_chars)
    return summary(decision, summary_chars)
