"""Settings > Passes: an AI app on this computer using one project's connections
(services.passes). A pass's token never comes back out; the setup it shows starts
Ringkeeper's bridge, which reads the token from the vault."""

import json
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import Field

from ringkeeper.api.auth import AdminDep
from ringkeeper.api.deps import RecentAuth, SessionDep, WriteSessionDep, require_owner
from ringkeeper.config import Settings, get_settings
from ringkeeper.models import Pass
from ringkeeper.schemas import Input, Slug
from ringkeeper.services import admin_audit, passes, titles, undo
from ringkeeper.vault import get_vault

router = APIRouter(prefix="/passes", dependencies=[AdminDep, Depends(require_owner)])


def _quote(part: str) -> str:
    """One argument as PowerShell and bash both read it: double quotes around a path with a
    space in it (Windows paths have no characters either shell would expand there)."""
    return f'"{part}"' if any(c in part for c in " &()'`;") else part


def _setup(settings: Settings, name: str) -> dict[str, Any]:
    started = passes.launch(settings, name)
    server = passes.server_name(name)
    entry = {"command": started["command"], "args": started["args"]}
    if started["env"]:
        entry["env"] = started["env"]
    env = [f"--env {key}={value}" for key, value in started["env"].items()]
    # '--' in quotes: in PowerShell, Claude Code's npm shim (a .ps1) would swallow a bare --.
    claude_code = " ".join(
        ["claude mcp add --scope user", *env, server, "'--'",
         _quote(started["command"]), *(_quote(a) for a in started["args"])]
    )  # fmt: skip
    return {
        "server": server,
        "json": json.dumps({"mcpServers": {server: entry}}, indent=2),
        "claude_code": claude_code,
    }


def _shown(session: Any, settings: Settings, row: Pass) -> dict[str, Any]:
    return {
        "name": row.name,
        "project": row.project.name,
        "project_title": titles.of(session, row.project.name),
        "created_at": row.created_at,
        "last_used_at": row.last_used_at,
        "revoked_at": row.revoked_at,
        "setup": _setup(settings, row.name),
    }


@router.get("")
def list_all(session: SessionDep) -> dict[str, Any]:
    settings = get_settings()
    return {
        "desktop": settings.mode == "desktop",
        "passes": [_shown(session, settings, p) for p in passes.list_passes(session)],
    }


class PassIn(Input):
    name: Annotated[str, Field(min_length=1, max_length=63)]
    project: Slug


@router.post("", status_code=201, dependencies=[RecentAuth])
def create(data: PassIn, session: WriteSessionDep) -> dict[str, Any]:
    row = passes.create(session, get_vault(), data.name, data.project)
    admin_audit.record(
        session, "pass.created", f"made the pass {row.name} for {row.project.name}",
        by="owner", project=row.project.name,
    )  # fmt: skip
    return _shown(session, get_settings(), row)


@router.post("/{name}/revoke", dependencies=[RecentAuth])
def revoke(name: Slug, session: WriteSessionDep) -> dict[str, Any]:
    row = passes.revoke(session, name, by="owner")
    admin_audit.record(
        session, "pass.revoked", f"revoked the pass {row.name}",
        by="owner", project=row.project.name,
    )  # fmt: skip
    return _shown(session, get_settings(), row)


@router.delete("/{name}", status_code=204, dependencies=[RecentAuth])
def remove(name: Slug, session: WriteSessionDep) -> None:
    project = passes.get(session, name).project.name
    passes.remove(session, get_vault(), name)
    admin_audit.record(
        session, "pass.removed", f"removed the pass {name}", by="owner", project=project
    )


@router.get("/{name}/changes")
def changes(name: Slug, session: SessionDep) -> list[dict[str, Any]]:
    """What the app changed through this pass: each with Undo for 7 days, or why it can't."""
    return undo.for_session(session, passes.session_id(passes.get(session, name)))
