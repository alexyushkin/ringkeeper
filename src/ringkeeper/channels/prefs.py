"""What a channel sends, when, and about which clients and projects.

    {"events": ["approvals", "routines", "failures"],
     "quiet": {"from": "22:00", "to": "07:00", "tz": "America/New_York"},
     "clients": [], "projects": []}

Events: approvals (anything waiting for you: held calls, plans, questions, "which
project?"), finished (tasks that succeeded), routines (a routine's run, for
routines set to send a summary), failures (tasks that failed, timed out or ran
out of budget). No clients and no projects means all of them; otherwise a
project counts when it's listed or its client is.

During quiet hours nothing is sent; what came up is sent when they end (an
approval only if it's still waiting).
"""

from dataclasses import dataclass
from datetime import datetime, time
from typing import Any, cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ringkeeper.errors import RuleViolationError

APPROVALS, FINISHED, ROUTINES, FAILURES = "approvals", "finished", "routines", "failures"
EVENTS = (APPROVALS, FINISHED, ROUTINES, FAILURES)
DEFAULT_EVENTS = frozenset({APPROVALS, ROUTINES, FAILURES})


def _clock(value: object) -> time:
    try:
        hours, minutes = str(value).split(":")
        return time(int(hours), int(minutes))
    except ValueError:
        raise RuleViolationError(
            f"Enter quiet hours as a time, like 22:00 (not {value!r})."
        ) from None


def _zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        raise RuleViolationError(
            f"Choose a time zone from the list ({name!r} isn't one)."
        ) from None


@dataclass(frozen=True)
class Prefs:
    events: frozenset[str] = DEFAULT_EVENTS
    quiet_from: time | None = None
    quiet_to: time | None = None
    tz: str = "UTC"
    clients: frozenset[str] = frozenset()
    projects: frozenset[str] = frozenset()

    @classmethod
    def load(cls, doc: dict[str, Any] | None) -> "Prefs":
        """From a stored or posted doc; checks it (RuleViolationError)."""
        doc = doc or {}
        events = frozenset(str(e) for e in cast(list[Any], doc.get("events", DEFAULT_EVENTS)))
        if events - set(EVENTS):
            raise RuleViolationError(
                "Some choices weren't recognised. Refresh the page and try again."
            )
        quiet = cast(dict[str, Any] | None, doc.get("quiet") or None)
        tz = str((quiet or {}).get("tz") or doc.get("tz") or "UTC")
        _zone(tz)
        return cls(
            events=events,
            quiet_from=_clock(quiet["from"]) if quiet else None,
            quiet_to=_clock(quiet["to"]) if quiet else None,
            tz=tz,
            clients=frozenset(str(c) for c in cast(list[Any], doc.get("clients") or [])),
            projects=frozenset(str(p) for p in cast(list[Any], doc.get("projects") or [])),
        )

    def dump(self) -> dict[str, Any]:
        quiet = None
        if self.quiet_from is not None and self.quiet_to is not None:
            quiet = {
                "from": self.quiet_from.strftime("%H:%M"),
                "to": self.quiet_to.strftime("%H:%M"),
                "tz": self.tz,
            }
        return {
            "events": sorted(self.events, key=EVENTS.index),
            "quiet": quiet,
            "clients": sorted(self.clients),
            "projects": sorted(self.projects),
        }

    def quiet(self, now: datetime) -> bool:
        if self.quiet_from is None or self.quiet_to is None or self.quiet_from == self.quiet_to:
            return False
        local = now.astimezone(_zone(self.tz)).time()
        if self.quiet_from < self.quiet_to:  # 13:00-14:00
            return self.quiet_from <= local < self.quiet_to
        return local >= self.quiet_from or local < self.quiet_to  # 22:00-07:00

    def covers(self, project: str | None, client: str | None) -> bool:
        """Every client and project when none are chosen; a decision about no
        project yet ("which project?") always counts."""
        if (not self.clients and not self.projects) or project is None:
            return True
        return project in self.projects or (client is not None and client in self.clients)
