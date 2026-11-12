"""Backups uploaded to Google Drive, so they leave this computer without a synced folder.

The same password-locked .rkbackup file the local copy is, uploaded into a "Ringkeeper
backups" folder Ringkeeper creates in that account's Drive, keeping the last few there (the
same number as on this computer). The password never leaves this computer, and Google can't
open the file. Only a Google connection that's the owner's own (shared, or their own client's)
and has "Drive: files Ringkeeper creates" can hold them: never a client's account.

Restoring is unchanged: on a new computer, download the file from Drive in a browser and pick
it in Settings, Backup.
"""

import asyncio
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
from ringkeeper.services import clients, google_oauth
from ringkeeper.vault import Vault

KEY = "backup-drive"  # {"connection", "folder", "last", "link", "error"}
FOLDER = "Ringkeeper backups"
FOLDER_TYPE = "application/vnd.google-apps.folder"
ACCESS = "drive_files"
BACKUP_TYPE = "application/octet-stream"
PREFIX = "Ringkeeper-backup-"


@dataclass(frozen=True)
class Drive:
    connection: str | None
    folder: str | None  # the "Ringkeeper backups" folder's id, once made
    last: str | None  # when the last upload finished
    link: str | None  # the last file in Drive
    error: str | None  # why the last upload didn't work


def state(session: Session) -> Drive:
    row = session.get(Setting, KEY)
    value = row.value if row is not None else {}
    return Drive(*(value.get(k) for k in ("connection", "folder", "last", "link", "error")))


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
    """Upload a backup that's already on this computer. None: no Google account chosen. A
    failure is kept (and said on Home and in Settings); the local copy stays either way."""
    now = state(session)
    if not now.connection:
        return None
    try:
        target = _target(session, vault, now.connection)
        folder, sent = asyncio.run(_send(target, now.folder, path, keep))
    except (RingkeeperError, ToolError, OSError) as error:
        return _save(session, error=f"The upload to Google Drive didn't work: {error}")
    return _save(
        session, folder=folder, last=datetime.now(UTC).isoformat(),
        link=sent.get("webViewLink"), error=None,
    )  # fmt: skip
