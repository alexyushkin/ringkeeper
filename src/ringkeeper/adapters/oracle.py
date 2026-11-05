"""Oracle Database, through python-oracledb in thin mode (no Oracle client to install).

The secret is JSON:

    {"dsn": "db.example.com:1521/orclpdb1", "user": "app", "password": "...",
     "wallet": "<the wallet .zip, base64>", "wallet_password": "..."}

An Autonomous Database needs its wallet: the .zip holds ewallet.pem and tnsnames.ora, and
`dsn` is then one of its names, like acme_high. Only those two files are unpacked, into a
private folder that lasts for the call.

The same checks as Postgres: `query` runs in a READ ONLY transaction, `execute` goes through
ringkeeper.destructive, and a plain UPDATE or DELETE can be undone.

Oracle differs in one way that matters. A function declared with PRAGMA
AUTONOMOUS_TRANSACTION writes even inside a READ ONLY transaction, so read-only alone isn't
enough: `query` also refuses any function it doesn't know, and that call has to go through
`execute`, where it waits. Statements are single by the driver's own rules, and PL/SQL
(BEGIN, DECLARE) needs the name typed like anything that can't be checked.
"""

import base64
import binascii
import contextlib
import datetime
import decimal
import io
import json
import re
import tempfile
import zipfile
from collections.abc import Callable, Generator
from dataclasses import replace
from functools import partial
from pathlib import Path
from typing import Annotated, Any, cast

import anyio.to_thread
import oracledb
from pydantic import Field

from ringkeeper import netpolicy
from ringkeeper.adapters import sql_paging
from ringkeeper.adapters.base import JSON, Adapter, Target, ToolError, ToolKind, read, write
from ringkeeper.adapters.undo_sql import CantUndoError, SqlUndo, plan
from ringkeeper.models import InstanceType
from ringkeeper.sql_text import reads_only

CONNECT_TIMEOUT = 10  # seconds
READ_TIMEOUT_MS = 15_000
WRITE_TIMEOUT_MS = 30_000
MAX_ROWS = 1000
MAX_UNDO_ROWS = 5000
MAX_WALLET_BYTES = 1_000_000
WALLET_FILES = ("ewallet.pem", "tnsnames.ora")
BIND = r":[A-Za-z0-9_]+"
ORACLE_SYSTEM = (
    "SYS", "SYSTEM", "OUTLN", "DBSNMP", "XDB", "CTXSYS", "MDSYS", "ORDSYS", "WMSYS",
    "APEX_PUBLIC_USER",
)  # fmt: skip
# Oracle's own plain functions, on top of sql_text.HARMLESS.
HARMLESS = frozenset({
    "nvl", "nvl2", "decode", "instr", "lpad", "rpad", "add_months", "months_between",
    "last_day", "next_day", "sys_context", "to_clob", "dbms_lob.substr", "listagg", "rownum",
    "row_number", "rank", "dense_rank", "lag", "lead", "initcap", "regexp_like",
    "regexp_substr", "regexp_replace", "number", "varchar2", "nvarchar2", "date",
})  # fmt: skip
HOST = re.compile(r"\(\s*host\s*=\s*([^)\s]+)\s*\)", re.I)

Sql = Annotated[str, Field(description="Exactly one SQL statement, without a closing semicolon.")]
Params = Annotated[list[Any] | None, Field(description="Values for :1, :2 binds, in order.")]
MaxRows = Annotated[int, Field(ge=1, le=MAX_ROWS, description="Cap on rows returned.")]
oracledb.defaults.fetch_lobs = False  # a LOB comes back as its text or bytes


# Tests swap this for a fake; production talks to the database.
def connector(**kwargs: Any) -> Any:
    return oracledb.connect(**kwargs)


def settings(value: str) -> JSON:
    try:
        data: Any = json.loads(value)
    except ValueError:
        raise ToolError("this Oracle connection's secret isn't in the expected form") from None
    fields = cast(JSON, data) if isinstance(data, dict) else {}
    if not all(str(fields.get(k) or "").strip() for k in ("dsn", "user", "password")):
        raise ToolError("this Oracle connection needs its connect string, user and password")
    if fields.get("wallet"):
        _unpacked(str(fields["wallet"]))
    return fields


