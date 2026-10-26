"""Archiving and deleting conversations and tasks, through the web app's API."""

import subprocess
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from ringkeeper.config import get_settings
from ringkeeper.db import session_scope
from ringkeeper.models import AuditEvent, Decision, DecisionKind, SessionEvent, Task, TaskStatus
from ringkeeper.services import cleanup, inbox, tasks, threads
from tests.helpers import make_project
from tests.test_web_api import (  # noqa: F401 (fixtures)
    admin,  # pyright: ignore[reportUnusedImport]
    owner,  # pyright: ignore[reportUnusedImport]
    setup_code,  # pyright: ignore[reportUnusedImport]
)

GIT = (
    "git",
    "-c",
    "user.name=t",
    "-c",
    "user.email=t@example.com",
    "-c",
    "init.defaultBranch=main",
)


def git(*args: str) -> str:
    done = subprocess.run([*GIT, *args], capture_output=True, text=True, check=True)  # noqa: S603
    return done.stdout


def new_task(prompt: str = "do it", *, status: TaskStatus = TaskStatus.SUCCEEDED) -> str:
    with session_scope() as session:
        thread = threads.post_message(session, prompt, author="owner", source="web").thread
        task = tasks.create_task(
            session, get_settings(), project="acme-web", prompt=prompt, thread_id=thread.id
        )
        task.status = status
        task.sessions[0].status = status
        tasks.add_event(session, task.sessions[0].id, 1, "sdk.assistant", {"text": "hi"})
        return str(task.id)


def thread_of(task_id: str) -> str:
    with session_scope() as session:
        return str(tasks.get_task(session, task_id).thread_id)


@pytest.fixture
def project(owner: TestClient) -> None:  # noqa: F811
    with session_scope() as session:
        make_project(session, "acme-web")


@pytest.mark.usefixtures("project")
def test_archived_tasks_and_conversations_leave_the_lists_but_not_search(
    owner: TestClient,  # noqa: F811
) -> None:
    kept, archived = new_task("fix the footer"), new_task("rename the pricing page")
    assert owner.post("/tasks/archive", json={"ids": [archived]}).json() == {"changed": 1}
    listed = [t["id"] for t in owner.get("/tasks").json()]
    assert (kept in listed, archived in listed) == (True, False)
    assert [t["id"] for t in owner.get("/tasks", params={"archived": True}).json()] == [archived]
    found = owner.get("/tasks", params={"q": "pricing"}).json()
    assert [(t["id"], t["archived"]) for t in found] == [(archived, True)]

    conversation = thread_of(archived)
    owner.post("/threads/archive", json={"ids": [conversation]})
    assert conversation not in [t["id"] for t in owner.get("/threads").json()]
    assert [t["id"] for t in owner.get("/threads", params={"q": "PRICING"}).json()] == [
        conversation
    ]
    owner.post("/threads/archive", json={"ids": [conversation], "archived": False})
    assert conversation in [t["id"] for t in owner.get("/threads").json()]


@pytest.mark.usefixtures("project")
def test_a_running_task_is_not_deleted_and_the_audit_log_stays(owner: TestClient) -> None:  # noqa: F811
    running = new_task(status=TaskStatus.RUNNING)
    refused = owner.post("/tasks/delete", json={"ids": [running]})
    assert refused.status_code == 409
    assert "Stop it first" in refused.json()["detail"]

    done = new_task()
    with session_scope() as session:
        task = tasks.get_task(session, done)
        question = inbox.create(
            session, get_settings(), DecisionKind.QUESTION, {"question": "?"}, task=task
        )
        question_id = question.id
        audited = session.scalar(select(func.count()).select_from(AuditEvent))
    assert owner.post("/tasks/delete", json={"ids": [done]}).json()["deleted"] == 1
    with session_scope() as session:
        assert session.get(Task, uuid.UUID(done)) is None
        assert session.scalar(select(func.count()).select_from(SessionEvent)) == 1  # the other's
        decision = session.get(Decision, question_id)
        assert decision is not None
        assert decision.task_id is None  # kept, unlinked
        after = session.scalar(select(func.count()).select_from(AuditEvent))
        assert after == (audited or 0) + 1  # nothing removed; the delete itself recorded
        pending = cleanup.take_pending_state(session)
    assert pending == [f"ringkeeper-state-{done}-worker"]  # the manager removes it


@pytest.mark.usefixtures("project")
def test_deleting_a_conversation_takes_its_tasks(owner: TestClient) -> None:  # noqa: F811
    task_id = new_task()
    conversation = thread_of(task_id)
    answer = owner.post("/threads/delete", json={"ids": [conversation]}).json()
    assert answer == {"deleted": 1, "tasks": 1}
    assert owner.get(f"/threads/{conversation}").status_code == 404
    assert owner.get(f"/tasks/{task_id}").status_code == 404


def test_a_task_branch_is_deleted_only_when_merged_or_ticked(
    owner: TestClient,  # noqa: F811
    tmp_path: Path,
) -> None:
    folder = tmp_path / "acme-web"
    git("init", "-q", str(folder))
    git("-C", str(folder), "commit", "-q", "--allow-empty", "-m", "start")
    with session_scope() as session:
        make_project(session, "acme-web", workspace_path=str(folder))
    merged, unmerged = new_task(), new_task()
    git("-C", str(folder), "branch", tasks.branch_name(uuid.UUID(merged)))
    git("-C", str(folder), "switch", "-q", "-c", tasks.branch_name(uuid.UUID(unmerged)))
    (folder / "work.txt").write_text("work\n", encoding="utf-8")
    git("-C", str(folder), "add", ".")
    git("-C", str(folder), "commit", "-q", "-m", "work")
    git("-C", str(folder), "switch", "-q", "main")

    info = owner.get(f"/tasks/{unmerged}/branch").json()["branch"]
    assert (info["exists"], info["unmerged"]) == (True, 1)
    both = {"ids": [merged, unmerged], "delete_branches": True}
    answer = owner.post("/tasks/delete", json=both).json()
    assert answer["branches"][merged] == "deleted"
    assert answer["branches"][unmerged].startswith("kept")
    branches = git("-C", str(folder), "branch", "--list").split()
    assert tasks.branch_name(uuid.UUID(unmerged)) in branches
    assert tasks.branch_name(uuid.UUID(merged)) not in branches
