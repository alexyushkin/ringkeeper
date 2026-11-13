"""ask_user: the worker's way to ask the operator something. not_done: its way to say
it couldn't do what was asked, so the task doesn't end as done.

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
NOTED = "Noted: the task will end as not done. End your turn with a one-line note of why."


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

    @tool(
        "not_done",
        "Say you couldn't do what was asked: a tool refused it, you lack the access or "
        "connection it needs, a file couldn't be read, or it can't be done. Call it whenever "
        "the main thing asked for isn't in your answer, even if you did part of it or offer "
        "ways forward. The task then ends as not done, with "
        "your reason, instead of done. `needs`: the kind of connection that was missing, if "
        "that's why: one of email, calendar, payments, ads, meta_ads, google_ads, google, slack, "
        "database, airtable, n8n, github, vercel, or empty. `done`: what you did finish, when "
        "part of it is done (it then shows as partly done); leave it out when nothing is. Still "
        "end your turn with a short note for the operator.",
        {
            "type": "object",
            "properties": {
                "reason": {"type": "string"},
                "needs": {"type": "string"},
                "done": {"type": "string"},
            },
            "required": ["reason"],
        },
    )
    async def not_done(args: dict[str, Any]) -> dict[str, Any]:
        reason = str(args.get("reason", "")).strip()[:MAX_QUESTION_CHARS]
        if not reason:
            return {"content": [{"type": "text", "text": "Say why."}], "is_error": True}
        emit(
            "ringkeeper.not_done", reason=reason, needs=str(args.get("needs") or "").strip(),
            done=str(args.get("done") or "").strip()[:MAX_QUESTION_CHARS],
        )  # fmt: skip
        return {"content": [{"type": "text", "text": NOTED}]}

    server = create_sdk_mcp_server(SERVER_NAME, tools=[ask_user, not_done])
    hooks = {"PreToolUse": [HookMatcher(matcher=None, hooks=[block_after_asking])]}
    return server, hooks
