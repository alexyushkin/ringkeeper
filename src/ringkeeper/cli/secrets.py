import sys
from typing import Annotated

import typer

from ringkeeper.cli.common import handle_errors
from ringkeeper.config import get_settings
from ringkeeper.db import session_scope
from ringkeeper.keys import key_file_path, load_master_key
from ringkeeper.services import instances
from ringkeeper.vault import VaultError, get_vault

app = typer.Typer(help="Store and check secrets. Values are never printed.", no_args_is_help=True)


def _read_value() -> str:
    if sys.stdin.isatty():
        return typer.prompt("Secret value", hide_input=True, confirmation_prompt=True)
    value = sys.stdin.read()
    # Drop exactly one trailing newline from `echo` or a file; keep anything else.
    return value.removesuffix("\n").removesuffix("\r")


@app.command("init")
def init() -> None:
    """Create the master key if there isn't one yet, and say where it lives."""
    settings = get_settings()
    with handle_errors():
        if settings.master_key is not None:
            load_master_key(settings)
            typer.echo("using the master key from RINGKEEPER_MASTER_KEY")
            return
        path = key_file_path(settings)
        existed = path.exists()
        load_master_key(settings)
    typer.echo(f"{'master key already exists' if existed else 'created master key'}: {path}")
    typer.echo(
        "Back it up now: losing it loses the vault and every worker token. "
        "Never give it (or its directory) to a worker."
    )


@app.command("set")
def set_(
    ref: Annotated[str, typer.Argument(help="file://name or aws-sm://name")],
) -> None:
    """Store a secret. The value comes from a hidden prompt, or stdin when piped.

    There is deliberately no way to pass the value as an argument: argv ends up
    in shell history and process listings.
    """
    with handle_errors():
        value = _read_value()
        if not value:
            raise VaultError("no value given; nothing stored")
        get_vault().store(ref, value)
    typer.echo(f"stored {ref}")


@app.command("check")
def check(instance: Annotated[str, typer.Argument()]) -> None:
    """Confirm an instance's secret_ref resolves, without printing it."""
    with handle_errors():
        with session_scope() as session:
            ref = instances.get_instance(session, instance).secret_ref
        get_vault().resolve(ref)
    typer.echo(f"ok: secret for {instance} resolves ({ref})")
