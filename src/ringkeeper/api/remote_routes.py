"""The catalog and connecting what's in it: the Recommended shelf (services with
built-in rules), the MCP Registry search, a remote server signed in to or given
a token or header, a server added by its address, a local server installed
after one confirmation, an extension installed from its file (.mcpb) after one
confirmation, and the sign-in callback.

The callback has no session to check: the browser arrives there from the
service's own page. It is protected by the one-time `state` instead, which
only the sign-in this process started knows (services.mcp_oauth).
"""

import contextlib
import hashlib
import json
import tempfile
from html import escape
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import Field

from ringkeeper.adapters import mcp
from ringkeeper.api.auth import AdminDep
from ringkeeper.api.deps import RecentAuth, SessionDep, WriteSessionDep, require_owner
from ringkeeper.config import Settings, get_settings
from ringkeeper.db import session_scope
from ringkeeper.errors import RuleViolationError
from ringkeeper.models import Env, InstanceType
from ringkeeper.schemas import Input, InstanceIn, NonEmpty, ScopeValue, Slug
from ringkeeper.services import (
    admin_audit,
    bundles,
    catalog,
    instances,
    local_servers,
    mcp_oauth,
    remote_connections,
    secrets_in,
)
from ringkeeper.services.remote_connections import NewConnection

router = APIRouter(dependencies=[AdminDep, Depends(require_owner)])
callback = APIRouter()

CALLBACK_PATH = "/oauth/callback"


Url = Annotated[str, Field(min_length=8, max_length=2000)]
ServerName = Annotated[str, Field(min_length=3, max_length=200)]
Values = dict[Annotated[str, Field(max_length=100)], Annotated[str, Field(max_length=4000)]]


class RemoteConnectionIn(Input):
    remote: Annotated[str, Field(min_length=1, max_length=40)] | None = None
    url: Url | None = None
    server: ServerName | None = None
    name: Slug
    owner: NonEmpty
    env: Env = Env.DEV
    project: Slug | None = None
    scope: dict[Slug, ScopeValue] = Field(default_factory=dict[str, str])
    # A shared tool (no client's data): any client's project, or all of them.
    shared: bool = False
    all_projects: bool = False

    def new(self) -> NewConnection:
        return NewConnection(
            remote=self.remote, url=self.url, server=self.server, name=self.name,
            owner=instances.SHARED_OWNER if self.shared else self.owner, env=self.env,
            project=self.project, scope=dict(self.scope) or None, shared=self.shared,
            all_projects=self.all_projects,
        )  # fmt: skip


class TokenConnectionIn(RemoteConnectionIn):
    token: str | None = Field(default=None, repr=False, max_length=4000)
    headers: Values = Field(default_factory=dict[str, str], repr=False)
    open: bool = False  # the server needs no token, header or sign-in


class ProbeIn(Input):
    url: Url


class PlanIn(Input):
    server: ServerName
    package: Annotated[int, Field(ge=0, le=50)] = 0


class LocalConnectionIn(PlanIn):
    fingerprint: Annotated[str, Field(min_length=8, max_length=64)]
    name: Slug
    owner: NonEmpty
    env: Env = Env.DEV
    project: Slug | None = None
    values: Values = Field(default_factory=dict[str, str], repr=False)
    replace: Slug | None = None  # update this connection to the planned version instead
    # A shared tool (no client's data): any client's project, or all of them.
    shared: bool = False
    all_projects: bool = False


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
    """The Recommended shelf: services with built-in rules."""
    return remote_connections.catalog_shelf()


@router.get("/catalog")
def search_catalog(q: str = "", cursor: str | None = None) -> dict[str, Any]:
    """MCP servers from the registry (or, offline, from what was seen before)."""
    return catalog.search(get_settings(), q[:200], cursor)


@router.get("/catalog/servers/{name:path}")
def catalog_server(name: str) -> dict[str, Any]:
    try:
        return catalog.get(get_settings(), name)
    except KeyError:
        raise HTTPException(404, detail="That server isn't in the catalog.") from None


@router.post("/catalog/probe")
def probe_url(data: ProbeIn) -> dict[str, Any]:
    """For adding a server: is it a known service, does sign-in work, does it need nothing?"""
    new = remote_connections.resolve(
        NewConnection(name="probe", owner="probe", env=Env.DEV, url=data.url)
    )
    known = remote_connections.service(new.remote) if new.remote else None
    signin = catalog.probe(new.url or "") if known is None or known.signin else False
    open_ = known is None and mcp_oauth.needs_nothing(new.url or "")
    return {"url": new.url, "recommended": new.remote, "signin": signin, "open": open_}


@router.post("/connections/signin", dependencies=[RecentAuth])
def start_signin(data: RemoteConnectionIn, request: Request, session: SessionDep) -> dict[str, str]:
    """The server's sign-in page to open; the connection is added once it sends you back."""
    new = remote_connections.check(session, data.new())
    remote_connections.can_sign_in(new)
    settings = get_settings()
    try:
        url = mcp_oauth.start(new.url or "", redirect_uri(request, settings), new.as_dict())
    except mcp_oauth.SignInError as error:
        raise HTTPException(502, detail=str(error)) from None
    return {"url": url}


