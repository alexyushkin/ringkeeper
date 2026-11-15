"""Backups uploaded to Google Drive (services.backup_drive): into a folder Ringkeeper makes, the
last few kept there, a failure said and the local copy kept, and never a client's account."""

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx2
import pytest

from ringkeeper.adapters import google
from ringkeeper.db import session_scope
from ringkeeper.errors import RuleViolationError
from ringkeeper.models import InstanceType, Setting
from ringkeeper.services import backup, backup_drive, clients, jobs
from ringkeeper.storage import events
from ringkeeper.vault import get_vault
from tests.helpers import make_instance
from tests.test_backup import desktop_db

SECRET = json.dumps(
    {
        "email": "me@example.com",
        "refresh_token": "rt",
        "access_token": "at",
        "expires_at": 4102444800,
        "app": "app-1",
    }
)


class FakeDrive:
    """The bits of Drive's API backups use: files Ringkeeper made, and uploads."""

    def __init__(self) -> None:
        self.files: dict[str, dict[str, Any]] = {}
        self.uploads = 0
        self.broken = False
        self.clock = 0

    def add(
        self, name: str, parent: str | None = None, kind: str = backup_drive.BACKUP_TYPE
    ) -> str:
        self.clock += 1
        ident = f"f{self.clock:03d}"
        self.files[ident] = {
            "id": ident,
            "name": name,
            "mimeType": kind,
            "parent": parent,
            "created": self.clock,
            "trashed": False,
        }
        return ident

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        path, params = request.url.path, request.url.params
        assert request.headers["Authorization"] == "Bearer at"
        if self.broken:
            return httpx2.Response(503, json={"error": {"message": "Drive is down"}})
        if path == "/upload/drive/v3/files" and request.method == "POST":
            meta = json.loads(request.content)
            assert params["uploadType"] == "resumable"
            return httpx2.Response(
                200, headers={"location": f"https://up.example/{meta['name']}|{meta['parents'][0]}"}
            )
        if request.url.host == "up.example":
            name, parent = path.lstrip("/").split("|")
            self.uploads += 1
            ident = self.add(name, parent)
            return httpx2.Response(
                200,
                json={"id": ident, "name": name, "webViewLink": f"https://drive.example/{ident}"},
            )
        if path == "/drive/v3/files" and request.method == "POST":
            made = json.loads(request.content)
            return httpx2.Response(200, json={"id": self.add(made["name"], None, made["mimeType"])})
        if path == "/drive/v3/files" and request.method == "GET":
            query = params["q"]
            found = [f for f in self.files.values() if not f["trashed"]]
            if "mimeType = 'application/vnd.google-apps.folder'" in query:
                found = [f for f in found if f["mimeType"] == backup_drive.FOLDER_TYPE]
            if "' in parents" in query:
                parent = query.split("'")[1]
                found = [f for f in found if f["parent"] == parent]
            found.sort(key=lambda f: -f["created"])
            return httpx2.Response(
                200, json={"files": [{"id": f["id"], "name": f["name"]} for f in found]}
            )
        if path.startswith("/drive/v3/files/") and request.method == "DELETE":
            del self.files[path.rsplit("/", 1)[1]]
            return httpx2.Response(204)
        if path.startswith("/drive/v3/files/") and request.method == "GET":
            found = self.files.get(path.rsplit("/", 1)[1])
            return httpx2.Response(
                200 if found else 404, json=found or {"error": {"message": "no"}}
            )
        return httpx2.Response(404, json={"error": {"message": path}})


@pytest.fixture
def drive(database_url: str, monkeypatch: pytest.MonkeyPatch) -> FakeDrive:
    fake = FakeDrive()
    monkeypatch.setattr(google, "transport", httpx2.MockTransport(fake.handle))
    get_vault().store("file://google/mine", SECRET)
    get_vault().store("file://google/acme", SECRET)
    with session_scope() as session:
        clients.ensure(session, "Me")
        clients.set_own(session, "me", True)
        make_instance(
            session,
            "my-google",
            InstanceType.GOOGLE,
            owner="me",
            secret_ref="file://google/mine",
            config={"access": ["drive_files"]},
        )
        make_instance(
            session,
            "acme-google",
            InstanceType.GOOGLE,
            owner="acme",
            secret_ref="file://google/acme",
            config={"access": ["drive_files"]},
        )
        make_instance(
            session,
            "my-mail",
            InstanceType.GOOGLE,
            owner="me",
            secret_ref="file://google/mine",
            config={"access": ["mail_read"]},
        )
    return fake


