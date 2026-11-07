"""ringkeeper scenarios: the self-test, from a terminal."""

import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import typer

from ringkeeper.cli.common import handle_errors
from ringkeeper.config import get_settings
from ringkeeper.scenarios import launcher
from ringkeeper.scenarios.catalog import SCENARIOS
from ringkeeper.scenarios.report import TARGET
from ringkeeper.vault import get_vault

app = typer.Typer(
    help="Real requests through the real router, workers and gateway.", no_args_is_help=True
)
POLL_SECONDS = 2.0


@app.command("list")
def list_scenarios() -> None:
    """What the self-test covers."""
    for scenario in SCENARIOS:
        needs = f"  (needs {scenario.needs})" if scenario.needs else ""
        typer.echo(f"{scenario.key:20} {scenario.title}{needs}")


def _default_out(day: str) -> Path:
    docs = Path("docs/scenarios")
    return (docs if docs.is_dir() else get_settings().data_dir / "self-test") / f"{day}.md"


@app.command("run")
def run(
    only: Annotated[list[str] | None, typer.Option(help="Just these scenarios.")] = None,
    config: Annotated[Path, typer.Option(help="Sandbox connections.")] = Path("scenarios.toml"),
    out: Annotated[Path | None, typer.Option(help="Where the report goes.")] = None,
    ceiling: Annotated[
        float, typer.Option(help="Stop starting scenarios past this estimated USD total.")
    ] = launcher.DEFAULT_CEILING,
    keep: Annotated[
        bool, typer.Option(help="Keep the run's folder, to look into a failure.")
    ] = False,
) -> None:
    """Run the scenarios in a scratch folder of their own, and write the report."""
    settings = get_settings()
    day = datetime.now().astimezone().date().isoformat()
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    folder = (settings.data_dir / "self-test" / f"run-{stamp}").absolute()
    with handle_errors():
        process = launcher.start(
            settings, get_vault(settings), folder, only=only,
            config=config if config.is_file() else None, ceiling=ceiling,
        )  # fmt: skip
    shown = ""
    while process.poll() is None:
        now = launcher.progress(folder) or {}
        line = f"{now.get('done', 0)}/{now.get('total', '?')}  {now.get('current') or ''}"
        if line != shown:
            typer.echo(line)
            shown = line
        time.sleep(POLL_SECONDS)
    text = launcher.report_text(folder)
    if text is None:
        now = launcher.progress(folder) or {}
        typer.echo(
            f"The self-test stopped: {now.get('error', 'see host.log')} ({folder})", err=True
        )
        raise typer.Exit(1)
    target = out or _default_out(day)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    done = launcher.finished(folder) or {}
    ran, passed = int(done.get("ran", 0)), int(done.get("passed", 0))
    typer.echo(f"{passed} of {ran} passed, {done.get('skipped', 0)} skipped: {target}")
    if not keep:
        launcher.remove(folder)
    if not ran or passed / ran < TARGET:
        raise typer.Exit(1)
