"""Google Ads' daily operations, as counted here.

Google gives each Cloud project one allowance, whichever client a call is for: so it's counted
per Google app, in the last 24 hours, by the hour. This is what this install sent. Another
install on the same project, or a script, isn't in the figure, so Google's own count may be higher,
and a call that never reached Google is in it, so it may be a little lower too.
"""

import time
from typing import Any, cast

from sqlalchemy.orm import Session

from ringkeeper.db import session_scope
from ringkeeper.errors import RuleViolationError
from ringkeeper.models import Setting

KEY = "google-ads-operations"
SECONDS_PER_HOUR = 3600
HOURS_KEPT = 24
# Google's limits for production accounts, by access level. Standard has none.
LEVELS: dict[str, tuple[str, int | None]] = {
    "explorer": ("Explorer", 2_880),
    "basic": ("Basic", 15_000),
    "standard": ("Standard", None),
}
DEFAULT_LEVEL = "explorer"


def _hour(now: float | None = None) -> int:
    return int((time.time() if now is None else now) // SECONDS_PER_HOUR)


def _recent(hours: dict[str, int], now: float | None) -> dict[str, int]:
    oldest = _hour(now) - HOURS_KEPT
    return {h: n for h, n in hours.items() if int(h) > oldest}


def _app(value: dict[str, Any], app: str) -> dict[str, Any]:
    return cast(dict[str, Any], value.get(app) or {})


def add(app: str, used: int, now: float | None = None) -> None:
    with session_scope() as session:
        row = session.get(Setting, KEY)
        value: dict[str, Any] = dict(row.value) if row else {}
        kept = _app(value, app)
        hours = _recent(cast(dict[str, int], kept.get("hours") or {}), now)
        hour = str(_hour(now))
        hours[hour] = hours.get(hour, 0) + used
        value[app] = {**kept, "hours": hours}
        if row:
            row.value = value
        else:
            session.add(Setting(key=KEY, value=value))


def used(session: Session, app: str, now: float | None = None) -> int:
    row = session.get(Setting, KEY)
    kept = _app(dict(row.value) if row else {}, app)
    return sum(_recent(cast(dict[str, int], kept.get("hours") or {}), now).values())


def level(session: Session, app: str) -> str:
    row = session.get(Setting, KEY)
    return str(_app(dict(row.value) if row else {}, app).get("level") or DEFAULT_LEVEL)


def set_level(session: Session, app: str, chosen: str) -> None:
    if chosen not in LEVELS:
        raise RuleViolationError(f"Google Ads has no access level called '{chosen}'.")
    row = session.get(Setting, KEY)
    value: dict[str, Any] = dict(row.value) if row else {}
    value[app] = {**_app(value, app), "level": chosen}
    if row:
        row.value = value
    else:
        session.add(Setting(key=KEY, value=value))


def shown(session: Session, app: str) -> dict[str, Any]:
    """What Settings shows for one Google app."""
    chosen = level(session, app)
    return {"level": chosen, "used": used(session, app), "limit": LEVELS[chosen][1]}
