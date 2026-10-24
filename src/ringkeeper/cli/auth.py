import sys
from typing import Annotated

import typer

from ringkeeper.admin_token import generate_api_token, stored_api_token
from ringkeeper.claude_auth import (
    ClaudeAuthError,
    check_format,
    credential_ref,
    normalize,
    resolve,
)
from ringkeeper.cli.common import handle_errors
from ringkeeper.config import get_settings
from ringkeeper.models import AuthMode
from ringkeeper.vault import get_vault

app = typer.Typer(
    help="The Claude credential workers use, and the API admin token.", no_args_is_help=True
)

SUBSCRIPTION_NOTE = (
    "Subscription mode is for personal use on your own Claude account only, per the "
    "Agent SDK terms. Don't run Ringkeeper on a subscription token for anyone else."
)


def _interactive() -> bool:
    return sys.stdin.isatty()


def read_credential(label: str) -> str:
    """Piped stdin if there is any, else one hidden prompt. Never argv.

    No confirmation prompt: retyping a 100-character token doesn't catch
    mistakes, and on Windows a pasted copy and its confirmation often differ
    by a wrapped line break. The shape check after this catches bad pastes.
    """
    if _interactive():
        typer.echo(
            "Paste it below (hidden). If pasting misbehaves, pipe it instead, e.g. "
            "`Get-Clipboard -Raw | uv run ringkeeper auth login ...` in PowerShell.",
            err=True,
        )
        raw: str = typer.prompt(label, hide_input=True, default="", show_default=False)
    else:
        raw = sys.stdin.read()
    return normalize(raw)


@app.command("login")
def login(
    # Accepted for clarity; storing an API key is what login does by default.
    api_key: Annotated[bool, typer.Option("--api-key", hidden=True)] = True,
    # Personal installs only, so deliberately absent from --help and the README setup.
    subscription: Annotated[bool, typer.Option("--subscription", hidden=True)] = False,
) -> None:
    """Store the Anthropic API key workers use (piped stdin or a hidden prompt, never argv)."""
    mode = AuthMode.SUBSCRIPTION if subscription else AuthMode.API_KEY
    settings = get_settings()
    if subscription:
        typer.echo(SUBSCRIPTION_NOTE, err=True)
        typer.echo(
            "Run `claude setup-token` in another terminal and copy the token it prints. "
            "Pipe it in (e.g. `Get-Clipboard -Raw | uv run ringkeeper auth login "
            "--subscription`) or paste it at the prompt.",
            err=True,
        )
    with handle_errors():
        value = read_credential("OAuth token" if subscription else "Anthropic API key")
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


@app.command("api-token")
def api_token(
    rotate: Annotated[
        bool, typer.Option("--rotate", help="Replace the token; the old one stops working.")
    ] = False,
) -> None:
    """Print the API admin token, creating it on first use. It goes to stdout only."""
    with handle_errors():
        settings = get_settings()
        vault = get_vault(settings)
        token = None if rotate else stored_api_token(vault)
        created = token is None
        if token is None:
            token = generate_api_token()
            vault.store(settings.api_token_ref, token)
    if created:
        verb = "rotated" if rotate else "created"
        typer.echo(f"{verb} the admin token at {settings.api_token_ref}", err=True)
    typer.echo(token)
