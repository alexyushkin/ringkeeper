"""The reports Check my setup and the Self-test leave: one Markdown file per day, in their
own folder under the data folder (setup-check/2026-11-11.md, self-test/2026-11-11.md).

Listed newest first, read back by day, and shown in their folder (desktop). Only a file
named like a day is ever read, and only in that folder.
"""

import re
from pathlib import Path
from typing import Literal

from ringkeeper.config import Settings
from ringkeeper.errors import NotFoundError
from ringkeeper.scenarios import self_test
from ringkeeper.services import setup_check

Kind = Literal["setup-check", "self-test"]
FOLDERS: dict[str, str] = {"setup-check": setup_check.FOLDER, "self-test": self_test.FOLDER}
DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def folder(settings: Settings, kind: Kind) -> Path:
    return (settings.data_dir / FOLDERS[kind]).absolute()


def listed(settings: Settings, kind: Kind) -> list[dict[str, str | int]]:
    """Every report kept, newest first: its day and size."""
    here = folder(settings, kind)
    found = [p for p in here.glob("*.md") if DAY.match(p.stem)] if here.is_dir() else []
    return [{"day": p.stem, "bytes": p.stat().st_size} for p in sorted(found, reverse=True)]


def path(settings: Settings, kind: Kind, day: str) -> Path:
    """The report from that day (NotFoundError: there's none)."""
    found = folder(settings, kind) / f"{day}.md"
    if not DAY.match(day) or not found.is_file():
        raise NotFoundError(f"There's no report from {day}.")
    return found


def read(settings: Settings, kind: Kind, day: str) -> str:
    return path(settings, kind, day).read_text(encoding="utf-8")
