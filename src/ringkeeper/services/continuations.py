"""A result too long for one answer, kept for a while so it can be read in parts.

Some results can't simply be asked for again: an HTTP call or an MCP tool may have done
something. So the gateway keeps what was already answered (after scrubbing, so nothing secret
is kept) and the agent reads on with `read_more`. It lives in this process's memory for
TTL_SECONDS, only for the session that asked, and never reaches disk. A restart ends it, and
the answer then says to read again.
"""

import json
import secrets
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

from ringkeeper.adapters.base import JSON, ToolError
from ringkeeper.adapters.paging import PAGE_CHARS, page
from ringkeeper.calls import Caller

# A page read natively (adapters.paging), with its fields around it, still fits in one answer.
FITS_CHARS = 300_000
MOST_KEPT_CHARS = 10_000_000  # of one result
MOST_RESULTS = 50  # across every session; the oldest goes first
TTL_SECONDS = 30 * 60
READ_ON = (
    "This is part of a longer result, as JSON text. Call read_more with this continuation and "
    "next_offset for the rest before answering, or say your answer is based on part of it."
)


@dataclass
class Kept:
    owner: str
    text: str
    total: int
    until: float


_lock = threading.Lock()
_kept: OrderedDict[str, Kept] = OrderedDict()


def _owner(caller: Caller) -> str | None:
    return caller.session_id or caller.token_id


def clear() -> None:
    """Forget everything kept. For tests only."""
    with _lock:
        _kept.clear()


def _answer(ident: str, kept: Kept, offset: int, limit: int) -> JSON:
    part = page(kept.text, offset, limit)
    part["total_chars"] = kept.total
    part.pop("note", None)
    answer: JSON = part | {"continuation": ident}
    if part["next_offset"] is not None:
        return answer | {"note": READ_ON}
    if kept.total > len(kept.text):
        kept_chars = len(kept.text)
        return answer | {
            "truncated": True,
            "note": f"Only the first {kept_chars:,} characters of this were kept. Ask for less "
            "at a time to see the rest.",
        }
    return answer


def fit(caller: Caller, result: JSON) -> JSON:
    """The result as it is, or its first part and how to read the rest. Takes a scrubbed one."""
    text = json.dumps(result, default=str, ensure_ascii=False)
    owner = _owner(caller)
    if len(text) <= FITS_CHARS or owner is None:
        return result
    ident = secrets.token_urlsafe(12)
    kept = Kept(owner, text[:MOST_KEPT_CHARS], len(text), time.monotonic() + TTL_SECONDS)
    now = time.monotonic()
    with _lock:
        for name in [n for n, item in _kept.items() if item.until < now]:
            del _kept[name]
        _kept[ident] = kept
        while len(_kept) > MOST_RESULTS:
            _kept.popitem(last=False)
    return _answer(ident, kept, 0, PAGE_CHARS)


def read_more(caller: Caller, ident: str, offset: int, limit: int) -> JSON:
    """Another part of a kept result. Someone else's reads as gone: it isn't theirs to know of."""
    with _lock:
        kept = _kept.get(ident)
    if kept is None or kept.owner != _owner(caller) or kept.until < time.monotonic():
        raise ToolError(
            "that result is no longer kept. Run the read again; don't repeat anything that "
            "changed something."
        )
    return _answer(ident, kept, offset, limit)


def kept_count() -> int:
    with _lock:
        return len(_kept)


def is_partial(result: Any) -> bool:
    return isinstance(result, dict) and "continuation" in result
