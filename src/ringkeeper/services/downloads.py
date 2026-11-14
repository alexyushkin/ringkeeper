"""Desktop: files the app saves for you (a table as CSV, a report) go into your Downloads
folder, and can be shown there.

The app window can't download a link the way a browser does, so the page sends the file
here instead. Only into the Downloads folder (RINGKEEPER_DOWNLOADS_DIR in tests), under a
plain file name of a known kind (no folders in it), and never over a file that's there: the
next free name is used ("table (2).csv"). Showing one shows only a file in that folder.
"""

import re
from pathlib import Path

from ringkeeper.config import Settings
from ringkeeper.errors import NotFoundError, RuleViolationError
from ringkeeper.services import task_files

KINDS = frozenset([".csv", ".md", ".txt", ".json"])  # .json: a connection's tool list
MAX_NAME = 120
MOST_COPIES = 999
_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = re.compile(r"^(con|prn|aux|nul|com\d|lpt\d)$", re.I)  # Windows' own names


def folder(settings: Settings) -> Path:
    return settings.downloads_dir or Path.home() / "Downloads"


def safe_name(name: str) -> str:
    """A plain file name: the last part of what was given, without characters a file name
    can't have. Refused if it isn't a kind the app saves (CSV, Markdown, text, JSON)."""
    plain = _UNSAFE.sub("", re.split(r"[\\/]", name)[-1]).strip()
    stem, dot, suffix = plain.rpartition(".")
    if not dot or f".{suffix.lower()}" not in KINDS:
        raise RuleViolationError("Only CSV, Markdown, text and JSON files are saved here.")
    stem = stem.strip(" .")[: MAX_NAME - len(suffix) - 1] or "download"
    if _RESERVED.match(stem):
        stem = f"{stem}-file"
    return f"{stem}.{suffix.lower()}"


def save(settings: Settings, name: str, text: str) -> Path:
    """Write it into the Downloads folder under the next free name; returns where."""
    into = folder(settings)
    plain = Path(safe_name(name))
    try:
        into.mkdir(parents=True, exist_ok=True)
        for number in range(1, MOST_COPIES + 1):
            path = into / (plain.name if number == 1 else f"{plain.stem} ({number}){plain.suffix}")
            try:
                with path.open("x", encoding="utf-8", newline="") as file:  # never over one
                    file.write(text)
            except FileExistsError:
                continue
            return path
    except OSError as error:
        raise RuleViolationError(
            f"Couldn't save it in {into} ({error.strerror}). Try again."
        ) from None
    raise RuleViolationError(f"There are too many files named {plain.name} in {into}.")


def show(settings: Settings, path: str) -> None:
    """Show a file saved here in its folder (only one in the Downloads folder)."""
    into = folder(settings).resolve()
    full = Path(path).resolve()
    if full.parent != into:
        raise RuleViolationError("Only files saved in your Downloads folder are shown from here.")
    if not full.is_file():
        raise NotFoundError("That file isn't in your Downloads folder any more.")
    task_files.show(full, reveal=True)
