"""Google accounts: the owner's Google apps (Settings > Google app), signing in an
account (a client's or your own) through one of them, and each account's write rules.

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
from ringkeeper.errors import RuleViolationError
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
def app_status(session: SessionDep) -> dict[str, Any]:
    """Your Google apps (each one's client ID; a secret never comes back) and the accounts
    signed in through each, what an account can be allowed, and whether sign-in works here.
    `configured` and `client_id`: the first app, the one accounts use unless they chose."""
    settings = get_settings()
    apps = google_oauth.list_apps(settings)
    accounts = google_connections.accounts(session, settings)
    return {
        "configured": bool(apps),
        "client_id": apps[0].client_id if apps else None,
        "apps": [
            {
                "id": a.id,
                "name": a.name,
                "client_id": a.client_id,
                "accounts": sum(1 for account in accounts if account["app"] == a.id),
            }
            for a in apps
        ],
        "accounts": accounts,
        "desktop": desktop(settings),
        "access": [
            {"key": a.key, "label": a.label, "scope": a.scope, "note": a.note}
            for a in google_oauth.ACCESS.values()
        ],
    }


class AppIn(Input):
    client_id: Annotated[str, Field(min_length=10, max_length=300)]
    client_secret: Annotated[str, Field(min_length=6, max_length=300, repr=False)]
    name: Annotated[str, Field(max_length=80)] | None = None


@router.put("/app", dependencies=[RecentAuth])
def save_app(data: AppIn, session: WriteSessionDep) -> dict[str, Any]:
    """The first app: set it up, or replace its client."""
    google_oauth.save_app(get_settings(), data.client_id, data.client_secret, name=data.name)
    admin_audit.record(session, "secret.google_app", "stored the Google app", by="owner")
    return app_status(session)


@router.delete("/app", status_code=204, dependencies=[RecentAuth])
def remove_app(session: WriteSessionDep) -> None:
    """The first app."""
    try:
        google_oauth.forget_app(get_settings())
    except RuleViolationError:
        return  # none to remove
    admin_audit.record(session, "secret.google_app", "removed the Google app", by="owner")


@router.post("/apps", status_code=201, dependencies=[RecentAuth])
def add_app(data: AppIn, session: WriteSessionDep) -> dict[str, Any]:
    """Another Google app: a client's own Google Cloud project, or one more 100 accounts."""
    app = google_oauth.add_app(get_settings(), data.name or "", data.client_id, data.client_secret)
    admin_audit.record(
        session, "secret.google_app", f"added the Google app {app.name} ({app.id})", by="owner"
    )
    return app_status(session)


@router.put("/apps/{app_id}", dependencies=[RecentAuth])
def replace_app(app_id: Slug, data: AppIn, session: WriteSessionDep) -> dict[str, Any]:
    app = google_oauth.save_app(
        get_settings(), data.client_id, data.client_secret, app_id=app_id, name=data.name
    )
    admin_audit.record(
        session, "secret.google_app", f"replaced the Google app {app.name} ({app.id})",
        by="owner",
    )  # fmt: skip
    return app_status(session)


@router.delete("/apps/{app_id}", status_code=204, dependencies=[RecentAuth])
def forget_app(app_id: Slug, session: WriteSessionDep) -> None:
    google_oauth.forget_app(get_settings(), app_id)
    admin_audit.record(session, "secret.google_app", f"removed the Google app {app_id}", by="owner")


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
    # The Google app to sign in through. Left out: a new account uses the first, and
    # one signing in again the one it used.
    app: Slug | None = None


@router.post("/signin", dependencies=[RecentAuth])
def start_signin(data: SignInIn, request: Request, session: SessionDep) -> dict[str, str]:
    """Google's page to open; the account is added once it sends the browser back."""
    settings = get_settings()
    if not desktop(settings):
        raise HTTPException(409, detail="Google accounts work in the desktop app for now.")
    if not data.replace and not data.shared and not data.owner:
        raise HTTPException(422, detail="Say whose Google account this is.")
    connection: dict[str, Any] = data.model_dump(exclude={"access", "app"}, exclude_none=True)
    app = data.app
    if data.replace:
        before = instances.get_instance(session, data.replace)
        if email := before.config.get("email"):
            connection["login_hint"] = email
        app = app or google_connections.app_of(before, None)
    google_connections.check_start(session, connection)
    try:
        # The app's own loopback address: a "Desktop app" client takes any port.
        back = str(request.base_url).rstrip("/") + CALLBACK_PATH
        url = google_oauth.start(settings, back, list(data.access), connection, app)
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
    first = google_oauth.load_app(get_settings())
    return {
        "email": instance.config.get("email"),
        "access": instance.config.get("access") or [],
        "app": google_connections.app_of(instance, first.id if first else None),
        "tools": google_connections.tools(instance),
    }


class WriteRulesIn(Input):
    rules: dict[Annotated[str, Field(max_length=64)], Literal["ask", "run"]]


@rules_router.put("/connections/{name}/write-rules", dependencies=[RecentAuth])
def write_rules(name: str, data: WriteRulesIn, session: WriteSessionDep) -> dict[str, Any]:
    """Per write tool: ask first (the default) or run without asking. Writes only."""
    google_connections.set_write_rules(session, name, dict(data.rules))
    return account(name, session)
