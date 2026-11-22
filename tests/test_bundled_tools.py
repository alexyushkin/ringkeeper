"""Node.js and uv from the desktop app's install folder go first on PATH (bundled_tools), so a
tool that runs with npx or uvx is found with nothing installed."""

import os
import shutil
import sys
from pathlib import Path

import pytest

from ringkeeper import bundled_tools
from ringkeeper.services import claude_desktop

WINDOWS = sys.platform == "win32"


def install(tmp_path: Path) -> tuple[str, Path, Path]:
    """An install folder as bundle.py lays it out: python/, and tools/ beside it."""
    prefix = tmp_path / "Ringkeeper" / "python"
    prefix.mkdir(parents=True)
    node = tmp_path / "Ringkeeper" / "tools" / "node"
    node_bin = node if WINDOWS else node / "bin"
    uv = tmp_path / "Ringkeeper" / "tools" / "uv"
    for folder, program in ((node_bin, "npx"), (uv, "uvx")):
        folder.mkdir(parents=True)
        made = folder / (f"{program}.cmd" if WINDOWS else program)
        made.write_text("@echo off\n" if WINDOWS else "#!/bin/sh\n", encoding="utf-8")
        made.chmod(0o755)
    return str(prefix), node_bin, uv


def test_an_install_without_tools_adds_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PATH", "before")
    assert bundled_tools.add_to_path(str(tmp_path / "python")) == []
    assert os.environ["PATH"] == "before"


def test_the_bundled_tools_come_first_on_path_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prefix, node_bin, uv = install(tmp_path)
    monkeypatch.setenv("PATH", os.pathsep.join(["first", "second"]))
    assert bundled_tools.folders(prefix) == [node_bin, uv]
    assert bundled_tools.add_to_path(prefix) == [node_bin, uv]
    assert os.environ["PATH"].split(os.pathsep) == [str(node_bin), str(uv), "first", "second"]
    assert bundled_tools.add_to_path(prefix) == []  # a second start adds nothing twice
    assert os.environ["PATH"].count(str(uv)) == 1


def test_a_tool_that_needs_npx_or_uvx_is_found_in_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prefix, _node_bin, _uv = install(tmp_path)
    empty = tmp_path / "nothing-installed"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    assert (
        claude_desktop.missing_command("npx") == "needs Node.js (npx), which isn't on this computer"
    )
    bundled_tools.add_to_path(prefix)
    assert claude_desktop.missing_command("npx") is None
    assert claude_desktop.missing_command("uvx") is None
    assert shutil.which("uvx") is not None
