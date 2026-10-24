from typing import Annotated

import typer

from interlock.cli.common import handle_errors, split_list
from interlock.db import session_scope
from interlock.schemas import BindingIn
from interlock.services import bindings


def bind(
    project: Annotated[str, typer.Argument()],
    instance: Annotated[str, typer.Argument()],
    default: Annotated[
        bool, typer.Option("--default", help="Make it the project's default for its type.")
    ] = False,
    tools: Annotated[
        list[str] | None,
        typer.Option("--tools", help="Allowed tools (empty = all). Repeatable or a,b."),
    ] = None,
    preapprove: Annotated[
        list[str] | None,
        typer.Option("--preapprove", help="Writes that need no approval. Repeatable or a,b."),
    ] = None,
) -> None:
    """Bind an instance to a project, or replace an existing binding's settings."""
    with handle_errors():
        data = BindingIn(
            project=project,
            instance=instance,
            is_default=default,
            allowed_tools=split_list(tools),
            preapproved_writes=split_list(preapprove),
        )
        with session_scope() as session:
            _, created = bindings.set_binding(session, data)
    verb = "bound" if created else "updated binding of"
    typer.echo(f"{verb} {instance} to {project}{' (default)' if default else ''}")


def unbind(
    project: Annotated[str, typer.Argument()],
    instance: Annotated[str, typer.Argument()],
) -> None:
    """Remove an instance from a project."""
    with handle_errors(), session_scope() as session:
        bindings.remove_binding(session, project, instance)
    typer.echo(f"unbound {instance} from {project}")
