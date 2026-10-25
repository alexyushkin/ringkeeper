"""What the owner manages in the web app: settings, secrets, projects, connections.

Every endpoint needs a token and a signed-in owner. Writes go through the
services layer and are audited (outcome `changed`). Secret values are
write-only: no response, log line or audit row ever contains one.

Changes that store a secret, redirect approvals or loosen them also need a
recent confirmation (deps.check_recent_auth): a stolen session cookie alone
can't point Telegram approvals at another chat or send a key elsewhere.
"""

import re
from typing import Annotated, Any

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Request,
)
from pydantic import BaseModel, Field
from sqlalchemy import select

from ringkeeper import netpolicy
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
from ringkeeper.models import AuthMode, Env, Instance, InstanceType, ProjectInstance
from ringkeeper.schemas import (
    BindingIn,
    CleanList,
    Input,
    InstanceIn,
    ModelId,
    ModelKey,
    NonEmpty,
    ProjectIn,
    Slug,
)
from ringkeeper.services import (
    admin_audit,
    bindings,
    instances,
    probes,
    projects,
    runtime,
    secrets_in,
    tasks,
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
            "mode": settings.claude_auth_mode.value,
            "api_key": _stored(credential_ref(settings, AuthMode.API_KEY)),
            "subscription": _stored(credential_ref(settings, AuthMode.SUBSCRIPTION)),
        },
        telegram={"token": _stored(settings.telegram_bot_token_ref)},
    )


@router.get("/settings")
def get_settings_view(request: Request, session: SessionDep) -> SettingsOut:
    return _settings_out(request, session)


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


class TelegramSecretIn(Input):
    token: str = Field(repr=False)


@router.put("/secrets/claude", dependencies=[RecentAuth])
def put_claude_secret(data: ClaudeSecretIn, session: WriteSessionDep) -> dict[str, Any]:
    settings = current_settings()
    try:
        stored = secrets_in.store_claude(session, settings, data.mode, data.value)
    except ClaudeAuthError as error:
        raise HTTPException(422, detail=str(error)) from None
    except RuleViolationError as error:
        raise HTTPException(422, detail=str(error)) from None
    except secrets_in.CheckUnavailableError as error:
        raise _unavailable(error) from None
    if settings.claude_auth_mode is not data.mode:
        runtime.update(session, {"claude_auth_mode": data.mode.value}, by=OWNER)
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
    owner: NonEmpty | None = None
    description: str | None = None
    aliases: CleanList | None = None
    repo_url: str | None = None
    workspace_path: str | None = None
    autonomy: str | None = None
    checks: CleanList | None = None
    models: dict[ModelKey, ModelId] | None = None


class ConnectionBindingIn(Input):
    is_default: bool = False
    allowed_tools: CleanList = Field(default_factory=list[str])
    preapproved_writes: CleanList = Field(default_factory=list[str])


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
def create_project(data: ProjectIn, session: WriteSessionDep) -> dict[str, str]:
    _check_workspace(data.workspace_path)
    project = projects.create_project(session, data)
    admin_audit.record(
        session, "project.create", f"created project {project.name}", by=OWNER,
        project=project.name,
    )  # fmt: skip
    return {"name": project.name}


@router.patch("/projects/{name}")
def update_project(
    name: str, data: ProjectPatch, request: Request, session: WriteSessionDep, token: OwnerToken
) -> dict[str, str]:
    project = projects.get_project(session, name)
    if data.autonomy is not None and data.autonomy != project.autonomy:
        check_recent_auth(request, token)  # autonomy decides what runs without asking
    if data.workspace_path != project.workspace_path:
        _check_workspace(data.workspace_path)
    current = ProjectIn.model_validate(
        {
            "name": project.name, "owner": project.owner, "description": project.description,
            "aliases": project.aliases, "repo_url": project.repo_url,
            "workspace_path": project.workspace_path, "autonomy": project.autonomy,
            "checks": project.checks, "models": project.models,
        }
    )  # fmt: skip
    changes = data.model_dump(exclude_unset=True)
    merged = ProjectIn.model_validate({**current.model_dump(), **changes})
    projects.update_project(session, merged)
    admin_audit.record(
        session, "project.update", f"changed {', '.join(sorted(changes)) or 'nothing'}",
        by=OWNER, project=name, details={"fields": sorted(changes)},
    )  # fmt: skip
    return {"name": name}


@router.delete("/projects/{name}", status_code=204)
def delete_project(name: str, session: WriteSessionDep) -> None:
    removed = projects.remove_project(session, name)
    admin_audit.record(
        session, "project.delete", f"removed project {name} ({removed} connections unbound)",
        by=OWNER, project=name,
    )  # fmt: skip


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
    binding, created = bindings.set_binding(
        session, BindingIn(project=name, instance=instance, **data.model_dump())
    )
    admin_audit.record(
        session, "binding.set",
        f"{'connected' if created else 'updated'} {instance}"
        f"{' as the default' if binding.is_default else ''}",
        by=OWNER, project=name, instance=instance,
    )  # fmt: skip
    return {"project": name, "instance": instance, "is_default": binding.is_default}


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
    type: InstanceType
    env: Env
    owner: NonEmpty
    base_url: str | None = None
    secret: str = Field(repr=False)


