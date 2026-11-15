"""Background work starting and finishing, said once for every screen: a "job" event.

Tasks, approvals and messages reach the app by database triggers. Work that isn't one of
those rows (the weekly backup, an upload to Google Drive, the connection check, a scan, a
download, the self-test) calls `changed()` when it starts and when it ends, in the session
that saves its result, so the event goes out when that commits. The app fetches every
screen's data again on it (web/lib/client/api.ts, useApi): no screen has to listen for its
own job. Kind and status only, as the triggers send ids only: the app fetches the rest.
"""

import json
import logging

from sqlalchemy.orm import Session

from ringkeeper.storage import events

logger = logging.getLogger(__name__)

TYPE = "job"
STARTED, DONE, FAILED = "started", "done", "failed"

# Every kind that's sent, so a test can check each one is.
BACKUP = "backup"  # the weekly backup, or Back up now
DRIVE_UPLOAD = "drive_upload"  # a backup uploaded to Google Drive
HEALTH = "health"  # the connection check
DIGEST = "digest"  # the morning digest
SETUP_CHECK = "setup_check"  # Check my setup
SELF_TEST = "self_test"
OCR = "ocr"  # the scan reader's download
VOICE = "voice"  # the voice model's download
INSTALL = "install"  # a local MCP server's install
KINDS = (BACKUP, DRIVE_UPLOAD, HEALTH, DIGEST, SETUP_CHECK, SELF_TEST, OCR, VOICE, INSTALL)


def payload(kind: str, status: str = DONE) -> str:
    return json.dumps({"type": TYPE, "kind": kind, "status": status})


def changed(session: Session, kind: str, status: str = DONE) -> None:
    """Tell the app, once this session commits."""
    events.notify(session, events.LIVE, payload(kind, status))


def announce(kind: str, status: str = DONE) -> None:
    """From work that has no session of its own (a download, a thread). Never raises: a
    screen that isn't told still fetches again when the window comes back."""
    from ringkeeper.db import session_scope  # noqa: PLC0415 (no database at import time)

    try:
        with session_scope() as session:
            changed(session, kind, status)
    except Exception:
        logger.exception("couldn't tell the app that %s %s", kind, status)