def check_address(fields: JSON) -> None:
    """Not this computer, nor Ringkeeper itself. A wallet's name is checked by its tnsnames."""
    for host in hosts_of(str(fields["dsn"])):
        netpolicy.check_host(host)


def hosts_of(dsn: str) -> list[str]:
    if found := HOST.findall(dsn):
        return found
    if "/" not in dsn and ":" not in dsn:
        return []  # a name from the wallet's tnsnames.ora
    bare = re.sub(r"^[a-z]+://", "", dsn.strip(), flags=re.I)
    return [re.split(r"[:/?]", bare, maxsplit=1)[0]]


def secret_fragments(value: str) -> list[str]:
    try:
        data: Any = json.loads(value)
    except ValueError:
        return []
    fields = cast(JSON, data) if isinstance(data, dict) else {}
    return [str(fields[k]) for k in ("password", "wallet_password", "wallet") if fields.get(k)]


def _unpacked(wallet: str) -> dict[str, bytes]:
    """The two files Ringkeeper uses from a wallet. Nothing else in it is read, and no name
    in it picks where anything goes."""
    try:
        raw = base64.b64decode(wallet, validate=True)
        if len(raw) > MAX_WALLET_BYTES:
            raise ToolError("that wallet is too big to be a wallet")
        with zipfile.ZipFile(io.BytesIO(raw)) as packed:
            names = {Path(n).name: n for n in packed.namelist()}
            found = {w: packed.read(names[w]) for w in WALLET_FILES if w in names}
    except (binascii.Error, ValueError, zipfile.BadZipFile):
        raise ToolError("that isn't a wallet .zip file") from None
    if "ewallet.pem" not in found:
        raise ToolError("this wallet has no ewallet.pem: download it again from the console")
    return found


@contextlib.contextmanager
def _connected(target: Target) -> Generator[Any]:
    fields = settings(target.secret.reveal())
    try:
        check_address(fields)
    except netpolicy.AddressNotAllowedError as error:
        raise ToolError(f"instance '{target.name}' can't be used: {error}") from None
    options: JSON = {
        "user": fields["user"], "password": fields["password"], "dsn": fields["dsn"],
        "tcp_connect_timeout": CONNECT_TIMEOUT,
    }  # fmt: skip
    with tempfile.TemporaryDirectory(prefix="rk-wallet-") as folder:
        if fields.get("wallet"):
            for name, content in _unpacked(str(fields["wallet"])).items():
                (Path(folder) / name).write_bytes(content)
            options |= {"config_dir": folder, "wallet_location": folder}
            if fields.get("wallet_password"):
                options["wallet_password"] = fields["wallet_password"]
        try:
            connection = connector(**options)
        except oracledb.Error as error:
            raise ToolError(f"cannot connect to '{target.name}': {_message(error)}") from None
        except OSError:  # the driver lets a name that doesn't resolve through as it is
            raise ToolError(f"cannot reach '{target.name}': check its connect string") from None
        try:
            yield connection
        finally:
            connection.close()


def _message(error: oracledb.Error) -> str:
    """The first line only: the rest can quote data."""
    text = str(error).strip()
    return text.splitlines()[0] if text else type(error).__name__


def _json_safe(value: object) -> object:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, decimal.Decimal):
        return str(value)
    if isinstance(value, datetime.datetime | datetime.date):
        return value.isoformat()
    if isinstance(value, bytes | bytearray):
        return {"base64": base64.b64encode(bytes(value)).decode()}
    return str(value)


def _kept(value: object) -> object:
    """A value as undo keeps it: JSON that says what it was, so it goes back the same."""
    if isinstance(value, datetime.datetime | datetime.date):
        return {"$date": value.isoformat()}
    return _json_safe(value)


