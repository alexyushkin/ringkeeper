"""The suite's own guard (conftest): touching the real app's keychain item or data
folder fails at once, before anything is read or written."""

import os
from pathlib import Path

import keyring
import pytest

from ringkeeper.desktop import paths
from tests import conftest


def test_the_real_app_is_off_limits() -> None:
    try:
        for target in (
            paths.data_dir() / "ringkeeper.db",
            Path(".ringkeeper") / "vault.json",
            paths.workspaces_dir() / "acme-web" / "notes.md",
        ):
            with pytest.raises(conftest.TouchedRealDataError, match="real app's data"):
                target.open("rb")
            conftest.touched.clear()
        with pytest.raises(conftest.TouchedRealDataError, match="real app's data"):
            os.listdir(paths.data_dir())
        conftest.touched.clear()
        with pytest.raises(conftest.TouchedRealDataError, match="keychain item"):
            keyring.get_password("Ringkeeper", "master-key")
        conftest.touched.clear()
        with pytest.raises(conftest.TouchedRealDataError, match="keychain item"):
            keyring.set_password("ringkeeper", "master-key", "x")  # any casing
    finally:
        conftest.touched.clear()  # this test's own trips don't stop the run
    # The tests' own keychain name and their own folders stay usable.
    assert os.environ["RINGKEEPER_KEYCHAIN_SERVICE"] == "RingkeeperTests"
