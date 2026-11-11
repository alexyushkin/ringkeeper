"""Home's first steps (services.onboarding) and the demo client (services.demo)."""

from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse, Response
from pydantic import Field

from ringkeeper.api.auth import AdminDep
from ringkeeper.api.deps import OWNER, SessionDep, WriteSessionDep, require_owner
from ringkeeper.claude_auth import credential_ref
from ringkeeper.db import session_scope
from ringkeeper.errors import RingkeeperError
from ringkeeper.schemas import Input
from ringkeeper.services import admin_audit, claude_place, demo, onboarding, report
from ringkeeper.services.runtime import current_settings
from ringkeeper.vault import get_vault

router = APIRouter(dependencies=[AdminDep, Depends(require_owner)])


def _claude_ready() -> bool:
    settings = current_settings()
    with session_scope(commit=False) as session:
        if claude_place.is_ready(session, get_vault()):
            return True
    try:
        get_vault().resolve(credential_ref(settings, settings.claude_auth_mode))
    except RingkeeperError:
        return False
    return True


@router.get("/start")
def start(session: SessionDep) -> dict[str, Any]:
    """The checklist, requests to try, and the demo (if it's there)."""
    settings = current_settings()
    steps = onboarding.checklist(session, _claude_ready())
    return {
        "checklist": {
            "claude": steps.claude,
            "project": steps.project,
            "connection": steps.connection,
            "task": steps.task,
            "done": steps.done,
            "dismissed": steps.dismissed,
            # Connections linked to no project, and whose they are (where to link them).
            "unlinked": [{"name": i.name, "client": i.owner} for i in onboarding.unlinked(session)],
        },
        "suggestions": onboarding.suggestions(session, settings),
        "demo": demo.summary(demo.current(session)),
        "demo_offered": settings.mode == "desktop",
        "tour_seen": onboarding.tour_seen(session),
        "whats_new": onboarding.whats_new(session, settings.app_version),
    }


@router.post("/start/dismiss", status_code=204)
def dismiss(session: WriteSessionDep) -> None:
    onboarding.dismiss(session)


@router.post("/start/whats-new", status_code=204)
def whats_new_seen(session: WriteSessionDep) -> None:
    onboarding.whats_new_seen(session)


@router.post("/start/tour", status_code=204)
def tour_done(session: WriteSessionDep) -> None:
    """The tour was finished or skipped: it doesn't show by itself again."""
    onboarding.see_tour(session)


def _desktop_only() -> None:
    if current_settings().mode != "desktop":
        raise HTTPException(404, detail="The demo is only in the desktop app.")


@router.post("/demo", status_code=201)
def add_demo(session: WriteSessionDep) -> dict[str, Any]:
    _desktop_only()
    made = demo.create(session, current_settings(), get_vault())
    admin_audit.record(
        session, "demo.create", "added the demo client", by=OWNER, project=made.project,
        instance=made.connection,
    )  # fmt: skip
    return demo.summary(made) or {}


@router.delete("/demo", status_code=204)
def remove_demo(session: WriteSessionDep) -> None:
    demo.remove(session, get_vault())
    admin_audit.record(session, "demo.remove", "removed the demo client and its data", by=OWNER)


# --- Report a problem (services.report) ---


@router.get("/report")
def report_preview(session: SessionDep) -> dict[str, Any]:
    """Exactly what a bug report would include, already cleaned of secrets."""
    parts = report.collect(session, current_settings())
    return {**parts, "text": report.as_text(parts)}


class ZipIn(Input):
    text: Annotated[str, Field(max_length=200_000)]
    what: Annotated[str, Field(max_length=20_000)] = ""


@router.post("/report/zip")
def report_zip(data: ZipIn) -> Response:
    """The report as a zip: saved into Documents (desktop), or downloaded (server)."""
    blob = report.zipped(data.text, data.what)
    name = report.file_name()
    if current_settings().mode == "desktop":
        folder = Path.home() / "Documents" / "Ringkeeper reports"
        try:
            folder.mkdir(parents=True, exist_ok=True)
            (folder / name).write_bytes(blob)
        except OSError as error:
            raise HTTPException(
                422, detail=f"Couldn't save the report ({error.strerror}). Try again."
            ) from None
        return JSONResponse({"path": str(folder / name)})
    return Response(
        blob,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )
