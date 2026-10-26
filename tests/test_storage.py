"""The storage seams: SQLite behaving like Postgres where Ringkeeper relies on it.

These run on a SQLite file of their own whichever database the rest of the
run uses, so both CI jobs cover them.
"""

import json
import re
import threading
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import Engine, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ringkeeper import migrate
from ringkeeper.models import AuthMode, Decision, DecisionKind, Setting, Task, TaskStatus
from ringkeeper.storage import events
from ringkeeper.storage.sql import READ_ONLY, NestedWriteError, insert, make_engine

SRC = Path(__file__).resolve().parents[1] / "src" / "ringkeeper"


@pytest.fixture
def sqlite_engine(tmp_path: Path) -> Iterator[Engine]:
    url = f"sqlite:///{(tmp_path / 'desktop.db').as_posix()}"
    migrate.upgrade(url)
    engine = make_engine(url)
    yield engine
    engine.dispose()


def _task(**fields: object) -> Task:
    values: dict[str, object] = {
        "project": "acme-web", "prompt": "count customers", "auth_mode": AuthMode.API_KEY,
        "max_turns": 5, "max_runtime_s": 60,
    }  # fmt: skip
    return Task(**(values | fields))


def test_notifications_arrive_after_commit_and_never_after_rollback(sqlite_engine: Engine) -> None:
    subscription = events.subscribe(str(sqlite_engine.url), ["ringkeeper_tasks"])
    try:
        with Session(sqlite_engine) as session:
            events.notify(session, "ringkeeper_tasks", "rolled back")
            session.rollback()
        with Session(sqlite_engine) as session:
            events.notify(session, "ringkeeper_tasks", "kept")
            assert subscription.get(timeout=0.05) == []  # not before the commit
            session.commit()
        assert subscription.get(timeout=1) == [("ringkeeper_tasks", "kept")]
    finally:
        subscription.close()


def test_triggers_send_the_same_payloads_as_postgres(sqlite_engine: Engine) -> None:
    subscription = events.subscribe(str(sqlite_engine.url), ["ringkeeper_events"])
    try:
        with Session(sqlite_engine, expire_on_commit=False) as session:
            task = _task()
            session.add(task)
            session.commit()
            task.status = TaskStatus.RUNNING
            session.commit()
            task.prompt = "no event for this"
            session.commit()
        payloads = [json.loads(p) for _, p in subscription.get(timeout=1)]
    finally:
        subscription.close()
    assert payloads == [
        {"type": "task", "task_id": str(task.id), "status": status, "project": "acme-web",
         "thread_id": None}
        for status in ("queued", "running")
    ]  # fmt: skip
    assert re.fullmatch(r"[0-9a-f-]{36}", payloads[0]["task_id"])


def test_times_come_back_in_utc(sqlite_engine: Engine) -> None:
    # In the future (the expiry must come after creation), as local time: the same instant.
    expiry = datetime.now(UTC).replace(microsecond=0) + timedelta(days=1)
    with Session(sqlite_engine) as session:
        session.add(
            Decision(
                kind=DecisionKind.QUESTION, payload={"question": "?"},
                expires_at=expiry.astimezone(),
            )
        )  # fmt: skip
        session.commit()
    with Session(sqlite_engine) as session:
        decision = session.scalars(select(Decision)).one()
        assert decision.expires_at == expiry
        assert decision.created_at.tzinfo is UTC  # the database's own default too
        assert abs(datetime.now(UTC) - decision.created_at) < timedelta(minutes=1)
        found = session.scalars(
            select(Decision).where(Decision.expires_at > expiry - timedelta(minutes=1))
        ).all()
        assert len(found) == 1


def test_one_writer_at_a_time(sqlite_engine: Engine) -> None:
    """Read-then-write from many threads loses no updates (BEGIN IMMEDIATE)."""
    with Session(sqlite_engine) as session:
        session.add(Setting(key="counter", value={"n": 0}))
        session.commit()

    def bump() -> None:
        for _ in range(25):
            with Session(sqlite_engine) as session:
                row = session.get_one(Setting, "counter", with_for_update=True)
                row.value = {"n": row.value["n"] + 1}
                session.commit()

    threads = [threading.Thread(target=bump) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    with Session(sqlite_engine) as session:
        assert session.get_one(Setting, "counter").value == {"n": 100}


def test_a_read_inside_a_write_is_fine_a_write_inside_a_write_fails_fast(
    sqlite_engine: Engine,
) -> None:
    reader = sqlite_engine.execution_options(**{READ_ONLY: True})
    with Session(sqlite_engine) as outer:
        outer.add(Setting(key="outer", value={}))
        outer.flush()  # holds the write lock now
        with Session(reader) as inner:
            assert inner.get(Setting, "outer") is None  # not committed yet
        with Session(sqlite_engine) as inner, pytest.raises(NestedWriteError):
            inner.get(Setting, "outer")
        outer.commit()
    with Session(sqlite_engine) as again:  # the lock was released
        assert again.get(Setting, "outer") is not None


def test_upsert(sqlite_engine: Engine) -> None:
    with Session(sqlite_engine) as session:
        for value in ({"a": 1}, {"a": 2}):
            statement = insert(session, Setting).values(key="k", value=value)
            session.execute(
                statement.on_conflict_do_update(
                    index_elements=["key"], set_={"value": statement.excluded.value}
                )
            )
        session.commit()
        assert session.get_one(Setting, "k").value == {"a": 2}


def test_the_audit_log_is_append_only_on_sqlite(sqlite_engine: Engine) -> None:
    with sqlite_engine.begin() as connection:
        connection.execute(text("INSERT INTO audit_events (outcome) VALUES ('refused')"))
    for statement in ("UPDATE audit_events SET outcome = 'held'", "DELETE FROM audit_events"):
        with pytest.raises(Exception, match="append-only"), sqlite_engine.begin() as connection:
            connection.execute(text(statement))


def test_foreign_keys_are_enforced(sqlite_engine: Engine) -> None:
    with Session(sqlite_engine) as session:
        session.add(_task(thread_id=uuid.uuid4()))
        with pytest.raises(IntegrityError, match="FOREIGN KEY"):
            session.commit()


def test_postgres_specifics_stay_in_storage() -> None:
    """Only storage/ (and the frozen 0001-0008 migrations) may use Postgres-only APIs."""
    allowed = {SRC / "storage", SRC / "migrations" / "versions"}
    offenders = [
        str(path.relative_to(SRC))
        for path in SRC.rglob("*.py")
        if not any(path.is_relative_to(a) for a in allowed)
        and re.search(
            r"sqlalchemy\.dialects\.postgresql|pg_notify|pg_advisory|\"LISTEN |NOTIFY \{",
            path.read_text(encoding="utf-8"),
        )
    ]
    assert offenders == []