class ConnectionPatch(Input):
    env: Env | None = None
    owner: NonEmpty | None = None
    base_url: str | None = None
    # Required with a new base_url: the key goes wherever the address points, so
    # an address can't be changed to send the stored key somewhere else.
    secret: str | None = Field(default=None, repr=False)


class ConnectionSecretIn(Input):
    secret: str = Field(repr=False)


class ConnectionOut(BaseModel):
    name: str
    type: InstanceType
    env: Env
    owner: str
    base_url: str | None
    secret_stored: bool
    projects: list[str]
    last_test_at: Any
    last_test_ok: bool | None
    last_test_message: str | None


def _connection_out(instance: Instance) -> ConnectionOut:
    return ConnectionOut(
        name=instance.name, type=instance.type, env=instance.env, owner=instance.owner,
        base_url=instance.base_url, secret_stored=_stored(instance.secret_ref),
        projects=instances.bound_project_names(instance), last_test_at=instance.last_test_at,
        last_test_ok=instance.last_test_ok, last_test_message=instance.last_test_message,
    )  # fmt: skip


@router.get("/connections")
def list_connections(session: SessionDep) -> list[ConnectionOut]:
    rows = session.scalars(select(Instance).order_by(Instance.name)).all()
    found = [instances.get_instance(session, row.name) for row in rows]
    return [_connection_out(i) for i in found]


@router.post("/connections", status_code=201, dependencies=[RecentAuth])
def create_connection(data: ConnectionIn, session: WriteSessionDep) -> ConnectionOut:
    if data.type is InstanceType.N8N and not data.base_url:
        raise HTTPException(422, detail="n8n needs its address (base URL)")
    base_url = _checked_url(data.base_url)
    if instances.find_instance(session, data.name) is not None:
        raise HTTPException(409, detail=f"a connection named '{data.name}' already exists")
    try:
        ref = secrets_in.store_connection(
            session, get_settings(), data.name, data.type, data.secret
        )
    except RuleViolationError as error:
        raise HTTPException(422, detail=str(error)) from None
    instance = instances.create_instance(
        session,
        InstanceIn(
            name=data.name, type=data.type, env=data.env, secret_ref=ref, owner=data.owner,
            base_url=base_url,
        ),
    )  # fmt: skip
    admin_audit.record(
        session, "connection.create", f"added {data.type} connection {data.name} ({data.env})",
        by=OWNER, instance=data.name,
    )  # fmt: skip
    return _connection_out(instances.get_instance(session, instance.name))


@router.patch("/connections/{name}")
def update_connection(
    name: str, data: ConnectionPatch, request: Request, session: WriteSessionDep, token: OwnerToken
) -> ConnectionOut:
    instance = instances.get_instance(session, name)
    changes = data.model_dump(exclude_unset=True, exclude={"secret"})
    base_url = instance.base_url
    moved = "base_url" in changes and changes["base_url"] != instance.base_url
    if moved or (instance.env is Env.PROD and changes.get("env", Env.PROD) is not Env.PROD):
        check_recent_auth(request, token)  # a new address, or production's rules dropped
    if moved:
        if not data.secret:
            raise HTTPException(
                422, detail="Changing the address needs the secret again, in the same request."
            )
        base_url = _checked_url(changes["base_url"])
        try:
            secrets_in.store_connection(session, get_settings(), name, instance.type, data.secret)
        except RuleViolationError as error:
            raise HTTPException(422, detail=str(error)) from None
        instance.last_test_at = instance.last_test_ok = instance.last_test_message = None
    merged = InstanceIn(
        name=instance.name, type=instance.type, env=changes.get("env", instance.env),
        secret_ref=instance.secret_ref, owner=changes.get("owner", instance.owner),
        base_url=base_url,
    )  # fmt: skip
    instances.update_instance(session, merged)
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
    secret_ref = instances.get_instance(session, name).secret_ref
    unbound = instances.remove_instance(session, name, force=force)
    secrets_in.forget(get_settings(), secret_ref)  # the client's secret leaves the vault too
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
def test_connection(name: str, session: WriteSessionDep) -> dict[str, Any]:
    result = probes.test_connection(session, name, by=OWNER)
    return {"ok": result.ok, "message": result.message}


@router.get("/projects/{name}/connections")
def project_connections(name: str, session: SessionDep, token: OwnerToken) -> list[dict[str, Any]]:
    del token
    project = projects.get_project(session, name)
    rows: list[ProjectInstance] = bindings.sorted_bindings(project)
    return [
        {"instance": b.instance.name, "type": b.instance_type, "env": b.instance_env,
         "is_default": b.is_default}
        for b in rows
    ]  # fmt: skip
