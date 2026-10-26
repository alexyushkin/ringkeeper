"""Desktop-only API: the tray's state for the app shell, and what this computer
can offer the window: Claude Code's folders as ready-made projects, and Claude
Desktop's MCP servers as connections.

Mounted only in desktop mode. The shell reaches it through the front door
with the launch secret (/desktop/api/..., see desktop/server.py); the window
through /bff like the rest of the API.
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select

from ringkeeper.api.auth import AdminDep
from ringkeeper.api.deps import RecentAuth, SessionDep, WriteSessionDep, require_owner
from ringkeeper.config import get_settings
from ringkeeper.errors import RuleViolationError
from ringkeeper.models import Project
from ringkeeper.schemas import Input, Slug
from ringkeeper.services import claude_desktop, inbox, runtime, workspaces
from ringkeeper.telegram.text import summary

# Same length rule as Telegram: a notification is a pointer to the app.
NOTICE_CHARS = 160

router = APIRouter(prefix="/desktop", dependencies=[AdminDep, Depends(require_owner)])


@router.get("/status")
def status(session: SessionDep) -> dict[str, Any]:
    """Paused or not, start at sign-in or not, and what is waiting for the user."""
    settings = runtime.effective(session)
    waiting = [
        {
            "id": str(decision.id),
            "kind": decision.kind.value,
            "project": decision.project,
            "summary": summary(decision, NOTICE_CHARS).text,
        }
        for decision in inbox.list_waiting(session)
    ]
    return {
        "paused": settings.paused,
        "start_at_login": settings.start_at_login,
        "waiting": waiting,
    }


@router.get("/claude-code-folders")
def claude_code_folders(session: SessionDep) -> list[dict[str, str]]:
    """Folders the user opened Claude Code in that aren't projects here yet.
    Only paths and names: nothing inside the folders is read."""
    settings = get_settings()
    paths = session.scalars(select(Project.workspace_path)).all()
    used = [str(workspaces.root(settings) / path) for path in paths if path]
    return [
        {"folder": str(path), "name": path.name}
        for path in workspaces.claude_code_folders(settings, used)
    ]


@router.get("/claude-desktop-servers")
def claude_desktop_servers(session: SessionDep) -> dict[str, Any]:
    """The MCP servers in Claude Desktop's settings: name, a preview with secrets
    masked, how many secrets it has, and what's in the way of importing it."""
    path = claude_desktop.find_config()
    if path is None:
        return {"config": None, "servers": []}
    done = claude_desktop.imported(session)
    return {
        "config": str(path),
        "servers": [
            s.summary() | {"imported_as": done.get(s.name)}
            for s in claude_desktop.read_servers(path)
        ],
    }


class ImportChoice(Input):
    name: str
    project: Slug


class ImportIn(Input):
    servers: list[ImportChoice]


@router.post("/claude-desktop-servers/import", dependencies=[RecentAuth])
def import_claude_desktop_servers(data: ImportIn, session: WriteSessionDep) -> dict[str, Any]:
    """Import the chosen servers, each linked to its project. The secrets are read
    here from Claude Desktop's file and go straight into the vault."""
    path = claude_desktop.find_config()
    if path is None:
        raise HTTPException(404, detail="Claude Desktop's settings file isn't on this computer.")
    found = {s.name: s for s in claude_desktop.read_servers(path)}
    done = claude_desktop.imported(session)
    created: dict[str, str] = {}
    for choice in data.servers:
        server = found.get(choice.name)
        if server is None:
            raise HTTPException(404, detail=f"Claude Desktop has no server named {choice.name}.")
        if choice.name in done:
            raise HTTPException(
                409, detail=f"{choice.name} is already imported as {done[choice.name]}."
            )
        try:
            created[choice.name] = claude_desktop.import_server(
                session, get_settings(), server, choice.project
            )
        except RuleViolationError as error:
            raise HTTPException(422, detail=str(error)) from None
    return {"imported": created}
