"""The Open button's address: a page in Ringkeeper.

Server install: the web app's public origin (`web_origin`), when it's set.
Desktop app: an address on this computer only, the routines' webhook port
(`hooks_port`), signed so a website can't make the window jump around. Opening
it brings the app window up at that page (desktop.opener); it never signs a
browser in. On a phone it doesn't open: Open is for when you're at the computer.
"""

import hashlib
import hmac
import secrets
import uuid
from urllib.parse import quote

from sqlalchemy.orm import Session

from ringkeeper.config import Settings
from ringkeeper.models import Decision, Setting
from ringkeeper.services import inbox

KEY = "desktop.open_key"


def _key(session: Session) -> bytes:
    row = session.get(Setting, KEY)
    if row is None:
        row = Setting(key=KEY, value={"key": secrets.token_urlsafe(32)})
        session.add(row)
        session.flush()
    return str(row.value["key"]).encode()


def signature(session: Session, path: str) -> str:
    return hmac.new(_key(session), path.encode(), hashlib.sha256).hexdigest()[:32]


def signed(session: Session, path: str, sig: str) -> bool:
    return hmac.compare_digest(signature(session, path), sig)


def task_page(settings: Settings, task_id: uuid.UUID | str) -> str:
    return f"/task?id={task_id}" if settings.mode == "desktop" else f"/tasks/{task_id}"


def thread_page(settings: Settings, thread_id: uuid.UUID | str) -> str:
    return f"/thread?id={thread_id}" if settings.mode == "desktop" else f"/threads/{thread_id}"


def routine_page(routine_id: uuid.UUID | str) -> str:
    return f"/routine?id={routine_id}"


def decision_page(settings: Settings, decision: Decision) -> str:
    """Approvals for actions; a question where it was asked (its thread or task)."""
    if inbox.is_approval(decision):
        return "/approvals"
    if decision.thread_id is not None:
        return thread_page(settings, decision.thread_id)
    if decision.task_id is not None:
        return task_page(settings, decision.task_id)
    return "/approvals"


def open_url(session: Session, settings: Settings, path: str) -> str | None:
    if settings.mode == "desktop":
        sig = signature(session, path)
        return f"http://127.0.0.1:{settings.hooks_port}/open?to={quote(path, safe='')}&sig={sig}"
    if settings.web_origin:
        return settings.web_origin.rstrip("/") + path
    return None
