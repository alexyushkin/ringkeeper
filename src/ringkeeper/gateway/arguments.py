"""A tool's arguments, before and after FastMCP checks them.

A call that doesn't fit a tool's signature never reaches Ringkeeper, so this is the one place to
put its values right (ToolSpec.tidy) and to say what's wrong. Pydantic's own message is written
for a developer, and it repeats what was sent, which may be anything.

Two things make the next call right:
- The name a model would guess is taken too: `query` for a tool's search words, `id` for the
  one thing it reads, `limit` for how many, `name` for a title (ALIASES, ids). Only for a name
  the tool doesn't take itself, and only when the call doesn't also give the real one.
- A call that still doesn't fit is told what the tool takes, from its own schema: each
  argument, its type, and whether it's needed.
"""

from collections.abc import Iterable, Mapping
from typing import Any, cast

import mcp.types as mt
from fastmcp.exceptions import ToolError as McpToolError
from fastmcp.exceptions import ValidationError as McpValidationError
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools.base import ToolResult
from pydantic import ValidationError

from ringkeeper.adapters.base import ToolSpec
from ringkeeper.scrub import scrub

SAID = {
    "dict_type": "should be an object",
    "list_type": "should be a list",
    "string_type": "should be text",
    "int_parsing": "should be a whole number",
    "int_type": "should be a whole number",
    "bool_parsing": "should be true or false",
    "missing_argument": "is needed",
    "missing_keyword_only_argument": "is needed",
    "unexpected_keyword_argument": "isn't something this tool takes",
}

# A tool's own argument, and the names a model may send for it instead.
ALIASES: dict[str, tuple[str, ...]] = {
    "query": ("q", "search", "text", "words", "keywords", "term"),
    "words": ("query", "q", "search", "text", "keywords"),
    "text": ("query", "q", "search", "words"),
    "search": ("query", "q", "words", "text"),
    "sql": ("query", "statement"),
    "note": ("text", "content", "body", "fact"),
    "limit": ("max_results", "max", "count", "top", "page_size", "per_page"),
    "max_rows": ("limit", "max_results", "max"),
    "page_size": ("limit", "max_results", "per_page"),
    "title": ("name",),
}
# An argument naming the one thing a tool works on, which a model may send as `id`; not one
# naming where it is (Airtable's base, Vercel's team).
ID_LIKE = ("uid", "sid")
CONTAINERS = frozenset({"base_id", "team_id", "workspace_id", "account_id", "folder_id"})

TYPES = {
    "string": "text",
    "integer": "whole number",
    "number": "number",
    "boolean": "true or false",
    "array": "list",
    "object": "object",
}


def _type(schema: Mapping[str, Any]) -> str:
    """An argument's type in plain words: "text", "list", "text or nothing"..."""
    if "enum" in schema:
        return "one of " + ", ".join(str(v) for v in schema["enum"])
    if "const" in schema:
        return f"just {schema['const']}"
    options = schema.get("anyOf") or schema.get("oneOf")
    if options:
        named = [_type(o) for o in options if o.get("type") != "null"]
        kinds = list(dict.fromkeys(named))
        return " or ".join(kinds) if kinds else "nothing"
    kind = schema.get("type")
    if isinstance(kind, list):
        kinds = [str(k) for k in cast(list[Any], kind)]
        return " or ".join(TYPES.get(k, k) for k in kinds if k != "null")
    return TYPES.get(str(kind), "any value")


def takes(schema: Mapping[str, Any]) -> str:
    """What a tool takes, from its input schema: "query (text, needed), limit (whole number)"."""
    props = cast(Mapping[str, Mapping[str, Any]], schema.get("properties") or {})
    needed = set(cast(list[str], schema.get("required") or []))
    if not props:
        return "nothing: call it with no arguments"
    parts = [
        f"{name} ({_type(prop)}{', needed' if name in needed else ''})"
        for name, prop in props.items()
    ]
    return ", ".join(parts)


def _id_of(props: Mapping[str, Any], needed: Iterable[str]) -> str | None:
    """The argument `id` stands for: the tool's one needed id, if it has just one."""
    ids = [
        name
        for name in needed
        if name in props
        and name not in CONTAINERS
        and (name.endswith("_id") or name in ID_LIKE or name.endswith("_sid"))
    ]
    return ids[0] if len(ids) == 1 else None


def renamed(schema: Mapping[str, Any], arguments: Mapping[str, Any]) -> dict[str, Any]:
    """The call's arguments with a guessed name put under the tool's own."""
    props = cast(Mapping[str, Any], schema.get("properties") or {})
    needed = cast(list[str], schema.get("required") or [])
    given = dict(arguments)
    wanted: dict[str, tuple[str, ...]] = {own: ALIASES[own] for own in props if own in ALIASES}
    if "id" not in props and (target := _id_of(props, needed)) is not None:
        wanted[target] = ("id",)
    for own, guesses in wanted.items():
        if own in given:
            continue
        for guess in guesses:
            if guess in given and guess not in props:
                given[own] = given.pop(guess)
                break
    return given


def refusal(tool: str, error: ValidationError, schema: Mapping[str, Any] | None = None) -> str:
    """Why a call didn't fit, without what was in it, and what the tool takes."""
    found = error.errors(include_url=False, include_context=False, include_input=False)
    problems = [
        f"`{'.'.join(str(part) for part in problem['loc'])}` "
        f"{SAID.get(problem['type'], problem['msg'].lower())}"
        for problem in found
    ]
    takes_it = f" It takes: {takes(schema)}." if schema is not None else ""
    return scrub(f"{tool} wasn't run: {'; '.join(problems)}.{takes_it}")


class CheckedArguments(Middleware):
    """`specs`: the adapter's tools on this server, which can tidy a call."""

    def __init__(self, specs: Iterable[ToolSpec] = ()) -> None:
        self.specs = {spec.name: spec for spec in specs}

    async def _schema(self, context: MiddlewareContext[Any], name: str) -> Mapping[str, Any] | None:
        server = context.fastmcp_context.fastmcp if context.fastmcp_context else None
        if server is None:
            return None
        try:
            tool = await server.get_tool(name)
        except Exception:  # no such tool: FastMCP says so itself
            return None
        return tool.parameters if tool is not None else None

    async def on_call_tool(
        self,
        context: MiddlewareContext[mt.CallToolRequestParams],
        call_next: CallNext[mt.CallToolRequestParams, ToolResult],
    ) -> ToolResult:
        message = context.message
        spec = self.specs.get(message.name)
        schema = await self._schema(context, message.name)
        arguments: dict[str, Any] = dict(message.arguments or {})
        if spec is not None and spec.tidy is not None and arguments:
            arguments = spec.tidy(arguments)  # it places every name itself
        elif schema is not None and arguments:
            arguments = renamed(schema, arguments)
        if arguments != (message.arguments or {}):
            context = context.copy(message=message.model_copy(update={"arguments": arguments}))
        try:
            return await call_next(context)
        except McpValidationError as error:
            cause = error.__cause__
            if not isinstance(cause, ValidationError):
                raise
            raise McpToolError(refusal(message.name, cause, schema)) from None
