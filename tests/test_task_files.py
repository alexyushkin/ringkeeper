"""Files a task made: listed where they are now, opened or shown in their folder (desktop),
read only from the task's own changed files inside the project's folder, and attached to a
chat only when small and not secret-looking."""

import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ringkeeper import scrub
from ringkeeper.api import work_routes
from ringkeeper.config import get_settings
from ringkeeper.db import session_scope
from ringkeeper.errors import NotFoundError, RuleViolationError
from ringkeeper.models import Task, TaskStatus
from ringkeeper.services import task_files, tasks
from tests import test_web_api
from tests.helpers import make_project

setup_code, admin, owner = test_web_api.setup_code, test_web_api.admin, test_web_api.owner
KEY = "sk-ant-api03-" + "q" * 60  # a secret Ringkeeper resolved: masked in what's sent


def git(root: Path, *args: str) -> str:
    done = subprocess.run(  # noqa: S603 (fixed argv)
        ["git", "-C", str(root), "-c", "user.email=d@example.com", "-c", "user.name=D", *args],  # noqa: S607
        capture_output=True, text=True, check=True,
    )  # fmt: skip
    return done.stdout.strip()


@pytest.fixture
def folder(tmp_path: Path, database_url: str) -> Path:
    root = tmp_path / "shop"
    root.mkdir()
    (root / "orders.csv").write_text(f"id,total\n1,40\nkey,{KEY}\n", encoding="utf-8")
    (root / "run.bat").write_text("echo hi\n", encoding="utf-8")
    (root / ".env").write_text("SECRET=1\n", encoding="utf-8")
    (root / "big.bin").write_bytes(b"x" * (task_files.ATTACH_BYTES + 1))
    with session_scope() as session:
        make_project(session, "gi-shop", owner="General Informatics LLC", workspace_path=str(root))
    return root


def a_task(files: list[str], branch: str | None = None) -> str:
    with session_scope() as session:
        task = tasks.create_task(session, get_settings(), project="gi-shop", prompt="export")
        task.status, task.changed_files, task.git_branch = TaskStatus.SUCCEEDED, files, branch
        return str(task.id)


def files_of(ident: str) -> list[task_files.TaskFile]:
    with session_scope() as session:
        task = session.get_one(Task, uuid.UUID(ident))
        return task_files.listed(session, get_settings(), task)


def test_files_in_the_folder_are_listed_and_attached_safely(folder: Path) -> None:
    scrub.register(KEY)
    ident = a_task(["orders.csv", "run.bat", ".env", "big.bin", "gone.txt"])
    shown = {f.path: f for f in files_of(ident)}
    assert shown["orders.csv"].size == (folder / "orders.csv").stat().st_size
    assert shown["run.bat"].runs
    assert shown["gone.txt"].size is None
    assert not shown["big.bin"].attachable
    with session_scope() as session:
        task = session.get_one(Task, uuid.UUID(ident))
        sent = dict(task_files.attachments(session, get_settings(), task))
    assert sorted(sent) == ["orders.csv", "run.bat"]  # not .env, not the big one
    assert KEY.encode() not in sent["orders.csv"]  # a known secret is masked, as in messages
    assert b"1,40" in sent["orders.csv"]


def test_a_file_on_the_task_s_branch_opens_from_a_copy(folder: Path) -> None:
    git(folder, "init", "-q", "-b", "main")
    git(folder, "add", "orders.csv")
    git(folder, "commit", "-qm", "start")
    git(folder, "switch", "-q", "-c", "ringkeeper/t1")
    (folder / "report.md").write_text("# Report\n", encoding="utf-8")
    git(folder, "add", "report.md")
    git(folder, "commit", "-qm", "report")
    git(folder, "switch", "-q", "main")
    assert not (folder / "report.md").exists()  # only on the branch
    ident = a_task(["report.md"], branch="ringkeeper/t1")
    [one] = files_of(ident)
    assert (one.on_branch, one.size) == (True, len("# Report\n"))
    with session_scope() as session:
        task = session.get_one(Task, uuid.UUID(ident))
        copy = task_files.local_file(session, get_settings(), task, "report.md")
        assert task_files.content(session, get_settings(), task, "report.md") == b"# Report\n"
    assert copy.read_text(encoding="utf-8") == "# Report\n"
    assert copy.is_relative_to(get_settings().data_dir.resolve())
    assert not (folder / "report.md").exists()  # the folder you work in isn't touched


def test_only_the_task_s_own_files_inside_the_folder(folder: Path) -> None:
    ident = a_task(["orders.csv", "../outside.txt"])
    with session_scope() as session:
        task = session.get_one(Task, uuid.UUID(ident))
        with pytest.raises(NotFoundError):
            task_files.local_file(session, get_settings(), task, "notes.txt")
        with pytest.raises(RuleViolationError):
            task_files.local_file(session, get_settings(), task, "../outside.txt")


def test_the_api_lists_them_and_opens_only_on_the_desktop(
    folder: Path, owner: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    ident = a_task(["orders.csv"])
    listed = owner.get(f"/tasks/{ident}/files").json()
    assert [(f["path"], f["can_open"]) for f in listed] == [("orders.csv", False)]
    refused = owner.post(f"/tasks/{ident}/files/open", json={"path": "orders.csv"})
    assert refused.status_code == 422
    assert "only in the desktop app" in refused.json()["detail"]
    opened: list[tuple[Path, bool]] = []

    def show(path: Path, *, reveal: bool) -> None:
        opened.append((path, reveal))

    desktop = get_settings().model_copy(update={"mode": "desktop"})
    monkeypatch.setattr(task_files, "show", show)
    monkeypatch.setattr(work_routes, "current_settings", lambda: desktop)
    done = owner.post(f"/tasks/{ident}/files/open", json={"path": "orders.csv", "reveal": True})
    assert done.status_code == 200, done.text
    assert opened == [((folder / "orders.csv").resolve(), True)]
    assert owner.get("/tasks").json()[0]["files"] == 1


@pytest.mark.skipif(sys.platform != "win32", reason="how Windows opens and shows files")
def test_a_program_is_shown_in_its_folder_never_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ran: list[Any] = []

    def startfile(path: Path) -> None:
        ran.append(("open", path))

    def popen(args: list[str], **_: Any) -> None:
        ran.append(("popen", args))

    monkeypatch.setattr(task_files.os, "startfile", startfile, raising=False)
    monkeypatch.setattr(task_files.subprocess, "Popen", popen)
    task_files.show(tmp_path / "run.bat", reveal=False)
    task_files.show(tmp_path / "notes.txt", reveal=False)
    assert ran == [
        ("popen", ["explorer", f"/select,{tmp_path / 'run.bat'}"]),
        ("open", tmp_path / "notes.txt"),
    ]


def test_only_documents_open_everything_else_is_shown_in_its_folder() -> None:
    for name in ["report.pdf", "Q3.XLSX", "notes.md", "orders.csv", "data.json", "logo.png"]:
        assert not task_files.shown_only(name), name
    for name in ["page.html", "logo.svg", "macro.xlsm", "run.bat", "tool", "setup.exe", "a.js"]:
        assert task_files.shown_only(name), name
