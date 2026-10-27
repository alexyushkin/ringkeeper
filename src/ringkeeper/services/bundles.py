"""Desktop extensions from a file: an MCP bundle (.mcpb, formerly .dxt), a zip
with a manifest.json that names the server, its publisher, its tools, the
command that starts it, and the settings it asks for.

1. `keep` and `read` (on upload): the file is kept in the data folder under
   its SHA-256, and its manifest is shown on one confirmation screen: name and
   version, the publisher as the bundle states it (nobody has checked it), its
   tools, what it runs and what this computer needs for that, and its settings.
2. `install` (in the background, a `local_servers` job): that same file,
   named by its SHA-256, so what's installed is what was shown, is unpacked
   into data/mcp-packages/mcpb/<name>@<version>+<sha>. The connection starts it
   from there with the settings filled in, all of it kept in the vault.

Its calls follow the same rules as an extension imported from Claude Desktop
(services.claude_desktop): a Test connection with the generic tool rules.
"""

import json
import os
import re
import shutil
import stat
import sys
import time
import zipfile
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, cast

from ringkeeper.adapters import mcp
from ringkeeper.adapters.base import JSON
from ringkeeper.config import Settings
from ringkeeper.services import local_servers
from ringkeeper.services.claude_desktop import PLACEHOLDER, fill, missing_command

REGISTRY = "mcpb"
MAX_BYTES = 300_000_000  # the file
MAX_UNPACKED = 1_000_000_000
MAX_FILES = 50_000
KEEP_UPLOADS_SECONDS = 24 * 3600
FOLDER = "<extension folder>"  # stands for the install folder on the confirmation screen
MASK = "••••"
FINGERPRINT = re.compile(r"^[0-9a-f]{64}$")
PLATFORMS = {"win32": "Windows", "darwin": "macOS", "linux": "Linux"}
TYPES = ("node", "python", "binary", "uv")


class BundleError(local_servers.InstallError):
    """The file isn't an extension Ringkeeper can install. The message is for the user."""


@dataclass(frozen=True)
class Setting:
    key: str
    title: str
    description: str
    type: str  # string, number, boolean, directory, file
    required: bool
    secret: bool
    default: str
    multiple: bool  # several values, one per line


@dataclass(frozen=True)
class Bundle:
    fingerprint: str  # the file's SHA-256
    name: str
    title: str
    version: str
    description: str
    publisher: str
    website: str
    kind: str  # node, python, binary or uv
    tools: tuple[tuple[str, str], ...]  # name, description
    tools_generated: bool  # it lists more tools once it starts
    settings: tuple[Setting, ...]
    template: JSON  # command, args, env, with ${...} placeholders
    problem: str | None  # why it can't run here, if it can't
    size: int
    files: int

    @property
    def pinned(self) -> str:
        """The installed copy's version: the bundle's, plus which file it came from."""
        return f"{self.version}+{self.fingerprint[:12]}"

    def runs(self) -> str:
        """The command, for people to read: the folder and the settings named, not filled."""
        named: JSON = {s.key: f"<{s.title}>" for s in self.settings}
        return mcp.preview(_spec(self, FOLDER, named))

    def as_dict(self) -> JSON:
        return {
            "fingerprint": self.fingerprint, "name": self.name, "title": self.title,
            "version": self.version, "description": self.description,
            "publisher": self.publisher, "website": self.website, "kind": self.kind,
            "tools": [{"name": n, "description": d} for n, d in self.tools],
            "tools_generated": self.tools_generated,
            "settings": [s.__dict__ for s in self.settings],
            "runs": self.runs(), "problem": self.problem, "size": self.size, "files": self.files,
        }  # fmt: skip


# --- The file ---


def uploads(settings: Settings) -> Path:
    return settings.data_dir / "mcp-packages" / REGISTRY / "uploads"


def staged(settings: Settings, fingerprint: str) -> Path:
    if not FINGERPRINT.match(fingerprint):
        raise BundleError("That isn't an extension file Ringkeeper has seen.")
    return uploads(settings) / f"{fingerprint}.mcpb"


def keep(settings: Settings, received: Path, fingerprint: str) -> Path:
    """An uploaded file, kept under its SHA-256 until it's installed (or a day passes)."""
    target = staged(settings, fingerprint)
    os.replace(received, target)
    cutoff = time.time() - KEEP_UPLOADS_SECONDS
    for old in uploads(settings).glob("*.mcpb"):
        if old != target and old.stat().st_mtime < cutoff:
            old.unlink(missing_ok=True)
    return target


