"""Importing MCP servers from Claude Desktop: claude_desktop_config.json and
installed Desktop extensions (the Claude Extensions folder, one manifest.json each).

Desktop mode only: the files are on this computer. Each server the user picks
becomes an `mcp` connection linked to one project. Everything needed to start
it (command, args, env; or URL and headers) goes into the vault, read here
straight from the files, so secrets never pass through the browser and never
sit in plain text in Ringkeeper's database. What stays in the connection's
config is a preview with secret-looking args masked.

A server that runs a command (npx, uvx, node, python, docker...) can only be
imported when that command is on this computer, because the gateway starts it.

An extension's command comes from its manifest, with ${__dirname}, the home
folders and ${user_config.*} filled in from Claude Desktop's saved settings
for it. Claude Desktop keeps sensitive values (API keys) elsewhere, so those
are asked for when importing; they go straight into the vault with the rest.
"""

import json
import os
import re
import shutil
import sys
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.orm import Session

from ringkeeper.adapters import mcp
from ringkeeper.adapters.base import JSON, ToolError
from ringkeeper.config import Settings
from ringkeeper.errors import RuleViolationError
from ringkeeper.models import Env, Instance, InstanceType
from ringkeeper.schemas import BindingIn, InstanceIn
from ringkeeper.services import admin_audit, bindings, instances, projects, secrets_in

SOURCE = "claude-desktop"
CONFIG_NAME = "claude_desktop_config.json"
EXTENSIONS = "Claude Extensions"
EXTENSION_SETTINGS = "Claude Extensions Settings"
EXTENSION_PREFIX = "extension:"
PLACEHOLDER = re.compile(r"\$\{([^}]+)\}")
NEEDS = {
    "npx": "Node.js", "node": "Node.js", "npm": "Node.js", "uvx": "uv", "uv": "uv",
    "docker": "Docker", "python": "Python", "python3": "Python", "deno": "Deno", "bunx": "Bun",
}  # fmt: skip


def config_paths() -> list[Path]:
    """Where Claude Desktop keeps its config, most likely first."""
    if sys.platform == "win32":
        roaming = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
        local = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
        store = sorted((local / "Packages").glob("Claude_*"))  # the Microsoft Store install
        return [roaming / "Claude" / CONFIG_NAME] + [
            p / "LocalCache" / "Roaming" / "Claude" / CONFIG_NAME for p in store
        ]
    if sys.platform == "darwin":
        return [Path.home() / "Library" / "Application Support" / "Claude" / CONFIG_NAME]
    return [Path.home() / ".config" / "Claude" / CONFIG_NAME]


def find_config() -> Path | None:
    return next((p for p in config_paths() if p.is_file()), None)


def find_extensions() -> Path | None:
    """Claude Desktop's folder of installed extensions, if there is one."""
    folders = (p.parent / EXTENSIONS for p in config_paths())
    return next((f for f in folders if f.is_dir()), None)


@dataclass(frozen=True)
class Needed:
    """A setting an extension needs that Claude Desktop's files don't show."""

    key: str
    title: str
    sensitive: bool


@dataclass(frozen=True)
class Server:
    name: str
    spec: JSON  # with its secrets: only ever stored in the vault
    transport: str  # "command" or "url"
    preview: str
    problem: str | None  # why it can't be imported here, if it can't
    key: str = ""  # unique: the config's server name, or "extension:<folder>"
    kind: str = "config"  # or "extension"
    needed: tuple[Needed, ...] = ()
    # An extension's manifest command and saved settings, to fill in (`complete`).
    source: JSON = field(default_factory=dict[str, Any])

    def __post_init__(self) -> None:
        if not self.key:
            object.__setattr__(self, "key", self.name)

    def summary(self) -> JSON:
        secrets = len(self.spec.get("env", {})) + len(self.spec.get("headers", {}))
        secrets += sum(1 for a in self.spec.get("args", []) if mcp.looks_secret(a))
        return {
            "name": self.name,
            "key": self.key,
            "kind": self.kind,
            "transport": self.transport,
            "preview": self.preview,
            "secrets": secrets,
            "needs": [{"key": n.key, "title": n.title, "secret": n.sensitive} for n in self.needed],
            "problem": self.problem,
        }


