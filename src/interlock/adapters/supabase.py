"""Supabase (and any Postgres): the instance's secret is a connection string.

Every statement is sent with the extended query protocol (`prepare=True`),
which accepts exactly one statement per call. Without that, psycopg would
use the simple protocol for parameterless SQL, and `query("COMMIT; DROP ...")`
could end the READ ONLY transaction and run a write.

The blocking psycopg driver runs in a worker thread: it behaves the same on
every platform (psycopg's async mode cannot use Windows' default event loop).
"""

import base64
import datetime
import decimal
import uuid
from functools import partial
from typing import Annotated, Any, LiteralString, cast
from urllib.parse import quote

import anyio.to_thread
import psycopg
from psycopg import sql as pgsql
from psycopg.conninfo import conninfo_to_dict
from pydantic import Field

from interlock.adapters.base import JSON, Adapter, Target, ToolError, read, write
from interlock.models import InstanceType

CONNECT_TIMEOUT = 10
READ_TIMEOUT = "15s"
WRITE_TIMEOUT = "30s"
MAX_ROWS = 1000
SYSTEM_SCHEMAS = ("pg_catalog", "information_schema", "pg_toast")

Sql = Annotated[str, Field(description="Exactly one SQL statement.")]
Params = Annotated[list[Any] | None, Field(description="Values for %s placeholders, in order.")]
MaxRows = Annotated[int, Field(ge=1, le=MAX_ROWS, description="Cap on rows returned.")]


def dsn_fragments(dsn: str) -> list[str]:
    try:
        password = conninfo_to_dict(dsn).get("password")
    except psycopg.ProgrammingError:
        return []
    if not isinstance(password, str) or not password:
        return []
    return [password, quote(password, safe=""), quote(password)]


def _connect(target: Target) -> psycopg.Connection[tuple[Any, ...]]:
    try:
        return psycopg.connect(
            target.secret.reveal(), connect_timeout=CONNECT_TIMEOUT, autocommit=False
        )
    except psycopg.Error as error:
        raise ToolError(f"cannot connect to instance '{target.name}': {_message(error)}") from None


def _message(error: psycopg.Error) -> str:
    # Just the primary message: details can quote data, and the DSN never belongs here.
    primary = getattr(getattr(error, "diag", None), "message_primary", None)
    if primary:
        return str(primary)
    text = str(error).strip()
    return text.splitlines()[0] if text else type(error).__name__


def _json_safe(value: object) -> object:  # noqa: PLR0911 (a type switch)
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, decimal.Decimal):
        return str(value)
    if isinstance(value, datetime.date | datetime.time | datetime.datetime):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, bytes | bytearray):
        return {"base64": base64.b64encode(bytes(value)).decode()}
    if isinstance(value, list | tuple):
        return [_json_safe(v) for v in cast(list[object], value)]
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in cast(dict[object, object], value).items()}
    return str(value)


def _rows(cursor: psycopg.Cursor[tuple[Any, ...]], max_rows: int) -> JSON:
    if cursor.description is None:
        return {"columns": [], "rows": [], "row_count": 0, "truncated": False}
    fetched = cursor.fetchmany(max_rows + 1)
    return {
        "columns": [column.name for column in cursor.description],
        "rows": [[_json_safe(v) for v in row] for row in fetched[:max_rows]],
        "row_count": min(len(fetched), max_rows),
        "truncated": len(fetched) > max_rows,
    }


def _run_blocking(
    target: Target, statement: str, params: list[Any] | None, read_only: bool, max_rows: int
) -> JSON:
    conn = _connect(target)
    try:
        conn.read_only = read_only
        with conn.transaction(), conn.cursor() as cursor:
            timeout = READ_TIMEOUT if read_only else WRITE_TIMEOUT
            cursor.execute(
                pgsql.SQL("SET LOCAL statement_timeout = {}").format(pgsql.Literal(timeout))
            )
            # prepare=True: extended protocol, so exactly one statement (see module doc).
            # Agent-supplied SQL is the whole point of this tool, hence the cast.
            cursor.execute(cast(LiteralString, statement), params or None, prepare=True)
            result = _rows(cursor, max_rows)
            if not read_only:
                result["rowcount"] = cursor.rowcount
            return result
    except psycopg.Error as error:
        raise ToolError(_message(error)) from None
    finally:
        conn.close()


