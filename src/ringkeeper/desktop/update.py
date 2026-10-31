"""App updates: what the shell has found and downloaded, and what the user asked for.

The shell checks for updates (on its own every 6 hours unless "Check
automatically" is off, or when the user asks: Settings > About or the tray)
and downloads what it finds; it never restarts on its own. It reports each
check here, the window shows the result ("You're up to date", "Update
available", or what went wrong), and the shell installs once the user asks
and nothing is running.

A check the window asks for is a request with an id: the shell sees it in
GET /desktop/status, checks, and reports with that id. In memory: a restart
forgets it all, and the shell tells the backend again.
"""

import secrets
import threading
import time
from dataclasses import dataclass
from typing import Literal

# The shell reports what it's doing, then how the check ended.
State = Literal["checking", "downloading", "current", "ready", "failed"]
DONE: tuple[State, ...] = ("current", "ready", "failed")
# A request the shell hasn't picked up by then: it isn't checking (a build without updates).
UNANSWERED_SECONDS = 30.0


@dataclass(frozen=True)
class Ready:
    version: str
    notes: str


@dataclass(frozen=True)
class Checked:
    """The last check that ended: when, how, and (failed) why, in plain words."""

    at: float  # unix time
    state: State
    error: str | None = None


class Updates:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.ready: Ready | None = None
        self.restart_requested = False
        self.request: str | None = None  # a check the window asked for, not yet done
        self.requested_at = 0.0
        self.working: State | None = None  # checking or downloading, now
        self.downloading: str | None = None  # the version
        self.last: Checked | None = None

    def offer(self, version: str, notes: str) -> None:
        with self._lock:
            if self.ready is None or self.ready.version != version:
                self.restart_requested = False
            self.ready = Ready(version, notes)

    def request_restart(self) -> bool:
        with self._lock:
            if self.ready is None:
                return False
            self.restart_requested = True
            return True

    def request_check(self) -> str:
        """Ask the shell to check now; asking again while it's pending changes nothing."""
        with self._lock:
            if self.request is None:
                self.request = secrets.token_urlsafe(8)
                self.requested_at = time.monotonic()
            return self.request

    def report(
        self,
        state: State,
        *,
        request: str | None = None,
        version: str | None = None,
        error: str | None = None,
        at: float | None = None,
    ) -> None:
        """From the shell: a check started, is downloading, or ended."""
        with self._lock:
            if request is not None and request == self.request:
                self.request = None
            if state in DONE:
                self.working, self.downloading = None, None
                self.last = Checked(at or time.time(), state, error if state == "failed" else None)
            else:
                self.working = state
                self.downloading = version if state == "downloading" else None

    def unanswered(self) -> bool:
        """The window asked, and the shell hasn't started checking."""
        with self._lock:
            return (
                self.request is not None
                and self.working is None
                and time.monotonic() - self.requested_at > UNANSWERED_SECONDS
            )


updates = Updates()
