"""A result too long for one answer, kept for a while so it can be read in parts.

Some results can't simply be asked for again: an HTTP call or an MCP tool may have done
something. So the gateway keeps what was already answered (after scrubbing, so nothing secret
is kept) and the agent reads on with `read_more`. It lives in this process's memory for
TTL_SECONDS, only for the session that asked, and never reaches disk. A restart ends it, and
the answer then says to read again.

A result with a list of rows (records, rows, workflows, messages) is kept as rows too: read_more
then gives only the fields asked for, the rows a filter matches, and a page at a time
(services.rows), so nothing needs a shell to slice it.
"""

import json
import secrets
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

from ringkeeper.adapters.base import JSON, ToolError
from ringkeeper.adapters.paging import PAGE_CHARS, page
from ringkeeper.calls import Caller
from ringkeeper.services import rows as row_lists

# A page read natively (adapters.paging), with its fields around it, still fits in one answer.
FITS_CHARS = 300_000
# A list of rows past this is saved and read a page at a time: well under what Claude Code
# shows of one answer (workers.env MAX_MCP_OUTPUT_TOKENS) before it saves it to a file.
ROWS_FIT_CHARS = 60_000
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
    key: str | None = None  # its rows: what the agent called them ("records")
    rows: list[JSON] = field(default_factory=list[JSON])


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


def _keep(kept: Kept) -> str:
    ident = secrets.token_urlsafe(12)
    now = time.monotonic()
    with _lock:
        for name in [n for n, item in _kept.items() if item.until < now]:
            del _kept[name]
        _kept[ident] = kept
        while len(_kept) > MOST_RESULTS:
            _kept.popitem(last=False)
    return ident


def _kept_for(
    owner: str, text: str, key: str | None = None, rows: list[JSON] | None = None
) -> Kept:
    until = time.monotonic() + TTL_SECONDS
    return Kept(owner, text[:MOST_KEPT_CHARS], len(text), until, key, rows or [])


def fit(
    caller: Caller, result: JSON, rows: str | None = None, fields: list[str] | None = None
) -> JSON:
    """The result as it is, or its first part and how to read the rest. Takes a scrubbed one.
    `rows` names a list tool's rows: they come with only `fields` (when given) and long text
    cut, and past ROWS_FIT_CHARS are saved and read in pages."""
    owner = _owner(caller)
    key = rows if isinstance(result.get(rows or ""), list) else None
    text = json.dumps(result, default=str, ensure_ascii=False)
    if key is None and len(text) > FITS_CHARS:
        key = row_lists.key_of(result)  # any tool's long list is better read as rows
    if key is None or owner is None:
        if len(text) <= FITS_CHARS or owner is None:
            return result
        kept = _kept_for(owner, text)
        return _answer(_keep(kept), kept, 0, PAGE_CHARS)
    shown = row_lists.project(result, key, fields) if fields else result
    shown, was_cut = row_lists.cut(shown, key)
    size = len(json.dumps(shown, default=str, ensure_ascii=False))
    if not was_cut and size <= ROWS_FIT_CHARS:
        return shown
    every = row_lists.flat(result, key)
    kept = _kept_for(owner, text, key, every)
    ident = _keep(kept)
    if size <= ROWS_FIT_CHARS:
        return shown | {"continuation": ident, "note": row_lists.CUT_NOTE}
    return _saved(ident, kept, result, fields)


def _saved(ident: str, kept: Kept, result: JSON, fields: list[str] | None) -> JSON:
    """A list too long for one answer: what else came with it, its first page, and exactly
    how to go on."""
    key = kept.key or "rows"
    around = {k: v for k, v in result.items() if k not in (key, "columns")}
    first = _rows_page(ident, kept, fields, None, 1)
    asked = json.dumps(fields, ensure_ascii=False) if fields else row_lists.fields_note(kept.rows)
    note = (
        f"{len(kept.rows):,} {key} saved. Use read_more with continuation and fields={asked} "
        f"(only those you need) to go through them, {row_lists.PAGE_ROWS} a page: page=2 next. "
        'where={"field": ..., "contains": ...} (or "equals") narrows them, and its total_rows '
        "counts the ones that match."
    )
    return around | first | {"saved_rows": len(kept.rows), "note": note}


def _rows_page(
    ident: str, kept: Kept, fields: list[str] | None, where: dict[str, Any] | None, number: int
) -> JSON:
    key = kept.key or "rows"
    row_lists.check_fields(kept.rows, fields or [])
    found = row_lists.matching(kept.rows, where) if where else kept.rows
    if fields:
        found = row_lists.project({key: found}, key, fields)[key]
    pages = max(1, -(-len(found) // row_lists.PAGE_ROWS))
    if number > pages:
        raise ToolError(f"there are only {pages} pages of {len(found):,} {key}.")
    start = (number - 1) * row_lists.PAGE_ROWS
    shown = found[start : start + row_lists.PAGE_ROWS]
    whole = where is not None and len(found) <= row_lists.FULL_ROWS
    shown, was_cut = (shown, False) if whole else row_lists.cut_rows(shown)
    following = number + 1 if number < pages else None
    answer: JSON = {
        key: shown,
        "total_rows": len(found),
        "page": number,
        "pages": pages,
        "next_page": following,
        "continuation": ident,
    }
    notes = [row_lists.CUT_NOTE] if was_cut else []
    if following is not None:
        notes.insert(0, READ_ON_PAGE.format(following=following))
    return answer | ({"note": " ".join(notes)} if notes else {})


READ_ON_PAGE = (
    "This is one page. Call read_more with this continuation, the same fields and where, and "
    "page={following} for the next, before answering."
)


def read_more(
    caller: Caller,
    ident: str,
    offset: int = 0,
    limit: int = PAGE_CHARS,
    *,
    fields: list[str] | None = None,
    where: dict[str, Any] | None = None,
    page: int | None = None,
) -> JSON:
    """Another part of a kept result. Someone else's reads as gone: it isn't theirs to know of.
    A kept list is read as rows (fields, where, page), unless an offset into its text is asked."""
    with _lock:
        kept = _kept.get(ident)
    if kept is None or kept.owner != _owner(caller) or kept.until < time.monotonic():
        raise ToolError(
            "that result is no longer kept. Run the read again; don't repeat anything that "
            "changed something."
        )
    by_text = offset > 0 and not (fields or where or page)
    if kept.key is None or by_text:
        if fields or where or page:
            raise ToolError(
                "this result isn't a list of rows: read it with offset and limit instead."
            )
        return _answer(ident, kept, offset, limit)
    return _rows_page(ident, kept, fields, where, page or 1)


def kept_count() -> int:
    with _lock:
        return len(_kept)


def is_partial(result: Any) -> bool:
    return isinstance(result, dict) and "continuation" in result
