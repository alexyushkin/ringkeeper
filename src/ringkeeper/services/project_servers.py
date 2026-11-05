"""MCP servers a project already uses, offered as that project's connections.

Found in the project's own folder: .mcp.json (Claude Code), .cursor/mcp.json, .vscode/mcp.json
and .codex/config.toml; Claude Code's per-project servers in ~/.claude.json; and a server
written in the project's own code. Each is offered once, masked, and connected only when
you confirm that one (api.convenience_routes). Like env_scan, a value never leaves the
server: confirming names a finding, whose value is read again from the file.

A `${NAME}` in a config is filled in from the project's .env files; one that isn't there
can't be connected yet. An address on a temporary tunnel gets a warning: it stops working
when the tunnel does.
"""

import json
import re
import tomllib
from pathlib import Path
from typing import Any, cast

from ringkeeper import netpolicy
from ringkeeper.adapters import mcp
from ringkeeper.adapters.base import JSON, ToolError
from ringkeeper.services import catalog, env_scan
from ringkeeper.services.claude_desktop import missing_command
from ringkeeper.services.env_scan import Finding, mask

CONFIGS = ((".mcp.json", "mcpServers"), (".cursor/mcp.json", "mcpServers"),
           (".vscode/mcp.json", "servers"))  # fmt: skip
CODEX = ".codex/config.toml"
MAX_BYTES = env_scan.MAX_BYTES
TUNNELS = ("trycloudflare.com", "ngrok.io", "ngrok-free.app", "ngrok.app", "loca.lt",
           "serveo.net", "localhost.run")  # fmt: skip
TUNNEL_WARNING = (
    "This address is a temporary tunnel, so it stops working when the tunnel does. Use a "
    "fixed address if you can."
)
VARIABLE = re.compile(r"\$\{(?:env:)?([A-Za-z_][A-Za-z0-9_]*)\}")
# Where a project's own server is likely to be, and what marks one.
CODE_FOLDERS = (".", "src", "server", "mcp", "mcp-server", "mcp_server")
CODE_FILES = {".py": ("python",), ".js": ("node",), ".mjs": ("node",), ".ts": ("npx", "tsx")}
CODE_MARK = re.compile(r"\bFastMCP\(|\bnew McpServer\(|\bMcpServer\(")
MOST_CODE_FILES = 200


def _read_json(path: Path) -> JSON:
    try:
        if path.stat().st_size > 20 * MAX_BYTES:
            return {}
        data: Any = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}
    return cast(JSON, data) if isinstance(data, dict) else {}


def _entries(folder: Path) -> list[tuple[str, str, JSON]]:
    """(file, server's name, its entry) from every config in the folder."""
    found: list[tuple[str, str, JSON]] = []
    for name, key in CONFIGS:
        path = folder / name
        if env_scan.inside(folder, path):
            servers = _read_json(path).get(key)
            if isinstance(servers, dict):
                found += [(name, str(n), cast(JSON, e)) for n, e in cast(JSON, servers).items()
                          if isinstance(e, dict)]  # fmt: skip
    codex = folder / CODEX
    if env_scan.inside(folder, codex):
        try:
            servers = tomllib.loads(codex.read_text(encoding="utf-8")).get("mcp_servers")
        except (OSError, ValueError):
            servers = None
        if isinstance(servers, dict):
            found += [(CODEX, str(n), cast(JSON, e)) for n, e in cast(JSON, servers).items()
                      if isinstance(e, dict)]  # fmt: skip
    return found + _claude_code(folder)


def _same_folder(a: str, b: Path) -> bool:
    def plain(text: str) -> str:
        return text.replace("\\", "/").rstrip("/").casefold()

    return plain(a) == plain(str(b))


def _claude_code(folder: Path) -> list[tuple[str, str, JSON]]:
    """Servers Claude Code keeps for this folder in the user's ~/.claude.json."""
    known = _read_json(Path.home() / ".claude.json").get("projects")
    if not isinstance(known, dict):
        return []
    found: list[tuple[str, str, JSON]] = []
    for path, entry in cast(JSON, known).items():
        servers = cast(JSON, entry).get("mcpServers") if isinstance(entry, dict) else None
        if _same_folder(path, folder.resolve()) and isinstance(servers, dict):
            found += [("Claude Code's settings", str(n), cast(JSON, e))
                      for n, e in cast(JSON, servers).items() if isinstance(e, dict)]  # fmt: skip
    return found


def _filled(value: str, known: dict[str, str], missing: list[str]) -> str:
    def one(match: re.Match[str]) -> str:
        name = match.group(1)
        if name.upper() not in known:
            missing.append(name)
            return match.group(0)
        return known[name.upper()]

    return VARIABLE.sub(one, value)


