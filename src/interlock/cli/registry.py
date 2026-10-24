from pathlib import Path
from typing import Annotated

import typer

from interlock.cli.common import handle_errors
from interlock.db import session_scope
from interlock.services import registry
from interlock.services.registry import Change, Plan

app = typer.Typer(help="Apply or export the whole registry as YAML.", no_args_is_help=True)

SYMBOLS = {"create": "+", "update": "~"}


def render_change(change: Change) -> list[str]:
    lines = [f"{SYMBOLS[change.action]} {change.kind} {change.key}"]
    lines += [
        f"    {f.name}: {registry.format_value(f.old)} -> {registry.format_value(f.new)}"
        for f in change.fields
    ]
    return lines


def render_plan(plan: Plan) -> str:
    if not plan.has_changes:
        return "No changes."
    lines = [line for c in plan.changes if c.action != "unchanged" for line in render_change(c)]
    summary = (
        f"{plan.count('create')} to create, {plan.count('update')} to update, "
        f"{plan.count('unchanged')} unchanged"
    )
    return "\n".join([*lines, "", summary])


@app.command("apply")
def apply(
    file: Annotated[Path, typer.Argument(help="Registry YAML file.")],
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Show the diff and check the rules; write nothing.")
    ] = False,
) -> None:
    """Create or update projects, instances and bindings from a file. Never deletes."""
    with handle_errors():
        data = registry.load_registry_file(file)
        with session_scope(commit=not dry_run) as session:
            plan = registry.apply_registry(session, data)
    typer.echo(render_plan(plan))
    if dry_run and plan.has_changes:
        typer.echo("Dry run: nothing was written.")


@app.command("export")
def export(
    output: Annotated[
        Path | None, typer.Option("--output", "-o", help="Write to a file instead of stdout.")
    ] = None,
) -> None:
    """Dump the current registry as YAML (includes secret_ref pointers, never secrets)."""
    with handle_errors(), session_scope() as session:
        text = registry.dump_registry(registry.export_registry(session))
    if output is None:
        typer.echo(text, nl=False)
    else:
        output.write_text(text, encoding="utf-8")
        typer.echo(f"wrote {output}")