async def _run(
    target: Target, statement: str, params: list[Any] | None, *, read_only: bool, max_rows: int
) -> JSON:
    return await anyio.to_thread.run_sync(
        partial(_run_blocking, target, statement, params, read_only, max_rows)
    )


@read
async def list_tables(
    target: Target,
    schema: Annotated[
        str | None, Field(description="Schema to list; null lists every non-system schema.")
    ] = "public",
) -> JSON:
    """List tables and views."""
    statement = (
        "SELECT table_schema, table_name, table_type FROM information_schema.tables "
        "WHERE table_schema <> ALL(%s) AND (%s::text IS NULL OR table_schema = %s) "
        "ORDER BY table_schema, table_name"
    )
    result = await _run(
        target,
        statement,
        [list(SYSTEM_SCHEMAS), schema, schema],
        read_only=True,
        max_rows=MAX_ROWS,
    )
    tables = [
        {"schema": s, "name": n, "type": "view" if t == "VIEW" else "table"}
        for s, n, t in result["rows"]
    ]
    return {"tables": tables, "truncated": result["truncated"]}


@read
async def describe_table(
    target: Target,
    table: Annotated[str, Field(description="Table or view name.")],
    schema: str = "public",
) -> JSON:
    """Columns, primary key, foreign keys and indexes of one table."""
    columns = await _run(
        target,
        "SELECT column_name, data_type, is_nullable = 'YES', column_default "
        "FROM information_schema.columns WHERE table_schema = %s AND table_name = %s "
        "ORDER BY ordinal_position",
        [schema, table],
        read_only=True,
        max_rows=MAX_ROWS,
    )
    if not columns["rows"]:
        raise ToolError(f"table {schema}.{table} does not exist or is not visible")
    constraints = await _run(
        target,
        "SELECT con.conname, con.contype, pg_get_constraintdef(con.oid) "
        "FROM pg_constraint con JOIN pg_class rel ON rel.oid = con.conrelid "
        "JOIN pg_namespace ns ON ns.oid = rel.relnamespace "
        "WHERE ns.nspname = %s AND rel.relname = %s AND con.contype IN ('p', 'f', 'u') "
        "ORDER BY con.conname",
        [schema, table],
        read_only=True,
        max_rows=MAX_ROWS,
    )
    indexes = await _run(
        target,
        "SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = %s AND tablename = %s "
        "ORDER BY indexname",
        [schema, table],
        read_only=True,
        max_rows=MAX_ROWS,
    )
    kinds = {"p": "primary_key", "f": "foreign_key", "u": "unique"}
    return {
        "schema": schema,
        "table": table,
        "columns": [
            {"name": n, "type": t, "nullable": nullable, "default": d}
            for n, t, nullable, d in columns["rows"]
        ],
        "constraints": [
            {"name": n, "kind": kinds[k], "definition": d} for n, k, d in constraints["rows"]
        ],
        "indexes": [{"name": n, "definition": d} for n, d in indexes["rows"]],
    }


@read
async def query(target: Target, sql: Sql, params: Params = None, max_rows: MaxRows = 200) -> JSON:
    """Run one read-only statement inside a READ ONLY transaction and return its rows."""
    return await _run(target, sql, params, read_only=True, max_rows=max_rows)


@write
async def execute(target: Target, sql: Sql, params: Params = None, max_rows: MaxRows = 200) -> JSON:
    """Run one statement that may write (INSERT/UPDATE/DELETE/DDL) and commit it."""
    return await _run(target, sql, params, read_only=False, max_rows=max_rows)


ADAPTER = Adapter(
    service=InstanceType.SUPABASE,
    tools=(list_tables, describe_table, query, execute),
    secret_fragments=dsn_fragments,
)
