"""Voice on this computer (the desktop app): the speech model downloaded once, with
its size known first and progress shown, and the stt service run in-process."""

import hashlib
import importlib
import io
import json
import sys
import time
import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx2
import pytest

from ringkeeper import voice_engine, voice_local
from ringkeeper.config import Settings
from ringkeeper.voice import SttClient, VoiceError

FILES = {"config.json": b"{}", "model.bin": b"\x00" * 50_000, "vocabulary.txt": b"a\nb\n"}
# Fast (the default) and Accuracy, told apart by size.
MODELS = {"tiny": FILES, "small": {**FILES, "model.bin": b"\x00" * 120_000}}

PYPI = "https://files.pythonhosted.org/packages/ab/cd"


def fake_wheel(members: dict[str, str] | None = None) -> bytes:
    """A stand-in for the engine's wheels: a package, code in .data/purelib, and
    a script that isn't installed."""
    members = members or {
        "fakevoice/__init__.py": "HEARD = 'hello'\n",
        "fakevoice-1.0.dist-info/METADATA": "Name: fakevoice\nVersion: 1.0\n",
        "fakevoice-1.0.data/purelib/fakevoice_extra.py": "EXTRA = True\n",
        "fakevoice-1.0.data/scripts/fakevoice-cli": "#!python\n",
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, text in members.items():
            archive.writestr(name, text)
    return buffer.getvalue()


def wheel_list(folder: Path, wheel: bytes, *, python: str | None = None) -> Path:
    """voice-packages.json, as the build writes it, for one wheel."""
    listed = folder / "voice-packages.json"
    listed.write_text(
        json.dumps(
            {
                "python": python or f"{sys.version_info.major}.{sys.version_info.minor}",
                "wheels": [
                    {
                        "name": "fakevoice",
                        "version": "1.0",
                        "url": f"{PYPI}/fakevoice-1.0-py3-none-any.whl",
                        "sha256": hashlib.sha256(wheel).hexdigest(),
                        "size": len(wheel),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return listed


@pytest.fixture
def fake_engine(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Local voice looks for the stand-in package; whatever was imported goes after."""
    monkeypatch.setattr(voice_local, "ENGINE", "fakevoice")
    monkeypatch.setattr(sys, "path", list(sys.path))
    yield
    for name in ("fakevoice", "fakevoice_extra"):
        sys.modules.pop(name, None)


class FakeHub:
    """Hugging Face (the model) and PyPI (the engine's wheels)."""

    def __init__(self, *, cut: str | None = None, wheel: bytes = b"") -> None:
        self.cut = cut  # a file that arrives short
        self.wheel = wheel

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        url = str(request.url)
        if url == f"{PYPI}/fakevoice-1.0-py3-none-any.whl":
            return httpx2.Response(200, content=self.wheel)
        for model, files in MODELS.items():
            if url.endswith(f"/api/models/Systran/faster-whisper-{model}/tree/main"):
                listing = [{"type": "file", "path": p, "size": len(b)} for p, b in files.items()]
                listing += [
                    {"type": "file", "path": "README.md", "size": 10},
                    {"type": "directory", "path": "x"},
                ]
                return httpx2.Response(200, json=listing)
            for path, body in files.items():
                if url.endswith(f"/Systran/faster-whisper-{model}/resolve/main/{path}"):
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
    monkeypatch.setattr(voice_local, "engine_installed", lambda: True)  # a source checkout
    monkeypatch.setattr(voice_local, "transport", httpx2.MockTransport(FakeHub().handle))
    model = voice_local.LocalModel(tmp_path)
    before = model.status()
    assert (before["state"], before["total"], before["done"]) == ("absent", 50_006, 0)
    model.start()
    done = wait_for(model, "ready")
    assert (done["done"], done["total"]) == (50_006, 50_006)
    folder = tmp_path / "voice" / "tiny"
    assert sorted(p.name for p in folder.iterdir()) == [".complete", *sorted(FILES)]
    assert not (tmp_path / "voice" / "tiny.partial").exists()
    assert voice_local.LocalModel(tmp_path).ready()  # remembered across restarts
    model.remove()
    assert not folder.exists()


def test_an_incomplete_download_leaves_nothing_behind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(voice_local, "engine_installed", lambda: True)
    monkeypatch.setattr(
        voice_local, "transport", httpx2.MockTransport(FakeHub(cut="model.bin").handle)
    )
    model = voice_local.LocalModel(tmp_path)
    model.start()
    failed = wait_for(model, "failed")
    assert "didn't finish" in str(failed["error"])
    assert not (tmp_path / "voice" / "tiny").exists()
    assert not (tmp_path / "voice" / "tiny.partial").exists()


def test_accuracy_is_a_choice_with_its_size_shown_and_only_one_model_kept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(voice_local, "engine_installed", lambda: True)
    monkeypatch.setattr(voice_local, "transport", httpx2.MockTransport(FakeHub().handle))
    model = voice_local.LocalModel(tmp_path)
    before = model.status()
    assert before["choice"] == "fast"
    sizes = {c["key"]: (c["model"], c["download"], c["ready"]) for c in before["choices"]}
    assert sizes == {"fast": ("tiny", 50_006, False), "accuracy": ("small", 120_006, False)}
    model.start()
    wait_for(model, "ready")

    model.choose("accuracy")
    switched = model.status()
    assert (switched["state"], switched["total"]) == ("absent", 120_006)
    assert (tmp_path / "voice" / "tiny").is_dir()  # still there until Accuracy is in
    with pytest.raises(VoiceError, match="Download voice first"):
        model.service(Settings(mode="desktop", data_dir=tmp_path))
    model.start()
    done = wait_for(model, "ready")
    assert done["done"] == 120_006
    assert not (tmp_path / "voice" / "tiny").exists()
    assert {c["key"]: c["download"] for c in done["choices"]} == {"fast": 50_006, "accuracy": 0}
    assert voice_local.LocalModel(tmp_path).choice == "accuracy"  # remembered
    with pytest.raises(ValueError, match="No voice model"):
        model.choose("huge")


def test_a_model_downloaded_before_the_choice_keeps_being_used(tmp_path: Path) -> None:
    folder = tmp_path / "voice" / "small"
    folder.mkdir(parents=True)
    (folder / voice_local.DONE_MARK).write_text("ok\n")
    assert voice_local.LocalModel(tmp_path).choice == "accuracy"


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
    with pytest.raises(VoiceError, match="Download voice first"):
        voice_local.transcriber(settings)
    assert json.dumps(voice_local.model(settings).progress.__dict__)


# --- The engine, downloaded with the model (the installed app doesn't carry it) ---


@pytest.mark.usefixtures("fake_engine")
def test_the_engine_downloads_with_the_model_checked_and_unpacked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wheel = fake_wheel()
    wheels = voice_engine.load(wheel_list(tmp_path, wheel))
    hub = FakeHub(wheel=wheel)
    monkeypatch.setattr(voice_local, "transport", httpx2.MockTransport(hub.handle))
    model = voice_local.LocalModel(tmp_path, wheels)
    before = model.status()
    # One download, its size known before anything is fetched: engine and model.
    total = 50_006 + len(wheel)
    assert (before["engine"], before["state"], before["total"]) == (True, "absent", total)
    with pytest.raises(VoiceError, match="Download voice first"):
        model.service(Settings(mode="desktop", data_dir=tmp_path))
    model.start()
    done = wait_for(model, "ready")
    assert done["done"] == done["total"] == total

    heard = importlib.import_module("fakevoice").HEARD
    extra = importlib.import_module("fakevoice_extra").EXTRA
    assert (heard, extra) == ("hello", True)
    [folder] = (tmp_path / "voice").glob("engine-*")
    assert sorted(p.name for p in folder.iterdir() if p.name != "__pycache__") == [
        ".complete", "fakevoice", "fakevoice-1.0.dist-info", "fakevoice_extra.py",
    ]  # fmt: skip
    assert not list((tmp_path / "voice").glob("*.partial"))
    # Remembered across restarts; removing voice removes the engine too.
    assert voice_local.LocalModel(tmp_path, wheels).all_ready()
    model.remove()
    assert not folder.exists()


@pytest.mark.usefixtures("fake_engine")
def test_a_wheel_that_doesnt_match_its_hash_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wheels = voice_engine.load(wheel_list(tmp_path, fake_wheel()))
    swapped = fake_wheel({"fakevoice/__init__.py": "import os  # not what was listed\n"})
    hub = FakeHub(wheel=swapped)
    monkeypatch.setattr(voice_local, "transport", httpx2.MockTransport(hub.handle))
    model = voice_local.LocalModel(tmp_path, wheels)
    model.start()
    assert "didn't finish" in str(wait_for(model, "failed")["error"])
    assert not list((tmp_path / "voice").glob("engine-*"))
    assert not voice_local.engine_installed()


def test_a_wheel_cant_write_outside_its_folder(tmp_path: Path) -> None:
    evil = tmp_path / "evil.whl"
    evil.write_bytes(fake_wheel({"../escaped.py": "x = 1\n"}))
    with pytest.raises(ValueError, match="unsafe path"):
        voice_engine.unpack(evil, tmp_path / "into")
    assert not (tmp_path / "escaped.py").exists()


def test_an_updated_list_doesnt_reuse_the_old_engine_and_cleans_it(tmp_path: Path) -> None:
    old = tmp_path / "voice" / "engine-0123456789abcdef"
    (old / "fakevoice").mkdir(parents=True)
    (old / ".complete").write_text("ok\n")
    (tmp_path / "voice" / "engine-feedfacefeedface.partial").mkdir()
    wheels = voice_engine.load(wheel_list(tmp_path, fake_wheel()))
    model = voice_local.LocalModel(tmp_path, wheels)
    assert model.engine is not None
    assert not model.engine.ready()  # another version: asked again, never reused
    assert list((tmp_path / "voice").iterdir()) == []


def test_a_list_for_another_python_is_ignored(tmp_path: Path) -> None:
    assert voice_engine.load(wheel_list(tmp_path, fake_wheel(), python="2.7")) is None
    assert voice_engine.load(tmp_path / "missing.json") is None
