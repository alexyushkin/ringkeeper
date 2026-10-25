"""The api's supervisor applies settings live; retention keeps summaries and the audit log."""

import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, select, update

from ringkeeper.admin_token import stored_api_token
from ringkeeper.api.supervisor import Supervisor, write_private
from ringkeeper.config import Settings, get_settings
from ringkeeper.db import session_scope
from ringkeeper.models import AuditEvent, SessionEvent, Task
from ringkeeper.services import retention, runtime, tasks
from ringkeeper.vault import get_vault

BOT_TOKEN = "123456:fake-telegram-bot-token-abcdef"


class Recorder:
    def __init__(self) -> None:
        self.started: list[Settings] = []
        self.stops: list[threading.Event] = []

    def make(self, settings: Settings) -> Callable[[threading.Event], None]:
        self.started.append(settings)

        def run(stop: threading.Event) -> None:
            self.stops.append(stop)
            stop.wait()

        return run


@pytest.fixture
def supervised(database_url: str, monkeypatch: pytest.MonkeyPatch) -> tuple[Supervisor, Recorder]:
    bridges, routers = Recorder(), Recorder()
    def fake_bridge(_self: Supervisor, settings: Settings) -> Callable[[threading.Event], None]:
        return bridges.make(settings)

    def fake_router(_self: Supervisor, settings: Settings) -> Callable[[threading.Event], None]:
        return routers.make(settings)

    monkeypatch.setattr(Supervisor, "_bridge", fake_bridge)
    monkeypatch.setattr(Supervisor, "_router", fake_router)
    monkeypatch.setenv("RINGKEEPER_ROUTER", "1")
    return Supervisor(get_settings()), bridges


def change(**values: object) -> None:
    with session_scope() as session:
        runtime.update(session, values, by="owner")


def test_telegram_starts_restarts_and_stops_with_the_settings(
    supervised: tuple[Supervisor, Recorder],
) -> None:
    supervisor, bridges = supervised
    supervisor.reconcile()
    assert bridges.started == []  # off by default

    change(telegram=True, telegram_chat_ids=[1001])
    supervisor.reconcile()
    assert bridges.started == []  # no bot token yet: not started, and why is shown
    assert "telegram" in supervisor.status()
    assert supervisor.status()["telegram"]["running"] is False

    get_vault().store(get_settings().telegram_bot_token_ref, BOT_TOKEN)
    supervisor.reconcile()
    assert len(bridges.started) == 1
    assert bridges.started[0].telegram_chat_ids == [1001]
    supervisor.reconcile()
    assert len(bridges.started) == 1  # nothing changed: left alone

    change(telegram_requests=True)
    supervisor.reconcile()
    assert len(bridges.started) == 2  # restarted with the new setting
    assert bridges.started[1].telegram_requests is True
    assert bridges.stops[0].is_set()

    change(telegram=False)
    supervisor.reconcile()
    assert bridges.stops[1].wait(1)
    assert supervisor.status()["telegram"]["running"] is False


def test_the_web_token_file_follows_the_admin_token(
    supervised: tuple[Supervisor, Recorder], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from typer.testing import CliRunner  # noqa: PLC0415

    from ringkeeper.cli.main import app as cli  # noqa: PLC0415

    token_file = tmp_path / "web" / "api-token"
    monkeypatch.setenv("RINGKEEPER_WEB_TOKEN_FILE", str(token_file))
    CliRunner().invoke(cli, ["auth", "api-token"])
    supervisor, _ = supervised
    supervisor.base = get_settings()
    supervisor.reconcile()
    assert token_file.read_text().strip() == stored_api_token()
    CliRunner().invoke(cli, ["auth", "api-token", "--rotate"])
    supervisor.reconcile()
    assert token_file.read_text().strip() == stored_api_token()


def test_write_private_skips_unchanged_files(tmp_path: Path) -> None:
    path = tmp_path / "token"
    write_private(path, "one")
    before = path.stat().st_mtime_ns
    write_private(path, "one")
    assert path.stat().st_mtime_ns == before
    write_private(path, "two")
    assert path.read_text() == "two\n"


def test_retention_deletes_old_transcripts_only(task_registry: None) -> None:
    with session_scope() as session:
        task = tasks.create_task(session, get_settings(), project="acme-web", prompt="go")
        task.summary = "Added the line."
        first = task.sessions[0]
        tasks.add_event(session, first.id, 1, "sdk.assistant", {"old": True})
        tasks.add_event(session, first.id, 2, "sdk.assistant", {"old": False})
        session.add(AuditEvent(project="acme-web", tool="x", outcome="changed"))
        session.flush()
        session.execute(
            update(SessionEvent)
            .where(SessionEvent.seq == 1)
            .values(at=datetime.now(UTC) - timedelta(days=91))
        )
        task_id = task.id
    with session_scope() as session:
        assert retention.due(session)
        assert retention.purge(session, 90) == 1
        assert not retention.due(session)
    with session_scope() as session:
        left = session.scalars(select(SessionEvent.seq)).all()
        assert left == [2]
        assert session.get_one(Task, task_id).summary == "Added the line."
        assert session.scalar(select(func.count()).select_from(AuditEvent)) == 1
