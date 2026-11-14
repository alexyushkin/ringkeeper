"""What the owner manages in the web app: settings, secrets, projects, connections.

Every endpoint needs a token and a signed-in owner. Writes go through the
services layer and are audited (outcome `changed`). Secret values are
write-only: no response, log line or audit row ever contains one.

Changes that store a secret, redirect approvals or loosen them also need a
recent confirmation (deps.check_recent_auth): a stolen session cookie alone
can't point Telegram approvals at another chat or send a key elsewhere.
"""

import contextlib
import re
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Request,
)
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ringkeeper import netpolicy, remote
from ringkeeper.adapters import vendor
from ringkeeper.adapters.base import HOLD_DRAFTS, ToolError
from ringkeeper.api.auth import AdminDep
from ringkeeper.api.deps import (
    OWNER,
    OwnerToken,
    RecentAuth,
    SessionDep,
    WriteSessionDep,
    check_recent_auth,
    require_owner,
)
from ringkeeper.api.supervisor import Supervisor
from ringkeeper.claude_auth import ClaudeAuthError, credential_ref
from ringkeeper.config import get_settings
from ringkeeper.errors import RingkeeperError, RuleViolationError
from ringkeeper.models import AuthMode, Client, Env, Instance, InstanceType, ProjectInstance
from ringkeeper.schemas import (
    BindingIn,
    CleanList,
    Input,
    InstanceIn,
    ModelId,
    ModelKey,
    NonEmpty,
    ProjectIn,
    ScopeValue,
    Slug,
    Title,
    WorkspaceId,
)
from ringkeeper.scrub import scrub
from ringkeeper.services import (
    admin_audit,
    bindings,
    claude_place,
    client_month,
    clients,
    connection_export,
    google_oauth,
    instances,
    local_servers,
    mcp_tools,
    model_list,
    probes,
    projects,
    runtime,
    secrets_in,
    subscription,
    tasks,
    titles,
    web_access,
    workspaces,
)
from ringkeeper.services.runtime import RuntimeIn, current_settings
from ringkeeper.telegram.api import BotApi, TelegramError, from_vault, recent_chats
from ringkeeper.vault import get_vault

router = APIRouter(dependencies=[AdminDep, Depends(require_owner)])

# Settings that decide who can approve and where approvals go.
CONFIRMED_SETTINGS = frozenset({"telegram", "telegram_chat_ids", "telegram_requests"})


def _stored(ref: str) -> bool:
    try:
        get_vault().resolve(ref)
    except RingkeeperError:
        return False
    return True


def _unavailable(error: secrets_in.CheckUnavailableError) -> HTTPException:
    return HTTPException(502, detail=str(error))


# --- Settings ---


class SettingsOut(BaseModel):
    values: dict[str, Any]  # every editable setting, as it is now
    overridden: list[str]  # the ones saved here (the rest come from .env)
    services: dict[str, dict[str, object]]  # the bridge and router: running, error
    claude: dict[str, Any]
    telegram: dict[str, Any]


def _settings_out(request: Request, session: SessionDep) -> SettingsOut:
    settings = runtime.effective(session)
    supervisor: Supervisor | None = getattr(request.app.state, "supervisor", None)
    return SettingsOut(
        values={key: getattr(settings, key) for key in runtime.EDITABLE},
        overridden=sorted(runtime.overrides(session)),
        services=supervisor.status() if supervisor else {},
        claude={
            "subscription_allowed": subscription.allowed(settings),
            "subscription_note": subscription.NOTE,
            "mode": settings.claude_auth_mode.value,
            "api_key": _stored(credential_ref(settings, AuthMode.API_KEY)),
            "subscription": _stored(credential_ref(settings, AuthMode.SUBSCRIPTION)),
        },
        telegram={"token": _stored(settings.telegram_bot_token_ref)},
    )


class PlaceIn(Input):
    kind: Literal["anthropic", "bedrock", "vertex", "foundry"]
    region: Annotated[str, Field(max_length=30)] = ""
    profile: Annotated[str, Field(max_length=64)] = ""
    project: Annotated[str, Field(max_length=63)] = ""
    resource: Annotated[str, Field(max_length=63)] = ""
    use_login: bool = False
    models: dict[str, Annotated[str, Field(max_length=200)]] = Field(default_factory=dict[str, str])
    key: str | None = Field(default=None, repr=False, max_length=20_000)
    make_default: bool = True


@router.get("/claude/place")
def get_place(session: SessionDep) -> dict[str, Any]:
    """Where Claude runs. Which keys are stored, never what they are."""
    return {**claude_place.places(session), "stored": claude_place.stored_keys(get_vault())}