def _back(value: object) -> object:
    """A kept value as it's bound again."""
    if not isinstance(value, dict):
        return value
    fields = cast(JSON, value)
    if "$date" in fields:
        return datetime.datetime.fromisoformat(str(fields["$date"]))
    if "base64" in fields:
        return base64.b64decode(str(fields["base64"]))
    return fields


def _clean(statement: str) -> str:
    return statement.strip().rstrip(";").rstrip()


def _rows(
    cursor: Any, max_rows: int, shown: Callable[[object], object] = _json_safe, offset: int = 0
) -> JSON:
    if cursor.description is None:
        return {"columns": [], "rows": [], "row_count": 0, "truncated": False}
    skipped = 0
    while skipped < offset:  # Oracle doesn't say how many rows there are until they're read
        passed = cursor.fetchmany(min(offset - skipped, MAX_ROWS))
        if not passed:
            break
        skipped += len(passed)
    fetched = cursor.fetchmany(max_rows + 1)
    return sql_paging.rows(
        [column[0] for column in cursor.description],
        [[shown(v) for v in row] for row in fetched],
        max_rows, offset, None,
    )  # fmt: skip


def _blocking(
    target: Target,
    statements: list[tuple[str, list[Any]]],
    read_only: bool,
    max_rows: int,
    shown: Callable[[object], object] = _json_safe,
    offset: int = 0,
) -> JSON:
    """One or more statements in one transaction: all of them, or none."""
    with _connected(target) as connection:
        connection.call_timeout = READ_TIMEOUT_MS if read_only else WRITE_TIMEOUT_MS
        cursor = connection.cursor()
        try:
            if read_only:
                cursor.execute("SET TRANSACTION READ ONLY")
            result: JSON = {}
            count = 0
            for statement, params in statements:
                cursor.execute(_clean(statement), params or [])
                result = _rows(cursor, max_rows, shown, offset)
                count += max(int(cursor.rowcount or 0), 0)
            if read_only:
                connection.rollback()
            else:
                connection.commit()
                result = sql_paging.after_write(result) | {"rowcount": count}
            return result
        except oracledb.Error as error:
            connection.rollback()
            raise ToolError(_message(error)) from None
        finally:
            cursor.close()


async def _run(
    target: Target,
    statement: str,
    params: list[Any] | None,
    *,
    read_only: bool,
    max_rows: int = MAX_ROWS,
    shown: Callable[[object], object] = _json_safe,
    offset: int = 0,
) -> JSON:
    work = partial(
        _blocking, target, [(statement, params or [])], read_only, max_rows, shown, offset
    )
    return await anyio.to_thread.run_sync(work)


def only_reads(sql: str) -> bool:
    return reads_only(sql, HARMLESS)


async def probe(target: Target) -> str:
    """The database's name and version (the Test button)."""
    found = await _run(
        target, "SELECT banner FROM v$version WHERE ROWNUM = 1", None, read_only=True, max_rows=1
    )
    rows = cast(list[list[Any]], found["rows"])
    return f"Connected to {rows[0][0] if rows else 'Oracle'}."


@read
async def list_tables(
    target: Target,
    schema: Annotated[str | None, Field(description="Owner; null: every non-system one.")] = None,
) -> JSON:
    """List tables and views."""
    marks = ", ".join(f":{i + 1}" for i in range(len(ORACLE_SYSTEM)))
    where = f"owner NOT IN ({marks})"
    params: list[Any] = list(ORACLE_SYSTEM)
    if schema:
        where += f" AND owner = :{len(params) + 1}"
        params.append(schema.upper())
    found = await _run(
        target,
        f"SELECT owner, table_name, 'table' FROM all_tables WHERE {where} "  # noqa: S608 (binds)
        f"UNION ALL SELECT owner, view_name, 'view' FROM all_views WHERE {where} ORDER BY 1, 2",
        params + params,
        read_only=True,
    )
    rows = cast(list[list[Any]], found["rows"])
    return {
        "tables": [{"schema": s, "name": n, "type": t} for s, n, t in rows],
        "truncated": found["truncated"],
    }


