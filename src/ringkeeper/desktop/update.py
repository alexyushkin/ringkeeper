"""An app update the shell has downloaded, and whether the user asked to restart.

The shell checks for updates and downloads them; it never restarts on its own.
It tells the backend what's ready, the window shows "Update ready, restart when
you're done", and the shell installs once the user asks and nothing is running.
In memory: a restart forgets it, and the shell tells the backend again.
"""

import threading
from dataclasses import dataclass


@dataclass(frozen=True)
class Ready:
    version: str
    notes: str


class Updates:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.ready: Ready | None = None
        self.restart_requested = False

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


updates = Updates()
