"""Run one Claude Agent SDK session and stream every message as a JSON line."""

from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient
from claude_agent_sdk.types import McpHttpServerConfig, McpServerConfig

from ringkeeper_worker.config import WorkerConfig
from ringkeeper_worker.emit import emit, message_type


def build_options(config: WorkerConfig) -> ClaudeAgentOptions:
    servers: dict[str, McpServerConfig] = {
        name: McpHttpServerConfig(
            type="http", url=url, headers={"Authorization": f"Bearer {config.gateway_token}"}
        )
        for name, url in config.mcp_servers.items()
    }
    return ClaudeAgentOptions(
        cwd=config.workspace,
        # The gateway endpoints are the only MCP servers, and no settings files
        # from anywhere (user, project or local) are loaded.
        mcp_servers=servers,  # pyright: ignore[reportArgumentType] (dict invariance)
        strict_mcp_config=True,
        setting_sources=[],
        max_turns=config.max_turns,
        max_budget_usd=config.max_budget_usd,
        resume=config.resume,
        # The container is the sandbox; the project's autonomy decides which tools exist.
        permission_mode="bypassPermissions",
        disallowed_tools=config.disallowed_tools,
        stderr=lambda line: emit("sdk.stderr", line=line),
    )


async def run(config: WorkerConfig) -> bool:
    """Returns True if the SDK reported success."""
    ok = False
    async with ClaudeSDKClient(options=build_options(config)) as client:
        await client.query(config.prompt)
        async for message in client.receive_response():
            kind = message_type(message)
            emit(kind, message=message)
            if kind == "sdk.result":
                ok = not getattr(message, "is_error", True)
    return ok
