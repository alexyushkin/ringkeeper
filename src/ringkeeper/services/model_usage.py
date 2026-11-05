"""Tokens each session used, as the model proxy saw them: the same count for any engine or
place, so a cap doesn't depend on what an engine reports about itself."""

import uuid
from collections.abc import Iterable, Mapping

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ringkeeper.models import ModelUsage

MAX_PLAIN_ANSWER = 2_000_000  # a plain JSON answer read for its usage; a stream isn't kept


def record(
    session: Session,
    session_id: uuid.UUID,
    project: str,
    provider: str,
    model: str,
    tokens: Mapping[str, int],
) -> None:
    session.add(
        ModelUsage(
            session_id=session_id, project=project, provider=provider, model=model,
            input_tokens=tokens["input"], output_tokens=tokens["output"],
            cache_read_tokens=tokens["cache_read"], cache_write_tokens=tokens["cache_write"],
        )
    )  # fmt: skip


def totals(session: Session, session_ids: Iterable[uuid.UUID]) -> dict[str, int]:
    """What these sessions used altogether."""
    ids = list(session_ids)
    found = session.execute(
        select(
            func.coalesce(func.sum(ModelUsage.input_tokens), 0),
            func.coalesce(func.sum(ModelUsage.output_tokens), 0),
            func.coalesce(func.sum(ModelUsage.cache_read_tokens), 0),
            func.coalesce(func.sum(ModelUsage.cache_write_tokens), 0),
            func.count(ModelUsage.id),
        ).where(ModelUsage.session_id.in_(ids))
    ).one()
    return {
        "input": int(found[0]), "output": int(found[1]), "cache_read": int(found[2]),
        "cache_write": int(found[3]), "requests": int(found[4]),
    }  # fmt: skip
