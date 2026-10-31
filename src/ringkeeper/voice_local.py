"""Voice on this computer (the desktop app): the server's speech-to-text service
(ringkeeper_stt) run in-process, with its model downloaded once, on first use.

Two models to choose from (Settings > Voice): Fast, faster-whisper's `tiny`
(the default, a small first download), and Accuracy, its `small`, as on a
server. Only the chosen one is kept. Its files are listed with their sizes
first, so the app can say how big the download is, then fetched from Hugging
Face into the data folder with progress, under a temporary folder until every
file is complete. The audio never leaves this computer: it's decoded and
transcribed in memory, with the same limits as the server's stt service, and
dropped.

The engine (faster-whisper and the libraries under it) isn't in the installer
either: it's downloaded with the model, as one download (voice_engine). A
source checkout uses the voice extra instead (`uv sync --extra voice`).
"""

import contextlib
import importlib
import logging
import shutil
import sys
import threading
from dataclasses import dataclass, field
from importlib.util import find_spec
from pathlib import Path
from typing import Any, cast

import httpx2

from ringkeeper import voice_engine
from ringkeeper.config import Settings
from ringkeeper.voice import (
    LANGUAGES,
    UNREACHABLE,
    SttClient,
    Transcriber,
    VoiceError,
    answer,
    spoken_language,
)

logger = logging.getLogger(__name__)

HUB = "https://huggingface.co"
SKIPPED = {".gitattributes", "README.md"}
DONE_MARK = ".complete"
# Tests swap in httpx2.MockTransport; production uses the real network.
transport: httpx2.BaseTransport | None = None


@dataclass(frozen=True)
class Choice:
    key: str
    label: str
    model: str
    repo: str


CHOICES = {
    c.key: c
    for c in (
        Choice("fast", "Fast", "tiny", "Systran/faster-whisper-tiny"),
        Choice("accuracy", "Accuracy", "small", "Systran/faster-whisper-small"),
    )
}
DEFAULT = "fast"
CHOICE_FILE = "choice.txt"


class NotInstalledError(VoiceError):
    """No engine here, and none to download (a source checkout without the voice extra)."""


# What local voice imports (tests put a stand-in package here).
ENGINE = "faster_whisper"


def engine_installed() -> bool:
    return find_spec(ENGINE) is not None


@dataclass
class Progress:
    state: str = "absent"  # absent, downloading, ready, failed
    total: int | None = None  # bytes, once the file list is known
    done: int = 0
    error: str | None = None
    # Each choice's download, once listed.
    sizes: dict[str, int] = field(default_factory=dict[str, int])


