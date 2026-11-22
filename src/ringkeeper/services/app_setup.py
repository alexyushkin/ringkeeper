"""A pass added to an AI app on this computer in one click: Claude Code and Cursor by their own
settings files, the way Ringkeeper Connect sets them up.

The rules for another app's settings file:

- Only its list of servers (`mcpServers`) changes. Every other setting is left as it was, in
  the same order, and that's checked before anything is saved.
- A file that's missing, can't be read, isn't valid JSON (comments included) or isn't in the
  shape expected is left alone, and the error says why.
- An encrypted copy of the file as it was is kept first (the newest few per app, in the data
  folder): these files often hold keys of their own.
- It's written to a temporary file beside it and renamed over it. If the app changes the file
  while this works, it starts again on the new version.

Claude Desktop isn't here: it gets the pass as an extension (services.passes.extension), whose
relay starts the bridge again after an update, and it rewrites its settings file while it runs.
"""

import base64
import contextlib
import json
import os
import re
import secrets
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from ringkeeper.config import Settings
from ringkeeper.errors import RingkeeperError
from ringkeeper.keys import derive_key, load_master_key
from ringkeeper.services import workspaces

KEY = "mcpServers"
BACKUPS = "app-settings-backups"
KEEP = 5  # copies per app
TRIES = 3  # the app rewrote the file while this worked: start again on its version
BUSY_TRIES = 8  # Windows holds a file a moment after it's written (a virus scanner, say)
NONCE_BYTES = 12

Servers = dict[str, Any]


class AppSetupError(RingkeeperError):
    """Why the app's settings weren't changed, in plain words."""


@dataclass(frozen=True)
class App:
    key: str
    label: str
    # Its server entry: Claude Code's says how it's started ("stdio"); Cursor's doesn't.
    typed: bool

    def settings_file(self) -> Path:
        if self.key == "claude-code":
            return workspaces.claude_code_config()
        return Path.home() / ".cursor" / "mcp.json"

    def found(self) -> bool:
        """Whether the app is set up on this computer: its settings folder is there."""
        if self.key == "claude-code":
            return self.settings_file().is_file() or (Path.home() / ".claude").is_dir()
        return self.settings_file().parent.is_dir()

    def creates_file(self) -> bool:
        """Cursor starts without the file; Claude Code writes its own the first time it runs."""
        return self.key == "cursor"


APPS: dict[str, App] = {
    "claude-code": App("claude-code", "Claude Code", typed=True),
    "cursor": App("cursor", "Cursor", typed=False),
}


def app(key: str) -> App:
    found = APPS.get(key)
    if found is None:
        raise AppSetupError(f"'{key}' isn't an app Ringkeeper sets up.")
    return found


def entry(started: dict[str, Any], the_app: App) -> dict[str, Any]:
    """The pass's server entry for this app, from how the bridge is started (passes.launch)."""
    made: dict[str, Any] = {"type": "stdio"} if the_app.typed else {}
    made |= {"command": started["command"], "args": list(started["args"])}
    # Claude Code writes env even when it's empty (`claude mcp add`), so a pass added that way
    # reads as the same entry.
    if started.get("env") or the_app.typed:
        made["env"] = dict(started.get("env") or {})
    return made


# --- reading ---


def _decode(raw: bytes, label: str) -> dict[str, Any]:
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise AppSetupError(
            f"Ringkeeper left {label}'s settings file alone: it isn't text."
        ) from None
    if not text.strip():
        return {}
    try:
        root = json.loads(text)
    except json.JSONDecodeError as error:
        raise AppSetupError(
            f"Ringkeeper left {label}'s settings file alone: it isn't valid JSON "
            f"(line {error.lineno}). Fix it in {label}, then try again."
        ) from None
    if not isinstance(root, dict):
        raise AppSetupError(
            f"Ringkeeper left {label}'s settings file alone: it isn't in the shape expected."
        )
    servers = cast(dict[str, Any], root).get(KEY)
    if servers is not None and not isinstance(servers, dict):
        raise AppSetupError(
            f"Ringkeeper left {label}'s settings file alone: its list of servers isn't in the "
            "shape expected."
        )
    return cast(dict[str, Any], root)


