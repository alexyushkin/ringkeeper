"""`ringkeeper mcp --pass <name>`: one local MCP server (stdio) for an AI app with a pass.

Claude Desktop, Claude Code and Cursor start it. It reads the pass's token from Ringkeeper's
vault (so it isn't written into the app's settings) and the gateway's address from the running
app, then passes every call through to the gateway with that token. It decides nothing: the
gateway checks each call as it does a task's. Tools are named by service: database_query,
airtable_list_records, memory_recall, ringkeeper_check_approval.

It exits when Ringkeeper quits, so it never holds the install folder while an update replaces
it. Claude Desktop's relay (desktop/relay.js) starts it again once Ringkeeper is back.

A pass for every client has one project at a time. The AI lists them
(ringkeeper_list_clients) and chooses one (ringkeeper_use_client); every call then names that
project in a header, and the gateway checks it as that project's. Until one is chosen, nothing
but the list answers. The choice holds for every chat in the app, until it's changed or the
app restarts.
"""

import asyncio
import contextlib
import json
import os
import sys
import threading
import time
from collections.abc import AsyncGenerator, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, cast

from fastmcp import Client, FastMCP
from fastmcp.client.transports import StreamableHttpTransport
from fastmcp.exceptions import ToolError
from fastmcp.server import create_proxy
from mcp_types import ToolAnnotations
from pydantic import Field

from ringkeeper import __version__

ALWAYS = ("memory", "ringkeeper")  # besides each kind of connection the project has
LISTS_ALL = "database"  # any service endpoint's list_instances names all of the project's
NOT_RUNNING = "Ringkeeper isn't running. Start it, then restart this app."
PROJECT_HEADER = "x-ringkeeper-project"  # services.passes.PROJECT_HEADER
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


@dataclass
class Choice:
    """The project a pass for every client is using now (None: not chosen yet)."""

    project: str | None = None


class _Chosen(StreamableHttpTransport):
    """A gateway endpoint for a pass for every client: each new session names the project
    chosen at that moment."""

    def __init__(self, url: str, token: str, choice: Choice) -> None:
        super().__init__(url, headers={"Authorization": f"Bearer {token}"})
        self.choice = choice

    @contextlib.asynccontextmanager
    async def connect_session(self, **kwargs: Any) -> AsyncGenerator[Any]:
        if self.choice.project:
            self.headers[PROJECT_HEADER] = self.choice.project
        else:
            self.headers.pop(PROJECT_HEADER, None)
        async with super().connect_session(**kwargs) as session:
            yield session


def _for(gateway: str, token: str, service: str, project: str | None) -> StreamableHttpTransport:
    return _Chosen(f"{gateway.rstrip('/')}/mcp/{service}", token, Choice(project))


async def services(gateway: str, token: str, project: str | None = None) -> list[str]:
    """The endpoints this pass needs: one per kind of connection its project uses.
    `project`: the one a pass for every client asks about."""
    transport = _for(gateway, token, LISTS_ALL, project) if project else None
    try:
        async with Client(transport or _transport(gateway, token, LISTS_ALL)) as client:
            listed = await client.call_tool("list_instances", {})
    except Exception as error:  # an expired or revoked pass, or the app stopped
        raise BridgeError(f"Ringkeeper refused this pass, or isn't running: {error}") from None
    found: dict[str, Any] = listed.structured_content or {}
    kinds = {str(i["type"]) for i in found.get("instances", []) if i.get("type")}
    return sorted(kinds) + list(ALWAYS)


async def about(
    gateway: str, token: str, project: str | None = None, *, switched: bool = False
) -> dict[str, Any]:
    """Whose connections these are (client, project), or nothing from a Ringkeeper too old to
    say: the app is then told the rule without the client's name. A pass for every client
    gets the projects to choose from; `project` and `switched` choose one (and say so in
    Activity)."""
    transport = _for(gateway, token, "ringkeeper", project) if project else None
    arguments = {"switched": True} if switched else {}
    try:
        async with Client(transport or _transport(gateway, token, "ringkeeper")) as client:
            found = await client.call_tool("about", arguments)
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


