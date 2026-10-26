"""Remote MCP services: the catalog, signing in, pasting a token, and the sign-in callback.

The callback has no session to check: the browser arrives there from the
service's own page. It is protected by the one-time `state` instead, which
only the sign-in this process started knows (services.mcp_oauth).
"""

import contextlib
from html import escape
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import Field

from ringkeeper.api.auth import AdminDep
from ringkeeper.api.deps import RecentAuth, SessionDep, WriteSessionDep, require_owner
from ringkeeper.config import Settings, get_settings
from ringkeeper.db import session_scope
from ringkeeper.errors import RuleViolationError
from ringkeeper.models import Env
from ringkeeper.schemas import Input, NonEmpty, ScopeValue, Slug
from ringkeeper.services import mcp_oauth, remote_connections
from ringkeeper.services.remote_connections import NewConnection

router = APIRouter(dependencies=[AdminDep, Depends(require_owner)])
callback = APIRouter()

CALLBACK_PATH = "/oauth/callback"


class RemoteConnectionIn(Input):
    remote: Annotated[str, Field(min_length=1, max_length=40)]
    name: Slug
    owner: NonEmpty
    env: Env = Env.DEV
    project: Slug | None = None
    scope: dict[Slug, ScopeValue] = Field(default_factory=dict[str, str])

    def new(self) -> NewConnection:
        return NewConnection(
            remote=self.remote, name=self.name, owner=self.owner, env=self.env,
            project=self.project, scope=dict(self.scope) or None,
        )  # fmt: skip


class TokenConnectionIn(RemoteConnectionIn):
    token: str = Field(repr=False, max_length=4000)


def redirect_uri(request: Request, settings: Settings) -> str:
    """Where the service sends the browser back: this app's own address."""
    if settings.mode == "desktop":
        return str(request.base_url).rstrip("/") + CALLBACK_PATH
    if not settings.web_origin:
        raise HTTPException(
            409, detail="Signing in needs RINGKEEPER_WEB_ORIGIN, the web app's own address."
        )
    return settings.web_origin.rstrip("/") + CALLBACK_PATH


@router.get("/remote-services")
def list_remote_services() -> list[dict[str, Any]]:
    return remote_connections.catalog()


@router.post("/connections/signin", dependencies=[RecentAuth])
def start_signin(data: RemoteConnectionIn, request: Request, session: SessionDep) -> dict[str, str]:
    """The service's sign-in page to open; the connection is added once it sends you back."""
    new = data.new()
    remote_connections.check(session, new)
    settings = get_settings()
    try:
        url = mcp_oauth.start(new.remote, redirect_uri(request, settings), new.as_dict())
    except mcp_oauth.SignInError as error:
        raise HTTPException(502, detail=str(error)) from None
    return {"url": url}


@router.post("/connections/remote", status_code=201, dependencies=[RecentAuth])
def add_with_token(data: TokenConnectionIn, session: WriteSessionDep) -> dict[str, Any]:
    secret = remote_connections.token_secret(data.remote, data.token)
    instance = remote_connections.create(session, get_settings(), data.new(), secret, how="token")
    return {"name": instance.name}


def _page(title: str, text: str, ok: bool) -> HTMLResponse:
    color = "#15803d" if ok else "#b91c1c"
    body = (
        "<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width'>"
        f"<title>{escape(title)}</title>"
        "<body style='font:16px system-ui;max-width:32rem;margin:15vh auto;padding:0 1rem'>"
        f"<h1 style='font-size:1.25rem;color:{color}'>{escape(title)}</h1>"
        f"<p>{escape(text)}</p></body>"
    )
    return HTMLResponse(body, status_code=200 if ok else 400)


@callback.get(CALLBACK_PATH, include_in_schema=False)
def finish_signin(
    state: str = "", code: str = "", error: str = "", error_description: str = ""
) -> HTMLResponse:
    if error:
        with contextlib.suppress(mcp_oauth.SignInError):
            mcp_oauth.take(state)  # spent either way
        said = error_description or error
        return _page("Not connected", f"The service said: {said[:300]}. Try again.", False)
    try:
        pending = mcp_oauth.take(state)
        secret = mcp_oauth.finish(pending, code)
        new = NewConnection.from_dict(pending.connection)
        with session_scope() as session:
            instance = remote_connections.create(session, get_settings(), new, secret, how="signin")
            name = instance.name
    except (mcp_oauth.SignInError, RuleViolationError) as failed:
        return _page("Not connected", str(failed), False)
    return _page(
        "Connected",
        f"Ringkeeper is connected as '{name}'. You can close this tab and go back to Ringkeeper.",
        True,
    )
