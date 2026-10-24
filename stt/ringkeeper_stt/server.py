"""POST /transcribe and GET /health over the standard library's HTTP server.

Audio lives only in memory for one request: it is read from the request
body, decoded from a buffer and dropped. Nothing is written to disk, and
neither audio nor transcripts are logged.
"""

import json
import logging
import threading
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Protocol

from ringkeeper_stt.config import SttConfig

logger = logging.getLogger(__name__)


class DecodeError(Exception):
    pass


@dataclass(frozen=True)
class Decoded:
    samples: Any  # 16 kHz mono float32, as the engine wants it
    seconds: float


@dataclass(frozen=True)
class Transcript:
    text: str
    language: str


class Engine(Protocol):
    def decode(self, data: bytes) -> Decoded: ...

    def transcribe(self, audio: Decoded) -> Transcript: ...


class Service:
    """The request rules, apart from HTTP. One transcription runs at a time."""

    def __init__(self, engine: Engine, config: SttConfig) -> None:
        self.engine = engine
        self.config = config
        self._lock = threading.Lock()

    def transcribe(self, data: bytes) -> tuple[int, dict[str, Any]]:
        if len(data) > self.config.max_bytes:
            return 413, {"error": "too_large", "max_bytes": self.config.max_bytes}
        try:
            audio = self.engine.decode(data)
        except DecodeError:
            return 422, {"error": "undecodable"}
        finally:
            del data
        seconds = round(audio.seconds, 1)
        if audio.seconds > self.config.max_seconds:
            return 413, {
                "error": "too_long", "seconds": seconds, "max_seconds": self.config.max_seconds,
            }  # fmt: skip
        with self._lock:
            result = self.engine.transcribe(audio)
        return 200, {"text": result.text, "language": result.language, "duration": seconds}


def handler_for(service: Service) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "ringkeeper-stt"
        sys_version = ""

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
            if self.path != "/transcribe":
                self._send(404, {"error": "not_found"})
                return
            try:
                length = int(self.headers.get("Content-Length", ""))
            except ValueError:
                self._send(411, {"error": "length_required"})
                return
            if length > service.config.max_bytes:
                # Refused before reading a byte of it.
                self.close_connection = True
                self._send(413, {"error": "too_large", "max_bytes": service.config.max_bytes})
                return
            try:
                status, body = service.transcribe(self.rfile.read(length))
            except Exception:
                logger.exception("transcription failed")
                status, body = 500, {"error": "failed"}
            self._send(status, body)

        def log_message(self, format: str, *args: Any) -> None:
            logger.info("%s %s", self.command, self.path)

    return Handler


def serve(service: Service, host: str = "0.0.0.0") -> ThreadingHTTPServer:  # noqa: S104
    return ThreadingHTTPServer((host, service.config.port), handler_for(service))
