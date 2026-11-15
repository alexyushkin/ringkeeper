"""Background work a screen shows: when it starts or finishes, every open window is told.

Tasks, approvals and messages tell the app's windows by themselves (database triggers send
their rows' changes on the event stream). Work that isn't one of those rows, such as the
weekly backup, a Google Drive upload or the daily connection check, says so here, as a "job"
event with its kind. The web app has one table of which of its reads each kind changes
(web/lib/client/refresh.ts), and every screen showing one fetches it again.
"""

import json

from sqlalchemy.orm import Session

from ringkeeper.storage import events

CHANNEL = "ringkeeper_events"  # what the event stream sends (api.live_routes)

BACKUP = "backup"  # a backup file was written (the weekly one, or one made in Settings)
UPLOAD = "backup_upload"  # a backup's upload to Google Drive started or ended
HEALTH = "health"  # the daily connection check finished

STARTED, DONE, FAILED = "started", "done", "failed"


def announce(session: Session, kind: str, status: str) -> None:
    """Sent once this session's transaction commits (dropped on a rollback)."""
    payload = json.dumps({"type": "job", "kind": kind, "status": status})
    events.notify(session, CHANNEL, payload)
