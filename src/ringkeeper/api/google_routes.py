"""Google accounts: the owner's Google app (Settings > Google), signing in an account
(a client's or your own), and each account's write rules.

The desktop app only, for now: its sign-in comes back to the app's own loopback
address, which a "Desktop app" OAuth client accepts on any port. The callback
is the one MCP sign-ins use (/oauth/callback, api.remote_routes), told apart by
its one-time state.
"""

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import Field

from ringkeeper.api.auth import AdminDep
from ringkeeper.api.deps import RecentAuth, SessionDep, WriteSessionDep, require_owner
from ringkeeper.api.remote_routes import CALLBACK_PATH
from ringkeeper.config import Settings, get_settings
from ringkeeper.models import InstanceType
from ringkeeper.schemas import Input, NonEmpty, Slug
from ringkeeper.services import admin_audit, google_connections, google_oauth, instances

router = APIRouter(prefix="/google", dependencies=[AdminDep, Depends(require_owner)])
rules_router = APIRouter(dependencies=[AdminDep, Depends(require_owner)])

AccessKey = Literal[
    "mail_read", "mail_send", "mail_drafts", "mail_trash", "drive_files", "drive_read_all",
    "calendar_read", "calendar_write", "sheets_read", "sheets_write",
]  # fmt: skip


def desktop(settings: Settings) -> bool:
    """Google sign-in comes back to the desktop app's loopback address (server mode later)."""
    return settings.mode == "desktop"


@router.get("/app")
def app_status() -> dict[str, Any]:
    """Whether your Google app is set up (its client ID; the secret never comes back), what
    an account can be allowed, and the address to give a Web client later."""
    settings = get_settings()
    app = google_oauth.load_app(settings)
    return {
        "configured": app is not None,
        "client_id": app.client_id if app else None,
        "desktop": desktop(settings),
        "access": [
            {"key": a.key, "label": a.label, "scope": a.scope, "note": a.note}
            for a in google_oauth.ACCESS.values()
        ],
    }


class AppIn(Input):
    client_id: Annotated[str, Field(min_length=10, max_length=300)]
    client_secret: Annotated[str, Field(min_length=6, max_length=300, repr=False)]


@router.put("/app", dependencies=[RecentAuth])
def save_app(data: AppIn, session: WriteSessionDep) -> dict[str, Any]:
    google_oauth.save_app(get_settings(), data.client_id, data.client_secret)
    admin_audit.record(session, "secret.google_app", "stored the Google app", by="owner")
    return app_status()


@router.delete("/app", status_code=204, dependencies=[RecentAuth])
def remove_app(session: WriteSessionDep) -> None:
    google_oauth.forget_app(get_settings())
    admin_audit.record(session, "secret.google_app", "removed the Google app", by="owner")


class SignInIn(Input):
    access: Annotated[list[AccessKey], Field(min_length=1, max_length=10)]
    # A new account: whose (a client's, or yours: shared), and where it goes.
    owner: NonEmpty | None = None
    shared: bool = False
    all_projects: bool = False
    name: Slug | None = None  # left out: from the address (seaside-info)
    project: Slug | None = None
    # Or an account to sign in again (more access, or Google stopped accepting it).
    replace: Slug | None = None


@router.post("/signin", dependencies=[RecentAuth])
def start_signin(data: SignInIn, request: Request, session: SessionDep) -> dict[str, str]:
    """Google's page to open; the account is added once it sends the browser back."""
    settings = get_settings()
    if not desktop(settings):
        raise HTTPException(409, detail="Google accounts work in the desktop app for now.")
    if not data.replace and not data.shared and not data.owner:
        raise HTTPException(422, detail="Say whose Google account this is.")
    connection: dict[str, Any] = data.model_dump(exclude={"access"}, exclude_none=True)
    if data.replace:
        email = instances.get_instance(session, data.replace).config.get("email")
        if email:
            connection["login_hint"] = email
    google_connections.check_start(session, connection)
    try:
        # The app's own loopback address: a "Desktop app" client takes any port.
        back = str(request.base_url).rstrip("/") + CALLBACK_PATH
        url = google_oauth.start(settings, back, list(data.access), connection)
    except google_oauth.SignInError as error:
        raise HTTPException(409, detail=str(error)) from None
    state = url.split("state=", 1)[1].split("&", 1)[0]
    google_connections.started(state)
    return {"url": url, "state": state}


@router.get("/signin/{state}")
def signin_status(state: str) -> dict[str, Any]:
    """How a sign-in started here went: waiting, done (and the connection), or failed."""
    found = google_connections.outcome(state)
    if found is None:
        raise HTTPException(404, detail="No sign-in like that here.")
    return {"state": found.state, "name": found.name, "error": found.error}


@rules_router.get("/connections/{name}/google")
def account(name: str, session: SessionDep) -> dict[str, Any]:
    """The account, what it was given, and each tool: allowed or not, asks first or runs."""
    instance = instances.get_instance(session, name)
    if instance.type is not InstanceType.GOOGLE:
        raise HTTPException(409, detail=f"'{name}' isn't a Google account.")
    return {
        "email": instance.config.get("email"),
        "access": instance.config.get("access") or [],
        "tools": google_connections.tools(instance),
    }


class WriteRulesIn(Input):
    rules: dict[Annotated[str, Field(max_length=64)], Literal["ask", "run"]]


@rules_router.put("/connections/{name}/write-rules", dependencies=[RecentAuth])
def write_rules(name: str, data: WriteRulesIn, session: WriteSessionDep) -> dict[str, Any]:
    """Per write tool: ask first (the default) or run without asking. Writes only."""
    google_connections.set_write_rules(session, name, dict(data.rules))
    return account(name, session)
