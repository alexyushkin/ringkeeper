"""The conveniences: files on a request, shortcuts, Run again, trusting an action, the
digest, what's pinned and recent, and offering a project's own keys as connections."""

import json
import threading
import uuid
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import unquote

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ringkeeper.adapters import vendor
from ringkeeper.adapters.base import ToolError
from ringkeeper.api.auth import AdminDep
from ringkeeper.api.deps import RecentAuth, SessionDep, WriteSessionDep, require_owner
from ringkeeper.config import get_settings
from ringkeeper.errors import RingkeeperError, RuleViolationError
from ringkeeper.models import Env, InstanceType, Project, Setting, Shortcut
from ringkeeper.schemas import BindingIn, Input, InstanceIn, NonEmpty, Slug, Title
from ringkeeper.services import (
    admin_audit,
    attachments,
    bindings,
    cli_logins,
    decisions,
    digest,
    env_scan,
    health,
    instances,
    project_servers,
    projects,
    remote_connections,
    secrets_in,
    tasks,
    trusts,
)
from ringkeeper.services import help as help_answers
from ringkeeper.services.claude_desktop import missing_command
from ringkeeper.services.runtime import current_settings
from ringkeeper.vault import get_vault

router = APIRouter(dependencies=[AdminDep, Depends(require_owner)])
OWNER = "owner"
PREFS_KEY = "ui.prefs"
SKIPPED_KEY = "scan.skipped"
LOGINS_SKIPPED_KEY = "logins_skipped"


# --- Files on a request ---


@router.post("/attachments")
async def add_attachment(
    request: Request,
    file_name: Annotated[str, Header(alias="x-file-name")] = "file",
    content_length: Annotated[int | None, Header()] = None,
) -> dict[str, str]:
    """One file, as the request's body: kept until a task takes it into its project."""
    if content_length is not None and content_length > attachments.MAX_BYTES:
        raise HTTPException(413, detail="That file is over 50 MB.")
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > attachments.MAX_BYTES:
            raise HTTPException(413, detail="That file is over 50 MB.")
        chunks.append(chunk)
    ident = attachments.save(current_settings(), unquote(file_name), b"".join(chunks))
    return {"id": ident, "name": attachments.name_of(ident)}


# --- Run again, and shortcuts ---


@router.post("/tasks/{task_id}/again", status_code=201)
def run_again(task_id: str, session: WriteSessionDep) -> dict[str, str]:
    """The same request as a new task, in the same conversation."""
    old = tasks.get_task(session, task_id)
    made = tasks.create_task(
        session, get_settings(), project=old.project, prompt=old.prompt, kind=old.kind,
        risk=old.risk, complexity=old.complexity, outcome=old.outcome, thread_id=old.thread_id,
        agent_id=old.agent_id,
    )  # fmt: skip
    admin_audit.record(
        session, "task.again", f"ran again: {(old.outcome or old.prompt)[:80]}", by=OWNER,
        project=old.project,
    )  # fmt: skip
    return {"id": str(made.id)}


class ShortcutIn(Input):
    project: Slug
    label: Annotated[str, Field(min_length=1, max_length=80)]
    prompt: Annotated[str, Field(min_length=1, max_length=8000)]


def _shortcut(row: Shortcut) -> dict[str, Any]:
    return {
        "id": str(row.id), "project": row.project.name, "client": row.project.owner,
        "label": row.label, "prompt": row.prompt,
    }  # fmt: skip


@router.get("/shortcuts")
def list_shortcuts(session: SessionDep) -> list[dict[str, Any]]:
    return [_shortcut(r) for r in session.scalars(select(Shortcut).order_by(Shortcut.label))]


@router.post("/shortcuts", status_code=201)
def add_shortcut(data: ShortcutIn, session: WriteSessionDep) -> dict[str, Any]:
    project = projects.get_project(session, data.project)
    row = Shortcut(project_id=project.id, label=data.label.strip(), prompt=data.prompt.strip())
    session.add(row)
    session.flush()
    return _shortcut(row)


@router.delete("/shortcuts/{shortcut_id}", status_code=204)
def remove_shortcut(shortcut_id: uuid.UUID, session: WriteSessionDep) -> None:
    row = session.get(Shortcut, shortcut_id)
    if row is not None:
        session.delete(row)


# --- Trusting an action for an hour ---


class TrustIn(Input):
    decision: NonEmpty


def _trust(row: Any) -> dict[str, Any]:
    return {
        "id": str(row.id), "project": row.project, "instance": row.instance, "tool": row.tool,
        "expires_at": row.expires_at,
    }  # fmt: skip


@router.get("/trusts")
def list_trusts(session: SessionDep) -> list[dict[str, Any]]:
    return [_trust(t) for t in trusts.active(session)]


