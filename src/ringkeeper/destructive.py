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
import unicodedata
from typing import Any

from ringkeeper.models import InstanceType

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
}

_COMMENTS = re.compile(r"--[^\n]*|/\*.*?\*/", re.S)
_ESCAPE_STRING = re.compile(r"(?<![\w'])[eE]'")
_DOLLAR_QUOTE = re.compile(r"\$[A-Za-z_]?\w*\$")
_QUOTED = re.compile(r"'(?:[^']|'')*'")
_IDENTIFIERS = re.compile(r'"(?:[^"]|"")*"')
# Zero-width and soft-hyphen characters, which can split a keyword invisibly.
_INVISIBLE_CHARS = (0x200B, 0x200C, 0x200D, 0x200E, 0x200F, 0x2060, 0xFEFF, 0xAD)
_INVISIBLE = re.compile("[" + "".join(map(chr, _INVISIBLE_CHARS)) + "]")

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
_CALL = re.compile(r"\b([a-z_][a-z0-9_$]*(?:\.[a-z_][a-z0-9_$]*)?)\s*\(")
# Not function calls, or functions without side effects.
_HARMLESS = {
    # SQL syntax followed by a parenthesis
    "values", "in", "exists", "any", "all", "some", "row", "array", "as", "on",
    "and", "or", "not", "where", "set", "from", "join", "using", "into", "over",
    "filter", "within", "select", "returning", "conflict", "when", "then", "else",
    "case", "is", "like", "ilike", "between", "do", "update", "with", "lateral",
    # types with sizes
    "numeric", "decimal", "varchar", "char", "character", "timestamp", "time",
    "interval", "bit", "float", "cast",
    # plain functions
    "coalesce", "nullif", "greatest", "least", "lower", "upper", "trim", "ltrim",
    "rtrim", "btrim", "length", "char_length", "concat", "concat_ws", "substring",
    "substr", "replace", "left", "right", "position", "split_part", "format",
    "now", "date_trunc", "date_part", "extract", "to_char", "to_date",
    "to_timestamp", "to_number", "age", "make_date", "make_interval", "round",
    "abs", "floor", "ceil", "ceiling", "trunc", "mod", "power", "sqrt", "random",
    "count", "sum", "max", "min", "avg", "string_agg", "array_agg", "jsonb_agg",
    "json_agg", "jsonb_build_object", "json_build_object", "jsonb_build_array",
    "json_build_array", "jsonb_set", "jsonb_insert", "to_jsonb", "to_json",
    "jsonb_strip_nulls", "array_append", "array_remove", "array_length",
    "unnest", "gen_random_uuid", "uuid_generate_v4", "md5", "encode", "decode",
}  # fmt: skip


def _normalized(sql: str) -> str:
    return _INVISIBLE.sub("", unicodedata.normalize("NFKC", sql))


def sql_reason(sql: str) -> str | None:  # noqa: PLR0911 (one return per reason)
    """Why a statement needs confirmation, or None if it is shown to be ordinary."""
    text = _COMMENTS.sub(" ", _normalized(sql))
    if _DOLLAR_QUOTE.search(text) or _ESCAPE_STRING.search(text):
        return "uses quoting that hides what it does ($$ or E'')"
    bare = _IDENTIFIERS.sub('"x"', _QUOTED.sub("''", text)).lower()
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
    for call in _CALL.finditer(bare):
        if target is not None and call.start() == target.start(1):
            continue
        name = call.group(1).rsplit(".", 1)[-1]
        if name not in _HARMLESS:
            return f"calls {call.group(1)}(), whose effects can't be checked"
    return None


def reason(
    service: InstanceType | None, tool: str | None, args: dict[str, Any] | None
) -> str | None:
    """Why this held write needs the connection's name typed, or None if it's ordinary."""
    args = args or {}
    if service is InstanceType.SUPABASE and tool == "execute":
        sql = args.get("sql")
        return sql_reason(sql) if isinstance(sql, str) else "has no SQL to check"
    if (service, tool) in SAFE_TOOLS:
        return None
    if service is InstanceType.N8N and tool == "update_workflow":
        return "replaces a whole workflow"
    return "uses a tool whose effects can't be checked"
