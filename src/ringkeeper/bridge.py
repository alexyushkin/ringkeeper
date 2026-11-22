"""`ringkeeper mcp --pass <name>`: one local MCP server (stdio) for an AI app with a pass.

Claude Desktop, Claude Code and Cursor start it. It reads the pass's token from Ringkeeper's
vault (so it isn't written into the app's settings) and the gateway's address from the running
app, then passes every call through to the gateway with that token. It decides nothing: the
gateway checks each call as it does a task's. Tools are named by service: database_query,
airtable_list_records, memory_recall, ringkeeper_check_approval.

It exits when Ringkeeper quits, so it never holds the install folder while an update replaces
it. Claude Desktop's relay (desktop/relay.js) starts it again once Ringkeeper is back.
"""

import asyncio
import json
import os
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

from fastmcp import Client, FastMCP
from fastmcp.client.transports import StreamableHttpTransport
from fastmcp.server import create_proxy

from ringkeeper import __version__

ALWAYS = ("memory", "ringkeeper")  # besides each kind of connection the project has
LISTS_ALL = "database"  # any service endpoint's list_instances names all of the project's
NOT_RUNNING = "Ringkeeper isn't running. Start it, then restart this app."
STOPPED = 75  # Ringkeeper quit or restarted: the relay starts the bridge again when it's back
WATCH_SECONDS = 2.0
MISSES = 2  # checks in a row that find it gone


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


def still_running(data_dir: Path, gateway: str) -> bool:
    """The app that wrote desktop.json is still the one this bridge talks to, and answers."""
    try:
        info = cast(dict[str, Any], json.loads((data_dir / "desktop.json").read_text("utf-8")))
    except (OSError, ValueError):
        return False  # removed when Ringkeeper quits
    if info.get("gateway") != gateway:
        return False  # it restarted, on another port
    import httpx2  # noqa: PLC0415

    try:
        return httpx2.get(f"{gateway.rstrip('/')}/health", timeout=2).status_code == 200
    except httpx2.HTTPError:
        return False


def watch(
    data_dir: Path,
    gateway: str,
    *,
    seconds: float = WATCH_SECONDS,
    leave: Callable[[int], object] = os._exit,  # the stdio server would block a plain exit
) -> None:
    """Leave once Ringkeeper has gone: an update can't replace files a program is running."""
    misses = 0
    while True:
        time.sleep(seconds)
        misses = 0 if still_running(data_dir, gateway) else misses + 1
        if misses >= MISSES:
            print("Ringkeeper: Ringkeeper stopped, so this bridge did too.", file=sys.stderr)
            leave(STOPPED)
            return


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


async def about(gateway: str, token: str) -> dict[str, Any]:
    """Whose connections these are (client, project), or nothing from a Ringkeeper too old to
    say: the app is then told the rule without the client's name."""
    try:
        async with Client(_transport(gateway, token, "ringkeeper")) as client:
            found = await client.call_tool("about", {})
    except Exception:  # an older Ringkeeper has no about
        return {}
    return dict(found.structured_content or {})


def instructions(found: dict[str, Any]) -> str:
    """What the AI app is told when it connects: whose connections these are, to use them and
    nothing else for that client's data, and how holds and requests work."""
    client, title = found.get("client"), found.get("project_title")
    whose = (
        f"{client}'s connections, for the project {title}"
        if client and title
        else ("One Ringkeeper project's connections")
    )
    theirs = f"{client}'s" if client else "this client's"
    return (
        f"{whose}, through Ringkeeper. Use these tools, and only these, for {theirs} data. "
        "Never use another connector, integration or built-in tool for it, even one for the "
        "same service (an Airtable, Notion, Google or database connector of your own, say): "
        f"those reach other accounts, not {theirs}, and would read or change the wrong one. "
        "Every call is checked by Ringkeeper. Changes to production wait for the user's "
        "approval in Ringkeeper; ringkeeper_check_approval says what became of one. "
        "ringkeeper_ask hands Ringkeeper a whole request to do as a task in this project; "
        "ringkeeper_task_status follows it."
    )


def build(
    gateway: str, token: str, kinds: list[str], name: str, found: dict[str, Any] | None = None
) -> FastMCP:
    server: FastMCP = FastMCP(
        name=f"ringkeeper-{name}", version=__version__, instructions=instructions(found or {})
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
    found = asyncio.run(about(address, token))
    if gateway is None:  # the desktop app's: follow it
        threading.Thread(
            target=watch, args=(data_dir, address), daemon=True, name="rk-watch"
        ).start()
    build(address, token, kinds, name, found).run(show_banner=False)
