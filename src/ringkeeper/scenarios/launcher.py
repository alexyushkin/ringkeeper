"""Starts the self-test's process, and follows it. Used by the CLI and by Settings.

Everything an install owns stays out of the run: the environment is built from nothing, the data
and workspaces are in a folder of the run's own, and its vault is locked by a key made for the
run. The install's Claude credential is the one thing passed in, on stdin, and goes when the
folder does.
"""

import json
import os
import secrets
import shutil
import stat
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ringkeeper import claude_auth
from ringkeeper.config import Settings
from ringkeeper.models import AuthMode
from ringkeeper.scenarios.host import PROGRESS, REPORT, RESULTS
from ringkeeper.services import subscription
from ringkeeper.vault import Vault
from ringkeeper.workers.process import POSIX_ENV, WINDOWS, WINDOWS_ENV

MAX_TURNS = 25  # a subscription has no spending stop, so each task gets a short leash
MAX_RUNTIME_SECONDS = 15 * 60
KEYCHAIN_NAME = "RingkeeperSelfTest"
DEFAULT_CEILING = 15.0


def environment(folder: Path, settings: Settings) -> dict[str, str]:
    """What the host runs with: nothing inherited but what Windows or Python needs to start."""
    env = {n: os.environ[n] for n in (WINDOWS_ENV if WINDOWS else POSIX_ENV) if n in os.environ}
    home = folder / "home"
    env |= {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(home),
        "PYTHONUNBUFFERED": "1",
        "PYTHONUTF8": "1",
        "RINGKEEPER_ENV_FILE": "",
        "RINGKEEPER_MODE": "desktop",
        "RINGKEEPER_MASTER_KEY": _master_key(),
        "RINGKEEPER_KEYCHAIN_SERVICE": f"{KEYCHAIN_NAME}{secrets.token_hex(4)}",
        "RINGKEEPER_WORKSPACES_ROOT": str(folder / "workspaces"),
        "RINGKEEPER_HOOKS_PORT": "0",
        "RINGKEEPER_DEFAULT_MAX_TURNS": str(MAX_TURNS),
        "RINGKEEPER_DEFAULT_MAX_RUNTIME_SECONDS": str(MAX_RUNTIME_SECONDS),
        "RINGKEEPER_CLAUDE_AUTH_MODE": settings.claude_auth_mode.value,
        "GIT_AUTHOR_NAME": "Ringkeeper self-test",
        "GIT_AUTHOR_EMAIL": "self-test@localhost",
        "GIT_COMMITTER_NAME": "Ringkeeper self-test",
        "GIT_COMMITTER_EMAIL": "self-test@localhost",
    }
    if WINDOWS:
        env |= {
            "USERPROFILE": str(home),
            "APPDATA": str(home / "AppData" / "Roaming"),
            "LOCALAPPDATA": str(home / "AppData" / "Local"),
            "TEMP": str(folder / "tmp"),
            "TMP": str(folder / "tmp"),
        }
    else:
        env["TMPDIR"] = str(folder / "tmp")
    if settings.claude_auth_mode is AuthMode.SUBSCRIPTION:
        env["RINGKEEPER_ALLOW_SUBSCRIPTION"] = "1"
        if settings.subscription_source:
            env["RINGKEEPER_SUBSCRIPTION_SOURCE"] = settings.subscription_source
        if settings.subscription_source == subscription.FROM_LOGIN:
            # Read-only, by the backend alone, so a long run keeps Claude Code's newer token.
            env["CLAUDE_CONFIG_DIR"] = str(subscription.credentials_file().parent)
    if base := os.environ.get("RINGKEEPER_WORKER_ANTHROPIC_BASE_URL"):
        env["RINGKEEPER_WORKER_ANTHROPIC_BASE_URL"] = base  # the tests' fake model
    if tools := settings.worker_tools_path:  # the bundled git, on the desktop
        env["RINGKEEPER_WORKER_TOOLS_PATH"] = json.dumps(tools)
    if path := os.environ.get("PYTHONPATH"):
        env["PYTHONPATH"] = path
    return env


def _master_key() -> str:
    from ringkeeper.keys import generate_master_key  # noqa: PLC0415

    return generate_master_key()


def credential_label(settings: Settings) -> str:
    if settings.claude_auth_mode is AuthMode.SUBSCRIPTION:
        return "a personal Claude subscription"
    return "an Anthropic API key"


def start(
    settings: Settings,
    vault: Vault,
    folder: Path,
    *,
    only: list[str] | None = None,
    config: Path | None = None,
    ceiling: float = DEFAULT_CEILING,
) -> subprocess.Popen[bytes]:
    """Start a run in `folder`, which must not exist yet. Raises ClaudeAuthError, saying what to
    do, if this install has no credential to run it with."""
    subscription.refresh(vault, settings)
    credential = claude_auth.resolve(vault, settings)
    for part in ("home", "tmp", "workspaces", "data"):
        (folder / part).mkdir(parents=True)
    job = {
        "credential": {
            "mode": settings.claude_auth_mode.value,
            "secret": credential.secret.reveal(),
            "label": credential_label(settings),
        },
        "only": only or [],
        "config": str(config) if config else None,
        "ceiling": ceiling,
    }
    log = (folder / "host.log").open("wb")
    process = subprocess.Popen(  # noqa: S603 (this Python, a fixed module, a folder of ours)
        [sys.executable, "-m", "ringkeeper.scenarios.host", str(folder)],
        stdin=subprocess.PIPE, stdout=log, stderr=subprocess.STDOUT,
        env=environment(folder, settings), cwd=folder,
    )  # fmt: skip
    if process.stdin is not None:
        process.stdin.write(json.dumps(job).encode("utf-8"))
        process.stdin.close()
    log.close()
    return process


def progress(folder: Path) -> dict[str, Any] | None:
    try:
        return json.loads((folder / PROGRESS).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def finished(folder: Path) -> dict[str, Any] | None:
    try:
        return json.loads((folder / RESULTS).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def report_text(folder: Path) -> str | None:
    try:
        return (folder / REPORT).read_text(encoding="utf-8")
    except OSError:
        return None


def remove(folder: Path) -> None:
    """The run's folder, with what it held: its data, its workspaces and the credential's copy."""

    def writable(retry: Callable[[str], object], path: str, _error: BaseException) -> None:
        os.chmod(path, stat.S_IWRITE)  # git marks its files read-only on Windows
        retry(path)

    if folder.exists():
        shutil.rmtree(folder, onexc=writable)
