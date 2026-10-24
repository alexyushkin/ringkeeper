from datetime import datetime
from typing import Annotated

import typer

from ringkeeper.cli.common import echo_table, handle_errors
from ringkeeper.db import session_scope
from ringkeeper.services import examples

app = typer.Typer(help="How messages get routed to projects.", no_args_is_help=True)
examples_app = typer.Typer(
    help="Remembered routing answers: an exact match routes without asking.",
    no_args_is_help=True,
)
app.add_typer(examples_app, name="examples")


def _when(value: datetime) -> str:
    return value.astimezone().strftime("%Y-%m-%d %H:%M")


@examples_app.command("list")
def list_(
    project: Annotated[str | None, typer.Option()] = None,
    limit: Annotated[int, typer.Option(min=1, max=1000)] = 50,
) -> None:
    """List saved routing examples, newest first."""
    with handle_errors(), session_scope() as session:
        rows = [
            [
                str(e.id)[:8],
                e.project,
                e.kind.value if e.kind else "-",
                e.created_by or "-",
                _when(e.created_at),
                e.text if len(e.text) <= 60 else e.text[:59] + "…",
            ]
            for e in examples.list_examples(session, project=project, limit=limit)
        ]
    echo_table(
        ["ID", "PROJECT", "KIND", "BY", "SAVED", "MESSAGE"], rows, empty="no routing examples"
    )


@examples_app.command("delete")
def delete(
    example_id: Annotated[str, typer.Argument(help="Full id or 8+ char prefix.")],
) -> None:
    """Forget an example, so that phrasing is routed (or asked about) afresh."""
    with handle_errors(), session_scope() as session:
        example = examples.delete(session, example_id)
        text, project = example.text, example.project
    typer.echo(f"deleted routing example '{text}' -> {project}")
