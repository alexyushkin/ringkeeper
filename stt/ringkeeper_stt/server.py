"""POST /transcribe and GET /health over the standard library's HTTP server.

Audio lives only in memory for one request: it is read from the request
body, decoded from a buffer and dropped. Nothing is written to disk, and
neither audio nor transcripts are logged.

A few kilobytes of compressed audio can decode to hours of samples, so:
only known audio formats are opened (by their first bytes), decoding stops
as soon as it passes the length limit, only a couple of requests are
handled at once, and slow clients are cut off.
"""

import json
import logging
import re
import threading
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Protocol
from urllib.parse import parse_qs, urlsplit

from ringkeeper_stt.config import SttConfig

logger = logging.getLogger(__name__)

MAX_HINT_CHARS = 500
LANGUAGE = re.compile(r"[a-z]{2,3}")  # a Whisper code; "auto" detects it
MAX_IN_FLIGHT = 2  # requests read and decoded at once; one transcribes at a time
SOCKET_TIMEOUT = 30.0  # seconds a client may stall while sending


class DecodeError(Exception):
    pass


class TooLongError(Exception):
    def __init__(self, seconds: float) -> None:
        super().__init__(f"longer than {seconds} s")
        self.seconds = seconds


# What each accepted container starts with, and the demuxer to open it with
# (never left to probing: some demuxers, like playlists, open other files).
_MAGIC: tuple[tuple[int, bytes, str], ...] = (
    (0, b"OggS", "ogg"),  # Telegram voice notes (opus), Firefox
    (0, b"\x1a\x45\xdf\xa3", "matroska"),  # webm: Chrome, Android
    (4, b"ftyp", "mp4"),  # iOS Safari
    (0, b"RIFF", "wav"),
    (0, b"fLaC", "flac"),
    (0, b"ID3", "mp3"),
)


def sniff(data: bytes) -> str | None:
    """The demuxer for this audio, or None if it isn't a format we accept."""
    for offset, magic, demuxer in _MAGIC:
        if data[offset : offset + len(magic)] == magic:
            return demuxer
    return None


@dataclass(frozen=True)
class Decoded:
    samples: Any  # 16 kHz mono float32, as the engine wants it
    seconds: float


@dataclass(frozen=True)
class Transcript:
    text: str
    language: str


class Engine(Protocol):
    def decode(self, data: bytes, max_seconds: float) -> Decoded:
        """Raises DecodeError, or TooLongError once past max_seconds."""
        ...

    def transcribe(
        self, audio: Decoded, hint: str | None = None, language: str | None = None
    ) -> Transcript:
        """`language`: a Whisper code, or None to detect it."""
        ...


class Service:
    """The request rules, apart from HTTP. One transcription runs at a time."""

    def __init__(self, engine: Engine, config: SttConfig) -> None:
        self.engine = engine
        self.config = config
        self._lock = threading.Lock()
        self.slots = threading.BoundedSemaphore(MAX_IN_FLIGHT)

    def transcribe(
        self, data: bytes, hint: str | None = None, language: str | None = None
    ) -> tuple[int, dict[str, Any]]:
        """`language`: a Whisper code, "auto" to detect it, or None for the configured one."""
        if refused := self._refuse(data, language):
            return refused
        spoken = self.config.language if language is None else language
        if spoken == "auto":
            spoken = None
        try:
            audio = self.engine.decode(data, self.config.max_seconds)
        except DecodeError:
            return 422, {"error": "undecodable"}
        except TooLongError:
            return 413, {"error": "too_long", "max_seconds": self.config.max_seconds}
        finally:
            del data
        seconds = round(audio.seconds, 1)
        if audio.seconds > self.config.max_seconds:
            return 413, {
                "error": "too_long", "seconds": seconds, "max_seconds": self.config.max_seconds,
            }  # fmt: skip
        with self._lock:
            result = self.engine.transcribe(audio, (hint or "")[:MAX_HINT_CHARS] or None, spoken)
        return 200, {"text": result.text, "language": result.language, "duration": seconds}

    def _refuse(self, data: bytes, language: str | None) -> tuple[int, dict[str, Any]] | None:
        """Checked before decoding anything."""
        if language not in (None, "auto") and not LANGUAGE.fullmatch(language):
            return 422, {"error": "bad_language"}
        if len(data) > self.config.max_bytes:
            return 413, {"error": "too_large", "max_bytes": self.config.max_bytes}
        if sniff(data) is None:
            return 422, {"error": "undecodable"}
        return None


def handler_for(service: Service) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "ringkeeper-stt"
        sys_version = ""
        timeout = SOCKET_TIMEOUT

        def _send(self, status: int, body: dict[str, Any]) -> None:
            payload = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self) -> None:
            if self.path == "/health":
                self._send(200, {"status": "ok", "model": service.config.model})
            else:
                self._send(404, {"error": "not_found"})

        def do_POST(self) -> None:
            url = urlsplit(self.path)
            if url.path != "/transcribe":
                self._send(404, {"error": "not_found"})
                return
            try:
                length = int(self.headers.get("Content-Length", ""))
            except ValueError:
                length = -1
            if length < 0:
                self.close_connection = True
                self._send(411, {"error": "length_required"})
                return
            if length > service.config.max_bytes:
                # Refused before reading a byte of it.
                self.close_connection = True
                self._send(413, {"error": "too_large", "max_bytes": service.config.max_bytes})
                return
            if not service.slots.acquire(blocking=False):
                self.close_connection = True
                self._send(503, {"error": "busy"})
                return
            try:
                query = parse_qs(url.query)
                hint = query.get("hint", [""])[0]
                language = query.get("language", [""])[0] or None
                status, body = service.transcribe(self.rfile.read(length), hint, language)
            except TimeoutError:
                self.close_connection = True
                return  # the client stalled: nothing to answer
            except Exception:
                logger.exception("transcription failed")
                status, body = 500, {"error": "failed"}
            finally:
                service.slots.release()
            self._send(status, body)

        def log_message(self, format: str, *args: Any) -> None:
            logger.info("%s %s", self.command, urlsplit(self.path).path)  # never the hint

    return Handler


def serve(service: Service, host: str = "0.0.0.0") -> ThreadingHTTPServer:  # noqa: S104
    return ThreadingHTTPServer((host, service.config.port), handler_for(service))
