"""Which held writes delete or drop data.

Those need more than a button: the web app asks for the connection's name
to be typed, the CLI for `--confirm <connection>`, and Telegram can't approve
them at all ("approve in the app"). The check looks at statement shapes
after removing comments and quoted text, so `UPDATE notes SET body = 'drop
table'` isn't destructive and `DELETE FROM customers` is.
"""

import re
from typing import Any

from ringkeeper.models import InstanceType

_COMMENTS = re.compile(r"--[^\n]*|/\*.*?\*/", re.S)
_QUOTED = re.compile(r"'(?:[^']|'')*'|\$(\w*)\$.*?\$\1\$", re.S)
_IDENTIFIERS = re.compile(r'"(?:[^"]|"")*"')
_RULES = (
    (re.compile(r"\bdelete\s+from\b", re.I), "deletes rows"),
    (re.compile(r"\btruncate\b", re.I), "empties a table"),
    (
        re.compile(
            r"\bdrop\s+(?:table|schema|database|view|materialized\s+view|index|function|"
            r"procedure|trigger|policy|sequence|type|extension|role|user)\b",
            re.I,
        ),
        "drops a database object",
    ),
    (re.compile(r"\balter\s+table\b[^;]*\bdrop\b", re.I), "drops a column or constraint"),
)


def _bare_sql(statement: str) -> str:
    text = _COMMENTS.sub(" ", statement)
    text = _QUOTED.sub("''", text)
    return _IDENTIFIERS.sub('"x"', text)


def reason(
    service: InstanceType | None, tool: str | None, args: dict[str, Any] | None
) -> str | None:
    """Why this held write is destructive, or None if it isn't."""
    args = args or {}
    if tool and tool.startswith(("delete_", "drop_", "remove_")):
        return "deletes data"
    if service is InstanceType.SUPABASE and isinstance(args.get("sql"), str):
        bare = _bare_sql(args["sql"])
        for pattern, why in _RULES:
            if pattern.search(bare):
                return why
    return None
