"""Where a backup outlives this computer (services.backup_places): synced folders of the
accounts signed in on each platform, the same-disk warning, and "I back this folder up another
way", per folder."""

import json
import plistlib
from collections.abc import Callable
from pathlib import Path

import pytest

from ringkeeper.db import session_scope
from ringkeeper.services import backup_places
from ringkeeper.services.backup_places import FOLDER_NAME, SAME_DISK, Machine, Place


def made(*paths: Path) -> None:
    for path in paths:
        path.mkdir(parents=True, exist_ok=True)


def labels(places: list[Place]) -> list[tuple[str, str]]:
    return [(p.service, p.label) for p in places]


def running(*names: str) -> Callable[[], set[str]]:
    return lambda: set(names)


def dropbox_info(at: Path, folder: Path) -> None:
    made(at)
    (at / "info.json").write_text(
        json.dumps({"personal": {"path": str(folder), "host": 1}}), encoding="utf-8"
    )


def test_windows_offers_the_accounts_signed_in(tmp_path: Path) -> None:
    home, g = tmp_path / "Users" / "ana", tmp_path / "G"
    personal, work = tmp_path / "E" / "OneDrive", home / "OneDrive - Acme Inc"
    dropbox = tmp_path / "D" / "Dropbox (Personal)"
    appdata = home / "AppData" / "Roaming"
    made(personal, work, g / "My Drive", dropbox, home / "iCloudDrive")
    dropbox_info(appdata / "Dropbox", dropbox)
    machine = Machine(
        home, {"APPDATA": str(appdata)}, "win32", drives=[g, tmp_path / "Z"],
        onedrive=[("Personal", personal), ("Business1", work)],
        running=running("onedrive.exe", "dropbox.exe"),
    )  # fmt: skip
    found = backup_places.synced_folders(machine)
    assert labels(found) == [
        ("OneDrive", "OneDrive"),
        ("OneDrive", "OneDrive (Acme Inc)"),
        ("Google Drive", "Google Drive"),
        ("Dropbox", "Dropbox"),
    ]  # iCloud for Windows: nothing says it's signed in, so its folder isn't offered
    assert found[0].root == str(personal)  # where the account syncs, not ~/OneDrive
    assert found[2].folder == str(g / "My Drive" / FOLDER_NAME)
    assert found[3].root == str(dropbox)  # where info.json says, not ~/Dropbox
    assert [p.note for p in found] == [None] * 4


def test_an_empty_onedrive_folder_with_no_account_isn_t_offered(tmp_path: Path) -> None:
    """From a real try: C:\\Users\\<me>\\OneDrive was there but empty, no account signed in,
    and "OneDrive" was offered, which also hid the same-disk warning."""
    home = tmp_path / "Users" / "ana"
    made(home / "OneDrive", home / "Dropbox", home / "Google Drive", home / "iCloudDrive")
    env = {"OneDrive": str(home / "OneDrive"), "APPDATA": str(home / "AppData" / "Roaming")}
    machine = Machine(home, env, "win32", drives=[tmp_path / "C"], running=running())
    assert backup_places.synced_folders(machine) == []


def test_an_app_that_isn_t_running_is_offered_with_a_note(tmp_path: Path) -> None:
    home, folder = tmp_path / "ana", tmp_path / "ana" / "OneDrive"
    made(folder)
    machine = Machine(home, {}, "win32", onedrive=[("Personal", folder)], running=running("x.exe"))
    (place,) = backup_places.synced_folders(machine)
    assert place.note == "OneDrive isn't running, so this won't upload until it is."
    unknown = Machine(home, {}, "win32", onedrive=[("Personal", folder)], running=running())
    assert backup_places.synced_folders(unknown)[0].note is None  # the list couldn't be read


def test_an_account_whose_folder_is_gone_isn_t_offered(tmp_path: Path) -> None:
    machine = Machine(tmp_path, {}, "win32", onedrive=[("Personal", tmp_path / "gone")])
    assert backup_places.synced_folders(machine) == []


def test_a_mac_offers_its_cloud_storage_dropbox_and_icloud_when_signed_in(tmp_path: Path) -> None:
    home = tmp_path / "ana"
    storage = home / "Library" / "CloudStorage"
    icloud = home / "Library" / "Mobile Documents" / "com~apple~CloudDocs"
    dropbox = storage / "Dropbox"
    made(storage / "OneDrive-Personal", storage / "GoogleDrive-ana@example.com" / "My Drive",
         dropbox, icloud, home / "Dropbox")  # fmt: skip
    dropbox_info(home / ".dropbox", dropbox)
    machine = Machine(home, {}, "darwin", running=running("onedrive", "google drive"))
    assert labels(backup_places.synced_folders(machine)) == [
        ("OneDrive", "OneDrive (Personal)"),
        ("Google Drive", "Google Drive"),
        ("Dropbox", "Dropbox"),
    ]  # iCloud Drive only once an Apple account has it on
    prefs = home / "Library" / "Preferences"
    made(prefs)
    service = {"Name": "MOBILE_DOCUMENTS", "Enabled": True}
    accounts = {"Accounts": [{"AccountID": "ana@example.com", "Services": [service]}]}
    (prefs / "MobileMeAccounts.plist").write_bytes(plistlib.dumps(accounts))
    found = backup_places.synced_folders(machine)
    assert labels(found)[-1] == ("iCloud Drive", "iCloud Drive")
    assert found[1].root == str(storage / "GoogleDrive-ana@example.com" / "My Drive")
    assert found[2].note == "Dropbox isn't running, so this won't upload until it is."


def test_linux_offers_a_linked_dropbox_only(tmp_path: Path) -> None:
    home = tmp_path / "ana"
    made(home / "Dropbox")  # a folder of that name, no account linked
    assert backup_places.synced_folders(Machine(home, {}, "linux")) == []
    dropbox_info(home / ".dropbox", home / "Dropbox")
    machine = Machine(home, {}, "linux", running=running("dropbox"))
    assert labels(backup_places.synced_folders(machine)) == [("Dropbox", "Dropbox")]


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
