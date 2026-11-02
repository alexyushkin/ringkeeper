"""Report a problem (services.report): what's included, and that secrets aren't."""

import io
import zipfile
from pathlib import Path

import pytest

from ringkeeper.config import Settings
from ringkeeper.db import session_scope
from ringkeeper.scrub import register
from ringkeeper.services import report

pytestmark = pytest.mark.usefixtures("database_url")

LEAKY = """\
INFO started with key sk-ant-api03-abcdefghijklmnopqrstuvwxyz0123456789
WARNING slack said invalid_auth for xoxb-123456789012-abcdefghijkl
connecting to postgresql://acme_admin:hunter2-secret@db.acme.example:5432/app
GitHub token ghp_0123456789abcdefghijABCDEFGHIJ and Authorization: Bearer abc.def.ghijkl
mail from anna@acme.example failed; password=correct-horse
the vault's own value: own-secret-value-123
"""


def test_secrets_of_every_shape_are_removed() -> None:
    register("own-secret-value-123")
    cleaned = report.clean(LEAKY)
    for leaked in ("sk-ant-api03", "xoxb-1234", "hunter2", "ghp_0123", "abc.def.ghijkl",
                   "anna@acme.example", "correct-horse", "own-secret-value-123"):  # fmt: skip
        assert leaked not in cleaned, leaked
    assert "postgresql://acme_admin:[removed]@db.acme.example" in cleaned  # where, not how
    assert "slack said invalid_auth" in cleaned  # what happened stays


def test_the_report_holds_the_version_counts_and_cleaned_logs(data_dir: Path) -> None:
    logs = data_dir / "logs"
    logs.mkdir(parents=True)
    (logs / "backend.log").write_text(LEAKY, encoding="utf-8")
    settings = Settings(mode="desktop", data_dir=data_dir, database_url="sqlite:///x.db")
    with session_scope() as session:
        parts = report.collect(session, settings)
    text = report.as_text(parts)
    assert "version:" in text
    assert "0 projects, 0 connections" in text
    assert "--- last lines of backend.log ---" in text
    assert "hunter2" not in text
    blob = report.zipped(text, "It stopped after I approved, my key is sk-ant-api03-zzzzzzzzzzzzzz")
    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        assert sorted(archive.namelist()) == ["report.txt", "what-happened.txt"]
        assert "sk-ant" not in archive.read("what-happened.txt").decode()


def test_a_server_report_has_no_logs(data_dir: Path) -> None:
    with session_scope() as session:
        parts = report.collect(session, Settings())
    assert parts["logs"] == {}
    assert parts["about"]["install"] == "Server (Docker)"