@router.put("/claude/place", dependencies=[RecentAuth])
def save_place(data: PlaceIn, session: WriteSessionDep) -> dict[str, Any]:
    fields = data.model_dump(exclude={"key", "make_default"})
    try:
        claude_place.save(session, get_vault(), fields, data.key, data.make_default)
    except RuleViolationError as error:
        raise HTTPException(422, detail=str(error)) from None
    admin_audit.record(session, "settings.claude_place", f"Claude runs in {data.kind}", by=OWNER)
    return get_place(session)


@router.post("/claude/place/check")
def check_place(data: PlaceIn) -> dict[str, Any]:
    """Live check, with what's on the form and the stored key. It sends one tiny request."""
    fields = data.model_dump(exclude={"key", "make_default"})
    try:
        place = claude_place.as_place(fields)
        return {"ok": True, "message": claude_place.check(place, get_vault(), current_settings())}
    except (ClaudeAuthError, RuleViolationError) as error:
        return {"ok": False, "message": str(error)}


class ProjectPlaceIn(Input):
    kind: Literal["anthropic", "bedrock", "vertex", "foundry"] | None = None


@router.get("/projects/{name}/claude-place")
def project_place(name: str, session: SessionDep) -> dict[str, Any]:
    return {"kind": claude_place.project_choice(session, name)}


@router.put("/projects/{name}/claude-place", dependencies=[RecentAuth])
def set_project_place(name: str, data: ProjectPlaceIn, session: WriteSessionDep) -> dict[str, Any]:
    try:
        claude_place.set_project(session, name, data.kind)
    except RuleViolationError as error:
        raise HTTPException(422, detail=str(error)) from None
    admin_audit.record(
        session, "settings.claude_place", f"this project runs in {data.kind or 'the default'}",
        by=OWNER, project=name,
    )  # fmt: skip
    return {"kind": data.kind}


@router.get("/settings")
def get_settings_view(request: Request, session: SessionDep) -> SettingsOut:
    return _settings_out(request, session)


@router.get("/models")
def list_models(session: SessionDep) -> dict[str, Any]:
    """The models to choose from: Anthropic's list, recommended ones first."""
    settings = runtime.effective(session)
    session.close()  # no transaction while Anthropic answers
    return model_list.offered(settings, get_vault(settings))


@router.patch("/settings")
def patch_settings(
    changes: RuntimeIn, request: Request, session: WriteSessionDep, token: OwnerToken
) -> SettingsOut:
    """Change some settings; null resets one to its .env value. Applied without a restart."""
    values = changes.model_dump(exclude_unset=True, mode="json")
    if CONFIRMED_SETTINGS & values.keys():
        check_recent_auth(request, token)
    runtime.update(session, values, by=OWNER)
    supervisor: Supervisor | None = getattr(request.app.state, "supervisor", None)
    if supervisor is not None:
        supervisor.wake()
    return _settings_out(request, session)


@router.post("/stop-all")
def stop_everything(request: Request, session: WriteSessionDep) -> dict[str, int]:
    """Pause the queue and the router, and stop every running or waiting task."""
    runtime.update(session, {"paused": True}, by=OWNER)
    stopped = tasks.stop_all(session, by=OWNER)
    admin_audit.record(session, "stop_all", f"stopped everything ({len(stopped)} tasks)", by=OWNER)
    return {"stopped": len(stopped)}


@router.post("/resume-all")
def resume_everything(session: WriteSessionDep) -> dict[str, bool]:
    runtime.update(session, {"paused": False}, by=OWNER)
    return {"paused": False}


# --- Secrets (write-only) ---


class ClaudeSecretIn(Input):
    mode: AuthMode = AuthMode.API_KEY
    value: str = Field(repr=False)
    # The key's Anthropic workspace: None leaves the saved one, "" clears it.
    workspace_id: WorkspaceId | Literal[""] | None = None


class TelegramSecretIn(Input):
    token: str = Field(repr=False)


