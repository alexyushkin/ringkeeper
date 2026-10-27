"""The release workflow's helpers (shell/scripts/release.py): the version from
the tag, the changelog entry it needs, and latest.json for installed apps."""

import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "shell" / "scripts" / "release.py"


@pytest.fixture
def release(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    spec = importlib.util.spec_from_file_location("release", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    conf = tmp_path / "tauri.conf.json"
    conf.write_text(json.dumps({"productName": "Ringkeeper", "version": "0.0.1"}))
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(
        "# Changelog\n\n## 0.1.1 (2026-11-02)\n\n- Fixes.\n\n## 0.1.0 (2026-10-26)\n\n- First.\n"
    )
    monkeypatch.setattr(module, "CONF", conf)
    monkeypatch.setattr(module, "CHANGELOG", changelog)
    return module


def test_the_version_comes_from_the_tag(release: ModuleType) -> None:
    assert release.set_version("v0.1.1") == "0.1.1"
    assert json.loads(release.CONF.read_text())["version"] == "0.1.1"
    assert b"\r\n" not in release.CONF.read_bytes()


@pytest.mark.parametrize("tag", ["main", "v1.2", "v1.2.3.4", "vX.Y.Z", "v0.2.0"])
def test_a_bad_tag_or_one_without_a_changelog_entry_stops_the_release(
    release: ModuleType, tag: str
) -> None:
    with pytest.raises(SystemExit):
        release.set_version(tag)
    assert json.loads(release.CONF.read_text())["version"] == "0.0.1"


def test_the_notes_are_that_version_s_entry_only(release: ModuleType) -> None:
    assert release.notes("0.1.1") == "- Fixes."
    assert release.notes("0.1.0") == "- First."


def test_latest_json_points_at_the_signed_files(release: ModuleType, tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    (dist / "windows").mkdir(parents=True)
    (dist / "macos").mkdir()
    setup = dist / "windows" / "Ringkeeper_0.1.1_x64-setup.exe"
    setup.write_bytes(b"installer")
    (dist / "windows" / f"{setup.name}.sig").write_text("c2lnbmF0dXJl\n")
    (dist / "macos" / "Ringkeeper.app.tar.gz").write_bytes(b"app")
    (dist / "macos" / "Ringkeeper.app.tar.gz.sig").write_text("bWFj")

    base = "https://github.com/alexyushkin/ringkeeper-releases/releases/download/v0.1.1"
    made = release.manifest("0.1.1", dist, base)
    assert made["version"] == "0.1.1"
    assert made["notes"] == "- Fixes."
    assert made["platforms"]["windows-x86_64"] == {
        "signature": "c2lnbmF0dXJl",
        "url": f"{base}/Ringkeeper_0.1.1_x64-setup.exe",
    }
    assert made["platforms"]["darwin-aarch64"]["url"] == f"{base}/Ringkeeper.app.tar.gz"


def test_no_manifest_without_an_updater_signature(release: ModuleType, tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "Ringkeeper_0.1.1_x64-setup.exe").write_bytes(b"installer")
    with pytest.raises(SystemExit, match="no updater signature"):
        release.manifest("0.1.1", dist, "https://example.invalid")
