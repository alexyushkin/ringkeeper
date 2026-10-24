from typing import Annotated

import typer

from ringkeeper.cli.common import echo_table, handle_errors, join
from ringkeeper.db import session_scope
from ringkeeper.models import Env, InstanceType
from ringkeeper.schemas import InstanceIn
from ringkeeper.services import instances

app = typer.Typer(help="Manage service instances.", no_args_is_help=True)


@app.command("add")
def add(
    name: Annotated[str, typer.Argument(help="Unique slug, e.g. acme-supabase-dev.")],
    type: Annotated[InstanceType, typer.Option("--type", help="Service type.")],
    env: Annotated[Env, typer.Option(help="prod instances can never be a default.")],
    secret_ref: Annotated[
        str, typer.Option(help="Pointer to the credentials: file:///path or aws-sm://name.")
    ],
    owner: Annotated[str, typer.Option(help="Person or team that owns the account.")],
    base_url: Annotated[str | None, typer.Option()] = None,
) -> None:
    """Add an instance."""
    with handle_errors():
        data = InstanceIn(
            name=name, type=type, env=env, secret_ref=secret_ref, owner=owner, base_url=base_url
        )
        with session_scope() as session:
            instances.create_instance(session, data)
    typer.echo(f"added instance {name}")


@app.command("list")
def list_(
    type: Annotated[InstanceType | None, typer.Option("--type")] = None,
    env: Annotated[Env | None, typer.Option()] = None,
) -> None:
    """List instances."""
    with handle_errors(), session_scope() as session:
        rows = [
            [i.name, i.type.value, i.env.value, i.owner, i.base_url or "-"]
            for i in instances.list_instances(session, type=type, env=env)
        ]
    echo_table(["NAME", "TYPE", "ENV", "OWNER", "BASE_URL"], rows, empty="no instances")


@app.command("show")
def show(name: Annotated[str, typer.Argument()]) -> None:
    """Show an instance and the projects it is bound to."""
    with handle_errors(), session_scope() as session:
        instance = instances.get_instance(session, name)
        details = [
            ("name", instance.name),
            ("type", instance.type.value),
            ("env", instance.env.value),
            ("owner", instance.owner),
            ("base_url", instance.base_url or "-"),
            ("secret_ref", instance.secret_ref),
            ("bound to", join(instances.bound_project_names(instance))),
        ]
    for key, value in details:
        typer.echo(f"{key + ':':<12}{value}")


@app.command("remove")
def remove(
    name: Annotated[str, typer.Argument()],
    force: Annotated[
        bool, typer.Option("--force", help="Also remove the instance's bindings.")
    ] = False,
) -> None:
    """Remove an instance. Fails while it is bound, unless --force."""
    with handle_errors(), session_scope() as session:
        unbound = instances.remove_instance(session, name, force=force)
    suffix = f" (unbound from {', '.join(unbound)})" if unbound else ""
    typer.echo(f"removed instance {name}{suffix}")
