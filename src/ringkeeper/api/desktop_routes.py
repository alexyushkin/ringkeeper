"""Desktop-only API: the tray's state for the app shell, and what this computer
can offer the window: Claude Code's folders as ready-made projects, and Claude
Desktop's MCP servers as connections.

Mounted only in desktop mode. The shell reaches it through the front door
with the launch secret (/desktop/api/..., see desktop/server.py); the window
through /bff like the rest of the API.
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field
from sqlalchemy import func, select

from ringkeeper import voice_local
from ringkeeper.api.auth import AdminDep
from ringkeeper.api.deps import RecentAuth, SessionDep, WriteSessionDep, require_owner
from ringkeeper.config import get_settings
from ringkeeper.desktop.update import updates
from ringkeeper.errors import RuleViolationError
from ringkeeper.models import Project, Task, TaskStatus
from ringkeeper.schemas import Input, Slug
from ringkeeper.services import claude_desktop, inbox, runtime, workspaces
from ringkeeper.telegram.text import summary
from ringkeeper.voice import VoiceError

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
        "running": _running(session),
        "install_update": updates.restart_requested,
    }


def _running(session: SessionDep) -> int:
    """Tasks working or about to: an update waits for these."""
    busy = (TaskStatus.RUNNING, TaskStatus.QUEUED)
    return session.scalar(select(func.count()).where(Task.status.in_(busy))) or 0


class UpdateIn(Input):
    version: str = Field(min_length=1, max_length=64)
    notes: str = Field(default="", max_length=10_000)


@router.post("/update", status_code=204)
def update_ready(data: UpdateIn) -> None:
    """The shell downloaded an update (signature checked); it waits for the user.
    Only the shell can install it: it holds the download, not the backend."""
    updates.offer(data.version, data.notes)


@router.get("/update")
def update_status(session: SessionDep) -> dict[str, Any]:
    ready = updates.ready
    return {
        "ready": None if ready is None else {"version": ready.version, "notes": ready.notes},
        "restart_requested": updates.restart_requested,
        "running": _running(session),
    }


@router.post("/update/restart", status_code=202)
def update_restart(session: SessionDep) -> dict[str, Any]:
    """The user is done: restart into the new version once nothing is running."""
    if not updates.request_restart():
        raise HTTPException(404, "There's no update ready.")
    return update_status(session)


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
    """The MCP servers in Claude Desktop's settings and its installed extensions: name,
    a preview with secrets masked, how many secrets, what an extension still needs,
    and what's in the way of importing it."""
    config, extensions = claude_desktop.find_config(), claude_desktop.find_extensions()
    done = claude_desktop.imported(session)
    return {
        "config": str(config) if config else None,
        "extensions": str(extensions) if extensions else None,
        "servers": [
            s.summary() | {"imported_as": done.get(s.key)} for s in claude_desktop.everything()
        ],
    }


class ImportChoice(Input):
    name: str  # the server's key (its name, or extension:<folder>)
    project: Slug | None = None  # one project (older clients); see projects
    projects: list[Slug] = Field(default_factory=list[str])
    # A shared tool (no client's data): any client's projects, or all of them.
    # None: what the server looks like (Server.suggested).
    shared: bool | None = None
    all_projects: bool = False
    # What an extension needs that Claude Desktop's files don't show (an API key).
    values: dict[str, Annotated[str, Field(max_length=4000)]] = Field(
        default_factory=dict[str, str], repr=False
    )


class ImportIn(Input):
    servers: list[ImportChoice]


@router.post("/claude-desktop-servers/import", dependencies=[RecentAuth])
def import_claude_desktop_servers(data: ImportIn, session: WriteSessionDep) -> dict[str, Any]:
    """Import the chosen servers, each linked to its project. The secrets are read
    here from Claude Desktop's files (or typed for an extension) and go straight
    into the vault."""
    found = {s.key: s for s in claude_desktop.everything()}
    if not found and claude_desktop.find_config() is None:
        raise HTTPException(404, detail="Claude Desktop's settings aren't on this computer.")
    done = claude_desktop.imported(session)
    created: dict[str, str] = {}
    for choice in data.servers:
        server = found.get(choice.name)
        if server is None:
            raise HTTPException(404, detail=f"Claude Desktop has no server named {choice.name}.")
        if choice.name in done:
            raise HTTPException(
                409, detail=f"{server.name} is already imported as {done[choice.name]}."
            )
        named: list[str] = [choice.project] if choice.project else []
        linked = list(dict.fromkeys([*named, *choice.projects]))
        shared = server.suggested() == "shared" if choice.shared is None else choice.shared
        try:
            created[choice.name] = claude_desktop.import_server(
                session,
                get_settings(),
                server,
                linked,
                dict(choice.values),
                shared=shared,
                all_projects=choice.all_projects,
            )
        except RuleViolationError as error:
            raise HTTPException(422, detail=str(error)) from None
    return {"imported": created}


# --- Voice on this computer ---


@router.get("/voice")
def voice_status() -> dict[str, Any]:
    """Whether speech works here: the engine, the model (and how big its download
    is), and a download's progress."""
    return voice_local.model(get_settings()).status()


@router.post("/voice/download", status_code=202)
def voice_download() -> dict[str, Any]:
    """Fetch the speech model, once. The app polls GET /desktop/voice for progress."""
    found = voice_local.model(get_settings())
    found.start()
    return found.status()


class VoiceChoiceIn(Input):
    choice: str


@router.put("/voice/choice")
def voice_choose(body: VoiceChoiceIn) -> dict[str, Any]:
    """Fast or Accuracy (Settings > Voice); the new model downloads when asked."""
    found = voice_local.model(get_settings())
    try:
        found.choose(body.choice)
    except ValueError as error:
        raise HTTPException(422, detail=str(error)) from None
    except VoiceError as error:
        raise HTTPException(409, detail=str(error)) from None
    return found.status()


@router.delete("/voice", status_code=204)
def voice_remove() -> None:
    voice_local.model(get_settings()).remove()
