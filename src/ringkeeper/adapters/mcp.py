"""MCP servers imported from Claude Desktop, run behind the gateway.

The secret (in the vault) is how to start or reach the server, JSON:

    {"command": "npx", "args": ["-y", "some-server"], "env": {"API_KEY": "..."}}
    {"url": "https://example.com/mcp", "headers": {"Authorization": "Bearer ..."}}
    {"url": "https://mcp.neon.tech/mcp", "oauth": {"access_token": "...", ...}}

The last is a remote service signed in to (services.mcp_oauth, ringkeeper.remote).

Args go in the vault too: they often hold a connection string or a token.
The connection's `config` holds what isn't secret: a preview of the command
with secrets masked, the server's own hints about each tool (its MCP
annotations, as last listed), and the user's choices per tool.

A local (command) server runs on the gateway's machine, so in practice the
desktop app: started for each call with a small environment (no Claude
credential, nothing of Ringkeeper's), in its own folder, and stopped after.
The agent sees two tools: list_tools and call_tool. What a call to one of
the server's tools needs (see `rule`):

- the user's choice for that tool, if they made one; otherwise
- its annotations: readOnlyHint runs it without asking, destructiveHint
  needs the connection's name typed to approve; otherwise
- a name that sounds like deleting needs the name typed; anything else
  waits for approval.
"""

import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, cast

from fastmcp import Client
from fastmcp.client.transports import ClientTransport, StdioTransport, StreamableHttpTransport
from pydantic import Field

from ringkeeper import remote
from ringkeeper.adapters.base import (
    JSON,
    Adapter,
    Hold,
    Target,
    ToolError,
    ToolKind,
    ToolSpec,
    read,
)
from ringkeeper.claude_auth import BILLING_ENV_VARS
from ringkeeper.config import get_settings
from ringkeeper.models import InstanceType

TIMEOUT_SECONDS = 120  # npx may download the server the first time
# What a local server may see of the gateway's environment: enough for npx, uvx
# and Windows itself to work; never a credential or Ringkeeper's own settings.
PASSED_ENV = (
    "PATH", "PATHEXT", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "TEMP", "TMP",
    "USERPROFILE", "HOME", "APPDATA", "LOCALAPPDATA", "PROGRAMDATA", "PROGRAMFILES",
    "PROGRAMFILES(X86)", "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE", "LANG", "LC_ALL",
    "TZ", "SSL_CERT_FILE", "NODE_EXTRA_CA_CERTS",
)  # fmt: skip
ToolName = Annotated[str, Field(min_length=1, max_length=128, description="From list_tools.")]


def server_spec(value: str) -> JSON:
    """The stored JSON, checked: a command (with args and env) or a URL (with headers)."""
    try:
        data: Any = json.loads(value)
    except ValueError:
        raise ToolError("this MCP server's settings aren't in the expected form") from None
    spec = cast(JSON, data) if isinstance(data, dict) else {}
    command, url = spec.get("command"), spec.get("url")
    oauth = spec.get("oauth")
    if isinstance(url, str) and url.startswith(("https://", "http://")):
        headers = _strings(spec.get("headers"))
        if isinstance(oauth, dict) and (token := cast(JSON, oauth).get("access_token")):
            # Signed in (services.mcp_oauth); the token is refreshed before it's read.
            headers["Authorization"] = f"Bearer {token}"
        return {"url": url, "headers": headers}
    if isinstance(command, str) and command.strip():
        raw: object = spec.get("args") or []
        items = cast(list[object], raw) if isinstance(raw, list) else None
        if items is None or not all(isinstance(a, str) for a in items):
            raise ToolError("this MCP server's args must be a list of strings")
        args = [str(a) for a in items]
        return {"command": command, "args": args, "env": _strings(spec.get("env"))}
    raise ToolError("this MCP server has neither a command nor a URL")


