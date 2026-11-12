# ruff: noqa: S603, S607 - git and the script itself, with fixed arguments
"""scripts/check_private_names.py: new text naming someone on the private list fails, and the
report never repeats the name."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_private_names.py"


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def commit(repo: Path, files: dict[str, str], message: str) -> str:
    for name, text in files.items():
        (repo / name).write_text(text, encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", message)
    return git(repo, "rev-parse", "HEAD")


def check(
    repo: Path, rng: str, names: Path | None, *extra: str
) -> subprocess.CompletedProcess[str]:
    args = [sys.executable, str(SCRIPT), "--range", rng, *extra]
    if names is not None:
        args += ["--names", str(names)]
    return subprocess.run(args, cwd=repo, capture_output=True, text=True, check=False)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q")
    # Already there before the change: not this change's to answer for.
    commit(root, {"old.md": "Bayside was here first\n"}, "start")
    return root


@pytest.fixture
def names(tmp_path: Path) -> Path:
    path = tmp_path / "names.txt"
    path.write_text("# private\nBayside\n\nMr. Gutter  # a person\n", encoding="utf-8")
    return path


def test_clean_change_passes(repo: Path, names: Path) -> None:
    base = git(repo, "rev-parse", "HEAD")
    head = commit(repo, {"new.md": "Acme and Globex\n"}, "Add a note")
    done = check(repo, f"{base}..{head}", names)
    assert done.returncode == 0, done.stderr
    assert "2 checked" in done.stdout


def test_added_line_and_message_fail_without_repeating_the_name(repo: Path, names: Path) -> None:
    base = git(repo, "rev-parse", "HEAD")
    commit(repo, {"a.md": "one\ntwo\nask MR. GUTTER about it\n"}, "Add a")
    head = commit(repo, {"b.md": "fine\n"}, "From a run for bayside")
    done = check(repo, f"{base}..{head}", names)
    assert done.returncode == 1
    assert "a.md:3" in done.stderr
    assert f"commit {head[:12]} (message)" in done.stderr
    report = (done.stdout + done.stderr).lower()
    assert "gutter" not in report
    assert "bayside" not in report


def test_a_new_file_name_fails(repo: Path, names: Path) -> None:
    base = git(repo, "rev-parse", "HEAD")
    head = commit(repo, {"bayside-notes.md": "fine\n"}, "Add notes")
    done = check(repo, f"{base}..{head}", names)
    assert done.returncode == 1
    assert "(file name)" in done.stderr


def test_text_already_on_the_base_is_left_alone(repo: Path, names: Path) -> None:
    base = git(repo, "rev-parse", "HEAD")
    head = commit(repo, {"old.md": "Bayside was here first\nand Acme\n"}, "Add Acme")
    done = check(repo, f"{base}..{head}", names)
    assert done.returncode == 0, done.stderr


def test_no_list_warns_and_passes_unless_strict(repo: Path, tmp_path: Path) -> None:
    base = git(repo, "rev-parse", "HEAD")
    head = commit(repo, {"a.md": "Bayside\n"}, "a")
    missing = tmp_path / "none.txt"
    done = check(repo, f"{base}..{head}", missing)
    assert done.returncode == 0
    assert "Passing without a check" in done.stdout
    assert check(repo, f"{base}..{head}", missing, "--strict").returncode == 1


def test_install_hook_writes_pre_push(repo: Path) -> None:
    done = subprocess.run(
        [sys.executable, str(SCRIPT), "--install-hook"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )
    hook = repo / ".git" / "hooks" / "pre-push"
    assert hook.is_file(), done.stdout
    assert "check_private_names.py" in hook.read_text(encoding="utf-8")
