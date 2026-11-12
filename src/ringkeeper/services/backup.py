"""Backups (desktop): everything Ringkeeper knows, in one password-protected file.

The file holds the database (projects, clients, connections, agents, routines,
settings, history), every secret in the vault, and a manifest. It's encrypted
with a key made from the password chosen at export (scrypt), with AES-GCM; the
master key of this computer isn't in it, so it can be restored on another one.

Project folders are referenced, not copied: a project keeps its folder's path,
and restoring says which folders aren't on this computer yet.

Restoring checks the password and shows what comes in, then stages the database
and writes the secrets into this computer's vault. The staged database replaces
the current one the next time Ringkeeper starts (desktop.server), which happens
right away: the backend restarts itself. The database it replaces is kept beside
it (ringkeeper.db.before-restore).

Automatic backups: weekly, into a folder you choose, keeping the last N, with a
password kept in the vault.
"""

import io
import json
import os
import secrets
import sqlite3
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
from sqlalchemy.orm import Session

from ringkeeper import __version__
from ringkeeper.config import Settings, get_settings
from ringkeeper.errors import RingkeeperError, RuleViolationError
from ringkeeper.models import Setting
from ringkeeper.vault import Vault
from ringkeeper.vault.file import FileBackend

MAGIC = b"RKBACKUP1"
SALT_BYTES, NONCE_BYTES = 16, 12
SCRYPT_N, SCRYPT_R, SCRYPT_P = 2**15, 8, 1
MIN_PASSWORD = 8
SUFFIX = ".rkbackup"
PENDING = "restore-pending.db"
REPLACED = "ringkeeper.db.before-restore"
PASSWORD_REF = "file://ringkeeper/backup/password"  # noqa: S105 (where it is, not what)
AUTO_KEY = "backup.auto"
EVERY = timedelta(days=7)
MAX_BYTES = 200 * 1024 * 1024


def _key(password: str, salt: bytes) -> bytes:
    return Scrypt(salt=salt, length=32, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P).derive(
        password.encode()
    )


def _check_password(password: str) -> None:
    if len(password) < MIN_PASSWORD:
        raise RuleViolationError(f"Use a password of at least {MIN_PASSWORD} characters.")


def _database(settings: Settings) -> Path:
    url = settings.database_url or ""
    if not url.startswith("sqlite:///"):
        raise RuleViolationError(
            "Backups are in the desktop app. On a server, back up its Postgres database and "
            "data folder instead (see the server install guide)."
        )
    return Path(url.removeprefix("sqlite:///"))


def _sqlite_copy(source: Path) -> bytes:
    """A consistent copy of the live database (SQLite's online backup)."""
    with tempfile.TemporaryDirectory() as folder:
        target = Path(folder) / "copy.db"
        src = sqlite3.connect(source)
        dst = sqlite3.connect(target)
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()
        return target.read_bytes()


def _manifest(db: bytes) -> dict[str, Any]:
    """What's in a database: counts and projects with their folders."""
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "look.db"
        path.write_bytes(db)
        conn = sqlite3.connect(path)
        try:
            names = dict(conn.execute("SELECT slug, name FROM clients").fetchall())
            projects = [
                {"name": n, "client": names.get(o, o), "folder": f}
                for n, o, f in conn.execute(
                    "SELECT name, owner, workspace_path FROM projects ORDER BY name"
                ).fetchall()
            ]
            connections = [
                {"name": n, "client": names.get(o, o), "type": t}
                for n, o, t in conn.execute(
                    "SELECT name, owner, type FROM instances ORDER BY name"
                ).fetchall()
            ]
            count = {
                table: conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]  # noqa: S608 (fixed names)
                for table in ("agents", "routines", "tasks", "threads")
            }
        except sqlite3.Error:
            raise RuleViolationError(
                "That file doesn't hold a Ringkeeper database. Choose a Ringkeeper backup."
            ) from None
        finally:
            conn.close()
    return {
        "clients": sorted(set(names.values())),
        "projects": projects,
        "connections": connections,
        **count,
    }


def export(settings: Settings, password: str) -> bytes:
    """The whole backup, encrypted with `password`."""
    _check_password(password)
    db = _sqlite_copy(_database(settings))
    vault = FileBackend.from_settings(get_settings())  # the vault everyone else uses
    kept = {
        ref: vault.get(ref)
        for ref in sorted(vault.refs())
        if ref != PASSWORD_REF  # the automatic backups' own password stays here
    }
    manifest = {
        "format": 1, "app_version": __version__, "created_at": datetime.now(UTC).isoformat(),
        "secrets": len(kept), **_manifest(db),
    }  # fmt: skip
    plain = io.BytesIO()
    with zipfile.ZipFile(plain, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest, indent=1))
        archive.writestr("ringkeeper.db", db)
        archive.writestr("secrets.json", json.dumps(kept))
    salt, nonce = secrets.token_bytes(SALT_BYTES), secrets.token_bytes(NONCE_BYTES)
    sealed = AESGCM(_key(password, salt)).encrypt(nonce, plain.getvalue(), MAGIC)
    return MAGIC + salt + nonce + sealed


@dataclass(frozen=True)
class Opened:
    manifest: dict[str, Any]
    db: bytes
    secrets: dict[str, str]


