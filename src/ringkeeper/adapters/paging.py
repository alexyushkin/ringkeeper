"""Reading something long in parts.

Every answer that holds part of something says how much there is, which part this is and
where the next part starts, so an agent can't take a part for the whole. A tool that can read
the same thing again (a message, a file) takes `offset` and `limit`. Anything else long is
kept for a while by the gateway and read with `read_more` (services.continuations).
"""

from typing import Annotated

from pydantic import Field

from ringkeeper.adapters.base import JSON

PAGE_CHARS = 200_000  # a generous page: most messages and files arrive whole
PARTIAL_NOTE = (
    "This is only part of it. Read from next_offset for the rest before answering, or say "
    "your answer is based on part of it."
)

Offset = Annotated[
    int,
    Field(ge=0, description="Where to start, in characters. Use next_offset from the last part."),
]
PageLimit = Annotated[
    int, Field(ge=1, le=PAGE_CHARS, description="The most characters to return in this part.")
]


def page(text: str, offset: int = 0, limit: int = PAGE_CHARS, field: str = "text") -> JSON:
    """One part of `text`, saying what part it is. `truncated` is true while more follows."""
    part = text[offset : offset + limit]
    end = offset + len(part)
    more = end < len(text)
    answer: JSON = {
        field: part,
        "total_chars": len(text),
        "offset": offset,
        "returned_chars": len(part),
        "truncated": more or offset > 0,
        "next_offset": end if more else None,
    }
    if more:
        answer["note"] = PARTIAL_NOTE
    return answer