class LocalModel:
    """Voice in the data folder (the speech model, and the engine when the app
    doesn't carry it): is it there, how big, fetch it, drop it."""

    def __init__(self, data_dir: Path, wheels: list[voice_engine.Wheel] | None = None) -> None:
        self.root = data_dir / "voice"
        self.choice = self._saved_choice()
        self.engine = voice_engine.Engine(data_dir, wheels) if wheels else None
        if self.engine:
            self.engine.clean()
            self.engine.activate()
        self._lock = threading.Lock()
        self.progress = Progress()
        self._service: Any = None

    def _saved_choice(self) -> str:
        with contextlib.suppress(OSError):
            saved = (self.root / CHOICE_FILE).read_text(encoding="utf-8").strip()
            if saved in CHOICES:
                return saved
        # Downloaded before there was a choice: keep using it.
        accuracy = CHOICES["accuracy"].model
        return "accuracy" if (self.root / accuracy / DONE_MARK).is_file() else DEFAULT

    @property
    def folder(self) -> Path:
        return self.root / CHOICES[self.choice].model

    def ready(self, choice: str | None = None) -> bool:
        """The (chosen) model is here."""
        return (self.root / CHOICES[choice or self.choice].model / DONE_MARK).is_file()

    def all_ready(self) -> bool:
        return self.ready() and engine_installed()

    def available(self) -> bool:
        """The engine is here, or can be downloaded."""
        return engine_installed() or self.engine is not None

    def _missing(
        self, choice: str | None = None
    ) -> tuple[list[tuple[str, int]], voice_engine.Engine | None]:
        """What a download fetches: the model's files, and the engine if needed."""
        files = [] if self.ready(choice) else self.files(choice)
        return files, None if engine_installed() else self.engine

    def _size(self, choice: str | None = None) -> int:
        files, engine = self._missing(choice)
        return sum(size for _, size in files) + (engine.size if engine else 0)

    def _http(self) -> httpx2.Client:
        return httpx2.Client(timeout=60.0, transport=transport, follow_redirects=True)

    def files(self, choice: str | None = None) -> list[tuple[str, int]]:
        """The model's files and sizes, from the hub's listing."""
        repo = CHOICES[choice or self.choice].repo
        with self._http() as http:
            response = http.get(f"{HUB}/api/models/{repo}/tree/main")
            response.raise_for_status()
            listed = cast(list[dict[str, Any]], response.json())
        return [
            (str(f["path"]), int(f.get("size") or 0))
            for f in listed
            if f.get("type") == "file" and f.get("path") not in SKIPPED
        ]

    def status(self) -> dict[str, Any]:
        """What the app shows: ready or not, the download's size, and its progress,
        what each choice would download, and the languages to choose from."""
        progress = self.progress
        if self.available() and progress.state != "downloading":
            for key in CHOICES:
                if key not in progress.sizes:
                    # Offline: the size shows once the download starts.
                    with contextlib.suppress(httpx2.HTTPError, ValueError, KeyError):
                        progress.sizes[key] = self._size(key)
            if progress.total is None and progress.state == "absent":
                progress.total = progress.sizes.get(self.choice)
        return {
            "engine": self.available(),
            "state": "ready" if self.all_ready() else progress.state,
            "total": progress.total,
            "done": progress.done,
            "error": progress.error,
            "choice": self.choice,
            "choices": [
                {
                    "key": c.key,
                    "label": c.label,
                    "model": c.model,
                    "ready": self.ready(c.key),
                    "download": progress.sizes.get(c.key),
                }
                for c in CHOICES.values()
            ],
            "languages": LANGUAGES,
        }

    def choose(self, choice: str) -> None:
        """Switch models. The new one downloads when asked; the old one goes once it's in."""
        if choice not in CHOICES:
            raise ValueError(f"No voice model called {choice!r}.")
        with self._lock:
            if self.progress.state == "downloading":
                raise VoiceError("Wait for the download to finish first.")
            self.root.mkdir(parents=True, exist_ok=True)
            (self.root / CHOICE_FILE).write_text(f"{choice}\n", encoding="utf-8")
            self.choice = choice
            self._service = None
            self.progress = Progress(sizes=self.progress.sizes)

    def start(self) -> None:
        with self._lock:
            if self.all_ready() or not self.available() or self.progress.state == "downloading":
                return
            self.progress = Progress(
                state="downloading", total=self.progress.total, sizes=self.progress.sizes
            )
        threading.Thread(target=self._download, daemon=True, name="rk-voice-model").start()

    def _count(self, size: int) -> None:
        self.progress.done += size

    def _download(self) -> None:
        try:
            files, engine = self._missing()
            self.progress.total = sum(size for _, size in files) + (engine.size if engine else 0)
            if engine:
                with self._http() as http:
                    engine.install(http, self._count)
                engine.activate()
            if files:
                self._download_model(files)
            self.progress.state = "ready"
        except (httpx2.HTTPError, OSError, ValueError) as error:
            logger.warning("voice download failed: %s", error)
            self.progress.state = "failed"
            self.progress.error = (
                "The download didn't finish. Check the internet connection and try again."
            )

    def _download_model(self, files: list[tuple[str, int]]) -> None:
        choice = CHOICES[self.choice]
        partial = self.root / f"{choice.model}.partial"
        try:
            shutil.rmtree(partial, ignore_errors=True)
            partial.mkdir(parents=True)
            with self._http() as http:
                for path, size in files:
                    target = partial / path
                    target.parent.mkdir(parents=True, exist_ok=True)
                    written = 0
                    with (
                        http.stream("GET", f"{HUB}/{choice.repo}/resolve/main/{path}") as response,
                        target.open("wb") as out,
                    ):
                        response.raise_for_status()
                        for chunk in response.iter_bytes():
                            out.write(chunk)
                            written += len(chunk)
                            self._count(len(chunk))
                    if size and written != size:
                        raise ValueError(f"{path} arrived incomplete ({written} of {size} bytes)")
            (partial / DONE_MARK).write_text("ok\n", encoding="utf-8")
            shutil.rmtree(self.folder, ignore_errors=True)
            partial.rename(self.folder)
            for other in CHOICES.values():
                if other.model != choice.model:
                    shutil.rmtree(self.root / other.model, ignore_errors=True)
            self.progress.sizes.clear()  # what each choice needs has changed
        finally:
            shutil.rmtree(partial, ignore_errors=True)

    def remove(self) -> None:
        with self._lock:
            self._service = None
            for choice in CHOICES.values():
                shutil.rmtree(self.root / choice.model, ignore_errors=True)
            if self.engine:
                self.engine.remove()
            self.progress = Progress()

    def service(self, settings: Settings) -> Any:
        """The stt service around the model, loaded once (it takes a few seconds)."""
        if not self.available():
            raise NotInstalledError("Voice isn't part of this build. Type it instead.")
        if not self.all_ready():
            raise VoiceError("Download voice first (Voice, in the request box).")
        with self._lock:
            if self._service is None:
                stt = _stt()
                config = stt["config"].SttConfig(
                    model=str(self.folder), language=settings.stt_language or None,
                    max_seconds=float(settings.stt_max_seconds),
                    max_bytes=settings.stt_max_bytes,
                )  # fmt: skip
                engine = stt["whisper"].WhisperEngine(config)
                self._service = stt["server"].Service(engine, config)
            return self._service


