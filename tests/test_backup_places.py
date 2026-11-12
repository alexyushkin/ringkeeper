"""Where a backup outlives this computer (services.backup_places): synced folders found on each
platform, the same-disk warning, and "I back this folder up another way", per folder."""

import json
from pathlib import Path

import pytest

from ringkeeper.db import session_scope
from ringkeeper.services import backup_places
from ringkeeper.services.backup_places import FOLDER_NAME, SAME_DISK, Place


def made(*paths: Path) -> None:
    for path in paths:
        path.mkdir(parents=True, exist_ok=True)


def labels(places: list[Place]) -> list[tuple[str, str]]:
    return [(p.service, p.label) for p in places]


def test_windows_finds_onedrive_google_drive_dropbox_and_icloud(tmp_path: Path) -> None:
    home, g = tmp_path / "Users" / "ana", tmp_path / "G"
    personal, work = home / "OneDrive", home / "OneDrive - Acme Inc"
    dropbox = tmp_path / "D" / "Dropbox (Personal)"
    appdata = home / "AppData" / "Roaming"
    made(personal, work, g / "My Drive", dropbox, appdata / "Dropbox", home / "iCloudDrive")
    (appdata / "Dropbox" / "info.json").write_text(
        json.dumps({"personal": {"path": str(dropbox), "host": 1}}), encoding="utf-8"
    )
    env = {"OneDrive": str(personal), "OneDriveCommercial": str(work), "APPDATA": str(appdata)}
    found = backup_places.synced_folders(home, env, "win32", drives=[g, tmp_path / "Z"])
    assert labels(found) == [
        ("OneDrive", "OneDrive"),
        ("OneDrive", "OneDrive (Acme Inc)"),  # the work account, once though it's found twice
        ("Google Drive", "Google Drive"),
        ("Dropbox", "Dropbox"),
        ("iCloud Drive", "iCloud Drive"),
    ]
    assert found[2].folder == str(g / "My Drive" / FOLDER_NAME)
    assert found[3].root == str(dropbox)  # where info.json says, not ~/Dropbox


def test_a_mac_finds_its_cloud_storage_and_icloud(tmp_path: Path) -> None:
    home = tmp_path / "ana"
    storage = home / "Library" / "CloudStorage"
    icloud = home / "Library" / "Mobile Documents" / "com~apple~CloudDocs"
    made(storage / "OneDrive-Personal", storage / "GoogleDrive-ana@example.com" / "My Drive",
         storage / "Dropbox", icloud)  # fmt: skip
    found = backup_places.synced_folders(home, {}, "darwin")
    assert labels(found) == [
        ("OneDrive", "OneDrive (Personal)"),
        ("Google Drive", "Google Drive"),
        ("Dropbox", "Dropbox"),
        ("iCloud Drive", "iCloud Drive"),
    ]
    assert found[1].root == str(storage / "GoogleDrive-ana@example.com" / "My Drive")


def test_linux_finds_dropbox_and_nothing_that_isn_t_there(tmp_path: Path) -> None:
    home = tmp_path / "ana"
    assert backup_places.synced_folders(home, {}, "linux") == []
    made(home / "Dropbox")
    assert labels(backup_places.synced_folders(home, {}, "linux")) == [("Dropbox", "Dropbox")]


@pytest.mark.usefixtures("database_url")
def test_the_same_disk_warns_until_the_owner_says_it_s_backed_up(tmp_path: Path) -> None:
    data = tmp_path / "data"
    synced_root = tmp_path / "OneDrive"
    made(data, synced_root)
    places = [Place("OneDrive", "OneDrive", str(synced_root), str(synced_root / FOLDER_NAME))]
    plain = str(tmp_path / "Documents" / FOLDER_NAME)  # not made yet: its parent decides
    other = str(tmp_path / "Elsewhere")
    with session_scope() as session:
        said = backup_places.check(session, plain, data, places)
        assert (said["same_disk"], said["synced"], said["warning"]) == (True, None, SAME_DISK)
        synced = backup_places.check(session, str(synced_root / FOLDER_NAME), data, places)
        assert (synced["synced"], synced["warning"]) == ("OneDrive", None)  # it leaves the disk
        backup_places.set_elsewhere(session, plain, True)
    with session_scope() as session:  # it sticks
        assert backup_places.check(session, plain, data, places)["warning"] is None
        assert backup_places.check(session, plain, data, places)["backed_up_elsewhere"]
        assert (
            backup_places.check(session, other, data, places)["warning"] == SAME_DISK
        )  # per folder
        backup_places.set_elsewhere(session, plain, False)
        assert backup_places.check(session, plain, data, places)["warning"] == SAME_DISK


@pytest.mark.usefixtures("database_url")
def test_another_drive_isn_t_warned_about(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def another_drive(_folder: Path, _data: Path) -> bool:
        return False

    monkeypatch.setattr(backup_places, "same_disk", another_drive)
    with session_scope() as session:
        said = backup_places.check(session, str(tmp_path / "E" / FOLDER_NAME), tmp_path, [])
    assert (said["same_disk"], said["warning"]) == (False, None)


def test_the_same_disk_is_the_same_volume(tmp_path: Path) -> None:
    made(tmp_path / "data")
    assert backup_places.same_disk(tmp_path / "not" / "made" / "yet", tmp_path / "data")
