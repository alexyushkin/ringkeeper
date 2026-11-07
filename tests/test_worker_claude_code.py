"""The worker image fetches Claude Code on first start instead of shipping it."""

import base64
import hashlib
import io
import json
import re
import tarfile
from pathlib import Path
from typing import Any

import pytest
from claude_agent_sdk._cli_version import __cli_version__

from ringkeeper_worker import claude_code

ROOT = Path(__file__).parents[1]
PROGRAM = b"#!/bin/sh\necho 'the real one'\n"


def _package(program: bytes = PROGRAM) -> bytes:
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w:gz") as tar:
        for name, data in (("package/package.json", b"{}"), ("package/claude", program)):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return out.getvalue()


def _integrity(data: bytes) -> str:
    return "sha512-" + base64.b64encode(hashlib.sha512(data).digest()).decode()


class Registry:
    """urlopen, answering for the pinned tarball and counting downloads."""

    def __init__(self, body: bytes) -> None:
        self.body = body
        self.calls: list[str] = []

    def __call__(self, url: str, timeout: float) -> Any:
        self.calls.append(url)
        return io.BytesIO(self.body)


@pytest.fixture
def pin(tmp_path: Path) -> Path:
    path = tmp_path / "claude-code.json"
    package = {"tarball": "https://registry.npmjs.org/x.tgz", "integrity": _integrity(_package())}
    path.write_text(
        json.dumps({"version": "9.9.9", "packages": {"x64": package, "arm64": package}})
    )
    return path


@pytest.fixture
def no_bundled_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(claude_code, "bundled", lambda: False)


@pytest.mark.usefixtures("no_bundled_cli")
def test_fetched_once_then_reused(
    tmp_path: Path, pin: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry = Registry(_package())
    monkeypatch.setattr(claude_code.urllib.request, "urlopen", registry)
    said: list[str] = []
    cache = tmp_path / "cache"

    program = claude_code.ensure(cache, pin, said.append)
    assert program == cache / "9.9.9" / "claude"
    assert program is not None
    assert program.read_bytes() == PROGRAM
    assert said == ["Getting Claude Code 9.9.9 ready. Only the first task does this."]

    assert claude_code.ensure(cache, pin, said.append) == program  # the next task
    assert len(registry.calls) == 1
    assert len(said) == 1
    assert sorted(p.name for p in cache.iterdir()) == ["9.9.9"]  # nothing half-done left


@pytest.mark.usefixtures("no_bundled_cli")
def test_a_tampered_download_is_refused_and_not_kept(
    tmp_path: Path, pin: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(claude_code.urllib.request, "urlopen", Registry(_package(b"evil")))
    cache = tmp_path / "cache"
    with pytest.raises(claude_code.InstallError, match="didn't match"):
        claude_code.ensure(cache, pin)
    assert list(cache.iterdir()) == []


@pytest.mark.usefixtures("no_bundled_cli")
def test_no_connection_says_what_to_do(
    tmp_path: Path, pin: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def offline(url: str, timeout: float) -> Any:
        raise OSError("Name or service not known")

    monkeypatch.setattr(claude_code.urllib.request, "urlopen", offline)
    with pytest.raises(claude_code.InstallError, match="Check the internet connection"):
        claude_code.ensure(tmp_path / "cache", pin)


def test_the_sdks_own_cli_is_used_outside_the_image(tmp_path: Path, pin: Path) -> None:
    assert claude_code.bundled()  # the desktop's Python and this checkout have it
    assert claude_code.ensure(tmp_path / "cache", pin) is None


def test_the_pin_is_the_cli_the_desktop_ships() -> None:
    """Bumped with each release (scripts/pin_claude_code.py), with the SDK."""
    pinned = claude_code.pinned()
    assert pinned["version"] == __cli_version__
    for arch in ("x64", "arm64"):
        package = pinned["packages"][arch]
        assert package["tarball"] == (
            f"https://registry.npmjs.org/@anthropic-ai/claude-code-linux-{arch}/-/"
            f"claude-code-linux-{arch}-{__cli_version__}.tgz"
        )
        assert package["integrity"].startswith("sha512-")


def test_the_image_uses_the_same_sdk_as_the_app() -> None:
    dockerfile = (ROOT / "worker" / "Dockerfile").read_text(encoding="utf-8")
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    [image] = re.findall(r"CLAUDE_AGENT_SDK_VERSION=(\S+)", dockerfile)
    [app] = re.findall(r'"claude-agent-sdk==([^"]+)"', pyproject)
    assert image == app
    assert "_bundled" in dockerfile  # and removes its CLI