def backup_file(tmp_path: Path, n: int) -> Path:
    path = tmp_path / f"Ringkeeper-backup-2026-11-{n:02d}-0900.rkbackup"
    path.write_bytes(b"locked" * 100)
    return path


def test_a_backup_goes_into_a_folder_ringkeeper_makes(drive: FakeDrive, tmp_path: Path) -> None:
    with session_scope() as session:
        backup_drive.choose(session, "my-google")
        done = backup_drive.upload(session, get_vault(), backup_file(tmp_path, 1), keep=4)
    assert done is not None
    assert done.error is None
    assert done.link == "https://drive.example/f002"
    assert done.last is not None
    folder = next(f for f in drive.files.values() if f["mimeType"] == backup_drive.FOLDER_TYPE)
    assert folder["name"] == "Ringkeeper backups"
    names = [f["name"] for f in drive.files.values() if f["parent"] == folder["id"]]
    assert names == ["Ringkeeper-backup-2026-11-01-0900.rkbackup"]
    with session_scope() as session:  # the same folder next time
        backup_drive.upload(session, get_vault(), backup_file(tmp_path, 2), keep=4)
    assert sum(1 for f in drive.files.values() if f["mimeType"] == backup_drive.FOLDER_TYPE) == 1


def test_only_the_last_few_are_kept_there(drive: FakeDrive, tmp_path: Path) -> None:
    with session_scope() as session:
        backup_drive.choose(session, "my-google")
        for n in range(1, 7):
            backup_drive.upload(session, get_vault(), backup_file(tmp_path, n), keep=3)
    kept = sorted(
        f["name"] for f in drive.files.values() if f["mimeType"] == backup_drive.BACKUP_TYPE
    )
    assert kept == [f"Ringkeeper-backup-2026-11-{n:02d}-0900.rkbackup" for n in (4, 5, 6)]


def test_a_failed_upload_is_said_and_the_local_copy_stays(drive: FakeDrive, tmp_path: Path) -> None:
    path = backup_file(tmp_path, 1)
    drive.broken = True
    with session_scope() as session:
        backup_drive.choose(session, "my-google")
        done = backup_drive.upload(session, get_vault(), path, keep=4)
    assert done is not None
    assert done.error is not None
    assert done.error.startswith("The upload to Google Drive didn't work")
    assert path.exists()  # the local copy is kept
    drive.broken = False
    with session_scope() as session:  # the next one that works clears it
        assert backup_drive.upload(session, get_vault(), path, keep=4).error is None  # type: ignore[union-attr]


@pytest.mark.usefixtures("drive")
def test_a_client_s_google_account_is_never_used() -> None:
    with session_scope() as session:
        with pytest.raises(RuleViolationError, match="client's Google account"):
            backup_drive.choose(session, "acme-google")
        with pytest.raises(RuleViolationError, match="Drive: files Ringkeeper creates"):
            backup_drive.choose(session, "my-mail")
        listed = {a["name"]: a["ready"] for a in backup_drive.accounts(session)}
    assert listed == {"my-google": True, "my-mail": False}  # never acme's


@pytest.mark.usefixtures("drive")
def test_nothing_is_uploaded_until_an_account_is_chosen(tmp_path: Path) -> None:
    with session_scope() as session:
        assert backup_drive.upload(session, get_vault(), backup_file(tmp_path, 1), keep=4) is None


# --- Showing it as it happens: "Uploading...", then how it went (services.jobs) ---


def heard(subscription: events.Subscription) -> list[tuple[str, str]]:
    """The job events sent so far: (kind, status)."""
    found: list[tuple[str, str]] = []
    for _, payload in subscription.get(timeout=2.0) + subscription.get(timeout=0.2):
        event = json.loads(payload)
        if event.get("type") == "job":
            found.append((event["kind"], event["status"]))
    return found


