"""`ringkeeper desktop`: the backend the desktop app starts (also a dev command)."""

import logging
from pathlib import Path
from typing import Annotated

import typer


def desktop(
    data_dir: Annotated[
        Path | None,
        typer.Option(help="Where Ringkeeper keeps its data (default: the per-user app folder)."),
    ] = None,
    web_dir: Annotated[
        Path | None, typer.Option(help="The web app's exported files, to serve to the window.")
    ] = None,
) -> None:
    """Run Ringkeeper for this computer: API, gateway and workers on 127.0.0.1, no Docker."""
    from ringkeeper.desktop import paths, server  # noqa: PLC0415 (not needed by other commands)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    folder = (data_dir or paths.data_dir()).resolve()
    try:
        server.run(folder, web_dir=web_dir)
    except server.AlreadyRunningError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(3) from None
