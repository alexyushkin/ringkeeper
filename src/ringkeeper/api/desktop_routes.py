"""Desktop-only API: the tray's state for the app shell, and what this computer
can offer the window: the folders you worked in with Claude (Claude Code, Cowork)
and your Claude projects as ready-made projects, and Claude Desktop's MCP
servers as connections.

Mounted only in desktop mode. The shell reaches it through the front door
with the launch secret (/desktop/api/..., see desktop/server.py); the window
through /bff like the rest of the API.
"""

from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import Field
from sqlalchemy import func, select

from ringkeeper import ocr_local, voice_local
from ringkeeper.api import manage_routes
from ringkeeper.api.auth import AdminDep
from ringkeeper.api.deps import RecentAuth, SessionDep, WriteSessionDep, require_owner
from ringkeeper.claude_auth import ClaudeAuthError
from ringkeeper.config import get_settings
from ringkeeper.desktop import lifecycle, opener
from ringkeeper.desktop.update import State, updates
from ringkeeper.errors import RuleViolationError
from ringkeeper.models import Project, Task, TaskStatus
from ringkeeper.scenarios.self_test import self_test
from ringkeeper.schemas import Input, ProjectIn, Slug
from ringkeeper.services import (
    backup,
    backup_places,
    claude_desktop,
    claude_work,
    downloads,
    inbox,
    onboarding,
    runtime,
    workspaces,
)
from ringkeeper.telegram.text import summary
from ringkeeper.vault import get_vault
from ringkeeper.voice import VoiceError
from ringkeeper.workers import desktop_docker

# Same length rule as Telegram: a notification is a pointer to the app.
NOTICE_CHARS = 160

router = APIRouter(prefix="/desktop", dependencies=[AdminDep, Depends(require_owner)])


@router.get("/status")
def status(session: SessionDep) -> dict[str, Any]:
    """Paused or not, start at sign-in or not, and what is waiting for the user."""
    settings = runtime.effective(session)
    waiting = [
        {
            "id": inbox.notice_key(decision),  # a snoozed one back is notified again
            "kind": decision.kind.value,
            "project": decision.project,
            "summary": summary(decision, NOTICE_CHARS).text,
            "approval": inbox.is_approval(decision),
            "quick": inbox.quick_approval(decision),
        }
        for decision in inbox.list_waiting(session)
    ]
    return {
        "paused": settings.paused,
        "routines_paused": settings.routines_paused,
        "start_at_login": settings.start_at_login,
        "waiting": waiting,
        "running": _running(session),
        "install_update": updates.restart_requested,
        "check_updates": settings.check_updates,
        "check_update": updates.request,  # a check the user asked for (its id)
        "open": opener.pending(),  # a page an Open link from Slack or Discord asked for
        "hotkey": settings.hotkey if settings.hotkey_enabled else None,  # the quick request box
    }


class OpenIn(Input):
    path: str = Field(pattern=r"^/[A-Za-z0-9/?=&_.-]*$", max_length=200)


@router.post("/open", status_code=202)
def open_in_window(data: OpenIn) -> None:
    """Bring the app's window up at a page (the quick box's link to its conversation)."""
    opener.ask(data.path)


def _running(session: SessionDep) -> int:
    """Tasks working or about to: an update waits for these."""
    busy = (TaskStatus.RUNNING, TaskStatus.QUEUED)
    return session.scalar(select(func.count()).where(Task.status.in_(busy))) or 0


class UpdateIn(Input):
    version: str = Field(min_length=1, max_length=64)
    notes: str = Field(default="", max_length=10_000)


@router.post("/update", status_code=204)
def update_ready(data: UpdateIn, session: WriteSessionDep) -> None:
    """The shell downloaded an update (signature checked); it waits for the user.
    Only the shell can install it: it holds the download, not the backend. Its notes
    are kept for Home's "What's new" once it runs."""
    updates.offer(data.version, data.notes)
    onboarding.keep_notes(session, data.version, data.notes)


