"""Engines, upserts and locks for both databases.

SQLite runs with WAL (readers never block, and are never blocked) and foreign
keys on. A session that may write starts with BEGIN IMMEDIATE: one writer at
a time, which stands in for Postgres's row locks (FOR UPDATE, SKIP LOCKED),
since SQLite ignores those clauses. Read-only sessions (`READ_ONLY`, what
`session_scope(commit=False)` uses) start a plain BEGIN instead. Desktop mode
is one process, so that's enough.

The one thing SQLite can't do that Postgres can: a second write transaction
in the same thread while the first is still open would wait for itself. That
raises NestedWriteError at once instead of hanging.
"""

import sqlite3
import threading
import uuid
from typing import Any

from sqlalchemy import Connection, Engine, Table, create_engine, event, text
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.orm import DeclarativeBase, Session
from sqlalchemy.pool import NullPool

from ringkeeper.errors import RingkeeperError
from ringkeeper.storage import events

SQLITE_BUSY_SECONDS = 30
READ_ONLY = "ringkeeper_read_only"  # an execution option: this session only reads

Insert = postgresql.Insert | sqlite.Insert


class NestedWriteError(RingkeeperError):
    pass


class _Connection(sqlite3.Connection):
    """A SQLite connection that carries its not-yet-committed notifications,
    and whether it holds the write lock (and for which database file)."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.pending: list[events.Notification] = []
        self.writing: str | None = None


class _Writers(threading.local):
    def __init__(self) -> None:
        self.files: set[str] = set()  # databases this thread holds the write lock on


_writers = _Writers()


def _held() -> set[str]:
    return _writers.files


def _raw(connection: Any) -> Any:
    return getattr(connection, "dbapi_connection", connection)  # pooled wrapper or raw


def _pending(connection: Any) -> list[events.Notification]:
    return getattr(_raw(connection), "pending", [])


def _release(connection: Any) -> None:
    raw = _raw(connection)
    if getattr(raw, "writing", None) is not None:
        _held().discard(raw.writing)
        raw.writing = None


def is_sqlite(url: str) -> bool:
    return url.startswith("sqlite")


def make_engine(url: str, *, connect_timeout: int = 10, pooled: bool = True) -> Engine:
    if not is_sqlite(url):
        return create_engine(
            url,
            pool_pre_ping=True,
            connect_args={"connect_timeout": connect_timeout},
            **({} if pooled else {"poolclass": NullPool}),
        )
    engine = create_engine(
        url,
        connect_args={
            "timeout": SQLITE_BUSY_SECONDS,
            "check_same_thread": False,
            "factory": _Connection,
        },
        **({} if pooled else {"poolclass": NullPool}),
    )
    _prepare_sqlite(engine)
    return engine


def _uuid_text(value: str | None) -> str | None:
    """rk_uuid(): a stored UUID (32 hex digits on SQLite) in its usual dashed form."""
    return None if value is None else str(uuid.UUID(value))


def _prepare_sqlite(engine: Engine) -> None:
    @event.listens_for(engine, "connect")
    def _connect(dbapi_connection: _Connection, _record: Any) -> None:
        # Transactions are begun explicitly below, not by the driver.
        dbapi_connection.isolation_level = None
        for pragma in (
            "journal_mode = WAL",
            "synchronous = NORMAL",
            "foreign_keys = ON",
            f"busy_timeout = {SQLITE_BUSY_SECONDS * 1000}",
        ):
            dbapi_connection.execute(f"PRAGMA {pragma}")
        pending = dbapi_connection.pending

        def rk_notify(channel: str, payload: str | None) -> None:
            pending.append((channel, payload or ""))

        dbapi_connection.create_function("rk_notify", 2, rk_notify)
        dbapi_connection.create_function("rk_uuid", 1, _uuid_text, deterministic=True)

    database = str(engine.url)

    @event.listens_for(engine, "begin")
    def _begin(connection: Connection) -> None:
        if connection.get_execution_options().get(READ_ONLY):
            connection.exec_driver_sql("BEGIN")
            return
        if database in _held():
            raise NestedWriteError(
                "a write transaction is already open in this thread; SQLite allows one "
                "writer at a time (read with session_scope(commit=False), or reuse the session)"
            )
        connection.exec_driver_sql("BEGIN IMMEDIATE")
        _held().add(database)
        _raw(connection.connection).writing = database

    # Notifications go out only after the commit has happened (Postgres's
    # NOTIFY semantics), and are dropped on rollback.
    dialect = engine.dialect
    commit, rollback = dialect.do_commit, dialect.do_rollback

    def do_commit(dbapi_connection: Any) -> None:
        try:
            commit(dbapi_connection)
        finally:
            _release(dbapi_connection)
        pending = _pending(dbapi_connection)
        sent = list(pending)
        pending.clear()
        events.publish(sent)

    def do_rollback(dbapi_connection: Any) -> None:
        _pending(dbapi_connection).clear()
        try:
            rollback(dbapi_connection)
        finally:
            _release(dbapi_connection)

    dialect.do_commit = do_commit  # type: ignore[method-assign]
    dialect.do_rollback = do_rollback  # type: ignore[method-assign]


def insert(session: Session, entity: type[DeclarativeBase] | Table) -> Insert:
    """An INSERT that can take ON CONFLICT ... DO UPDATE / DO NOTHING, for this database."""
    if session.get_bind().dialect.name == "sqlite":
        return sqlite.insert(entity)
    return postgresql.insert(entity)


def lock(session: Session, key: int) -> None:
    """Serialize a critical section until the transaction ends.

    Postgres: a transaction-scoped advisory lock. SQLite: nothing to do, since
    BEGIN IMMEDIATE already lets only one writer in at a time.
    """
    if session.get_bind().dialect.name != "sqlite":
        session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})