@router.post("/connections/remote", status_code=201, dependencies=[RecentAuth])
def add_with_token(data: TokenConnectionIn, session: WriteSessionDep) -> dict[str, Any]:
    """A remote server with a pasted token (Authorization: Bearer), the headers it needs, or
    (open) nothing at all."""
    new = data.new()
    if data.open:
        secret, how = remote_connections.open_secret(new), "open"
    elif data.headers:
        secret, how = remote_connections.headers_secret(new, dict(data.headers)), "headers"
    else:
        secret, how = remote_connections.token_secret(new, data.token or ""), "token"
    instance = remote_connections.create(session, get_settings(), new, secret, how=how)
    return {"name": instance.name}


# --- Local servers: one confirmation, then an install in the background ---


def _plan(server: str, package: int) -> local_servers.Plan:
    try:
        summary = catalog.get(get_settings(), server)
    except KeyError:
        raise HTTPException(404, detail="That server isn't in the catalog.") from None
    return local_servers.plan(summary, package)


@router.post("/catalog/plan")
def plan_local(data: PlanIn) -> dict[str, Any]:
    """What installing it means: publisher, package and version, commands, what's needed."""
    return _plan(data.server, data.package).as_dict()


@router.post("/connections/local", status_code=202, dependencies=[RecentAuth])
def install_local(data: LocalConnectionIn, session: SessionDep) -> dict[str, Any]:
    plan = _plan(data.server, data.package)
    if data.fingerprint != plan.fingerprint:
        raise HTTPException(409, detail="The package changed since you confirmed. Look again.")
    new = NewConnection(
        name=data.replace or data.name, env=data.env, server=data.server,
        owner=instances.SHARED_OWNER if data.shared else data.owner,
        project=data.project, shared=data.shared, all_projects=data.all_projects,
    )  # fmt: skip
    if data.replace:
        current = instances.get_instance(session, data.replace)
        if current.type is not InstanceType.MCP or current.config.get("registry") != data.server:
            raise RuleViolationError(f"'{data.replace}' isn't a connection to {data.server}.")
    else:
        remote_connections.check_new(session, new)
    settings = get_settings()
    values = dict(data.values)
    local_servers.environment(plan, values)  # anything required that's missing, before starting

    def work() -> None:
        spec = local_servers.install(settings, plan, values)
        _store_local(new, plan, spec, replace=bool(data.replace))

    local_servers.start(new.name, work)
    return {"name": new.name, "state": "installing"}


def _store_local(
    new: NewConnection, plan: local_servers.Plan, spec: dict[str, Any], *, replace: bool
) -> None:
    settings = get_settings()
    secret = json.dumps({k: spec[k] for k in ("command", "args", "env")})
    package = {
        "registry": plan.registry, "identifier": plan.identifier, "version": plan.version,
        "pinned": spec["pinned"],
    }  # fmt: skip
    config = {
        "registry": plan.server, "package": package, "source": "catalog",
        "publisher": plan.publisher, "preview": mcp.preview(spec),
    }  # fmt: skip
    with session_scope() as session:
        if not replace:
            ref = secrets_in.store_connection(session, settings, new.name, InstanceType.MCP, secret)
            instances.create_instance(
                session,
                InstanceIn(
                    name=new.name, type=InstanceType.MCP, env=new.env, secret_ref=ref,
                    owner=new.owner, config=config, shared=new.shared,
                    all_projects=new.all_projects,
                ),
            )  # fmt: skip
            admin_audit.record(
                session, "connection.create",
                f"installed {plan.identifier} {plan.version} ({plan.registry}) as {new.name}"
                f" ({new.env})",
                by="owner", instance=new.name,
            )  # fmt: skip
            remote_connections.link(session, new)
            return
        instance = instances.get_instance(session, new.name)
        old = dict(instance.config.get("package") or {})
        secrets_in.store_connection(session, settings, new.name, InstanceType.MCP, secret)
        instance.config = {**instance.config, **config}
        admin_audit.record(
            session, "connection.update",
            f"{new.name}: {plan.identifier} {old.get('version')} -> {plan.version}",
            by="owner", instance=new.name,
        )  # fmt: skip
    if old.get("pinned") and old.get("pinned") != package["pinned"]:
        local_servers.remove_folder(settings, str(old["pinned"]), str(old.get("registry")))


# --- Extensions from a file (.mcpb): the manifest to confirm, then the same kind of install ---


