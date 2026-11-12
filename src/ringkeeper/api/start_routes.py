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
from ringkeeper.services import (
    admin_audit,
    claude_place,
    demo,
    onboarding,
    report,
    run_reports,
    setup_check,
    task_files,
)
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
        "backup_nudge": onboarding.backup_nudge(session, settings),
    }


@router.post("/start/dismiss", status_code=204)
def dismiss(session: WriteSessionDep) -> None:
    onboarding.dismiss(session)


@router.post("/start/backup-nudge", status_code=204)
def backup_nudge_dismissed(session: WriteSessionDep) -> None:
    """Not now: Home doesn't ask to turn automatic backups on again."""
    onboarding.backup_nudge_done(session)


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


# --- Check my setup (Settings > Advanced) ---


@router.get("/setup-check")
def setup_check_status(session: SessionDep) -> dict[str, Any]:
    """Whether it's running and how far it's got, what it would send, and the last report."""
    return setup_check.in_app.status(session, current_settings())


@router.post("/setup-check", status_code=202)
def setup_check_start(session: SessionDep) -> dict[str, Any]:
    """Send the read-only requests through this install. The app polls GET /setup-check."""
    if not _claude_ready():
        raise HTTPException(422, detail="Connect Claude first, in Settings > Claude.")
    setup_check.in_app.start(current_settings())
    return setup_check.in_app.status(session, current_settings())


@router.post("/setup-check/stop", status_code=202)
def setup_check_stop(session: SessionDep) -> dict[str, Any]:
    """Finish the request under way and send no more."""
    setup_check.in_app.stop()
    return setup_check.in_app.status(session, current_settings())


@router.post("/setup-check/archive")
def setup_check_archive(session: WriteSessionDep) -> dict[str, int]:
    """Archive every conversation Check my setup started, and their tasks."""
    return {"archived": setup_check.archive(session, by=OWNER)}


# --- Their reports, and the Self-test's (services.run_reports) ---


@router.get("/run-reports/{kind}")
def run_reports_listed(kind: run_reports.Kind) -> list[dict[str, Any]]:
    """Every report Check my setup (or the Self-test) kept, newest first."""
    return list(run_reports.listed(current_settings(), kind))


@router.get("/run-reports/{kind}/{day}")
def run_report(kind: run_reports.Kind, day: str) -> dict[str, str]:
    """One day's report, to read again."""
    return {"day": day, "report": run_reports.read(current_settings(), kind, day)}


@router.post("/run-reports/{kind}/{day}/show")
def show_run_report(kind: run_reports.Kind, day: str) -> dict[str, bool]:
    """Desktop: show the report's file in its folder (with the earlier ones)."""
    settings = current_settings()
    if settings.mode != "desktop":
        raise HTTPException(
            422, detail="Files open on the computer Ringkeeper runs on: only in the desktop app."
        )
    task_files.show(run_reports.path(settings, kind, day), reveal=True)
    return {"ok": True}
