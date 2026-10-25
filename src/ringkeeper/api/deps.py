"""Shared FastAPI dependencies: DB sessions, the owner session, the client address.

Every endpoint needs the admin token (only the web app's server side and
your scripts have it). Endpoints the web app uses on the owner's behalf
also need a signed-in owner: the web server passes the owner's session
token in `X-Ringkeeper-Session`, and the browser's address in
`X-Ringkeeper-Client` for rate limiting. Both headers are trusted only
because the admin token is required too.
"""

from collections.abc import Generator
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session

from ringkeeper.config import get_settings
from ringkeeper.db import session_scope
from ringkeeper.services import owner

SESSION_HEADER = "X-Ringkeeper-Session"
CLIENT_HEADER = "X-Ringkeeper-Client"


def get_session() -> Generator[Session]:
    # Reads never commit.
    with session_scope(commit=False) as session:
        yield session


def get_write_session() -> Generator[Session]:
    with session_scope() as session:
        yield session


SessionDep = Annotated[Session, Depends(get_session)]
WriteSessionDep = Annotated[Session, Depends(get_write_session)]


def client_address(
    request: Request, x_ringkeeper_client: Annotated[str | None, Header()] = None
) -> str:
    if x_ringkeeper_client:
        return x_ringkeeper_client.strip()[:100]
    return request.client.host if request.client else "unknown"


ClientDep = Annotated[str, Depends(client_address)]


def require_owner(x_ringkeeper_session: Annotated[str | None, Header()] = None) -> str:
    """The owner's session token, if it is valid (idle and absolute expiry checked)."""
    if not x_ringkeeper_session:
        raise HTTPException(401, detail="sign in first")
    try:
        with session_scope() as session:
            owner.verify_session(session, get_settings(), x_ringkeeper_session)
    except owner.AuthError:
        raise HTTPException(401, detail="sign in first") from None
    return x_ringkeeper_session


OwnerToken = Annotated[str, Depends(require_owner)]
OWNER = "owner"  # how the web app's owner appears in audit rows and answers