@read
async def describe_table(
    target: Target,
    table: Annotated[str, Field(description="Table or view name.")],
    schema: Annotated[str | None, Field(description="Owner; null: the current one.")] = None,
) -> JSON:
    """Columns, primary key, foreign keys and indexes of one table."""
    owner = "NVL(:2, SYS_CONTEXT('USERENV', 'CURRENT_SCHEMA'))"
    params = [table.upper(), schema.upper() if schema else None]
    columns = await _run(
        target,
        "SELECT column_name, data_type, nullable, data_default FROM all_tab_columns "  # noqa: S608
        f"WHERE table_name = :1 AND owner = {owner} ORDER BY column_id",
        params,
        read_only=True,
    )
    if not columns["rows"]:
        raise ToolError(f"table {table} does not exist or is not visible")
    constraints = await _run(
        target,
        "SELECT constraint_name, constraint_type FROM all_constraints "  # noqa: S608
        f"WHERE table_name = :1 AND owner = {owner} AND constraint_type IN ('P', 'R', 'U') "
        "ORDER BY constraint_name",
        params,
        read_only=True,
    )
    indexes = await _run(
        target,
        "SELECT index_name, uniqueness FROM all_indexes "  # noqa: S608
        f"WHERE table_name = :1 AND table_owner = {owner} ORDER BY index_name",
        params,
        read_only=True,
    )
    kinds = {"P": "primary_key", "R": "foreign_key", "U": "unique"}
    return {
        "table": table,
        "columns": [
            {"name": n, "type": t, "nullable": nullable == "Y", "default": d}
            for n, t, nullable, d in cast(list[list[Any]], columns["rows"])
        ],
        "constraints": [
            {"name": n, "kind": kinds[k]} for n, k in cast(list[list[Any]], constraints["rows"])
        ],
        "indexes": [
            {"name": n, "unique": u == "UNIQUE"} for n, u in cast(list[list[Any]], indexes["rows"])
        ],
    }


@read
async def query(
    target: Target,
    sql: Sql,
    params: Params = None,
    max_rows: MaxRows = 200,
    offset: sql_paging.RowOffset = 0,
) -> JSON:
    """Run one read-only statement inside a READ ONLY transaction and return its rows. A call
    to a function Ringkeeper doesn't know is refused here: use execute, which waits. A long
    result comes in parts: the same statement with offset=next_offset gives the next rows (use
    ORDER BY, so they keep their order). Oracle doesn't say how many there are in all."""
    if not only_reads(sql):
        raise ToolError(
            "that isn't shown to only read: Oracle functions can write even in a read-only "
            "transaction. Use execute, which waits for approval if it has to."
        )
    return await _run(target, sql, params, read_only=True, max_rows=max_rows, offset=offset)


@write
async def execute(target: Target, sql: Sql, params: Params = None, max_rows: MaxRows = 200) -> JSON:
    """Run one statement that may write (INSERT/UPDATE/DELETE/DDL) and commit it."""
    return await _run(target, sql, params, read_only=only_reads(sql), max_rows=max_rows)


def _reads(_target: Target, args: JSON) -> ToolKind:
    """Reading never waits for approval, even through execute on production."""
    sql = args.get("sql")
    return ToolKind.READ if isinstance(sql, str) and only_reads(sql) else ToolKind.WRITE


def _name(identifier: str) -> str:
    """A column as Oracle names it: unquoted ones fold to upper case."""
    identifier = identifier.strip()
    if identifier.startswith('"'):
        return identifier[1:-1].replace('""', '"')
    return identifier.upper()