def everything() -> list[Server]:
    """The config's servers, then the installed extensions."""
    config, extensions = find_config(), find_extensions()
    found = read_servers(config) if config is not None else []
    return found + (read_extensions(extensions) if extensions is not None else [])


def read_servers(path: Path) -> list[Server]:
    try:
        data: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise RuleViolationError(f"Couldn't read Claude Desktop's settings: {error}") from None
    listed = cast(JSON, data).get("mcpServers") if isinstance(data, dict) else None
    if not isinstance(listed, dict):
        return []
    return [_server(str(name), entry) for name, entry in cast(JSON, listed).items()]


def _missing_command(command: str) -> str | None:
    if shutil.which(command) is not None:
        return None
    needs = NEEDS.get(Path(command).stem.lower())
    if needs:
        return f"needs {needs} ({command}), which isn't on this computer"
    return f"runs {command}, which isn't on this computer"


def _server(name: str, entry: Any) -> Server:
    fields = cast(JSON, entry) if isinstance(entry, dict) else {}
    kind = str(fields.get("type") or "").lower()
    if kind == "sse":
        return Server(name, {}, "url", str(fields.get("url", "")), "uses SSE, not supported yet")
    try:
        spec = mcp.server_spec(json.dumps(fields))
    except ToolError as error:
        return Server(name, {}, "command", "", str(error))
    if "url" in spec:
        return Server(name, spec, "url", mcp.preview(spec), None)
    command = str(spec["command"])
    return Server(name, spec, "command", mcp.preview(spec), _missing_command(command))


# --- Desktop extensions ---


def _read_json(path: Path) -> JSON:
    try:
        data: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return cast(JSON, data) if isinstance(data, dict) else {}


def read_extensions(folder: Path) -> list[Server]:
    found: list[Server] = []
    for directory in sorted(p for p in folder.iterdir() if (p / "manifest.json").is_file()):
        if ".backup-" in directory.name:  # a copy Claude Desktop kept from an update
            continue
        manifest = _read_json(directory / "manifest.json")
        saved = _read_json(folder.parent / EXTENSION_SETTINGS / f"{directory.name}.json")
        found.append(_extension(directory, manifest, saved))
    return found


def _extension(directory: Path, manifest: JSON, saved: JSON) -> Server:
    name = str(manifest.get("display_name") or manifest.get("name") or directory.name)
    key = EXTENSION_PREFIX + directory.name
    template = cast(JSON, cast(JSON, manifest.get("server") or {}).get("mcp_config") or {})
    if not template.get("command"):
        return Server(name, {}, "command", "", "has no command to start", key, "extension")
    defined = {
        str(k): cast(JSON, d)
        for k, d in cast(JSON, manifest.get("user_config") or {}).items()
        if isinstance(d, dict)
    }
    values = cast(JSON, saved.get("userConfig") or {})
    used = {
        m.group(1).removeprefix("user_config.") for m in PLACEHOLDER.finditer(json.dumps(template))
    }
    needed = tuple(
        Needed(k, str(d.get("title") or k), bool(d.get("sensitive")))
        for k, d in defined.items()
        if k in used and values.get(k) is None and d.get("default") is None and d.get("required")
    )
    source: JSON = {"template": template, "dir": str(directory), "defined": defined,
                    "values": values}  # fmt: skip
    server = complete(Server(name, {}, "command", "", None, key, "extension", needed, source))
    if saved.get("isEnabled") is False:
        return replace(server, problem="turned off in Claude Desktop")
    return server


def _text(value: Any) -> str:
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, list):
        return ",".join(str(v) for v in cast(list[Any], value))
    return "" if value is None else str(value)


