"""Voice on this computer (the desktop app): the server's speech-to-text service
(ringkeeper_stt) run in-process, with its model downloaded once, on first use.

The model is faster-whisper's `small`, as on a server. Its files are listed
with their sizes first, so the app can say how big the download is, then
fetched from Hugging Face into the data folder with progress, under a
temporary folder until every file is complete. The audio never leaves this
computer: it's decoded and transcribed in memory, with the same limits as
the server's stt service, and dropped.

The engine (faster-whisper) is part of the app; only the model is downloaded.
"""

import contextlib
import importlib
import logging
import shutil
import sys
import threading
from dataclasses import dataclass
from importlib.util import find_spec
from pathlib import Path
from typing import Any, cast

import httpx2

from ringkeeper.config import Settings
from ringkeeper.voice import UNREACHABLE, SttClient, Transcriber, VoiceError, answer

logger = logging.getLogger(__name__)

MODEL = "small"
REPO = "Systran/faster-whisper-small"
HUB = "https://huggingface.co"
SKIPPED = {".gitattributes", "README.md"}
DONE_MARK = ".complete"
# Tests swap in httpx2.MockTransport; production uses the real network.
transport: httpx2.BaseTransport | None = None


class NotInstalledError(VoiceError):
    """The engine isn't part of this build (a source checkout without the voice extra)."""


def engine_installed() -> bool:
    return find_spec("faster_whisper") is not None


@dataclass
class Progress:
    state: str = "absent"  # absent, downloading, ready, failed
    total: int | None = None  # bytes, once the file list is known
    done: int = 0
    error: str | None = None


class LocalModel:
    """The speech model in the data folder: is it there, how big, fetch it, drop it."""

    def __init__(self, data_dir: Path) -> None:
        self.folder = data_dir / "voice" / MODEL
        self._lock = threading.Lock()
        self.progress = Progress(state="ready" if self.ready() else "absent")
        self._service: Any = None

    def ready(self) -> bool:
        return (self.folder / DONE_MARK).is_file()

    def _http(self) -> httpx2.Client:
        return httpx2.Client(timeout=60.0, transport=transport, follow_redirects=True)

    def files(self) -> list[tuple[str, int]]:
        """The model's files and sizes, from the hub's listing."""
        with self._http() as http:
            response = http.get(f"{HUB}/api/models/{REPO}/tree/main")
            response.raise_for_status()
            listed = cast(list[dict[str, Any]], response.json())
        return [
            (str(f["path"]), int(f.get("size") or 0))
            for f in listed
            if f.get("type") == "file" and f.get("path") not in SKIPPED
        ]

    def status(self) -> dict[str, Any]:
        """What the app shows: ready or not, the download's size, and its progress."""
        progress = self.progress
        if progress.total is None and progress.state == "absent":
            # Offline: the size shows once the download starts.
            with contextlib.suppress(httpx2.HTTPError, ValueError, KeyError):
                progress.total = sum(size for _, size in self.files())
        return {
            "engine": engine_installed(),
            "state": "ready" if self.ready() else progress.state,
            "total": progress.total,
            "done": progress.done,
            "error": progress.error,
        }

    def start(self) -> None:
        with self._lock:
            if self.ready() or self.progress.state == "downloading":
                return
            self.progress = Progress(state="downloading", total=self.progress.total)
        threading.Thread(target=self._download, daemon=True, name="rk-voice-model").start()

    def _download(self) -> None:
        partial = self.folder.with_name(f"{MODEL}.partial")
        try:
            files = self.files()
            self.progress.total = sum(size for _, size in files)
            shutil.rmtree(partial, ignore_errors=True)
            partial.mkdir(parents=True)
            with self._http() as http:
                for path, size in files:
                    target = partial / path
                    target.parent.mkdir(parents=True, exist_ok=True)
                    written = 0
                    with (
                        http.stream("GET", f"{HUB}/{REPO}/resolve/main/{path}") as response,
                        target.open("wb") as out,
                    ):
                        response.raise_for_status()
                        for chunk in response.iter_bytes():
                            out.write(chunk)
                            written += len(chunk)
                            self.progress.done += len(chunk)
                    if size and written != size:
                        raise ValueError(f"{path} arrived incomplete ({written} of {size} bytes)")
            (partial / DONE_MARK).write_text("ok\n", encoding="utf-8")
            shutil.rmtree(self.folder, ignore_errors=True)
            partial.rename(self.folder)
            self.progress.state = "ready"
        except (httpx2.HTTPError, OSError, ValueError) as error:
            logger.warning("voice model download failed: %s", error)
            shutil.rmtree(partial, ignore_errors=True)
            self.progress.state = "failed"
            self.progress.error = (
                "The download didn't finish. Check the internet connection and try again."
            )

    def remove(self) -> None:
        with self._lock:
            self._service = None
            shutil.rmtree(self.folder, ignore_errors=True)
            self.progress = Progress()

    def service(self, settings: Settings) -> Any:
        """The stt service around the model, loaded once (it takes a few seconds)."""
        if not engine_installed():
            raise NotInstalledError("Voice isn't part of this build. Type it instead.")
        if not self.ready():
            raise VoiceError("Download the speech model first (Voice, in the request box).")
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
            _models[key] = LocalModel(key)
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
            status, body = self._service.transcribe(audio, hint)
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
