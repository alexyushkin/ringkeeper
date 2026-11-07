"""The Open link (Slack, Discord, Telegram, or an app using a pass), on the desktop.

GET /open (api.channels_routes) records the page; the shell sees it in the
status it reads every few seconds and opens its window at that page, once.

When no shell is there to take it (it hasn't asked for its status lately), the
link signs the browser that opened it in instead, with a one-time start code,
and lands it on the page. Either way the person ends up signed in at the page,
never at a "Loading..." that can't load.
"""

import threading
import time
import uuid
from collections.abc import Callable
from typing import Any

FRESH_SECONDS = 60.0
SHELL_SECONDS = 15.0  # it asks every 3 seconds

_lock = threading.Lock()
_asked: dict[str, Any] | None = None
_shell_seen: float | None = None
_sign_in: Callable[[str], str] | None = None  # page -> a one-time sign-in address


def shell_asked() -> None:
    """The shell read its status (with the launch secret): its window can open a page."""
    global _shell_seen  # noqa: PLW0603
    _shell_seen = time.monotonic()


def window_can_open() -> bool:
    seen = _shell_seen
    return seen is not None and time.monotonic() - seen < SHELL_SECONDS


def can_sign_in(make: Callable[[str], str] | None) -> None:
    """Set by the desktop backend at launch: how to sign a browser in at a page."""
    global _sign_in  # noqa: PLW0603
    _sign_in = make


def sign_in(path: str) -> str | None:
    return _sign_in(path) if _sign_in is not None else None


def ask(path: str) -> None:
    global _asked  # noqa: PLW0603 (one request at a time, for the shell)
    with _lock:
        _asked = {"id": uuid.uuid4().hex, "path": path, "at": time.monotonic()}


def pending() -> dict[str, str] | None:
    """The latest page asked for, while it's fresh: {"id", "path"}."""
    with _lock:
        if _asked is None or time.monotonic() - float(_asked["at"]) > FRESH_SECONDS:
            return None
        return {"id": str(_asked["id"]), "path": str(_asked["path"])}
