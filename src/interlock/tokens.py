"""Worker tokens: signed, short-lived, naming a project and a session.

HS256 JWTs signed with a key derived from the master key. The gateway trusts
nothing else about a caller: no token, no access; the project comes only from
a token whose signature, audience, issuer and expiry all check out.
"""

import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt

from interlock.config import Settings, get_settings
from interlock.errors import InterlockError
from interlock.keys import derive_key, load_master_key

ISSUER = "interlock"
AUDIENCE = "interlock-gateway"
ALGORITHM = "HS256"
REQUIRED_CLAIMS = ["exp", "iat", "sub", "sid", "jti", "iss", "aud"]
CLOCK_SKEW = timedelta(seconds=30)
_TTL = re.compile(r"^(\d+)([smhd])$")
_UNITS = {"s": "seconds", "m": "minutes", "h": "hours", "d": "days"}


class TokenError(InterlockError):
    pass


@dataclass(frozen=True)
class WorkerClaims:
    project: str
    session_id: str
    token_id: str
    issued_at: datetime
    expires_at: datetime


def parse_ttl(text: str) -> timedelta:
    match = _TTL.match(text.strip())
    if not match:
        raise TokenError(f"invalid ttl '{text}': use a number and a unit, e.g. 30m, 1h, 2d")
    return timedelta(**{_UNITS[match[2]]: int(match[1])})


def _key(settings: Settings) -> bytes:
    return derive_key(load_master_key(settings), "worker-tokens")


def issue_token(
    project: str,
    ttl: timedelta,
    *,
    session_id: str | None = None,
    settings: Settings | None = None,
    now: datetime | None = None,
) -> tuple[str, WorkerClaims]:
    settings = settings or get_settings()
    max_ttl = timedelta(seconds=settings.token_max_ttl_seconds)
    if ttl <= timedelta(0) or ttl > max_ttl:
        raise TokenError(f"ttl must be positive and at most {max_ttl}")
    issued = (now or datetime.now(UTC)).replace(microsecond=0)
    claims = WorkerClaims(
        project=project,
        session_id=session_id or str(uuid.uuid4()),
        token_id=str(uuid.uuid4()),
        issued_at=issued,
        expires_at=issued + ttl,
    )
    payload = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": claims.project,
        "sid": claims.session_id,
        "jti": claims.token_id,
        "iat": int(claims.issued_at.timestamp()),
        "exp": int(claims.expires_at.timestamp()),
    }
    return jwt.encode(payload, _key(settings), algorithm=ALGORITHM), claims


def verify_token(token: str, *, settings: Settings | None = None) -> WorkerClaims:
    settings = settings or get_settings()
    try:
        payload: dict[str, Any] = jwt.decode(
            token,
            _key(settings),
            algorithms=[ALGORITHM],  # never trust the header's choice of algorithm
            audience=AUDIENCE,
            issuer=ISSUER,
            options={"require": REQUIRED_CLAIMS},
        )
    except jwt.ExpiredSignatureError:
        raise TokenError("worker token has expired; ask for a new one") from None
    except jwt.InvalidSignatureError:
        raise TokenError("worker token signature is invalid") from None
    except jwt.InvalidTokenError:
        raise TokenError("worker token is malformed or not an Interlock token") from None

    issued = datetime.fromtimestamp(payload["iat"], UTC)
    expires = datetime.fromtimestamp(payload["exp"], UTC)
    if issued > datetime.now(UTC) + CLOCK_SKEW:
        raise TokenError("worker token is issued in the future")
    if expires - issued > timedelta(seconds=settings.token_max_ttl_seconds):
        raise TokenError("worker token lifetime exceeds the allowed maximum")
    if not all(isinstance(payload[k], str) and payload[k] for k in ("sub", "sid", "jti")):
        raise TokenError("worker token is malformed or not an Interlock token")
    return WorkerClaims(
        project=payload["sub"],
        session_id=payload["sid"],
        token_id=payload["jti"],
        issued_at=issued,
        expires_at=expires,
    )