def _object(value: Any) -> JSON:
    return cast(JSON, value) if isinstance(value, dict) else {}


def _string(value: Any, limit: int = 2000) -> str:
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, list):
        return "\n".join(str(v) for v in cast(list[Any], value))[:limit]
    return "" if value is None else str(value)[:limit]


def read(path: Path, fingerprint: str) -> Bundle:
    """The bundle's manifest, checked, for the confirmation screen."""
    try:
        with zipfile.ZipFile(path) as bundle:
            infos = bundle.infolist()
            try:
                raw = bundle.read("manifest.json")
            except KeyError:
                raise BundleError("This file has no manifest.json: not an extension.") from None
    except (zipfile.BadZipFile, OSError):
        raise BundleError("This isn't an extension file (.mcpb): it doesn't open.") from None
    try:
        manifest: Any = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError):
        raise BundleError("This extension's manifest.json isn't valid JSON.") from None
    if not isinstance(manifest, dict):
        raise BundleError("This extension's manifest.json isn't an object.")
    manifest = cast(JSON, manifest)
    name, version = _string(manifest.get("name"), 100), _string(manifest.get("version"), 64)
    if not name or not re.fullmatch(r"[A-Za-z0-9@][A-Za-z0-9._@/+-]*", name):
        raise BundleError("This extension's manifest doesn't give it a usable name.")
    if not local_servers.VERSION.match(version):
        raise BundleError("This extension's manifest doesn't give an exact version.")
    server = _object(manifest.get("server"))
    kind = _string(server.get("type"), 20)
    template = _template(server, kind)
    names = {i.filename.replace("\\", "/").rstrip("/") for i in infos}
    author = _object(manifest.get("author"))
    tools = tuple(
        (_string(t.get("name"), 200), _string(t.get("description"), 500))
        for t in (_object(t) for t in cast(list[Any], manifest.get("tools") or [])[:500])
        if t.get("name")
    )
    settings = tuple(
        _setting(str(k), _object(d)) for k, d in _object(manifest.get("user_config")).items()
    )
    bundle = Bundle(
        fingerprint=fingerprint, name=name,
        title=_string(manifest.get("display_name"), 200) or name, version=version,
        description=_string(manifest.get("description"), 1000),
        publisher=_string(author.get("name"), 200) or "not given",
        website=_string(author.get("url") or manifest.get("homepage"), 500), kind=kind,
        tools=tools, tools_generated=bool(manifest.get("tools_generated")), settings=settings,
        template=template, problem=None, size=path.stat().st_size, files=len(infos),
    )  # fmt: skip
    return replace(bundle, problem=_problem(bundle, manifest, names))


def _template(server: JSON, kind: str) -> JSON:
    """How it starts on this computer: mcp_config, with this platform's overrides."""
    config = _object(server.get("mcp_config"))
    override = _object(_object(config.get("platform_overrides")).get(sys.platform))
    env = {**_object(config.get("env")), **_object(override.get("env"))}
    template: JSON = {
        "command": override.get("command") or config.get("command"),
        "args": override.get("args") if "args" in override else config.get("args") or [],
        "env": env,
    }
    entry = _string(server.get("entry_point"), 500)
    if not template["command"] and entry:  # an older manifest: start the entry point
        started = {"node": ["node"], "python": ["python"], "binary": []}.get(kind)
        if started is not None:
            whole = [*started, "${__dirname}/" + entry]
            template |= {"command": whole[0], "args": whole[1:]}
    return template


def _setting(key: str, d: JSON) -> Setting:
    return Setting(
        key=key, title=_string(d.get("title"), 200) or key,
        description=_string(d.get("description"), 1000),
        type=_string(d.get("type"), 20) or "string",
        required=bool(d.get("required")), secret=bool(d.get("sensitive")),
        default=_string(d.get("default")), multiple=bool(d.get("multiple")),
    )  # fmt: skip


def _outside(name: str) -> bool:
    """A path in the file that would land outside its folder when unpacked."""
    return name.startswith("/") or bool(re.match(r"[A-Za-z]:", name)) or ".." in name.split("/")


