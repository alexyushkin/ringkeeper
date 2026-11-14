"""The task page's Copy details for support: how a task ran, and why each session started,
with none of what it was about."""

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from ringkeeper.config import get_settings
from ringkeeper.db import session_scope
from ringkeeper.models import SessionRole, TaskStatus
from ringkeeper.services import holds, tasks
from tests import test_web_api
from tests.helpers import make_project

setup_code, admin, owner = test_web_api.setup_code, test_web_api.admin, test_web_api.owner

pytestmark = pytest.mark.usefixtures("task_registry")
PROMPT = "Find the emails from Harbor Flour Mill about the late delivery"
ANSWER = "Three emails from Harbor Flour Mill, the last on Tuesday."


def test_it_says_why_each_session_started_and_nothing_it_was_about(owner: TestClient) -> None:
    with session_scope() as session:
        make_project(session, "hb-site", owner="Harbor Bakery", title="Bakery site")
        task = tasks.create_task(session, get_settings(), project="hb-site", prompt=PROMPT)
        first = task.sessions[0]
        first.status, first.turns, first.model = TaskStatus.SUCCEEDED, 6, "claude-haiku-5-5"
        first.started_at = first.finished_at = datetime(2026, 11, 14, 9, 0, tzinfo=UTC)
        tasks.add_session(session, get_settings(), task, holds.report([]), SessionRole.WORKER)
        task.sessions[-1].status = task.status = TaskStatus.SUCCEEDED
        task.sessions[-1].turns = 1
        task.summary = ANSWER
        ident = str(task.id)
    shown = owner.get(f"/tasks/{ident}/support")
    assert shown.status_code == 200, shown.text
    text = shown.json()["text"]
    assert "Session 1: worker · started for the request · succeeded · 6 turns" in text
    assert "Session 2: worker · started for held calls were decided, so it went on" in text
    assert f"Result: {len(ANSWER)} characters" in text
    assert "Ringkeeper: " in text
    for private in ("Harbor", "Flour", "hb-site", "Bakery", "emails", "Tuesday"):
        assert private not in text
