"""Importing MCP servers from Claude Desktop's claude_desktop_config.json.

Desktop mode only: the file is on this computer. Each server the user picks
becomes an `mcp` connection linked to one project. Everything needed to start
it (command, args, env; or URL and headers) goes into the vault, read here
straight from the file, so secrets never pass through the browser and never
sit in plain text in Ringkeeper's database. What stays in the connection's
config is a preview with secret-looking args masked.

A server that runs a command (npx, uvx, node, python, docker...) can only be
imported when that command is on this computer, because the gateway starts it.
"""

import json
import os
import shutil
import sys
from dataclasses import dataclass
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


@dataclass(frozen=True)
class Server:
    name: str
    spec: JSON  # with its secrets: only ever stored in the vault
    transport: str  # "command" or "url"
    preview: str
    problem: str | None  # why it can't be imported here, if it can't

    def summary(self) -> JSON:
        secrets = len(self.spec.get("env", {})) + len(self.spec.get("headers", {}))
        secrets += sum(1 for a in self.spec.get("args", []) if mcp.looks_secret(a))
        return {
            "name": self.name,
            "transport": self.transport,
            "preview": self.preview,
            "secrets": secrets,
            "problem": self.problem,
        }


def read_servers(path: Path) -> list[Server]:
    try:
        data: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise RuleViolationError(f"Couldn't read Claude Desktop's settings: {error}") from None
    listed = cast(JSON, data).get("mcpServers") if isinstance(data, dict) else None
    if not isinstance(listed, dict):
        return []
    return [_server(str(name), entry) for name, entry in cast(JSON, listed).items()]


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
    problem = None
    if shutil.which(command) is None:
        needs = NEEDS.get(Path(command).stem.lower())
        problem = (
            f"needs {needs} ({command}), which isn't on this computer"
            if needs
            else f"runs {command}, which isn't on this computer"
        )
    return Server(name, spec, "command", mcp.preview(spec), problem)


def imported(session: Session) -> dict[str, str]:
    """Claude Desktop server name -> the connection it became."""
    rows = session.scalars(select(Instance).where(Instance.type == InstanceType.MCP)).all()
    return {str(i.config.get("server")): i.name for i in rows if i.config.get("source") == SOURCE}


def _free_name(session: Session, server: str) -> str:
    base = "".join(c if c.isalnum() else "-" for c in server.lower()).strip("-")[:56] or "mcp"
    name, n = base, 2
    while instances.find_instance(session, name) is not None:
        name, n = f"{base}-{n}", n + 1
    return name


def import_server(session: Session, settings: Settings, server: Server, project: str) -> str:
    """One server -> an `mcp` connection linked to `project`. Returns the connection's name."""
    if server.problem:
        raise RuleViolationError(f"{server.name} can't be imported: {server.problem}.")
    owner = projects.get_project(session, project).owner
    name = _free_name(session, server.name)
    ref = secrets_in.store_connection(
        session, settings, name, InstanceType.MCP, json.dumps(server.spec)
    )
    config: JSON = {
        "source": SOURCE,
        "server": server.name,
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
