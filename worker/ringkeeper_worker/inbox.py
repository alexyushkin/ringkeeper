"""ask_user: the worker's way to ask the operator something.

The tool records the question and tells the agent to end its turn; a
PreToolUse hook then refuses every further tool call, so the session ends
right after asking. The session manager turns the question into a decision
(status needs_input), and answering it resumes this SDK session.
"""

from dataclasses import dataclass
from typing import Any

from claude_agent_sdk import HookContext, HookMatcher, create_sdk_mcp_server, tool
from claude_agent_sdk.types import HookInput, HookJSONOutput, McpSdkServerConfig

from ringkeeper_worker.emit import emit

SERVER_NAME = "ringkeeper-inbox"
TOOL_NAME = f"mcp__{SERVER_NAME}__ask_user"
MAX_QUESTION_CHARS = 2000
ASKED = (
    "Your question was sent to the operator. End your turn now with a one-line summary "
    "of where you are; you'll be resumed in this same session with the answer."
)
BLOCKED = "You asked the operator a question; don't use more tools. End your turn now."


@dataclass
class AskState:
    question: str | None = None


def build(state: AskState) -> tuple[McpSdkServerConfig, dict[str, list[HookMatcher]]]:
    @tool(
        "ask_user",
        "Ask the operator a question you can't answer from the workspace or your tools "
        "(a missing requirement, a choice only they can make). Your session ends after "
        "asking and resumes with their answer. Ask one clear question.",
        {"question": str},
    )
    async def ask_user(args: dict[str, Any]) -> dict[str, Any]:
        question = str(args.get("question", "")).strip()[:MAX_QUESTION_CHARS]
        if not question:
            return {
                "content": [{"type": "text", "text": "The question is empty."}],
                "is_error": True,
            }
        if state.question is None:
            state.question = question
            emit("ringkeeper.ask_user", question=question)
        return {"content": [{"type": "text", "text": ASKED}]}

    async def block_after_asking(
        _input: HookInput, _tool_use_id: str | None, _context: HookContext
    ) -> HookJSONOutput:
        if state.question is None:
            return {}
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": BLOCKED,
            }
        }

    server = create_sdk_mcp_server(SERVER_NAME, tools=[ask_user])
    hooks = {"PreToolUse": [HookMatcher(matcher=None, hooks=[block_after_asking])]}
    return server, hooks
