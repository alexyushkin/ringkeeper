"""Which held writes need the connection's name typed to approve (and never Telegram).

This fails closed: a write passes as ordinary only when it is shown to be a
plain INSERT, UPDATE or SELECT, with no deleting or schema-changing keyword
anywhere, no dollar-quoted bodies or escape strings (which can hide either),
and no function calls beyond a short list of harmless ones. Everything else,
including DO, MERGE, CALL, COPY and every DROP, needs the typed name; so do
writes through tools this module doesn't know to be harmless.

Comments, quoted strings and quoted identifiers are removed before matching,
so `UPDATE notes SET body = 'drop table'` is ordinary and
`DELETE FROM customers` isn't.
"""

import re
from typing import Any, cast

from ringkeeper.adapters import github, google, mcp
from ringkeeper.models import InstanceType
from ringkeeper.sql_text import (
    CALL,
    COMMENTS,
    DOLLAR_QUOTE,
    ESCAPE_STRING,
    HARMLESS,
    IDENTIFIERS,
    QUOTED,
    normalized,
    sql_of,
)

# Writes through these tools change nothing irreversibly; any other write tool
# needs confirmation. (supabase `execute` is checked statement by statement.)
SAFE_TOOLS = {
    (InstanceType.N8N, "activate_workflow"),
    (InstanceType.N8N, "deactivate_workflow"),
    (InstanceType.AIRTABLE, "create_records"),  # adds; changes nothing that was there
    # Each puts an existing build live; another promote or rollback undoes it.
    (InstanceType.VERCEL, "redeploy"),
    (InstanceType.VERCEL, "promote"),
    (InstanceType.VERCEL, "rollback"),
    # Sends can't be undone, but they destroy nothing: the approval itself is the
    # check, and Telegram may give it.
    (InstanceType.TWILIO, "send_sms"),
    (InstanceType.GMAIL, "send_email"),
    (InstanceType.GMAIL, "create_draft"),
    # Google: each write waits for approval (unless the owner lets that tool run);
    # the trash keeps mail 30 days, and nothing else removes anything.
    *((InstanceType.GOOGLE, tool) for tool in google.WRITES),
    # GitHub: each write waits for approval; a push never forces. A merge is the one that
    # needs the name typed.
    *((InstanceType.GITHUB, tool) for tool in github.WRITES if tool != "merge_pull"),
    (InstanceType.N8N, "retry_execution"),
    (InstanceType.N8N, "stop_execution"),
}
MCP_REASONS = {
    mcp.BY_USER: "you set to need the connection's name",
    mcp.BY_ANNOTATIONS: "its server marks as destructive",
    mcp.BY_NAME: "sounds like it deletes",
    mcp.BY_SERVICE: "calls an endpoint that deletes (or doesn't say which)",
}

_FIRST_WORD = {
    "delete": "deletes rows",
    "truncate": "empties a table",
    "drop": "drops a database object",
    "alter": "changes a table's structure",
    "merge": "can delete rows (MERGE)",
    "do": "runs a code block that can't be checked",
    "call": "runs a procedure that can't be checked",
    "execute": "runs a prepared statement that can't be checked",
    "copy": "copies data in or out of the database",
    "create": "changes the database's structure",
    "grant": "changes permissions",
    "revoke": "changes permissions",
}
_ORDINARY_FIRST = {"insert", "update", "select", "with", "values"}
# Anywhere in the statement (outside quotes), these mean it can delete or restructure.
_KEYWORDS = re.compile(
    r"\b(delete|truncate|drop|alter|merge|call|execute|copy|create|grant|revoke|"
    r"vacuum|reindex|cluster|lock|comment|security|refresh|reset|discard|"
    r"listen|notify|prepare|deallocate|import|load|reassign|owned)\b"
)


def sql_reason(sql: str) -> str | None:  # noqa: PLR0911 (one return per reason)
    """Why a statement needs confirmation, or None if it is shown to be ordinary."""
    text = COMMENTS.sub(" ", normalized(sql))
    if DOLLAR_QUOTE.search(text) or ESCAPE_STRING.search(text):
        return "uses quoting that hides what it does ($$ or E'')"
    bare = IDENTIFIERS.sub('"x"', QUOTED.sub("''", text)).lower()
    words = re.findall(r"[a-z_]+", bare)
    if not words:
        return "isn't a statement that can be checked"
    first = words[0]
    if first in _FIRST_WORD:
        return _FIRST_WORD[first]
    if first not in _ORDINARY_FIRST:
        return f"starts with {first.upper()}, which can't be checked"
    if match := _KEYWORDS.search(bare):
        return _FIRST_WORD.get(match.group(1), f"contains {match.group(1).upper()}")
    target = re.search(r"\binsert\s+into\s+([a-z_][a-z0-9_$.]*|\"x\")\s*\(", bare)
    for call in CALL.finditer(bare):
        if target is not None and call.start() == target.start(1):
            continue
        name = call.group(1).rsplit(".", 1)[-1]
        if name not in HARMLESS:
            return f"calls {call.group(1)}(), whose effects can't be checked"
    return None


def reason(
    service: InstanceType | None,
    tool: str | None,
    args: dict[str, Any] | None,
    config: dict[str, Any] | None = None,
) -> str | None:
    """Why this held write needs the connection's name typed, or None if it's ordinary.

    `config` is the connection's: an MCP server's tool rules live there.
    """
    args = args or {}
    if service is InstanceType.SUPABASE and tool == "execute":
        sql = args.get("sql")
        return sql_reason(sql) if isinstance(sql, str) else "has no SQL to check"
    if (service, tool) in SAFE_TOOLS:
        return None
    if service is InstanceType.MCP and tool == "call_tool":
        # Held unless it reads; the typed name is for the ones marked or sounding final.
        name = str(args.get("tool", ""))
        arguments = args.get("arguments")
        found = mcp.call_rule(
            config or {},
            name,
            cast(dict[str, Any], arguments) if isinstance(arguments, dict) else None,
        )
        if found.source == mcp.BY_SQL:
            sql = sql_of(args.get("arguments"))
            return sql_reason(sql) if sql else "has no SQL to check"
        return (
            f"runs '{name}', which {MCP_REASONS[found.source]}"
            if found.level == mcp.CONFIRM
            else None
        )
    if service is InstanceType.N8N and tool == "update_workflow":
        return "replaces a whole workflow"
    return "uses a tool whose effects can't be checked"
