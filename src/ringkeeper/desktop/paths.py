"""Where the desktop app keeps things, per operating system."""

import os
import sys
from pathlib import Path

APP_NAME = "Ringkeeper"


def data_dir() -> Path:
    """The per-user app data folder: the database, vault, logs and worker state."""
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / APP_NAME
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / APP_NAME.lower()


def workspaces_dir() -> Path:
    """Where new projects' folders go unless the user picks another place."""
    return Path.home() / "Documents" / APP_NAME