@router.get("/update")
def update_status(session: SessionDep) -> dict[str, Any]:
    """This version, the last check and how it ended, what's downloaded, and whether
    a restart waits for running tasks."""
    ready, last = updates.ready, updates.last
    return {
        "version": get_settings().app_version,
        "ready": None if ready is None else {"version": ready.version, "notes": ready.notes},
        "restart_requested": updates.restart_requested,
        "running": _running(session),
        "checking": updates.request is not None or updates.working is not None,
        "downloading": updates.downloading,
        "unanswered": updates.unanswered(),
        "last": None
        if last is None
        else {
            "at": datetime.fromtimestamp(last.at, UTC).isoformat(),
            "result": last.state,
            "error": last.error,
        },
        "check_updates": runtime.effective(session).check_updates,
    }


@router.post("/update/check", status_code=202)
def update_check(session: SessionDep) -> dict[str, Any]:
    """Check now (Settings > About): the shell picks it up within a few seconds."""
    updates.request_check()
    return update_status(session)


class CheckedIn(Input):
    state: State
    request: str | None = Field(default=None, max_length=64)
    version: str | None = Field(default=None, max_length=64)
    error: str | None = Field(default=None, max_length=500)
    at: float | None = None  # unix time; the shell remembers its last check across restarts


@router.post("/update/checked", status_code=204)
def update_checked(data: CheckedIn) -> None:
    """The shell: a check started, is downloading, or ended (and how)."""
    updates.report(
        data.state, request=data.request, version=data.version, error=data.error,
        at=data.at,
    )  # fmt: skip


@router.post("/update/restart", status_code=202)
def update_restart(session: SessionDep) -> dict[str, Any]:
    """The user is done: restart into the new version once nothing is running."""
    if not updates.request_restart():
        raise HTTPException(404, "There's no update ready.")
    return update_status(session)


@router.get("/claude-code-folders")
def claude_code_folders(session: SessionDep) -> list[dict[str, Any]]:
    """Folders the user worked in with Claude (Claude Code, and the Claude app's Cowork
    and Code tab) that aren't projects here yet, and where each was used. Only paths
    and names: nothing inside the folders is read."""
    settings = get_settings()
    paths = session.scalars(select(Project.workspace_path)).all()
    used = [str(workspaces.root(settings) / path) for path in paths if path]
    found = workspaces.claude_code_folders(settings, used, claude_work.opened_folders())
    opened = claude_work.last_opened()
    # A folder holding other projects (or these folders) is a folder of projects.
    inside = [path for path, _ in found] + [Path(p) for p in used]
    return [
        {
            "folder": str(path), "name": path.name, "from": where,
            "used_at": max(
                (t for t in (workspaces.claude_code_used(path),
                             opened.get(workspaces.folder_key(str(path)))) if t),
                default=None,
            ),
            "holds": workspaces.holds(path, inside),
        }
        for path, where in found
    ]  # fmt: skip


@router.get("/claude-projects")
def claude_projects(session: SessionDep) -> list[dict[str, Any]]:
    """The Claude projects (Chat and Cowork) the Claude app keeps a copy of, and what
    each brings: its description, whether it has instructions, how many files."""
    return [
        {
            "id": p.id, "name": p.name, "description": p.description,
            "instructions": bool(p.instructions), "files": len(p.files()),
            "used_at": p.used_at(),
        }
        for p in claude_work.claude_projects()
    ]  # fmt: skip


class ClaudeProjectIn(Input):
    name: Slug
    owner: Annotated[str, Field(min_length=1, max_length=200)]


@router.post("/claude-projects/{project_id}", status_code=201, dependencies=[RecentAuth])
def import_claude_project(
    project_id: Annotated[str, Field(max_length=100)],
    data: ClaudeProjectIn,
    session: WriteSessionDep,
) -> dict[str, Any]:
    """A Claude project as a project here: a new folder, its instructions as CLAUDE.md,
    and a copy of its files."""
    found = claude_work.find_project(project_id)
    if found is None:
        raise HTTPException(404, "That Claude project isn't on this computer any more.")
    made = manage_routes.create_project(
        ProjectIn(
            name=data.name,
            title="" if found.name == data.name else found.name,
            owner=data.owner,
            description=found.description,
        ),
        session,
    )
    wrote, copied = claude_work.bring_in(get_settings(), found, Path(made["folder"]))
    return {**made, "instructions": wrote, "files": copied}


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