def _read(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None


def servers(key: str) -> Servers:
    """The app's servers as its file lists them now (empty when there's no file yet)."""
    the_app = app(key)
    raw = _read(the_app.settings_file())
    if raw is None:
        return {}
    return dict(cast(Servers, _decode(raw, the_app.label).get(KEY) or {}))


# --- writing ---


def _layout(raw: bytes) -> tuple[str | int | None, str, bool]:
    """How the file is laid out, to write it back the same way: (indent, line ending, ends with
    a newline). A file on one line stays on one line."""
    text = raw.decode("utf-8-sig") if raw else ""
    eol = "\r\n" if "\r\n" in text else "\n"
    found = re.search(r"\n([ \t]+)\S", text)
    indent: str | int | None
    if found:
        indent = "\t" if found.group(1).startswith("\t") else len(found.group(1))
    else:
        indent = None if text.strip() else 2
    return indent, eol, text.endswith("\n") or not text.strip()


def render(root: dict[str, Any], raw: bytes) -> bytes:
    indent, eol, newline = _layout(raw)
    text = json.dumps(root, indent=indent, ensure_ascii=False)
    if eol != "\n":
        text = text.replace("\n", eol)
    if newline:
        text += eol
    bom = "﻿" if raw.startswith(b"\xef\xbb\xbf") else ""
    return (bom + text).encode("utf-8")


def _same_apart_from_servers(before: dict[str, Any], after_raw: bytes, expected: Servers) -> None:
    """Saved only if the new file reads back with exactly the servers meant and every other
    setting as it was."""
    after = json.loads(after_raw.decode("utf-8-sig"))
    rest_before = {k: v for k, v in before.items() if k != KEY}
    rest_after = {k: v for k, v in after.items() if k != KEY}
    if rest_after != rest_before or after.get(KEY, {}) != expected:
        raise AppSetupError(
            "The edited settings didn't come out as expected, so nothing was saved."
        )


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.ringkeeper-{os.getpid()}-{secrets.token_hex(4)}.tmp")
    temporary.write_bytes(data)
    with contextlib.suppress(OSError):  # keep who may read it, where the file had a mode
        temporary.chmod(path.stat().st_mode & 0o777 if path.exists() else 0o600)
    for attempt in range(BUSY_TRIES):
        try:
            temporary.replace(path)
            return
        except PermissionError:
            if attempt == BUSY_TRIES - 1 or sys.platform != "win32":
                temporary.unlink(missing_ok=True)
                raise
            time.sleep(0.05 * (attempt + 1))


def _cipher(settings: Settings) -> AESGCM:
    return AESGCM(derive_key(load_master_key(settings), "app-settings-backup"))


def backup_folder(settings: Settings) -> Path:
    return settings.data_dir / BACKUPS


def keep_copy(settings: Settings, key: str, raw: bytes) -> Path:
    """The file as it was, encrypted, before Ringkeeper changes it; the newest KEEP are kept."""
    folder = backup_folder(settings)
    folder.mkdir(parents=True, exist_ok=True)
    nonce = secrets.token_bytes(NONCE_BYTES)
    sealed = nonce + _cipher(settings).encrypt(nonce, raw, key.encode())
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    # Two copies in one clock tick (Windows' clock can repeat) get the next number, so none
    # overwrites another and they still sort oldest first.
    n = 0
    while (path := folder / f"{key}-{stamp}-{n:03d}.bak").exists():
        n += 1
    path.write_bytes(base64.b64encode(sealed))
    for old in sorted(folder.glob(f"{key}-*.bak"))[:-KEEP]:
        old.unlink(missing_ok=True)
    return path


def read_copy(settings: Settings, key: str, path: Path) -> bytes:
    """A kept copy, decrypted (for tests, and for putting a file back by hand)."""
    sealed = base64.b64decode(path.read_bytes())
    return _cipher(settings).decrypt(sealed[:NONCE_BYTES], sealed[NONCE_BYTES:], key.encode())


def _edit(settings: Settings, key: str, change: Callable[[Servers], bool]) -> bool:
    """Run `change` on the app's servers and save them, if it changed anything."""
    the_app = app(key)
    path = the_app.settings_file()
    if not the_app.found():
        raise AppSetupError(f"{the_app.label} isn't set up on this computer.")
    for _ in range(TRIES):
        raw = _read(path)
        if raw is None and not the_app.creates_file():
            raise AppSetupError(
                f"{the_app.label} hasn't saved its settings yet. Open {the_app.label} once, then "
                "try again."
            )
        root = _decode(raw or b"", the_app.label)
        listed = dict(cast(Servers, root.get(KEY) or {}))
        if not change(listed):
            return False
        updated = {**root, KEY: listed}  # in its place if it was there, else last
        data = render(updated, raw or b"")
        _same_apart_from_servers(root, data, listed)
        if _read(path) != raw:
            continue  # the app changed it meanwhile
        if raw is not None:
            keep_copy(settings, key, raw)
        try:
            _write(path, data)
        except OSError as error:
            raise AppSetupError(
                f"Couldn't save {the_app.label}'s settings ({error.strerror or error}). Close "
                f"{the_app.label} and try again."
            ) from None
        return True
    raise AppSetupError(
        f"{the_app.label} kept changing its settings while Ringkeeper was saving. Close it and "
        "try again."
    )


def add(settings: Settings, key: str, server: str, started: dict[str, Any]) -> bool:
    """The pass in the app's servers, as `started` says to run it. False: it was already there
    exactly so."""
    wanted = entry(started, app(key))

    def put(listed: Servers) -> bool:
        if listed.get(server) == wanted:
            return False
        listed[server] = wanted
        return True

    return _edit(settings, key, put)


def remove(settings: Settings, key: str, server: str) -> bool:
    """The pass taken out of the app's servers. False: it wasn't there."""

    def take(listed: Servers) -> bool:
        return listed.pop(server, None) is not None

    return _edit(settings, key, take)


def status(key: str, server: str, started: dict[str, Any]) -> dict[str, Any]:
    """For the pass's card: whether the app is here, whether the pass is in it (and as this
    install runs it now), or why that can't be told."""
    the_app = app(key)
    shown: dict[str, Any] = {"app": key, "label": the_app.label, "found": the_app.found()}
    if not shown["found"]:
        return shown | {"added": False, "current": False, "problem": None}
    try:
        listed = servers(key)
    except AppSetupError as error:
        return shown | {"added": False, "current": False, "problem": str(error)}
    there = listed.get(server)
    return shown | {
        "added": there is not None,
        "current": there == entry(started, the_app),
        "problem": None,
    }
