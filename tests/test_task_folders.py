"""Tasks and the project folder: one task at a time per folder, a conversation's tasks share
a branch, and a task's changes are kept (merged) or discarded from its page."""

import subprocess
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ringkeeper.config import get_settings
from ringkeeper.db import session_scope
from ringkeeper.errors import RuleViolationError
from ringkeeper.models import Task, TaskStatus, Thread
from ringkeeper.services import branches, tasks
from tests import test_web_api
from tests.helpers import make_project

setup_code, admin, owner = test_web_api.setup_code, test_web_api.admin, test_web_api.owner


def git(root: Path, *args: str) -> str:
    done = subprocess.run(  # noqa: S603 (fixed argv)
        ["git", "-C", str(root), *args],  # noqa: S607
        capture_output=True, text=True, check=True,
    )  # fmt: skip
    return done.stdout.strip()


@pytest.fixture
def folder(tmp_path: Path, database_url: str) -> Path:
    root = tmp_path / "site"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "dev@example.com")
    git(root, "config", "user.name", "Dev")
    (root / "README.md").write_text("site\n", encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "initial")
    with session_scope() as session:
        make_project(session, "gi-site", owner="General Informatics LLC", workspace_path=str(root))
        make_project(
            session,
            "gi-blog",
            owner="General Informatics LLC",
            workspace_path=str(tmp_path / "blog"),
        )
    return root


def queue(project: str, thread: str | None = None) -> str:
    with session_scope() as session:
        task = tasks.create_task(session, get_settings(), project=project, prompt="do it")
        if thread is not None:
            task.thread_id = uuid.UUID(thread)
        return str(task.id)


def claim() -> str | None:
    with session_scope() as session:
        found = tasks.claim_next(session)
        return str(found.task_id) if found is not None else None


def finish(task_id: str, branch: str | None = None) -> None:
    with session_scope() as session:
        task = session.get_one(Task, uuid.UUID(task_id))
        task.status = TaskStatus.SUCCEEDED
        task.git_branch = branch
        for row in task.sessions:
            row.status = TaskStatus.SUCCEEDED


def test_one_task_at_a_time_per_project_folder(folder: Path) -> None:
    first, second = queue("gi-site"), queue("gi-site")
    other = queue("gi-blog")
    assert claim() == first
    assert claim() == other  # another folder isn't held up
    assert claim() is None  # the second waits for the first
    with session_scope() as session:
        assert tasks.waits_for_folder(session, session.get_one(Task, uuid.UUID(second)))
    finish(first)
    assert claim() == second


def test_a_follow_up_in_the_conversation_continues_on_the_same_branch(folder: Path) -> None:
    with session_scope() as session:
        thread = Thread(title="site work", source="web")
        session.add(thread)
        session.flush()
        place = str(thread.id)
    first = queue("gi-site", place)
    finish(first, "ringkeeper/first")
    later = queue("gi-site", place)
    elsewhere = queue("gi-site")
    with session_scope() as session:
        follow_up = session.get_one(Task, uuid.UUID(later))
        alone = session.get_one(Task, uuid.UUID(elsewhere))
        assert tasks.task_branch(session, follow_up) == "ringkeeper/first"
        assert tasks.task_branch(session, alone) == f"ringkeeper/{elsewhere}"


def task_with_branch(folder: Path, file: str = "NOTES.md", text: str = "notes\n") -> str:
    ident = queue("gi-site")
    branch = f"ringkeeper/{ident}"
    git(folder, "switch", "-q", "-c", branch)
    (folder / file).write_text(text, encoding="utf-8")
    git(folder, "add", "-A")
    git(folder, "commit", "-q", "-m", "task work")
    git(folder, "switch", "-q", "main")
    finish(ident, branch)
    return ident


def test_keep_merges_the_changes_into_main(folder: Path, owner: TestClient) -> None:
    ident = task_with_branch(folder)
    git(folder, "switch", "-q", "-c", "feature/mine")  # whatever the user had out
    kept = owner.post(f"/tasks/{ident}/keep")
    assert kept.status_code == 200, kept.text
    assert kept.json()["said"] == "Kept: the changes are on main."
    assert git(folder, "show", "main:NOTES.md") == "notes"
    assert git(folder, "branch", "--list", "ringkeeper/*") == ""
    assert git(folder, "rev-parse", "--abbrev-ref", "HEAD") == "feature/mine"  # as it was
    assert owner.get(f"/tasks/{ident}").json()["branch_state"] == "kept"


def test_keep_refuses_plainly_on_a_conflict(folder: Path) -> None:
    ident = task_with_branch(folder, "README.md", "the task's version\n")
    (folder / "README.md").write_text("someone else's version\n", encoding="utf-8")
    git(folder, "commit", "-qam", "meanwhile on main")
    with session_scope() as session, pytest.raises(RuleViolationError) as refused:
        branches.keep(session, get_settings(), ident)
    said = str(refused.value)
    assert said.startswith("These changes conflict with what's on main now (README.md).")
    assert git(folder, "show", "main:README.md") == "someone else's version"  # nothing merged
    assert git(folder, "status", "--porcelain") == ""
    assert git(folder, "rev-parse", "--abbrev-ref", "HEAD") == "main"


def test_discard_deletes_the_branch(folder: Path) -> None:
    ident = task_with_branch(folder)
    with session_scope() as session:
        said = branches.discard(session, get_settings(), ident)
    assert said == "Discarded: the branch and its changes are gone."
    assert git(folder, "branch", "--list", "ringkeeper/*") == ""
    assert not (folder / "NOTES.md").exists()


def test_uncommitted_changes_stop_keep(folder: Path) -> None:
    ident = task_with_branch(folder)
    (folder / "README.md").write_text("half-done edit\n", encoding="utf-8")
    with session_scope() as session, pytest.raises(RuleViolationError, match="aren't committed"):
        branches.keep(session, get_settings(), ident)
