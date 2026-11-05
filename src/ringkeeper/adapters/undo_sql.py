"""Undo for the database tools: a plain UPDATE or DELETE on one table with a primary key.

Before the statement runs, the rows it will touch are read with the statement's
own WHERE: for an UPDATE only the primary key and the columns it sets, for a
DELETE whole rows. Reverting puts those values back (an UPDATE by primary key)
or the rows back (an INSERT), in one transaction.

Postgres does the conversions itself: rows are read with row_to_json and put
back with json_populate_recordset, so every column type round-trips. The demo's
SQLite file is done in Python.

Anything else (INSERT, which can't say which rows it added; schema changes;
UPDATE ... FROM; DELETE ... USING; a CTE) can't be undone, and says so before
it's approved.
"""

import json
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from ringkeeper.adapters import sqlite_file
from ringkeeper.adapters.base import JSON, Target, ToolError, Undoer
from ringkeeper.errors import RingkeeperError
from ringkeeper.sql_text import COMMENTS, normalized, reads_only

MAX_ROWS = 5000
IDENT = r'(?:"(?:[^"]|"")+"|[A-Za-z_][A-Za-z0-9_$]*)'
TABLE = rf"(?P<table>{IDENT}(?:\.{IDENT})?)"
_UPDATE = re.compile(
    rf"^\s*update\s+(?:only\s+)?{TABLE}\s+set\s+(?P<set>.+?)"
    r"(?:\s+where\s+(?P<where>.+?))?(?:\s+returning\s+.+?)?\s*;?\s*$",
    re.I | re.S,
)
_DELETE = re.compile(
    rf"^\s*delete\s+from\s+(?:only\s+)?{TABLE}"
    r"(?:\s+where\s+(?P<where>.+?))?(?:\s+returning\s+.+?)?\s*;?\s*$",
    re.I | re.S,
)
_SCHEMA = re.compile(r"^\s*(create|alter|drop|truncate|grant|revoke|comment)\b", re.I)


class CantUndoError(RingkeeperError):
    """This write runs, but it can't be taken back (the message says why)."""


def _mask(sql: str) -> str:
    """The statement with comments and quoted text blanked, same length, so keywords
    are found only where they are SQL and positions still match the original."""

    def blank(match: re.Match[str]) -> str:
        text = match.group(0)
        return text[0] + "x" * (len(text) - 2) + text[-1] if len(text) > 1 else " "

    masked = COMMENTS.sub(lambda m: " " * len(m.group(0)), sql)
    return re.sub(r"'(?:[^']|'')*'|\"(?:[^\"]|\"\")*\"", blank, masked)


def _top_level(text: str, masked: str) -> list[str]:
    """Split on commas outside parentheses."""
    parts: list[str] = []
    depth, start = 0, 0
    for i, ch in enumerate(masked):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == "," and depth == 0:
            parts.append(text[start:i])
            start = i + 1
    parts.append(text[start:])
    return [p.strip() for p in parts if p.strip()]


def _name(identifier: str) -> str:
    """A column as row_to_json names it: unquoted ones fold to lower case."""
    identifier = identifier.strip()
    if identifier.startswith('"'):
        return identifier[1:-1].replace('""', '"')
    return identifier.lower()


@dataclass(frozen=True)
class Plan:
    kind: str  # "update" or "delete"
    table: str  # as written
    where: str | None
    where_params: list[Any]
    columns: list[str]  # an update's columns, as written


def plan(sql: str, params: list[Any] | None, bind: str = r"%s") -> Plan:
    """What an UPDATE or DELETE touches. Raises CantUndoError for anything else. `bind` is how
    a value is marked in the statement: Postgres's %s, or Oracle's :1."""
    text = normalized(sql).strip()
    masked = _mask(text)
    if reads_only(text):
        raise CantUndoError("it only reads")
    if ";" in masked.rstrip().rstrip(";"):
        raise CantUndoError("it's more than one statement")
    if _SCHEMA.match(masked):
        raise CantUndoError("changes to tables themselves (create, alter, drop) can't be undone")
    if re.match(r"^\s*insert\b", masked, re.I):
        raise CantUndoError(
            "Ringkeeper can't tell which rows an INSERT adds, so it can't take them out again"
        )
    params = list(params or [])
    for pattern, kind in ((_UPDATE, "update"), (_DELETE, "delete")):
        found = pattern.match(masked)
        if found is None:
            continue
        if re.search(
            r"\b(from|using)\b",
            masked[
                found.end("table") : found.start("where") if found.group("where") else len(masked)
            ],
            re.I,
        ):
            raise CantUndoError("Ringkeeper can only undo a change to one table (no FROM or USING)")
        table = text[found.start("table") : found.end("table")]
        where = text[found.start("where") : found.end("where")] if found.group("where") else None
        before_where = masked[: found.start("where")] if where else masked
        used = len(re.findall(bind, before_where))
        where_params = params[used : used + len(re.findall(bind, _mask(where or "")))]
        columns: list[str] = []
        if kind == "update":
            set_text, set_masked = (
                text[found.start("set") : found.end("set")],
                masked[found.start("set") : found.end("set")],
            )
            for part, part_masked in zip(
                _top_level(set_text, set_masked), _top_level(set_masked, set_masked), strict=True
            ):
                left = part_masked.split("=", 1)[0].strip()
                if left.startswith("("):
                    raise CantUndoError("Ringkeeper can only undo SET column = value")
                columns.append(part[: len(left)].strip())
        return Plan(kind, table, where, where_params, columns)
    raise CantUndoError("Ringkeeper can only undo a plain UPDATE or DELETE on one table")


