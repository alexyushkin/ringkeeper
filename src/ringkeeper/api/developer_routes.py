"""Settings > Developer apps: your own registered apps, and signing accounts in through them
(services.developer_apps). The browser comes back to /oauth/callback, or (Intuit's
production keys) the page's address is pasted here. A secret never comes back out."""

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field

from ringkeeper.api.auth import AdminDep
from ringkeeper.api.deps import RecentAuth, SessionDep, WriteSessionDep, require_owner
from ringkeeper.config import Settings, get_settings
from ringkeeper.db import session_scope
from ringkeeper.errors import RuleViolationError
from ringkeeper.models import Env, InstanceType
from ringkeeper.schemas import Input, NonEmpty, Slug
from ringkeeper.services import admin_audit, developer_apps, google_oauth, instances

router = APIRouter(prefix="/developer-apps", dependencies=[AdminDep, Depends(require_owner)])

AppSecret = Annotated[str, Field(min_length=6, max_length=500, repr=False)]
ClientId = Annotated[str, Field(min_length=4, max_length=300)]
ProviderKey = Literal["microsoft", "hubspot", "salesforce", "xero", "intuit", "jobber"]


def _address(settings: Settings) -> str:
    try:
        return developer_apps.fixed_redirect(settings)
    except developer_apps.SignInError as error:
        return str(error)


def _redirect(settings: Settings, found: developer_apps.Provider, app: developer_apps.App) -> str:
    try:
        return developer_apps.redirect_for(settings, found, app)
    except developer_apps.SignInError as error:
        return str(error)


def _accounts(session: Any) -> dict[str, int]:
    """How many connections signed in through each app."""
    counts: dict[str, int] = {}
    for instance in instances.list_instances(session, type=InstanceType.VENDOR):
        if app := instance.config.get("app"):
            counts[str(app)] = counts.get(str(app), 0) + 1
    return counts


def _shown(settings: Settings, found: developer_apps.Provider, session: Any) -> dict[str, Any]:
    counts = _accounts(session)
    redirect = _address(settings)
    apps = developer_apps.list_apps(settings, found.key)
    return {
        "key": found.key,
        "name": found.name,
        "console": found.console,
        "redirect": redirect,
        "steps": [s.format(redirect=redirect) for s in found.steps],
        "scopes": [*found.scopes, *(a.scope for a in found.access)],
        "access": [{"key": a.key, "label": a.label} for a in found.access],
        "options": [
            {"key": o.key, "label": o.label, "hint": o.hint, "default": o.default,
             "choices": [list(c) for c in o.choices]}
            for o in found.options
        ],
        "note": found.note,
        "apps": [
            {
                "id": a.id, "name": a.name, "client_id": a.client_id, "options": dict(a.options),
                "redirect": _redirect(settings, found, a),
                "accounts": counts.get(a.id, 0),
            }
            for a in apps
        ],
    }  # fmt: skip


@router.get("")
def list_all(session: SessionDep) -> dict[str, Any]:
    """Every vendor that needs an app of yours: the guide, what to paste, and your apps."""
    settings = get_settings()
    return {
        "desktop": settings.mode == "desktop",
        "providers": [_shown(settings, p, session) for p in developer_apps.PROVIDERS.values()],
    }


class AppIn(Input):
    name: Annotated[str, Field(min_length=1, max_length=80)]
    client_id: ClientId
    client_secret: AppSecret
    options: dict[Annotated[str, Field(max_length=40)], Annotated[str, Field(max_length=320)]] = (
        Field(default_factory=dict[str, str])
    )


@router.post("/{provider}/apps", status_code=201, dependencies=[RecentAuth])
def add_app(provider: ProviderKey, data: AppIn, session: WriteSessionDep) -> dict[str, Any]:
    settings = get_settings()
    app = developer_apps.save_app(
        settings, provider, data.name, data.client_id, data.client_secret, data.options
    )
    admin_audit.record(
        session, "secret.developer_app", f"added the {provider} app {app.name} ({app.id})",
        by="owner",
    )  # fmt: skip
    return _shown(settings, developer_apps.provider(provider), session)


