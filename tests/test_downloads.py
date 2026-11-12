"""Desktop: Download CSV (and Save report) save into the Downloads folder through the
backend, since the app window ignores a download link. Only there, under a plain name of a
known kind, never over a file; Show in folder shows only a file saved there."""

from pathlib import Path

import pytest

from ringkeeper.api import desktop_routes
from ringkeeper.config import Settings
from ringkeeper.errors import NotFoundError, RuleViolationError
from ringkeeper.services import downloads, task_files


@pytest.fixture
def into(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The Downloads folder, a scratch one (never the real one)."""
    folder = tmp_path / "Downloads"
    monkeypatch.setenv("RINGKEEPER_DOWNLOADS_DIR", str(folder))
    return folder


def test_a_plain_name_of_a_known_kind() -> None:
    assert downloads.safe_name("table.csv") == "table.csv"
    assert downloads.safe_name("../../Windows/evil.csv") == "evil.csv"
    assert downloads.safe_name("C:\\Users\\x\\notes.MD") == "notes.md"
    assert downloads.safe_name('a<b>:"c|?*.csv') == "abc.csv"
    assert downloads.safe_name("CON.csv") == "CON-file.csv"
    assert downloads.safe_name(" .csv") == "download.csv"
    for name in ["run.bat", "page.html", "table", "a.csv.exe"]:
        with pytest.raises(RuleViolationError):
            downloads.safe_name(name)


def test_saving_never_overwrites(into: Path) -> None:
    settings = Settings()
    first = downloads.save(settings, "table.csv", "a,b\n1,2\n")
    second = downloads.save(settings, "table.csv", "a,b\n3,4\n")
    third = downloads.save(settings, "folder/table.csv", "a,b\n5,6\n")
    assert [p.name for p in (first, second, third)] == [
        "table.csv",
        "table (2).csv",
        "table (3).csv",
    ]
    assert {p.parent for p in (first, second, third)} == {into}
    assert first.read_text(encoding="utf-8") == "a,b\n1,2\n"  # left as it was
    assert sorted(p.name for p in into.iterdir()) == ["table (2).csv", "table (3).csv", "table.csv"]


def test_the_routes_save_and_show_only_in_downloads(
    into: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    shown: list[tuple[Path, bool]] = []

    def show(path: Path, *, reveal: bool) -> None:
        shown.append((path, reveal))

    monkeypatch.setattr(task_files, "show", show)
    saved = desktop_routes.save_download(
        desktop_routes.DownloadIn(name="table.csv", text="\ufeffOrder,Total\n1007,$40\n")
    )
    assert saved["name"] == "table.csv"
    assert Path(saved["path"]).read_bytes().startswith("\ufeff".encode())  # Excel reads UTF-8
    desktop_routes.show_download(desktop_routes.SavedIn(path=saved["path"]))
    assert shown == [((into / "table.csv").resolve(), True)]
    outside = tmp_path / "secret.csv"
    outside.write_text("x", encoding="utf-8")
    with pytest.raises(RuleViolationError):
        desktop_routes.show_download(desktop_routes.SavedIn(path=str(outside)))
    with pytest.raises(RuleViolationError):
        desktop_routes.show_download(desktop_routes.SavedIn(path=str(into / ".." / "secret.csv")))
    with pytest.raises(NotFoundError):
        desktop_routes.show_download(desktop_routes.SavedIn(path=str(into / "gone.csv")))
    assert len(shown) == 1
