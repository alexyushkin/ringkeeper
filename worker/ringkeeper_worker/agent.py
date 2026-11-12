"""Run one Claude Agent SDK session and stream every message as a JSON line."""

from pathlib import Path

from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient
from claude_agent_sdk.types import McpHttpServerConfig, McpServerConfig

from ringkeeper_worker import inbox
from ringkeeper_worker.config import WorkerConfig
from ringkeeper_worker.edits import EDITS
from ringkeeper_worker.emit import emit, message_type
from ringkeeper_worker.privileges import is_root

# Appended to Claude Code's own system prompt. Nobody reads a worker's final
# message as it happens, so a question asked in prose would go unanswered.
HEADLESS_NOTE = """\
You are running unattended inside Ringkeeper. {workspace} is the root of your
project; there is no other project directory. No one reads your messages while
you work: if you need anything from the operator (a choice, a missing detail,
confirmation), call the ask_user tool. Don't ask questions in your reply text.
Your final message is what the operator reads: when you're asked to tell, report
or reply with something, put it there. That isn't a request to send an email or
post a message anywhere, unless the task says to send one.
Check your answer before you give it, and give it once: no correcting it mid-message.
If you couldn't do what was asked (a tool refused it, you lack the access or the
connection, or it can't be done), call the not_done tool with why before you end.
Never end as if it were done. The main thing asked for decides: if it isn't in your
answer (you couldn't read the file, find the numbers, or make the change), call
not_done, even when you did part of it or offer ways forward. If the work needs the
web and you have no web tools, say plainly that web access is off for this
project's tasks.
Some tool calls are held for the operator's approval. They already see those,
so never ask about a held call: end your turn as its result says, and you'll be
resumed with what happened.
A tool result can be only part of what's there. When one says "truncated": true,
or has a next_offset, next_page or continuation, fetch the rest before you answer
(offset=next_offset, or read_more with the continuation). If you can't or it isn't
needed, say plainly that your answer is based on part of it. Never summarize part
of something as if it were the whole. A write isn't repeated to see more of its
result. A long file starts with its outline: read the pages you need with pages=
(next_pages), and look at a page with a table, a chart or a scan with the view
tool (view_attachment, view_file)."""


def build_options(
    config: WorkerConfig, ask: inbox.AskState | None = None, cli: Path | None = None
) -> ClaudeAgentOptions:
    servers: dict[str, McpServerConfig] = {
        name: McpHttpServerConfig(
            type="http", url=url, headers={"Authorization": f"Bearer {config.gateway_token}"}
        )
        for name, url in config.mcp_servers.items()
    }
    ask_server, hooks = inbox.build(ask or inbox.AskState())
    servers[inbox.SERVER_NAME] = ask_server
    return ClaudeAgentOptions(
        cwd=config.workspace,
        system_prompt={
            "type": "preset",
            "preset": "claude_code",
            "append": HEADLESS_NOTE.format(workspace=config.workspace),
        },
        # The gateway endpoints are the only MCP servers, and no settings files
        # from anywhere (user, project or local) are loaded.
        mcp_servers=servers,  # pyright: ignore[reportArgumentType] (dict invariance)
        strict_mcp_config=True,
        setting_sources=[],
        max_turns=config.max_turns,
        max_budget_usd=config.max_budget_usd,
        resume=config.resume,
        model=config.model,
        effort=config.effort,  # pyright: ignore[reportArgumentType] (checked by the manager)
        hooks=hooks,  # pyright: ignore[reportArgumentType] (dict invariance)
        # The container is the sandbox; the project's autonomy decides which tools exist.
        permission_mode="bypassPermissions",
        disallowed_tools=config.disallowed_tools,
        stderr=lambda line: emit("sdk.stderr", line=line),
        # The agent runs as the worker user, never as the runner (root).
        user="worker" if is_root() else None,
        cli_path=cli,  # None: the SDK's own (see claude_code.py)
    )


async def run(config: WorkerConfig, cli: Path | None = None) -> bool:
    """Returns True if the SDK reported success."""
    ok = False
    ask = inbox.AskState()
    async with ClaudeSDKClient(options=build_options(config, ask, cli)) as client:
        await client.query(config.prompt)
        async for message in client.receive_response():
            kind = message_type(message)
            emit(kind, message=message)
            EDITS.message(message)  # the files it edits itself, for the commit
            if kind == "sdk.result":
                ok = not getattr(message, "is_error", True)
    return ok
