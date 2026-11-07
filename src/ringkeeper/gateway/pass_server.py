"""/mcp/ringkeeper: what an AI app with a pass has besides the project's connections.

A task is resumed with what happened to its held calls. An app using a pass isn't, so it asks
here. It sees only its own held calls: the decision must be its pass's and its project's.
"""

from collections.abc import Callable
from typing import Annotated, Any

import anyio.to_thread
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError as McpToolError
from mcp_types import ToolAnnotations
from pydantic import Field

from ringkeeper import __version__
from ringkeeper.calls import Caller
from ringkeeper.db import session_scope
from ringkeeper.errors import RingkeeperError
from ringkeeper.gateway.arguments import CheckedArguments
from ringkeeper.models import DecisionStatus
from ringkeeper.scrub import scrub
from ringkeeper.services import decisions, holds, passes

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
    with session_scope(commit=False) as session:
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
        if decision.status is DecisionStatus.EXECUTED:
            found["result"] = scrub((decision.result or "")[:RESULT_CHARS])
        if decision.status is DecisionStatus.FAILED:
            found["error"] = scrub(decision.error or "")
        if decision.status is DecisionStatus.DENIED and decision.reason:
            found["reason"] = scrub(decision.reason)
        return found


def build_pass_server(current_caller: Callable[[], Caller]) -> FastMCP:
    server = FastMCP(
        name="ringkeeper",
        version=__version__,
        instructions=(
            "You're using one Ringkeeper project's connections through a pass. Changes to "
            "production are held for the user's approval in Ringkeeper; check_approval says "
            "what became of one."
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

    reads = ToolAnnotations(read_only_hint=True, destructive_hint=False)
    server.tool(check_approval, name="check_approval", annotations=reads)
    return server
