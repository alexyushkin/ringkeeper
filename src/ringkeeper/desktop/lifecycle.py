"""Stopping the desktop backend from inside: the shell starts it again.

A restored backup replaces the database at the next start (services.backup), so
the backend stops itself; the shell sees it go and starts it again, which shows
the window's "restarting" page for a moment.
"""

import threading
from collections.abc import Callable

_stop: Callable[[], None] | None = None


def on_stop(stop: Callable[[], None]) -> None:
    """desktop.server: how to stop this backend."""
    global _stop  # noqa: PLW0603 (one backend per process)
    _stop = stop


def restart_soon(delay: float = 1.5) -> bool:
    """Stop shortly (after this request's answer is sent). False outside the desktop app."""
    if _stop is None:
        return False
    timer = threading.Timer(delay, _stop)
    timer.daemon = True
    timer.start()
    return True
