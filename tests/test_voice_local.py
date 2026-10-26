"""Voice on this computer (the desktop app): the speech model downloaded once, with
its size known first and progress shown, and the stt service run in-process."""

import json
import time
from pathlib import Path
from typing import Any

import httpx2
import pytest

from ringkeeper import voice_local
from ringkeeper.config import Settings
from ringkeeper.voice import SttClient, VoiceError

FILES = {"config.json": b"{}", "model.bin": b"\x00" * 50_000, "vocabulary.txt": b"a\nb\n"}


class FakeHub:
    def __init__(self, *, cut: str | None = None) -> None:
        self.cut = cut  # a file that arrives short

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        url = str(request.url)
        if url.endswith("/api/models/Systran/faster-whisper-small/tree/main"):
            listing = [{"type": "file", "path": p, "size": len(b)} for p, b in FILES.items()]
            listing += [
                {"type": "file", "path": "README.md", "size": 10},
                {"type": "directory", "path": "x"},
            ]
            return httpx2.Response(200, json=listing)
        for path, body in FILES.items():
            if url.endswith(f"/Systran/faster-whisper-small/resolve/main/{path}"):
                return httpx2.Response(200, content=body[:-1] if path == self.cut else body)
        return httpx2.Response(404)


def wait_for(model: voice_local.LocalModel, state: str) -> dict[str, Any]:
    deadline = time.monotonic() + 10
    while (status := model.status())["state"] != state:
        assert time.monotonic() < deadline, status
        time.sleep(0.05)
    return status


def test_the_model_is_sized_first_then_downloaded_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(voice_local, "transport", httpx2.MockTransport(FakeHub().handle))
    model = voice_local.LocalModel(tmp_path)
    before = model.status()
    assert (before["state"], before["total"], before["done"]) == ("absent", 50_006, 0)
    model.start()
    done = wait_for(model, "ready")
    assert (done["done"], done["total"]) == (50_006, 50_006)
    folder = tmp_path / "voice" / "small"
    assert sorted(p.name for p in folder.iterdir()) == [".complete", *sorted(FILES)]
    assert not (tmp_path / "voice" / "small.partial").exists()
    assert voice_local.LocalModel(tmp_path).ready()  # remembered across restarts
    model.remove()
    assert not folder.exists()


def test_an_incomplete_download_leaves_nothing_behind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        voice_local, "transport", httpx2.MockTransport(FakeHub(cut="model.bin").handle)
    )
    model = voice_local.LocalModel(tmp_path)
    model.start()
    failed = wait_for(model, "failed")
    assert "didn't finish" in str(failed["error"])
    assert not (tmp_path / "voice" / "small").exists()
    assert not (tmp_path / "voice" / "small.partial").exists()


class FakeService:
    def __init__(self, status: int, body: dict[str, Any]) -> None:
        self.answer = (status, body)
        self.got: list[tuple[bytes, str | None]] = []

    def transcribe(self, data: bytes, hint: str | None = None) -> tuple[int, dict[str, Any]]:
        self.got.append((data, hint))
        return self.answer


@pytest.mark.parametrize(
    ("status", "body", "said"),
    [
        (200, {"text": " add a line to acme-web "}, "add a line to acme-web"),
        (413, {"error": "too_long", "seconds": 200}, "the limit is 3:00"),
        (422, {"error": "undecodable"}, "couldn't read that audio"),
        (200, {"text": ""}, "couldn't hear anything"),
    ],
)
def test_local_speech_answers_like_the_stt_service(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, status: int, body: dict[str, Any], said: str
) -> None:
    settings = Settings(mode="desktop", data_dir=tmp_path)
    service = FakeService(status, body)
    def loaded(_self: voice_local.LocalModel, _settings: Settings) -> FakeService:
        return service

    monkeypatch.setattr(voice_local.LocalModel, "service", loaded)
    stt = voice_local.transcriber(settings)
    assert isinstance(stt, voice_local.LocalStt)
    if status == 200 and body["text"].strip():
        assert stt.transcribe(b"audio", "acme-web") == said
        assert service.got == [(b"audio", "acme-web")]
    else:
        with pytest.raises(VoiceError, match=said):
            stt.transcribe(b"audio")


def test_a_server_still_uses_its_stt_service(tmp_path: Path) -> None:
    stt = voice_local.transcriber(Settings(mode="server", data_dir=tmp_path))
    assert isinstance(stt, SttClient)
    stt.close()


def test_without_the_model_or_the_engine_it_says_what_to_do(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = Settings(mode="desktop", data_dir=tmp_path)
    monkeypatch.setattr(voice_local, "engine_installed", lambda: False)
    with pytest.raises(voice_local.NotInstalledError, match="isn't part of this build"):
        voice_local.transcriber(settings)
    monkeypatch.setattr(voice_local, "engine_installed", lambda: True)
    with pytest.raises(VoiceError, match="Download the speech model first"):
        voice_local.transcriber(settings)
    assert json.dumps(voice_local.model(settings).progress.__dict__)