@router.post("/trusts", status_code=201, dependencies=[RecentAuth])
def add_trust(data: TrustIn, session: WriteSessionDep) -> dict[str, Any]:
    return _trust(trusts.grant(session, decisions.get_decision(session, data.decision), by=OWNER))


@router.delete("/trusts/{trust_id}", status_code=204)
def stop_trusting(trust_id: str, session: WriteSessionDep) -> None:
    trusts.revoke(session, trust_id, by=OWNER)


# --- The digest ---


@router.get("/digest")
def show_digest(session: SessionDep) -> dict[str, Any]:
    return digest.build(session)


# --- Help > Ask ---


class AskIn(Input):
    question: Annotated[str, Field(min_length=2, max_length=500)]


@router.post("/help/ask")
def ask_help(data: AskIn, session: SessionDep) -> dict[str, Any]:
    """A how-to answered from the guide and your setup: no project, no task, no approval."""
    from ringkeeper.router.model import AgentSdkModel  # noqa: PLC0415 (Agent SDK: only here)

    settings = current_settings()
    model = AgentSdkModel(get_vault(settings), settings)
    try:
        return help_answers.answer(session, settings, model, data.question).as_dict()
    except RingkeeperError as error:
        raise HTTPException(503, detail=f"Help couldn't answer just now: {error}") from None


# --- Testing every connection now ---


@router.post("/connections/test-all", status_code=202)
def test_all() -> dict[str, str]:
    """The daily check, now, in the background."""
    settings = current_settings()
    threading.Thread(target=health.check_all, args=(settings,), daemon=True).start()
    return {"state": "started"}


# --- What's pinned and recent (the switcher, Ctrl+K) ---


Key = Annotated[str, Field(max_length=200)]


class PrefsIn(Input):
    pins: Annotated[list[Key], Field(max_length=50)] = Field(default_factory=list[Key])
    recents: Annotated[list[Key], Field(max_length=20)] = Field(default_factory=list[Key])


@router.get("/ui-prefs")
def get_prefs(session: SessionDep) -> dict[str, Any]:
    row = session.get(Setting, PREFS_KEY)
    return dict(row.value) if row else {"pins": [], "recents": []}


@router.put("/ui-prefs")
def put_prefs(data: PrefsIn, session: WriteSessionDep) -> dict[str, Any]:
    value = data.model_dump()
    row = session.get(Setting, PREFS_KEY)
    if row is None:
        session.add(Setting(key=PREFS_KEY, value=value))
    else:
        row.value = value
    return value


# --- A project's own keys, offered as connections ---


def _folder(project: Project) -> Path:
    from ringkeeper.workers.manager import resolve_workspace  # noqa: PLC0415 (cycle)

    return Path(resolve_workspace(project.workspace_path, get_settings().workspaces_root))


def _skipped(session: Session, project: str) -> list[str]:
    row = session.get(Setting, SKIPPED_KEY)
    return list(row.value.get(project, [])) if row else []


@router.get("/projects/{name}/scan")
def scan_project(name: str, session: SessionDep) -> dict[str, Any]:
    """What this project's own files hold. Values stay here: only masked ones go out."""
    project = projects.get_project(session, name)
    try:
        folder = _folder(project)
    except (OSError, RuleViolationError):
        return {"folder": None, "found": []}
    skipped = _skipped(session, name)
    titles = {i.title for i in instances.list_instances(session)}
    found = [f for f, _, _ in project_servers.everything(folder) if f.id not in skipped]
    return {
        "folder": str(folder),
        "found": [
            {**vars(f), "added": f.title in titles and f.usable} for f in found
        ],
    }  # fmt: skip


class ScanConnectIn(Input):
    id: Annotated[str, Field(min_length=8, max_length=32)]
    env: Env
    name: Slug
    title: Title


@router.post("/projects/{name}/scan/connect", status_code=201, dependencies=[RecentAuth])
def connect_found(name: str, data: ScanConnectIn, session: WriteSessionDep) -> dict[str, str]:
    """Connect one finding, confirmed: its value is read again from the project's file."""
    project = projects.get_project(session, name)
    found = project_servers.everything(_folder(project))
    match = next((m for m in found if m[0].id == data.id and m[0].usable), None)
    if match is None:
        raise HTTPException(409, detail="That key isn't in the project's files any more.")
    finding, secret, address = match
    kind = InstanceType(finding.service)
    if kind is InstanceType.VENDOR:
        try:
            vendor.check_env(secret, data.env)
        except ToolError as error:
            raise HTTPException(422, detail=f"{str(error).capitalize()}.") from None
    ref = secrets_in.store_connection(session, get_settings(), data.name, kind, secret)
    instances.create_instance(
        session,
        InstanceIn(
            name=data.name, title=data.title, type=kind, env=data.env, secret_ref=ref,
            owner=project.owner, base_url=address, config=_found_config(finding, secret),
        ),
    )  # fmt: skip
    bindings.set_binding(session, BindingIn(project=name, instance=data.name))
    admin_audit.record(
        session, "connection.create",
        f"added {finding.label} connection {data.name} from {finding.file} ({data.env})",
        by=OWNER, project=name, instance=data.name,
    )  # fmt: skip
    return {"name": data.name}