@router.put("/{provider}/apps/{app_id}", dependencies=[RecentAuth])
def replace_app(
    provider: ProviderKey, app_id: Slug, data: AppIn, session: WriteSessionDep
) -> dict[str, Any]:
    settings = get_settings()
    app = developer_apps.save_app(
        settings, provider, data.name, data.client_id, data.client_secret, data.options, app_id
    )
    admin_audit.record(
        session, "secret.developer_app", f"replaced the {provider} app {app.name} ({app.id})",
        by="owner",
    )  # fmt: skip
    return _shown(settings, developer_apps.provider(provider), session)


@router.delete("/{provider}/apps/{app_id}", status_code=204, dependencies=[RecentAuth])
def forget_app(provider: ProviderKey, app_id: Slug, session: WriteSessionDep) -> None:
    developer_apps.forget_app(get_settings(), provider, app_id)
    admin_audit.record(
        session, "secret.developer_app", f"removed the {provider} app {app_id}", by="owner"
    )


def _checked(run: Any) -> dict[str, Any]:
    try:
        return {"ok": True, "message": run()}
    except developer_apps.SignInError as error:
        return {"ok": False, "message": str(error)}


@router.post("/{provider}/apps/{app_id}/check")
def check_app(provider: ProviderKey, app_id: Slug) -> dict[str, Any]:
    """Live check: does the vendor accept this client? Nothing is created."""
    return _checked(lambda: developer_apps.check_app(get_settings(), provider, app_id))


@router.post("/google/apps/{app_id}/check")
def check_google(app_id: Slug) -> dict[str, Any]:
    settings = get_settings()
    app = google_oauth.load_app(settings, app_id)
    if app is None:
        raise HTTPException(404, detail="There's no Google app like that.")
    return _checked(lambda: developer_apps.check_client(
        google_oauth.TOKEN, app.client_id, app.client_secret, "http://127.0.0.1/oauth/callback",
        basic=False,
    ))  # fmt: skip


class SignInIn(Input):
    vendor: Annotated[str, Field(min_length=2, max_length=40)]
    access: Annotated[list[Annotated[str, Field(max_length=40)]], Field(max_length=20)] = Field(
        default_factory=list[str]
    )
    # A new account: whose (a client's, or yours: shared), and where it goes.
    owner: NonEmpty | None = None
    shared: bool = False
    all_projects: bool = False
    env: Env = Env.DEV
    name: Slug | None = None
    project: Slug | None = None
    replace: Slug | None = None  # or a connection to sign in again
    app: Slug | None = None  # left out: the first


@router.post("/signin", dependencies=[RecentAuth])
def start_signin(data: SignInIn, session: SessionDep) -> dict[str, Any]:
    """The vendor's page to open; the account is added once it comes back."""
    if not data.replace and not data.shared and not data.owner:
        raise HTTPException(422, detail="Say whose account this is.")
    connection = data.model_dump(
        exclude={"vendor", "access", "app"}, exclude_none=True, mode="json"
    )
    developer_apps.check_start(session, connection, data.vendor)
    try:
        url, state, paste = developer_apps.start(
            get_settings(), data.vendor, connection, data.access, data.app
        )
    except developer_apps.SignInError as error:
        raise HTTPException(409, detail=str(error)) from None
    return {"url": url, "state": state, "paste": paste}


@router.get("/signin/{state}")
def signin_status(state: str) -> dict[str, Any]:
    found = developer_apps.outcome(state)
    if found is None:
        raise HTTPException(404, detail="No sign-in like that here.")
    return {"state": found.state, "name": found.name, "error": found.error}


class PasteIn(Input):
    address: Annotated[str, Field(min_length=10, max_length=3000, repr=False)]


@router.post("/signin/{state}/paste", dependencies=[RecentAuth])
def finish_pasted(state: str, data: PasteIn) -> dict[str, Any]:
    """The address of the page a sign-in ended on, for a vendor that can't come back here."""
    try:
        code, back = developer_apps.from_address(state, data.address)
        found = developer_apps.take(state)
        with session_scope() as session:
            name = developer_apps.finish(session, get_settings(), found, state, code, back)
    except (developer_apps.SignInError, RuleViolationError) as error:
        raise HTTPException(409, detail=str(error)) from None
    return {"state": "done", "name": name}
