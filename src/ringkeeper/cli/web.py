import typer

from ringkeeper import bootstrap
from ringkeeper.cli.common import handle_errors
from ringkeeper.config import get_settings
from ringkeeper.db import session_scope
from ringkeeper.services import owner

app = typer.Typer(help="The web app's owner login.", no_args_is_help=True)


@app.command("reset-login")
def reset_login(
    yes: bool = typer.Option(False, "--yes", help="Don't ask for confirmation."),
) -> None:
    """Forgot the password? Remove the login (and passkeys) and print a new setup code.

    Projects, connections, secrets and history stay. Open the web app and run
    the wizard's first step again with the new code.
    """
    if not yes:
        typer.confirm(
            "Remove the web app's login? You'll set a new password with a new setup code.",
            abort=True,
        )
    settings = get_settings()
    with handle_errors():
        with session_scope() as session:
            owner.reset_owner(session)
        code = bootstrap.ensure_setup_code(settings, announce=False)
    typer.echo(f"New setup code: {code}")
    typer.echo("Open the web app and create the login again with this code.")


@app.command("setup-code")
def setup_code() -> None:
    """Print the setup code the web app's wizard asks for (only before the login exists)."""
    with handle_errors():
        code = bootstrap.ensure_setup_code(get_settings(), announce=False)
    if code is None:
        typer.echo("Setup is done: the login exists.")
        typer.echo("Forgot the password? Run `ringkeeper web reset-login`.")
        raise typer.Exit(code=1)
    typer.echo(code)