@router.put("/secrets/claude", dependencies=[RecentAuth])
def put_claude_secret(data: ClaudeSecretIn, session: WriteSessionDep) -> dict[str, Any]:
    settings = current_settings()
    if data.mode is AuthMode.SUBSCRIPTION and not subscription.allowed(settings):
        # Anthropic's terms don't let an app offer claude.ai sign-in to others; your
        # own install may turn personal subscription mode on.
        raise HTTPException(
            422,
            detail="Personal subscription mode is off: set RINGKEEPER_ALLOW_SUBSCRIPTION=1 "
            "to use your own Claude subscription. Otherwise use an API key.",
        )
    workspace = settings.anthropic_workspace_id if data.workspace_id is None else data.workspace_id
    try:
        stored = secrets_in.store_claude(
            session, settings, data.mode, data.value, workspace or None
        )
    except ClaudeAuthError as error:
        raise HTTPException(422, detail=str(error)) from None
    except RuleViolationError as error:
        raise HTTPException(422, detail=str(error)) from None
    except secrets_in.CheckUnavailableError as error:
        raise _unavailable(error) from None
    changes: dict[str, Any] = {}
    if settings.claude_auth_mode is not data.mode:
        changes["claude_auth_mode"] = data.mode.value
    if data.mode is AuthMode.SUBSCRIPTION:
        changes["subscription_source"] = subscription.FROM_SETUP_TOKEN
    if data.workspace_id is not None and (data.workspace_id or None) != (
        settings.anthropic_workspace_id
    ):
        changes["anthropic_workspace_id"] = data.workspace_id or None
    if changes:
        runtime.update(session, changes, by=OWNER)
    return {"stored": True, "verified": stored.verified, "message": stored.message}


@router.post("/secrets/claude/from-claude-code", dependencies=[RecentAuth])
def use_claude_code_login(session: WriteSessionDep) -> dict[str, Any]:
    """Subscription mode with the Claude Code login on this computer (desktop, personal
    mode only). Its token is copied into the vault, and again whenever it changes."""
    settings = current_settings()
    if settings.mode != "desktop" or not settings.allow_subscription:
        raise HTTPException(422, detail="Turn on personal subscription mode first.")
    stored = secrets_in.store_claude(
        session, settings, AuthMode.SUBSCRIPTION, subscription.login_token()
    )
    runtime.update(
        session,
        {
            "claude_auth_mode": AuthMode.SUBSCRIPTION.value,
            "subscription_source": subscription.FROM_LOGIN,
        },
        by=OWNER,
    )
    return {"stored": True, "verified": stored.verified, "message": stored.message}


@router.put("/secrets/telegram", dependencies=[RecentAuth])
def put_telegram_secret(data: TelegramSecretIn, session: WriteSessionDep) -> dict[str, Any]:
    try:
        bot = secrets_in.store_telegram(session, get_settings(), data.token)
    except RuleViolationError as error:
        raise HTTPException(422, detail=str(error)) from None
    except secrets_in.CheckUnavailableError as error:
        raise _unavailable(error) from None
    return {"stored": True, "bot": bot}


@router.get("/telegram/detect")
def detect_telegram_chats(
    request: Request, code: Annotated[str, Query(min_length=6, max_length=32)]
) -> dict[str, Any]:
    """Chats that sent the bot the pairing code the app shows, for picking the chat id.
    Not while the bridge runs: it takes every update first."""
    supervisor: Supervisor | None = getattr(request.app.state, "supervisor", None)
    if supervisor and supervisor.status().get("telegram", {}).get("running"):
        raise HTTPException(409, detail="Telegram is already connected.")
    settings = get_settings()
    try:
        api: BotApi = from_vault(get_vault(settings), settings)
    except TelegramError as error:
        raise HTTPException(409, detail=str(error)) from None
    try:
        updates = api.get_updates(None, 0)
    except TelegramError as error:
        raise HTTPException(502, detail=str(error)) from None
    finally:
        api.close()
    return {"chats": recent_chats(updates, code)}


# --- Projects ---


class ProjectPatch(Input):
    title: Title | None = None
    owner: NonEmpty | None = None
    description: str | None = None
    aliases: CleanList | None = None
    repo_url: str | None = None
    workspace_path: str | None = None
    autonomy: str | None = None
    web: bool | None = None
    checks: CleanList | None = None
    models: dict[ModelKey, ModelId] | None = None


class ConnectionBindingIn(Input):
    is_default: bool = False
    allowed_tools: CleanList = Field(default_factory=list[str])
    preapproved_writes: CleanList = Field(default_factory=list[str])
    scope: dict[Slug, ScopeValue] | None = None  # left out: the link keeps its scope


WORKSPACE_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*(/[A-Za-z0-9][A-Za-z0-9._-]*)*")


def _check_workspace(path: str | None) -> None:
    """From the web app, a folder inside RINGKEEPER_WORKSPACES_ROOT only: an absolute
    path would let a worker mount anything on the host. (The CLI may still set one.)"""
    if path is None:
        return
    if not WORKSPACE_PATTERN.fullmatch(path) or ".." in path.split("/"):
        raise HTTPException(
            422,
            detail="The folder must be a name inside the workspaces folder, like acme-web "
            "(letters, digits, - _ . and /).",
        )