def _spec(entry: JSON, known: dict[str, str], missing: list[str]) -> JSON:
    """How to start or reach the server, the way adapters.mcp stores it."""
    url = entry.get("url") or entry.get("serverUrl")
    raw_headers: object = entry.get("headers") or entry.get("http_headers")
    headers = cast(JSON, raw_headers) if isinstance(raw_headers, dict) else {}
    if isinstance(url, str):
        return {
            "url": _filled(url, known, missing),
            "headers": {str(k): _filled(str(v), known, missing) for k, v in headers.items()},
        }
    raw_args = entry.get("args")
    raw_env = entry.get("env")
    args = cast(list[Any], raw_args) if isinstance(raw_args, list) else []
    env = cast(JSON, raw_env) if isinstance(raw_env, dict) else {}
    return {
        "command": str(entry.get("command") or ""),
        "args": [_filled(str(a), known, missing) for a in args],
        "env": {str(k): _filled(str(v), known, missing) for k, v in env.items()},
    }


def _problem(spec: JSON) -> tuple[str, str]:
    """(why it can't connect, a warning), each maybe empty."""
    url = spec.get("url")
    if isinstance(url, str):
        try:
            catalog.check_remote_url(url)
        except netpolicy.AddressNotAllowedError as error:
            return f"It can't connect as it is: {error}.", ""
        host = re.sub(r"^https?://", "", url).split("/", 1)[0].split(":", 1)[0].lower()
        tunnel = any(host == t or host.endswith(f".{t}") for t in TUNNELS)
        return "", TUNNEL_WARNING if tunnel else ""
    return missing_command(str(spec.get("command") or "")) or "", ""


def _finding(file: str, name: str, spec: JSON, why: str, warning: str) -> tuple[Finding, str, None]:
    shown = mask(str(spec["url"])) if "url" in spec else mcp.preview(spec)
    identity = json.dumps({k: spec.get(k) for k in ("url", "command", "args")}, sort_keys=True)
    finding = Finding(
        env_scan.finding_id(file, "mcp", [name, identity]), file, "mcp", name, [name], shown, name,
        usable=not why, why=why, warning=warning,
    )  # fmt: skip
    return finding, json.dumps(spec), None


def _code_files(folder: Path) -> list[Path]:
    found: list[Path] = []
    for name in CODE_FOLDERS:
        where = folder / name
        if where.is_dir() and where.resolve().is_relative_to(folder.resolve()):
            found += [p for p in sorted(where.iterdir()) if p.suffix in CODE_FILES and p.is_file()]
    return found[:MOST_CODE_FILES]


def _in_code(folder: Path) -> list[tuple[Finding, str, None]]:
    """A server written in the project: how to start it, by what the file is."""
    found: list[tuple[Finding, str, None]] = []
    for path in _code_files(folder):
        try:
            if path.stat().st_size > MAX_BYTES or not CODE_MARK.search(
                path.read_text(encoding="utf-8", errors="replace")
            ):
                continue
        except OSError:
            continue
        *command, last = (*CODE_FILES[path.suffix], str(path.resolve()))
        spec: JSON = {"command": command[0], "args": [*command[1:], last], "env": {}}
        file = path.relative_to(folder).as_posix()
        why = missing_command(command[0]) or ""
        finding, secret, address = _finding(file, f"{folder.name} MCP server", spec, why, "")
        found.append((finding, secret, address))
    return found


def scan(folder: Path) -> list[tuple[Finding, str, None]]:
    """Every MCP server the project uses; the same one in two files shows once."""
    known: dict[str, str] = {}
    for path in env_scan.files_in(folder):
        known = env_scan.values_in(path) | known
    found: list[tuple[Finding, str, None]] = []
    seen: set[str] = set()
    for file, name, entry in _entries(folder):
        missing: list[str] = []
        spec = _spec(entry, known, missing)
        try:
            mcp.server_spec(json.dumps(spec))
        except ToolError:
            continue  # neither a command nor an address
        why, warning = _problem(spec)
        if missing:
            why = f"It needs {', '.join(sorted(set(missing)))}, which isn't in this project's .env."
        finding, secret, address = _finding(file, name, spec, why, warning)
        if secret not in seen:
            seen.add(secret)
            found.append((finding, secret, address))
    return found + [m for m in _in_code(folder) if m[1] not in seen]


def everything(folder: Path) -> list[tuple[Finding, str, str | None]]:
    """What the project's folder offers: its keys, then its MCP servers."""
    return [*env_scan.scan(folder), *scan(folder)]