@pytest.fixture
def folder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The default backup folder, here: never the real Documents folder."""
    place = tmp_path / "Ringkeeper backups"
    place.mkdir()
    monkeypatch.setattr(backup, "default_folder", lambda: place)
    return place


def test_an_upload_says_it_started_and_how_it_ended(
    drive: FakeDrive, folder: Path, database_url: str
) -> None:
    seen_while_going: list[str | None] = []
    upload = drive.handle

    def watching(request: httpx2.Request) -> httpx2.Response:
        if request.url.host == "up.example":  # the file itself is on its way
            with session_scope(commit=False) as session:  # a read, beside the upload's
                seen_while_going.append(backup_drive.state(session).uploading)
        return upload(request)

    google.transport = httpx2.MockTransport(watching)
    with session_scope() as session:
        backup_drive.choose(session, "my-google")
    subscription = events.subscribe(database_url, [jobs.CHANNEL])
    try:
        done = backup_drive.upload_now(get_vault(), backup_file(folder, 1))
        said = heard(subscription)
    finally:
        subscription.close()
    assert done is not None
    assert done.error is None
    assert seen_while_going
    assert seen_while_going[0] is not None  # "Uploading..." meanwhile
    assert said == [(jobs.UPLOAD, jobs.STARTED), (jobs.UPLOAD, jobs.DONE)]
    with session_scope() as session:
        now = backup_drive.state(session)
    assert (now.uploading, now.file) == (None, "Ringkeeper-backup-2026-11-01-0900.rkbackup")


def test_a_failed_upload_says_so_too(drive: FakeDrive, folder: Path, database_url: str) -> None:
    drive.broken = True
    with session_scope() as session:
        backup_drive.choose(session, "my-google")
    subscription = events.subscribe(database_url, [jobs.CHANNEL])
    try:
        done = backup_drive.upload_now(get_vault(), backup_file(folder, 1))
        said = heard(subscription)
    finally:
        subscription.close()
    assert done is not None
    assert done.error is not None
    assert said == [(jobs.UPLOAD, jobs.STARTED), (jobs.UPLOAD, jobs.FAILED)]


def test_upload_now_sends_the_newest_backup_on_this_computer(
    drive: FakeDrive, folder: Path
) -> None:
    older, newer = backup_file(folder, 1), backup_file(folder, 2)
    os.utime(older, (1_700_000_000, 1_700_000_000))
    os.utime(newer, (1_800_000_000, 1_800_000_000))
    with session_scope() as session:
        assert backup.newest_local(session) == newer
        backup_drive.choose(session, "my-google")
    done = backup_drive.upload_now(get_vault())
    assert done is not None
    assert done.file == newer.name
    assert [
        f["name"] for f in drive.files.values() if f["mimeType"] == backup_drive.BACKUP_TYPE
    ] == [newer.name]


def test_a_backup_saved_elsewhere_is_found_too(
    folder: Path, tmp_path: Path, database_url: str
) -> None:
    elsewhere = tmp_path / "Dropbox"
    elsewhere.mkdir()
    saved = backup_file(elsewhere, 3)
    with session_scope() as session:
        assert backup.newest_local(session) is None
        backup.remember(session, saved)
        assert backup.newest_local(session) == saved


def test_nothing_goes_up_without_an_account_or_a_backup(drive: FakeDrive, folder: Path) -> None:
    assert backup_drive.upload_now(get_vault()) is None  # no account chosen
    with session_scope() as session:
        backup_drive.choose(session, "my-google")
    assert backup_drive.upload_now(get_vault()) is None  # no backup here yet
    assert drive.uploads == 0


def test_an_upload_cut_off_isnt_shown_as_going_on(database_url: str) -> None:
    started = datetime(2026, 11, 15, 10, 0, tzinfo=UTC)
    with session_scope() as session:
        session.add(Setting(key=backup_drive.KEY, value={"uploading": started.isoformat()}))
    with session_scope() as session:
        assert backup_drive.state(session, started + timedelta(minutes=5)).uploading is not None
        assert backup_drive.state(session, started + timedelta(hours=1)).uploading is None


def test_a_weekly_backup_says_it_finished(folder: Path, tmp_path: Path, database_url: str) -> None:
    weekly = tmp_path / "weekly"
    desktop, _ = desktop_db(tmp_path)  # a backup is the desktop app's database
    with session_scope() as session:
        backup.set_auto(session, get_vault(), enabled=True, folder=str(weekly), keep=4,
                        password="a long password")  # fmt: skip
    subscription = events.subscribe(database_url, [jobs.CHANNEL])
    try:
        with session_scope() as session:
            written = backup.run_auto(session, desktop, get_vault(), force=True)
        said = heard(subscription)
    finally:
        subscription.close()
    assert written is not None
    assert written.parent == weekly
    assert said == [(jobs.BACKUP, jobs.DONE)]
    with session_scope() as session:
        assert backup.newest_local(session) == written
