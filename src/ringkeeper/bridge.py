"""`ringkeeper mcp --pass <name>`: one local MCP server (stdio) for an AI app with a pass.

Claude Desktop, Claude Code and Cursor start it. It reads the pass's token from Ringkeeper's
vault (so it isn't written into the app's settings) and the gateway's address from the running
app, then passes every call through to the gateway with that token. It decides nothing: the
gateway checks each call as it does a task's. Tools are named by service: database_query,
airtable_list_records, memory_recall, ringkeeper_check_approval.
"""

import asyncio
import json
from pathlib import Path
from typing import Any, cast

from fastmcp import Client, FastMCP
from fastmcp.client.transports import StreamableHttpTransport
from fastmcp.server import create_proxy

from ringkeeper import __version__

ALWAYS = ("memory", "ringkeeper")  # besides each kind of connection the project has
LISTS_ALL = "database"  # any service endpoint's list_instances names all of the project's
NOT_RUNNING = "Ringkeeper isn't running. Start it, then restart this app."


class BridgeError(Exception):
    """Said to the AI app's log, in plain words."""


def gateway_of(data_dir: Path) -> str:
    """The running desktop app's gateway, from the file it writes when it starts."""
    try:
        info = cast(dict[str, Any], json.loads((data_dir / "desktop.json").read_text("utf-8")))
    except (OSError, ValueError):
        raise BridgeError(NOT_RUNNING) from None
    gateway = info.get("gateway")
    if not isinstance(gateway, str):
        raise BridgeError(f"{NOT_RUNNING} (It may be an older version: update it.)")
    return gateway


def _transport(gateway: str, token: str, service: str) -> StreamableHttpTransport:
    return StreamableHttpTransport(
        f"{gateway.rstrip('/')}/mcp/{service}", headers={"Authorization": f"Bearer {token}"}
    )


async def services(gateway: str, token: str) -> list[str]:
    """The endpoints this pass needs: one per kind of connection its project uses."""
    try:
        async with Client(_transport(gateway, token, LISTS_ALL)) as client:
            listed = await client.call_tool("list_instances", {})
    except Exception as error:  # an expired or revoked pass, or the app stopped
        raise BridgeError(f"Ringkeeper refused this pass, or isn't running: {error}") from None
    found: dict[str, Any] = listed.structured_content or {}
    kinds = {str(i["type"]) for i in found.get("instances", []) if i.get("type")}
    return sorted(kinds) + list(ALWAYS)


def build(gateway: str, token: str, kinds: list[str], name: str) -> FastMCP:
    server: FastMCP = FastMCP(
        name=f"ringkeeper-{name}",
        version=__version__,
        instructions=(
            "One Ringkeeper project's connections. Every call is checked by Ringkeeper. "
            "Changes to production wait for the user's approval in Ringkeeper; "
            "ringkeeper_check_approval says what became of one."
        ),
    )
    for kind in kinds:
        server.mount(create_proxy(_transport(gateway, token, kind)), namespace=kind)
    return server


def run(name: str, data_dir: Path, gateway: str | None = None) -> None:
    from ringkeeper.config import get_settings  # noqa: PLC0415 (after the caller's environment)
    from ringkeeper.keys import load_master_key  # noqa: PLC0415
    from ringkeeper.services import passes  # noqa: PLC0415
    from ringkeeper.vault import VaultError, get_vault  # noqa: PLC0415

    address = gateway or gateway_of(data_dir)
    settings = get_settings()
    load_master_key(settings, create=False)
    try:
        token = get_vault(settings).resolve(passes.ref(name)).reveal()
    except VaultError:
        raise BridgeError(
            f"There's no pass named {name} in Ringkeeper. Check its name in Settings > Passes."
        ) from None
    kinds = asyncio.run(services(address, token))
    build(address, token, kinds, name).run(show_banner=False)
