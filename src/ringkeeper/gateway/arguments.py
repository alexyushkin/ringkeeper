"""A tool's arguments, before and after FastMCP checks them.

A call that doesn't fit a tool's signature never reaches Ringkeeper, so this is the one place to
put its values right (ToolSpec.tidy) and to say what's wrong. Pydantic's own message is written
for a developer, and it repeats what was sent, which may be anything.
"""

import inspect
from collections.abc import Iterable
from typing import Any

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


def refusal(tool: str, error: ValidationError, takes: list[str] | None = None) -> str:
    """Why a call didn't fit, without what was in it."""
    found = error.errors(include_url=False, include_context=False, include_input=False)
    problems = [
        f"`{'.'.join(str(part) for part in problem['loc'])}` "
        f"{SAID.get(problem['type'], problem['msg'].lower())}"
        for problem in found
    ]
    takes_it = f" It takes: {', '.join(takes)}." if takes else ""
    return scrub(f"{tool} wasn't run: {'; '.join(problems)}.{takes_it}")


class CheckedArguments(Middleware):
    """`specs`: the adapter's tools on this server, which can tidy a call and say what they take."""

    def __init__(self, specs: Iterable[ToolSpec] = ()) -> None:
        self.specs = {spec.name: spec for spec in specs}

    async def on_call_tool(
        self,
        context: MiddlewareContext[mt.CallToolRequestParams],
        call_next: CallNext[mt.CallToolRequestParams, ToolResult],
    ) -> ToolResult:
        message = context.message
        spec = self.specs.get(message.name)
        if spec is not None and spec.tidy is not None and message.arguments:
            tidied: dict[str, Any] = spec.tidy(message.arguments)
            context = context.copy(message=message.model_copy(update={"arguments": tidied}))
        try:
            return await call_next(context)
        except McpValidationError as error:
            cause = error.__cause__
            if not isinstance(cause, ValidationError):
                raise
            takes = None
            if spec is not None:
                takes = [*list(inspect.signature(spec.handler).parameters)[1:], "instance"]
            raise McpToolError(refusal(message.name, cause, takes)) from None
