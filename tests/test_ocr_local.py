"""The scan reader: optional, offered once, checked by its hash, and read in its own process.

Tesseract isn't here in these tests: a stand-in wheel takes its place. The build reads a real
scan with the real wheels on all three platforms (ocr_local's main)."""

import hashlib
import io
import json
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Any

import httpx2
import pypdf
import pytest

from ringkeeper import ocr_local, voice_engine
from ringkeeper.adapters import extract
from ringkeeper.config import get_settings
from tests.test_voice_local import wait_for

PYPI = "https://files.pythonhosted.org/packages/ab/cd"
LANGUAGE = b"a language"
SCAN = Path(ocr_local.__file__).with_name("ocr_check.pdf").read_bytes()


def stand_in() -> bytes:
    """tesserocr's wheel, as far as the reader can tell: it says what it was asked to read."""
    code = (
        "from pathlib import Path\n"
        "def file_to_text(name, lang, path):\n"
        "    assert Path(path, 'eng.traineddata').is_file()\n"
        "    return f'{lang}: ' + Path(name).read_text()\n"
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("tesserocr/__init__.py", code)
        archive.writestr("tesserocr-1.0.dist-info/METADATA", "Name: tesserocr\nVersion: 1.0\n")
    return buffer.getvalue()


def wheels(wheel: bytes) -> list[voice_engine.Wheel]:
    return [
        voice_engine.Wheel(
            "tesserocr", "1.0", f"{PYPI}/tesserocr-1.0-py3-none-any.whl",
            hashlib.sha256(wheel).hexdigest(), len(wheel),
        )
    ]  # fmt: skip


class FakeHosts:
    def __init__(self, wheel: bytes, language: bytes = LANGUAGE) -> None:
        self.wheel = wheel
        self.language = language

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        if str(request.url) == ocr_local.LANGUAGE_URL:
            return httpx2.Response(200, content=self.language)
        return httpx2.Response(200, content=self.wheel)


@pytest.fixture
def reader(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ocr_local.LocalOcr:
    wheel = stand_in()
    monkeypatch.setattr(ocr_local, "transport", httpx2.MockTransport(FakeHosts(wheel).handle))
    monkeypatch.setattr(ocr_local, "LANGUAGE_SHA256", hashlib.sha256(LANGUAGE).hexdigest())
    found = ocr_local.LocalOcr(tmp_path, wheels(wheel))
    monkeypatch.setattr(extract, "_local", lambda: found)
    return found


def downloaded(reader: ocr_local.LocalOcr) -> dict[str, Any]:
    reader.start()
    return wait_for(reader, "ready")  # type: ignore[arg-type]


def test_it_downloads_once_and_reads_in_a_process_of_its_own(reader: ocr_local.LocalOcr) -> None:
    assert reader.status()["state"] == "absent"
    assert not reader.ready()
    status = downloaded(reader)
    assert status["offer"] is False
    assert reader.ready()
    assert reader.read(b"Invoice 42", ".png") == "eng: Invoice 42"
    assert not list(reader.root.glob(f"{ocr_local.READING_PREFIX}*"))  # the picture is gone
    assert "tesserocr" not in sys.modules


def test_a_language_file_that_doesnt_match_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wheel = stand_in()
    hosts = FakeHosts(wheel, language=b"something else")
    monkeypatch.setattr(ocr_local, "transport", httpx2.MockTransport(hosts.handle))
    found = ocr_local.LocalOcr(tmp_path, wheels(wheel))
    found.start()
    status = wait_for(found, "failed")  # type: ignore[arg-type]
    assert "didn't finish" in status["error"]
    assert not found.language.exists()
    assert not found.ready()


def test_a_reader_that_crashes_or_hangs_reads_nothing(
    reader: ocr_local.LocalOcr, monkeypatch: pytest.MonkeyPatch
) -> None:
    downloaded(reader)
    monkeypatch.setattr(ocr_local, "READ", "import sys; sys.exit(3)")
    assert reader.read(b"x", ".png") == ""

    def hangs(*_args: object, **_kwargs: object) -> None:
        raise subprocess.TimeoutExpired("python", 60)

    monkeypatch.setattr(ocr_local.subprocess, "run", hangs)
    assert reader.read(b"x", ".png") == ""
    assert not list(reader.root.glob(f"{ocr_local.READING_PREFIX}*"))


def test_it_is_offered_the_first_time_and_not_after_not_now(
    reader: ocr_local.LocalOcr, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(extract.shutil, "which", lambda _name="": None)
    assert reader.status()["offer"] is False  # nothing has needed it yet
    first = extract.extract("scan.png", "image/png", b"\x89PNG")
    assert first.text == ""
    assert first.note == extract.OFFERED
    assert reader.status()["offer"] is True
    reader.decline()
    assert reader.status()["offer"] is False
    again = extract.extract("scan.png", "image/png", b"\x89PNG")
    assert again.note == extract.NOT_DOWNLOADED
    assert reader.status()["offer"] is False
    assert ocr_local.LocalOcr(reader.root.parent, wheels(stand_in())).status()["offer"] is False


def test_removing_it_is_an_answer_too(reader: ocr_local.LocalOcr) -> None:
    downloaded(reader)
    reader.remove()
    assert not reader.ready()
    assert reader.status()["state"] == "absent"
    assert reader.missed() is False


def test_a_scanned_pdf_offers_it_and_a_blank_page_doesnt(
    reader: ocr_local.LocalOcr, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(extract.shutil, "which", lambda _name="": None)
    blank = io.BytesIO()
    writer = pypdf.PdfWriter()
    writer.add_blank_page(100, 100)
    writer.write(blank)
    plain = extract.extract("blank.pdf", "application/pdf", blank.getvalue())
    assert plain.note == "1 of 1 pages had no text that could be read."
    assert reader.status()["offer"] is False
    scan = extract.extract("scan.pdf", "application/pdf", SCAN)
    assert scan.note
    assert "1 of 1 pages" in scan.note
    assert extract.OFFERED in scan.note
    assert reader.status()["offer"] is True


def test_with_the_reader_a_scanned_pdf_is_read(
    reader: ocr_local.LocalOcr, monkeypatch: pytest.MonkeyPatch
) -> None:
    downloaded(reader)
    asked: list[bytes] = []

    def read(data: bytes, _suffix: str) -> str:
        asked.append(data)
        return "Invoice 4217"

    monkeypatch.setattr(extract, "_ocr", read)
    found = extract.extract("scan.pdf", "application/pdf", SCAN)
    assert "Invoice 4217" in found.text
    assert found.note is None
    assert asked
    assert asked[0].startswith(b"\x89PNG")  # the page's picture, taken out by PDFium


def test_a_long_scan_stops_and_says_so(
    reader: ocr_local.LocalOcr, monkeypatch: pytest.MonkeyPatch
) -> None:
    downloaded(reader)
    monkeypatch.setattr(extract, "OCR_BUDGET_SECONDS", -1)
    found = extract.extract("scan.pdf", "application/pdf", SCAN)
    assert found.note
    assert "The pictures on pages 1 weren't read, for lack of time" in found.note


def test_tesseract_alone_reads_a_scanned_pdf_without_pillow(
    reader: ocr_local.LocalOcr, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PDFium takes the page's picture out, so the scan reader's Pillow isn't needed."""
    monkeypatch.setattr(extract.shutil, "which", lambda _name="": "tesseract")

    class Done:
        returncode = 0
        stdout = b"Invoice 4217"

    def run(*_args: object, **_kwargs: object) -> Done:
        return Done()

    monkeypatch.setattr(extract.subprocess, "run", run)
    found = extract.extract("scan.pdf", "application/pdf", SCAN)
    assert "Invoice 4217" in found.text
    assert found.note is None
    assert reader.status()["offer"] is False


def test_a_server_has_nothing_to_offer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    found = ocr_local.LocalOcr(tmp_path)
    monkeypatch.setattr(extract, "_local", lambda: found)
    monkeypatch.setattr(extract.shutil, "which", lambda _name="": None)
    assert extract.extract("scan.png", "image/png", b"\x89PNG").note == extract.NO_OCR
    status = found.status()
    assert status["available"] is False
    assert status["offer"] is False
    assert status["needs_macos"] is None
    found.start()
    assert found.status()["state"] == "absent"


def test_an_older_mac_isnt_offered_a_wheel_it_cant_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ocr_local.sys, "platform", "darwin")
    monkeypatch.setattr(ocr_local.platform, "mac_ver", lambda: ("14.6.1", ("", "", ""), ""))
    older = ocr_local.LocalOcr(tmp_path, wheels(stand_in()), macos=15)
    assert older.available() is False
    assert older.status()["needs_macos"] == 15
    monkeypatch.setattr(ocr_local.platform, "mac_ver", lambda: ("15.1", ("", "", ""), ""))
    assert older.available() is True
    assert older.status()["needs_macos"] is None


def test_the_list_says_which_macos_it_needs(tmp_path: Path) -> None:
    listed = tmp_path / "ocr-packages.json"
    listed.write_text(json.dumps({"python": "3.12", "macos": 15, "wheels": []}), encoding="utf-8")
    assert ocr_local.macos_needed(listed) == 15
    assert ocr_local.macos_needed(tmp_path / "missing.json") is None


def test_the_check_scan_has_no_text_layer_and_one_picture() -> None:
    page = pypdf.PdfReader(io.BytesIO(SCAN)).pages[0]
    assert not page.extract_text().strip()
    assert len(page.images.keys()) == 1


def test_the_model_is_kept_per_data_folder() -> None:
    settings = get_settings()
    assert ocr_local.model(settings) is ocr_local.model(settings)
