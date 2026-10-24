"""Run the project's checks after a worker turn, in this container.

Each check is a shell command run in /workspace with a minimal environment:
no Claude credential and no gateway token, since checks run project code.
"""

import os
import subprocess

from ringkeeper_worker.emit import emit

OUTPUT_CHARS = 8000
TIMEOUT_SECONDS = 600
SAFE_ENV = ("PATH", "HOME", "LANG", "LC_ALL", "TZ")


def _env() -> dict[str, str]:
    env = {k: os.environ[k] for k in SAFE_ENV if k in os.environ}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["CI"] = "1"
    return env


def _tail(text: str) -> str:
    return text if len(text) <= OUTPUT_CHARS else "…" + text[-OUTPUT_CHARS:]


def run_checks(commands: list[str], workspace: str) -> dict[str, object]:
    results: list[dict[str, object]] = []
    for command in commands:
        try:
            done = subprocess.run(  # noqa: S602 (the operator's own check commands, by design)
                command,
                shell=True,
                cwd=workspace,
                env=_env(),
                capture_output=True,
                text=True,
                timeout=TIMEOUT_SECONDS,
                check=False,
            )
            code, output = done.returncode, (done.stdout or "") + (done.stderr or "")
        except subprocess.TimeoutExpired as expired:
            code = -1
            output = f"timed out after {TIMEOUT_SECONDS}s\n{expired.stdout or ''}"
        results.append(
            {"command": command, "exit_code": code, "passed": code == 0, "output": _tail(output)}
        )
    summary: dict[str, object] = {
        "passed": all(r["passed"] for r in results),
        "results": results,
    }
    emit("ringkeeper.checks", **summary)
    return summary