EVERY = (
    "Connections for every client, one client at a time, through Ringkeeper. Before you use "
    "any tool for a client's data, call ringkeeper_list_clients, then ringkeeper_use_client "
    "with that client: only the chosen client's connections answer, and the choice holds for "
    "every chat in this app until it's changed. Use these tools, and only these, for a "
    "client's data. Never use another connector, integration or built-in tool for it, even "
    "one for the same service (an Airtable, Notion, Google or database connector of your own, "
    "say): those reach other accounts, not the client's, and would read or change the wrong "
    "one. Every call is checked by Ringkeeper. Changes to production wait for the user's "
    "approval in Ringkeeper; ringkeeper_check_approval says what became of one. ringkeeper_ask "
    "hands Ringkeeper a whole request to do as a task in the chosen client's project; "
    "ringkeeper_task_status follows it."
)


def pick(listed: list[dict[str, Any]], wanted: str) -> dict[str, Any]:
    """The project `wanted` names: a project's name or title, or a client with one project.
    Raises BridgeError with what to say when it names none, or a client with several."""
    said = wanted.strip().casefold()
    for row in listed:
        if said in {str(row["project"]).casefold(), str(row["project_title"]).casefold()}:
            return row
    theirs = [
        row
        for row in listed
        if said in {str(row["client"]).strip().casefold(), str(row.get("client_id")).casefold()}
    ]
    if len(theirs) == 1:
        return theirs[0]
    if theirs:
        names = ", ".join(f"{r['project']} ({r['project_title']})" for r in theirs)
        raise BridgeError(f"{theirs[0]['client']} has several projects: {names}. Choose one.")
    raise BridgeError(f"There's no client or project {wanted}. ringkeeper_list_clients lists them.")


async def every_kind(gateway: str, token: str, listed: list[dict[str, Any]]) -> list[str]:
    """Every kind of connection any of the projects uses, Ringkeeper's own included."""
    kinds: set[str] = set()
    for row in listed:
        kinds |= set(await services(gateway, token, str(row["project"])))
    return sorted(kinds - set(ALWAYS)) + list(ALWAYS)


def build_every(gateway: str, token: str, kinds: list[str], name: str) -> FastMCP:
    """A pass for every client: its tools, and the two that list and choose the client."""
    choice = Choice()
    server: FastMCP = FastMCP(name=f"ringkeeper-{name}", version=__version__, instructions=EVERY)
    for kind in kinds:
        url = f"{gateway.rstrip('/')}/mcp/{kind}"
        server.mount(create_proxy(_Chosen(url, token, choice)), namespace=kind)

    async def ringkeeper_list_clients() -> dict[str, Any]:
        """The clients and projects this connection can use, and the one chosen now."""
        found = await about(gateway, token)
        return {"projects": found.get("projects", []), "chosen": choice.project}

    async def ringkeeper_use_client(
        client: Annotated[
            str,
            Field(
                description="The client's name (when it has one project), or the project's "
                "name or title, as ringkeeper_list_clients shows them.",
                min_length=1,
                max_length=200,
            ),
        ],
    ) -> dict[str, Any]:
        """Use this client's connections from now on, in every chat in this app."""
        listed = (await about(gateway, token)).get("projects", [])
        try:
            row = pick(listed, client)
        except BridgeError as error:
            raise ToolError(str(error)) from None
        now = await about(gateway, token, str(row["project"]), switched=True)
        if not now.get("project"):
            raise ToolError(f"Ringkeeper refused {row['project']}. Check that it's still there.")
        choice.project = str(now["project"])
        return {"now": f"{now['client']}: {now['project_title']} ({now['project']})", **now}

    reads = ToolAnnotations(read_only_hint=True, destructive_hint=False)
    server.tool(ringkeeper_list_clients, annotations=reads)
    server.tool(ringkeeper_use_client, annotations=ToolAnnotations(destructive_hint=False))
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
    found = asyncio.run(about(address, token))
    if found.get("every_client"):
        listed = cast(list[dict[str, Any]], found.get("projects") or [])
        kinds = asyncio.run(every_kind(address, token, listed))
        server = build_every(address, token, kinds, name)
    else:
        server = build(address, token, asyncio.run(services(address, token)), name, found)
    if gateway is None:  # the desktop app's: follow it
        threading.Thread(
            target=watch, args=(data_dir, address), daemon=True, name="rk-watch"
        ).start()
    server.run(show_banner=False)