@router.post("/projects", status_code=201)
def create_project(data: ProjectIn, session: WriteSessionDep) -> dict[str, Any]:
    settings = get_settings()
    path: Path | None = None
    if settings.mode == "desktop":
        path = workspaces.choose(settings, data.workspace_path, data.name)
        data = data.model_copy(update={"workspace_path": str(path)})
    else:
        _check_workspace(data.workspace_path)
    project = projects.create_project(session, data)
    admin_audit.record(
        session, "project.create", f"created project {project.title}", by=OWNER,
        project=project.name,
    )  # fmt: skip
    if path is None:
        return {"name": project.name}
    folder = workspaces.prepare(settings, path)
    return {
        "name": project.name, "folder": folder.path, "created": folder.created,
        "git": folder.git,
    }  # fmt: skip


@router.patch("/projects/{name}")
def update_project(
    name: str, data: ProjectPatch, request: Request, session: WriteSessionDep, token: OwnerToken
) -> dict[str, str]:
    project = projects.get_project(session, name)
    if data.autonomy is not None and data.autonomy != project.autonomy:
        check_recent_auth(request, token)  # autonomy decides what runs without asking
    if data.web and not web_access.allowed(session, project):
        check_recent_auth(request, token)  # the web is a way out for a client's data
    settings = get_settings()
    folder: Path | None = None
    if settings.mode == "desktop":
        if "workspace_path" in data.model_fields_set:
            folder = workspaces.choose(settings, data.workspace_path, name)
            data.workspace_path = str(folder)
    elif data.workspace_path != project.workspace_path:
        _check_workspace(data.workspace_path)
    current = ProjectIn.model_validate(
        {
            "name": project.name, "title": project.title, "owner": project.owner,
            "description": project.description,
            "aliases": project.aliases, "repo_url": project.repo_url,
            "workspace_path": project.workspace_path, "autonomy": project.autonomy,
            "web": project.web, "checks": project.checks, "models": project.models,
        }
    )  # fmt: skip
    changes = data.model_dump(exclude_unset=True)
    merged = ProjectIn.model_validate({**current.model_dump(), **changes})
    projects.update_project(session, merged)
    admin_audit.record(
        session, "project.update", f"changed {', '.join(sorted(changes)) or 'nothing'}",
        by=OWNER, project=name, details={"fields": sorted(changes)},
    )  # fmt: skip
    if folder is not None:
        workspaces.prepare(settings, folder)
    return {"name": name}


@router.delete("/projects/{name}", status_code=204)
def delete_project(name: str, session: WriteSessionDep) -> None:
    title = titles.of(session, name)
    removed = projects.remove_project(session, name)
    admin_audit.record(
        session, "project.delete", f"removed project {title} ({removed} connections unbound)",
        by=OWNER, project=name,
    )  # fmt: skip


class RenameIn(Input):
    name: Slug


@router.post("/projects/{name}/rename")
def rename_project(name: str, data: RenameIn, session: WriteSessionDep) -> dict[str, str]:
    """A new name everywhere the project is named; the old one stays as another name."""
    projects.rename_project(session, name, data.name)
    admin_audit.record(
        session, "project.rename", f"changed the short name {name} to {data.name}", by=OWNER,
        project=data.name,
    )  # fmt: skip
    return {"name": data.name}


# --- Clients ---


class ClientIn(Input):
    name: Annotated[str, Field(min_length=1, max_length=200)]


class ClientOut(BaseModel):
    slug: str
    name: str
    projects: int = 0
    connections: int = 0
    month_usd: float = 0.0  # AI cost of this month's tasks (services.client_month)
    cap_usd: float | None = None
    default_project: str | None = None  # as chosen; with one project, that one is used
    own: bool = False  # the operator's own client: general work goes here


class OwnIn(Input):
    own: bool


class DefaultProjectIn(Input):
    project: Annotated[str | None, Field(max_length=200)] = None


def _with_month(session: Session, listed: list[clients.Listed]) -> list[ClientOut]:
    now = client_month.local_now()
    spent = client_month.spend_by_client(session, now.year, now.month)
    rows = session.execute(
        select(Client.slug, Client.monthly_cap_usd, Client.default_project, Client.own)
    ).all()
    caps = {slug: (cap, default) for slug, cap, default, _own in rows}
    owns = {slug for slug, _cap, _default, own in rows if own}
    return [
        ClientOut(
            **c.__dict__,
            month_usd=round(spent.get(c.slug, 0.0), 4),
            cap_usd=caps.get(c.slug, (None, None))[0],
            default_project=caps.get(c.slug, (None, None))[1],
            own=c.slug in owns,
        )
        for c in listed
    ]


