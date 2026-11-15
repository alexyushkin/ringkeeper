"""Background work tells the app when it ends (services.jobs): one "job" event on the live
stream, which every screen's data is fetched again on. One test per kind of job."""

import json
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from ringkeeper import ocr_local, voice_local
from ringkeeper.config import get_settings
from ringkeeper.db import session_scope
from ringkeeper.errors import RingkeeperError
from ringkeeper.scenarios import launcher, self_test
from ringkeeper.services import backup, digest, health, jobs, local_servers, setup_check
from ringkeeper.storage import events
from ringkeeper.vault import get_vault


class Heard:
    def __init__(self, subscription: events.Subscription) -> None:
        self.subscription = subscription

    def jobs(self, wanted: int = 1, timeout: float = 10.0) -> list[tuple[str, str]]:
        """The job events that arrive, until `wanted` have or the time is up."""
        found: list[tuple[str, str]] = []
        while len(found) < wanted:
            got = self.subscription.get(timeout=timeout)
            if not got:
                break
            for _, payload in got:
                data: dict[str, Any] = json.loads(payload)
                if data.get("type") == jobs.TYPE:
                    found.append((str(data["kind"]), str(data["status"])))
        return found


@pytest.fixture
def heard(database_url: str) -> Iterator[Heard]:
    subscription = events.subscribe(database_url, [events.LIVE])
    try:
        yield Heard(subscription)
    finally:
        subscription.close()


def test_the_event_goes_out_when_the_work_s_session_commits(heard: Heard) -> None:
    with session_scope() as session:
        jobs.changed(session, jobs.BACKUP)
        assert heard.jobs(timeout=0.3) == []  # not before it's saved
    assert heard.jobs() == [(jobs.BACKUP, jobs.DONE)]
    with pytest.raises(RuntimeError):
        _rolled_back()
    assert heard.jobs(timeout=0.3) == []  # rolled back: nothing happened


def _rolled_back() -> None:
    with session_scope() as session:
        jobs.changed(session, jobs.BACKUP)
        raise RuntimeError("rolled back")


def test_the_weekly_backup(heard: Heard, tmp_path: Path, database_url: str) -> None:
    if not database_url.startswith("sqlite"):
        pytest.skip("a backup is the desktop app's SQLite file")
    with session_scope() as session:
        backup.set_auto(
            session, get_vault(), enabled=True, folder=str(tmp_path), keep=2,
            password="a long password",
        )  # fmt: skip
    with session_scope() as session:
        assert backup.run_auto(session, get_settings(), get_vault(), force=True) is not None
    assert (jobs.BACKUP, jobs.DONE) in heard.jobs()


def test_the_connection_check(heard: Heard) -> None:
    health.check_all(get_settings())
    assert heard.jobs() == [(jobs.HEALTH, jobs.DONE)]


def test_the_morning_digest(heard: Heard, monkeypatch: pytest.MonkeyPatch) -> None:
    def always(*_: object) -> bool:
        return True

    def quiet(*_: object, **__: object) -> None:
        return None

    monkeypatch.setattr(digest, "due", always)
    monkeypatch.setattr(digest.announce, "send", quiet)
    with session_scope() as session:
        assert digest.send_if_due(session, get_settings())
    assert heard.jobs() == [(jobs.DIGEST, jobs.DONE)]


def test_check_my_setup(heard: Heard, monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(*_: object) -> None:
        raise RingkeeperError("no Claude here")

    monkeypatch.setattr(setup_check, "check", broken)
    setup_check.InApp()._run(get_settings(), None)  # type: ignore[arg-type]
    assert heard.jobs() == [(jobs.SETUP_CHECK, jobs.FAILED)]


def test_the_self_test(heard: Heard, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    class Ended:
        def wait(self) -> int:
            return 0

    def report(_: Path) -> str:
        return "# Self-test\n"

    def keep(_: Path) -> None:
        return None

    monkeypatch.setattr(launcher, "report_text", report)
    monkeypatch.setattr(launcher, "remove", keep)
    self_test.SelfTest()._finish(Ended(), tmp_path / "run")  # type: ignore[arg-type]
    assert heard.jobs() == [(jobs.SELF_TEST, jobs.DONE)]


def test_the_scan_reader_s_download(heard: Heard, tmp_path: Path) -> None:
    reader = ocr_local.LocalOcr(tmp_path)

    def offline() -> None:
        raise OSError("offline")

    reader.install = offline  # type: ignore[method-assign]
    reader._download()  # pyright: ignore[reportPrivateUsage]
    assert heard.jobs() == [(jobs.OCR, jobs.FAILED)]


def test_the_voice_model_s_download(heard: Heard, tmp_path: Path) -> None:
    model = voice_local.LocalModel(tmp_path, [])

    def offline() -> Any:
        raise OSError("offline")

    model._missing = offline  # type: ignore[method-assign]
    model._download()  # pyright: ignore[reportPrivateUsage]
    assert heard.jobs() == [(jobs.VOICE, jobs.FAILED)]


def test_a_local_server_s_install(heard: Heard) -> None:
    ran = threading.Event()
    local_servers.start("demo-files", ran.set)
    assert ran.wait(10)
    assert heard.jobs() == [(jobs.INSTALL, jobs.DONE)]


def test_every_kind_has_a_test_here() -> None:
    """A new kind of job gets its own test above (Drive uploads: test_backup_drive)."""
    tested = {"BACKUP", "HEALTH", "DIGEST", "SETUP_CHECK", "SELF_TEST", "OCR", "VOICE", "INSTALL"}
    assert {k for k in dir(jobs) if getattr(jobs, k) in jobs.KINDS} == tested | {"DRIVE_UPLOAD"}