@router.post("/catalog/extensions")
async def read_extension(
    request: Request, content_length: Annotated[int | None, Header()] = None
) -> dict[str, Any]:
    """Keep the uploaded file (under its SHA-256) and show what's in its manifest."""
    too_big = f"That file is too big; the limit is {bundles.MAX_BYTES // 1_000_000} MB."
    if content_length is not None and content_length > bundles.MAX_BYTES:
        raise HTTPException(413, detail=too_big)
    settings = get_settings()
    folder = bundles.uploads(settings)
    folder.mkdir(parents=True, exist_ok=True)
    digest, size = hashlib.sha256(), 0
    with tempfile.NamedTemporaryFile(dir=folder, suffix=".part", delete=False) as out:
        received = Path(out.name)
        try:
            async for chunk in request.stream():
                size += len(chunk)
                if size > bundles.MAX_BYTES:
                    raise HTTPException(413, detail=too_big)
                digest.update(chunk)
                out.write(chunk)
        except BaseException:
            out.close()
            received.unlink(missing_ok=True)
            raise
    if not size:
        received.unlink(missing_ok=True)
        raise HTTPException(422, detail="The file is empty.")
    path = bundles.keep(settings, received, digest.hexdigest())
    try:
        return bundles.read(path, digest.hexdigest()).as_dict()
    except bundles.BundleError:
        path.unlink(missing_ok=True)
        raise


class ExtensionIn(Input):
    fingerprint: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    name: Slug
    owner: NonEmpty
    project: Slug | None = None
    values: Values = Field(default_factory=dict[str, str], repr=False)
    # A shared tool (no client's data): any client's project, or all of them.
    shared: bool = False
    all_projects: bool = False


@router.post("/connections/extension", status_code=202, dependencies=[RecentAuth])
def install_extension(data: ExtensionIn, session: SessionDep) -> dict[str, Any]:
    """Install the extension that was shown (named by its SHA-256) and connect it."""
    settings = get_settings()
    path = bundles.staged(settings, data.fingerprint)
    if not path.is_file():
        raise HTTPException(409, detail="Open the extension file again: it isn't here anymore.")
    bundle = bundles.read(path, data.fingerprint)
    if bundle.problem:
        raise RuleViolationError(f"{bundle.title} can't run here: {bundle.problem}.")
    # As an extension imported from Claude Desktop: Test data, and its calls wait for
    # approval by the generic tool rules (adapters.mcp). It can be set to Production.
    new = NewConnection(
        name=data.name, owner=instances.SHARED_OWNER if data.shared else data.owner,
        env=Env.DEV, project=data.project, shared=data.shared, all_projects=data.all_projects,
    )  # fmt: skip
    remote_connections.check_new(session, new)
    values = dict(data.values)
    bundles.values_for(bundle, values)  # anything required that's missing, before starting

    def work() -> None:
        spec = bundles.install(settings, bundle, path, values)
        _store_extension(new, bundle, spec)

    local_servers.start(new.name, work)
    return {"name": new.name, "state": "installing"}


def _store_extension(new: NewConnection, bundle: bundles.Bundle, spec: dict[str, Any]) -> None:
    settings = get_settings()
    secret = json.dumps({k: spec[k] for k in ("command", "args", "env")})
    package = {
        "registry": bundles.REGISTRY, "identifier": bundle.name, "version": bundle.version,
        "pinned": spec["pinned"],
    }  # fmt: skip
    config = {
        "source": bundles.REGISTRY, "extension": bundle.title, "package": package,
        "publisher": bundle.publisher, "preview": spec["preview"],
    }  # fmt: skip
    with session_scope() as session:
        ref = secrets_in.store_connection(session, settings, new.name, InstanceType.MCP, secret)
        instances.create_instance(
            session,
            InstanceIn(
                name=new.name, type=InstanceType.MCP, env=new.env, secret_ref=ref,
                owner=new.owner, config=config, shared=new.shared, all_projects=new.all_projects,
            ),
        )  # fmt: skip
        admin_audit.record(
            session, "connection.create",
            f"installed the extension {bundle.title} {bundle.version} as {new.name} ({new.env})",
            by="owner", instance=new.name,
        )  # fmt: skip
        remote_connections.link(session, new)


@router.get("/connections/local/{name}")
def install_status(name: Slug) -> dict[str, Any]:
    found = local_servers.job(name)
    if found is None:
        raise HTTPException(404, detail="No install of that name.")
    return {"name": name, "state": found.state, "error": found.error}


@router.get("/catalog/updates")
def catalog_updates(session: SessionDep) -> list[dict[str, Any]]:
    """Installed local servers with a newer version in the catalog (never installed by itself)."""
    latest = catalog.latest_versions(get_settings())
    found: list[dict[str, Any]] = []
    for instance in instances.list_instances(session):
        config = instance.config or {}
        package = dict(config.get("package") or {})
        newer = latest.get(str(config.get("registry") or ""))
        if package and newer and newer != package.get("version"):
            found.append(
                {
                    "connection": instance.name, "server": config["registry"],
                    "installed": package.get("version"), "latest": newer,
                }
            )  # fmt: skip
    return found


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
