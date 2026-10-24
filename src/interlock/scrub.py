"""Remove secret values from anything that leaves the process.

Every resolved secret registers its fragments (the value itself plus pieces an
adapter knows can leak separately, like the password inside a DSN). Tool
results, errors, audit rows and log records all pass through `scrub`.
"""

import logging
import threading
from collections.abc import Callable
from typing import Any, cast

REDACTED = "[redacted]"
MIN_FRAGMENT_LENGTH = 4  # shorter fragments would mangle ordinary text

_lock = threading.Lock()
_fragments: set[str] = set()


def register(*fragments: str) -> None:
    with _lock:
        _fragments.update(f for f in fragments if len(f) >= MIN_FRAGMENT_LENGTH)


def _snapshot() -> list[str]:
    with _lock:
        # Longest first, so a fragment inside a longer one doesn't leave a tail.
        return sorted(_fragments, key=len, reverse=True)


def scrub(text: str) -> str:
    for fragment in _snapshot():
        if fragment in text:
            text = text.replace(fragment, REDACTED)
    return text


def scrub_obj(value: object) -> Any:
    """Scrub every string inside a JSON-like structure (keys included)."""
    if isinstance(value, str):
        return scrub(value)
    if isinstance(value, dict):
        items = cast(dict[object, object], value).items()
        return {scrub_obj(k): scrub_obj(v) for k, v in items}
    if isinstance(value, list | tuple):
        return [scrub_obj(v) for v in cast(list[object], value)]
    return value


class _ScrubbingRecordFactory:
    def __init__(self, inner: Callable[..., logging.LogRecord]) -> None:
        self.inner = inner

    def __call__(self, *args: Any, **kwargs: Any) -> logging.LogRecord:
        record = self.inner(*args, **kwargs)
        record.msg = scrub(record.getMessage())
        record.args = None
        if record.exc_info and not record.exc_text:
            record.exc_text = scrub(logging.Formatter().formatException(record.exc_info))
        if record.stack_info:
            record.stack_info = scrub(record.stack_info)
        return record


def install_log_scrubbing() -> None:
    """Scrub every log record created from now on, whatever logger or handler."""
    current = logging.getLogRecordFactory()
    if not isinstance(current, _ScrubbingRecordFactory):
        logging.setLogRecordFactory(_ScrubbingRecordFactory(current))
