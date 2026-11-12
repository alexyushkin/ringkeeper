"""Where a project's notes are kept, the notes themselves, and the owner's shared notes."""

import uuid
from typing import Annotated, Any, Literal, cast

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import Field
from sqlalchemy import func, select

from ringkeeper.api.auth import AdminDep
from ringkeeper.api.deps import OWNER, RecentAuth, SessionDep, WriteSessionDep, require_owner
from ringkeeper.db import session_scope
from ringkeeper.errors import RingkeeperError
from ringkeeper.models import MemoryNote
from ringkeeper.schemas import Input
from ringkeeper.services import (
    admin_audit,
    memory,
    notion_api,
    paging,
    personal_memory,
    projects,
)

router = APIRouter(dependencies=[AdminDep, Depends(require_owner)])
NotionId = Annotated[str, Field(min_length=8, max_length=64, pattern=r"^[0-9a-fA-F-]+$")]


def _note(note: MemoryNote) -> dict[str, Any]:
    return {
        "id": str(note.id), "note": note.body, "agent": note.agent, "task": note.source_task,
        "backend": note.backend, "created_at": note.created_at,
    }  # fmt: skip


def _where(saved: dict[str, Any]) -> dict[str, Any] | None:
    keys = ("connection", "parent", "data_source", "url", "agents_write")
    return {k: saved.get(k) for k in keys} if saved else None


@router.get("/projects/{name}/memory")
def memory_status(name: str, session: SessionDep) -> dict[str, Any]:
    project = projects.get_project(session, name)
    saved = memory.settings_of(project)
    every = memory.every_settings(session)
    kept = session.scalar(
        select(func.count()).select_from(MemoryNote).where(MemoryNote.project_id == project.id)
    )
    return {
        "backend": saved.get("backend") or memory.LOCAL,
        "notion": _where(saved.get("notion") or {}),
        "title": memory.title_of(session, project),
        "shared_notes": saved.get("shared_notes", True),
        "shared_available": bool(memory.shared_settings(session) or memory.mine_settings(session)),
        "notes": int(kept or 0),
        # Notion for every project is on, and this one follows when its first task starts.
        "follows": bool(every) and not saved.get("notion"),
        "connections": [
            {"name": i.name, "title": i.title or i.name}
            for i in memory.connections_for(session, project)
        ],
    }


class MemoryIn(Input):
    backend: Literal["local", "notion"]
    connection: Annotated[str, Field(max_length=63)] | None = None
    parent: NotionId | None = None
    data_source: NotionId | None = None
    shared_notes: bool | None = None


@router.put("/projects/{name}/memory")
def set_memory(name: str, data: MemoryIn, session: WriteSessionDep) -> dict[str, Any]:
    if data.connection:
        memory.attach(
            session, name, data.connection, parent=data.parent, data_source=data.data_source
        )
    memory.choose(session, name, data.backend, data.shared_notes)
    admin_audit.record(
        session, "memory.set", f"keeps its notes in {data.backend}", by=OWNER, project=name
    )
    return memory_status(name, session)


@router.post("/projects/{name}/memory/sync")
def sync_memory(name: str) -> dict[str, int]:
    return vars(memory.sync(name))


@router.get("/projects/{name}/memory/notes")
def list_notes(
    name: str,
    session: SessionDep,
    response: Response,
    limit: Annotated[int, Query(ge=1, le=paging.MOST_PER_PAGE)] = 50,
    q: Annotated[str | None, Query(max_length=200)] = None,
    cursor: Annotated[str | None, Query(max_length=300)] = None,
) -> list[dict[str, Any]]:
    page = memory.notes(session, name, words=q, limit=limit, cursor=cursor)
    page.tell(response)
    return [_note(n) for n in page.items]


class NoteIn(Input):
    note: Annotated[str, Field(min_length=1, max_length=memory.MOST_CHARS)]
    task: uuid.UUID | None = None


@router.post("/projects/{name}/memory/notes", status_code=201)
def add_note(name: str, data: NoteIn, session: SessionDep) -> dict[str, Any]:
    """A note of the owner's: the same filter as an agent's, in the project's own place."""
    projects.get_project(session, name)
    kept = memory.keep(name, data.note, str(data.task) if data.task else None)
    with session_scope() as writing:
        admin_audit.record(writing, "memory.keep", "added a note", by=OWNER, project=name)
    return kept


@router.get("/tasks/{task_id}/memory")
def task_notes(task_id: uuid.UUID, session: SessionDep) -> list[dict[str, str]]:
    """What this task saved to memory."""
    return memory.saved_by(session, task_id)


@router.delete("/projects/{name}/memory/notes/{note_id}", status_code=204)
def delete_note(name: str, note_id: uuid.UUID, session: WriteSessionDep) -> None:
    memory.forget(session, name, note_id)
    admin_audit.record(session, "memory.forget", "deleted a note", by=OWNER, project=name)


