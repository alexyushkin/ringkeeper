"""Listen in a background thread: call back whenever a channel fires.

Postgres LISTEN in server mode, the in-process bus in desktop mode (see
storage.events). Every loop that uses this also polls, so a dropped
connection only slows things down until it reconnects.
"""

import threading
from collections.abc import Callable, Iterable

import psycopg

from ringkeeper.config import Settings
from ringkeeper.storage import events


def listen_forever(
    settings: Settings,
    channels: Iterable[str],
    on_notify: Callable[[str, str], None],
    stop: threading.Event,
) -> None:
    """Call on_notify(channel, payload) for each notification until stop is set."""
    names = list(channels)
    while not stop.is_set():
        try:
            subscription = events.subscribe(settings.database_url, names)
            try:
                while not stop.is_set():
                    for channel, payload in subscription.get(timeout=5.0):
                        on_notify(channel, payload)
            finally:
                subscription.close()
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
