"""What a connection's card can copy or save, for the owner to pass on: its tool list, and its
details for support. One click in the app, never a script (see CLAUDE.md).

Neither holds a token, a key, an address that may carry one, or a client's data: the tool list
is each tool's name, the server's own flags, the rule Ringkeeper applies, its argument names and
the first line of its help; the details are what kind of connection it is, how it signs in, when
that ends, its last test, how many tools it has and the app's version. Neither reads the vault.
"""

import inspect
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy.orm import Session

from ringkeeper import __version__, remote
from ringkeeper.adapters import ADAPTERS, mcp, vendor
from ringkeeper.adapters.base import Hold, ToolKind, ToolSpec
from ringkeeper.adapters.vendor_spec import DRAFT, FINAL, HELD, PLACEHOLDER, READ, Op, Vendor
from ringkeeper.config import get_settings
from ringkeeper.db import session_scope
from ringkeeper.models import Env, Instance, InstanceType
from ringkeeper.services import instances, mcp_tools

JSON = dict[str, Any]
RUNS = "runs without asking"
LOGGED = "runs, and is in the audit log"
WAITS = "waits for your approval"
TYPED = "waits; approving needs the connection's name typed"
PER_CALL = "decided by each call"
LEVEL_RULES = {mcp.READ: RUNS, mcp.APPROVE: WAITS, mcp.CONFIRM: TYPED}


def _first_line(text: str | None) -> str:
    return next((line.strip() for line in (text or "").splitlines() if line.strip()), "")


def _tool(
    name: str,
    rule: str,
    arguments: list[str],
    required: list[str],
    about: str | None,
    read_only: bool | None = None,
    destructive: bool | None = None,
) -> JSON:
    return {
        "name": name, "read_only": read_only, "destructive": destructive, "rule": rule,
        "arguments": arguments, "required": required, "about": _first_line(about),
    }  # fmt: skip


def _mcp_tools(name: str) -> list[JSON]:
    """Asked of the server now, as its Tools list does."""
    listed = mcp_tools.refresh(name)
    found: list[JSON] = []
    for t in cast(list[JSON], listed.get("tools") or []):
        schema = cast(JSON, t.get("input_schema") or {})
        hints = cast(JSON, t.get("hints") or {})
        found.append(
            _tool(
                str(t.get("name")),
                LEVEL_RULES.get(str(t.get("rule")), WAITS),
                list(cast(JSON, schema.get("properties") or {})),
                [str(r) for r in cast(list[Any], schema.get("required") or [])],
                str(t.get("description") or ""),
                hints.get("read_only"),
                hints.get("destructive"),
            )
        )
    return found


def _builtin_rule(spec: ToolSpec, env: Env, config: JSON) -> str:  # noqa: PLR0911 (one per hold)
    """What a call to one of Ringkeeper's own tools needs on this connection."""
    if spec.classify is not None:
        return PER_CALL
    if spec.kind is ToolKind.READ:
        return RUNS
    if spec.typed is not None:
        return PER_CALL
    if spec.hold is Hold.ALWAYS:
        return WAITS
    if spec.hold is Hold.NEVER:
        return LOGGED
    if spec.hold is Hold.DRAFT:
        return WAITS if config.get("hold_drafts") else LOGGED
    return WAITS if env is Env.PROD else LOGGED  # Hold.PROD: only live data waits


def _arguments(spec: ToolSpec) -> tuple[list[str], list[str]]:
    params = list(inspect.signature(spec.handler).parameters.values())[1:]  # after the target
    taken = [p for p in params if p.kind not in (p.VAR_POSITIONAL, p.VAR_KEYWORD)]
    return [p.name for p in taken], [p.name for p in taken if p.default is p.empty]


def _builtin_tools(instance: Instance) -> list[JSON]:
    adapter = ADAPTERS.get(instance.type)
    if adapter is None:
        return []
    config = dict(instance.config or {})
    found: list[JSON] = []
    for spec in adapter.tools:
        if adapter.offers is not None and not adapter.offers(config, spec.name):
            continue
        arguments, required = _arguments(spec)
        found.append(
            _tool(
                spec.name,
                _builtin_rule(spec, instance.env, config),
                arguments,
                required,
                spec.description,
            )
        )
    return found


def _op_rule(op: Op, config: JSON) -> str:
    if op.level == READ:
        return LOGGED if op.audited else RUNS
    if op.level == DRAFT:
        return WAITS if config.get("hold_drafts") else LOGGED
    if op.level == FINAL:
        return PER_CALL if callable(op.why) else TYPED
    if op.level == HELD:
        return PER_CALL if callable(op.why) else WAITS
    return WAITS


