"""Desktop-only API: the tray's state for the app shell, and what this computer
can offer the window (Claude Code's folders, as ready-made projects).

Mounted only in desktop mode. The shell reaches it through the front door
with the launch secret (/desktop/api/..., see desktop/server.py); the window
through /bff like the rest of the API.
"""

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import select

from ringkeeper.api.auth import AdminDep
from ringkeeper.api.deps import SessionDep, require_owner
from ringkeeper.config import get_settings
from ringkeeper.models import Project
from ringkeeper.services import inbox, runtime, workspaces
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
