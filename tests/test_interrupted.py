"""Tasks a restart or an update cut short: listed on Home after startup, with Resume and
Resume all. A resumed task picks its conversation up where it stopped, or starts over if it
hadn't got going."""

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from ringkeeper.config import get_settings
from ringkeeper.db import session_scope
from ringkeeper.errors import RuleViolationError
from ringkeeper.models import Task, TaskStatus, WorkerSession
from ringkeeper.services import interrupted, tasks
from ringkeeper.vault import get_vault
from ringkeeper.workers.manager import Manager
from tests import test_web_api
from tests.fake_runtime import FakeRuntime

setup_code, admin, owner = test_web_api.setup_code, test_web_api.admin, test_web_api.owner

pytestmark = pytest.mark.usefixtures("task_registry")


def cut_short(prompt: str = "x", *, sdk: str | None = None) -> str:
    """A task that was running when Ringkeeper restarted."""
    with session_scope() as session:
        ident = str(
            tasks.create_task(session, get_settings(), project="acme-web", prompt=prompt).id
        )
    with session_scope() as session:
        tasks.claim_next(session)
        session.scalars(select(WorkerSession)).all()[-1].sdk_session_id = sdk
    Manager(FakeRuntime(lambda _spec: None), get_vault()).recover()  # type: ignore[arg-type]
    return ident


def listed() -> list[str]:
    with session_scope() as session:
        return [str(t.id) for t in interrupted.listed(session)]


def sessions(ident: str) -> list[dict[str, Any]]:
    with session_scope() as session:
        task = session.get(Task, ident)
        assert task is not None
        rows = sorted(task.sessions, key=lambda s: s.ordinal)
        return [{"prompt": s.prompt, "sdk": s.sdk_session_id, "status": s.status} for s in rows]


def test_a_restart_lists_the_task_with_a_plain_reason() -> None:
    ident = cut_short()
    with session_scope() as session:
        task = session.get(Task, ident)
        assert task is not None
        assert task.status is TaskStatus.FAILED
        assert task.error == interrupted.ERROR
    assert listed() == [ident]


def test_resume_picks_up_the_conversation_where_it_stopped() -> None:
    ident = cut_short("write the notes", sdk="sdk-1")
    with session_scope() as session:
        interrupted.resume(session, get_settings(), ident)
    last = sessions(ident)[-1]
    assert (last["prompt"], last["sdk"], last["status"]) == (
        interrupted.RESUME, "sdk-1", TaskStatus.QUEUED,
    )  # fmt: skip
    assert listed() == []


def test_a_task_that_hadn_t_got_going_starts_over() -> None:
    ident = cut_short("write the notes")
    with session_scope() as session:
        interrupted.resume(session, get_settings(), ident)
    last = sessions(ident)[-1]
    assert (last["prompt"], last["sdk"]) == ("write the notes", None)


def test_resume_all() -> None:
    first, second = cut_short("one"), cut_short("two")
    assert set(listed()) == {first, second}
    with session_scope() as session:
        assert interrupted.resume_all(session, get_settings()) == 2
    assert listed() == []


def test_only_a_task_cut_short_is_resumed_this_way() -> None:
    with session_scope() as session:
        ident = str(tasks.create_task(session, get_settings(), project="acme-web", prompt="x").id)
    with session_scope() as session, pytest.raises(RuleViolationError):
        interrupted.resume(session, get_settings(), ident)


def test_the_api_lists_and_resumes(owner: TestClient) -> None:
    first, second = cut_short("one"), cut_short("two")
    shown = owner.get("/tasks/interrupted")
    assert shown.status_code == 200, shown.text
    assert {t["id"] for t in shown.json()} == {first, second}
    assert {t["prompt"] for t in shown.json()} == {"one", "two"}
    one = owner.post(f"/tasks/{first}/resume-interrupted")
    assert one.status_code == 200, one.text
    assert one.json()["status"] == "queued"
    assert owner.post("/tasks/interrupted/resume").json() == {"resumed": 1}
    assert owner.get("/tasks/interrupted").json() == []
