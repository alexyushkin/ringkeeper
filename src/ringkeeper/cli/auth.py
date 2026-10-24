from typing import Annotated

import typer

from ringkeeper.claude_auth import ClaudeAuthError, check_format, credential_ref, resolve
from ringkeeper.cli.common import handle_errors
from ringkeeper.cli.secrets import read_secret_value
from ringkeeper.config import get_settings
from ringkeeper.models import AuthMode
from ringkeeper.vault import get_vault

app = typer.Typer(help="The Claude credential workers use.", no_args_is_help=True)

SUBSCRIPTION_NOTE = (
    "Subscription mode is for personal use on your own Claude account only, per the "
    "Agent SDK terms. Don't run Ringkeeper on a subscription token for anyone else."
)


@app.command("login")
def login(
    # Accepted for clarity; storing an API key is what login does by default.
    api_key: Annotated[bool, typer.Option("--api-key", hidden=True)] = True,
    # Personal installs only, so deliberately absent from --help and the README setup.
    subscription: Annotated[bool, typer.Option("--subscription", hidden=True)] = False,
) -> None:
    """Store the Anthropic API key workers use (hidden prompt or stdin, never argv)."""
    mode = AuthMode.SUBSCRIPTION if subscription else AuthMode.API_KEY
    settings = get_settings()
    if subscription:
        typer.echo(SUBSCRIPTION_NOTE, err=True)
        typer.echo(
            "Run `claude setup-token` in another terminal, sign in, then paste the token "
            "it prints here.",
            err=True,
        )
    with handle_errors():
        value = read_secret_value("OAuth token" if subscription else "Anthropic API key")
        if not value:
            raise ClaudeAuthError("no value given; nothing stored")
        check_format(mode, value)
        ref = credential_ref(settings, mode)
        get_vault().store(ref, value)
    typer.echo(f"stored the {mode} credential at {ref}")
    if subscription and settings.claude_auth_mode is not AuthMode.SUBSCRIPTION:
        typer.echo(
            "Workers still use api_key mode; set RINGKEEPER_CLAUDE_AUTH_MODE=subscription "
            "to switch.",
            err=True,
        )


@app.command("status")
def status() -> None:
    """Show the auth mode and whether its credential resolves. Never prints it."""
    settings = get_settings()
    mode = settings.claude_auth_mode
    typer.echo(f"mode:       {mode}")
    typer.echo(f"credential: {credential_ref(settings, mode)}")
    try:
        resolve(get_vault(), settings, mode)
    except ClaudeAuthError as error:
        typer.echo(f"resolves:   no ({error})")
        raise typer.Exit(code=1) from None
    typer.echo("resolves:   yes")
    if mode is AuthMode.SUBSCRIPTION:
        typer.echo(SUBSCRIPTION_NOTE)
