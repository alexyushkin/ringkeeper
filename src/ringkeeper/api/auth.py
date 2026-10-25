"""Token auth, required on every endpoint except /health. Project names,
decisions and audit rows are client data, so reads are protected too.

Two tokens are accepted (see admin_token.py):

- the admin token: everything, for your scripts;
- the web token (the web app's server side): only setup and sign-in on its
  own; everything else also needs a valid owner session in
  `X-Ringkeeper-Session`.

Tokens are read from the vault on each request, so `ringkeeper auth
api-token --rotate` (or a new web token) takes effect without a restart.
With no admin token stored the API fails closed.

Desktop mode has a third caller: the app's own window. There's no token or
sign-in; the desktop front door (ringkeeper.desktop.server) checks the
window's per-launch cookie and marks the request, and only then, and only
in desktop mode, is it the owner.
"""

import hmac
from enum import StrEnum
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from ringkeeper.admin_token import stored_api_token, stored_web_token
from ringkeeper.config import get_settings
from ringkeeper.db import session_scope
from ringkeeper.services import owner

_bearer = HTTPBearer(auto_error=False)

# What the web token may call without a signed-in owner.
WEB_PUBLIC = frozenset(
    {
        ("GET", "/setup/status"),
        ("POST", "/setup/owner"),
        ("POST", "/auth/login"),
        ("POST", "/auth/passkeys/login/options"),
        ("POST", "/auth/passkeys/login"),
    }
)


DESKTOP_MARK = "ringkeeper_desktop"  # request.state flag set by the desktop front door


class Caller(StrEnum):
    ADMIN = "admin"
    WEB = "web"
    DESKTOP = "desktop"  # the desktop app's window: the one local user, the owner


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(401, detail=detail, headers={"WWW-Authenticate": "Bearer"})


def _matches(presented: str, expected: str | None) -> bool:
    return expected is not None and hmac.compare_digest(presented.encode(), expected.encode())


def require_admin(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    x_ringkeeper_session: Annotated[str | None, Header()] = None,
) -> None:
    if getattr(request.state, DESKTOP_MARK, False) and get_settings().mode == "desktop":
        request.state.caller = Caller.DESKTOP
        return
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise _unauthorized("the admin token is required (Authorization: Bearer ...)")
    expected = stored_api_token()
    if expected is None:
        raise HTTPException(
            503, detail="no admin token is configured; run `ringkeeper auth api-token`"
        )
    if _matches(credentials.credentials, expected):
        request.state.caller = Caller.ADMIN
        return
    if not _matches(credentials.credentials, stored_web_token()):
        raise _unauthorized("invalid admin token")
    request.state.caller = Caller.WEB
    if (request.method.upper(), request.url.path) in WEB_PUBLIC:
        return
    if not x_ringkeeper_session:
        raise HTTPException(401, detail="sign in first")
    try:
        with session_scope() as session:
            owner.verify_session(session, get_settings(), x_ringkeeper_session)
    except owner.AuthError:
        raise HTTPException(401, detail="sign in first") from None


def caller(request: Request) -> Caller | None:
    return getattr(request.state, "caller", None)


AdminDep = Depends(require_admin)
