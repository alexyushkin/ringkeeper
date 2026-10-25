"""What the desktop app shell asks for: the tray's state, in one cheap call.

Mounted only in desktop mode. The shell reaches it through the front door
with the launch secret (/desktop/api/..., see desktop/server.py); the window
could too, but has no use for it.
"""

from typing import Any

from fastapi import APIRouter, Depends

from ringkeeper.api.auth import AdminDep
from ringkeeper.api.deps import SessionDep, require_owner
from ringkeeper.services import inbox, runtime
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
