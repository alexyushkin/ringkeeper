"""The speech engine's Python packages (faster-whisper and what it needs) for the
desktop app: not part of the installer, downloaded when voice is first used,
together with the model.

The installer carries only their list, voice-packages.json next to its Python
(written by shell/scripts/bundle.py from uv.lock): each wheel's PyPI address,
size and SHA-256, for this exact Python and platform. Every wheel is checked
against its hash and unpacked into the data folder, under a temporary folder
until all of them are in. The folder is named after the list, so an app update
that changes a version asks to download again (as the first time: never
silently), and the old folder is removed at the next start.

    python -m ringkeeper.voice_engine LIST FOLDER   # install, then import (the build's check)
"""

import contextlib
import hashlib
import importlib
import json
import logging
import shutil
import sys
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import httpx2

logger = logging.getLogger(__name__)

LIST_NAME = "voice-packages.json"
DONE_MARK = ".complete"
PREFIX = "engine-"


@dataclass(frozen=True)
class Wheel:
    name: str
    version: str
    url: str
    sha256: str
    size: int

    @property
    def filename(self) -> str:
        return self.url.rsplit("/", 1)[-1]


def default_list() -> Path:
    """Where the installer puts the list: beside its Python."""
    return Path(sys.prefix) / LIST_NAME


def load(path: Path) -> list[Wheel] | None:
    """The wheels for this Python, or None when there's no list (or it's for another)."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if data.get("python") != f"{sys.version_info.major}.{sys.version_info.minor}":
        return None
    fields = ("name", "version", "url", "sha256", "size")
    return [Wheel(**{k: w[k] for k in fields}) for w in data["wheels"]]


def _member_path(name: str, data_dir: str) -> PurePosixPath | None:
    """Where a wheel member goes under site-packages, or None to skip it. Code in
    `<name>.data/purelib|platlib` goes in with the rest; scripts and headers don't."""
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or ":" in name or "\\" in name:
        raise ValueError(f"unsafe path in a wheel: {name}")
    if path.parts and path.parts[0] == data_dir:
        if len(path.parts) > 2 and path.parts[1] in ("purelib", "platlib"):
            return PurePosixPath(*path.parts[2:])
        return None
    return path


def unpack(wheel: Path, into: Path) -> None:
    """Install a wheel by unpacking it: all a wheel needs, without pip."""
    with zipfile.ZipFile(wheel) as archive:
        data_dir = next(
            (n.split("/")[0] for n in archive.namelist() if n.split("/")[0].endswith(".data")),
            "",
        )
        for member in archive.infolist():
            if member.is_dir():
                continue
            relative = _member_path(member.filename, data_dir)
            if relative is None:
                continue
            target = into.joinpath(*relative.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member) as source, target.open("wb") as out:
                shutil.copyfileobj(source, out)


class Engine:
    """The engine's packages in the data folder: there or not, how big, fetch them."""

    def __init__(self, data_dir: Path, wheels: list[Wheel]) -> None:
        self.wheels = wheels
        digest = hashlib.sha256("\n".join(sorted(w.sha256 for w in wheels)).encode()).hexdigest()
        self.root = data_dir / "voice"
        self.folder = self.root / f"{PREFIX}{digest[:16]}"

    @property
    def size(self) -> int:
        return sum(w.size for w in self.wheels)

    def ready(self) -> bool:
        return (self.folder / DONE_MARK).is_file()

    def activate(self) -> None:
        """Make the packages importable in this process."""
        if self.ready() and str(self.folder) not in sys.path:
            sys.path.append(str(self.folder))
            importlib.invalidate_caches()

    def clean(self) -> None:
        """Drop other versions' folders and unfinished downloads."""
        found: list[Path] = list(self.root.glob(f"{PREFIX}*")) if self.root.is_dir() else []
        for old in found:
            if old != self.folder or not self.ready():
                shutil.rmtree(old, ignore_errors=True)

    def install(self, http: httpx2.Client, on_bytes: Callable[[int], None]) -> None:
        """Download, check and unpack every wheel; raises ValueError, OSError or
        httpx2.HTTPError, and leaves nothing half-done behind."""
        partial = self.folder.with_name(f"{self.folder.name}.partial")
        shutil.rmtree(partial, ignore_errors=True)
        (partial / "_wheels").mkdir(parents=True)
        try:
            for wheel in self.wheels:
                saved = partial / "_wheels" / wheel.filename
                digest = hashlib.sha256()
                written = 0
                with http.stream("GET", wheel.url) as response, saved.open("wb") as out:
                    response.raise_for_status()
                    for chunk in response.iter_bytes():
                        out.write(chunk)
                        digest.update(chunk)
                        written += len(chunk)
                        on_bytes(len(chunk))
                if written != wheel.size or digest.hexdigest() != wheel.sha256:
                    raise ValueError(f"{wheel.filename} doesn't match its listed size and hash")
                unpack(saved, partial)
                saved.unlink()
            shutil.rmtree(partial / "_wheels")
            (partial / DONE_MARK).write_text("ok\n", encoding="utf-8")
            shutil.rmtree(self.folder, ignore_errors=True)
            partial.rename(self.folder)
        finally:
            shutil.rmtree(partial, ignore_errors=True)

    def remove(self) -> None:
        """The done mark goes first, so a folder Windows won't fully delete while
        the engine is loaded counts as absent and is cleaned up at the next start."""
        with contextlib.suppress(OSError):
            (self.folder / DONE_MARK).unlink()
        shutil.rmtree(self.folder, ignore_errors=True)


def main(argv: list[str]) -> None:
    listed, folder = Path(argv[0]), Path(argv[1])
    wheels = load(listed)
    if not wheels:
        sys.exit(f"{listed}: no wheels for this Python")
    engine = Engine(folder, wheels)
    with httpx2.Client(timeout=120.0, follow_redirects=True) as http:
        engine.install(http, lambda _: None)
    engine.activate()
    for module in ("faster_whisper", "ctranslate2", "av", "onnxruntime", "numpy"):
        importlib.import_module(module)
    print(f"voice engine: {len(wheels)} packages, {engine.size / 1e6:.1f} MB, imports fine")


if __name__ == "__main__":
    main(sys.argv[1:])