@router.get("/notion/{connection}/pages")
def notion_pages(
    connection: str, session: SessionDep, q: Annotated[str | None, Query(max_length=200)] = None
) -> list[dict[str, Any]]:
    """What this connection was given in Notion: pages, and databases to use as they are."""
    try:
        return notion_api.find(memory._token(session, connection), (q or "").strip())  # pyright: ignore[reportPrivateUsage]
    except RingkeeperError as error:
        raise HTTPException(409, detail=str(error)) from None


@router.get("/memory/personal")
def personal_status(session: SessionDep) -> dict[str, Any]:
    """The shared Notion connections, and whether each uses the owner's Notion memory."""
    return {
        "connections": [personal_memory.status(i) for i in personal_memory.connections(session)]
    }


class PersonalIn(Input):
    connection: Annotated[str, Field(max_length=63)]
    enabled: bool


@router.put("/memory/personal", dependencies=[RecentAuth])
def set_personal(data: PersonalIn, session: WriteSessionDep) -> dict[str, Any]:
    """It lets some calls run without asking, so it needs a recent confirmation."""
    personal_memory.set_enabled(session, data.connection, data.enabled)
    said = "on" if data.enabled else "off"
    admin_audit.record(
        session, "memory.personal", f"turned Notion memory {said}", by=OWNER,
        instance=data.connection,
    )  # fmt: skip
    return personal_status(session)


def _place_of(project: Any) -> dict[str, Any]:
    saved = memory.settings_of(project)
    where = cast(dict[str, Any], saved.get("notion") or {})
    in_notion = saved.get("backend") == memory.NOTION and bool(where)
    return {"name": project.name, "title": project.title,
            "where": memory.NOTION if in_notion else memory.LOCAL,
            "url": where.get("url") if in_notion else None}  # fmt: skip


@router.get("/memory/every")
def every_status(session: SessionDep) -> dict[str, Any]:
    """Notion for every project: the root page, and where each project's notes are now."""
    every = memory.every_settings(session)
    return {
        "every": {k: every.get(k) for k in ("connection", "root", "title", "url")} if every
        else None,
        "connections": [
            {"name": i.name, "title": i.title or i.name}
            for i in memory.connections_for(session, None)
        ],
        "projects": [_place_of(p) for p in projects.list_projects(session)],
    }  # fmt: skip


class EveryIn(Input):
    connection: Annotated[str, Field(max_length=63)]
    root: NotionId


@router.put("/memory/every")
def set_every(data: EveryIn, session: WriteSessionDep) -> dict[str, Any]:
    try:
        done = memory.use_everywhere(session, data.connection, data.root)
    except notion_api.NotionError as error:
        raise HTTPException(409, detail=str(error)) from None
    admin_audit.record(
        session, "memory.every", "kept every project's notes in Notion", by=OWNER,
        instance=data.connection,
    )  # fmt: skip
    return {**every_status(session), **done}


@router.delete("/memory/every", status_code=204)
def stop_every(session: WriteSessionDep) -> None:
    memory.stop_everywhere(session)
    admin_audit.record(session, "memory.every", "stopped Notion for new projects", by=OWNER)


@router.get("/memory/mine")
def mine_status(session: SessionDep) -> dict[str, Any]:
    return {
        "mine": _where(memory.mine_settings(session)),
        "connections": [
            {"name": i.name, "title": i.title or i.name}
            for i in memory.connections_for(session, None)
        ],
    }


class MineIn(Input):
    connection: Annotated[str, Field(max_length=63)]
    parent: NotionId | None = None
    data_source: NotionId | None = None


@router.put("/memory/mine")
def set_mine(data: MineIn, session: WriteSessionDep) -> dict[str, Any]:
    memory.attach_mine(session, data.connection, parent=data.parent, data_source=data.data_source)
    admin_audit.record(session, "memory.mine", "set the My memory database", by=OWNER)
    return mine_status(session)


@router.delete("/memory/mine", status_code=204)
def remove_mine(session: WriteSessionDep) -> None:
    memory.detach_mine(session)
    admin_audit.record(session, "memory.mine", "removed the My memory database", by=OWNER)


@router.get("/memory/shared")
def shared_status(session: SessionDep) -> dict[str, Any]:
    return {
        "shared": _where(memory.shared_settings(session)),
        "connections": [
            {"name": i.name, "title": i.title or i.name}
            for i in memory.connections_for(session, None)
        ],
    }


class SharedIn(Input):
    connection: Annotated[str, Field(max_length=63)]
    parent: NotionId | None = None
    data_source: NotionId | None = None
    agents_write: bool = False


@router.put("/memory/shared")
def set_shared(data: SharedIn, session: WriteSessionDep) -> dict[str, Any]:
    memory.attach_shared(
        session, data.connection, parent=data.parent, data_source=data.data_source,
        agents_write=data.agents_write,
    )  # fmt: skip
    admin_audit.record(session, "memory.shared", "set the shared notes page", by=OWNER)
    return shared_status(session)


@router.delete("/memory/shared", status_code=204)
def remove_shared(session: WriteSessionDep) -> None:
    memory.detach_shared(session)
    admin_audit.record(session, "memory.shared", "removed the shared notes page", by=OWNER)
