"""Connections set up in other AI apps on this computer, to import like Claude Desktop's.

Read only: Ringkeeper never writes to these files (it writes a pass into Claude Code's and
Cursor's, which is services.app_setup). The places and shapes follow Ringkeeper Connect's list
of apps. A file that isn't there is skipped; one that can't be read is reported with a plain
reason, never skipped silently.

Each app writes a few things its own way: a URL as `serverUrl` (Windsurf) or `httpUrl`
(Gemini CLI), Zed's command as an object, Continue's list in YAML, Codex's in TOML. They're
made into the usual command-or-URL entry here. A value that refers to a variable (Cursor's
`${env:NAME}`, `${NAME}` elsewhere) is filled in from this computer's environment; one that
isn't set there is named, and that server can't be imported until it is.
"""

import json
import os
import re
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import yaml

JSON = dict[str, Any]


@dataclass(frozen=True)
class Place:
    path: Path
    key: str = "mcpServers"  # where the list of servers is in the file
    kind: str = "json"  # json (with comments allowed), yaml or toml


@dataclass(frozen=True)
class App:
    id: str
    label: str
    places: tuple[Place, ...]


@dataclass(frozen=True)
class Found:
    """One server as an app has it, made into the usual entry (command or URL)."""

    app: str
    label: str
    name: str
    entry: JSON
    unset: tuple[str, ...] = ()  # variables it refers to that aren't set here


@dataclass(frozen=True)
class Seen:
    id: str
    label: str
    count: int
    problems: tuple[str, ...]


def _home() -> Path:
    return Path(os.environ.get("USERPROFILE") or os.environ.get("HOME") or Path.home())


def _roaming() -> Path:
    """Windows' roaming app data folder."""
    return Path(os.environ.get("APPDATA") or _home() / "AppData" / "Roaming")


def _editor_user(app: str) -> Path:
    """VS Code's (and its forks') settings folder."""
    if sys.platform == "win32":
        return _roaming() / app / "User"
    if sys.platform == "darwin":
        return _home() / "Library" / "Application Support" / app / "User"
    return _home() / ".config" / app / "User"


def _one(app_id: str, label: str, path: Path, key: str = "mcpServers", kind: str = "json") -> App:
    return App(app_id, label, (Place(path, key, kind),))


def apps() -> list[App]:
    """Every app Ringkeeper looks in, Claude Desktop aside (services.claude_desktop)."""
    home = _home()
    win = sys.platform == "win32"
    config = _roaming() if win else home / ".config"
    claude = Path(os.environ.get("CLAUDE_CONFIG_DIR") or home)
    editors = [_editor_user(a) for a in ("Code", "Cursor", "Windsurf")]

    def in_editors(extension: str, file: str) -> list[Place]:
        return [Place(u / "globalStorage" / extension / "settings" / file) for u in editors]

    cline = [
        Place(home / ".cline" / "data" / "settings" / "cline_mcp_settings.json"),
        *in_editors("saoudrizwan.claude-dev", "cline_mcp_settings.json"),
        Place(home / "Documents" / "Cline" / "MCP" / "cline_mcp_settings.json"),
    ]
    more = home / ".continue" / "mcpServers"
    listed = sorted(more.glob("*")) if more.is_dir() else []
    continued = [
        Place(home / ".continue" / "config.yaml", kind="yaml"),
        *(Place(p, kind="yaml") for p in listed if p.suffix.lower() in {".yaml", ".yml"}),
        *(Place(p) for p in listed if p.suffix.lower() == ".json"),
    ]
    amazon = home / ".aws" / "amazonq"
    lm_studio = [
        Place(home / ".lmstudio" / "mcp.json"),
        Place(home / ".cache" / "lm-studio" / "mcp.json"),
    ]
    found = [
        _one("claude-code", "Claude Code", claude / ".claude.json"),
        _one("cursor", "Cursor", home / ".cursor" / "mcp.json"),
        _one("vscode", "VS Code", editors[0] / "mcp.json", key="servers"),
        _one("windsurf", "Windsurf", home / ".codeium" / "windsurf" / "mcp_config.json"),
        _one(
            "windsurf-next",
            "Windsurf Next",
            home / ".codeium" / "windsurf-next" / "mcp_config.json",
        ),
        _one("devin", "Devin Desktop", config / "devin" / "mcp_config.json"),
        _one("gemini-cli", "Gemini CLI", home / ".gemini" / "settings.json"),
        _one("antigravity", "Antigravity", home / ".gemini" / "config" / "mcp_config.json"),
        _one("copilot-cli", "GitHub Copilot CLI", home / ".copilot" / "mcp-config.json"),
        _one("kiro", "Kiro", home / ".kiro" / "settings" / "mcp.json"),
        _one("codex", "Codex", home / ".codex" / "config.toml", key="mcp_servers", kind="toml"),
        App("cline", "Cline", tuple(cline)),
        App(
            "roo-code",
            "Roo Code",
            tuple(in_editors("rooveterinaryinc.roo-cline", "mcp_settings.json")),
        ),
        _one("warp", "Warp", home / ".warp" / ".mcp.json"),
        App("lm-studio", "LM Studio", tuple(lm_studio)),
        _one("junie", "Junie", home / ".junie" / "mcp" / "mcp.json"),
        _one(
            "zed",
            "Zed",
            config / ("Zed" if win else "zed") / "settings.json",
            key="context_servers",
        ),
        App("continue", "Continue", tuple(continued)),
        App(
            "amazon-q",
            "Amazon Q",
            (Place(amazon / "mcp.json"), Place(amazon / "agents" / "default.json")),
        ),
    ]
    if win:  # Visual Studio's file is in the home folder, on Windows only
        found.insert(3, _one("visual-studio", "Visual Studio", home / ".mcp.json", key="servers"))
    return found