def _vendor_tools(instance: Instance) -> list[JSON]:
    """A business system's operations, each called through call_operation."""
    found: Vendor | None = vendor.VENDORS.get(str((instance.config or {}).get("vendor") or ""))
    if found is None:
        return []
    config = dict(instance.config or {})
    rows: list[JSON] = []
    for op in found.ops:
        placed = _placeholders(op.path)
        rows.append(_tool(op.name, _op_rule(op, config), [*placed, *op.params], placed, op.says))
    return rows


def _placeholders(path: str) -> list[str]:
    return list(dict.fromkeys(PLACEHOLDER.findall(path)))


def tool_list(name: str) -> JSON:
    """Every tool the connection offers, as tasks see them. Raises ToolError (or TimeoutError)
    when an MCP server doesn't answer."""
    with session_scope(commit=False) as session:  # closed before a server is started
        instance = instances.get_instance(session, name)
        kind = instance.type
        config = dict(instance.config or {})
        if kind is InstanceType.MCP:
            tools: list[JSON] | None = None
        elif kind is InstanceType.VENDOR:
            tools = _vendor_tools(instance)
        else:
            tools = _builtin_tools(instance)
    if tools is None:
        tools = _mcp_tools(name)
    return {
        "connection": name, "kind": _kind(kind, config), "count": len(tools),
        "listed_at": datetime.now(UTC).isoformat(timespec="seconds"), "tools": tools,
    }  # fmt: skip


# --- Details for support ---


def _kind(kind: InstanceType, config: JSON | None) -> str:
    config = config or {}
    if kind is InstanceType.MCP:
        service = remote.BY_KEY.get(str(config.get("remote") or ""))
        if service is not None:
            return f"MCP server: {service.name}"
        registry = config.get("registry")
        return f"MCP server from the catalog ({registry})" if registry else "MCP server"
    if kind is InstanceType.VENDOR:
        found = vendor.VENDORS.get(str(config.get("vendor") or ""))
        return found.name if found is not None else "Business system"
    return kind.value


def _signs_in(kind: InstanceType, config: JSON) -> str:
    source = str(config.get("source") or "")
    if kind is InstanceType.MCP:
        if config.get("app"):
            return "Signed in through your own app (Settings > Developer apps)"
        return {
            "signin": "Signed in on the service's own page",
            "token": "A pasted token",
            "headers": "Headers you gave it",
            "open": "Nothing: the server needs no sign-in",
        }.get(source, "Runs on this computer" if config.get("package") or not source else source)
    if kind is InstanceType.VENDOR:
        if source == "signin":
            return "Signed in through your own app (Settings > Developer apps)"
        return "A pasted key"
    if kind is InstanceType.GOOGLE:
        return "Signed in with Google"
    return "A key or password kept in the vault"


def _ends(kind: InstanceType, config: JSON) -> str:
    if expires := config.get("expires_at"):
        return str(expires)
    if config.get("source") == "signin" or kind is InstanceType.GOOGLE:
        return "Not known: renewed while it's used, where the service allows"
    return "Not known"


def _tested(instance: Instance) -> str:
    """Whether it worked, and when. Not what it said: that can name the client's account."""
    if instance.last_test_at is None:
        return "Never tested"
    at = instance.last_test_at
    at = at if at.tzinfo else at.replace(tzinfo=UTC)
    when = at.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")
    return f"{'worked' if instance.last_test_ok else 'failed'}, {when}"


def _tool_count(instance: Instance) -> str:
    config = dict(instance.config or {})
    if instance.type is InstanceType.MCP:
        known = config.get("annotations")
        return str(len(cast(JSON, known))) if isinstance(known, dict) else "Not listed yet"
    if instance.type is InstanceType.VENDOR:
        return str(len(_vendor_tools(instance)))
    return str(len(_builtin_tools(instance)))


def support_details(session: Session, name: str) -> JSON:
    """Plain lines for support. The connection's own name is left out: it often names a client."""
    instance = instances.get_instance(session, name)
    config = dict(instance.config or {})
    settings = get_settings()
    lines = {
        "Connection kind": _kind(instance.type, config),
        "Signs in with": _signs_in(instance.type, config),
        "Sign-in ends": _ends(instance.type, config),
        "Kind of data": instance.env.value,
        "Shared tool": "yes" if instance.shared else "no",
        "Last test": _tested(instance),
        "Tools": _tool_count(instance),
        "Ringkeeper": f"{settings.app_version or __version__} ({settings.mode})",
    }
    text = "\n".join(f"{k}: {v}" for k, v in lines.items())
    return {"lines": lines, "text": text}