class MoveIn(Input):
    to: Annotated[str, Field(min_length=1, max_length=200)]


def _client_out(session: Session, slug: str) -> ClientOut:
    found = next((c for c in clients.list_clients(session) if c.slug == slug), None)
    if found is None:
        raise HTTPException(
            404, detail="That client isn't here any more. Go back to Clients & projects."
        )
    return _with_month(session, [found])[0]


@router.get("/clients")
def list_clients(session: SessionDep) -> list[ClientOut]:
    """Every client, with or without projects and connections."""
    return _with_month(session, clients.list_clients(session))


@router.post("/clients", status_code=201)
def create_client(data: ClientIn, session: WriteSessionDep) -> ClientOut:
    """A client named as typed (or the existing one with that name)."""
    slug = clients.ensure(session, data.name)
    if slug == clients.SHARED_OWNER:
        raise HTTPException(422, detail="'you' stands for your own tools; pick another name.")
    admin_audit.record(session, "client.create", f"added client {data.name}", by=OWNER)
    return _client_out(session, slug)


@router.put("/clients/{slug}/own")
def set_own_client(slug: str, data: OwnIn, session: WriteSessionDep) -> ClientOut:
    """Mark the operator's own client: general work goes to its default project."""
    client = clients.set_own(session, slug, data.own)
    said = f"marked {client.name} as your own" if data.own else f"unmarked {client.name}"
    admin_audit.record(session, "client.own", said, by=OWNER)
    return _client_out(session, client.slug)


@router.put("/clients/{slug}/default-project")
def set_default_project(slug: str, data: DefaultProjectIn, session: WriteSessionDep) -> ClientOut:
    """Where a request for this client goes when it needs no particular project."""
    try:
        client = clients.set_default_project(session, slug, data.project or None)
    except RuleViolationError as error:
        raise HTTPException(422, detail=str(error)) from None
    said = (
        f"set {client.name}'s default project to {data.project}"
        if data.project
        else f"cleared {client.name}'s default project"
    )
    admin_audit.record(session, "client.default_project", said, by=OWNER)
    return _client_out(session, client.slug)


@router.patch("/clients/{slug}")
def rename_client(slug: str, data: ClientIn, session: WriteSessionDep) -> ClientOut:
    """Change the name shown; the slug in URLs and IDs stays."""
    client = clients.rename(session, slug, data.name)
    admin_audit.record(
        session, "client.rename", f"renamed client {client.slug} to {client.name}", by=OWNER
    )
    return _client_out(session, client.slug)


@router.post("/clients/{slug}/move", dependencies=[RecentAuth])
def move_client(slug: str, data: MoveIn, session: WriteSessionDep) -> ClientOut:
    """Give a client's projects and connections to another client."""
    target = clients.get(session, data.to)
    moved = clients.move(session, slug, target.slug)
    admin_audit.record(
        session, "client.move", f"moved {moved} projects and connections from {slug} to "
        f"{target.slug}", by=OWNER,
    )  # fmt: skip
    return _client_out(session, target.slug)


@router.delete("/clients/{slug}", status_code=204, dependencies=[RecentAuth])
def delete_client(slug: str, session: WriteSessionDep) -> None:
    """Only a client with no projects and no connections (move or remove them first)."""
    clients.delete(session, slug)
    admin_audit.record(session, "client.delete", f"removed client {slug}", by=OWNER)


@router.put("/projects/{name}/connections/{instance}")
def bind_connection(
    name: str,
    instance: str,
    data: ConnectionBindingIn,
    request: Request,
    session: WriteSessionDep,
    token: OwnerToken,
) -> dict[str, Any]:
    if data.preapproved_writes:
        check_recent_auth(request, token)  # writes that run without an approval
    bindings.check_same_client(session, name, instance)
    binding, created = bindings.set_binding(
        session, BindingIn(project=name, instance=instance, **data.model_dump())
    )
    admin_audit.record(
        session, "binding.set",
        f"{'connected' if created else 'updated'} {instance}"
        f"{' as the default' if binding.is_default else ''}",
        by=OWNER, project=name, instance=instance,
    )  # fmt: skip
    return {
        "project": name, "instance": instance, "is_default": binding.is_default,
        "scope": binding.scope,
    }  # fmt: skip


