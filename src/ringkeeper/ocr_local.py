"""Reading scans on this computer (the desktop app): Tesseract, downloaded once, the first
time a task meets a picture or a scanned page and you say yes.

Like voice, it isn't in the installer. The installer carries ocr-packages.json, next to its
Python (written by shell/scripts/bundle.py from uv.lock): tesserocr's wheel, which has
Tesseract inside, and Pillow's, which gets the pictures out of a PDF. Each is checked against
its hash (voice_engine), and so is the language file. Nothing goes anywhere: the picture is
read on this computer and dropped.

A picture comes from outside, so it's read in a process of its own, with a time limit.

tesserocr's Mac wheels need macOS 15, newer than the oldest macOS the app runs on. The list
says so, and an older Mac isn't offered the download.

    python -m ringkeeper.ocr_local LIST FOLDER   # install, then read a scan (the build's check)
"""

import contextlib
import hashlib
import json
import logging
import platform
import shutil
import subprocess
import sys
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx2
import pypdf

from ringkeeper import voice_engine
from ringkeeper.config import Settings

logger = logging.getLogger(__name__)

AREA = "ocr"
LIST_NAME = "ocr-packages.json"
OFFER_FILE = "offer.txt"
WANTED = "wanted"
DECLINED = "declined"
READING_PREFIX = "reading-"
SECONDS = 60
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
# Tests swap in httpx2.MockTransport; production uses the real network.
transport: httpx2.BaseTransport | None = None

LANGUAGE_FOLDER = "tessdata"
LANGUAGE_FILE = "eng.traineddata"
LANGUAGE_COMMIT = "87416418657359cb625c412a48b6e1d6d41c29bd"
LANGUAGE_URL = (
    f"https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/{LANGUAGE_COMMIT}/"
    f"{LANGUAGE_FILE}"
)
LANGUAGE_SHA256 = "7d4322bd2a7749724879683fc3912cb542f19906c83bcc1a52132556427170b2"
LANGUAGE_SIZE = 4_113_088

# Both paths are relative to the area, the one folder here that's sure to be a plain path to
# Tesseract's C code, whatever the Windows user is called.
READ = """
import sys
sys.path.append(sys.argv[1])
import tesserocr
sys.stdout.reconfigure(encoding="utf-8")
print(tesserocr.file_to_text(sys.argv[3], lang="eng", path=sys.argv[2]))
"""


def default_list() -> Path:
    return Path(sys.prefix) / LIST_NAME


def macos_needed(path: Path) -> int | None:
    """The oldest macOS the list's wheels run on, when that's newer than the app's."""
    try:
        return int(json.loads(path.read_text(encoding="utf-8")).get("macos") or 0) or None
    except (OSError, ValueError):
        return None


def _too_old(needed: int | None) -> bool:
    if needed is None or sys.platform != "darwin":
        return False
    return int(platform.mac_ver()[0].split(".")[0] or 0) < needed


@dataclass
class Progress:
    state: str = "absent"  # absent, downloading, ready, failed
    done: int = 0
    error: str | None = None


