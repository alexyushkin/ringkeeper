import sys

import typer

from ringkeeper import __version__, migrate
from ringkeeper.cli import (
    audit,
    auth,
    bindings,
    decisions,
    desktop,
    inbox,
    instances,
    projects,
    registry,
    routing,
    secrets,
    tasks,
    telegram,
    tokens,
    web,
)
from ringkeeper.cli.common import handle_errors
from ringkeeper.scrub import install_log_scrubbing

install_log_scrubbing()


def _utf8_when_piped() -> None:
    """Agent output is often non-ASCII (…, quotes, names).

    A real console already gets Unicode through the Windows console API, but a
    pipe or capture falls back to the locale code page (cp1252), which mangles
    it. Use UTF-8 there instead.
    """
    for stream in (sys.stdout, sys.stderr):
        encoding = (getattr(stream, "encoding", "") or "").lower().replace("-", "")
        if encoding != "utf8" and not stream.isatty():
            reconfigure = getattr(stream, "reconfigure", None)
            if callable(reconfigure):
                reconfigure(encoding="utf-8", errors="replace")


_utf8_when_piped()

app = typer.Typer(
    name="ringkeeper",
    help="Route tasks to projects; bind each project to its own service instances.",
    no_args_is_help=True,
    pretty_exceptions_show_locals=False,  # locals could include secrets
)
app.add_typer(projects.app, name="project")
app.add_typer(instances.app, name="instance")
app.add_typer(registry.app, name="registry")
app.add_typer(secrets.app, name="secret")
app.add_typer(tokens.app, name="token")
app.add_typer(decisions.app, name="decision")
app.add_typer(audit.app, name="audit")
app.add_typer(auth.app, name="auth")
app.add_typer(tasks.app, name="task")
app.add_typer(routing.app, name="routing")
app.add_typer(inbox.app, name="inbox")
app.command("ask")(inbox.ask)
app.add_typer(telegram.app, name="telegram")
app.add_typer(web.app, name="web")
app.command("stop-all")(tasks.stop_all)
app.command("bind")(bindings.bind)
app.command("unbind")(bindings.unbind)
app.command("desktop")(desktop.desktop)

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
    """Print the Ringkeeper version."""
    typer.echo(__version__)
