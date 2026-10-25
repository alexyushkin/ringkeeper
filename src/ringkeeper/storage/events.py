"""Wake-ups between parts of Ringkeeper: NOTIFY on Postgres, a bus on SQLite.

Postgres delivers a NOTIFY when its transaction commits, to every listening
connection in any process. On SQLite (desktop mode, one process) a write
queues its notifications on its connection, from `notify()` or from a trigger
calling `rk_notify()`, and they're published to this process's subscribers
right after the commit (dropped on rollback). Other processes, such as a CLI
run beside the desktop app, aren't told; every listener also polls.
"""

import queue
import threading
from collections.abc import Iterable
from typing import Protocol

import psycopg
from psycopg import sql
from sqlalchemy import text
from sqlalchemy.orm import Session

Notification = tuple[str, str]  # channel, payload


class Subscription(Protocol):
    def get(self, timeout: float) -> list[Notification]:
        """Wait up to `timeout` seconds; return what arrived (all of it, as soon as any)."""
        ...

    def close(self) -> None: ...


def notify(session: Session, channel: str, payload: str = "") -> None:
    """Tell listeners on `channel`, once this session's transaction commits."""
    function = "rk_notify" if session.get_bind().dialect.name == "sqlite" else "pg_notify"
    session.execute(
        text(f"SELECT {function}(:channel, :payload)"), {"channel": channel, "payload": payload}
    )


def subscribe(database_url: str, channels: Iterable[str]) -> Subscription:
    if database_url.startswith("sqlite"):
        return _LocalSubscription(set(channels))
    return _PostgresSubscription(database_url, list(channels))


class _PostgresSubscription:
    def __init__(self, database_url: str, channels: list[str]) -> None:
        url = database_url.replace("postgresql+psycopg://", "postgresql://")
        self._conn = psycopg.connect(url, autocommit=True)
        for name in channels:
            self._conn.execute(sql.SQL("LISTEN {}").format(sql.Identifier(name)))

    def get(self, timeout: float) -> list[Notification]:
        notifies = self._conn.notifies(timeout=timeout, stop_after=1)
        found = [(n.channel, n.payload) for n in notifies]
        if found:  # anything else already delivered comes along without waiting
            found += [(n.channel, n.payload) for n in self._conn.notifies(timeout=0)]
        return found

    def close(self) -> None:
        self._conn.close()


# The in-process bus (SQLite).
_subscribers: set["_LocalSubscription"] = set()
_lock = threading.Lock()


class _LocalSubscription:
    def __init__(self, channels: set[str]) -> None:
        self.channels = channels
        self._queue: queue.SimpleQueue[Notification] = queue.SimpleQueue()
        with _lock:
            _subscribers.add(self)

    def put(self, item: Notification) -> None:
        self._queue.put(item)

    def get(self, timeout: float) -> list[Notification]:
        try:
            found = [self._queue.get(timeout=timeout)]
        except queue.Empty:
            return []
        while True:
            try:
                found.append(self._queue.get_nowait())
            except queue.Empty:
                return found

    def close(self) -> None:
        with _lock:
            _subscribers.discard(self)


def publish(items: Iterable[Notification]) -> None:
    """Hand committed notifications to this process's subscribers."""
    with _lock:
        subscribers = list(_subscribers)
    for channel, payload in items:
        for subscriber in subscribers:
            if channel in subscriber.channels:
                subscriber.put((channel, payload))