@router.delete("/projects/{name}/connections/{instance}", status_code=204)
def unbind_connection(name: str, instance: str, session: WriteSessionDep) -> None:
    bindings.remove_binding(session, name, instance)
    admin_audit.record(
        session, "binding.remove", f"disconnected {instance}", by=OWNER, project=name,
        instance=instance,
    )  # fmt: skip


# --- Connections ---


class ConnectionIn(Input):
    name: Slug
    title: Title | None = None  # as shown ("n8n · n8n.geninfos.com")
    type: InstanceType
    env: Env
    owner: NonEmpty
    base_url: str | None = None
    secret: str = Field(repr=False)
    # Yours (a shared tool: any client's projects, or all of them), not a client's.
    shared: bool = False
    # All projects it can reach, new ones too: everyone's for a shared tool, otherwise
    # all of its client's.
    all_projects: bool = False


class ConnectionPatch(Input):
    title: Title | None = None
    # When its key expires ("2027-01-31"), if you know; empty: not known.
    expires_at: Annotated[str, Field(pattern=r"^(\d{4}-\d{2}-\d{2})?$")] | None = None
    env: Env | None = None
    owner: NonEmpty | None = None
    shared: bool | None = None  # yours (a shared tool), or a client's connection
    all_projects: bool | None = None  # every project it can reach gets it
    # ...except these clients' projects and these projects (only with all_projects)
    except_clients: CleanList | None = None
    except_projects: CleanList | None = None
    base_url: str | None = None
    # Its drafts wait for approval too (by default, saving a draft runs: it reaches nobody).
    hold_drafts: bool | None = None
    # Required with a new base_url: the key goes wherever the address points, so
    # an address can't be changed to send the stored key somewhere else.
    secret: str | None = Field(default=None, repr=False)


class ConnectionSecretIn(Input):
    secret: str = Field(repr=False)


class ConnectionOut(BaseModel):
    name: str
    title: str | None = None  # as shown; None: shown by its name
    type: InstanceType
    env: Env
    owner: str
    shared: bool = False  # a shared tool: any client's projects can use it
    all_projects: bool = False  # ...and every project gets it, new ones too
    except_clients: list[str] = []  # ...except these clients' projects
    except_projects: list[str] = []  # ...and these projects
    # "All projects except Acme (client), seaside-messages" ("" if not for all projects)
    reach: str = ""
    config: dict[str, Any]  # never secret (for an MCP server: a masked preview, read-only tools)
    base_url: str | None
    secret_stored: bool
    projects: list[str]
    # Each project's link: what it's narrowed to (empty: the whole connection).
    links: list[dict[str, Any]] = []
    # What a link can be narrowed by (ringkeeper.remote); empty: it can't.
    scope_fields: list[dict[str, Any]] = []
    last_test_at: Any
    last_test_ok: bool | None
    last_test_message: str | None
    # Another connection has the same name: what to call this one instead.
    suggested_title: str | None = None


def _scope_fields(instance: Instance) -> list[dict[str, Any]]:
    rules = remote.spec_for(instance.type.value, instance.config)
    fields = rules.fields if rules is not None else ()
    return [{"key": f.key, "label": f.label, "hint": f.hint, "many": f.many} for f in fields]


def _connection_out(instance: Instance) -> ConnectionOut:
    links = sorted(
        ({"project": b.project.name, "scope": dict(b.scope or {}), "is_default": b.is_default}
         for b in instance.bindings),
        key=lambda link: str(link["project"]),
    )  # fmt: skip
    return ConnectionOut(
        name=instance.name, title=instance.title, type=instance.type, env=instance.env,
        owner=instance.owner, shared=instance.shared, all_projects=instance.all_projects,
        except_clients=list(instance.except_clients),
        except_projects=list(instance.except_projects),
        reach=instances.describe_reach(instance),
        config=dict(instance.config or {}), base_url=instance.base_url,
        secret_stored=_stored(instance.secret_ref),
        projects=instances.bound_project_names(instance), links=links,
        scope_fields=_scope_fields(instance), last_test_at=instance.last_test_at,
        last_test_ok=instance.last_test_ok, last_test_message=instance.last_test_message,
        suggested_title=instances.suggested_title(instance),
    )  # fmt: skip


@router.get("/connections")
def list_connections(session: SessionDep) -> list[ConnectionOut]:
    rows = session.scalars(select(Instance).order_by(Instance.name)).all()
    found = [instances.get_instance(session, row.name) for row in rows]
    return [_connection_out(i) for i in found]


