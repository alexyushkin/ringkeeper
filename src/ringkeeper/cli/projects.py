from typing import Annotated

import typer

from ringkeeper.cli.common import echo_table, handle_errors, join, split_list
from ringkeeper.db import session_scope
from ringkeeper.models import Autonomy
from ringkeeper.schemas import ProjectIn
from ringkeeper.services import bindings, projects

app = typer.Typer(help="Manage projects.", no_args_is_help=True)


@app.command("add")
def add(
    name: Annotated[str, typer.Argument(help="Unique slug, e.g. acme-web.")],
    owner: Annotated[str, typer.Option(help="Person or team that owns the project.")],
    description: Annotated[str, typer.Option()] = "",
    alias: Annotated[
        list[str] | None, typer.Option(help="Other names for routing. Repeatable or a,b.")
    ] = None,
    repo_url: Annotated[str | None, typer.Option()] = None,
    workspace_path: Annotated[str | None, typer.Option()] = None,
    autonomy: Annotated[Autonomy, typer.Option()] = Autonomy.STANDARD,
) -> None:
    """Add a project."""
    with handle_errors():
        data = ProjectIn(
            name=name,
            owner=owner,
            description=description,
            aliases=split_list(alias),
            repo_url=repo_url,
            workspace_path=workspace_path,
            autonomy=autonomy,
        )
        with session_scope() as session:
            projects.create_project(session, data)
    typer.echo(f"added project {name}")


@app.command("list")
def list_() -> None:
    """List projects."""
    with handle_errors(), session_scope() as session:
        rows = [
            [p.name, p.owner, p.autonomy.value, join(p.aliases)]
            for p in projects.list_projects(session)
        ]
    echo_table(["NAME", "OWNER", "AUTONOMY", "ALIASES"], rows, empty="no projects")


@app.command("show")
def show(name: Annotated[str, typer.Argument()]) -> None:
    """Show a project and the instances bound to it."""
    with handle_errors(), session_scope() as session:
        project = projects.get_project(session, name)
        details = [
            ("name", project.name),
            ("owner", project.owner),
            ("description", project.description or "-"),
            ("aliases", join(project.aliases)),
            ("repo_url", project.repo_url or "-"),
            ("workspace_path", project.workspace_path or "-"),
            ("autonomy", project.autonomy.value),
        ]
        rows = [
            [
                b.instance.name,
                b.instance_type.value,
                b.instance_env.value,
                "yes" if b.is_default else "",
                join(b.allowed_tools) if b.allowed_tools else "all",
                join(b.preapproved_writes),
            ]
            for b in bindings.sorted_bindings(project)
        ]
    for key, value in details:
        typer.echo(f"{key + ':':<16}{value}")
    typer.echo("")
    echo_table(
        ["INSTANCE", "TYPE", "ENV", "DEFAULT", "TOOLS", "PREAPPROVED"],
        rows,
        empty="no instances bound",
    )


@app.command("remove")
def remove(name: Annotated[str, typer.Argument()]) -> None:
    """Remove a project and its bindings. Instances are kept."""
    with handle_errors(), session_scope() as session:
        removed = projects.remove_project(session, name)
    suffix = f" and {removed} binding(s)" if removed else ""
    typer.echo(f"removed project {name}{suffix}")
