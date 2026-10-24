"""Postgres LISTEN in a background thread: set an event whenever a channel fires.

Every loop that uses this also polls, so a dropped connection only slows
things down until it reconnects.
"""

import threading
from collections.abc import Callable, Iterable

import psycopg
from psycopg import sql

from ringkeeper.config import Settings


def listen_forever(
    settings: Settings,
    channels: Iterable[str],
    on_notify: Callable[[str, str], None],
    stop: threading.Event,
) -> None:
    """Call on_notify(channel, payload) for each notification until stop is set."""
    url = settings.database_url.replace("postgresql+psycopg://", "postgresql://")
    names = list(channels)
    while not stop.is_set():
        try:
            with psycopg.connect(url, autocommit=True) as conn:
                for name in names:
                    conn.execute(sql.SQL("LISTEN {}").format(sql.Identifier(name)))
                while not stop.is_set():
                    for notify in conn.notifies(timeout=5.0):
                        on_notify(notify.channel, notify.payload)
        except psycopg.Error:
            stop.wait(5.0)


def start_listener(
    settings: Settings,
    channels: Iterable[str],
    on_notify: Callable[[str, str], None],
    stop: threading.Event,
    name: str,
) -> threading.Thread:
    thread = threading.Thread(
        target=listen_forever, args=(settings, list(channels), on_notify, stop), daemon=True,
        name=name,
    )  # fmt: skip
    thread.start()
    return thread
