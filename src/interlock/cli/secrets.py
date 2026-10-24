import sys
from typing import Annotated

import typer

from interlock.cli.common import handle_errors
from interlock.db import session_scope
from interlock.services import instances
from interlock.vault import VaultError, get_vault

app = typer.Typer(help="Store and check secrets. Values are never printed.", no_args_is_help=True)


def _read_value() -> str:
    if sys.stdin.isatty():
        return typer.prompt("Secret value", hide_input=True, confirmation_prompt=True)
    value = sys.stdin.read()
    # Drop exactly one trailing newline from `echo` or a file; keep anything else.
    return value.removesuffix("\n").removesuffix("\r")


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
