"""/mcp/architect: the architect's loop tools. Only architect tokens get in.

Each tool records one step on the caller's own architect session (the token's
session id), checked against the task's phase; the session manager carries
it out when the architect ends its turn.
"""

from collections.abc import Callable
from typing import Annotated, Any

import anyio.to_thread
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError as McpToolError
from mcp_types import ToolAnnotations
from pydantic import Field
from sqlalchemy.orm import Session

from ringkeeper import __version__
from ringkeeper.calls import Caller
from ringkeeper.db import session_scope
from ringkeeper.errors import RingkeeperError
from ringkeeper.scrub import scrub
from ringkeeper.services import architect
from ringkeeper.services.calls import bound_instances

Text = Annotated[str, Field(min_length=1)]


def build_architect_server(current_caller: Callable[[], Caller]) -> FastMCP:
    server = FastMCP(
        name="ringkeeper-architect",
        version=__version__,
        instructions=(
            "Ringkeeper's architect loop for one feature task: plan, dispatch a worker, "
            "review its turns. Each tool call ends your turn."
        ),
        mask_error_details=True,
    )

    async def step(fn: Callable[[Session], str]) -> dict[str, Any]:
        def work() -> str:
            with session_scope() as session:
                return fn(session)

        try:
            return {"status": "recorded", "message": await anyio.to_thread.run_sync(work)}
        except RingkeeperError as error:
            raise McpToolError(scrub(str(error))) from None

    async def submit_plan(title: Text, plan: Text, worker_prompt: Text) -> dict[str, Any]:
        """Submit your plan for the operator's approval. `title`: one line. `plan`: what
        changes, where, and how it is tested. `worker_prompt`: complete instructions for
        the worker. Ends your turn."""
        caller = current_caller()
        return await step(
            lambda s: architect.submit_plan(
                s, caller, title=title, plan=plan, worker_prompt=worker_prompt
            )
        )

    async def dispatch_worker(prompt: Text) -> dict[str, Any]:
        """After the plan is approved: start the worker with this prompt. Ends your turn;
        you are resumed with the checks output and the diff."""
        caller = current_caller()
        return await step(lambda s: architect.dispatch_worker(s, caller, prompt=prompt))

    async def continue_worker(feedback: Text) -> dict[str, Any]:
        """Send the worker back with concrete feedback (same worker session). Ends your
        turn. After the last allowed review this asks the operator instead."""
        caller = current_caller()
        return await step(lambda s: architect.continue_worker(s, caller, feedback=feedback))

    async def approve_work(summary: Text) -> dict[str, Any]:
        """Approve the worker's branch (only when the checks pass), with a short summary
        for the operator. Ends your turn and finishes the task."""
        caller = current_caller()
        return await step(lambda s: architect.approve_work(s, caller, summary=summary))

    async def list_instances() -> dict[str, Any]:
        """List the instances your project uses (name, type, env), for context."""
        try:
            instances = await anyio.to_thread.run_sync(bound_instances, current_caller())
        except RingkeeperError as error:
            raise McpToolError(scrub(str(error))) from None
        return {"instances": instances}

    loop_hint = ToolAnnotations(read_only_hint=False, destructive_hint=False)
    for fn in (submit_plan, dispatch_worker, continue_worker, approve_work):
        server.tool(fn, name=fn.__name__, annotations=loop_hint)
    server.tool(
        list_instances,
        name="list_instances",
        annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False),
    )
    return server