# --- Tasks in Docker ---


@router.get("/docker")
def docker_status(session: SessionDep) -> dict[str, Any]:
    """Whether tasks can run in Docker here, and the next step if not."""
    return desktop_docker.status(runtime.effective(session))


@router.post("/docker/download", status_code=202)
def docker_download(session: SessionDep) -> dict[str, Any]:
    """Fetch the image, once. The app polls GET /desktop/docker for progress."""
    settings = runtime.effective(session)
    image = desktop_docker.image_for(settings)
    if image is None:
        raise HTTPException(422, detail="This version of Ringkeeper doesn't have a Docker image.")
    desktop_docker.download.start(desktop_docker.connect, image)
    return desktop_docker.status(settings)


class DockerIn(Input):
    on: bool


@router.put("/docker")
def docker_choose(body: DockerIn, session: WriteSessionDep) -> dict[str, Any]:
    """On only once it can work: Docker running, the image here, the gateway reachable."""
    settings = runtime.effective(session)
    if body.on and (problem := desktop_docker.turn_on(settings)) is not None:
        raise HTTPException(422, detail=problem)
    runtime.update(session, {"docker_tasks": body.on}, by="owner")
    return desktop_docker.status(runtime.effective(session))


# --- Self-test ---


@router.get("/self-test")
def self_test_status() -> dict[str, Any]:
    """Whether it's running, how far it's got, and the last report."""
    return self_test.status(get_settings())


@router.post("/self-test", status_code=202)
def self_test_start(session: SessionDep) -> dict[str, Any]:
    """Start a run in a scratch folder. The app polls GET /desktop/self-test."""
    settings = runtime.effective(session)
    try:
        self_test.start(settings, get_vault())
    except ClaudeAuthError as error:
        raise HTTPException(422, detail=str(error)) from None
    return self_test.status(settings)


@router.post("/self-test/stop", status_code=202)
def self_test_stop() -> dict[str, Any]:
    """Finish the scenario under way and skip the rest."""
    settings = get_settings()
    self_test.stop(settings)
    return self_test.status(settings)


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


# --- Reading scans on this computer ---


@router.get("/ocr")
def ocr_status() -> dict[str, Any]:
    """Whether scans can be read here, and whether a task has just needed that."""
    return ocr_local.model(get_settings()).status()


@router.post("/ocr/download", status_code=202)
def ocr_download() -> dict[str, Any]:
    """Fetch the scan reader, once. The app polls GET /desktop/ocr for progress."""
    found = ocr_local.model(get_settings())
    found.start()
    return found.status()


@router.post("/ocr/decline")
def ocr_decline() -> dict[str, Any]:
    """Not now: the offer goes, and the download stays in Settings."""
    found = ocr_local.model(get_settings())
    found.decline()
    return found.status()


@router.delete("/ocr", status_code=204)
def ocr_remove() -> None:
    ocr_local.model(get_settings()).remove()


# --- Backup (services.backup) ---


def _backup_folder() -> Path:
    return Path.home() / "Documents" / "Ringkeeper backups"


class ExportIn(Input):
    password: Annotated[str, Field(max_length=1000)]
    folder: Annotated[str, Field(max_length=1000)] | None = None


@router.post("/backup/export", dependencies=[RecentAuth])
def backup_export(data: ExportIn) -> dict[str, Any]:
    """Write a backup file into a folder (Documents\\Ringkeeper backups unless chosen)."""
    folder = Path(data.folder.strip()) if data.folder and data.folder.strip() else _backup_folder()
    if not folder.is_absolute():
        raise HTTPException(422, detail="Choose a full folder path for the backup, or Browse.")
    blob = backup.export(get_settings(), data.password)
    try:
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"Ringkeeper-backup-{datetime.now(UTC):%Y-%m-%d-%H%M}{backup.SUFFIX}"
        path.write_bytes(blob)
    except OSError as error:
        raise HTTPException(
            422,
            detail=f"Couldn't write the backup there ({error.strerror}). Choose another folder.",
        ) from None
    return {"path": str(path), "bytes": len(blob), "default_folder": str(_backup_folder())}


