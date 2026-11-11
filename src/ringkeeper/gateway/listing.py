"""What a worker reads about the tools, on every step of a task.

Each tool's name, help and argument schema go to the model with every turn, so what's listed
costs tokens the whole way through. Two things keep it short: a project sees only the tools
its own connections can use (one its link doesn't allow, or one its account wasn't given
access for, would only be refused), and each schema loses what says nothing (an optional
argument's `null`).
"""

from collections.abc import Callable, Sequence
from typing import Any, cast

import anyio.to_thread
import mcp.types as mt
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools.base import Tool

from ringkeeper.adapters.base import Adapter
from ringkeeper.calls import Caller
from ringkeeper.services import calls

ALWAYS = frozenset({"list_instances", "read_more"})


def _lean_one(schema: Any) -> Any:
    if isinstance(schema, list):
        return [_lean_one(item) for item in cast(list[Any], schema)]
    if not isinstance(schema, dict):
        return schema
    found: dict[str, Any] = {}
    for key, value in cast(dict[str, Any], schema).items():
        if key in ("properties", "$defs") and isinstance(value, dict):  # names, not schemas
            items = cast(dict[str, Any], value).items()
            found[key] = {name: _lean_one(inner) for name, inner in items}
        else:
            found[key] = _lean_one(value)
    options = found.get("anyOf")
    if isinstance(options, list) and len(cast(list[Any], options)) == 2:
        kept = [o for o in cast(list[Any], options) if o != {"type": "null"}]
        if len(kept) == 1 and isinstance(kept[0], dict):
            found.pop("anyOf")
            found = cast(dict[str, Any], kept[0]) | found
    if "default" in found and found["default"] is None:
        found.pop("default")
    found.pop("title", None)
    return found


def lean(schema: dict[str, Any]) -> dict[str, Any]:
    """A tool's argument schema without what costs tokens and tells the model nothing: an
    optional argument's null (leaving it out says the same), and titles. The arguments are
    still checked against the tool's own signature, which takes null as it did."""
    properties = cast(dict[str, Any], schema.get("properties") or {})
    shown = {k: _lean_one(v) for k, v in properties.items()}
    return schema | {"properties": shown} if properties else schema


class ListedTools(Middleware):
    """Lists only what the calling project's connections of this kind can use."""

    def __init__(self, adapter: Adapter, caller: Callable[[], Caller]) -> None:
        self.adapter = adapter
        self.caller = caller

    async def on_list_tools(
        self,
        context: MiddlewareContext[mt.ListToolsRequest],
        call_next: CallNext[mt.ListToolsRequest, Sequence[Tool]],
    ) -> Sequence[Tool]:
        tools = await call_next(context)
        try:
            caller = self.caller()
        except RuntimeError:  # not over HTTP: nobody to list them for
            return tools
        usable = await anyio.to_thread.run_sync(calls.usable_tools, caller, self.adapter)
        if usable is None:
            return tools
        return [t for t in tools if t.name in usable or t.name in ALWAYS]