@router.post("/connections", status_code=201, dependencies=[RecentAuth])
def create_connection(data: ConnectionIn, session: WriteSessionDep) -> ConnectionOut:
    if data.type is InstanceType.N8N and not data.base_url:
        raise HTTPException(422, detail="Add your n8n address, like https://n8n.example.com.")
    if data.type is InstanceType.HTTP and not data.base_url:
        raise HTTPException(422, detail="Add the API's address, like https://api.example.com/v1.")
    base_url = _checked_url(data.base_url)
    if instances.find_instance(session, data.name) is not None:
        raise HTTPException(
            409, detail=f"There's already a connection named {data.name}. Pick another name."
        )
    config = _vendor_config(data.type, data.secret, data.env)
    try:
        ref = secrets_in.store_connection(
            session, get_settings(), data.name, data.type, data.secret
        )
    except RuleViolationError as error:
        raise HTTPException(422, detail=str(error)) from None
    instance = instances.create_instance(
        session,
        InstanceIn(
            name=data.name, title=data.title, type=data.type, env=data.env, secret_ref=ref,
            owner=instances.SHARED_OWNER if data.shared else data.owner, base_url=base_url,
            config=config, shared=data.shared, all_projects=data.all_projects,
        ),
    )  # fmt: skip
    admin_audit.record(
        session, "connection.create", f"added {data.type} connection {data.name} ({data.env})",
        by=OWNER, instance=data.name,
    )  # fmt: skip
    return _connection_out(instances.get_instance(session, instance.name))


def _vendor_config(kind: InstanceType, secret: str, env: Env) -> dict[str, Any]:
    """A business system: which one, and that the key reaches what the connection says."""
    if kind is not InstanceType.VENDOR:
        return {}
    try:
        vendor.check_env(secret, env)
        return vendor.config_of(secret)
    except ToolError as error:
        message = str(error)
        raise HTTPException(422, detail=f"{message[:1].upper()}{message[1:]}.") from None


def _with_config(instance: Instance, changes: dict[str, Any]) -> dict[str, Any]:
    config = dict(instance.config or {})
    if "expires_at" in changes:
        config["expires_at"] = changes["expires_at"] or None
    if changes.get("hold_drafts") is not None:
        config[HOLD_DRAFTS] = bool(changes["hold_drafts"])
    return config


@router.patch("/connections/{name}")
def update_connection(
    name: str, data: ConnectionPatch, request: Request, session: WriteSessionDep, token: OwnerToken
) -> ConnectionOut:
    instance = instances.get_instance(session, name)
    changes = data.model_dump(exclude_unset=True, exclude={"secret"})
    base_url = instance.base_url
    moved = "base_url" in changes and changes["base_url"] != instance.base_url
    widened = bool(changes.get("shared")) and not instance.shared  # other clients can link it
    if (
        moved
        or widened
        or (instance.env is Env.PROD and changes.get("env", Env.PROD) is not Env.PROD)
    ):
        check_recent_auth(request, token)  # new address, wider reach, or prod's rules dropped
    if moved:
        if not data.secret:
            raise HTTPException(
                422, detail="To change the address, paste the key or password again too."
            )
        base_url = _checked_url(changes["base_url"])
        try:
            secrets_in.store_connection(session, get_settings(), name, instance.type, data.secret)
        except RuleViolationError as error:
            raise HTTPException(422, detail=str(error)) from None
        instance.last_test_at = instance.last_test_ok = instance.last_test_message = None
    shared = changes.get("shared", instance.shared)
    owner = instances.SHARED_OWNER if shared else changes.get("owner", instance.owner)
    everywhere = changes.get("all_projects", instance.all_projects)
    left_out = changes.get("except_clients", instance.except_clients)
    merged = InstanceIn(
        name=instance.name, title=changes.get("title"), type=instance.type,
        env=changes.get("env", instance.env), secret_ref=instance.secret_ref, owner=owner,
        base_url=base_url, config=_with_config(instance, changes), shared=shared,
        all_projects=everywhere,
        # Exceptions only mean something for all projects; turning that off drops them.
        except_clients=left_out if everywhere and shared else [],
        except_projects=(
            changes.get("except_projects", instance.except_projects) if everywhere else []
        ),
    )  # fmt: skip
    try:
        instances.update_instance(session, merged)
    except RuleViolationError as error:
        raise HTTPException(422, detail=str(error)) from None
    admin_audit.record(
        session, "connection.update", f"changed {', '.join(sorted(changes)) or 'nothing'}",
        by=OWNER, instance=name, details={k: str(v) for k, v in changes.items()},
    )  # fmt: skip
    return _connection_out(instances.get_instance(session, name))