def _quoted(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _dicts(found: JSON) -> list[JSON]:
    columns = cast(list[str], found["columns"])
    return [dict(zip(columns, row, strict=True)) for row in cast(list[list[Any]], found["rows"])]


class OracleUndo(SqlUndo):
    """A plain UPDATE or DELETE, read and put back by primary key (adapters.undo_sql)."""

    def why_not(self, args: JSON) -> str | None:
        try:
            plan(str(args.get("sql") or ""), cast(list[Any] | None, args.get("params")), BIND)
        except CantUndoError as error:
            return str(error)
        return None

    async def _keys(self, target: Target, table: str) -> list[str]:
        owner, _, bare = table.rpartition(".")
        found = await _run(
            target,
            "SELECT cc.column_name FROM all_constraints c JOIN all_cons_columns cc "
            "ON c.owner = cc.owner AND c.constraint_name = cc.constraint_name "
            "WHERE c.constraint_type = 'P' AND c.table_name = :1 "
            "AND c.owner = NVL(:2, SYS_CONTEXT('USERENV', 'CURRENT_SCHEMA')) "
            "ORDER BY cc.position",
            [_name(bare), _name(owner) if owner else None],
            read_only=True,
        )
        return [str(r[0]) for r in cast(list[list[Any]], found["rows"])]

    async def before(self, target: Target, args: JSON) -> JSON:
        found = plan(str(args.get("sql") or ""), cast(list[Any] | None, args.get("params")), BIND)
        keys = await self._keys(target, found.table)
        if not keys:
            raise CantUndoError(
                f"{found.table} has no primary key, so its rows can't be told apart later"
            )
        columns = [_name(c) for c in found.columns]
        picked = ", ".join(map(_quoted, [*keys, *columns])) if found.kind == "update" else "*"
        where = f" WHERE {found.where}" if found.where else ""
        rows = await _run(
            target,
            f"SELECT {picked} FROM {found.table}{where}",  # noqa: S608 (the statement's own WHERE)
            found.where_params,
            read_only=True,
            max_rows=MAX_UNDO_ROWS,
            shown=_kept,
        )
        if rows["truncated"]:
            raise CantUndoError(f"it changes more than {MAX_UNDO_ROWS:,} rows")
        return {
            "kind": found.kind, "table": found.table, "keys": keys, "columns": columns,
            "rows": _dicts(rows),
        }  # fmt: skip

    async def current(self, target: Target, kept: JSON) -> JSON:
        table, keys = str(kept["table"]), cast(list[str], kept["keys"])
        columns = cast(list[str], kept["columns"]) if kept["kind"] == "update" else None
        picked = ", ".join(map(_quoted, [*keys, *columns])) if columns else "*"
        match = " AND ".join(f"{_quoted(k)} = :{i + 1}" for i, k in enumerate(keys))
        found: list[JSON] = []
        for row in cast(list[JSON], kept["rows"]):
            hit = await _run(
                target,
                f"SELECT {picked} FROM {table} WHERE {match}",  # noqa: S608 (names from the database)
                [_back(row[k]) for k in keys],
                read_only=True,
                max_rows=1,
                shown=_kept,
            )
            found += _dicts(hit)
        return {"rows": found}

    async def restore(self, target: Target, kept: JSON) -> JSON:
        table, keys = str(kept["table"]), cast(list[str], kept["keys"])
        rows = cast(list[JSON], kept["rows"])
        if not rows:
            return {"restored": 0}
        statements: list[tuple[str, list[Any]]] = []
        for row in rows:
            if kept["kind"] == "update":
                columns = [c for c in row if c not in keys]
                sets = ", ".join(f"{_quoted(c)} = :{i + 1}" for i, c in enumerate(columns))
                match = " AND ".join(
                    f"{_quoted(k)} = :{len(columns) + i + 1}" for i, k in enumerate(keys)
                )
                values = [_back(row[c]) for c in [*columns, *keys]]
                statements.append((f"UPDATE {table} SET {sets} WHERE {match}", values))  # noqa: S608
            else:
                names = ", ".join(map(_quoted, row))
                marks = ", ".join(f":{i + 1}" for i in range(len(row)))
                statements.append(
                    (f"INSERT INTO {table} ({names}) VALUES ({marks})",  # noqa: S608
                     [_back(v) for v in row.values()])
                )  # fmt: skip
        work = partial(_blocking, target, statements, False, 1)
        await anyio.to_thread.run_sync(work)
        return {"restored": len(rows)}


execute = replace(execute, classify=_reads, undo=OracleUndo())

ADAPTER = Adapter(
    service=InstanceType.ORACLE,
    tools=(list_tables, describe_table, query, execute),
    secret_fragments=secret_fragments,
)
