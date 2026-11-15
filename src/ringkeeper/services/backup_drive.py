"""Backups uploaded to Google Drive, so they leave this computer without a synced folder.

The same password-locked .rkbackup file the local copy is, uploaded into a "Ringkeeper
backups" folder Ringkeeper creates in that account's Drive, keeping the last few there (the
same number as on this computer). The password never leaves this computer, and Google can't
open the file. Only a Google connection that's the owner's own (shared, or their own client's)
and has "Drive: files Ringkeeper creates" can hold them: never a client's account.

Restoring is unchanged: on a new computer, download the file from Drive in a browser and pick
it in Settings, Backup.

An upload runs in the background (`start`): the app shows "Uploading..." from when it starts
and the result when it ends, told by a "job" event (services.jobs), so the screen never waits
on Google or goes stale.
"""

import asyncio
import logging
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import httpx2
from sqlalchemy import select
from sqlalchemy.orm import Session

from ringkeeper.adapters import google, rest
from ringkeeper.adapters.base import Target, ToolError
from ringkeeper.errors import RingkeeperError, RuleViolationError
from ringkeeper.models import Instance, InstanceType, Setting
from ringkeeper.services import clients, google_oauth, jobs
from ringkeeper.vault import Vault, get_vault

logger = logging.getLogger(__name__)

KEY = "backup-drive"  # {"connection", "folder", "last", "link", "error", "uploading"}
FOLDER = "Ringkeeper backups"
FOLDER_TYPE = "application/vnd.google-apps.folder"
ACCESS = "drive_files"
BACKUP_TYPE = "application/octet-stream"
PREFIX = "Ringkeeper-backup-"
SUFFIX = ".rkbackup"  # services.backup.SUFFIX
_busy = threading.Lock()  # one upload at a time; held while one runs in this process


@dataclass(frozen=True)
class Drive:
    connection: str | None
    folder: str | None  # the "Ringkeeper backups" folder's id, once made
    last: str | None  # when the last upload finished
    link: str | None  # the last file in Drive
    error: str | None  # why the last upload didn't work
    uploading: str | None = None  # when the upload running now started
    file: str | None = None  # the last file uploaded


def state(session: Session) -> Drive:
    row = session.get(Setting, KEY)
    value = row.value if row is not None else {}
    found = Drive(*(value.get(k) for k in ("connection", "folder", "last", "link", "error",
                                           "uploading", "file")))  # fmt: skip
    if found.uploading and not _busy.locked():  # the app quit while one ran: it's not running
        return Drive(**{**found.__dict__, "uploading": None})
    return found


def _save(session: Session, **changes: Any) -> Drive:
    row = session.get(Setting, KEY)
    value = {**(row.value if row is not None else {}), **changes}
    if row is None:
        session.add(Setting(key=KEY, value=value))
    else:
        row.value = value
    session.flush()
    return state(session)


def _owners_own(session: Session, instance: Instance) -> bool:
    """Shared (the owner's own tools) or in the owner's own client: never a client's account."""
    return instance.shared or instance.owner == clients.own_client(session)


def _can_create_files(instance: Instance) -> bool:
    return ACCESS in cast(list[str], (instance.config or {}).get("access") or [])


def accounts(session: Session) -> list[dict[str, Any]]:
    """The owner's own Google accounts, each with whether it can hold the backups now."""
    found = session.scalars(
        select(Instance).where(Instance.type == InstanceType.GOOGLE).order_by(Instance.name)
    )
    return [
        {"name": i.name, "title": i.title or i.name, "shared": i.shared, "owner": i.owner,
         "ready": _can_create_files(i), "access": list((i.config or {}).get("access") or [])}
        for i in found if _owners_own(session, i)
    ]  # fmt: skip


def choose(session: Session, name: str | None) -> Drive:
    """Upload to this Google connection from now on (None: stop uploading)."""
    if name is None:
        return _save(session, connection=None, folder=None, error=None)
    instance = session.scalar(select(Instance).where(Instance.name == name))
    if instance is None or instance.type is not InstanceType.GOOGLE:
        raise RuleViolationError("Choose one of your Google connections.")
    if not _owners_own(session, instance):
        raise RuleViolationError(
            "That's a client's Google account: backups hold every client's data, so they go "
            "only to your own account (a shared one, or your own client's)."
        )
    if not _can_create_files(instance):
        raise RuleViolationError(
            f"'{instance.title or instance.name}' wasn't given \"Drive: files Ringkeeper "
            'creates". Add it with Change access, then choose it again.'
        )
    previous = state(session)
    folder = previous.folder if previous.connection == name else None
    return _save(session, connection=name, folder=folder, error=None)


