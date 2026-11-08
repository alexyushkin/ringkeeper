"""/mcp/ringkeeper: what an AI app with a pass has besides the project's connections.

A task is resumed with what happened to its held calls. An app using a pass isn't, so it asks
here. It sees only its own held calls: the decision must be its pass's and its project's.
It can also hand Ringkeeper a request (ask): a task in its project, which it can follow.
"""

import time
from collections.abc import Callable
from typing import Annotated, Any

import anyio.to_thread
from fastmcp import Context, FastMCP
from fastmcp.exceptions import ToolError as McpToolError
from mcp_types import ToolAnnotations
from pydantic import Field

from ringkeeper import __version__
from ringkeeper.calls import Caller
from ringkeeper.config import get_settings
from ringkeeper.db import session_scope
from ringkeeper.errors import RingkeeperError
from ringkeeper.gateway.arguments import CheckedArguments
from ringkeeper.models import DecisionStatus
from ringkeeper.scrub import scrub
from ringkeeper.services import decisions, holds, pass_tasks, passes

RESULT_CHARS = 4000
NOT_YOURS = "There's no held call with that id for this pass."
SAYS = {
    DecisionStatus.PENDING: "It's still waiting for the user's approval in Ringkeeper.",
    DecisionStatus.APPROVED: "The user approved it, and it's running now.",
    DecisionStatus.EXECUTED: "The user approved it, and it ran.",
    DecisionStatus.FAILED: "The user approved it, but it failed.",
    DecisionStatus.DENIED: "The user said no. Don't try it again.",
    DecisionStatus.EXPIRED: "No one decided in time, so it didn't run.",
}


def approval(caller: Caller, decision_id: str) -> dict[str, Any]:
    if not passes.is_pass_session(caller.session_id):
        raise RingkeeperError("only a pass can check its held calls here")
    with session_scope() as session:  # the first link makes the Open links' key
        try:
            decision = decisions.get_decision(session, decision_id)
        except RingkeeperError:
            raise RingkeeperError(NOT_YOURS) from None
        if decision.session_id != caller.session_id or decision.project != caller.project:
            raise RingkeeperError(NOT_YOURS)
        found: dict[str, Any] = {
            "status": decision.status.value,
            "call": holds.label(decision),
            "says": SAYS.get(decision.status, decision.status.value),
        }
        if decision.status is DecisionStatus.PENDING:  # where to decide, again
            found |= passes.approval_link(session, get_settings())
        if decision.status is DecisionStatus.EXECUTED:
            found["result"] = scrub((decision.result or "")[:RESULT_CHARS])
        if decision.status is DecisionStatus.FAILED:
            found["error"] = scrub(decision.error or "")
        if decision.status is DecisionStatus.DENIED and decision.reason:
            found["reason"] = scrub(decision.reason)
        return found


def asked(caller: Caller, request: str) -> str:
    with session_scope() as session:
        return str(pass_tasks.start(session, get_settings(), caller, request).id)


def task_status(caller: Caller, task_id: str) -> dict[str, Any]:
    with session_scope() as session:  # the first link makes the Open links' key
        return pass_tasks.status(session, get_settings(), caller, task_id)


async def ask_and_wait(caller: Caller, request: str, ctx: Context | None = None) -> dict[str, Any]:
    """Start the task and wait for it, up to WAIT_SECONDS: its result once it's done, where to
    decide once it waits on the user, or that it's still running."""
    task_id = await anyio.to_thread.run_sync(asked, caller, request)
    started = time.monotonic()
    while True:
        found = await anyio.to_thread.run_sync(task_status, caller, task_id)
        waited = time.monotonic() - started
        if found["status"] != "running":
            return found
        if waited >= pass_tasks.WAIT_SECONDS:
            return {**found, "says": pass_tasks.STILL_RUNNING}
        if ctx is not None:  # so the app knows it's still coming
            await ctx.report_progress(waited, pass_tasks.WAIT_SECONDS, "Ringkeeper is on it")
        await anyio.sleep(pass_tasks.CHECK_SECONDS)


def build_pass_server(current_caller: Callable[[], Caller]) -> FastMCP:
    server = FastMCP(
        name="ringkeeper",
        version=__version__,
        instructions=(
            "You're using one Ringkeeper project's connections through a pass. Changes to "
            "production are held for the user's approval in Ringkeeper; check_approval says "
            "what became of one. ask hands Ringkeeper a whole request to do as a task in this "
            "project; task_status follows it."
        ),
        mask_error_details=True,
    )
    server.add_middleware(CheckedArguments())

    async def check_approval(
        decision_id: Annotated[
            str, Field(description="The decision_id a held call returned.", max_length=64)
        ],
    ) -> dict[str, Any]:
        """What became of a call that was held for the user's approval: still waiting,
        approved and run (with its result), failed, denied, or expired."""
        try:
            return await anyio.to_thread.run_sync(approval, current_caller(), decision_id)
        except RingkeeperError as error:
            raise McpToolError(scrub(str(error))) from None

    async def ask(
        request: Annotated[
            str,
            Field(
                description="What Ringkeeper should do, in plain words, as the user would type it.",
                min_length=1,
                max_length=20_000,
            ),
        ],
        ctx: Context,
    ) -> dict[str, Any]:
        """Hand Ringkeeper a request to do as a task in this project, with its usual approvals,
        limits and audit log. Waits up to 2 minutes: returns the result if it's done, the
        approval link if it waits on the user, or the task_id to check later with task_status.
        The user sees the request and the task in Ringkeeper."""
        try:
            return await ask_and_wait(current_caller(), request, ctx)
        except RingkeeperError as error:
            raise McpToolError(scrub(str(error))) from None

    async def task_status_tool(
        task_id: Annotated[
            str, Field(description="The task_id ask returned.", min_length=1, max_length=64)
        ],
    ) -> dict[str, Any]:
        """Where a task started with ask stands: running, waiting for the user's approval (with
        the link), asking the user something, done (with its result), or failed."""
        try:
            return await anyio.to_thread.run_sync(task_status, current_caller(), task_id)
        except RingkeeperError as error:
            raise McpToolError(scrub(str(error))) from None

    reads = ToolAnnotations(read_only_hint=True, destructive_hint=False)
    server.tool(check_approval, name="check_approval", annotations=reads)
    starts = ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=True)
    server.tool(ask, name="ask", annotations=starts)
    server.tool(task_status_tool, name="task_status", annotations=reads)
    return server