def _fill(text: str, directory: str, values: JSON) -> list[str]:
    """One manifest string with its placeholders filled. A string that is just one
    list setting (several folders, say) becomes one argument per item."""
    whole = PLACEHOLDER.fullmatch(text)
    if whole and whole.group(1).startswith("user_config."):
        value = values.get(whole.group(1).removeprefix("user_config."))
        if isinstance(value, list):
            return [str(v) for v in cast(list[Any], value)]
    home = Path.home()
    fixed = {
        "__dirname": directory, "HOME": str(home), "DESKTOP": str(home / "Desktop"),
        "DOCUMENTS": str(home / "Documents"), "DOWNLOADS": str(home / "Downloads"),
        "pathSeparator": os.sep, "/": os.sep,
    }  # fmt: skip

    def one(match: re.Match[str]) -> str:
        name = match.group(1)
        if name.startswith("user_config."):
            return _text(values.get(name.removeprefix("user_config.")))
        return fixed.get(name, match.group(0))

    return [PLACEHOLDER.sub(one, text)]


def complete(server: Server, given: dict[str, str] | None = None) -> Server:
    """An extension's command with its settings filled in. `given`: the values the
    user typed for what Claude Desktop's files don't show. Raises if one is missing
    once anything was given (that is, when importing)."""
    if server.kind != "extension" or not server.source:
        return server
    given = {k: v for k, v in (given or {}).items() if v}
    defined = cast(dict[str, JSON], server.source["defined"])
    values: JSON = {k: d.get("default") for k, d in defined.items()}
    values |= {k: v for k, v in cast(JSON, server.source["values"]).items() if v is not None}
    values |= given
    template = cast(JSON, server.source["template"])
    directory = str(server.source["dir"])
    args = [
        a
        for arg in cast(list[Any], template.get("args") or [])
        for a in _fill(str(arg), directory, values)
    ]
    env = {
        str(k): _fill(str(v), directory, values)[0]
        for k, v in cast(JSON, template.get("env") or {}).items()
    }
    command = _fill(str(template["command"]), directory, values)[0]
    spec: JSON = {"command": command, "args": args, "env": env}
    if given and (missing := [n.title for n in server.needed if n.key not in given]):
        raise RuleViolationError(f"{server.name} needs {', '.join(missing)} to start.")
    return replace(server, spec=spec, preview=mcp.preview(spec), problem=_missing_command(command))


# --- Importing ---


def imported(session: Session) -> dict[str, str]:
    """Claude Desktop server key (its name, or extension:<folder>) -> the connection."""
    rows = session.scalars(select(Instance).where(Instance.type == InstanceType.MCP)).all()
    return {str(i.config.get("server")): i.name for i in rows if i.config.get("source") == SOURCE}


def _free_name(session: Session, server: str) -> str:
    base = "".join(c if c.isalnum() else "-" for c in server.lower()).strip("-")[:56] or "mcp"
    base = re.sub("-+", "-", base)
    name, n = base, 2
    while instances.find_instance(session, name) is not None:
        name, n = f"{base}-{n}", n + 1
    return name


def import_server(
    session: Session,
    settings: Settings,
    server: Server,
    project: str,
    values: dict[str, str] | None = None,
) -> str:
    """One server -> an `mcp` connection linked to `project`. Returns the connection's
    name. `values`: what an extension needs that Claude Desktop's files don't show."""
    if server.needed:
        given = {k: v.strip() for k, v in (values or {}).items() if v.strip()}
        if missing := [n.title for n in server.needed if n.key not in given]:
            raise RuleViolationError(f"{server.name} needs {', '.join(missing)} to start.")
        server = complete(server, given)
    if server.problem:
        raise RuleViolationError(f"{server.name} can't be imported: {server.problem}.")
    owner = projects.get_project(session, project).owner
    name = _free_name(session, server.name)
    ref = secrets_in.store_connection(
        session, settings, name, InstanceType.MCP, json.dumps(server.spec)
    )
    config: JSON = {
        "source": SOURCE,
        "server": server.key,
        "transport": server.transport,
        "preview": server.preview,
    }
    # Test: a local tool isn't client data by default, and its calls still wait
    # for approval by its tool rules (adapters.mcp). It can be set to Production.
    instances.create_instance(
        session,
        InstanceIn(
            name=name, type=InstanceType.MCP, env=Env.DEV, secret_ref=ref, owner=owner,
            config=config,
        ),
    )  # fmt: skip
    bindings.set_binding(session, BindingIn(project=project, instance=name))
    admin_audit.record(
        session, "connection.import", f"imported MCP server {server.name} from Claude Desktop",
        by="owner", instance=name, project=project,
    )  # fmt: skip
    return name