def _target(session: Session, vault: Vault, name: str) -> Target:
    instance = session.scalar(select(Instance).where(Instance.name == name))
    if instance is None:
        raise RuleViolationError(f"The Google connection '{name}' isn't there any more.")
    secret = google_oauth.connection_secret(vault, InstanceType.GOOGLE, name, instance.secret_ref)
    return Target(name, instance.env, None, secret, dict(instance.config or {}))


def _client(target: Target, base: str) -> httpx2.AsyncClient:
    return httpx2.AsyncClient(
        base_url=base,
        headers={"Authorization": f"Bearer {google.access_token(target)}"},
        timeout=120.0,
        transport=google.transport,
    )


async def _folder(target: Target, known: str | None) -> str:
    """The "Ringkeeper backups" folder Ringkeeper made (with Drive's files access it can only
    see its own), made when it isn't there."""
    if known:
        found = await rest.call(_client(target, google.DRIVE), "Google", "GET", f"/files/{known}",
                                params={"fields": "id,trashed"})  # fmt: skip
        if not found.get("trashed"):
            return known
    query = f"name = '{FOLDER}' and mimeType = '{FOLDER_TYPE}' and trashed = false"
    listed = await rest.call(_client(target, google.DRIVE), "Google", "GET", "/files",
                             params={"q": query, "fields": "files(id)", "pageSize": 1})  # fmt: skip
    files = cast(list[dict[str, Any]], listed.get("files") or [])
    if files:
        return str(files[0]["id"])
    made = await rest.call(_client(target, google.DRIVE), "Google", "POST", "/files",
                           params={"fields": "id"},
                           json={"name": FOLDER, "mimeType": FOLDER_TYPE})  # fmt: skip
    return str(made["id"])


async def _upload(target: Target, folder: str, name: str, blob: bytes) -> dict[str, Any]:
    """One file, by Drive's resumable upload (a backup can be bigger than one request)."""
    meta = {"name": name, "parents": [folder], "mimeType": BACKUP_TYPE}
    async with _client(target, google.UPLOAD) as http:
        try:
            started = await http.post(
                # The whole address: no slash after "files", which the base address would add.
                f"{google.UPLOAD}?uploadType=resumable&fields=id,name,webViewLink",
                json=meta,
                headers={"X-Upload-Content-Type": BACKUP_TYPE,
                         "X-Upload-Content-Length": str(len(blob))},
            )  # fmt: skip
            if started.status_code >= 400:
                raise ToolError(f"Google refused the upload: {rest.error_message(started)}")
            where = started.headers.get("location")
            if not where:
                raise ToolError("Google didn't say where to send the file.")
            sent = await http.put(where, content=blob, headers={"Content-Type": BACKUP_TYPE})
        except httpx2.HTTPError as error:
            raise ToolError(f"cannot reach Google: {type(error).__name__}") from None
    if sent.status_code >= 400:
        raise ToolError(f"Google refused the file: {rest.error_message(sent)}")
    return cast(dict[str, Any], sent.json())


async def _rotate(target: Target, folder: str, keep: int) -> int:
    """Keep the newest `keep` backups in the folder; the older ones are deleted."""
    query = f"'{folder}' in parents and name contains '{PREFIX}' and trashed = false"
    listed = await rest.call(_client(target, google.DRIVE), "Google", "GET", "/files",
                             params={"q": query, "orderBy": "createdTime desc",
                                     "fields": "files(id,name)", "pageSize": 100})  # fmt: skip
    old = cast(list[dict[str, Any]], listed.get("files") or [])[keep:]
    for file in old:
        await rest.call(_client(target, google.DRIVE), "Google", "DELETE", f"/files/{file['id']}")
    return len(old)


