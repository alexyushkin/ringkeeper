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
from urllib.parse import urlsplit

from fastmcp import Client
from fastmcp.client.transports import ClientTransport, StdioTransport, StreamableHttpTransport
from pydantic import Field

from ringkeeper import cloud_rules, leadfeeder, plan_limits, remote
from ringkeeper.adapters.base import (
    JSON,
    Adapter,
    Hold,
    PlanLimitError,
    Target,
    ToolError,
    ToolKind,
    ToolSpec,
    read,
)
from ringkeeper.claude_auth import BILLING_ENV_VARS
from ringkeeper.config import get_settings
from ringkeeper.models import InstanceType
from ringkeeper.sql_text import reads_only, sql_of

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
    # A key in the address itself (Zoho's): a long path segment.
    found += [p for p in urlsplit(str(spec.get("url") or "")).path.split("/") if len(p) >= 24]
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
BY_SERVICE = "service"  # what Ringkeeper knows of a Recommended service's tools (ringkeeper.remote)
BY_MEMORY = "memory"  # the owner's own Notion memory (services.personal_memory)
BY_GUARD = "guard"  # the rules for this kind of service: the per-tool setting can't lower them
# An API endpoint that only reads, by its name (getAgent, list_calls, retrieve_phone_number).
READ_ENDPOINT = re.compile(r"^(get|list|retrieve|search|fetch)([_A-Z]|$)")
PLAN_REFUSAL_CHARS = 1000
# A tool whose name says it deletes or resets something, when its server doesn't say.
DESTRUCTIVE_NAME = re.compile(r"delete|drop|remove|destroy|truncate|purge|wipe|reset|erase", re.I)
# When its server doesn't say: a name that only reads (list_issues, getPage, search) runs
# without asking, unless a word in it changes something or may cost money (an Apollo
# enrichment, a paid lookup or check), which still waits for approval.
READ_WORDS = frozenset({
    "get", "list", "search", "find", "fetch", "read", "query", "describe", "count", "show",
    "lookup", "view", "retrieve", "explain", "preview", "status", "whoami", "info", "schema",
})  # fmt: skip
CHANGE_WORDS = frozenset({
    "create", "update", "delete", "send", "post", "put", "patch", "set", "add", "remove",
    "write", "insert", "upload", "publish", "deploy", "run", "execute", "exec", "trigger",
    "start", "stop", "cancel", "merge", "push", "move", "rename", "archive", "invite",
    "share", "assign", "apply", "submit", "save", "edit", "replace", "import", "sync",
    "restore", "reset", "drop", "approve", "reject", "close", "open", "enable", "disable",
    # may cost money
    "enrich", "enrichment", "reveal", "unlock", "purchase", "buy", "charge", "pay",
    "validate", "validation", "verify", "verification", "credit", "credits",
})  # fmt: skip


def name_words(tool: str) -> list[str]:
    """list_issues, getPage, apollo.people-search: its words, lowercase."""
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", tool)
    return [w for w in re.split(r"[^A-Za-z0-9]+", spaced.lower()) if w]


def reads_by_name(tool: str) -> bool:
    words = set(name_words(tool))
    return bool(words & READ_WORDS) and not words & CHANGE_WORDS


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
    if hints.get("read_only") is None and hints.get("destructive") is None and reads_by_name(tool):
        return Rule(READ, BY_NAME)
    return Rule(APPROVE, BY_DEFAULT)


def floor(service: remote.Remote | None, tool: str) -> Rule | None:
    """The least this service's kind allows for `tool`, or None if it sets none."""
    guard = remote.GUARDS.get(service.guard) if service is not None else None
    if service is None or guard is None:
        return None
    words = set(name_words(tool))
    if tool in service.opaque_tools or (words & guard.final and not words & guard.unless):
        return Rule(CONFIRM, BY_GUARD)
    if tool in service.credit_tools:
        return Rule(APPROVE, BY_GUARD)
    return None if reads_by_name(tool) or tool in service.read_tools else Rule(APPROVE, BY_GUARD)


