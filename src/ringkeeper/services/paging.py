"""Long lists, a page at a time: newest first, and each page starts after a row, not at a
number, so a row added meanwhile doesn't push another into the next page twice.

A cursor names the last row of a page and when it was. The comparison is against that row's own
stored time, read back in the query: SQLite keeps a time written by the database and one written
by Ringkeeper in different text forms, and only a stored value sorts exactly as the list does.
The time in the cursor is for a row deleted since.

A list keeps answering with a plain array. How to go on is in the headers.
"""

import base64
import binascii
import json
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast

from fastapi import Response
from sqlalchemy import Select, and_, func, or_, select
from sqlalchemy.orm import InstrumentedAttribute, Session

from ringkeeper.errors import RuleViolationError

NEXT_HEADER = "X-Next-Cursor"
TOTAL_HEADER = "X-Total-Count"
PAGE_SIZE = 50
MOST_PER_PAGE = 500


@dataclass(frozen=True)
class Page[T]:
    items: Sequence[T]
    next: str | None
    total: int

    def tell(self, response: Response) -> None:
        response.headers[TOTAL_HEADER] = str(self.total)
        if self.next:
            response.headers[NEXT_HEADER] = self.next


def _encode(ident: Any, at: datetime) -> str:
    text = json.dumps([ident if isinstance(ident, int) else str(ident), at.isoformat()])
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")


def _decode(cursor: str) -> tuple[int | uuid.UUID, datetime]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        ident, at = json.loads(base64.urlsafe_b64decode(padded.encode()))
        key = ident if isinstance(ident, int) else uuid.UUID(str(ident))
        return key, datetime.fromisoformat(str(at))
    except (binascii.Error, ValueError, TypeError) as error:
        raise RuleViolationError("That page of the list isn't there. Start again.") from error


def _after(
    query: Select[Any],
    stamp: InstrumentedAttribute[datetime],
    key: InstrumentedAttribute[Any],
    cursor: str,
) -> Select[Any]:
    value, at = _decode(cursor)
    stored = select(stamp).where(key == value).scalar_subquery()
    when = func.coalesce(stored, at)
    return query.where(or_(stamp < when, and_(stamp == when, key < value)))


def fetch[T](
    session: Session,
    query: Select[T],
    stamp: InstrumentedAttribute[datetime],
    key: InstrumentedAttribute[Any],
    *,
    limit: int = PAGE_SIZE,
    cursor: str | None = None,
    load: tuple[Any, ...] = (),
) -> Page[T]:
    """One page of `query` (filtered, not yet ordered), newest first, and how many there are in
    all, whichever page this is. `load`: options for the rows, like selectinload."""
    size = max(limit, 1)
    counted = query.with_only_columns(func.count(), maintain_column_froms=True).order_by(None)
    total = int(session.scalar(counted) or 0)
    ordered = query.order_by(None).order_by(stamp.desc(), key.desc())
    if cursor:
        ordered = _after(ordered, stamp, key, cursor)
    rows = list(session.scalars(ordered.options(*load).limit(size + 1)).all())
    shown = rows[:size]
    last = cast(Any, shown[-1]) if shown else None
    more = len(rows) > size and last is not None
    return Page(
        shown,
        _encode(getattr(last, key.key), getattr(last, stamp.key)) if more else None,
        total,
    )
