"""The Open link from Slack or Discord, on the desktop: bring the window up there.

GET /open (api.channels_routes) records the page; the shell sees it in the
status it reads every few seconds and opens its window at that page, once.
"""

import threading
import time
import uuid
from typing import Any

FRESH_SECONDS = 60.0

_lock = threading.Lock()
_asked: dict[str, Any] | None = None


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
