"""`ringkeeper mcp --pass <name>`: what Claude Desktop, Claude Code or Cursor starts for a pass."""

import os
import sys
from pathlib import Path
from typing import Annotated

import typer


def mcp(
    pass_name: Annotated[
        str, typer.Option("--pass", help="The pass's name, from Settings > Passes.")
    ],
    data_dir: Annotated[
        Path | None,
        typer.Option(help="Ringkeeper's data folder (default: the desktop app's)."),
    ] = None,
    gateway: Annotated[
        str | None, typer.Option(help="The gateway's address (default: the running app's).")
    ] = None,
) -> None:
    """Serve one project's connections to an AI app on this computer, over stdio. Every call
    goes to Ringkeeper's gateway and is checked there."""
    from ringkeeper.desktop import paths  # noqa: PLC0415

    folder = (data_dir or paths.data_dir()).resolve()
    # The desktop app's settings, as its backend has them (Settings reads the environment).
    os.environ.setdefault("RINGKEEPER_MODE", "desktop")
    os.environ.setdefault("RINGKEEPER_DATA_DIR", str(folder))
    from ringkeeper import bridge  # noqa: PLC0415 (after the environment is set)

    try:
        bridge.run(pass_name, folder, gateway)
    except bridge.BridgeError as error:
        # stdout is the MCP channel: what went wrong goes to the app's log.
        print(f"Ringkeeper: {error}", file=sys.stderr)
        raise typer.Exit(1) from None
