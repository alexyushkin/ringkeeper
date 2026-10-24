"""The stt service: request rules on a fake engine, then the real image with a recording."""

import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import docker
import httpx2
import pytest

from ringkeeper_stt.config import ConfigError, SttConfig, load
from ringkeeper_stt.server import Decoded, DecodeError, Service, Transcript, serve
from tests.conftest import free_port

ROOT = Path(__file__).parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "voice-works.ogg"
IMAGE = "ringkeeper-stt:test"
MODELS_VOLUME = "ringkeeper_test_stt_models"  # kept between runs: the model is ~150 MB


class FakeEngine:
    def __init__(self, seconds: float = 2.0) -> None:
        self.seconds = seconds
        self.decoded: list[int] = []

    def decode(self, data: bytes) -> Decoded:
        if not data.startswith(b"OggS"):
            raise DecodeError("not ogg")
        self.decoded.append(len(data))
        return Decoded(samples=None, seconds=self.seconds)

    def transcribe(self, audio: Decoded) -> Transcript:
        return Transcript(text="voice works", language="en")


@pytest.fixture
def fake_stt() -> Iterator[tuple[str, FakeEngine]]:
    engine = FakeEngine()
    server = serve(Service(engine, SttConfig(max_seconds=60, max_bytes=1000, port=free_port())))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}", engine
    server.shutdown()
    server.server_close()


def test_config_defaults_and_overrides() -> None:
    assert load({}) == SttConfig()
    config = load({"RINGKEEPER_STT_MODEL": "base", "RINGKEEPER_STT_LANGUAGE": "de",
                   "RINGKEEPER_STT_MAX_SECONDS": "30"})  # fmt: skip
    assert (config.model, config.language, config.max_seconds) == ("base", "de", 30.0)
    assert load({"RINGKEEPER_STT_LANGUAGE": " "}).language is None  # auto-detect
    with pytest.raises(ConfigError, match="must be a number"):
        load({"RINGKEEPER_STT_MAX_BYTES": "lots"})


def test_transcribe_and_limits(fake_stt: tuple[str, FakeEngine]) -> None:
    url, engine = fake_stt
    with httpx2.Client(base_url=url) as http:
        assert http.get("/health").json() == {"status": "ok", "model": "small"}
        ok = http.post("/transcribe", content=b"OggS" + b"x" * 10)
        assert ok.status_code == 200
        assert ok.json() == {"text": "voice works", "language": "en", "duration": 2.0}

        # Too big: refused from Content-Length alone, never decoded.
        big = http.post("/transcribe", content=b"OggS" + b"x" * 2000)
        assert (big.status_code, big.json()["error"]) == (413, "too_large")
        assert engine.decoded == [14]

        engine.seconds = 61.0
        long = http.post("/transcribe", content=b"OggS")
        assert long.status_code == 413
        assert long.json() == {"error": "too_long", "seconds": 61.0, "max_seconds": 60}

        bad = http.post("/transcribe", content=b"not audio")
        assert (bad.status_code, bad.json()) == (422, {"error": "undecodable"})
        assert http.post("/other", content=b"").status_code == 404


# --- The real image ---


def _audio_files(container: Any) -> list[str]:
    """Audio-looking files anywhere outside the Python install, and anything in /tmp.

    The root filesystem is read-only, so /tmp (a tmpfs) and /models (the model
    cache) are the only places the service could write.
    """
    found = container.exec_run(
        ["sh", "-c", "find / /tmp /models -type f \\( -iname '*.ogg' -o -iname '*.oga' "
         "-o -iname '*.opus' -o -iname '*.wav' -o -iname '*.mp3' -o -iname '*.m4a' \\) "
         "-not -path '/usr/*' -not -path '/proc/*' -not -path '/sys/*' 2>/dev/null; "
         "find /tmp -type f 2>/dev/null"]
    )  # fmt: skip
    return [line for line in found.output.decode().splitlines() if line]


@pytest.fixture(scope="module")
def stt_container() -> Iterator[tuple[str, Any]]:
    client = docker.from_env()
    client.images.build(path=str(ROOT / "stt"), tag=IMAGE, rm=True)
    port = free_port()
    container = client.containers.run(
        IMAGE, detach=True, remove=True,
        environment={"RINGKEEPER_STT_MODEL": "base", "RINGKEEPER_STT_MAX_SECONDS": "10"},
        volumes={MODELS_VOLUME: {"bind": "/models", "mode": "rw"}},
        ports={"9000/tcp": ("127.0.0.1", port)},
        read_only=True, tmpfs={"/tmp": ""},  # noqa: S108 (as in docker-compose.yml)
    )  # fmt: skip
    url = f"http://127.0.0.1:{port}"
    try:
        deadline = time.monotonic() + 600  # the first run downloads the model
        while True:
            try:
                if httpx2.get(f"{url}/health", timeout=2).status_code == 200:
                    break
            except httpx2.HTTPError:
                pass
            container.reload()
            if container.status != "running" or time.monotonic() > deadline:
                logs: bytes = container.logs(tail=40)
                pytest.fail(f"stt never became healthy:\n{logs.decode(errors='replace')}")
            time.sleep(2)
        yield url, container
    finally:
        container.stop(timeout=5)


@pytest.mark.container
def test_the_real_service_transcribes_a_voice_note(stt_container: tuple[str, Any]) -> None:
    url, container = stt_container
    response = httpx2.post(
        f"{url}/transcribe", content=FIXTURE.read_bytes(),
        headers={"Content-Type": "audio/ogg"}, timeout=120,
    )  # fmt: skip
    assert response.status_code == 200, response.text
    body = response.json()
    words = body["text"].lower()
    assert "voice" in words, body
    assert "work" in words, body
    assert body["language"] == "en"
    assert 1.0 < body["duration"] < 10.0
    assert _audio_files(container) == []


@pytest.mark.container
def test_the_real_service_enforces_the_duration_limit(stt_container: tuple[str, Any]) -> None:
    url, container = stt_container
    # Concatenated Ogg streams decode as one: four copies run past the 10 s limit.
    response = httpx2.post(f"{url}/transcribe", content=FIXTURE.read_bytes() * 4, timeout=120)
    assert response.status_code == 413, response.text
    assert response.json()["error"] == "too_long"
    garbage = httpx2.post(f"{url}/transcribe", content=b"\x00" * 512, timeout=30)
    assert (garbage.status_code, garbage.json()) == (422, {"error": "undecodable"})
    assert _audio_files(container) == []
