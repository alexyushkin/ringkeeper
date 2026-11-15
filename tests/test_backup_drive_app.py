"""Settings, Backup, Back up to Google Drive, in the desktop app: turning it on uploads the
newest backup right away, Upload now sends it again, and the window is told when each
upload starts and ends (a "job" on the live stream), so the section never goes stale."""

import json
import threading
import time
from pathlib import Path
from typing import Any

import httpx2
import pytest

from ringkeeper.adapters import google
from ringkeeper.api import desktop_routes
from ringkeeper.db import session_scope
from ringkeeper.models import InstanceType
from ringkeeper.services import clients
from ringkeeper.vault import get_vault
from tests.helpers import make_instance
from tests.test_backup_drive import SECRET, FakeDrive, backup_file
from tests.test_desktop import desktop, window

__all__ = ["desktop"]  # the desktop app, started for each test


class Stream:
    """The window's live stream (GET /bff/events), read in the background."""

    def __init__(self, client: httpx2.Client) -> None:
        self.events: list[dict[str, Any]] = []
        self._client = client
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._read, daemon=True)
        self._thread.start()

    def _read(self) -> None:
        try:
            with self._client.stream("GET", "/bff/events", timeout=None) as response:
                for line in response.iter_lines():
                    if line.startswith("data: "):
                        self.events.append(json.loads(line[6:]))
                    if self._stop.is_set():
                        return
        except httpx2.HTTPError:
            return  # the app quit at the end of the test

    def job(self, status: str, timeout: float = 30.0) -> None:
        """Wait for this upload event; the ones after it (it may end right away) are kept."""
        deadline = time.monotonic() + timeout
        while True:
            for at, event in enumerate(list(self.events)):
                if (event.get("type"), event.get("kind"), event.get("status")) == (
                    "job", "drive_upload", status,
                ):  # fmt: skip
                    del self.events[: at + 1]
                    return
            assert time.monotonic() < deadline, f"no drive_upload {status}: {self.events}"
            time.sleep(0.1)

    def close(self) -> None:
        self._stop.set()


@pytest.fixture
def ready(
    desktop: dict[str, Any], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> tuple[FakeDrive, Path]:
    fake = FakeDrive()
    monkeypatch.setattr(google, "transport", httpx2.MockTransport(fake.handle))
    folder = tmp_path / "backups"
    folder.mkdir()
    monkeypatch.setattr(desktop_routes, "_backup_folder", lambda: folder)
    get_vault().store("file://google/mine", SECRET)
    with session_scope() as session:
        clients.ensure(session, "Me")
        clients.set_own(session, "me", True)
        make_instance(
            session, "my-google", InstanceType.GOOGLE, owner="me",
            secret_ref="file://google/mine", config={"access": ["drive_files"]},
        )  # fmt: skip
    return fake, folder


def test_turning_it_on_uploads_the_newest_backup_and_says_when(
    desktop: dict[str, Any], ready: tuple[FakeDrive, Path]
) -> None:
    fake, folder = ready
    newest = backup_file(folder, 14)
    win = window(desktop)
    stream = Stream(window(desktop))
    try:
        time.sleep(0.5)  # the stream is open
        turned = win.put("/bff/desktop/backup/drive", json={"connection": "my-google"})
        assert turned.status_code == 200, turned.text
        assert turned.json()["uploading"] is not None  # "Uploading..." at once
        assert turned.json()["next"] is False
        stream.job("started")
        stream.job("done")
        shown = win.get("/bff/desktop/backup").json()
        assert shown["drive"]["uploading"] is None
        assert shown["drive"]["file"] == newest.name
        assert shown["drive"]["link"] == "https://drive.example/f002"  # Open in Drive
        assert shown["newest"]["name"] == newest.name
        assert fake.uploads == 1

        again = win.post("/bff/desktop/backup/drive/upload")  # Upload now
        assert again.status_code == 200, again.text
        stream.job("done")
        assert fake.uploads == 2
    finally:
        stream.close()


def test_with_no_backup_here_the_next_one_will_be_uploaded(
    desktop: dict[str, Any], ready: tuple[FakeDrive, Path]
) -> None:
    fake, _ = ready
    win = window(desktop)
    turned = win.put("/bff/desktop/backup/drive", json={"connection": "my-google"}).json()
    assert (turned["next"], turned["uploading"]) == (True, None)
    nothing = win.post("/bff/desktop/backup/drive/upload")
    assert nothing.status_code == 409
    assert "no backup on this computer" in nothing.json()["detail"]
    # Back up now uses the weekly backups' password: without them, Make a backup.
    now = win.post("/bff/desktop/backup/now")
    assert now.status_code == 409
    assert "weekly backups" in now.json()["detail"]
    assert fake.uploads == 0


def test_back_up_now_with_weekly_backups_uploads_it(
    desktop: dict[str, Any], ready: tuple[FakeDrive, Path], tmp_path: Path
) -> None:
    fake, _ = ready
    win = window(desktop)
    weekly = tmp_path / "weekly"
    on = win.put(
        "/bff/desktop/backup/auto",
        json={"enabled": True, "folder": str(weekly), "keep": 2, "password": "a long password"},
    )
    assert on.status_code == 200, on.text
    win.put("/bff/desktop/backup/drive", json={"connection": "my-google"})
    made = win.post("/bff/desktop/backup/now")
    assert made.status_code == 200, made.text
    assert Path(made.json()["path"]).parent == weekly
    deadline = time.monotonic() + 30
    while win.get("/bff/desktop/backup").json()["drive"]["uploading"]:
        assert time.monotonic() < deadline
        time.sleep(0.2)
    assert win.get("/bff/desktop/backup").json()["drive"]["file"] == Path(made.json()["path"]).name
    assert fake.uploads >= 1
