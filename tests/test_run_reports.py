"""The reports Check my setup and the Self-test keep, one per day: listed newest first,
read back by day, and shown in their folder only in the desktop app."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ringkeeper.api import start_routes
from ringkeeper.config import get_settings
from ringkeeper.errors import NotFoundError
from ringkeeper.services import run_reports, task_files
from tests import test_web_api

setup_code, admin, owner = test_web_api.setup_code, test_web_api.admin, test_web_api.owner


def kept(data_dir: Path, folder: str, *days: str) -> Path:
    here = data_dir / folder
    here.mkdir(parents=True, exist_ok=True)
    for day in days:
        (here / f"{day}.md").write_text(f"# Report, {day}\n", encoding="utf-8")
    return here


def test_reports_are_listed_newest_first_and_read_by_day(data_dir: Path) -> None:
    here = kept(data_dir, "setup-check", "2026-11-02", "2026-11-11", "2026-10-30")
    (here / "notes.md").write_text("not a report", encoding="utf-8")
    (data_dir / "secret.md").write_text("outside", encoding="utf-8")
    settings = get_settings()
    assert [r["day"] for r in run_reports.listed(settings, "setup-check")] == [
        "2026-11-11", "2026-11-02", "2026-10-30",
    ]  # fmt: skip
    assert run_reports.listed(settings, "self-test") == []  # never run
    assert run_reports.read(settings, "setup-check", "2026-11-02") == "# Report, 2026-11-02\n"
    for day in ["notes", "../secret", "2026-01-01"]:
        with pytest.raises(NotFoundError):
            run_reports.read(settings, "setup-check", day)


def test_the_api_reads_them_and_shows_them_only_on_the_desktop(
    data_dir: Path, owner: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    kept(data_dir, "self-test", "2026-11-09", "2026-11-10")
    listed = owner.get("/run-reports/self-test").json()
    assert [r["day"] for r in listed] == ["2026-11-10", "2026-11-09"]
    one = owner.get("/run-reports/self-test/2026-11-09").json()
    assert one == {"day": "2026-11-09", "report": "# Report, 2026-11-09\n"}
    assert owner.get("/run-reports/self-test/2026-01-01").status_code == 404
    assert owner.get("/run-reports/elsewhere").status_code == 422
    refused = owner.post("/run-reports/self-test/2026-11-09/show")
    assert refused.status_code == 422
    assert "only in the desktop app" in refused.json()["detail"]
    shown: list[tuple[Path, bool]] = []

    def show(path: Path, *, reveal: bool) -> None:
        shown.append((path, reveal))

    desktop = get_settings().model_copy(update={"mode": "desktop"})
    monkeypatch.setattr(start_routes, "current_settings", lambda: desktop)
    monkeypatch.setattr(task_files, "show", show)
    assert owner.post("/run-reports/self-test/2026-11-09/show").status_code == 200
    assert shown == [((data_dir / "self-test" / "2026-11-09.md").absolute(), True)]