def _strings(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {str(k): str(v) for k, v in cast(dict[Any, Any], value).items()}


def secret_fragments(value: str) -> list[str]:
    """Env and header values, and args that look secret, so none of them leaks alone."""
    try:
        spec = server_spec(value)
    except ToolError:
        return []
    found = [*spec.get("env", {}).values(), *spec.get("headers", {}).values()]
    found += [h.split(" ", 1)[1] for h in spec.get("headers", {}).values() if " " in h]
    oauth: object = json.loads(value).get("oauth")
    if isinstance(oauth, dict):
        tokens = cast(JSON, oauth)
        found += [str(tokens.get(k) or "") for k in ("access_token", "refresh_token")]
        found += [str(cast(JSON, tokens.get("client") or {}).get("client_secret") or "")]
    found += [a for a in spec.get("args", []) if looks_secret(a)]
    return [f for f in found if len(f) >= 6]


SECRET_ARG = re.compile(
    r"://[^/\s:@]+:[^/\s@]+@"  # a URL with a password in it
    r"|^(sk-|pat|ghp_|gho_|github_pat_|xox[abpr]-|AKIA|eyJ|glpat-|shpat_|sk_live_|rk_live_)"
    r"|(key|token|secret|password|passwd|auth)[^=]*=.+",
    re.IGNORECASE,
)


def looks_secret(arg: str) -> bool:
    return bool(SECRET_ARG.search(arg)) or (len(arg) >= 32 and " " not in arg and "/" not in arg
                                            and "\\" not in arg and "." not in arg)  # fmt: skip


def preview(spec: JSON) -> str:
    """How the server starts, for people to read: secret-looking args masked."""
    if "url" in spec:
        return str(spec["url"])
    parts = [str(spec["command"])] + [
        "••••" if looks_secret(a) else a for a in cast(list[str], spec.get("args", []))
    ]
    return " ".join(parts)


def _env(extra: Mapping[str, str]) -> dict[str, str]:
    env = {k: v for k in PASSED_ENV if (v := os.environ.get(k)) is not None}
    env |= dict(extra)
    for name in BILLING_ENV_VARS:  # the server's own env may not switch Claude billing
        env.pop(name, None)
    return env


def _transport(target: Target) -> ClientTransport:
    spec = server_spec(target.secret.reveal())
    if "url" in spec:
        return StreamableHttpTransport(spec["url"], headers=spec["headers"])
    folder = get_settings().data_dir / "mcp" / target.name
    folder.mkdir(parents=True, exist_ok=True)
    return StdioTransport(
        spec["command"], spec["args"], env=_env(spec["env"]), cwd=str(Path(folder)),
        keep_alive=False,
    )  # fmt: skip


def _client(target: Target) -> Client[Any]:
    return Client(_transport(target), timeout=TIMEOUT_SECONDS)


# What a call to one of the server's tools needs.
READ, APPROVE, CONFIRM = "read", "approve", "confirm"
LEVELS = (READ, APPROVE, CONFIRM)
# Where that came from.
BY_USER, BY_ANNOTATIONS, BY_NAME, BY_DEFAULT = "you", "annotations", "name", "default"
BY_SQL = "sql"  # a remote service's SQL tool: the statement decides (ringkeeper.destructive)
# A tool whose name says it deletes or resets something, when its server doesn't say.
DESTRUCTIVE_NAME = re.compile(r"delete|drop|remove|destroy|truncate|purge|wipe|reset|erase", re.I)
RUNS = {
    READ: "without asking",
    APPROVE: "after the user approves",
    CONFIRM: "after the user approves and types the connection's name",
}


@dataclass(frozen=True)
class Rule:
    level: str  # READ, APPROVE or CONFIRM
    source: str  # BY_USER, BY_ANNOTATIONS, BY_NAME or BY_DEFAULT


def default_rule(hints: JSON | None, tool: str) -> Rule:
    """From the server's annotations for this tool, else its name."""
    hints = hints or {}
    if hints.get("read_only") is True:
        return Rule(READ, BY_ANNOTATIONS)
    if hints.get("destructive") is True:
        return Rule(CONFIRM, BY_ANNOTATIONS)
    if hints.get("destructive") is False:
        return Rule(APPROVE, BY_ANNOTATIONS)
    if DESTRUCTIVE_NAME.search(tool):
        return Rule(CONFIRM, BY_NAME)
    return Rule(APPROVE, BY_DEFAULT)


def rule(config: Mapping[str, Any], tool: str) -> Rule:
    """What a call to `tool` needs: the user's choice, else the default."""
    chosen = cast(JSON, config.get("tool_rules") or {}).get(tool)
    if chosen in LEVELS:
        return Rule(str(chosen), BY_USER)
    if tool in cast(list[str], config.get("read_only_tools") or []):  # the first version's ticks
        return Rule(READ, BY_USER)
    service = remote.BY_KEY.get(str(config.get("remote") or ""))
    if service is not None and tool in service.sql_tools:
        return Rule(APPROVE, BY_SQL)  # whatever its hints say: the SQL decides
    return default_rule(cast(JSON, config.get("annotations") or {}).get(tool), tool)


def _hints(tool: Any) -> JSON:
    notes = getattr(tool, "annotations", None)
    return {
        "read_only": getattr(notes, "read_only_hint", None),
        "destructive": getattr(notes, "destructive_hint", None),
    }


@read
async def list_tools(target: Target) -> JSON:
    """List this server's tools: name, what it does, its arguments, and how a call runs
    (without asking, after the user approves, or after approval with the name typed)."""
    try:
        async with _client(target) as client:
            tools = await client.list_tools()
    except ToolError:
        raise
    except Exception as error:  # a server that won't start or answer
        raise ToolError(f"the MCP server didn't answer: {type(error).__name__}: {error}") from None
    hints = {t.name: _hints(t) for t in tools}
    # The rules as they will be once these hints are remembered (services.mcp_tools).
    config = {**target.config, "annotations": hints}
    listed: list[JSON] = []
    for t in tools:
        now, default = rule(config, t.name), default_rule(hints[t.name], t.name)
        if now.source == BY_SQL:
            default = now  # a remote service's SQL tool: its statement decides
        listed.append(
            {
                "name": t.name,
                "description": (t.description or "")[:1000],
                "input_schema": t.input_schema,
                "runs": RUNS[now.level],
                "read_only": now.level == READ,
                "rule": now.level,
                "rule_from": now.source,
                "hints": hints[t.name],
                "default": default.level,
                "default_from": default.source,
            }
        )
    return {"tools": listed}


def annotations_of(listed: JSON) -> JSON:
    """The server's hints per tool, from list_tools' answer, to remember with the connection."""
    return {str(t["name"]): t["hints"] for t in cast(list[JSON], listed.get("tools") or [])}


def arguments_of(listed: JSON) -> dict[str, list[str]]:
    """Each tool's argument names, so a link's scope can fill in an ID (ringkeeper.remote)."""
    return {
        str(t["name"]): sorted(
            cast(JSON, cast(JSON, t.get("input_schema") or {}).get("properties") or {})
        )
        for t in cast(list[JSON], listed.get("tools") or [])
    }


def _classify(target: Target, args: JSON) -> ToolKind:
    level = rule(target.config, str(args.get("tool", ""))).level
    return ToolKind.READ if level == READ else ToolKind.WRITE


async def _call_tool(
    target: Target, tool: ToolName, arguments: dict[str, Any] | None = None
) -> JSON:
    """Call one of the server's tools (see list_tools). Waits for the user's approval unless
    list_tools says that tool runs without asking."""
    try:
        async with _client(target) as client:
            result = await client.call_tool(tool, arguments or {}, raise_on_error=False)
    except Exception as error:
        raise ToolError(f"the MCP server didn't answer: {type(error).__name__}: {error}") from None
    text = "\n".join(getattr(block, "text", "") for block in result.content).strip()
    if result.is_error:
        raise ToolError(text or f"'{tool}' failed")
    answer: JSON = {"text": text[:50_000]}
    if result.structured_content is not None:
        answer["structured"] = result.structured_content
    return answer


call_tool = ToolSpec(
    name="call_tool",
    kind=ToolKind.WRITE,  # the careful answer; _classify decides per call
    description=(_call_tool.__doc__ or "").strip(),
    handler=_call_tool,
    hold=Hold.ALWAYS,
    classify=_classify,
)

ADAPTER = Adapter(
    service=InstanceType.MCP,
    tools=(list_tools, call_tool),
    secret_fragments=secret_fragments,
)
