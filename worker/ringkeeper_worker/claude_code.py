"""The Claude Code CLI, fetched on first start rather than shipped in the image.

The image holds no copy of the CLI. The first task fetches the pinned version's Linux
package from the npm registry, checks it against the sha512 pinned beside this file
(claude-code.json, bumped with each release), and keeps it in a cache volume that
later tasks reuse. The runner (root) does this before the agent starts, and the cache
is root's: the agent runs as the worker user and can't change what the next task runs.

Outside the container (the desktop's local tasks, tests) the SDK's own CLI is used.
"""

import base64
import contextlib
import hashlib
import json
import os
import platform
import shutil
import tarfile
import tempfile
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

CACHE = Path("/opt/claude-code")  # the cache volume (see the session manager)
PIN = Path(__file__).with_name("claude-code.json")
MEMBER = "package/claude"
TIMEOUT = 60
CHUNK = 1 << 20


class InstallError(RuntimeError):
    pass


def pinned(pin: Path = PIN) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(pin.read_text(encoding="utf-8")))


def architecture() -> str:
    return "arm64" if platform.machine().lower() in ("arm64", "aarch64") else "x64"


def bundled() -> bool:
    """Whether the installed SDK carries its own CLI (everywhere but the image)."""
    import claude_agent_sdk  # noqa: PLC0415

    folder = Path(claude_agent_sdk.__file__).parent / "_bundled"
    return any(folder.glob("claude*"))


def _integrity(digest: bytes) -> str:
    return "sha512-" + base64.b64encode(digest).decode("ascii")


def _download(url: str, into: Path, integrity: str) -> None:
    sha = hashlib.sha512()
    with urllib.request.urlopen(url, timeout=TIMEOUT) as response, into.open("wb") as out:  # noqa: S310 (a pinned https URL)
        while chunk := response.read(CHUNK):
            sha.update(chunk)
            out.write(chunk)
    if _integrity(sha.digest()) != integrity:
        raise InstallError(
            "The downloaded Claude Code didn't match the copy this version of Ringkeeper "
            "expects, so it wasn't used. Run the task again; if it happens again, report it."
        )


def _extract(archive: Path, folder: Path) -> Path:
    with tarfile.open(archive, "r:gz") as tar:
        member = tar.getmember(MEMBER)
        if not member.isfile():
            raise InstallError("the Claude Code package didn't contain the program")
        source = tar.extractfile(member)
        if source is None:
            raise InstallError("the Claude Code package didn't contain the program")
        target = folder / "claude"
        with source, target.open("wb") as out:
            shutil.copyfileobj(source, out, CHUNK)
    target.chmod(0o755)
    return target


def ensure(
    cache: Path = CACHE, pin: Path = PIN, say: Callable[[str], None] | None = None
) -> Path | None:
    """The CLI to run: None when the SDK has its own, else the cached pinned version,
    fetched first if this is the first task to need it."""
    if bundled():
        return None
    spec = pinned(pin)
    version = str(spec["version"])
    package = spec["packages"][architecture()]
    final = cache / version
    program = final / "claude"
    if program.is_file():
        return program
    if say is not None:
        say(f"Getting Claude Code {version} ready. Only the first task does this.")
    cache.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=".partial-", dir=cache))
    try:
        archive = work / "package.tgz"
        try:
            _download(str(package["tarball"]), archive, str(package["integrity"]))
        except OSError as error:
            raise InstallError(
                f"Couldn't download Claude Code {version}, which tasks in Docker need ({error}). "
                "Check the internet connection, then run the task again."
            ) from None
        _extract(archive, work)
        archive.unlink()
        work.chmod(0o755)
        try:
            os.rename(work, final)  # atomic; another task may have won the race
        except OSError:
            if not program.is_file():
                raise
    finally:
        with contextlib.suppress(OSError):
            shutil.rmtree(work)
    return program
