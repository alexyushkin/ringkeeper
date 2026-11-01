"""Routine schedules: the picker's choices, in words, the next run in the owner's time
zone (across daylight saving), and catching up once after the computer slept."""

from datetime import UTC, datetime

import pytest

from ringkeeper.errors import RuleViolationError
from ringkeeper.schedules import catch_up, describe, next_after, parse

NY = "America/New_York"


def utc(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=UTC)


def test_in_words() -> None:
    assert describe(parse({"every": "weekdays", "time": "08:00", "tz": NY})) == (
        "Weekdays at 8:00 AM (America/New_York)"
    )
    assert describe(
        parse({"every": "week", "days": ["thu", "mon"], "time": "17:30", "tz": NY})
    ) == ("Every Mon, Thu at 5:30 PM (America/New_York)")
    assert describe(parse({"every": "month", "day": 31, "time": "09:00", "tz": NY})).startswith(
        "Monthly on day 31 (or the month's last day)"
    )
    assert (
        describe(parse({"every": "hours", "hours": 4, "tz": NY}))
        == "Every 4 hours (America/New_York)"
    )


@pytest.mark.parametrize(
    "raw",
    [
        {"every": "fortnight", "tz": NY},
        {"every": "day", "time": "8am", "tz": NY},
        {"every": "day", "time": "08:00", "tz": "Mars/Base"},
        {"every": "week", "days": [], "time": "08:00", "tz": NY},
        {"every": "hours", "hours": 5, "tz": NY},
    ],
)
def test_bad_choices_say_what_s_wrong(raw: dict[str, object]) -> None:
    with pytest.raises(RuleViolationError):
        parse(raw)


def test_next_run_is_in_the_owner_s_time_zone_across_daylight_saving() -> None:
    daily = parse({"every": "day", "time": "08:00", "tz": NY})
    # 8:00 in New York is 12:00 UTC in summer and 13:00 UTC in winter.
    assert next_after(daily, utc("2026-10-31T12:30:00")) == utc("2026-11-01T13:00:00")
    assert next_after(daily, utc("2026-07-01T11:00:00")) == utc("2026-07-01T12:00:00")


def test_weekdays_skip_the_weekend_and_months_use_their_last_day() -> None:
    weekdays = parse({"every": "weekdays", "time": "08:00", "tz": NY})
    friday_after = utc("2026-10-30T14:00:00")  # Friday, after 8:00 New York time
    assert next_after(weekdays, friday_after) == utc("2026-11-02T13:00:00")  # Monday
    monthly = parse({"every": "month", "day": 31, "time": "09:00", "tz": NY})
    assert next_after(monthly, utc("2026-11-01T00:00:00")) == utc("2026-11-30T14:00:00")


def test_after_sleep_one_run_makes_up_for_the_missed_ones() -> None:
    hourly = parse({"every": "hours", "hours": 1, "tz": "UTC"})
    due = utc("2026-11-01T08:00:00")
    woke = utc("2026-11-01T11:20:00")  # asleep through 8, 9, 10 and 11
    caught = catch_up(hourly, due, woke)
    assert (caught.run, caught.missed, caught.next_run) == (True, 3, utc("2026-11-01T12:00:00"))
    on_time = catch_up(hourly, utc("2026-11-01T12:00:00"), utc("2026-11-01T12:00:05"))
    assert (on_time.run, on_time.missed) == (True, 0)
    early = catch_up(hourly, utc("2026-11-01T13:00:00"), utc("2026-11-01T12:00:05"))
    assert (early.run, early.next_run) == (False, utc("2026-11-01T13:00:00"))
