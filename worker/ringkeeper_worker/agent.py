"""Run one Claude Agent SDK session and stream every message as a JSON line."""

from datetime import datetime
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
# Nothing runs after the session ends, so nothing may run in the background: Claude Code's
# own switch turns off background shells and agents.
NO_BACKGROUND = {"CLAUDE_CODE_DISABLE_BACKGROUND_TASKS": "1"}
HEADLESS_NOTE = """\
You are running unattended inside Ringkeeper. {workspace} is the root of your
project; there is no other project directory. No one reads your messages while
you work: if you need anything from the operator (a choice, a missing detail,
confirmation), call the ask_user tool. Don't ask questions in your reply text.
Your final message is what the operator reads: when you're asked to tell, report
or reply with something, put it there. That isn't a request to send an email or
post a message anywhere, unless the task says to send one.
Answer what was asked, at the size it was asked: "list the tables" is their names, not
every column of each. Don't fetch details nobody asked for; offer them in one line at the
end ("I can list each table's columns.").
Check your answer before you give it, and give it once: no correcting it mid-message.
Say nothing about memory or notes in your final message unless you saved one: no "I saved
no notes" or "nothing was saved to memory".
Finish all the work before your final message: never leave part of it to a
background task or agent, and never end saying something is still being fetched.
Nothing runs after you end. If you can't finish it, call not_done.
If you couldn't do what was asked (a tool refused it, you lack the access or the
connection, or it can't be done), call the not_done tool with your reason before you end,
and with done saying what you did finish, if part of it is done.
A connection is reached only through its own gateway endpoint. When its tools can't do
something, stop there and say so: don't try the same connection through another endpoint
(ringkeeper-mcp, ringkeeper-http or any other); they refuse it too.
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

# A task that doesn't work on the project's code (data, ops, a general question) gets this
# instead of Claude Code's own system prompt, which is mostly about editing code.
SHORT_PROMPT = """\
You are an agent doing one task for the operator through Ringkeeper. Your tools are
mostly the project's connections, through Ringkeeper's gateway: use a connection's own
tools for its data, and files or the shell only when the task needs them. Today is
{today}. Answer plainly and briefly: the answer, what you changed, or what's left."""


def system_prompt(config: WorkerConfig) -> str | dict[str, str]:
    """Claude Code's own prompt for work on the code; a short one for everything else."""
    note = HEADLESS_NOTE.format(workspace=config.workspace)
    if config.git:
        return {"type": "preset", "preset": "claude_code", "append": note}
    today = datetime.now().astimezone().strftime("%A, %Y-%m-%d")
    return SHORT_PROMPT.format(today=today) + "\n\n" + note


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
        system_prompt=system_prompt(config),  # pyright: ignore[reportArgumentType] (TypedDict)
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
        env=NO_BACKGROUND,
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
