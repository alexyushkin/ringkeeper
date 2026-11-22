"""Settings > Passes: an AI app on this computer using one project's connections
(services.passes). A pass's token never comes back out; the setup it shows starts
Ringkeeper's bridge, which reads the token from the vault."""

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field

from ringkeeper.api.auth import AdminDep
from ringkeeper.api.deps import RecentAuth, SessionDep, WriteSessionDep, require_owner
from ringkeeper.config import Settings, get_settings
from ringkeeper.models import Pass
from ringkeeper.schemas import Input, Slug
from ringkeeper.services import admin_audit, app_setup, passes, titles, undo
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


def _apps(settings: Settings, name: str) -> list[dict[str, Any]]:
    """Each AI app on this computer Ringkeeper sets up, and whether the pass is in it. Only the
    desktop app: a server's AI apps aren't on the computer it runs on."""
    if settings.mode != "desktop":
        return []
    started = passes.launch(settings, name)
    server = passes.server_name(name)
    return [app_setup.status(key, server, started) for key in app_setup.APPS]


def _shown(session: Any, settings: Settings, row: Pass) -> dict[str, Any]:
    return {
        "name": row.name,
        "project": row.project.name,
        "project_title": titles.of(session, row.project.name),
        "created_at": row.created_at,
        "last_used_at": row.last_used_at,
        "revoked_at": row.revoked_at,
        "setup": _setup(settings, row.name),
        "apps": _apps(settings, row.name),
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


@router.delete("/{name}", dependencies=[RecentAuth])
def remove(name: Slug, session: WriteSessionDep) -> dict[str, Any]:
    """Gone, and out of the AI apps it was added to, so they don't keep starting a pass that
    isn't there. An app whose file can't be changed now is said, and the pass goes anyway."""
    project = passes.get(session, name).project.name
    passes.remove(session, get_vault(), name)
    admin_audit.record(
        session, "pass.removed", f"removed the pass {name}", by="owner", project=project
    )
    taken: list[str] = []
    left: list[str] = []
    if get_settings().mode == "desktop":
        for key, the_app in app_setup.APPS.items():
            if not the_app.found():
                continue
            try:
                if app_setup.remove(get_settings(), key, passes.server_name(name)):
                    taken.append(the_app.label)
            except app_setup.AppSetupError as error:
                left.append(f"{the_app.label}: {error}")
    return {"removed_from": taken, "left_in": left}


def downloads() -> Path:
    return Path.home() / "Downloads"


@router.post("/{name}/claude-desktop")
def claude_desktop(name: Slug, session: SessionDep) -> dict[str, str]:
    """The pass as a Claude Desktop extension, saved into Downloads to open from there."""
    settings = get_settings()
    if settings.mode != "desktop":
        raise HTTPException(409, "A pass for Claude Desktop needs the desktop app.")
    row = passes.get(session, name)
    if row.revoked_at is not None:
        raise HTTPException(409, f"The pass {name} is revoked. Make a new one.")
    blob = passes.extension(settings, row.name, titles.of(session, row.project.name))
    path = downloads() / f"Ringkeeper-{row.name}.mcpb"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(blob)
    except OSError as error:
        raise HTTPException(422, f"Couldn't save it ({error.strerror}). Try again.") from None
    return {"path": str(path)}


def open_file(path: Path) -> None:
    """Open the file with the app that opens it: Claude Desktop, for an extension."""
    if sys.platform == "win32":
        os.startfile(path)  # noqa: S606 (an extension Ringkeeper just made)  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])  # noqa: S603, S607
    else:
        subprocess.Popen(["xdg-open", str(path)])  # noqa: S603, S607


def _desktop_or_409(settings: Settings) -> None:
    if settings.mode != "desktop":
        raise HTTPException(409, "Adding a pass to an app needs the desktop app.")


def _usable(session: Any, name: str) -> Pass:
    row = passes.get(session, name)
    if row.revoked_at is not None:
        raise HTTPException(409, f"The pass {name} is revoked. Make a new one.")
    return row


AppKey = Annotated[str, Field(min_length=1, max_length=40)]


@router.post("/{name}/apps/{app}", dependencies=[RecentAuth])
def add_to_app(name: Slug, app: AppKey, session: WriteSessionDep) -> dict[str, Any]:
    """The pass in an AI app on this computer, in one click. Claude Desktop: its extension,
    saved into Downloads and opened, so Claude asks whether to install it. Claude Code and
    Cursor: their settings files (services.app_setup)."""
    settings = get_settings()
    _desktop_or_409(settings)
    row = _usable(session, name)
    if app == "claude-desktop":
        saved = claude_desktop(name, session)
        path = Path(saved["path"])
        try:
            open_file(path)
        except OSError:
            return {"said": f"Saved to {path}. Double-click it to install it in Claude Desktop.",
                    "path": str(path), "opened": False}  # fmt: skip
        return {"said": "Claude Desktop is asking whether to install it. Click Install, then "
                "start a new chat.", "path": str(path), "opened": True}  # fmt: skip
    try:
        the_app = app_setup.app(app)
        app_setup.add(
            settings, app, passes.server_name(row.name), passes.launch(settings, row.name)
        )
    except app_setup.AppSetupError as error:
        raise HTTPException(409, str(error)) from None
    admin_audit.record(
        session, "pass.added_to_app", f"added the pass {name} to {the_app.label}",
        by="owner", project=row.project.name,
    )  # fmt: skip
    restart = "Start a new Claude Code session" if app == "claude-code" else "Restart Cursor"
    return {"said": f"Added to {the_app.label}. {restart} and it has the pass's tools.",
            "status": _status(settings, row.name, app)}  # fmt: skip


@router.delete("/{name}/apps/{app}", dependencies=[RecentAuth])
def remove_from_app(name: Slug, app: AppKey, session: WriteSessionDep) -> dict[str, Any]:
    """The pass taken out of an AI app's settings. A revoked pass too: that's when it's wanted."""
    settings = get_settings()
    _desktop_or_409(settings)
    row = passes.get(session, name)
    if app == "claude-desktop":
        raise HTTPException(409, "Remove it in Claude Desktop: Settings, then Extensions.")
    try:
        the_app = app_setup.app(app)
        app_setup.remove(settings, app, passes.server_name(row.name))
    except app_setup.AppSetupError as error:
        raise HTTPException(409, str(error)) from None
    admin_audit.record(
        session, "pass.removed_from_app", f"took the pass {name} out of {the_app.label}",
        by="owner", project=row.project.name,
    )  # fmt: skip
    return {"said": f"Taken out of {the_app.label}.", "status": _status(settings, row.name, app)}


def _status(settings: Settings, name: str, app: str) -> dict[str, Any]:
    started = passes.launch(settings, name)
    return app_setup.status(app, passes.server_name(name), started)


@router.get("/{name}/changes")
def changes(name: Slug, session: SessionDep) -> list[dict[str, Any]]:
    """What the app changed through this pass: each with Undo for 7 days, or why it can't."""
    return undo.for_session(session, passes.session_id(passes.get(session, name)))