async def _send(
    target: Target, folder: str | None, path: Path, keep: int
) -> tuple[str, dict[str, Any]]:
    where = await _folder(target, folder)
    sent = await _upload(target, where, path.name, path.read_bytes())
    await _rotate(target, where, keep)
    return where, sent


def upload(session: Session, vault: Vault, path: Path, keep: int) -> Drive | None:
    """Upload a backup that's already on this computer, now, in this session (`start` runs it
    in the background without holding one). None: no Google account chosen. A failure is
    kept (and said on Home and in Settings); the local copy stays either way."""
    now = state(session)
    if not now.connection:
        return None
    return _finish(session, path, _transfer(session, vault, now, path, keep))


def _transfer(
    session: Session, vault: Vault, now: Drive, path: Path, keep: int
) -> tuple[str, dict[str, Any]] | str:
    """The upload: (folder, the file) or why it didn't work."""
    try:
        target = _target(session, vault, now.connection or "")
    except (RingkeeperError, ToolError) as error:
        return str(error)
    return _send_now(target, now.folder, path, keep)


def _send_now(
    target: Target, folder: str | None, path: Path, keep: int
) -> tuple[str, dict[str, Any]] | str:
    try:
        return asyncio.run(_send(target, folder, path, keep))
    except (RingkeeperError, ToolError, OSError) as error:
        return str(error)


def _finish(session: Session, path: Path, result: tuple[str, dict[str, Any]] | str) -> Drive:
    if isinstance(result, str):
        jobs.changed(session, jobs.DRIVE_UPLOAD, jobs.FAILED)
        return _save(
            session, uploading=None, error=f"The upload to Google Drive didn't work: {result}"
        )
    folder, sent = result
    jobs.changed(session, jobs.DRIVE_UPLOAD, jobs.DONE)
    return _save(
        session, folder=folder, last=datetime.now(UTC).isoformat(), file=path.name,
        link=sent.get("webViewLink"), error=None, uploading=None,
    )  # fmt: skip


def start(session: Session, path: Path, keep: int) -> threading.Thread | None:
    """Upload in the background: "uploading" is saved in this session (the app shows it once
    it commits), and the result in the upload's own. None: no Google account chosen."""
    now = state(session)
    if not now.connection:
        return None
    if not _busy.acquire(blocking=False):
        raise RuleViolationError("An upload to Google Drive is already running.")
    try:
        _save(session, uploading=datetime.now(UTC).isoformat(), error=None)
        jobs.changed(session, jobs.DRIVE_UPLOAD, jobs.STARTED)
        thread = threading.Thread(
            target=_run, args=(now, path, keep), daemon=True, name="rk-drive-upload"
        )
        thread.start()
    except BaseException:
        _busy.release()
        raise
    return thread


def _run(now: Drive, path: Path, keep: int) -> None:
    """No session is held while the file goes to Google: on SQLite a writing session holds
    the one write lock, and an upload can take minutes. `now` is what the starting session
    saw: it may not have committed yet."""
    from ringkeeper.db import session_scope  # noqa: PLC0415 (no database at import time)

    try:
        with session_scope(commit=False) as session:
            try:
                ready: Target | str = _target(session, get_vault(), now.connection or "")
            except (RingkeeperError, ToolError) as error:
                ready = str(error)
        result = ready if isinstance(ready, str) else _send_now(ready, now.folder, path, keep)
        # Waits for the session that started it to commit (its "uploading").
        with session_scope() as session:
            _finish(session, path, result)
    except Exception:
        logger.exception("the upload to Google Drive failed")
        try:
            with session_scope() as session:
                _save(session, uploading=None, error="The upload to Google Drive stopped.")
                jobs.changed(session, jobs.DRIVE_UPLOAD, jobs.FAILED)
        except Exception:
            logger.exception("couldn't save that the upload stopped")
    finally:
        _busy.release()


def newest(folders: list[Path]) -> Path | None:
    """The newest backup on this computer, in any of these folders."""
    found: list[Path] = []
    for folder in folders:
        try:
            found += [p for p in folder.glob(f"{PREFIX}*{SUFFIX}") if p.is_file()]
        except OSError:
            continue
    return max(found, key=lambda p: p.stat().st_mtime, default=None)
