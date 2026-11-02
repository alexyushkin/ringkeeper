"""Backups (services.backup): one password-protected file, restored on another computer."""

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ringkeeper.config import Settings
from ringkeeper.db import session_scope
from ringkeeper.errors import RuleViolationError
from ringkeeper.services import backup
from ringkeeper.vault import get_vault

PASSWORD = "correct horse battery"


def desktop_db(tmp_path: Path) -> tuple[Settings, Path]:
    """A desktop-style SQLite database with a client, a project and a connection."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    db = tmp_path / "ringkeeper.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE clients (slug TEXT, name TEXT);
        CREATE TABLE projects (name TEXT, owner TEXT, workspace_path TEXT);
        CREATE TABLE instances (name TEXT, owner TEXT, type TEXT);
        CREATE TABLE agents (id INTEGER); CREATE TABLE routines (id INTEGER);
        CREATE TABLE tasks (id INTEGER); CREATE TABLE threads (id INTEGER);
        INSERT INTO clients VALUES ('acme', 'Acme Inc');
        """
    )
    conn.execute(
        "INSERT INTO projects VALUES ('acme-web', 'acme', ?)", (str(tmp_path / "acme-web"),)
    )
    conn.execute("INSERT INTO instances VALUES ('acme-db', 'acme', 'supabase')")
    conn.commit()
    conn.close()
    return Settings(mode="desktop", database_url=f"sqlite:///{db.as_posix()}"), db


def test_a_backup_opens_only_with_its_password_and_says_what_comes_in(
    data_dir: Path, tmp_path: Path
) -> None:
    settings, _ = desktop_db(tmp_path)
    get_vault().store("file://ringkeeper/connections/acme-db", "postgresql://u:p@db.example/x")
    blob = backup.export(settings, PASSWORD)
    assert blob.startswith(backup.MAGIC)
    assert b"acme-web" not in blob  # it's encrypted
    assert b"db.example" not in blob
    with pytest.raises(RuleViolationError, match="doesn't open this backup"):
        backup.preview(blob, "not the password")
    with pytest.raises(RuleViolationError, match="isn't a Ringkeeper backup"):
        backup.preview(b"hello", PASSWORD)
    seen = backup.preview(blob, PASSWORD)
    assert seen["clients"] == ["Acme Inc"]
    assert seen["projects"] == [
        {"name": "acme-web", "client": "Acme Inc", "folder": str(tmp_path / "acme-web"),
         "folder_here": False}
    ]  # fmt: skip
    assert seen["connections"] == [{"name": "acme-db", "client": "Acme Inc", "type": "supabase"}]
    assert seen["secrets"] == 1


def test_a_short_password_is_refused(data_dir: Path, tmp_path: Path) -> None:
    settings, _ = desktop_db(tmp_path)
    with pytest.raises(RuleViolationError, match="at least 8"):
        backup.export(settings, "short")


def test_restoring_brings_secrets_now_and_the_database_at_the_next_start(
    data_dir: Path, tmp_path: Path
) -> None:
    settings, _ = desktop_db(tmp_path / "old")
    ref = "file://ringkeeper/connections/acme-db"
    get_vault().store(ref, "postgresql://u:p@db.example/x")
    blob = backup.export(settings, PASSWORD)
    get_vault().delete(ref)

    new_dir = tmp_path / "new"
    new_dir.mkdir()
    fresh = new_dir / "ringkeeper.db"
    sqlite3.connect(fresh).close()
    here = Settings(mode="desktop", database_url=f"sqlite:///{fresh.as_posix()}")
    backup.restore(here, get_vault(), blob, PASSWORD)
    assert get_vault().resolve(ref).reveal() == "postgresql://u:p@db.example/x"
    assert (new_dir / backup.PENDING).exists()

    assert backup.apply_pending(new_dir, fresh) is True
    names = sqlite3.connect(fresh).execute("SELECT name FROM projects").fetchall()
    assert names == [("acme-web",)]
    assert (new_dir / backup.REPLACED).exists()  # what it replaced is kept
    assert backup.apply_pending(new_dir, fresh) is False  # once


def test_a_server_points_at_its_own_backups(data_dir: Path) -> None:
    server = Settings(database_url="postgresql+psycopg://x@db/x")
    with pytest.raises(RuleViolationError, match="desktop app"):
        backup.export(server, PASSWORD)


def test_weekly_backups_keep_the_last_few(
    database_url: str, data_dir: Path, tmp_path: Path
) -> None:
    settings, _ = desktop_db(tmp_path)
    folder = tmp_path / "backups"
    with session_scope() as session:
        with pytest.raises(RuleViolationError, match="password"):
            backup.set_auto(
                session, get_vault(), enabled=True, folder=str(folder), keep=2, password=None
            )
        backup.set_auto(
            session, get_vault(), enabled=True, folder=str(folder), keep=2, password=PASSWORD
        )
        start = datetime(2026, 11, 2, 9, tzinfo=UTC)
        for week in range(3):
            assert backup.run_auto(session, settings, get_vault(), start + timedelta(weeks=week))
        assert (
            backup.run_auto(session, settings, get_vault(), start + timedelta(weeks=2, days=1))
            is None
        )
        kept = sorted(p.name for p in folder.iterdir())
        assert len(kept) == 2
        assert kept[-1].startswith("Ringkeeper-backup-2026-11-16")
        assert backup.auto(session).last_error is None
    # The automatic backups' own password isn't in the file.
    opened = backup.open_backup((folder / kept[-1]).read_bytes(), PASSWORD)
    assert backup.PASSWORD_REF not in opened.secrets
