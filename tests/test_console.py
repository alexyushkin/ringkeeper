"""CLI output stays readable when piped or captured on Windows (cp1252 by default)."""

import os
import subprocess
import sys

SNIPPET = "from ringkeeper.cli import main; import typer; typer.echo('dots… and “quotes”')"


def test_piped_output_is_utf8() -> None:
    env = {k: v for k, v in os.environ.items() if k not in {"PYTHONIOENCODING", "PYTHONUTF8"}}
    result = subprocess.run(  # noqa: S603 (our own interpreter, fixed code)
        [sys.executable, "-c", SNIPPET], capture_output=True, env=env, check=True
    )
    assert result.stdout.decode("utf-8").strip() == "dots… and “quotes”"
