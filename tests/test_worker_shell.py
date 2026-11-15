"""A worker's shell on Windows prints any text: a record's accents and other scripts come out
as UTF-8, through Ringkeeper's own Python, whatever the computer's code page is."""

import json
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

from ringkeeper.workers.env import TASK_ENV, worker_env
from ringkeeper.workers.process import ProcessRuntime
from ringkeeper.workers.runtime import WorkerSpec

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="the Windows worker shell")

TEXT = "Zoë Ångström, Łódź, 東京, ✓"
PRINT = f"import sys; print({TEXT!r}); print(sys.executable)"


def worker_shell_env(tmp_path: Path) -> dict[str, str]:
    runtime = ProcessRuntime(tmp_path / "workers")
    task = {"task_id": "t"}
    spec = WorkerSpec(
        name="w", image="", workspace=str(tmp_path), network="",
        state_volume=f"ringkeeper-state-{uuid.uuid4()}-worker",
        env=worker_env({}, task, "token"),
    )  # fmt: skip
    env = runtime.environment(spec)
    assert json.loads(env[TASK_ENV])["task_id"] == "t"
    return env


def printed(argv: list[str], env: dict[str, str], cwd: Path) -> tuple[str, str]:
    done = subprocess.run(argv, env=env, cwd=cwd, capture_output=True, check=False)  # noqa: S603
    assert done.returncode == 0, done.stderr.decode("utf-8", "replace")
    text, python = done.stdout.decode("utf-8").splitlines()[:2]
    return text, python


def test_cmd_prints_non_ascii_text_with_ringkeepers_python(tmp_path: Path) -> None:
    env = worker_shell_env(tmp_path)
    cmd = env.get("COMSPEC") or os.environ["COMSPEC"]
    text, python = printed([cmd, "/d", "/c", "python", "-c", PRINT], env, tmp_path)
    assert text == TEXT
    assert Path(python).parent == Path(sys.executable).parent  # not the computer's own
    assert env["PIP_REQUIRE_VIRTUALENV"] == "1"  # so pip never installs into Ringkeeper's own


def test_git_bash_prints_non_ascii_text(tmp_path: Path) -> None:
    env = worker_shell_env(tmp_path)
    bash = shutil.which("bash", path=env["PATH"])
    if bash is None or "system32" in bash.lower():  # WSL's bash isn't Claude Code's shell
        pytest.skip("no Git Bash on this computer")
    text, python = printed([bash, "-c", f'python -c "{PRINT}"'], env, tmp_path)
    assert text == TEXT
    assert Path(python).parent == Path(sys.executable).parent
