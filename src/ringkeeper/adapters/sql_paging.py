"""A query's rows in parts: how every SQL connection says what part this is."""

from typing import Annotated, Any

from pydantic import Field

from ringkeeper.adapters.base import JSON

RowOffset = Annotated[
    int, Field(ge=0, description="The row to start from. Use next_offset from the last part.")
]
PARTIAL_NOTE = (
    "This is only part of the result. Run the same statement with query and "
    "offset=next_offset for the rest before answering, or say your answer is based on part of "
    "it."
)
WRITE_NOTE = "Only these rows came back, and the statement has run: don't run it again to see more."


def rows(
    columns: list[str], found: list[list[Any]], most: int, offset: int, total: int | None
) -> JSON:
    """`found` holds up to one row more than asked for, which is how "more" is known when the
    database doesn't say how many there are in all."""
    chosen = found[:most]
    end = offset + len(chosen)
    more = len(found) > most or (total is not None and end < total)
    answer: JSON = {
        "columns": columns,
        "rows": chosen,
        "row_count": len(chosen),
        "total_rows": total,
        "offset": offset,
        "truncated": more or offset > 0,
        "next_offset": end if more else None,
    }
    if more:
        answer["note"] = PARTIAL_NOTE
    return answer


def after_write(result: JSON) -> JSON:
    """A write can't be run again for its next rows, so the answer doesn't say to."""
    if result.get("next_offset") is None:
        return result
    return result | {"next_offset": None, "note": WRITE_NOTE}