@router.put("/connections/{name}/secret", status_code=204, dependencies=[RecentAuth])
def replace_connection_secret(
    name: str, data: ConnectionSecretIn, session: WriteSessionDep
) -> None:
    instance = instances.get_instance(session, name)
    try:
        ref = secrets_in.store_connection(session, get_settings(), name, instance.type, data.secret)
    except RuleViolationError as error:
        raise HTTPException(422, detail=str(error)) from None
    if instance.secret_ref != ref:
        instance.secret_ref = ref
    instance.last_test_at = instance.last_test_ok = instance.last_test_message = None


@router.delete("/connections/{name}", status_code=204)
def delete_connection(
    name: str, session: WriteSessionDep, force: Annotated[bool, Field()] = False
) -> None:
    found = instances.get_instance(session, name)
    secret_ref, package = found.secret_ref, dict((found.config or {}).get("package") or {})
    kind = found.type
    unbound = instances.remove_instance(session, name, force=force)
    if kind is InstanceType.GOOGLE:  # tell Google this sign-in isn't used any more
        with contextlib.suppress(RingkeeperError):
            google_oauth.revoke(get_vault(get_settings()).resolve(secret_ref).reveal())
    secrets_in.forget(get_settings(), secret_ref)  # the client's secret leaves the vault too
    if package.get("pinned"):  # a local server from the catalog: its installed copy goes too
        local_servers.remove_folder(
            get_settings(), str(package["pinned"]), str(package["registry"])
        )
    admin_audit.record(
        session, "connection.delete",
        f"removed {name}" + (f" (unbound from {', '.join(unbound)})" if unbound else ""),
        by=OWNER, instance=name,
    )  # fmt: skip


def _checked_url(url: str | None) -> str | None:
    if not url:
        return None
    try:
        return netpolicy.check_url(url)
    except netpolicy.AddressNotAllowedError as error:
        raise HTTPException(422, detail=str(error)) from None


@router.post("/connections/{name}/test")
def test_connection(name: str) -> dict[str, Any]:
    result = probes.test_connection(name, by=OWNER)
    return {"ok": result.ok, "message": result.message}


@router.get("/connections/{name}/tools")
def connection_tools(name: str, session: SessionDep) -> dict[str, Any]:
    """An MCP server's tools, asked of the server itself (it may take a while to start)."""
    instance = instances.get_instance(session, name)
    if instance.type is not InstanceType.MCP:
        raise HTTPException(409, detail="Only MCP server connections list their tools here.")
    session.close()  # no transaction while the server starts
    try:
        return mcp_tools.refresh(name)
    except (ToolError, TimeoutError) as error:
        raise HTTPException(502, detail=scrub(str(error))) from None


@router.get("/connections/{name}/tool-list")
def connection_tool_list(name: str) -> dict[str, Any]:
    """Every tool the connection offers, to copy or save: no token, key or client data. An MCP
    server is asked now, so it may take a while to start."""
    try:
        return connection_export.tool_list(name)
    except (ToolError, TimeoutError) as error:
        raise HTTPException(502, detail=scrub(str(error))) from None


@router.get("/connections/{name}/support")
def connection_support(name: str, session: SessionDep) -> dict[str, Any]:
    """What support needs to know about a connection, with no secret and no client data."""
    return connection_export.support_details(session, name)


class ToolRulesIn(Input):
    # Tool name -> "read", "approve" or "confirm"; leave a tool out for its default.
    rules: dict[
        Annotated[str, Field(min_length=1, max_length=128)], Literal["read", "approve", "confirm"]
    ]


@router.put("/connections/{name}/tool-rules", dependencies=[RecentAuth])
def set_tool_rules(name: str, data: ToolRulesIn, session: WriteSessionDep) -> dict[str, Any]:
    """What a call to each of an MCP server's tools needs. It can loosen approvals, so
    it needs a recent confirmation like other such changes."""
    if instances.get_instance(session, name).type is not InstanceType.MCP:
        raise HTTPException(409, detail="Only MCP server connections have tool rules.")
    return {"tool_rules": mcp_tools.set_rules(session, name, dict(data.rules), by=OWNER)}


@router.get("/projects/{name}/connections")
def project_connections(name: str, session: SessionDep, token: OwnerToken) -> list[dict[str, Any]]:
    del token
    project = projects.get_project(session, name)
    rows: list[ProjectInstance] = bindings.sorted_bindings(project)
    return [
        {"instance": b.instance.name, "title": b.instance.title, "type": b.instance_type,
         "env": b.instance_env, "is_default": b.is_default, "scope": dict(b.scope or {})}
        for b in rows
    ]  # fmt: skip