# --- Postgres ---


async def _pg(target: Target, statement: str, params: list[Any], *, read_only: bool) -> JSON:
    from ringkeeper.adapters import supabase  # noqa: PLC0415 (supabase imports this module)

    return await supabase.run_sql(target, statement, params, read_only=read_only, max_rows=MAX_ROWS)


async def _pg_keys(target: Target, table: str) -> list[str]:
    found = await _pg(
        target,
        "SELECT a.attname FROM pg_index i JOIN pg_attribute a ON a.attrelid = i.indrelid "
        "AND a.attnum = ANY(i.indkey) WHERE i.indrelid = %s::regclass AND i.indisprimary "
        "ORDER BY array_position(i.indkey, a.attnum)",
        [table],
        read_only=True,
    )
    return [str(r[0]) for r in found["rows"]]


def _quoted(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _rows(found: JSON) -> list[JSON]:
    if found.get("truncated"):
        raise CantUndoError(f"it changes more than {MAX_ROWS:,} rows")
    return [cast(JSON, r[0]) for r in found["rows"]]


class SqlUndo(Undoer):
    """The `execute` tool's undo (Postgres, and the demo's SQLite file)."""

    def why_not(self, args: JSON) -> str | None:
        try:
            plan(str(args.get("sql") or ""), cast(list[Any] | None, args.get("params")))
        except CantUndoError as error:
            return str(error)
        return None

    async def before(self, target: Target, args: JSON) -> JSON:
        found = plan(str(args.get("sql") or ""), cast(list[Any] | None, args.get("params")))
        demo = sqlite_file.path_of(target)
        keys = _sqlite_keys(demo, found.table) if demo else await _pg_keys(target, found.table)
        if not keys:
            raise CantUndoError(
                f"{found.table} has no primary key, so its rows can't be told apart later"
            )
        columns = [_name(c) for c in found.columns]
        if demo is not None:
            rows, more = _sqlite_select(
                demo, found.table, keys, columns or None, found.where, found.where_params, MAX_ROWS
            )
            if more:
                raise CantUndoError(f"it changes more than {MAX_ROWS:,} rows")
        else:
            picked = (
                ", ".join([*map(_quoted, keys), *found.columns]) if found.kind == "update" else "*"
            )
            where = f" WHERE {found.where}" if found.where else ""
            rows = _rows(
                await _pg(
                    target,
                    f"SELECT row_to_json(rk_x) FROM (SELECT {picked} FROM {found.table}{where}) rk_x",  # noqa: S608, E501
                    found.where_params,
                    read_only=True,
                )
            )
        return {
            "kind": found.kind,
            "table": found.table,
            "keys": keys,
            "columns": columns,
            "rows": rows,
        }

    async def current(self, target: Target, kept: JSON) -> JSON:
        table, keys = str(kept["table"]), cast(list[str], kept["keys"])
        columns = cast(list[str], kept["columns"]) if kept["kind"] == "update" else None
        rows = cast(list[JSON], kept["rows"])
        demo = sqlite_file.path_of(target)
        if demo is not None:
            return {"rows": _sqlite_by_key(demo, table, keys, columns, rows)}
        picked = ", ".join(f"rk_t.{_quoted(c)}" for c in [*keys, *columns]) if columns else "rk_t.*"
        on = " AND ".join(f"rk_t.{_quoted(k)} = rk_r.{_quoted(k)}" for k in keys)
        found = await _pg(
            target,
            f"SELECT row_to_json(rk_x) FROM (SELECT {picked} FROM {table} rk_t "  # noqa: S608 (names read from the database)
            f"JOIN json_populate_recordset(NULL::{table}, %s::json) rk_r ON {on}) rk_x",
            [json.dumps(rows)],
            read_only=True,
        )
        return {"rows": _rows(found)}

    async def restore(self, target: Target, kept: JSON) -> JSON:
        table, keys = str(kept["table"]), cast(list[str], kept["keys"])
        rows = cast(list[JSON], kept["rows"])
        if not rows:
            return {"restored": 0}
        demo = sqlite_file.path_of(target)
        if demo is not None:
            count = _sqlite_put_back(demo, table, keys, str(kept["kind"]), rows)
            return {"restored": count}
        if kept["kind"] == "update":
            columns = cast(list[str], kept["columns"])
            sets = ", ".join(f"{_quoted(c)} = rk_r.{_quoted(c)}" for c in columns)
            on = " AND ".join(f"rk_t.{_quoted(k)} = rk_r.{_quoted(k)}" for k in keys)
            rows_in = f"json_populate_recordset(NULL::{table}, %s::json)"
            statement = f"UPDATE {table} AS rk_t SET {sets} FROM {rows_in} AS rk_r WHERE {on}"  # noqa: S608
        else:
            rows_in = f"json_populate_recordset(NULL::{table}, %s::json)"
            statement = f"INSERT INTO {table} OVERRIDING SYSTEM VALUE SELECT * FROM {rows_in}"  # noqa: S608
        result = await _pg(target, statement, [json.dumps(rows)], read_only=False)
        return {"restored": result.get("rowcount", len(rows))}

    def describe(self, kept: JSON) -> str:
        rows = cast(list[JSON], kept["rows"])
        table = str(kept["table"])
        if not rows:
            return f"Nothing to put back: the change touched no rows in {table}."
        n = f"{len(rows)} row" + ("" if len(rows) == 1 else "s")
        keys = cast(list[str], kept["keys"])
        sample = "; ".join(
            ", ".join(f"{k} {row.get(k)}" for k in keys)
            + (
                ": "
                + ", ".join(f"{c} = {_shown(row.get(c))}" for c in cast(list[str], kept["columns"]))
                if kept["kind"] == "update"
                else ""
            )
            for row in rows[:3]
        )
        more = f" and {len(rows) - 3} more" if len(rows) > 3 else ""
        if kept["kind"] == "update":
            return f"Puts back the old values in {n} of {table} ({sample}{more})."
        return f"Adds back the {n} deleted from {table} ({sample}{more})."


def _shown(value: object) -> str:
    text = "empty" if value is None else json.dumps(value) if isinstance(value, str) else str(value)
    return text if len(text) <= 40 else text[:37] + "..."


# --- The demo's SQLite file: rows read and put back by primary key ---


def _ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _dicts(found: JSON) -> list[JSON]:
    return [dict(zip(found["columns"], row, strict=True)) for row in found["rows"]]


def _sqlite_keys(path: Path, table: str) -> list[str]:
    bare = table.rsplit(".", 1)[-1].strip('"')
    found = sqlite_file.run(
        path, "SELECT name, pk FROM pragma_table_info(?)", [bare], read_only=True, max_rows=1000
    )
    return [str(n) for n, pk in sorted(found["rows"], key=lambda r: r[1]) if pk]


def _sqlite_select(
    path: Path,
    table: str,
    keys: list[str],
    columns: list[str] | None,
    where: str | None,
    params: list[Any],
    limit: int,
) -> tuple[list[JSON], bool]:
    """The rows a statement's WHERE picks (key and these columns, or all), and
    whether there were more than `limit`."""
    picked = ", ".join(map(_ident, [*keys, *columns])) if columns else "*"
    clause = f" WHERE {where}" if where else ""
    found = sqlite_file.run(
        path,
        f"SELECT {picked} FROM {table}{clause}",  # noqa: S608 (the agent's own WHERE, read-only)
        params,
        read_only=True,
        max_rows=limit,
    )
    return _dicts(found), bool(found["truncated"])


def _sqlite_by_key(
    path: Path, table: str, keys: list[str], columns: list[str] | None, rows: list[JSON]
) -> list[JSON]:
    picked = ", ".join(map(_ident, [*keys, *columns])) if columns else "*"
    match = " AND ".join(f"{_ident(k)} = ?" for k in keys)
    found: list[JSON] = []
    for row in rows:
        hit = sqlite_file.run(
            path,
            f"SELECT {picked} FROM {table} WHERE {match}",  # noqa: S608 (names from the database)
            [row[k] for k in keys],
            read_only=True,
            max_rows=1,
        )
        found += _dicts(hit)
    return found


def _sqlite_put_back(path: Path, table: str, keys: list[str], kind: str, rows: list[JSON]) -> int:
    """An update's old values by primary key, or deleted rows inserted again: one transaction."""
    conn = sqlite3.connect(f"{path.as_uri()}?mode=rw", uri=True, timeout=5)
    try:
        with conn:
            for row in rows:
                if kind == "update":
                    columns = [c for c in row if c not in keys]
                    sets = ", ".join(f"{_ident(c)} = ?" for c in columns)
                    match = " AND ".join(f"{_ident(k)} = ?" for k in keys)
                    conn.execute(
                        f"UPDATE {table} SET {sets} WHERE {match}",  # noqa: S608
                        [*(row[c] for c in columns), *(row[k] for k in keys)],
                    )
                else:
                    names = ", ".join(map(_ident, row))
                    marks = ", ".join("?" * len(row))
                    conn.execute(
                        f"INSERT INTO {table} ({names}) VALUES ({marks})",  # noqa: S608
                        list(row.values()),
                    )
        return len(rows)
    except sqlite3.Error as error:
        raise ToolError(str(error)) from None
    finally:
        conn.close()