def open_backup(blob: bytes, password: str) -> Opened:
    if len(blob) > MAX_BYTES:
        raise RuleViolationError("That file is too big to be a Ringkeeper backup.")
    if not blob.startswith(MAGIC) or len(blob) < len(MAGIC) + SALT_BYTES + NONCE_BYTES + 16:
        raise RuleViolationError("That isn't a Ringkeeper backup file (it ends in .rkbackup).")
    at = len(MAGIC)
    salt, nonce = blob[at : at + SALT_BYTES], blob[at + SALT_BYTES : at + SALT_BYTES + NONCE_BYTES]
    try:
        plain = AESGCM(_key(password, salt)).decrypt(
            nonce, blob[at + SALT_BYTES + NONCE_BYTES :], MAGIC
        )
    except InvalidTag:
        raise RuleViolationError(
            "That password doesn't open this backup. Check it and try again."
        ) from None
    with zipfile.ZipFile(io.BytesIO(plain)) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        db = archive.read("ringkeeper.db")
        kept = cast(dict[str, str], json.loads(archive.read("secrets.json")))
    return Opened(cast(dict[str, Any], manifest), db, kept)


def preview(blob: bytes, password: str) -> dict[str, Any]:
    """What restoring brings in, and which project folders aren't on this computer."""
    opened = open_backup(blob, password)
    found = dict(opened.manifest)
    found["projects"] = [
        {**p, "folder_here": bool(p.get("folder")) and Path(str(p["folder"])).is_dir()}
        for p in cast(list[dict[str, Any]], found.get("projects") or [])
    ]
    return found


def restore(settings: Settings, vault: Vault, blob: bytes, password: str) -> dict[str, Any]:
    """Put the secrets in this computer's vault and stage the database for the next start."""
    opened = open_backup(blob, password)
    current = _database(settings)
    for ref, value in opened.secrets.items():
        vault.store(ref, value)
    staged = current.with_name(PENDING)
    staged.write_bytes(opened.db)
    return preview(blob, password)


def apply_pending(data_dir: Path, database: Path | None = None) -> bool:
    """At startup, before anything opens the database: a staged restore replaces it."""
    database = database or data_dir / "ringkeeper.db"
    staged = database.with_name(PENDING)
    if not staged.exists():
        return False
    replaced = database.with_name(REPLACED)
    replaced.unlink(missing_ok=True)
    if database.exists():
        os.replace(database, replaced)
    for extra in ("-wal", "-shm"):  # the old database's journal mustn't touch the new one
        Path(f"{database}{extra}").unlink(missing_ok=True)
    os.replace(staged, database)
    return True


# --- Automatic backups ---


@dataclass(frozen=True)
class Auto:
    enabled: bool
    folder: str
    keep: int
    last: str | None
    last_error: str | None


def auto(session: Session) -> Auto:
    row = session.get(Setting, AUTO_KEY)
    value = row.value if row is not None else {}
    return Auto(
        bool(value.get("enabled")), str(value.get("folder") or ""), int(value.get("keep") or 4),
        value.get("last"), value.get("last_error"),
    )  # fmt: skip


def set_auto(
    session: Session, vault: Vault, *, enabled: bool, folder: str, keep: int, password: str | None
) -> Auto:
    if keep < 1 or keep > 52:
        raise RuleViolationError("Keep between 1 and 52 backups.")
    if enabled:
        if not folder.strip() or not Path(folder).is_absolute():
            raise RuleViolationError("Choose the folder for the backups (a full path).")
        if password:
            _check_password(password)
            vault.store(PASSWORD_REF, password)
        elif not _has_password(vault):
            raise RuleViolationError("Choose a password for the automatic backups.")
    now = auto(session)
    _save_auto(session, {"enabled": enabled, "folder": folder.strip(), "keep": keep,
                         "last": now.last, "last_error": now.last_error})  # fmt: skip
    return auto(session)


def _has_password(vault: Vault) -> bool:
    try:
        vault.resolve(PASSWORD_REF)
    except RingkeeperError:  # not stored (or unreadable): there's none
        return False
    return True


def _save_auto(session: Session, value: dict[str, Any]) -> None:
    row = session.get(Setting, AUTO_KEY)
    if row is None:
        session.add(Setting(key=AUTO_KEY, value=value))
    else:
        row.value = value
    session.flush()


def run_auto(
    session: Session, settings: Settings, vault: Vault, now: datetime | None = None
) -> Path | None:
    """The weekly backup, when it's due. Returns the file written (None: not due or off)."""
    now = now or datetime.now(UTC)
    current = auto(session)
    if not current.enabled or (current.last and datetime.fromisoformat(current.last) + EVERY > now):
        return None
    record: dict[str, Any] = {"enabled": True, "folder": current.folder, "keep": current.keep,
                              "last": now.isoformat(), "last_error": None}  # fmt: skip
    written: Path | None = None
    try:
        folder = Path(current.folder)
        folder.mkdir(parents=True, exist_ok=True)
        blob = export(settings, vault.resolve(PASSWORD_REF).reveal())
        written = folder / f"Ringkeeper-backup-{now:%Y-%m-%d-%H%M}{SUFFIX}"
        written.write_bytes(blob)
        old = sorted(folder.glob(f"Ringkeeper-backup-*{SUFFIX}"))
        for extra in old[: max(0, len(old) - current.keep)]:
            extra.unlink(missing_ok=True)
    except (OSError, RingkeeperError) as error:
        record["last_error"] = f"The weekly backup didn't work: {error}"
        written = None
    _save_auto(session, record)
    if written is not None:  # and to Google Drive, when an account is chosen
        from ringkeeper.services import backup_drive  # noqa: PLC0415 (it's optional)

        backup_drive.upload(session, vault, written, current.keep)
    return written
