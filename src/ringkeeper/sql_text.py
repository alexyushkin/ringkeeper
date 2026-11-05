"""SQL as text: what's quoted, what's called, and whether a statement only reads.

Shared by ringkeeper.destructive (which held writes need the connection's name
typed) and the adapters (a SQL tool's call that only reads runs without asking).
Both fail closed: anything that can't be shown to be harmless isn't.
"""

import re
import unicodedata
from typing import Any, cast

COMMENTS = re.compile(r"--[^\n]*|/\*.*?\*/", re.S)
ESCAPE_STRING = re.compile(r"(?<![\w'])[eE]'")
# Oracle's q'[it's]': any delimiter, so what's inside can't be told from what's outside.
Q_QUOTE = re.compile(r"(?<![\w'])[nN]?[qQ]'")
DOLLAR_QUOTE = re.compile(r"\$[A-Za-z_]?\w*\$")
QUOTED = re.compile(r"'(?:[^']|'')*'")
IDENTIFIERS = re.compile(r'"(?:[^"]|"")*"')
# Zero-width and soft-hyphen characters, which can split a keyword invisibly.
INVISIBLE_CHARS = (0x200B, 0x200C, 0x200D, 0x200E, 0x200F, 0x2060, 0xFEFF, 0xAD)
INVISIBLE = re.compile("[" + "".join(map(chr, INVISIBLE_CHARS)) + "]")

CALL = re.compile(r"\b([a-z_][a-z0-9_$]*(?:\.[a-z_][a-z0-9_$]*)?)\s*\(")
# Not function calls, or functions without side effects.
HARMLESS = {
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


def normalized(sql: str) -> str:
    return INVISIBLE.sub("", unicodedata.normalize("NFKC", sql))


SQL_ARGUMENTS = ("sql", "query", "sqlStatements", "sql_statements", "statements")


def sql_of(arguments: object) -> str | None:
    """The SQL a remote service's SQL tool was given (one statement, or a list)."""
    if not isinstance(arguments, dict):
        return None
    given = cast(dict[str, Any], arguments)
    for key in SQL_ARGUMENTS:
        value = given.get(key)
        if isinstance(value, str):
            return value
        if isinstance(value, list) and value:
            return ";\n".join(str(v) for v in cast(list[Any], value))
    return None


# Statements that only read, by their first word. EXPLAIN is checked by what it explains.
_READ_FIRST = {"select", "with", "values", "table", "show", "explain"}
# Anywhere in a statement (outside quotes), these mean it writes, locks, or can't be checked.
_WRITES = re.compile(
    r"\b(insert|update|delete|merge|upsert|truncate|drop|alter|create|grant|revoke|copy|call|"
    r"do|execute|prepare|deallocate|vacuum|analyze|reindex|cluster|lock|comment|security|"
    r"refresh|reset|discard|listen|notify|unlisten|import|load|reassign|owned|set|into|"
    r"nextval|setval)\b"
)
_EXPLAIN_OPTIONS = re.compile(r"^explain\s*(\([^)]*\)\s*|verbose\s+|costs\s+)*")


def reads_only(sql: str, harmless: frozenset[str] = frozenset()) -> bool:
    """Whether every statement in `sql` is shown to only read: SELECT (WITH, VALUES,
    TABLE, SHOW, or EXPLAIN of one) with no writing keyword anywhere, no quoting that
    can hide one, and no function calls beyond harmless ones (HARMLESS, and `harmless`).
    Anything else: False."""
    text = COMMENTS.sub(" ", normalized(sql))
    if DOLLAR_QUOTE.search(text) or ESCAPE_STRING.search(text) or Q_QUOTE.search(text):
        return False
    bare = IDENTIFIERS.sub('"x"', QUOTED.sub("''", text)).lower()
    statements = [s.strip() for s in bare.split(";") if s.strip()]
    if not statements:
        return False
    return all(_reads(s, harmless) for s in statements)


def _reads(statement: str, harmless: frozenset[str]) -> bool:
    # EXPLAIN (not ANALYZE, which runs it: _WRITES) is judged by what it explains.
    explained = _EXPLAIN_OPTIONS.sub("", statement).strip()
    words = re.findall(r"[a-z_]+", explained)
    if not words or words[0] not in _READ_FIRST or words[0] == "explain":
        return False
    if _WRITES.search(explained):
        return False
    allowed = HARMLESS | harmless
    return all(
        call.group(1) in allowed or call.group(1).rsplit(".", 1)[-1] in HARMLESS
        for call in CALL.finditer(explained)
    )
