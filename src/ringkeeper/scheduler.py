"""The routines scheduler: every half minute, start what's due (services.routines.tick).

It runs in the api service, next to the router: in the desktop app while the
computer is awake (a missed run is made up once on wake), and 24/7 on a server.
"""

import logging
import threading
from datetime import UTC, datetime

from ringkeeper.config import Settings
from ringkeeper.db import session_scope
from ringkeeper.services import routine_events, routines

logger = logging.getLogger(__name__)

TICK_SECONDS = 30.0


class Scheduler:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def tick(self) -> int:
        now = datetime.now(UTC)
        # Watched services first, outside any transaction: a call the gateway refuses
        # writes to the audit log, which the tick's write lock would hold up.
        with session_scope(commit=False) as session:
            watches = routines.due_watches(session, self.settings, now)
        fetched = routine_events.fetch_all(watches, now)
        with session_scope() as session:
            started = routines.tick(session, self.settings, now, fetched)
        for one in started:
            if one.run.skipped:
                logger.info("routine run skipped: %s", one.run.skipped)
        return len(started)

    def run_forever(self, stop: threading.Event) -> None:
        while not stop.is_set():
            try:
                self.tick()
            except Exception:
                logger.exception("routines pass failed")
            stop.wait(TICK_SECONDS)