@router.get("/backup")
def backup_info(session: SessionDep) -> dict[str, Any]:
    auto = backup.auto(session)
    return {
        "default_folder": str(_backup_folder()),
        "auto": auto.__dict__,
        # One-click choices next to Browse: Ringkeeper only writes the file there.
        "synced": [place.__dict__ for place in backup_places.synced_folders()],
    }


@router.get("/backup/folder")
def backup_folder_check(
    session: SessionDep, folder: Annotated[str, Query(max_length=1000)] = ""
) -> dict[str, Any]:
    """Whether a backup there outlives this computer: synced, another drive, or backed up
    another way, or the same disk (the warning)."""
    chosen = folder.strip() or str(_backup_folder())
    if not Path(chosen).is_absolute():
        raise HTTPException(422, detail="Choose a full folder path for the backup, or Browse.")
    places = backup_places.synced_folders()
    return backup_places.check(session, chosen, get_settings().data_dir, places)


class ElsewhereIn(Input):
    folder: Annotated[str, Field(min_length=1, max_length=1000)]
    backed_up: bool = True


@router.put("/backup/elsewhere")
def backup_elsewhere(data: ElsewhereIn, session: WriteSessionDep) -> dict[str, Any]:
    """'I back this folder up another way': the same-disk warning isn't shown for it."""
    kept = backup_places.set_elsewhere(session, data.folder.strip(), data.backed_up)
    return {"folders": kept}


async def _upload(request: Request) -> bytes:
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > backup.MAX_BYTES:
        raise HTTPException(413, detail="That file is too big to be a Ringkeeper backup.")
    body = b""
    async for chunk in request.stream():
        body += chunk
        if len(body) > backup.MAX_BYTES:
            raise HTTPException(413, detail="That file is too big to be a Ringkeeper backup.")
    return body


Password = Annotated[str, Header(alias="x-backup-password", max_length=1000)]


@router.post("/backup/preview")
async def backup_preview(request: Request, password: Password) -> dict[str, Any]:
    """What restoring this file brings in (the password opens it; nothing changes)."""
    return backup.preview(await _upload(request), password)


@router.post("/backup/restore", dependencies=[RecentAuth])
async def backup_restore(request: Request, password: Password) -> dict[str, Any]:
    """Bring a backup in: its secrets now, its database at the restart that follows."""
    found = backup.restore(get_settings(), get_vault(), await _upload(request), password)
    found["restarting"] = lifecycle.restart_soon()
    return found


class AutoIn(Input):
    enabled: bool
    folder: Annotated[str, Field(max_length=1000)] = ""
    keep: int = 4
    password: Annotated[str, Field(max_length=1000)] | None = None


@router.put("/backup/auto", dependencies=[RecentAuth])
def backup_auto(data: AutoIn, session: WriteSessionDep) -> dict[str, Any]:
    folder = data.folder.strip() or str(_backup_folder())
    auto = backup.set_auto(
        session, get_vault(), enabled=data.enabled, folder=folder, keep=data.keep,
        password=data.password or None,
    )  # fmt: skip
    if auto.enabled:
        onboarding.backup_nudge_done(session)  # Home doesn't ask again, even if turned off
    return auto.__dict__


# --- Saving files into Downloads (services.downloads) ---


class DownloadIn(Input):
    name: Annotated[str, Field(min_length=1, max_length=200)]
    text: Annotated[str, Field(max_length=900_000)]  # under the 1 MB a request can be


class SavedIn(Input):
    path: Annotated[str, Field(min_length=1, max_length=2000)]


@router.post("/downloads")
def save_download(data: DownloadIn) -> dict[str, str]:
    """What a browser would download (a table as CSV, a report): saved into Downloads,
    under the next free name. The app window can't download a link itself."""
    path = downloads.save(get_settings(), data.name, data.text)
    return {"path": str(path), "name": path.name}


@router.post("/downloads/show")
def show_download(data: SavedIn) -> dict[str, bool]:
    """Show a file saved into Downloads in its folder."""
    downloads.show(get_settings(), data.path)
    return {"ok": True}