_COMMENTS = re.compile(r'"(?:\\.|[^"\\])*"|//[^\n]*|/\*.*?\*/', re.DOTALL)
_TRAILING = re.compile(r'"(?:\\.|[^"\\])*"|,(\s*[}\]])', re.DOTALL)


def jsonc(text: str) -> Any:
    """JSON as editors write it: comments and trailing commas allowed."""
    plain = _COMMENTS.sub(lambda m: m.group(0) if m.group(0).startswith('"') else "", text)
    plain = _TRAILING.sub(lambda m: m.group(1) if m.group(1) is not None else m.group(0), plain)
    return json.loads(plain)


class UnreadableError(Exception):
    """Why a file couldn't be read, in plain words."""


def _parse(place: Place) -> Any:
    """The file's contents, parsed by its kind; None when it's empty."""
    try:
        text = place.path.read_text(encoding="utf-8-sig")
    except OSError:
        raise UnreadableError("Ringkeeper isn't allowed to open it") from None
    if not text.strip():
        return None
    parse = {"toml": tomllib.loads, "yaml": yaml.safe_load}.get(place.kind, jsonc)
    try:
        return parse(text)
    except (ValueError, yaml.YAMLError):  # TOMLDecodeError is a ValueError
        raise UnreadableError("it isn't valid") from None


def read(place: Place) -> dict[str, Any]:
    """The servers in one file, by name. Raises UnreadableError."""
    root = _parse(place)
    if root is None:
        return {}
    if not isinstance(root, dict):
        raise UnreadableError("its settings aren't in the expected shape")
    listed = cast(JSON, root).get(place.key)
    if listed is None:
        return {}
    if isinstance(listed, list):  # Continue: a list of {name, ...}
        named: dict[str, Any] = {}
        for item in cast(list[Any], listed):
            fields = cast(JSON, item) if isinstance(item, dict) else {}
            if isinstance(fields.get("name"), str):
                named[str(fields["name"])] = {k: v for k, v in fields.items() if k != "name"}
        return named
    if not isinstance(listed, dict):
        raise UnreadableError("its list of connections isn't in the expected shape")
    return cast(JSON, listed)


_VARIABLE = re.compile(r"\$\{\s*(?:env:)?([A-Za-z_][A-Za-z0-9_]*)\s*\}")


def _fill(value: Any, unset: set[str]) -> Any:
    if isinstance(value, str):

        def known(match: re.Match[str]) -> str:
            found = os.environ.get(match.group(1))
            if found is None:
                unset.add(match.group(1))
                return match.group(0)
            return found

        return _VARIABLE.sub(known, value)
    if isinstance(value, list):
        return [_fill(v, unset) for v in cast(list[Any], value)]
    if isinstance(value, dict):
        return {k: _fill(v, unset) for k, v in cast(JSON, value).items()}
    return value


def entry(raw: Any) -> tuple[JSON, tuple[str, ...]]:
    """An app's server made into the usual entry, and the variables it needs that aren't set."""
    fields = dict(cast(JSON, raw)) if isinstance(raw, dict) else {}
    command = fields.get("command")
    if isinstance(command, dict):  # Zed: {"command": {"path", "args", "env"}}
        nested = cast(JSON, command)
        fields |= {
            "command": nested.get("path"),
            "args": nested.get("args"),
            "env": nested.get("env"),
        }
    url = fields.get("url") or fields.get("serverUrl") or fields.get("httpUrl")
    made: JSON = {"type": fields.get("type")} if fields.get("type") else {}
    if isinstance(url, str) and url:
        made |= {"url": url, "headers": fields.get("headers") or {}}
    else:
        made |= {
            "command": fields.get("command"),
            "args": fields.get("args") or [],
            "env": fields.get("env") or {},
        }
    unset: set[str] = set()
    filled = cast(JSON, _fill(made, unset))
    return filled, tuple(sorted(unset))


def ours(raw: Any) -> bool:
    """A Ringkeeper pass an app was given (services.app_setup): nothing to import."""
    fields = cast(JSON, raw) if isinstance(raw, dict) else {}
    raw_args: Any = fields.get("args") or []
    args = [str(a) for a in cast(list[Any], raw_args)] if isinstance(raw_args, list) else []
    return "--pass" in args and "ringkeeper" in args


def scan() -> tuple[list[Found], list[Seen]]:
    """Every server in every app here, and what was seen in each app (files it couldn't read
    included). Apps with none of their files here aren't listed."""
    found: list[Found] = []
    seen: list[Seen] = []
    for app in apps():
        places = [p for p in app.places if p.path.is_file()]
        if not places:
            continue
        problems: list[str] = []
        count = 0
        for place in places:
            try:
                servers = read(place)
            except UnreadableError as reason:
                problems.append(f"Couldn't read {app.label}'s settings ({place.path}): {reason}.")
                continue
            for name, raw in servers.items():
                if ours(raw):
                    continue
                made, unset = entry(raw)
                found.append(Found(app.id, app.label, str(name), made, unset))
                count += 1
        seen.append(Seen(app.id, app.label, count, tuple(problems)))
    return found, seen