def _stt() -> dict[str, Any]:
    """The stt service's modules (ringkeeper_stt), from this install or a checkout."""
    if find_spec("ringkeeper_stt") is None:
        checkout = Path(__file__).resolve().parents[2] / "stt"
        if (checkout / "ringkeeper_stt").is_dir() and str(checkout) not in sys.path:
            sys.path.append(str(checkout))
    return {
        name: importlib.import_module(f"ringkeeper_stt.{name}")
        for name in ("config", "server", "whisper")
    }


_models: dict[Path, LocalModel] = {}
_models_lock = threading.Lock()


def model(settings: Settings) -> LocalModel:
    with _models_lock:
        key = settings.data_dir.resolve()
        if key not in _models:
            wheels = voice_engine.load(settings.voice_packages or voice_engine.default_list())
            _models[key] = LocalModel(key, wheels)
        return _models[key]


class LocalStt:
    """Same interface as voice.SttClient: transcribe in this process, raise VoiceError."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._service = model(settings).service(settings)

    def close(self) -> None:
        """Nothing to close: the model stays loaded for the next recording."""

    def transcribe(self, audio: bytes, hint: str | None = None) -> str:
        try:
            status, body = self._service.transcribe(audio, hint, spoken_language(self.settings))
        except Exception:
            logger.exception("local transcription failed")
            raise VoiceError(UNREACHABLE) from None
        return answer(status, body, len(audio), self.settings)


def transcriber(settings: Settings) -> Transcriber:
    """Where speech becomes text: this computer in the desktop app, the stt service
    on a server. Both take audio in memory and raise VoiceError."""
    if settings.mode == "desktop":
        return LocalStt(settings)
    return SttClient(settings)