class LocalOcr:
    """The reader in the data folder: is it there, should it be offered, fetch it, drop it."""

    def __init__(
        self,
        data_dir: Path,
        wheels: list[voice_engine.Wheel] | None = None,
        macos: int | None = None,
    ) -> None:
        self.root = data_dir / AREA
        self.engine = voice_engine.Engine(data_dir, wheels, AREA) if wheels else None
        self.macos = macos
        self._lock = threading.Lock()
        self.progress = Progress()
        if self.engine:
            self.engine.clean()
            self.engine.activate()
        found: list[Path] = list(self.root.glob(f"{READING_PREFIX}*")) if self.root.is_dir() else []
        for left in found:  # a read cut off when the app stopped
            shutil.rmtree(left, ignore_errors=True)

    @property
    def language(self) -> Path:
        return self.root / LANGUAGE_FOLDER / LANGUAGE_FILE

    def ready(self) -> bool:
        return self.engine is not None and self.engine.ready() and self.language.is_file()

    def available(self) -> bool:
        """It's here, or can be downloaded on this computer."""
        return self.engine is not None and not _too_old(self.macos)

    @property
    def size(self) -> int:
        return (self.engine.size if self.engine else 0) + LANGUAGE_SIZE

    def _offer(self) -> str:
        with contextlib.suppress(OSError):
            return (self.root / OFFER_FILE).read_text(encoding="utf-8").strip()
        return ""

    def _answer(self, answer: str) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / OFFER_FILE).write_text(f"{answer}\n", encoding="utf-8")

    def missed(self) -> bool:
        """A task met a scan it couldn't read. Returns whether the app is offering the
        download: the first time it is, and after "Not now" it's left to Settings."""
        with self._lock:
            if not self._offer():
                self._answer(WANTED)
            return self._offer() == WANTED

    def decline(self) -> None:
        with self._lock:
            self._answer(DECLINED)

    def status(self) -> dict[str, Any]:
        progress = self.progress
        ready = self.ready()
        return {
            "available": self.available(),
            "state": "ready" if ready else progress.state,
            "total": self.size,
            "done": progress.done,
            "error": progress.error,
            "offer": self.available() and not ready and self._offer() == WANTED,
            "needs_macos": self.macos if self.engine and not self.available() else None,
        }

    def start(self) -> None:
        with self._lock:
            if self.ready() or not self.available() or self.progress.state == "downloading":
                return
            self.progress = Progress(state="downloading")
        threading.Thread(target=self._download, daemon=True, name="rk-ocr").start()

    def _count(self, size: int) -> None:
        self.progress.done += size

    def _http(self) -> httpx2.Client:
        return httpx2.Client(timeout=60.0, transport=transport, follow_redirects=True)

    def _download(self) -> None:
        try:
            self.install()
            self.progress.state = "ready"
        except (httpx2.HTTPError, OSError, ValueError) as error:
            logger.warning("the scan reader's download failed: %s", error)
            self.progress.state = "failed"
            self.progress.error = (
                "The download didn't finish. Check the internet connection and try again."
            )

    def install(self) -> None:
        if self.engine is None:
            raise ValueError("there's no scan reader to download here")
        self.root.mkdir(parents=True, exist_ok=True)
        with self._http() as http:
            if not self.engine.ready():
                self.engine.install(http, self._count)
                self.engine.activate()
            if not self.language.is_file():
                self._fetch_language(http)

    def _fetch_language(self, http: httpx2.Client) -> None:
        self.language.parent.mkdir(parents=True, exist_ok=True)
        partial = self.language.with_name(f"{LANGUAGE_FILE}.partial")
        digest = hashlib.sha256()
        try:
            with http.stream("GET", LANGUAGE_URL) as response, partial.open("wb") as out:
                response.raise_for_status()
                for chunk in response.iter_bytes():
                    out.write(chunk)
                    digest.update(chunk)
                    self._count(len(chunk))
            if digest.hexdigest() != LANGUAGE_SHA256:
                raise ValueError(f"{LANGUAGE_FILE} doesn't match its listed hash")
            partial.replace(self.language)
        finally:
            partial.unlink(missing_ok=True)

    def remove(self) -> None:
        """Removing it is an answer too: it isn't offered again."""
        with self._lock:
            if self.engine:
                self.engine.remove()
            shutil.rmtree(self.language.parent, ignore_errors=True)
            self.progress = Progress()
            self._answer(DECLINED)

    def read(self, data: bytes, suffix: str) -> str:
        """The text in a picture, or "" if none could be read."""
        if self.engine is None:
            return ""
        with tempfile.TemporaryDirectory(dir=self.root, prefix=READING_PREFIX) as folder:
            picture = Path(folder) / f"picture{suffix}"
            picture.write_bytes(data)
            try:
                done = subprocess.run(  # noqa: S603 (this Python, and our own file)
                    [sys.executable, "-I", "-c", READ, str(self.engine.folder), LANGUAGE_FOLDER,
                     f"{picture.parent.name}/{picture.name}"],
                    cwd=self.root, capture_output=True, timeout=SECONDS, check=False,
                    creationflags=_NO_WINDOW,
                )  # fmt: skip
            except (OSError, subprocess.TimeoutExpired):
                return ""
        return done.stdout.decode("utf-8", "replace").strip() if done.returncode == 0 else ""


_models: dict[Path, LocalOcr] = {}
_models_lock = threading.Lock()


def model(settings: Settings) -> LocalOcr:
    with _models_lock:
        key = settings.data_dir.resolve()
        if key not in _models:
            listed = settings.ocr_packages or default_list()
            _models[key] = LocalOcr(key, voice_engine.load(listed), macos_needed(listed))
        return _models[key]


def main(argv: list[str]) -> None:
    listed, folder = Path(argv[0]), Path(argv[1])
    wheels = voice_engine.load(listed)
    if not wheels:
        sys.exit(f"{listed}: no wheels for this Python")
    reader = LocalOcr(folder, wheels, macos_needed(listed))
    reader.install()
    scan = Path(__file__).with_name("ocr_check.pdf")
    [picture] = list(pypdf.PdfReader(scan).pages[0].images)  # through Pillow, as a task's would
    text = reader.read(bytes(picture.data), Path(picture.name).suffix)
    if "Invoice 4217" not in text:
        sys.exit(f"the scan reader read {text!r}")
    print(f"scan reader: {len(wheels)} packages, {reader.size / 1e6:.1f} MB, reads a scan")


if __name__ == "__main__":
    main(sys.argv[1:])