def _found_config(finding: env_scan.Finding, secret: str) -> dict[str, Any]:
    """An MCP server found in a project says where it came from, with its secrets masked;
    a business system says which one it is."""
    if finding.service == InstanceType.VENDOR.value:
        return vendor.config_of(secret)
    if finding.service != InstanceType.MCP.value:
        return {}
    return {"source": "project", "file": finding.file, "preview": finding.masked}


# --- Logins on this computer, offered as your own connections ---


def _on_this_computer() -> bool:
    return get_settings().mode == "desktop"


def _skipped_logins(session: Session) -> list[str]:
    row = session.get(Setting, LOGINS_SKIPPED_KEY)
    return list(row.value.get("skipped", [])) if row else []


@router.get("/logins")
def list_logins(session: SessionDep) -> dict[str, Any]:
    """Who's signed in on this computer: never a token. Desktop only: it's this computer's."""
    if not _on_this_computer():
        return {"found": []}
    skipped = _skipped_logins(session)
    titles = {i.title for i in instances.list_instances(session)}
    return {
        "found": [
            {**vars(login), "added": login.title in titles and login.usable}
            for login in cli_logins.find()
            if login.id not in skipped
        ]
    }


class LoginConnectIn(Input):
    id: Annotated[str, Field(min_length=8, max_length=32)]
    name: Slug
    title: Title


@router.post("/logins/connect", status_code=201, dependencies=[RecentAuth])
def connect_login(data: LoginConnectIn, session: WriteSessionDep) -> dict[str, str]:
    """Connect one login, confirmed, as your own tool. Production: every change asks."""
    if not _on_this_computer():
        raise HTTPException(404, detail="Only the desktop app can see logins on this computer.")
    login = next((x for x in cli_logins.find() if x.id == data.id and x.usable), None)
    if login is None:
        raise HTTPException(409, detail="That login isn't on this computer any more.")
    cloud = cli_logins.is_cloud(login)
    config: dict[str, Any] = {}
    try:
        if cloud:
            kind = InstanceType.MCP
            secret = remote_connections.local_secret(login.service, dict(login.env))
            command = str(json.loads(secret)["command"])
            if missing := missing_command(command):
                raise RuleViolationError(f"{login.tool} {missing}.")
            config = {"remote": login.service, "source": "login", "preview": login.account}
        else:
            kind = InstanceType(login.service)
            secret = cli_logins.token_of(login)
        ref = secrets_in.store_connection(session, get_settings(), data.name, kind, secret)
    except RuleViolationError as error:
        raise HTTPException(422, detail=str(error)) from None
    instances.create_instance(
        session,
        InstanceIn(
            name=data.name, title=data.title, type=kind, env=Env.PROD, secret_ref=ref,
            owner=instances.SHARED_OWNER, shared=True, base_url=login.host or None, config=config,
        ),
    )  # fmt: skip
    admin_audit.record(
        session, "connection.create", f"added {login.tool}'s login as {data.name}",
        by=OWNER, instance=data.name,
    )  # fmt: skip
    return {"name": data.name}


class LoginSkipIn(Input):
    id: Annotated[str, Field(min_length=8, max_length=32)]


@router.post("/logins/skip", status_code=204)
def skip_login(data: LoginSkipIn, session: WriteSessionDep) -> None:
    """Don't offer this one again."""
    row = session.get(Setting, LOGINS_SKIPPED_KEY)
    value = {"skipped": sorted({*_skipped_logins(session), data.id})}
    if row is None:
        session.add(Setting(key=LOGINS_SKIPPED_KEY, value=value))
    else:
        row.value = value


class ScanSkipIn(Input):
    id: Annotated[str, Field(min_length=8, max_length=32)]


@router.post("/projects/{name}/scan/skip", status_code=204)
def skip_found(name: str, data: ScanSkipIn, session: WriteSessionDep) -> None:
    """Don't offer this one again."""
    row = session.get(Setting, SKIPPED_KEY)
    value = dict(row.value) if row else {}
    value[name] = sorted({*value.get(name, []), data.id})
    if row is None:
        session.add(Setting(key=SKIPPED_KEY, value=value))
    else:
        row.value = value