def _problem(bundle: Bundle, manifest: JSON, names: set[str]) -> str | None:
    if any(_outside(n) for n in names):
        return "it has a file that would land outside its folder"
    platforms = cast(list[Any], _object(manifest.get("compatibility")).get("platforms") or [])
    if platforms and sys.platform not in platforms:
        made_for = ", ".join(PLATFORMS.get(str(p), str(p)) for p in platforms)
        return f"it's made for {made_for} only"
    if bundle.kind not in TYPES:
        return f"it's a {bundle.kind or 'server'} of a kind Ringkeeper can't start"
    if not bundle.template.get("command"):
        return "its manifest doesn't say how to start it"
    command = fill(str(bundle.template["command"]), FOLDER, {})[0]
    if command.startswith(FOLDER):  # its own program, in the bundle
        inside = command[len(FOLDER) :].replace("\\", "/").lstrip("/")
        return None if inside in names else "the program it starts isn't in the file"
    return missing_command(command)


# --- Settings and the command ---


def _value(setting: Setting, raw: str) -> Any:
    if setting.multiple:
        return [fill(line.strip(), "", {})[0] for line in raw.splitlines() if line.strip()]
    if setting.type == "boolean":
        return raw.strip().lower() in ("true", "1", "yes", "on")
    return fill(raw.strip(), "", {})[0]  # a default may say ${HOME}


def values_for(bundle: Bundle, given: dict[str, str], *, masked: bool = False) -> JSON:
    """Each setting's value: what was typed, else its default. Raises if a required
    one is missing. `masked`: secrets as dots, for the preview that's stored."""
    values: JSON = {}
    for setting in bundle.settings:
        raw = (given.get(setting.key) or "").strip() or setting.default
        if not raw:
            if setting.required:
                raise BundleError(f"Fill in {setting.title}.")
            continue
        values[setting.key] = MASK if masked and setting.secret else _value(setting, raw)
    return values


def _spec(bundle: Bundle, folder: str, values: JSON) -> JSON:
    template = bundle.template
    args = [
        a
        for arg in cast(list[Any], template.get("args") or [])
        for a in fill(str(arg), folder, values)
        if a != "" or not PLACEHOLDER.fullmatch(str(arg))  # an optional setting left empty
    ]
    env = {
        str(k): v
        for k, raw in cast(JSON, template.get("env") or {}).items()
        if (v := fill(str(raw), folder, values)[0])
    }
    return {"command": fill(str(template["command"]), folder, values)[0], "args": args, "env": env}


# --- Installing ---


def folder_for(settings: Settings, bundle: Bundle) -> Path:
    return local_servers.folder_for(settings, REGISTRY, bundle.name, bundle.pinned)


def unpack(path: Path, folder: Path) -> None:
    """Every file of the bundle into `folder`, refusing anything that would land
    outside it, links, and more than MAX_UNPACKED or MAX_FILES."""
    partial = folder.with_name(folder.name + ".partial")
    shutil.rmtree(partial, ignore_errors=True)
    partial.mkdir(parents=True)
    root = partial.resolve()
    total = 0
    with zipfile.ZipFile(path) as bundle:
        infos = bundle.infolist()
        if len(infos) > MAX_FILES:
            raise BundleError(f"This extension has more than {MAX_FILES} files.")
        for info in infos:
            mode = info.external_attr >> 16
            if stat.S_ISLNK(mode):
                raise BundleError("This extension contains a link, which Ringkeeper won't unpack.")
            target = (partial / info.filename).resolve()
            if not target.is_relative_to(root) or (target == root and not info.is_dir()):
                raise BundleError("This extension has a file that would land outside its folder.")
            total += info.file_size  # reading stops at the stated size
            if total > MAX_UNPACKED:
                raise BundleError("This extension is too big once unpacked.")
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with bundle.open(info) as source, target.open("wb") as out:
                shutil.copyfileobj(source, out)
            if sys.platform != "win32" and mode & stat.S_IXUSR:
                target.chmod(0o755)  # its own program
    shutil.rmtree(folder, ignore_errors=True)
    partial.rename(folder)


def install(settings: Settings, bundle: Bundle, path: Path, given: dict[str, str]) -> JSON:
    """Unpack the bundle and fill in its command; returns the server's settings (for
    the vault) and the preview to show, its secrets masked."""
    values = values_for(bundle, given)
    folder = folder_for(settings, bundle)
    unpack(path, folder)
    spec = _spec(bundle, str(folder), values)
    shown = mcp.preview(_spec(bundle, str(folder), values_for(bundle, given, masked=True)))
    path.unlink(missing_ok=True)
    return {**spec, "pinned": f"{bundle.name}@{bundle.pinned}", "preview": shown}
