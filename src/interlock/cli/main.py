import typer

from interlock import __version__, migrate
from interlock.cli import bindings, instances, projects, registry, secrets, tokens
from interlock.cli.common import handle_errors
from interlock.scrub import install_log_scrubbing

install_log_scrubbing()

app = typer.Typer(
    name="interlock",
    help="Route tasks to projects; bind each project to its own service instances.",
    no_args_is_help=True,
    pretty_exceptions_show_locals=False,  # locals could include secrets
)
app.add_typer(projects.app, name="project")
app.add_typer(instances.app, name="instance")
app.add_typer(registry.app, name="registry")
app.add_typer(secrets.app, name="secret")
app.add_typer(tokens.app, name="token")
app.command("bind")(bindings.bind)
app.command("unbind")(bindings.unbind)

db_app = typer.Typer(help="Database maintenance.", no_args_is_help=True)
app.add_typer(db_app, name="db")


@db_app.command("upgrade")
def db_upgrade() -> None:
    """Apply database migrations up to the latest version."""
    with handle_errors():
        migrate.upgrade()
    typer.echo("database is up to date")


@app.command("version")
def version() -> None:
    """Print the Interlock version."""
    typer.echo(__version__)