def rule(config: Mapping[str, Any], tool: str) -> Rule:
    """What a call to `tool` needs: the stricter of its own rule and its service's floor."""
    found = _own_rule(config, tool)
    service = remote.BY_KEY.get(str(config.get("remote") or ""))
    least = floor(service, tool)
    if least is None and _server_holds(service, config, tool):
        least = Rule(APPROVE, BY_GUARD)
    if least is not None and LEVELS.index(least.level) > LEVELS.index(found.level):
        return least
    return found


def _server_holds(service: remote.Remote | None, config: Mapping[str, Any], tool: str) -> bool:
    """The server says so itself: this tool takes the argument that lets a held call through."""
    if service is None or not service.confirm_arg:
        return False
    takes = cast(dict[str, list[str]], config.get("arguments") or {})
    return service.confirm_arg in takes.get(tool, [])


def _own_rule(config: Mapping[str, Any], tool: str) -> Rule:
    """The user's choice, else the default."""
    chosen = cast(JSON, config.get("tool_rules") or {}).get(tool)
    if chosen in LEVELS:
        return Rule(str(chosen), BY_USER)
    if tool in cast(list[str], config.get("read_only_tools") or []):  # the first version's ticks
        return Rule(READ, BY_USER)
    service = remote.BY_KEY.get(str(config.get("remote") or ""))
    if service is not None and tool in service.sql_tools:
        return Rule(APPROVE, BY_SQL)  # whatever its hints say: the SQL decides
    if service is not None and tool in service.read_tools:
        return Rule(READ, BY_SERVICE)
    if service is not None and tool in service.endpoint_tools:
        return Rule(APPROVE, BY_SERVICE)  # the careful answer; each call names its endpoint
    return default_rule(cast(JSON, config.get("annotations") or {}).get(tool), tool)


def memory_call(
    config: Mapping[str, Any], tool: str, arguments: Mapping[str, Any] | None
) -> str | None:
    """What this call does with the owner's Notion memory: search, read or save. Notion has no
    save tool of its own: it's a page whose parent is "memory". None: it's not a memory call, or
    only the guide, which is Notion's own help."""
    if config.get("remote") != "notion":
        return None
    given = arguments or {}
    if tool == "notion-memory-search":
        return "search"
    if tool == "notion-fetch" and given.get("id") == "memory":
        return "read"
    if tool == "notion-create-pages" and given.get("parent") == "memory":
        return "save"
    return None


