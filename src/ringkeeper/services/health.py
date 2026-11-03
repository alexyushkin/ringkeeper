"""The daily connection check: every connection's Test (reads only), once a day.

The result lands on the connection like a Test you pressed. A connection that was
working and stopped gets a message, once; so does a key 7 days before it expires,
when its expiry is known: GitHub says so when asked, and you can write one in on the
card (Details > Change). It runs while the app is open, a day after the last run.
"""

import logging
import threading
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ringkeeper.channels.prefs import FAILURES
from ringkeeper.config import Settings
from ringkeeper.db import session_scope
from ringkeeper.models import Instance, Setting
from ringkeeper.services import announce, probes

logger = logging.getLogger(__name__)

STATE_KEY = "health"
EVERY = timedelta(days=1)
WARN_DAYS = 7
_running = threading.Lock()


@dataclass(frozen=True)
class Report:
    checked: int
    failed: list[str]
    expiring: list[str]


def _state(session: Session) -> dict[str, Any]:
    row = session.get(Setting, STATE_KEY)
    return dict(row.value) if row else {}


def _save(session: Session, state: dict[str, Any]) -> None:
    row = session.get(Setting, STATE_KEY)
    if row is None:
        session.add(Setting(key=STATE_KEY, value=state))
    else:
        row.value = state


def due(session: Session, now: datetime | None = None) -> bool:
    now = now or datetime.now(UTC)
    at = _state(session).get("at")
    return not isinstance(at, str) or datetime.fromisoformat(at) + EVERY <= now


def days_left(expires: object, today: date | None = None) -> int | None:
    """Days until a key's expiry ("2027-01-31"), or None when it isn't known."""
    if not isinstance(expires, str) or not expires:
        return None
    try:
        return (date.fromisoformat(expires[:10]) - (today or datetime.now(UTC).date())).days
    except ValueError:
        return None


def check_all(settings: Settings, now: datetime | None = None) -> Report:
    """Test every connection, then say what's new. One at a time: each is a read."""
    now = now or datetime.now(UTC)
    with session_scope(commit=False) as session:
        rows = session.scalars(select(Instance).order_by(Instance.name)).all()
        before = [(i.name, i.title or i.name, i.last_test_ok) for i in rows]
    failed: list[str] = []
    for name, title, was_ok in before:
        try:
            result = probes.test_connection(name, by="daily check")
        except Exception:
            logger.exception("the daily check of %s failed", name)
            continue
        if not result.ok and was_ok is not False:  # was working, or never tested
            failed.append(f"{title}: {result.message}")
    with session_scope() as session:
        state = _state(session)
        warned: dict[str, str] = dict(state.get("warned") or {})
        expiring: list[str] = []
        for instance in session.scalars(select(Instance)):
            expires = (instance.config or {}).get("expires_at")
            left = days_left(expires, now.date())
            if left is None or left > WARN_DAYS or warned.get(instance.name) == expires:
                continue
            warned[instance.name] = str(expires)
            title = instance.title or instance.name
            expiring.append(
                f"{title}: its key has expired"
                if left < 0
                else f"{title}: key expires in {left} day{'' if left == 1 else 's'}"
            )
        _save(session, {"at": now.isoformat(), "warned": warned})
        if failed or expiring:
            lines = [f"Connection stopped working: {line}" for line in failed]
            lines += [f"Key expiring: {line}" for line in expiring]
            announce.send(session, settings, "\n".join(lines), event=FAILURES)
    return Report(len(before), failed, expiring)


def run_if_due(settings: Settings) -> None:
    """In the background: a slow MCP server mustn't hold anything else up."""
    if not settings.health_checks:
        return
    with session_scope() as session:
        if not _state(session):  # the first start: the first check is a day from now
            _save(session, {"at": datetime.now(UTC).isoformat()})
            return
        if not due(session):
            return
    if not _running.acquire(blocking=False):
        return

    def work() -> None:
        try:
            check_all(settings)
        except Exception:
            logger.exception("the daily connection check failed")
        finally:
            _running.release()

    threading.Thread(target=work, daemon=True, name="rk-health").start()
