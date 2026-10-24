"""Admin-token auth, required on every endpoint except /health. Project names,
decisions and audit rows are client data, so reads are protected too.

The token is read from the vault on each request, so `ringkeeper auth
api-token --rotate` takes effect without a restart. With no token stored the
API fails closed.
"""

import hmac
from typing import Annotated

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from ringkeeper.admin_token import stored_api_token

_bearer = HTTPBearer(auto_error=False)


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(401, detail=detail, headers={"WWW-Authenticate": "Bearer"})


def require_admin(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> None:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise _unauthorized("the admin token is required (Authorization: Bearer ...)")
    expected = stored_api_token()
    if expected is None:
        raise HTTPException(
            503, detail="no admin token is configured; run `ringkeeper auth api-token`"
        )
    if not hmac.compare_digest(credentials.credentials.encode(), expected.encode()):
        raise _unauthorized("invalid admin token")


AdminDep = Depends(require_admin)
