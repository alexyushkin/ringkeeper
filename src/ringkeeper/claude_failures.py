"""Failures on Claude's side, in plain words, and what Ringkeeper does about them.

Claude Code (a task's worker) and the Agent SDK (the router) report these through the error
type on an assistant message (`authentication_failed`, `billing_error`, `rate_limit`,
`server_error`), the HTTP status of the failing call on the result (429, 500, 529), and their
own error text ("Claude AI usage limit reached|<reset>", "Invalid API key · Please run /login",
"API Error: 529 ..."). `classify` reads the structured signals first, then only Claude Code's
own phrases, each at the start of a line of the SDK's result or errors: never loose words in
the model's own text or the worker's errors (a task that read about an API's "rate limit
reached" isn't Claude's limit). It turns them into one of five kinds:

- usage limit (a subscription's limit, or no credit left): nothing new starts until it
  resets (`pause_until`), and Home says so; tasks wait instead of failing.
- login: missing or expired. Not retried: it says where to sign in again.
- rate limit, overloaded, network: transient. Tried again a few times, with a wait between.
- slow: the router's model call ran past its time limit (`timed_out`). Transient too: tried
  again once at once, then later, like the others.
"""

import enum
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from ringkeeper.models import Setting

PAUSE_KEY = "claude_pause"
RETRY_WAITS = (5.0, 20.0, 60.0)  # seconds between tries of a transient failure
UNKNOWN_RESET = timedelta(hours=1)  # a usage limit without a reset time: look again then


class Kind(enum.StrEnum):
    USAGE_LIMIT = "usage_limit"
    LOGIN = "login"
    RATE_LIMIT = "rate_limit"
    OVERLOADED = "overloaded"
    NETWORK = "network"
    SLOW = "slow"


TRANSIENT = frozenset({Kind.RATE_LIMIT, Kind.OVERLOADED, Kind.NETWORK, Kind.SLOW})


@dataclass(frozen=True)
class Failure:
    kind: Kind
    message: str  # what happened and what to do, in plain words
    resets_at: datetime | None = None  # a usage limit's reset, when Claude said

    @property
    def transient(self) -> bool:
        return self.kind in TRANSIENT


# Claude Code's own messages, each at the start of a line (re.M): what it says, not what a
# task says about something else.
_USAGE = re.compile(
    r"^(?:Claude AI usage limit reached|You've hit your (?:\w+ )?limit|"
    r"(?:5-hour|weekly|session|Opus weekly) limit reached|You're out of extra usage|"
    r"Credit balance is too low)",
    re.M,
)
_LOGIN = re.compile(
    r"^(?:Invalid API key|OAuth token (?:has )?expired|Please run /login|Not logged in|"
    r"API Error: 40[13]\b)",
    re.M,
)
_RATE = re.compile(r"^API Error: 429\b", re.M)
_OVERLOADED = re.compile(r"^API Error: (?:5\d\d\b|Overloaded)", re.M)
_NETWORK = re.compile(
    r"^API Error:? \(?(?:Connection error|Request timed out|Unable to connect to API)",
    re.M,
)
_EPOCH = re.compile(r"\|(\d{10})\b")
_CLOCK = re.compile(r"resets?\s+(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*([ap])\.?m\.?", re.I)


def _reset(text: str, now: datetime) -> datetime | None:
    """When a usage limit resets, from Claude's own words: an epoch after "|" ("Claude AI
    usage limit reached|1731272400") or a clock time ("resets 3pm"), local time."""
    if found := _EPOCH.search(text):
        return datetime.fromtimestamp(int(found.group(1)), UTC)
    if found := _CLOCK.search(text):
        hour = int(found.group(1)) % 12 + (12 if found.group(3).lower() == "p" else 0)
        local = now.astimezone()
        at = local.replace(hour=hour, minute=int(found.group(2) or 0), second=0, microsecond=0)
        if at <= local:
            at += timedelta(days=1)
        return at.astimezone(UTC)
    return None


def said_time(at: datetime) -> str:
    """3:00 pm, or "3:00 pm tomorrow"."""
    local = at.astimezone()
    clock = local.strftime("%I:%M %p").lstrip("0").lower()
    return clock if local.date() == datetime.now().astimezone().date() else f"{clock} tomorrow"


def classify(
    text: str | None = None,
    *,
    status: int | None = None,
    error: str | None = None,
    now: datetime | None = None,
) -> Failure | None:
    """A failure on Claude's side, or None for anything else. `text`: what Claude Code or the
    SDK said (the result's text and errors), one message per line; never the model's own
    words or the worker's errors."""
    said = "\n".join(line.strip() for line in (text or "").splitlines())
    if error == "billing_error" or _USAGE.search(said):
        resets = _reset(said, now or datetime.now(UTC))
        when = f"; it resets at {said_time(resets)}" if resets else ""
        return Failure(
            Kind.USAGE_LIMIT,
            f"Your Claude plan's limit is reached{when}. Tasks will wait until then.",
            resets,
        )
    if error == "authentication_failed" or status in (401, 403) or _LOGIN.search(said):
        return Failure(
            Kind.LOGIN,
            "Ringkeeper's sign-in to Claude is missing or expired. Sign in again in "
            "Settings › Claude, then try again.",  # noqa: RUF001 (the app's own path)
        )
    if error == "rate_limit" or status == 429 or _RATE.search(said):
        return Failure(
            Kind.RATE_LIMIT,
            "Claude is limiting how fast requests can go right now. Ringkeeper tries again "
            "in a moment.",
        )
    if (
        error == "server_error"
        or (status is not None and status >= 500)
        or _OVERLOADED.search(said)
    ):
        return Failure(
            Kind.OVERLOADED, "Claude is overloaded right now. Ringkeeper tries again in a moment."
        )
    if _NETWORK.search(said):
        return Failure(
            Kind.NETWORK,
            "Ringkeeper couldn't reach Claude: check this computer's internet connection. It "
            "tries again in a moment.",
        )
    return None


def timed_out(seconds: float) -> Failure:
    """The router's model call ran past its time limit."""
    return Failure(
        Kind.SLOW,
        f"Claude took more than {seconds:.0f} seconds to work out where this goes. Ringkeeper "
        "tries again in a moment.",
    )


# --- A usage limit: nothing new starts until it resets ---


@dataclass(frozen=True)
class Pause:
    until: datetime
    message: str


def pause_until(session: Session, failure: Failure) -> Pause:
    """Hold new tasks (and routing) until the limit resets."""
    until = failure.resets_at or datetime.now(UTC) + UNKNOWN_RESET
    value = {"until": until.isoformat(), "message": failure.message}
    row = session.get(Setting, PAUSE_KEY)
    if row is None:
        session.add(Setting(key=PAUSE_KEY, value=value))
    else:
        row.value = value
    session.flush()
    return Pause(until, failure.message)


def clear(session: Session) -> bool:
    """Stop waiting for the limit to reset: "Try now", or a new sign-in, key or place for
    Claude (it may not have the limit). Returns whether there was a pause."""
    row = session.get(Setting, PAUSE_KEY)
    if row is None or not row.value.get("until"):
        return False
    row.value = {}
    session.flush()
    return True


def paused(session: Session, now: datetime | None = None) -> Pause | None:
    """The pause for a usage limit, while it lasts."""
    row = session.get(Setting, PAUSE_KEY)
    if row is None or not row.value.get("until"):
        return None
    until = datetime.fromisoformat(str(row.value["until"]))
    if until <= (now or datetime.now(UTC)):
        return None
    return Pause(until, str(row.value.get("message") or ""))
