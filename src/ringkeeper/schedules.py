"""When a routine runs: the friendly picker's choices, in the owner's time zone.

A schedule is stored as what the picker shows, not as a cron string:

    {"every": "day", "time": "08:00", "tz": "America/New_York"}
    {"every": "weekdays", "time": "08:00", "tz": ...}
    {"every": "week", "days": ["mon", "thu"], "time": "17:30", "tz": ...}
    {"every": "month", "day": 1, "time": "09:00", "tz": ...}  # day 29-31: the month's last
    {"every": "hours", "hours": 4, "tz": ...}  # on the hour, from midnight: 0:00, 4:00...

Times are wall-clock times in `tz`, so 8:00 stays 8:00 across daylight saving.
`next_after` gives the next run (UTC) after a moment; `catch_up` tells, on wake,
whether runs were missed while the computer slept: one run makes up for all of
them, and says how many.
"""

import calendar
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Literal, cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ringkeeper.errors import RuleViolationError

Every = Literal["day", "weekdays", "week", "month", "hours"]
DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
DAY_NAMES: dict[str, str] = dict(
    zip(DAYS, ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"), strict=True)
)
EVERY: tuple[Every, ...] = ("day", "weekdays", "week", "month", "hours")
LOOK_AHEAD_DAYS = 400  # more than a year: every schedule fires within it


@dataclass(frozen=True)
class Schedule:
    every: Every
    tz: str
    at: time = time(9, 0)
    days: tuple[str, ...] = ()  # "week": which days
    day: int = 1  # "month": which day (29-31: the month's last, when shorter)
    hours: int = 1  # "hours": how often

    @property
    def zone(self) -> ZoneInfo:
        return ZoneInfo(self.tz)

    def as_json(self) -> dict[str, Any]:
        data: dict[str, Any] = {"every": self.every, "tz": self.tz}
        if self.every == "hours":
            data["hours"] = self.hours
        else:
            data["time"] = self.at.strftime("%H:%M")
        if self.every == "week":
            data["days"] = list(self.days)
        if self.every == "month":
            data["day"] = self.day
        return data


def parse(raw: dict[str, Any]) -> Schedule:
    """The picker's choices, checked. Raises RuleViolationError saying what's wrong."""
    every = raw.get("every")
    if every not in EVERY:
        raise RuleViolationError(f"A schedule is every {', '.join(EVERY)}; not {every!r}.")
    tz = str(raw.get("tz") or "")
    try:
        ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError):
        raise RuleViolationError(f"'{tz}' isn't a time zone (like America/New_York).") from None
    at = time(9, 0)
    if every != "hours":
        try:
            hour, minute = (int(p) for p in str(raw.get("time", "")).split(":"))
            at = time(hour, minute)
        except ValueError:
            raise RuleViolationError("Give the time as HH:MM, like 08:00.") from None
    days: tuple[str, ...] = ()
    if every == "week":
        chosen = cast(list[Any], raw.get("days") or [])
        days = tuple(d for d in DAYS if d in {str(c).lower() for c in chosen})
        if not days or len(days) != len(set(chosen)):
            raise RuleViolationError(f"Choose the days of the week ({', '.join(DAYS)}).")
    day = int(raw.get("day") or 1)
    if every == "month" and not 1 <= day <= 31:
        raise RuleViolationError("The day of the month is 1 to 31.")
    hours = int(raw.get("hours") or 1)
    if every == "hours" and hours not in (1, 2, 3, 4, 6, 8, 12):
        raise RuleViolationError("Every 1, 2, 3, 4, 6, 8 or 12 hours.")
    return Schedule(every, tz, at, days, day, hours)


def describe(schedule: Schedule) -> str:
    """In words: "Weekdays at 8:00 AM (America/New_York)"."""
    clock = schedule.at.strftime("%I:%M %p").lstrip("0")
    where = f" ({schedule.tz})"
    match schedule.every:
        case "day":
            return f"Every day at {clock}{where}"
        case "weekdays":
            return f"Weekdays at {clock}{where}"
        case "week":
            return f"Every {', '.join(DAY_NAMES[d] for d in schedule.days)} at {clock}{where}"
        case "month":
            last = " (or the month's last day)" if schedule.day > 28 else ""
            return f"Monthly on day {schedule.day}{last} at {clock}{where}"
        case "hours":
            return (
                "Every hour" if schedule.hours == 1 else f"Every {schedule.hours} hours"
            ) + where


def _runs_on(schedule: Schedule, day: date) -> bool:
    weekday = DAYS[day.weekday()]
    match schedule.every:
        case "day" | "hours":
            return True
        case "weekdays":
            return day.weekday() < 5
        case "week":
            return weekday in schedule.days
        case "month":
            last = calendar.monthrange(day.year, day.month)[1]
            return day.day == min(schedule.day, last)


def _times(schedule: Schedule, day: date) -> list[datetime]:
    """That day's runs, as wall-clock times in the schedule's zone."""
    if not _runs_on(schedule, day):
        return []
    clocks = (
        [time(h, 0) for h in range(0, 24, schedule.hours)]
        if schedule.every == "hours"
        else [schedule.at]
    )
    return [datetime.combine(day, c, tzinfo=schedule.zone) for c in clocks]


def next_after(schedule: Schedule, moment: datetime) -> datetime:
    """The first run strictly after `moment`, in UTC."""
    local = moment.astimezone(schedule.zone)
    for offset in range(LOOK_AHEAD_DAYS):
        for run in _times(schedule, local.date() + timedelta(days=offset)):
            # A wall-clock time skipped by a daylight-saving jump runs at the same
            # instant UTC normalization gives it (an hour later), never not at all.
            when = run.astimezone(UTC)
            if when > moment.astimezone(UTC):
                return when
    raise RuleViolationError("This schedule never runs.")  # unreachable for valid ones


@dataclass(frozen=True)
class CatchUp:
    run: bool  # run once now
    missed: int  # how many runs it stands for (0: on time)
    next_run: datetime  # the next one after now


def catch_up(schedule: Schedule, due: datetime, now: datetime) -> CatchUp:
    """At `now`, for a routine whose next run was `due`: run (once) if it's due, how many
    runs were missed while the computer slept, and when the next one is."""
    if due > now:
        return CatchUp(False, 0, due)
    missed, moment = 0, due
    while moment <= now and missed < 10_000:
        missed += 1
        moment = next_after(schedule, moment)
    return CatchUp(True, missed - 1, moment)
