"""Files dropped on a request: kept in the data folder until a task starts, then copied
into the project, so the worker finds them where it works.

An attachment's id is "<uuid>/<name>". The name is cleaned of any folder, and a task
only ever gets files from the attachments folder, into `attachments/` in its project.
"""

import re
import shutil
import uuid
from pathlib import Path

from ringkeeper.config import Settings
from ringkeeper.errors import RuleViolationError

MAX_BYTES = 50_000_000
FOLDER = "attachments"
_ID = re.compile(r"^[0-9a-f]{32}/[^/\\]{1,120}$")


def _root(settings: Settings) -> Path:
    return settings.data_dir / FOLDER


def clean_name(name: str) -> str:
    """The file's own name, without any folder, safe on every system."""
    base = name.replace("\\", "/").rsplit("/", 1)[-1].strip().lstrip(".")
    base = re.sub(r'[<>:"|?*\x00-\x1f]', "_", base)[:120]
    return base or "file"


def save(settings: Settings, name: str, content: bytes) -> str:
    if len(content) > MAX_BYTES:
        raise RuleViolationError(f"That file is over {MAX_BYTES // 1_000_000} MB.")
    ident = f"{uuid.uuid4().hex}/{clean_name(name)}"
    path = _root(settings) / ident
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return ident


def name_of(ident: str) -> str:
    return ident.rsplit("/", 1)[-1]


def _kept(settings: Settings, ident: str) -> Path | None:
    if not _ID.match(ident):
        return None
    path = _root(settings) / ident
    return path if path.is_file() else None


def bring_in(settings: Settings, idents: list[str], workspace: str) -> list[str]:
    """Copy the files into the project's attachments folder. Their paths, as the worker
    sees them from the project's root. A file that's gone is left out."""
    folder = Path(workspace) / FOLDER
    copied: list[str] = []
    for ident in idents:
        source = _kept(settings, ident)
        if source is None:
            continue
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / source.name
        for n in range(2, 1000):
            if not target.exists():
                break
            target = folder / f"{source.stem}-{n}{source.suffix}"
        shutil.copy2(source, target)
        copied.append(f"{FOLDER}/{target.name}")
    return copied
