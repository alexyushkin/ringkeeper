"""Revoked worker tokens. The gateway refuses any token whose jti is listed."""

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from ringkeeper.models import TokenRevocation


def revoke(
    session: Session,
    jti: str,
    *,
    reason: str,
    by: str,
    expires_at: datetime | None = None,
) -> bool:
    """Revoke a token. Returns False if it was already revoked (the first reason stays)."""
    statement = (
        insert(TokenRevocation)
        .values(jti=jti, reason=reason, revoked_by=by, expires_at=expires_at)
        .on_conflict_do_nothing(index_elements=[TokenRevocation.jti])
        .returning(TokenRevocation.jti)
    )
    return session.scalars(statement).first() is not None


def is_revoked(session: Session, jti: str) -> bool:
    return session.get(TokenRevocation, jti) is not None


def get_revocation(session: Session, jti: str) -> TokenRevocation | None:
    return session.scalars(select(TokenRevocation).where(TokenRevocation.jti == jti)).first()