def call_rule(  # noqa: PLR0911 (one per way a call is decided)
    config: Mapping[str, Any], tool: str, arguments: Mapping[str, Any] | None
) -> Rule:
    """What this call needs: the tool's rule, or, for a tool that calls any endpoint of the
    service's API, the endpoint's (unless the user chose a rule for the tool)."""
    found = rule(config, tool)
    if config.get("personal_memory") and memory_call(config, tool, arguments):
        # They run; `_screen` has already refused what mustn't. The owner can only be stricter.
        return found if found.source == BY_USER else Rule(READ, BY_MEMORY)
    if found.source == BY_SQL:  # a statement shown to only read runs; the rest waits
        sql = sql_of(arguments)
        return Rule(READ, BY_SQL) if sql and reads_only(sql) else found
    service = remote.BY_KEY.get(str(config.get("remote") or ""))
    if service is not None and tool in service.command_tools:
        # The command decides: what the tool's name or a setting says can only be stricter.
        level = cloud_rules.level(service.cli, (arguments or {}).get(service.command_tools[tool]))
        strictest = max(level, found.level, key=LEVELS.index) if found.source == BY_USER else level
        return Rule(strictest, BY_GUARD)
    if found.source != BY_SERVICE or service is None or tool not in service.endpoint_tools:
        return found
    names = [arguments.get(a) for a in service.endpoint_tools[tool]] if arguments else []
    endpoint = next((str(n) for n in names if isinstance(n, str) and n), "")
    if READ_ENDPOINT.match(endpoint):
        return Rule(READ, BY_SERVICE)
    if not endpoint or DESTRUCTIVE_NAME.search(endpoint):
        return Rule(CONFIRM, BY_SERVICE)  # deletes, or can't tell what it calls
    return Rule(APPROVE, BY_SERVICE)


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
        if now.source in (BY_SQL, BY_SERVICE, BY_GUARD):
            default = now  # a Recommended service's rules: the statement or endpoint decides
        listed.append(
            {
                "name": t.name,
                "description": (t.description or "")[:1000],
                "input_schema": t.input_schema,
                "runs": RUNS[now.level],
                "read_only": now.level == READ,
                "rule": now.level,
                "rule_from": now.source,
                "locked": now.source == BY_GUARD,
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
    arguments = args.get("arguments")
    given = cast(JSON, arguments) if isinstance(arguments, dict) else None
    service = remote.BY_KEY.get(str(target.config.get("remote") or ""))
    tool = str(args.get("tool", ""))
    if service is not None and tool in service.command_tools:
        command = (given or {}).get(service.command_tools[tool])
        if flag := cloud_rules.escape(service.cli, command):
            raise ToolError(f"{flag} isn't allowed: this connection stays on its own login")
    level = call_rule(target.config, str(args.get("tool", "")), given).level
    return ToolKind.READ if level == READ else ToolKind.WRITE


def _screen(target: Target, args: JSON) -> JSON:
    """What's checked before a memory call, with the database to hand. It answers with the
    call's arguments as they may go."""
    tool, arguments = str(args.get("tool", "")), args.get("arguments")
    given = cast(JSON, arguments) if isinstance(arguments, dict) else {}
    service = remote.BY_KEY.get(str(target.config.get("remote") or ""))
    if service is not None and service.confirm_arg and service.confirm_arg in given:
        raise ToolError(
            f"don't pass {service.confirm_arg}: Ringkeeper confirms it after your approval. Call "
            "the tool without it"
        )
    if kind := memory_call(target.config, tool, given):
        from ringkeeper.services import personal_memory  # noqa: PLC0415 (it needs the database)

        return {**args, "arguments": personal_memory.check(target, kind, given)}
    return args


def _refused_by_plan(text: str) -> bool:
    """Some servers send a refusal as an ordinary answer. A real answer can mention a plan, so
    it only counts when the answer is short and says it was refused."""
    return len(text) <= PLAN_REFUSAL_CHARS and plan_limits.found(text)


def _found_key(value: Any, key: str) -> str | None:
    if isinstance(value, dict):
        found = cast(JSON, value)
        if isinstance(found.get(key), str):
            return str(found[key])
        value = list(found.values())
    if isinstance(value, list):
        for inner in cast(list[Any], value):
            if deeper := _found_key(inner, key):
                return deeper
    return None


def _answer_of(result: Any) -> Any:
    if result.structured_content is not None:
        return result.structured_content
    text = "\n".join(getattr(block, "text", "") for block in result.content).strip()
    try:
        return json.loads(text)
    except ValueError:
        return None


def _server_token(service: remote.Remote, result: Any) -> str | None:
    """The token of a call the server held back, if it did."""
    return _found_key(_answer_of(result), service.confirm_arg) if service.confirm_arg else None


def _holds_next_time(target: Target, service: remote.Remote, tool: str) -> JSON:
    """A tool Ringkeeper took for a read turned out to be held by the server. Nothing ran: it
    waits for a person from now on."""
    from ringkeeper.services import mcp_tools  # noqa: PLC0415 (it needs the database)

    mcp_tools.takes(target.name, tool, service.confirm_arg)
    raise ToolError(f"'{tool}' needs your approval first. Call it again and it will wait for that")


def _saves(target: Target, args: JSON) -> bool:
    arguments = args.get("arguments")
    given = cast(JSON, arguments) if isinstance(arguments, dict) else {}
    return memory_call(target.config, str(args.get("tool", "")), given) == "save"


async def _call_tool(
    target: Target, tool: ToolName, arguments: dict[str, Any] | None = None
) -> JSON:
    """Call one of the server's tools (see list_tools). Waits for the user's approval unless
    list_tools says that tool runs without asking."""
    service = remote.BY_KEY.get(str(target.config.get("remote") or ""))
    try:
        async with _client(target) as client:
            result = await client.call_tool(tool, arguments or {}, raise_on_error=False)
            if service is not None and (token := _server_token(service, result)):
                if call_rule(target.config, tool, arguments).level == READ:
                    return _holds_next_time(target, service, tool)
                # This call is what the user approved: the same call, with the server's token.
                confirmed = {**(arguments or {}), service.confirm_arg: token}
                result = await client.call_tool(tool, confirmed, raise_on_error=False)
    except ToolError:
        raise
    except Exception as error:
        raise ToolError(f"the MCP server didn't answer: {type(error).__name__}: {error}") from None
    text = "\n".join(getattr(block, "text", "") for block in result.content).strip()
    if result.is_error or _refused_by_plan(text):
        raise ToolError(text or f"'{tool}' failed")
    answer: JSON = {"text": text}  # a long one is read in parts: services.continuations
    if result.structured_content is not None:
        answer["structured"] = result.structured_content
    return answer


async def accounts(target: Target) -> str:
    """Which accounts this sign-in reaches, for a link's scope, where the server can say. Raises
    PlanLimitError if the plan reaches nothing in them, with those accounts in its message."""
    service = remote.BY_KEY.get(str(target.config.get("remote") or ""))
    if service is None or not service.plan_probe:
        return ""
    async with _client(target) as client:
        result = await client.call_tool("get_account_info", {}, raise_on_error=False)
        answer = _answer_of(result)
        first = _found_key(answer, "id")
        probed = (
            await client.call_tool(service.plan_probe, {"account_id": first}, raise_on_error=False)
            if first
            else None
        )
    found = (
        cast(list[JSON], cast(JSON, answer).get("data") or []) if isinstance(answer, dict) else []
    )
    shown = [f"{cast(JSON, a.get('attributes') or {}).get('name')} ({a.get('id')})" for a in found]
    said = f"Accounts: {', '.join(shown)}." if shown else ""
    text = "\n".join(getattr(b, "text", "") for b in probed.content) if probed else ""
    if plan_limits.found(text):
        raise PlanLimitError(
            f"{plan_limits.message(service.name, service.plan_note)} {said}".strip()
        )
    return said


async def _names(target: Target, args: JSON) -> JSON:
    """What a job will cost, from the server's own free estimate, for the approval card."""
    service = remote.BY_KEY.get(str(target.config.get("remote") or ""))
    tool, arguments = str(args.get("tool", "")), cast(JSON, args.get("arguments") or {})
    if service is None or tool not in service.credit_tools:
        return {}
    asked = leadfeeder.estimate(tool, arguments)
    if asked is None:
        return {}
    async with _client(target) as client:
        result = await client.call_tool(asked[0], asked[1], raise_on_error=False)
    estimated = None if result.is_error else leadfeeder.total(_answer_of(result))
    return {} if estimated is None else {"credits": estimated}


def credit_card(config: Mapping[str, Any], args: JSON, estimated: float | None) -> JSON | None:
    """What an approval card shows for a call that may spend credits, or None."""
    service = remote.BY_KEY.get(str(config.get("remote") or ""))
    tool, arguments = str(args.get("tool", "")), cast(JSON, args.get("arguments") or {})
    if service is None or tool not in service.credit_tools:
        return None
    return {
        "amount": leadfeeder.cost(tool, arguments, estimated), "who": "For",
        "counterparty": leadfeeder.what(tool), "which": f"{service.name} account",
        "account": str(arguments.get("account_id") or "not stated"),
    }  # fmt: skip


call_tool = ToolSpec(
    name="call_tool",
    kind=ToolKind.WRITE,  # the careful answer; _classify decides per call
    description=(_call_tool.__doc__ or "").strip(),
    handler=_call_tool,
    hold=Hold.ALWAYS,
    classify=_classify,
    screen=_screen,
    logged=_saves,
    names=_names,
)

ADAPTER = Adapter(
    service=InstanceType.MCP,
    tools=(list_tools, call_tool),
    secret_fragments=secret_fragments,
)
