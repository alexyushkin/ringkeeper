"""Home's first steps (services.onboarding) and the demo client (services.demo)."""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from ringkeeper.api.auth import AdminDep
from ringkeeper.api.deps import OWNER, SessionDep, WriteSessionDep, require_owner
from ringkeeper.claude_auth import credential_ref
from ringkeeper.errors import RingkeeperError
from ringkeeper.services import admin_audit, demo, onboarding
from ringkeeper.services.runtime import current_settings
from ringkeeper.vault import get_vault

router = APIRouter(dependencies=[AdminDep, Depends(require_owner)])


def _claude_ready() -> bool:
    settings = current_settings()
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
        },
        "suggestions": onboarding.suggestions(session, settings),
        "demo": demo.summary(demo.current(session)),
        "demo_offered": settings.mode == "desktop",
        "tour_seen": onboarding.tour_seen(session),
    }


@router.post("/start/dismiss", status_code=204)
def dismiss(session: WriteSessionDep) -> None:
    onboarding.dismiss(session)


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
