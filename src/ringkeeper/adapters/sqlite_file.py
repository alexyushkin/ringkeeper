"""The demo database: a SQLite file behind the database tools.

Only Ringkeeper's own demo (services.demo) makes one: its connection string is
`sqlite:///<path>`, and the path must be inside the data folder's demo folder,
so no other file on the computer can be reached this way. The web app's
connection form never accepts one (netpolicy.check_dsn refuses it).

Reads open the file read-only; a write runs one statement and commits. Like
the Postgres side, a call is exactly one statement (sqlite3 refuses more).
"""

import re
import sqlite3
import time
from pathlib import Path
from typing import Any

from ringkeeper.adapters.base import JSON, Target, ToolError
from ringkeeper.config import get_settings

PREFIX = "sqlite:///"
TIMEOUT_SECONDS = 15.0
# Postgres-style placeholders, which the tools document, become SQLite's.
_PLACEHOLDER = re.compile(r"%s")


def demo_folder() -> Path:
    return (get_settings().data_dir / "demo").absolute()


def path_of(target: Target) -> Path | None:
    """The demo database's file, or None for a Postgres connection."""
    dsn = target.secret.reveal()
    if not dsn.startswith(PREFIX):
        return None
    path = Path(dsn[len(PREFIX) :]).absolute()
    if demo_folder() not in path.parents:
        raise ToolError(f"instance '{target.name}' points at a file outside the demo folder")
    if not path.is_file():
        raise ToolError(
            f"the demo database for '{target.name}' is missing; remove the demo and add it again"
        )
    return path


def _value(value: object) -> object:
    return value if value is None or isinstance(value, str | int | float) else str(value)


def run(
    path: Path, statement: str, params: list[Any] | None, *, read_only: bool, max_rows: int
) -> JSON:
    uri = f"{path.as_uri()}?mode={'ro' if read_only else 'rw'}"
    try:
        conn = sqlite3.connect(uri, uri=True, timeout=5)
    except sqlite3.Error as error:
        raise ToolError(f"cannot open the demo database: {error}") from None
    deadline = time.monotonic() + TIMEOUT_SECONDS
    conn.set_progress_handler(lambda: int(time.monotonic() > deadline), 10_000)
    try:
        cursor = conn.execute(_PLACEHOLDER.sub("?", statement), params or [])
        columns = [c[0] for c in cursor.description or []]
        fetched = cursor.fetchmany(max_rows + 1) if columns else []
        result: JSON = {
            "columns": columns,
            "rows": [[_value(v) for v in row] for row in fetched[:max_rows]],
            "row_count": min(len(fetched), max_rows),
            "truncated": len(fetched) > max_rows,
        }
        if not read_only:
            conn.commit()
            result["rowcount"] = cursor.rowcount
        return result
    except sqlite3.Error as error:
        raise ToolError(str(error)) from None
    finally:
        conn.close()


def list_tables(path: Path) -> JSON:
    found = run(
        path,
        "SELECT name, type FROM sqlite_master WHERE type IN ('table', 'view') "
        "AND name NOT LIKE 'sqlite_%' ORDER BY name",
        None,
        read_only=True,
        max_rows=1000,
    )
    return {
        "tables": [{"schema": "main", "name": n, "type": t} for n, t in found["rows"]],
        "truncated": found["truncated"],
    }


def describe_table(path: Path, table: str) -> JSON:
    def pragma(name: str) -> list[list[Any]]:
        # name is one of three fixed pragmas below; the table is a parameter.
        statement = f"SELECT * FROM pragma_{name}(?)"  # noqa: S608
        return run(path, statement, [table], read_only=True, max_rows=1000)["rows"]

    columns = pragma("table_info")
    if not columns:
        raise ToolError(f"table {table} does not exist or is not visible")
    keys = [c[1] for c in sorted(columns, key=lambda c: c[5]) if c[5]]
    constraints = (
        [
            {
                "name": f"{table}_pkey",
                "kind": "primary_key",
                "definition": f"PRIMARY KEY ({', '.join(keys)})",
            }
        ]
        if keys
        else []
    )
    constraints += [
        {
            "name": f"{table}_{f[3]}_fkey",
            "kind": "foreign_key",
            "definition": f"FOREIGN KEY ({f[3]}) REFERENCES {f[2]}({f[4]})",
        }
        for f in pragma("foreign_key_list")
    ]
    return {
        "schema": "main",
        "table": table,
        "columns": [
            {"name": c[1], "type": c[2], "nullable": not c[3], "default": c[4]} for c in columns
        ],
        "constraints": constraints,
        "indexes": [
            {"name": i[1], "definition": f"unique={bool(i[2])}"} for i in pragma("index_list")
        ],
    }
